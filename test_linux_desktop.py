"""The Linux desktop on any distribution: login start without systemd (XDG autostart), the bundle's programs (and the
AppImage's changing mount path), and readings that degrade to "not available" when a tool is missing."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import lifetime_import as importer
import lifetime_settings as settings
import lifetime_setup as setup
import lifetime_signals as signals


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
        # sudo ./install_server_linux.sh makes the person who ran it Admin; root itself is not "a person".
        self.assertIn('[[ -n "$extra_admin" || -z "${SUDO_USER:-}" || "$SUDO_USER" == root ]] || extra_admin="$SUDO_USER"', server)
        self.assertLess(server.index('extra_admin="$SUDO_USER"'),
                        server.index('usermod -a -G lifetime-admin,lifetime-console "$extra_admin"'))
        # Their hidden menu entry is shown, edited as them (root never writes through a link in their home folder).
        self.assertIn('[[ -f "$entry" && ! -L "$entry" ]]', server)
        self.assertIn("runuser -u \"$extra_admin\" -- sed -i '/^NoDisplay=true$/d' \"$entry\"", server)
        self.assertNotIn('chown "$extra_admin', server)
        # Lifetime for Linux shows the entry by itself when the server is already installed.
        desktop = (root / 'install_linux.sh').read_text(encoding='utf-8')
        self.assertIn("[[ ! -x /opt/lifetime-server/LifetimeServerRuntime ]] || hidden=''", desktop)


class AppImageStartTests(unittest.TestCase):
    """The AppImage on a computer with Lifetime Server installed asks which program to open, remembered per person."""

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


if __name__ == '__main__':
    unittest.main()
