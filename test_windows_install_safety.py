"""Elevated Windows install steps never trust the per-user folder (security audit M1).

%LOCALAPPDATA%\\Lifetime belongs to the signed-in user, so anything running as them can replace a file in it or
turn it into a junction. The installer therefore refuses links and hands back ownership only of what it wrote, and
the Connect page asks for approval to run the agent setup files kept under Program Files, not the per-user copies.
"""
import base64
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import lifetime_connect as connect

PROJECT = Path(__file__).parent
RUN = subprocess.run      # the tests below replace subprocess.run with a recorder
WINDOWS = unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell and junctions')


def powershell(script):
    return RUN(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def junction(link, target):
    RUN(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)


class ConnectElevationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.user, self.setup, self.data = root / 'user', root / 'Program Files' / 'LifetimeAgentSetup', root / 'data'
        self.user.mkdir()
        for name in connect.AGENT_SETUP:
            (self.user / name).write_bytes(b'per-user ' + name.encode())
        self.scripts = []

        def run(command, **kwargs):
            if command[0] == 'whoami.exe':
                return Mock(stdout='"PC\\User","S-1-5-21-123"', returncode=0)
            if command[0] == 'powershell.exe':
                self.scripts.append(command[-1])
            return Mock(returncode=0)
        for target, value in ((connect.sys, dict(frozen=True, executable=str(self.user / 'Lifetime.exe'), platform='win32')),
                              (connect.subprocess, dict(run=run, CREATE_NO_WINDOW=0))):
            for name, item in value.items():
                self.enterContext(patch.object(target, name, item, create=True))
        self.enterContext(patch.object(connect, 'agent_setup_directory', return_value=self.setup))

    def elevated(self, action='finish'):
        connect.run_agent(action, dict(history=True), 'SECRET', self.data, display_name='PC')
        self.assertEqual(len(self.scripts), 1)       # still one approval
        script = self.scripts[0]
        self.assertIn('-Verb RunAs', script)
        return script

    def test_the_copies_under_program_files_are_what_administrator_approval_runs(self):
        self.setup.mkdir(parents=True)
        for name in connect.AGENT_SETUP:
            (self.setup / name).write_bytes(b'installed')
        for action, expected in (('finish', 'install_agent_windows.ps1'), ('policy', 'LifetimeAgent.exe'), ('disconnect', 'LifetimeAgent.exe')):
            self.scripts.clear()
            script = self.elevated(action)
            self.assertIn(str(self.setup / expected), script)
            self.assertNotIn(str(self.user), script)
            self.assertNotIn('EncodedCommand', script)
            if action == 'finish':      # Windows clients refuse script files by default: the installer must still run
                self.assertRegex(script, r'-ExecutionPolicy Bypass -File "?[^"]*install_agent_windows\.ps1')
                # Connecting again after a Disconnect finds the agent already installed: it is replaced, not refused.
                self.assertIn('-Mode Packaged -Reinstall -EnableReporting', script)

    def test_without_them_the_elevated_step_copies_to_program_files_and_checks_each_hash_before_running(self):
        script = self.elevated()
        encoded = re.search(r'-EncodedCommand ([A-Za-z0-9+/=]+)', script).group(1)
        inner = base64.b64decode(encoded).decode('utf-16-le')
        self.assertIn("Join-Path $env:ProgramFiles 'LifetimeAgentSetup'", inner)      # chosen by the elevated side
        self.assertIn('D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)', inner)     # users cannot write
        for name in connect.AGENT_SETUP:
            self.assertIn(hashlib.sha256((self.user / name).read_bytes()).hexdigest(), inner)
        copied, started = inner.index('ComputeHash'), inner.index('Start-Process')
        self.assertLess(copied, started)
        run = inner[started:]
        self.assertIn(str(self.setup / 'install_agent_windows.ps1'), run)
        self.assertIn('-ExecutionPolicy Bypass -File', run)
        self.assertNotIn(str(self.user), run)        # nothing from the per-user folder is executed

    def test_a_link_where_the_setup_folder_should_be_is_not_trusted(self):
        self.setup.mkdir(parents=True)
        (self.setup / 'LifetimeAgent.exe').write_bytes(b'installed')
        with patch.object(connect, '_linked', side_effect=lambda path: Path(path) == self.setup):
            self.assertIn('EncodedCommand', self.elevated())      # staged again; the elevated step refuses the link

    @WINDOWS
    def test_the_staging_step_really_copies_verifies_and_refuses_links(self):
        target = Path(self.temporary.name) / 'staged'
        names = list(connect.AGENT_SETUP)

        def stage(**changes):
            # The access-list line needs an elevated process to keep working afterwards; everything else runs here.
            script = connect.stage_script(self.user, names, connect._ps_quote(target)).replace(connect.STAGE_ACL, '')
            for old, new in changes.items():
                script = script.replace(old, new)
            return powershell(script)
        result = stage()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in names:
            self.assertEqual((target / name).read_bytes(), (self.user / name).read_bytes())
        # The file changed between what the app read and what was copied: nothing is left to run.
        digest = hashlib.sha256((self.user / names[0]).read_bytes()).hexdigest()
        result = stage(**{digest: '0' * 64})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((target / names[0]).exists())
        # A junction instead of the folder: refused before anything is copied through it.
        elsewhere = Path(self.temporary.name) / 'elsewhere'
        elsewhere.mkdir()
        for name in names:
            (target / name).unlink(missing_ok=True)
        target.rmdir()
        junction(target, elsewhere)
        self.addCleanup(lambda: target.rmdir() if target.exists() else None)
        result = stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])


