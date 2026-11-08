import copy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qt_test_support import qt_app
from lifetime_server_export import ServerExportDialog
from test_server_reports import device


class ServerExportTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.source = device('one')
        self.threads = []
        def call(operation, **arguments):
            self.threads.append(threading.get_ident())
            return [copy.deepcopy(self.source)] if operation == 'devices' else copy.deepcopy(self.source)
        self.client = SimpleNamespace(directory=Path(self.root.name), call=call)
        self.dialog = ServerExportDialog(self.client, ['one'])
        self.addCleanup(self.dialog.close)

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

    def test_read_failure_is_visible(self):
        self.client.call = lambda *a, **k: (_ for _ in ()).throw(PermissionError('secret'))
        self.dialog.refresh_preview()
        self.wait()
        self.assertIsNone(self.dialog.reviewed_report)
        self.assertIn('Could not export', self.dialog.result.text())
        self.assertNotIn('secret', self.dialog.result.text())


if __name__ == '__main__':
    unittest.main()
