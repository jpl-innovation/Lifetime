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


if __name__ == '__main__':
    unittest.main()