class InstallerScriptTests(unittest.TestCase):
    text = (PROJECT / 'install_windows_common.ps1').read_text(encoding='utf-8')

    def test_ownership_is_handed_back_only_for_what_the_installer_wrote(self):
        self.assertNotIn('-Recurse', self.text)       # walking the folder while elevated follows junctions
        owner = self.text[self.text.index('$Owner = '):]
        self.assertIn('foreach ($Path in ($Written + @($ShortcutPath)))', owner)
        self.assertLess(owner.index('Assert-NotLinked $Path'), owner.index('SetOwner'))
        self.assertNotIn('Get-ChildItem', self.text)

    def test_every_elevated_copy_is_checked_for_links_first_and_the_agent_setup_goes_to_program_files(self):
        self.assertLess(self.text.index('Assert-NotLinked $InstallDir'), self.text.index('New-Item -ItemType Directory -Force -Path $InstallDir'))
        self.assertEqual(self.text.count('Copy-Item'), 3)
        for copy in re.finditer(r'Copy-Item', self.text):
            self.assertIn('Assert-NotLinked', self.text[max(0, copy.start() - 260):copy.start()])
        self.assertIn("$AgentSetup = Join-Path $env:ProgramFiles 'LifetimeAgentSetup'", self.text)
        self.assertIn(connect.STAGE_ACL.split("'")[1], self.text)      # the same access list as the Connect page's step

    def test_installing_over_a_running_version_closes_only_this_installation_and_starts_the_tracker_again(self):
        """Windows cannot replace a running program: an upgrade over a running tracker used to stop with an error."""
        closing = self.text.index('$Closed = @(Get-Process -Name Lifetime, LifetimeTracker, LifetimeImport')
        self.assertIn('[IO.Path]::GetDirectoryName($_.Path) -eq $InstallDir', self.text[closing:closing + 300])   # never another copy
        self.assertLess(closing, self.text.index('Copy-Item'))
        self.assertIn("{ Start-ScheduledTask -TaskName 'Lifetime Tracker' }", self.text)

    def test_the_agent_can_be_replaced_in_place_and_nothing_keeps_running_after_its_uninstall(self):
        install = (PROJECT / 'install_agent_windows.ps1').read_text(encoding='utf-8')
        uninstall = (PROJECT / 'uninstall_agent_windows.ps1').read_text(encoding='utf-8')
        # Without -Reinstall an installed agent is left alone (exit 6), so a start-up script can run at every boot.
        self.assertIn('if ($installed -and -not $Reinstall) { Stop-Install 6', install)
        replaced = install.index("& (Join-Path $PSScriptRoot 'uninstall_agent_windows.ps1')")
        self.assertLess(install.index("'--check') + $enrollArguments"), replaced)      # every check first
        self.assertLess(replaced, install.index("Filter 'lifetime_*.py' -File | ForEach-Object { Copy-Item"))
        # Stopping the task ends only its first process: the child that does the work is ended too, by its own path.
        for script in (uninstall, install[install.index('function Install-StagedUpdate'):install.index('function Stop-ForAgent')]):
            self.assertLess(script.index('Stop-ScheduledTask'), script.index('Stop-Process'))
        self.assertIn('$_.ExecutablePath -eq $executable', uninstall)
        self.assertLess(uninstall.index('Stop-Process'), uninstall.index('Unregister-ScheduledTask'))

    def test_the_server_uninstall_closes_this_installations_dashboard_before_anything_is_removed(self):
        """Dashboard windows left open by the server uninstall locked a file during a later reinstall."""
        uninstall = (PROJECT / 'uninstall_server_windows.ps1').read_text(encoding='utf-8')
        closing = uninstall.index("$dashboard = Join-Path $installRoot 'LifetimeServerDashboard.exe'")
        block = uninstall[closing:uninstall.index('$service = Get-Service')]
        # Only this installation's dashboard: its packaged program, or an interpreter running its own script.
        self.assertIn('$_.ExecutablePath -eq $dashboard', block)
        self.assertIn("$dashboardScript = Join-Path $installRoot 'lifetime_server_gui.py'", block)
        self.assertIn('$_.CommandLine.Contains($dashboardScript)', block)
        self.assertIn("(Join-Path $PSScriptRoot 'server-install.json')", uninstall[:closing])     # the copy in the install folder
        stop = uninstall.index('Stop-Process', closing)
        self.assertLess(stop, uninstall.index('Stop-Service'))
        self.assertLess(stop, uninstall.index('Unregister-ScheduledTask'))
        self.assertLess(stop, uninstall.index('Remove-Item -LiteralPath $shortcutPath'))
        self.assertLess(closing, stop)

    def test_a_packaged_agent_folder_counts_as_an_agent_installation_so_connecting_again_can_replace_it(self):
        """A Packaged install holds LifetimeAgent.exe and no lifetime_agent.py. The desktop's Finish passes -Reinstall,
        and the ownership check used to refuse that folder: 'not a Lifetime Agent installation' (exit 2)."""
        install = (PROJECT / 'install_agent_windows.ps1').read_text(encoding='utf-8')
        check = install[install.index('$ours ='):install.index("'The existing nonempty install directory is not a Lifetime Agent installation.'")]
        self.assertIn("'lifetime_agent.py'", check)
        self.assertIn("'LifetimeAgent.exe'", check)
        self.assertIn('-not $ours', check)

    @WINDOWS
    def test_the_link_check_refuses_a_junction_and_a_file_link_and_accepts_real_and_missing_paths(self):
        function = re.search(r'function Assert-NotLinked.*?\n}\r?\n', self.text, re.S).group(0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'real' / 'file.txt').write_text('x')
            junction(root / 'link', root / 'real')
            quote = connect._ps_quote
            try:
                for path, refused in ((root / 'real', False), (root / 'real' / 'file.txt', False), (root / 'missing', False),
                                      (root / 'link', True)):
                    result = powershell("$ErrorActionPreference='Stop'; " + function + '; Assert-NotLinked ' + quote(path))
                    self.assertEqual(result.returncode != 0, refused, (path, result.stderr))
            finally:
                (root / 'link').rmdir()


