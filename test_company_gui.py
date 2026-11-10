import json
from pathlib import Path
import tempfile
import time
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
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
        self.assertEqual(self.page.connected_title.text(), 'Not connected right now')
        self.assertFalse(self.page.unreachable.isHidden())
        self.page.completed(0, up)                                 # and it recovers by itself
        self.assertEqual(self.page.connected_title.text(), 'Connected')
        self.assertTrue(self.page.unreachable.isHidden())

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
            self.assertEqual(run.call_args.args[1], dict(history=False, resources=True, health=False, control=True))
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


if __name__ == '__main__':
    unittest.main()
