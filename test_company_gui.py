import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from qt_test_support import qt_app
from lifetime_company import CompanyDialog, read_status


class CompanyTests(unittest.TestCase):
    def wait(self, dialog):
        deadline = time.monotonic() + 5
        while dialog._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(dialog._busy)

    def setUp(self):
        self.app = qt_app()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def test_open_does_not_initialize_or_contact_server(self):
        machine = Path(self.root.name) / 'not-installed'
        with patch('lifetime_tls.request_json', side_effect=AssertionError('Unexpected network')):
            dialog = CompanyDialog(directory=self.root.name, machine_root=machine)
            self.wait(dialog)
            self.assertFalse(machine.exists())
            self.assertFalse(dialog.history.isChecked())
            self.assertFalse(dialog.resources.isChecked())
            self.assertFalse(dialog.identity.isChecked())
            self.assertFalse(dialog.consent.isChecked())
            self.assertFalse(dialog.pair.isEnabled())
            dialog.consent.setChecked(True)
            self.assertFalse(dialog.pair.isEnabled())
            dialog.close()

    def test_status_whitelist_and_unicode_small_window(self):
        root = Path(self.root.name)
        (root / 'status.json').write_text(json.dumps(dict(enabled=True, server='https://server:8443',
            status='pending', last_sync=None, pending=20, dropped=1, credential='secret')), encoding='utf-8')
        self.assertNotIn('credential', read_status(root))
        dialog = CompanyDialog(directory=root, machine_root=root, language='vi')
        dialog.resize(390, 440)
        dialog.show()
        self.wait(dialog)
        self.app.processEvents()
        self.assertEqual(dialog.windowTitle(), 'Máy chủ công ty')
        self.assertNotIn('secret', dialog.summary.text())
        self.assertIn('20', dialog.summary.text())
        self.assertLessEqual(dialog.content.minimumSizeHint().width(), dialog.scroll.viewport().width())
        self.assertTrue(dialog.consent.accessibleName())
        dialog.close()

    def test_missing_and_bad_status(self):
        root = Path(self.root.name)
        self.assertFalse(read_status(root)['enabled'])
        (root / 'status.json').write_text('bad', encoding='utf-8')
        with self.assertRaises(ValueError):
            read_status(root)


if __name__ == '__main__':
    unittest.main()