class UninstallerTests(unittest.TestCase):
    """uninstall_windows.ps1 removes the per-user install and, unless asked, never the awake-time history."""
    text = (PROJECT / 'uninstall_windows.ps1').read_text(encoding='utf-8').replace('\r\n', '\n')
    install = (PROJECT / 'install_windows_common.ps1').read_text(encoding='utf-8').replace('\r\n', '\n')

    def function(self, name):
        return re.search(r'function ' + name + r'\b.*?\n}\n', self.text, re.S).group(0)

    def test_links_are_refused_and_this_installation_is_closed_before_anything_is_removed(self):
        self.assertLess(self.text.index('Assert-NotLinked $InstallDir'), self.text.index('Stop-Process'))
        self.assertLess(self.text.index('Assert-NotLinked $InstallDir'), self.text.index('Unregister-ScheduledTask'))
        # The same filter as the installer: only programs in this installation's folder, never another copy.
        for text in (self.text, self.install):
            self.assertIn('[IO.Path]::GetDirectoryName($_.Path) -eq $InstallDir', text)
        self.assertLess(self.text.index('Stop-Process'), self.text.index('Remove-Folder $InstallDir'))
        # The folders are removed only by Remove-Folder, after its link checks.
        remove = self.function('Remove-Folder')
        self.assertLess(remove.index('Assert-NotLinked $Path'), remove.index('Remove-Item'))
        self.assertLess(remove.index('-Attributes ReparsePoint'), remove.index('Remove-Item'))
        self.assertEqual(re.findall(r'Remove-Item -LiteralPath (\$\w+) -Recurse', self.text), ['$Path', '$Entry'])
        # The task and the tray entry only when they run this installation.
        task = self.text[self.text.index('$Task = Get-ScheduledTask'):self.text.index('Unregister-ScheduledTask')]
        self.assertIn('Test-Ours $_.Execute', task)
        self.assertIn('if (Test-Ours $Value) { Remove-ItemProperty', self.text)

    def test_the_history_is_deleted_only_with_remove_data_and_the_machine_agent_is_explained(self):
        self.assertEqual(self.text.count('Remove-Folder $DataDir'), 1)
        removal = self.text.index('Remove-Folder $DataDir')
        self.assertLess(self.text.index('if ($RemoveData) {\n'), removal)
        self.assertLess(removal, self.text.index('} elseif (Test-Path -LiteralPath $DataDir)'))
        self.assertIn('if ($RemoveData) { Assert-NotLinked $DataDir }', self.text)
        self.assertIn("$DataDir = Join-Path $env:APPDATA 'Lifetime'", self.text)
        self.assertIn("$InstallDir = Join-Path $env:LOCALAPPDATA 'Lifetime'", self.text)
        self.assertIn('uninstall_agent_windows.ps1 from an elevated PowerShell', self.text)
        self.assertNotIn('RunAs', self.text)       # a per-user uninstall needs no administrator

    def test_the_installer_ships_it_and_lists_lifetime_in_installed_apps_for_the_signed_in_user(self):
        self.assertEqual(self.install.count(", 'uninstall_windows.ps1')"), 2)       # the .exe and the Python version
        self.assertIn('uninstall_windows.ps1', (PROJECT / 'build_windows.ps1').read_text(encoding='utf-8'))
        entry = self.install[self.install.index('# Windows "Installed apps" entry'):self.install.index('if ($Admin) {\n    $Owner')]
        # HKCU would be the approving administrator's registry: the entry goes to the signed-in user's hive.
        self.assertIn('"Registry::HKEY_USERS\\$Sid\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Lifetime"', entry)
        self.assertNotIn('HKCU:', self.install)
        self.assertIn('UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Uninstaller`" -Pause"', entry)
        self.assertIn("$Uninstaller = Join-Path $InstallDir 'uninstall_windows.ps1'", entry)
        self.assertIn("Publisher = 'JPL Innovation'", entry)
        self.assertLess(self.install.index('$Sid = '), self.install.index('$Registered.SetSecurityDescriptor'))

    @WINDOWS
    def test_the_folder_removal_refuses_links_and_leaves_their_targets_alone(self):
        script = "$ErrorActionPreference='Stop'; " + self.function('Assert-NotLinked') + self.function('Remove-Folder')
        quote = connect._ps_quote
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elsewhere = root / 'elsewhere'
            elsewhere.mkdir()
            (elsewhere / 'keep.txt').write_text('history')
            plain = root / 'plain'
            (plain / 'assets').mkdir(parents=True)
            (plain / 'assets' / 'lifetime.ico').write_text('x')
            result = powershell(script + '; Remove-Folder ' + quote(plain))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(plain.exists())
            self.assertEqual(powershell(script + '; Remove-Folder ' + quote(root / 'missing')).returncode, 0)
            holding = root / 'holding'
            holding.mkdir()
            (holding / 'file.txt').write_text('x')
            junction(holding / 'link', elsewhere)
            linked = root / 'linked'
            junction(linked, elsewhere)
            try:
                for path in (holding, linked):
                    result = powershell(script + '; Remove-Folder ' + quote(path))
                    self.assertNotEqual(result.returncode, 0, path)
                    self.assertIn('link or junction', result.stderr)
                    self.assertTrue((elsewhere / 'keep.txt').exists())
                self.assertTrue((holding / 'file.txt').exists())        # refused before anything was removed
            finally:
                (holding / 'link').rmdir()
                linked.rmdir()

    @WINDOWS
    def test_only_this_installations_folder_counts_as_ours(self):
        script = "$InstallDir = 'C:\\Users\\A\\AppData\\Local\\Lifetime'; " + self.function('Test-Ours')
        cases = {'"C:\\Users\\A\\AppData\\Local\\Lifetime\\LifetimeTracker.exe" --run': True,
                 'pythonw.exe "c:\\users\\a\\appdata\\local\\lifetime\\lifetime_gui.py" --tray': True,
                 'C:\\Users\\A\\AppData\\Local\\Lifetime2\\LifetimeTracker.exe': False,
                 'C:\\Other\\Lifetime.exe': False, '': False}
        for text, ours in cases.items():
            result = powershell(script + '; if (Test-Ours ' + connect._ps_quote(text) + ') { "yes" } else { "no" }')
            self.assertEqual(result.stdout.strip(), 'yes' if ours else 'no', (text, result.stderr))


