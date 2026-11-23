"""The Linux desktop on any distribution: login start without systemd (XDG autostart), the bundle's programs (and the
AppImage's changing mount path), and readings that degrade to "not available" when a tool is missing."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import lifetime_import as importer
import lifetime_settings as settings
import lifetime_setup as setup
import lifetime_signals as signals

PROJECT = Path(__file__).parent


def bash_program():
    """A real bash to run the installers' shell code: Git's on Windows (never WSL's bash.exe), the system one elsewhere."""
    if sys.platform != 'win32':
        return shutil.which('bash')
    git = shutil.which('git')
    for parent in Path(git).resolve().parents if git else ():
        if (parent / 'bin' / 'bash.exe').is_file() and (parent / 'usr' / 'bin').is_dir():
            return str(parent / 'bin' / 'bash.exe')
    return None


BASH = bash_program()
WITH_BASH = unittest.skipUnless(BASH, 'bash (Git for Windows on Windows)')


def run_bash(script, **environment):
    return subprocess.run([BASH, '-s'], input=script, capture_output=True, text=True, env=dict(os.environ, **environment),
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.config = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.config)}))
        self.enterContext(patch.object(setup.sys, 'platform', 'linux'))
        self.run = self.enterContext(patch.object(setup.subprocess, 'run', return_value=Mock(returncode=0)))
        self.entry = self.config / 'autostart' / 'lifetime-tracker.desktop'

    def test_without_systemd_the_tracker_starts_through_xdg_autostart(self):
        with patch.object(setup, 'systemd_user_available', return_value=False):
            setup.configure_login(True)
            text = self.entry.read_text(encoding='utf-8')
            self.assertIn('Exec="', text)
            self.assertIn('lifetime_tracker.py" "--run"', text)
            self.assertIn('NoDisplay=true', text)
            self.run.assert_not_called()           # no systemctl where there is no systemd
            setup.configure_login(False)
            self.assertFalse(self.entry.exists())
            setup.configure_login(False)           # already off: nothing to do, no error

    def test_with_systemd_the_user_service_is_used_and_an_old_autostart_entry_removed(self):
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text('[Desktop Entry]\n', encoding='utf-8')
        with patch.object(setup, 'systemd_user_available', return_value=True):
            setup.configure_login(True)
        self.assertFalse(self.entry.exists())
        self.assertTrue((self.config / 'systemd/user/lifetime-tracker.service').is_file())
        self.assertEqual(self.run.call_args.args[0], ['systemctl', '--user', 'enable', 'lifetime-tracker.service'])

    def test_systemd_detection_needs_a_running_user_manager(self):
        with patch('shutil.which', return_value=None):
            self.assertFalse(setup.systemd_user_available())
        with patch('shutil.which', return_value='/bin/systemctl'), patch.object(setup.os.path, 'isdir', return_value=False):
            self.assertFalse(setup.systemd_user_available())       # installed but not booted (OpenRC, containers)
        with patch('shutil.which', return_value='/bin/systemctl'), patch.object(setup.os.path, 'isdir', return_value=True):
            self.run.return_value = Mock(returncode=1)
            self.assertFalse(setup.systemd_user_available())       # no user manager in this session
            self.run.return_value = Mock(returncode=0)
            self.assertTrue(setup.systemd_user_available())
            self.run.side_effect = OSError()
            self.assertFalse(setup.systemd_user_available())

    def test_desktop_exec_quotes_every_argument(self):
        self.assertEqual(setup.desktop_exec(['/opt/My Apps/Lifetime', '--run']), '"/opt/My Apps/Lifetime" "--run"')
        self.assertEqual(setup.desktop_exec(['/a"b$c`d\\e%f']), '"/a\\"b\\$c\\`d\\\\e%%f"')


