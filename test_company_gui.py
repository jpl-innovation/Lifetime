import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtTest import QTest
from qt_test_support import qt_app
from lifetime_company import read_status
from lifetime_connection import issue, connection_line
from lifetime_connect import ConnectPage, locate_server, run_agent


class FakeTasks(QObject):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = []
    def submit(self, function):
        self.jobs.append(function)
        return len(self.jobs)
    def resolve(self, result=None, error=None, execute=False):
        if execute:
            result = self.jobs[-1]()
        if error:
            self.failed.emit(len(self.jobs), error)
        else:
            self.completed.emit(len(self.jobs), result)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.enterContext(patch('lifetime_connect.Tasks', FakeTasks))
        self.enterContext(patch.dict('os.environ', LIFETIME_REDUCE_MOTION='1'))
        self.page = ConnectPage(directory=self.root.name, machine_root=Path(self.root.name)/'agent')
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.timer.stop)
        self.page.tasks.resolve(dict(enabled=False))
        self.code = issue('0'*64)

    def test_open_reads_only_redacted_status(self):
        self.assertEqual(self.page.step, 1)
        self.assertFalse((Path(self.root.name)/'agent').exists())
        self.assertFalse(self.page.connect_button.isEnabled())
        path = Path(self.root.name)/'status.json'
        path.write_text(json.dumps(dict(enabled=True, credential='SECRET', policy=dict(history=True))), encoding='utf-8')
        self.assertNotIn('credential', read_status(self.root.name))
        self.assertEqual(read_status(self.root.name)['policy'], dict(history=True))

    def test_normalize_as_you_type_and_paste(self):
        self.page.code.setText(self.code.replace('-', '').lower())
        self.assertEqual(self.page.code.text(), self.code)
        self.assertTrue(self.page.connect_button.isEnabled())
        self.page.code.clear()
        QTest.keyClicks(self.page.code, self.code[4:].replace('-', '').lower())
        self.assertEqual(self.page.code.text(), self.code)
        self.assertTrue(self.page.connect_button.isEnabled())
        self.page.code.clear()
        QTest.keyClicks(self.page.code, self.code.lower())
        self.assertEqual(self.page.code.text(), self.code)
        self.assertTrue(self.page.connect_button.isEnabled())
        self.page.code.setText('lts-typo')
        self.assertFalse(self.page.connect_button.isEnabled())
        line = connection_line('https://example.test:8443', self.code)
        self.page.code.setText(line)
        self.assertEqual(self.page.code.text(), self.code)
        self.assertEqual(self.page._origin, 'https://example.test:8443')
        self.assertTrue(self.page.connect_button.isEnabled())

    def find(self):
        self.page.code.setText(self.code)
        QTest.keyClick(self.page.code, Qt.Key.Key_Return)
        self.assertEqual(self.page.step, 2)
        with patch('lifetime_tls.decode_bundle', return_value=dict(address='https://server.test:8443')):
            self.page.tasks.resolve('private-bundle')
        self.assertEqual(self.page.step, 3)

    def test_the_tutorial_puts_the_server_step_first_in_both_languages(self):
        from lifetime_i18n import TABLES
        for language in ('en', 'vi'):
            page = ConnectPage(directory=self.root.name, machine_root=Path(self.root.name)/'agent', language=language)
            self.addCleanup(page.close)
            self.addCleanup(page.timer.stop)
            words = [label.text() for label in page.findChildren(type(page.code_error))]
            first, then = TABLES[language]['First, on the server'], TABLES[language]['Then, on this computer']
            self.assertIn(first, words)
            self.assertIn(then, words)
            self.assertNotIn(TABLES[language]['Type that code below.'], words)    # "that code" had nothing to point at

    def test_a_computer_the_server_removed_is_told_so_and_the_dead_code_is_cleared_once(self):
        self.page._operation = 'status'
        self.page.completed(0, dict(enabled=True, phase='approved', status='connected', reported=True, server='https://s:8443'))
        self.assertEqual(self.page.step, 5)
        self.page.code.setText(self.code)                          # the old code, still sitting in the box
        removed = dict(enabled=False, status='revoked')
        self.page.completed(0, removed)
        self.assertEqual(self.page.step, 1)
        self.assertIn('removed this computer', self.page.code_error.text())
        self.assertEqual(self.page.code.text(), '')
        self.page.code.setText(self.code)                          # a new code is typed ...
        self.page.completed(0, removed)                            # ... and a later poll must not wipe it
        self.assertEqual(self.page.code.text(), self.code)
        self.page.completed(0, dict(enabled=False, status='denied'))
        self.assertIn('This code cannot be used', self.page.code_error.text())

    def test_the_last_report_is_shown_as_local_time_not_a_raw_utc_stamp(self):
        import datetime as dt
        from lifetime_i18n import datetime_text
        self.page._operation = 'status'
        self.page.completed(0, dict(enabled=True, phase='approved', status='connected', reported=True, server='https://s:8443',
                                    last_sync='2026-11-10T17:03:17Z'))
        shown = self.page.summary.text()
        self.assertNotIn('T17:03:17Z', shown)
        local = dt.datetime(2026, 11, 10, 17, 3, 17, tzinfo=dt.timezone.utc).astimezone()
        self.assertIn('Last report: ' + str(datetime_text(local)), shown)
        self.page.completed(0, dict(enabled=True, phase='approved', status='connected', reported=True, server='https://s:8443'))
        self.assertIn('Last report: Not available', self.page.summary.text())

    def test_the_title_never_says_connected_beside_cannot_reach_the_server(self):
        self.page._operation = 'status'
        up = dict(enabled=True, phase='approved', status='connected', reported=True, server='https://server:8443')
        self.page.completed(0, up)
        self.assertEqual(self.page.connected_title.text(), 'Connected')
        self.assertTrue(self.page.unreachable.isHidden())
        self.page.completed(0, dict(up, status='unreachable'))
        self.assertEqual(self.page.connected_title.text(), 'Waiting for the server')
        self.assertFalse(self.page.unreachable.isHidden())
        self.page.completed(0, dict(up, status='unreachable', error='timeout'))      # and it says why
        self.assertIn('firewall', self.page.unreachable.text())
        self.page.completed(0, dict(up, status='unreachable', error='http-503'))
        self.assertIn('503', self.page.unreachable.text())
        self.page.completed(0, dict(up, status='unreachable', error='expired'))
        self.assertIn('clock', self.page.unreachable.text())
        self.page.completed(0, dict(up, status='unreachable', error='hostname'))
        self.assertIn("server's names", self.page.unreachable.text())
        self.page.completed(0, up)                                 # and it recovers by itself
        self.assertEqual(self.page.connected_title.text(), 'Connected')
        self.assertTrue(self.page.unreachable.isHidden())
        self.assertNotIn('older version', self.page.summary.text())
        self.page.completed(0, dict(up, trimmed=True))            # an older server: never trimmed silently
        self.assertIn('older version of Lifetime', self.page.summary.text())

    def test_the_review_step_says_found_not_connected_until_it_really_is(self):
        self.find()
        self.assertEqual(self.page.review_title.text(), 'Found server.test')
        self.page._operation = 'status'
        self.page.completed(0, dict(enabled=True, status='connected', phase='approved', reported=True,
                                    server='https://server.test:8443', policy=dict(history=True)))
        self.page.change_policy()                                  # already connected: changing what is shared
        self.assertEqual(self.page.review_title.text(), 'Connected to server.test')

    def test_transitions_policy_and_no_send_before_finish(self):
        with patch('lifetime_connect.run_agent') as run:
            self.find()
            run.assert_not_called()
            self.assertTrue(self.page.awake.isChecked())
            self.assertFalse(self.page.awake.isEnabled())
            self.assertTrue(self.page.history.isChecked())
            self.assertTrue(self.page.resources.isChecked())
            self.assertFalse(self.page.control.isChecked())
            self.assertFalse(self.page.health.isChecked())
            self.page.history.setChecked(False)
            self.page.control.setChecked(True)
            QTest.keyClick(self.page.finish_button, Qt.Key.Key_Return)
            self.page.tasks.jobs[-1]()
            self.assertEqual(run.call_args.args[1], dict(history=False, resources=True, health=False, control=True,
                                                         updates=False))     # updates stay off unless chosen
        self.page.tasks.resolve(dict(enabled=True, status='pending', phase='pending', server='https://server.test:8443'))
        self.assertEqual(self.page.step, 4)
        self.page.poll()
        self.page.tasks.resolve(dict(enabled=True, status='connected', phase='approved', server='https://server.test:8443', policy=dict(history=True)))
        self.assertEqual(self.page.step, 4)         # approved, but the server has no details yet: keep the loading screen
        self.assertEqual(self.page.stage, 3)
        self.page.poll()
        self.page.tasks.resolve(dict(enabled=True, status='connected', phase='approved', reported=True, server='https://server.test:8443', policy=dict(history=True)))
        self.assertEqual(self.page.step, 5)
        self.assertIn('Awake history by day', self.page.summary.text())
        self.page.change_policy()
        self.assertEqual(self.page.step, 3)
        self.assertTrue(self.page.history.isChecked())
        self.assertFalse(self.page.control.isChecked())

    def test_update_consent_is_its_own_switch_and_shows_when_on(self):
        self.find()
        self.assertFalse(self.page.updates.isChecked())
        self.page.updates.setChecked(True)
        self.assertTrue(self.page.policy()['updates'])
        self.page._operation = 'status'
        up = dict(enabled=True, status='connected', phase='approved', reported=True, server='https://server.test:8443',
                  policy=dict(history=True), updates=True)
        self.page.completed(0, up)
        self.assertIn('Get Lifetime updates approved by the server', self.page.summary.text())
        self.page.change_policy()
        self.assertTrue(self.page.updates.isChecked())
        self.page.completed(0, dict(up, updates=False))
        self.page.change_policy()
        self.assertFalse(self.page.updates.isChecked())
        self.assertNotIn('approved by the server', self.page.summary.text())

    def test_local_health_limits_consent(self):
        with patch('lifetime_connect.read_settings', return_value=dict(completed=True, health=True)):
            other = ConnectPage(directory=self.root.name, machine_root=self.root.name)
        other.timer.stop()
        self.addCleanup(other.close)
        self.assertTrue(other.policy()['health'])
        other.health.setChecked(False)
        self.assertFalse(other.policy()['health'])
        self.page.health.setChecked(True)
        self.assertFalse(self.page.policy()['health'])

    def test_search_failure_and_uac_decline(self):
        self.page.code.setText(self.code)
        self.page.connect_server()
        self.page.tasks.resolve(error='ValueError')
        self.assertEqual(self.page.step, 1)
        self.assertIn('No server found', self.page.code_error.text())
        self.find()
        self.page.finish()
        self.page.tasks.resolve(error='PermissionError')
        self.assertEqual(self.page.step, 3)
        self.assertEqual(self.page.review_error.text(), 'Windows permission is needed to finish.')
        self.assertTrue(self.page.finish_button.isEnabled())

    def test_finish_says_why_the_agent_setup_failed_from_its_exit_code(self):
        """Every installer failure used to show the same "Could not finish connecting" message."""
        import lifetime_connect as connect
        from lifetime_i18n import VI
        script = (Path(connect.__file__).with_name('install_agent_windows.ps1')).read_text(encoding='utf-8')
        for code, text in connect.SETUP_EXIT.items():
            self.assertIn(f'Stop-Install {code} ', script)          # only codes the installer really uses
            self.assertIn(text, VI)
        self.find()
        for error, expected in (('SetupFailed:exit-3', connect.SETUP_EXIT[3]), ('SetupFailed:exit-5', connect.SETUP_EXIT[5]),
                                ('SetupFailed:exit-77', 'Could not finish connecting (error 77). Check that the server is on and try again.'),
                                ('RuntimeError', connect.FINISH_FAILED)):
            self.page.finish()
            self.page.tasks.resolve(error=error)
            self.assertEqual((self.page.step, self.page.review_error.text()), (3, expected))
        self.assertEqual(connect.SetupFailed(2).detail_code, 'exit-2')
        self.assertEqual(connect.SetupFailed(-1).detail_code, 'exit-unknown')

    def test_restart_pending_unreachable_and_used_code(self):
        self.page._operation = 'status'
        self.page.completed(0, dict(enabled=True, phase='pending', status='unreachable', server='https://server:8443'))
        self.assertEqual(self.page.step, 4)
        self.page.completed(0, dict(enabled=True, phase='approved', status='unreachable', server='https://server:8443'))
        self.assertEqual(self.page.step, 5)
        self.assertFalse(self.page.unreachable.isHidden())
        self.page.completed(0, dict(enabled=False, status='denied'))
        self.assertEqual(self.page.step, 1)
        self.assertIn('new code', self.page.code_error.text())

    def waiting(self):
        self.find()
        self.page.finish()
        self.page.tasks.resolve(dict(enabled=True, status='pending', phase='pending', server='https://server.test:8443'))

    def test_cancel_waiting_stops_reporting_and_allows_a_new_connection(self):
        self.waiting()
        self.assertFalse(self.page.waiting_cancel.isHidden())
        with patch('lifetime_connect.run_agent', return_value=dict(enabled=False)) as run:
            QTest.keyClick(self.page.waiting_cancel, Qt.Key.Key_Return)
            self.assertFalse(self.page.waiting_cancel.isEnabled())
            self.page.tasks.resolve(execute=True)
            run.assert_called_once_with('disconnect', {}, directory=self.page.machine_root)
        self.assertEqual(self.page.step, 1)
        self.assertEqual(self.page.code.text(), '')
        self.assertIsNone(self.page.bundle)
        self.find()

    def test_unreachable_enrollment_is_not_presented_as_server_approval(self):
        for state in ('enrolling', 'unreachable'):
            with self.subTest(state=state):
                self.page._operation = 'status'
                self.page.completed(0, dict(enabled=True, phase='enrolling', status=state,
                                           server='https://old-server.test:8443'))
                self.assertEqual(self.page.stage, 1)
                self.assertEqual(self.page.loading_title.text(), 'Connecting securely')
                self.assertIn('https://old-server.test:8443', self.page.waiting_text.text())
                self.assertNotIn('needs to approve', self.page.waiting_text.text())
                self.assertFalse(self.page.waiting_cancel.isHidden())
                if state == 'unreachable':
                    self.assertIn("Can't reach", self.page.waiting_text.text())
        self.page.waiting_cancel.click()
        self.page.tasks.resolve(dict(enabled=False))
        self.assertEqual(self.page.step, 1)

    def test_acknowledged_pending_request_keeps_approval_stage_and_names_server(self):
        self.waiting()
        self.assertEqual(self.page.stage, 2)
        self.assertIn('https://server.test:8443', self.page.waiting_text.text())
        self.assertIn('needs to approve', self.page.waiting_text.text())

    def test_cancel_waits_for_status_even_if_approval_arrives_or_status_fails(self):
        for error in (None, 'OSError'):
            with self.subTest(error=error):
                self.waiting()
                self.page.poll()
                self.page.waiting_cancel.click()
                self.assertEqual(self.page._operation, 'status')
                self.page.tasks.resolve(dict(enabled=True, status='connected', reported=True), error=error)
                self.assertEqual(self.page._operation, 'cancel-waiting')
                self.page.tasks.resolve(dict(enabled=False))
                self.assertEqual(self.page.step, 1)

    def test_failed_cancellation_stays_on_waiting_screen_and_can_retry(self):
        self.waiting()
        self.page.waiting_cancel.click()
        self.page.tasks.resolve(error='PermissionError')
        self.assertEqual(self.page.step, 4)
        self.assertTrue(self.page.waiting_cancel.isEnabled())
        self.assertIn('Windows permission', self.page.waiting_text.text())
        self.page.waiting_cancel.click()
        self.page.tasks.resolve(error='OSError')
        self.assertIn('Could not cancel', self.page.waiting_text.text())
        self.assertTrue(self.page.waiting_cancel.isEnabled())

    def test_cancel_button_fits_all_palettes_styles_and_languages(self):
        from lifetime_theme import THEMES, set_theme, stylesheet, active_theme
        from lifetime_glass import STYLES, set_style, active_style
        from lifetime_i18n import TABLES
        original_theme, original_style = active_theme(), active_style()
        self.addCleanup(set_theme, original_theme)
        self.addCleanup(set_style, original_style)
        for theme in THEMES:
            for style in STYLES:
                for language in TABLES:
                    with self.subTest(theme=theme, style=style, language=language):
                        set_theme(theme)
                        set_style(style)
                        page = ConnectPage(directory=self.root.name, machine_root=self.root.name, language=language)
                        page.timer.stop()
                        page.tasks.resolve(dict(enabled=True, status='pending', server='https://server.test:8443'))
                        page.setStyleSheet(stylesheet())
                        page.resize(420, 800)
                        page.show()
                        self.app.processEvents()
                        button = page.waiting_cancel
                        self.assertEqual(button.text(), TABLES[language]['Cancel connection'])
                        self.assertGreaterEqual(button.height(), 40)
                        self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 16)
                        self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
                        page.close()

    def test_search_order_and_mismatches(self):
        with patch('lifetime_discovery.discover', return_value=[dict(address='https://wrong:8443'), dict(address='https://right:8443')]), patch('lifetime_connection.bootstrap', side_effect=[ValueError(), 'verified']) as bootstrap, patch('lifetime_connection.find_server') as fallback:
            self.assertEqual(locate_server(self.code), 'verified')
            self.assertEqual(bootstrap.call_count, 2)
            fallback.assert_not_called()
        with patch('lifetime_discovery.discover') as discovery, patch('lifetime_connection.bootstrap', return_value='verified'):
            self.assertEqual(locate_server(connection_line('https://vpn:8443', self.code)), 'verified')
            discovery.assert_not_called()
        with patch('lifetime_discovery.discover', return_value=[]), patch('lifetime_connection.find_server', return_value='fallback') as fallback:
            self.assertEqual(locate_server(self.code), 'fallback')
            fallback.assert_called_once_with(self.code)

    def test_crossfade_can_be_interrupted_and_reduce_motion_has_no_effect(self):
        self.page.show()
        self.app.processEvents()
        self.page.reduce_motion = False
        self.page.show_step(2)
        self.page.show_step(3)
        QTest.qWait(220)
        self.assertEqual(self.page.step, 3)
        self.assertIsNone(self.page.steps.currentWidget().graphicsEffect())
        self.page.reduce_motion = True
        self.page.show_step(1)
        self.assertIsNone(self.page.steps.currentWidget().graphicsEffect())

    def test_disconnect_requires_confirmation(self):
        from PyQt6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            count = len(self.page.tasks.jobs)
            self.page.disconnect()
            self.assertEqual(len(self.page.tasks.jobs), count)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch('lifetime_connect.run_agent') as run:
            self.page.disconnect()
            self.page.tasks.jobs[-1]()
            self.assertEqual(run.call_args.args[0], 'disconnect')

    def test_uac_one_prompt_wait_exit_code_and_private_file_cleanup(self):
        import lifetime_connect as connect
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if command[0] == 'whoami.exe':
                return Mock(stdout='"PC\\User","S-1-5-21-123"', returncode=0)
            if command[0] == 'icacls':
                self.private_dir = Path(command[1])
                return Mock(returncode=0)
            script = command[-1]
            self.assertIn('-Verb RunAs -PassThru -WindowStyle Hidden', script)
            self.assertNotIn('-Wait', script)   # -Wait would hang on the detached agent
            self.assertIn('WaitForExit', script)
            self.bundle_path = self.private_dir/'connection.txt'
            self.assertTrue(self.bundle_path.exists())
            return Mock(returncode=1223)
        with patch.object(connect.sys, 'platform', 'win32'), patch.object(connect.subprocess, 'run', side_effect=fake_run), patch.object(connect.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            with self.assertRaises(PermissionError):
                run_agent('finish', dict(history=True), 'SECRET', self.root.name)
        self.assertEqual(sum(c[0] == 'powershell.exe' for c in calls), 1)
        self.assertFalse(self.bundle_path.exists())
        with patch.object(connect.sys, 'platform', 'win32'), patch.object(connect.subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                patch.object(connect.subprocess, 'run', side_effect=lambda command, **kwargs: fake_run(command) if command[0] != 'powershell.exe' else Mock(returncode=4)):
            with self.assertRaises(connect.SetupFailed) as caught:
                run_agent('finish', dict(history=True), 'SECRET', self.root.name)
        self.assertEqual(caught.exception.detail_code, 'exit-4')


class ServerStateTests(unittest.TestCase):
    """The Server chip comes only from the agent's status file: connected, reachable, unreachable, not enrolled."""
    NOW = 1_800_000_000

    def state(self, language='en', **status):
        from lifetime_connect import server_state
        from lifetime_i18n import TABLES
        t = lambda key, **fmt: TABLES[language][key].format(**fmt)
        return server_state(dict(dict(enabled=True, phase='approved', status='connected'), **status), t, self.NOW)

    def test_not_enrolled_is_not_connected(self):
        from lifetime_connect import server_state
        from lifetime_i18n import tr
        for status in ({}, dict(enabled=False), dict(enabled=True, phase='revoked')):
            self.assertEqual(server_state(status, tr)[:2], ('neutral', 'Not connected'))

    def test_fresh_check_online_offline_and_the_reason_per_kind(self):
        self.assertEqual(self.state(checked_at=self.NOW - 5, server_reachable=True)[:2], ('good', 'Server: Online'))
        for kind, words in (('refused', 'not running'), ('timeout', 'firewall'), ('name', 'could not be found'),
                            ('certificate', 'certificate has changed'), ('http-503', 'error 503')):
            with self.subTest(kind=kind):
                state = self.state(checked_at=self.NOW - 5, server_reachable=False, server_problem=kind,
                                   server_seen_at=self.NOW - 40)
                self.assertEqual((state[0], state[1]), ('warning', 'Server: Offline'))     # amber: nothing to fix here
                self.assertTrue(state[2].startswith('Server: Offline · last reached '))
                self.assertIn(words, state[3])
                self.assertIn('Awake time keeps counting', state[3])

    def test_a_silent_agent_is_checking_and_an_older_agent_is_judged_by_its_sync(self):
        self.assertEqual(self.state(checked_at=self.NOW - 600, server_reachable=True)[0], 'info')
        self.assertEqual(self.state()[:2], ('good', 'Server: Online'))
        state = self.state(status='unreachable', error='timeout', last_sync='2026-11-16T08:00:00Z')
        self.assertEqual(state[0], 'warning')
        self.assertIn('firewall', state[3])

    def test_vietnamese_words_fit_the_sidebar(self):
        from PyQt6.QtGui import QFont, QFontMetrics
        from lifetime_widgets_dashboard import chip_width
        qt_app()
        font = QFont('Segoe UI')
        font.setPixelSize(13)
        for state in (self.state('vi', checked_at=self.NOW, server_reachable=False, server_problem='timeout'),
                      self.state('vi', checked_at=self.NOW - 600), self.state('vi', checked_at=self.NOW, server_reachable=True)):
            with self.subTest(words=state[1]):
                self.assertTrue(state[1].startswith('Máy chủ: '))
                self.assertLessEqual(chip_width(QFontMetrics(font), state[1]), 200)     # the sidebar's 232 px less margins

    def test_the_page_chip_changes_only_when_the_state_does_and_reads_no_network(self):
        with patch('lifetime_connect.Tasks', FakeTasks), patch.dict('os.environ', LIFETIME_REDUCE_MOTION='1'), \
                tempfile.TemporaryDirectory() as root, patch('socket.create_connection') as network, \
                patch('lifetime_tls.verify_server') as probe:
            page = ConnectPage(directory=root, machine_root=root)
            self.addCleanup(page.timer.stop)
            seen = []
            page.serverState.connect(lambda *state: seen.append(state[0]))
            self.assertTrue(page.server_chip.isHidden())                # personal use: no server status at all
            status = dict(enabled=True, phase='approved', status='connected', server='https://office:8443',
                          checked_at=time.time(), server_reachable=True)
            page.tasks.resolve(status)
            self.assertFalse(page.server_chip.isHidden())
            for value in (status, dict(status, server_reachable=False, server_problem='refused'), {}):
                page.tasks.resolve(value)
            self.assertEqual(seen, ['good', 'warning', 'neutral'])
            self.assertTrue(page.server_chip.isHidden())                # disconnected: the chip goes away
            network.assert_not_called()
            probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