class ServerInstallerModeTests(unittest.TestCase):
    """Lifetime 1.2.1: Windows Server installs without Python. Without -Mode the installer must pick the ready-made
    runtime (PackagedTask) whenever LifetimeServerRuntime.exe is beside it, before it ever looks for Python."""
    text = (PROJECT / 'install_server_windows.ps1').read_text(encoding='utf-8').replace('\r\n', '\n')
    choice = ("if (-not $PSBoundParameters.ContainsKey('Mode') -and (Test-Path -LiteralPath "
              "(Join-Path $PSScriptRoot 'LifetimeServerRuntime.exe') -PathType Leaf)) {\n    $Mode = 'PackagedTask'\n}\n")

    def test_without_mode_the_packaged_runtime_is_chosen_before_python_is_looked_for(self):
        self.assertEqual(self.text.count(self.choice), 1)
        self.assertLess(self.text.index(self.choice), self.text.index("if ($Mode -ne 'PackagedTask') {"))
        self.assertLess(self.text.index(self.choice), self.text.index('Get-Command $Python'))
        # The double-click install passes no -Mode, so it gets the ready-made runtime.
        double_click = (PROJECT / 'Install Lifetime Server.cmd').read_text(encoding='utf-8')
        self.assertIn('install_server_windows.ps1" -Start', double_click)
        self.assertNotIn('-Mode', double_click)

    @WINDOWS
    def test_the_choice_really_picks_the_runtime_with_no_python_on_the_path(self):
        # Only the installer's parameters and its choice run, from a temporary folder: nothing is installed.
        param = self.text[self.text.index('[CmdletBinding()]'):self.text.index("$ErrorActionPreference = 'Stop'")]
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / 'probe.ps1'
            probe.write_text(param + self.choice + 'Write-Output $Mode\n', encoding='utf-8')
            windows = Path(__import__('os').environ.get('SystemRoot', r'C:\Windows'))
            environment = dict(__import__('os').environ, PATH=str(windows / 'System32'))       # no Python here

            def mode(*arguments):
                result = RUN([str(windows / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'), '-NoProfile',
                              '-ExecutionPolicy', 'Bypass', '-File', str(probe), *arguments],
                             capture_output=True, text=True, env=environment,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            self.assertEqual(mode(), 'SourceService')         # a source checkout: the Python modes, as before
            (Path(directory) / 'LifetimeServerRuntime.exe').write_bytes(b'')
            self.assertEqual(mode(), 'PackagedTask')          # the release package: no Python needed
            self.assertEqual(mode('-Mode', 'SourceTask'), 'SourceTask')      # named explicitly: kept


if __name__ == '__main__':
    unittest.main()
