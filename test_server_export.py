import copy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qt_test_support import dispose, qt_app
import lifetime_insights as insights
from lifetime_server_export import ServerExportDialog
from test_server_reports import device, measured


class ServerExportTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.source = device('one')
        self.threads = []
        self.requests = []

        def call(operation, **arguments):
            self.threads.append(threading.get_ident())
            self.requests.append((operation, arguments))
            if operation == 'availability':
                return dict(devices=[measured(identifier) for identifier in arguments['device_ids']])
            if operation == 'insights':
                return dict(settings=insights.settings(), computers=[dict(
                    device_id=self.source['device_id'], energy={},
                    power=dict(watts=20.0, method='estimated', source='typical-laptop', readings=0))])
            if operation == 'alert_rules':
                return [dict(rule='disk_low', threshold=10), dict(rule='offline', threshold=None)]
            return [copy.deepcopy(self.source)] if operation == 'devices' else copy.deepcopy(self.source)
        self.client = SimpleNamespace(directory=Path(self.root.name), call=call)
        self.dialog = ServerExportDialog(self.client, ['one'])
        self.addCleanup(dispose, self.dialog)

    def wait(self):
        deadline = time.monotonic() + 5
        while self.dialog._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(self.dialog._busy)

    def test_preview_off_thread_and_options_invalidation(self):
        self.dialog.identity_checks['display_name'].setChecked(True)
        self.dialog.refresh_preview()
        self.wait()
        self.assertTrue(self.dialog.reviewed_report['identity'])
        self.assertIn(('energy', 'Power method', 'estimated: typical laptop'), self.dialog.reviewed_report['extra_rows'])
        self.assertTrue(self.dialog.save_csv.isEnabled())
        self.assertTrue(all(t != threading.get_ident() for t in self.threads))
        self.dialog.system_checks['system'].setChecked(True)
        self.assertIsNone(self.dialog.reviewed_report)
        self.assertFalse(self.dialog.save_csv.isEnabled())

    def test_cancel_and_changed_consent_before_save(self):
        self.dialog.system_checks['system'].setChecked(True)
        self.dialog.refresh_preview()
        self.wait()
        self.assertEqual(self.dialog.reviewed_report['system']['system'], 'Client OS')
        before = len(self.threads)
        with patch('lifetime_server_export.QFileDialog.getSaveFileName', return_value=('', '')):
            self.dialog.save_report('csv')
        self.assertEqual(len(self.threads), before)
        self.source['policy']['resources'] = False
        path = str(Path(self.root.name) / 'report.csv')
        with patch('lifetime_server_export.QFileDialog.getSaveFileName', return_value=(path, '')):
            self.dialog.save_report('csv')
        self.wait()
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.dialog.reviewed_report['system'], {})
        self.assertIn('changed', self.dialog.result.text())

    def test_save_matches_review_and_enforces_extension(self):
        self.dialog.refresh_preview()
        self.wait()
        created = self.dialog.reviewed_report['created']
        path = Path(self.root.name) / 'server.sqlite'
        path.write_text('original')
        with patch('lifetime_server_export.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.dialog.save_report('csv')
        self.wait()
        self.assertEqual(path.read_text(), 'original')
        exported = Path(str(path) + '.csv').read_text(encoding='utf-8-sig')
        self.assertIn(created, exported)
        self.assertIn('Report saved.', self.dialog.result.text())

    def test_a_computer_reporting_again_after_the_preview_does_not_block_the_save(self):
        # A connected computer reports every second, so its observation and receipt times differ on every
        # build. Only a real change (permissions, identity, totals) may ask for a fresh review.
        self.dialog.refresh_preview()
        self.wait()
        self.source['last_observed_at'] = '2026-11-07T12:00:05+07:00'    # seconds later, same day
        self.source['last_received_at'] = '2026-11-07T05:01:06Z'
        path = Path(self.root.name) / 'live.csv'
        with patch('lifetime_server_export.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.dialog.save_report('csv')
        self.wait()
        self.assertTrue(path.exists())
        self.assertIn('Report saved.', self.dialog.result.text())

    def test_availability_uses_one_fixed_as_of_and_a_group_scope(self):
        self.dialog.refresh_preview()
        self.wait()
        rows = self.dialog.reviewed_report['extra_rows']
        self.assertIn(('availability', 'Observed availability', '90.909 %'), rows)
        first = [arguments for operation, arguments in self.requests if operation == 'availability']
        path = Path(self.root.name) / 'saved.csv'
        with patch('lifetime_server_export.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.dialog.save_report('csv')
        self.wait()
        calls = [arguments for operation, arguments in self.requests if operation == 'availability']
        self.assertEqual(calls[0]['as_of'], calls[-1]['as_of'])         # the save measures what was reviewed
        self.assertTrue(first[0]['daily'])
        self.assertIn('Observed availability', path.read_text(encoding='utf-8-sig'))
        fleet = ServerExportDialog(self.client, None, groups=['Sales', 'Lab'])
        self.addCleanup(dispose, fleet)
        self.assertEqual(fleet.scope.count(), 3)
        fleet.scope.setCurrentIndex(1)
        self.dialog = fleet
        fleet.refresh_preview()
        self.wait()
        self.assertIn(('devices', dict(limit=25, offset=0, group='Sales')), self.requests)
        self.assertIn(('report', 'Report scope', 'Group: Sales'), fleet.reviewed_report['extra_rows'])

    def test_read_failure_is_visible(self):
        self.client.call = lambda *a, **k: (_ for _ in ()).throw(PermissionError('secret'))
        self.dialog.refresh_preview()
        self.wait()
        self.assertIsNone(self.dialog.reviewed_report)
        self.assertIn('Could not export', self.dialog.result.text())
        self.assertNotIn('secret', self.dialog.result.text())


if __name__ == '__main__':
    unittest.main()
