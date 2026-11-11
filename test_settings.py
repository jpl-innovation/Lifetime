"""Consent boundaries, atomic persistence, history reset and setup navigation."""
from concurrent.futures import Future
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox
import lifetime_dashboard as dashboard
import lifetime_gui as gui
import lifetime_settings as settings
import lifetime_tracker as tracker
from lifetime_setup import SetupDialog, configure_login
from qt_test_support import qt_app


class SettingsTests(unittest.TestCase):
    def test_remote_power_defaults_off_and_requires_explicit_true(self):
        import lifetime_settings as settings
        self.assertFalse(settings.defaults()['remote_power'])
        self.assertFalse(settings.normalize({'remote_power': 1})['remote_power'])
        self.assertTrue(settings.normalize({'remote_power': True})['remote_power'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_style_defaults_to_classic_round_trips_and_ignores_unknown_values(self):
        self.assertEqual(settings.defaults()['style'], 'classic')
        self.assertEqual(settings.load_settings(self.root)['style'], 'classic')
        self.assertEqual(settings.update_settings({'style': 'glass'}, self.root)['style'], 'glass')
        self.assertEqual(settings.load_settings(self.root)['style'], 'glass')
        settings.save_settings(dict(settings.load_settings(self.root), completed=True), self.root)   # a full save keeps it
        self.assertEqual(settings.load_settings(self.root)['style'], 'glass')
        settings.save_settings({'completed': True}, self.root)                   # and so does one that does not mention it
        self.assertEqual(settings.load_settings(self.root)['style'], 'glass')
        settings.update_settings({'theme': 'light'}, self.root)                  # other preferences leave it alone
        self.assertEqual(settings.load_settings(self.root)['style'], 'glass')
        for bad in ('neon', None, 3, ['glass']):
            (self.root/'settings.json').write_text(json.dumps({'style': bad}))
            self.assertEqual(settings.load_settings(self.root)['style'], 'classic')

    def test_defaults_missing_invalid_and_partial(self):
        self.assertEqual(settings.load_settings(self.root), settings.defaults())
        path = self.root/'settings.json'
        for raw in ('bad json', 'null', '[]', '{"network":"yes", "completed":1}'):
            path.write_text(raw)
            self.assertEqual(settings.load_settings(self.root), settings.defaults())
        path.write_text('{"completed":true,"battery":true,"awake":false}')
        value = settings.load_settings(self.root)
        self.assertTrue(value['battery'])
        self.assertTrue(value['awake'])
        self.assertFalse(value['wifi_name'])

    def test_atomic_save_roundtrip_and_failure_preserves_old_settings(self):
        value = dict(completed=True, wifi_name=True, history=True, autostart=False)
        settings.save_settings(value, self.root)
        self.assertEqual(settings.load_settings(self.root), settings.normalize(value))
        previous = (self.root/'settings.json').read_bytes()
        with patch.object(tracker.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                settings.save_settings(dict(completed=True, battery=True), self.root)
        self.assertEqual((self.root/'settings.json').read_bytes(), previous)
        self.assertEqual(len(list(self.root.glob('settings.json*'))), 1)

    def test_tracker_does_not_write_before_consent(self):
        with patch.object(tracker, 'awake_clock') as clock:
            tracker.run(self.root)
        clock.assert_not_called()
        self.assertFalse((self.root/'ledger.json').exists())

    def test_manual_boot_credit_respects_disabled_history(self):
        settings.save_settings(dict(completed=True, history=False), self.root)
        with patch.object(tracker, 'awake_clock', return_value=100), patch.object(tracker, 'boot_id', return_value='a'):
            ledger = tracker.credit_current_boot(self.root)
        self.assertEqual(ledger['daily'], {})
        self.assertEqual(ledger['sessions'], [])

    def test_login_windows_uses_current_user_and_no_elevation(self):
        import base64
        with patch('lifetime_setup.sys.platform', 'win32'), \
                patch('lifetime_setup.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            configure_login(True)
            encoded = run.call_args.args[0][-1]
            script = base64.b64decode(encoded).decode('utf-16le')
            self.assertIn('WindowsIdentity]::GetCurrent().Name', script)
            self.assertIn('-RunLevel Limited', script)
            self.assertNotIn('RunAs', script)
            configure_login(False)
            script = base64.b64decode(run.call_args.args[0][-1]).decode('utf-16le')
            self.assertIn('Disable-ScheduledTask', script)

    def test_login_linux_user_service_only(self):
        with patch('lifetime_setup.sys.platform', 'linux'), \
                patch.dict('os.environ', {'XDG_CONFIG_HOME': str(self.root)}), \
                patch('lifetime_setup.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            configure_login(True)
            unit = self.root/'systemd/user/lifetime-tracker.service'
            self.assertIn('--run', unit.read_text())
            self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'enable', 'lifetime-tracker.service'])
            configure_login(False)
            self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'disable', 'lifetime-tracker.service'])

    def test_disabled_history_does_not_collect_dates_hours_or_sessions(self):
        ledger = tracker.new_ledger()
        tracker.sample(ledger, 100, 'boot', history=False)
        tracker.sample(ledger, 130, 'boot', history=False)
        self.assertEqual(ledger['total_seconds'], 130)
        self.assertEqual(ledger['daily'], {})
        self.assertEqual(ledger['hourly'], [0]*24)
        self.assertEqual(ledger['sessions'], [])
        self.assertIsNone(ledger['history_started'])

    def test_delete_backs_up_before_reset_and_does_not_recredit_boot(self):
        self.root.mkdir(exist_ok=True)
        ledger = tracker.new_ledger()
        ledger['total_seconds'] = 1000
        tracker.atomic_json(self.root/'ledger.json', ledger)
        with patch.object(tracker, 'awake_clock', return_value=100), patch.object(tracker, 'boot_id', return_value='a'):
            backup = settings.delete_history(self.root)
        self.assertEqual(json.loads((backup/'ledger.json').read_text())['total_seconds'], 1000)
        fresh = tracker.read_ledger(self.root)
        tracker.sample(fresh, 130, 'a')
        self.assertEqual(fresh['total_seconds'], 30)


class ConsentGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def test_worker_never_submits_disabled_probes(self):
        worker = dashboard.ProbeWorker(settings=dict(completed=True, network=True))
        self.addCleanup(worker.stop)
        worker.timer.stop()
        with patch.object(worker.executor, 'submit', return_value=Future()) as submit:
            worker.poll()
            self.assertEqual(set(worker.jobs), {'clocks', 'network', 'counters'})
            self.assertEqual(submit.call_count, 3)
        self.assertNotIn('wifi', worker.probes)
        worker.set_settings(settings.defaults())
        with patch.object(worker.executor, 'submit') as submit:
            worker.poll()
            submit.assert_not_called()

    def test_each_category_enables_only_its_probes(self):
        for category, expected in (
            ('battery', {'power', 'battery'}), ('network', {'network', 'counters'}),
            ('wifi_name', {'wifi'}), ('graphics', {'gpu'}),
            ('resources', {'cpu', 'ram', 'disks', 'identity'})):
            self.assertEqual(settings.allowed_probes(dict(completed=True, **{category: True})), expected | {'clocks'})

    def test_revoked_inflight_readings_are_discarded(self):
        worker = dashboard.ProbeWorker(settings=dict(completed=True, wifi_name=True))
        self.addCleanup(worker.stop)
        worker.timer.stop()
        pending = Future()
        pending.set_result([{'ssid': 'Sensitive'}])
        worker.jobs['wifi'] = (pending, time.monotonic(), False)
        worker.set_settings(dict(completed=True))
        worker.probes = {}
        worker.poll()
        self.assertNotIn('wifi', worker.values)

    def test_setup_back_next_summary_and_save_once(self):
        with tempfile.TemporaryDirectory() as directory, patch('lifetime_setup.configure_login') as login:
            dialog = SetupDialog(gui.THEMES['dark'], directory=directory)
            self.assertTrue(dialog.checks['awake'].isChecked())
            self.assertFalse(dialog.checks['awake'].isEnabled())
            self.assertFalse(dialog.checks['wifi_name'].isChecked())
            dialog.checks['history'].setChecked(True)
            dialog.advance()
            self.assertEqual(dialog.progress.text(), 'Step 2 of 5')
            dialog.go(0)
            self.assertTrue(dialog.checks['history'].isChecked())
            dialog.go(4)
            self.assertIn('Daily and hourly history', dialog.summary.text())
            self.assertFalse((Path(directory)/'settings.json').exists())
            dialog.advance()
            self.assertTrue(settings.load_settings(directory)['completed'])
            login.assert_not_called()

    def test_disabled_groups_and_no_battery_rows(self):
        card = dashboard.SystemDashboard()
        card.set_settings(dict(battery=True))
        self.assertFalse(card.cards[0].isHidden())
        self.assertTrue(all(c.isHidden() for c in card.cards[1:]))
        card.update_values({'power': {'state': 'No battery'}}, gui.THEMES['dark'])
        self.assertEqual(list(card.cards[0].fields), ['Battery'])
        self.assertEqual(card.cards[0].fields['Battery'][0].text(), 'No battery')

    def test_delete_cancel_does_not_touch_data(self):
        dialog = SetupDialog(gui.THEMES['dark'], dict(completed=True))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No), \
                patch('lifetime_setup.delete_history') as delete:
            dialog.confirm_delete()
            delete.assert_not_called()

    def test_select_all_toggles_every_optional_choice_and_stays_in_sync(self):
        dialog = SetupDialog(gui.THEMES['dark'], dict(completed=False))
        optional = [box for key, box in dialog.checks.items() if key != 'awake']
        self.assertTrue(dialog.checks['awake'].isChecked())
        self.assertFalse(dialog.select_all.isChecked())
        dialog.select_all.setChecked(True)
        self.assertTrue(all(box.isChecked() for box in optional))
        optional[0].setChecked(False)                      # un-ticking one item un-ticks "Select all"
        self.assertFalse(dialog.select_all.isChecked())
        optional[0].setChecked(True)                       # ticking the last one ticks it again
        self.assertTrue(dialog.select_all.isChecked())
        dialog.select_all.setChecked(False)
        self.assertFalse(any(box.isChecked() for box in optional))
        self.assertTrue(dialog.checks['awake'].isChecked())   # the required item can never be switched off


if __name__ == '__main__':
    unittest.main()