class BundleTests(unittest.TestCase):
    def test_programs_beside_this_one_and_inside_an_appimage(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', r'C:\Lifetime\Lifetime.exe'):
            self.assertEqual(Path(settings.bundled_command('LifetimeTracker')[0]).name, 'LifetimeTracker.exe')
        with tempfile.TemporaryDirectory() as mount:
            executable = str(Path(mount) / 'usr/lib/lifetime/Lifetime')
            with patch.object(sys, 'platform', 'linux'), patch.object(sys, 'executable', executable):
                with patch.dict(os.environ, {'APPDIR': '', 'APPIMAGE': ''}):
                    self.assertEqual(Path(settings.bundled_command('LifetimeAgent')[0]).name, 'LifetimeAgent')
                with patch.dict(os.environ, {'APPDIR': mount, 'APPIMAGE': '/home/a/Lifetime.AppImage'}):
                    self.assertEqual(settings.bundled_command('LifetimeTracker'),
                                     ['/home/a/Lifetime.AppImage', '--run-tool', 'LifetimeTracker'])
                with patch.dict(os.environ, {'APPDIR': str(Path(mount) / 'elsewhere'), 'APPIMAGE': '/x.AppImage'}):
                    # an AppImage variable inherited by an installed copy is ignored
                    self.assertEqual(Path(settings.bundled_command('LifetimeTracker')[0]).name, 'LifetimeTracker')

    def test_apprun_only_starts_programs_of_the_bundle(self):
        text = (Path(__file__).parent / 'packaging/linux/AppRun').read_text(encoding='utf-8')
        for name in ('Lifetime', 'LifetimeTracker', 'LifetimeImport', 'LifetimeAgent', 'LifetimeServerDashboard'):
            self.assertIn(name, text)
        self.assertIn('exit 2', text)


class MissingToolTests(unittest.TestCase):
    def test_verify_without_a_journal_says_not_available(self):
        with patch.object(signals.shutil, 'which', return_value=None), \
                patch.object(signals, 'run_command') as run:
            self.assertEqual(signals.verify_linux_awake(), dict(message='Not available · this system has no systemd journal'))
            run.assert_not_called()
        import lifetime_i18n
        lifetime_i18n.set_language('vi')
        try:
            self.assertNotEqual(lifetime_i18n.tr('Not available · this system has no systemd journal'),
                                'Not available · this system has no systemd journal')
        finally:
            lifetime_i18n.set_language('en')

    def test_history_import_without_a_journal_explains_instead_of_crashing(self):
        with patch.object(importer.shutil, 'which', return_value=None), \
                patch.object(importer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'no systemd journal'):
                importer.linux_boots()
            run.assert_not_called()

    def test_missing_network_and_graphics_tools_are_never_launched(self):
        with patch.object(signals.shutil, 'which', return_value=None), patch.object(signals.subprocess, 'run') as run:
            self.assertEqual(signals.nmcli_wifi(), [])
            self.assertIsNone(signals.lspci_name('00:02.0'))
            run.assert_not_called()


class ServerDashboardMenuTests(unittest.TestCase):
    """The Lifetime Server menu entry starts the dashboard with no arguments: it finds the server on this computer."""

    def test_the_admin_port_comes_from_the_installed_settings(self):
        import lifetime_server_gui as gui
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = folder / 'server.conf'
        config.write_text('# settings\nLIFETIME_PORT=8443\nLIFETIME_ADMIN_PORT=9444\n', encoding='utf-8')
        self.assertEqual(gui.linux_admin_port(config), 9444)
        sockets = folder / 'run'
        sockets.mkdir()
        self.assertEqual(gui.linux_admin_port(folder / 'missing.conf', sockets), 8444)
        # server.conf is not readable by a desktop admin: the running server's socket names the port instead.
        (sockets / 'admin-9555.sock').touch()
        self.assertEqual(gui.linux_admin_port(folder / 'missing.conf', sockets), 9555)
        (sockets / 'admin-9556.sock').touch()        # two servers: no guessing
        self.assertEqual(gui.linux_admin_port(folder / 'missing.conf', sockets), 8444)

    def test_on_linux_the_dashboard_needs_no_arguments(self):
        import lifetime_server_gui as gui
        opened = Mock(side_effect=SystemExit)
        with patch.object(gui.sys, 'platform', 'linux'), patch.object(gui, 'linux_admin_port', return_value=9444), \
                patch.object(gui, 'create_application'), patch.object(gui, 'set_theme'), \
                patch.object(gui, 'tray_available', return_value=False), patch.object(gui, 'ServerWindow', opened):
            with self.assertRaises(SystemExit):
                gui.main([])
        self.assertEqual(opened.call_args.args[:2], ('/var/lib/lifetime-server', 9444))
        with patch.object(gui.sys, 'platform', 'win32'), self.assertRaises(SystemExit), \
                patch('sys.stderr'):
            gui.main([])            # Windows passes them from its Start menu shortcut

    def test_the_installers_put_the_dashboard_in_the_menu_for_the_person_who_installed_the_server(self):
        root = Path(__file__).parent
        server = (root / 'install_server_linux.sh').read_text(encoding='utf-8')
        # The service account's own groups are set as before, whoever installs.
        self.assertIn('usermod -a -G lifetime-admin,lifetime-console lifetime\n', server)
        # Their hidden menu entry is shown, edited as them (root never writes through a link in their home folder).
        self.assertIn('[[ -f "$entry" && ! -L "$entry" ]]', server)
        self.assertIn("runuser -u \"$extra_admin\" -- sed -i '/^NoDisplay=true$/d' \"$entry\"", server)
        self.assertNotIn('chown "$extra_admin', server)

    @WITH_BASH
    def test_lifetime_for_linux_shows_the_entry_only_with_the_server_installed_and_to_its_admins(self):
        desktop = (PROJECT / 'install_linux.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
        start = desktop.index("    hidden='NoDisplay=true\\n'")
        block = desktop[start:desktop.index("    {\n        printf '[Desktop Entry]", start)]
        self.assertEqual(block.count('/opt/lifetime-server/LifetimeServerRuntime'), 1)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / 'LifetimeServerRuntime'
            script = ('id() { if [[ "$1" == -un ]]; then echo alice; else echo "$MEMBER_OF"; fi; }\n'
                      + block.replace('/opt/lifetime-server/LifetimeServerRuntime', runtime.as_posix())
                      + 'echo "[$hidden]"\n')

            def shown(groups):
                result = run_bash(script, MEMBER_OF=groups)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip() == '[]'
            self.assertFalse(shown('alice lifetime-admin'))         # no server on this computer
            runtime.write_text('#!/bin/sh\n')
            runtime.chmod(0o755)
            self.assertTrue(shown('alice sudo lifetime-admin'))
            self.assertTrue(shown('alice lifetime-console'))
            self.assertFalse(shown('alice sudo'))                   # anyone else on the computer
            self.assertFalse(shown('alice lifetime-admins'))


    @WITH_BASH
    def test_the_person_who_ran_sudo_becomes_admin_but_a_failure_for_them_never_stops_the_install(self):
        """A directory (AD/LDAP) account is not in /etc/passwd, so usermod refused it and a plain sudo install stopped
        half-way. gpasswd adds any account; if even that fails, only an explicit --admin stops the install."""
        server = (PROJECT / 'install_server_linux.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
        block = server[server.index('admin_groups() {'):server.index('install -d -m 0700 -o lifetime')]
        stubs = ('set -euo pipefail\n'
                 'exec 3>&1\n'          # the installer silences gpasswd; the stub reports on its own channel
                 'fail() { echo "FAIL: $1" >&2; exit 1; }\n'
                 'getent() { return 0; }\n'
                 'gpasswd() { [[ "$2" != refused ]] || return 1; echo "$2 $3" >&3; }\n'     # gpasswd -a USER GROUP
                 'extra_admin="$EXTRA"\n')
        def install(extra='', sudo=''):
            environment = dict(EXTRA=extra)
            if sudo:
                environment['SUDO_USER'] = sudo
            return run_bash(stubs + block + 'echo "admin=$extra_admin"\n', **environment)
        result = install(sudo='alice')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['alice', 'lifetime-admin', 'alice', 'lifetime-console', 'admin=alice'])
        result = install(sudo='refused')                     # the implicit admin: warn with the command, carry on
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['admin='])
        self.assertIn('sudo gpasswd -a refused lifetime-admin && sudo gpasswd -a refused lifetime-console', result.stderr)
        result = install(sudo='root')                        # root itself is not a person to add
        self.assertEqual(result.stdout.split(), ['admin='])
        result = install(extra='refused', sudo='alice')      # asked for explicitly: the install stops
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL: could not add refused', result.stderr)
        result = install(extra='bob', sudo='alice')          # --admin wins over the account that ran sudo
        self.assertEqual(result.stdout.split()[-1], 'admin=bob')


class AppImageStartTests(unittest.TestCase):
    """The AppImage on a computer with Lifetime Server installed asks which program to open, remembered per person."""

    def test_settings_offer_to_ask_again_once_a_choice_is_remembered(self):
        from PyQt6.QtWidgets import QApplication
        global APPLICATION
        APPLICATION = QApplication.instance() or QApplication([])      # kept: Qt needs it alive
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        page = setup.SettingsPage(Mock(settings=settings.defaults()), directory=folder)
        self.assertTrue(page.ask_again.isHidden())          # nothing remembered: nothing to offer
        page.deleteLater()
        settings.start_choice_path(folder).write_text('server', encoding='utf-8')
        page = setup.SettingsPage(Mock(settings=settings.defaults()), directory=folder)
        self.assertFalse(page.ask_again.isHidden())
        page.ask_again.click()
        self.assertFalse(settings.start_choice_path(folder).exists())
        self.assertTrue(page.ask_again.isHidden())
        page.deleteLater()
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.server = self.folder / 'server.conf'
        self.ask = Mock(return_value=('server', False))

    def start(self, marked=True):
        environ = {'LIFETIME_APPIMAGE_START': '1'} if marked else {}
        choice = settings.appimage_start(self.ask, self.folder / 'data', environ, (str(self.server),))
        self.assertNotIn('LIFETIME_APPIMAGE_START', environ)        # nothing started from here inherits it
        return choice

    def test_without_a_server_or_with_arguments_lifetime_opens_without_a_question(self):
        self.assertEqual(self.start(), 'desktop')
        self.server.touch()
        self.assertEqual(self.start(marked=False), 'desktop')
        self.ask.assert_not_called()

    def test_the_choice_is_asked_until_remembered_and_a_closed_question_opens_nothing(self):
        self.server.touch()
        self.assertEqual(self.start(), 'server')
        self.assertEqual(self.start(), 'server')
        self.assertEqual(self.ask.call_count, 2)        # not remembered: asked again
        self.ask.return_value = (None, True)
        self.assertIsNone(self.start())
        self.assertFalse((self.folder / 'data' / settings.START_CHOICE).exists())
        self.ask.return_value = ('desktop', True)
        self.assertEqual(self.start(), 'desktop')
        self.ask.reset_mock()
        self.assertEqual(self.start(), 'desktop')
        self.ask.assert_not_called()

    def test_only_a_start_without_arguments_is_marked_and_lifetime_asks_before_anything_else(self):
        apprun = (Path(__file__).parent / 'packaging' / 'linux' / 'AppRun').read_text(encoding='utf-8')
        marked = apprun[apprun.index('if [ $# -eq 0 ]; then'):apprun.index('elif [ "${1:-}" = "--run-tool" ]; then')]
        self.assertIn('export LIFETIME_APPIMAGE_START', marked)
        self.assertEqual(apprun.count('LIFETIME_APPIMAGE_START=1'), 1)
        gui = (Path(__file__).parent / 'lifetime_gui.py').read_text(encoding='utf-8')
        main = gui[gui.index("if __name__ == '__main__':"):]
        self.assertLess(main.index('set_style('), main.index('appimage_start(ask_start_program)'))   # in its look
        self.assertLess(main.index('appimage_start(ask_start_program)'), main.index("tell_running_instance('lifetime-gui')"))
        self.assertIn("os.execve(program, [program, '--language', language]", gui)     # the AppImage stays mounted


class ShellBehaviourTests(unittest.TestCase):
    """The shell code itself, run in bash with stand-ins for what needs root or an AppImage."""

    @WITH_BASH
    def test_apprun_marks_only_a_start_without_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'usr' / 'lib' / 'lifetime').mkdir(parents=True)
            for name in ('Lifetime', 'LifetimeServerDashboard'):
                program = root / 'usr' / 'lib' / 'lifetime' / name
                program.write_text('#!/bin/sh\necho "' + name + ' ${LIFETIME_APPIMAGE_START:-unmarked} $*"\n', newline='\n')
                program.chmod(0o755)
            apprun = root / 'AppRun'
            apprun.write_bytes((PROJECT / 'packaging' / 'linux' / 'AppRun').read_bytes().replace(b'\r\n', b'\n'))
            environment = {key: value for key, value in os.environ.items() if key != 'LIFETIME_APPIMAGE_START'}

            def start(*arguments):
                return subprocess.run([BASH, apprun.as_posix(), *arguments], capture_output=True, text=True,
                                      env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(start().stdout.strip(), 'Lifetime 1')                      # may ask
            self.assertEqual(start('--tray').stdout.strip(), 'Lifetime unmarked --tray')
            self.assertEqual(start('--run-tool', 'Lifetime').stdout.strip(), 'Lifetime unmarked')     # never asks
            self.assertEqual(start('--run-tool', 'LifetimeServerDashboard').stdout.strip(),
                             'LifetimeServerDashboard unmarked')
            self.assertEqual(start('--run-tool', 'Other').returncode, 2)

    @WITH_BASH
    def test_the_server_installer_shows_the_admins_menu_entry_editing_it_as_them(self):
        server = (PROJECT / 'install_server_linux.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
        block = server[server.index('# Lifetime for Linux already installed for that person'):
                       server.index("printf 'Group membership takes effect")]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            # /etc/passwd separates fields with ':', so Git for Windows' bash gets /c/... for C:/...
            passwd_home = home.as_posix()
            if sys.platform == 'win32' and passwd_home[1:2] == ':':
                passwd_home = '/' + passwd_home[0].lower() + passwd_home[2:]
            stubs = ('set -euo pipefail\n'
                     'getent() { echo "alice:x:1000:1000::$HOME_DIR:/bin/bash"; }\n'
                     'runuser() { echo "as $2" >&2; shift 3; "$@"; }\n'      # runuser -u USER -- COMMAND...
                     'extra_admin=alice\n')
            result = run_bash(stubs + block, HOME_DIR=passwd_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('install Lifetime for Linux as alice', result.stdout)       # no entry yet
            entry = home / '.local' / 'share' / 'applications' / 'lifetime-server.desktop'
            entry.parent.mkdir(parents=True)
            entry.write_text('[Desktop Entry]\nName=Lifetime Server\nNoDisplay=true\nCategories=Utility;\n', newline='\n')
            result = run_bash(stubs + block, HOME_DIR=passwd_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr.strip(), 'as alice')                       # edited as that person
            self.assertNotIn('NoDisplay', entry.read_text(encoding='utf-8'))
            self.assertIn('alice signs out and back in once', result.stdout)


if __name__ == '__main__':
    unittest.main()
