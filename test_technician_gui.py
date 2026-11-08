"""Headless technician workflows; every ledger/settings/output is synthetic."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_STYLE_OVERRIDE'] = 'Fusion'
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

import lifetime_reports as report_engine
import lifetime_settings as settings
import lifetime_technician as ui

from qt_test_support import qt_app
APP = qt_app()


class TechnicianDialogsTests(unittest.TestCase):
    def test_export_never_overwrites_ledger_or_settings_when_user_chooses_their_names(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        for name in ('ledger.json', 'settings.json'):
            target = self.directory/name
            before = target.read_bytes()
            with self.destination(target):
                dialog.save_report('csv')
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue((self.directory/(name+'.csv')).exists())

    def test_unbroken_device_name_and_large_counters_do_not_widen_main_window(self):
        import lifetime_gui as gui
        name = 'X'*2000
        settings.update_settings({'identity': {'display_name': name}}, self.directory)
        large = dict(self.ledger, total_seconds=10**18, longest_session_seconds=10**16)
        with patch.object(gui, 'data_dir', return_value=self.directory), patch.object(gui, 'read_ledger', return_value=large):
            window = gui.Window()
            self.addCleanup(window.close)
            window.resize(390, 440)
            window.show()
            APP.processEvents()
            self.assertLessEqual(window.scroll.widget().width(), window.scroll.viewport().width())
            self.assertEqual(window.device_name.accessibleName(), name)

    def test_keyboard_identity_entry_and_save(self):
        dialog = self.dialog(ui.IdentityDialog)
        dialog.show()
        APP.processEvents()
        dialog.editors['display_name'].setFocus()
        QTest.keyClicks(dialog.editors['display_name'], 'Reception PC')
        QTest.keyClick(dialog.editors['display_name'], Qt.Key.Key_Tab)
        self.assertTrue(dialog.editors['asset_tag'].hasFocus())
        QTest.keyClicks(dialog.editors['asset_tag'], 'PC-014')
        save = dialog.actions.itemAt(1).widget()
        for _ in range(12):
            if save.hasFocus():
                break
            QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Tab)
        self.assertTrue(save.hasFocus())
        QTest.keyClick(save, Qt.Key.Key_Space)
        saved = settings.read_settings(self.directory)['identity']
        self.assertEqual(saved['display_name'], 'Reception PC')
        self.assertEqual(saved['asset_tag'], 'PC-014')

    def test_keyboard_reaches_main_export_action(self):
        import lifetime_gui as gui
        settings.update_settings({'identity': {'display_name': 'Reception PC'}}, self.directory)
        with patch.object(gui, 'data_dir', return_value=self.directory), patch.object(gui, 'read_ledger', return_value=self.ledger):
            window = gui.Window()
            self.addCleanup(window.close)
            window.show()
            APP.processEvents()
            window.navigation.group.button(3).click()
            first = window.navigation.group.button(0)
            first.setFocus()
            export = window.technician_actions['Export report']
            for _ in range(30):
                if export.hasFocus():
                    break
                QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Tab)
            self.assertTrue(export.hasFocus())
            with patch.object(ui.ExportDialog, 'exec', return_value=0) as show:
                QTest.keyClick(export, Qt.Key.Key_Space)
            show.assert_called_once()

    def test_preview_scrolls_into_small_window_before_save(self):
        dialog = self.dialog()
        dialog.resize(390, 440)
        dialog.show()
        APP.processEvents()
        dialog.refresh_preview()
        APP.processEvents()
        top = dialog.preview.y() - dialog.scroll.verticalScrollBar().value()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + dialog.preview.height(), dialog.scroll.viewport().height())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.ledger = {
            'total_seconds': 10800, 'baseline_seconds': 7200, 'boots': 2,
            'first_seen': '2026-01-01', 'recording_started': '2026-02-01T00:00:00+00:00',
            'last_update': '2026-02-02T12:00:00+00:00',
            'daily': {'2026-02-01': 3600, '2026-02-02': 7200},
            'sessions': [], 'current_session_seconds': 3600,
            'longest_session_seconds': 7200, 'hourly': [0] * 24,
        }
        self.write_ledger()
        settings.save_settings(dict(completed=True, history=True, resources=True, language='en'), self.directory)
        self.estimator = patch('lifetime_tracker.estimate_first_seen', return_value='2026-01-01')
        self.estimator.start()
        self.addCleanup(self.estimator.stop)
        self.warning = patch.object(ui.QMessageBox, 'warning')
        self.warnings = self.warning.start()
        self.addCleanup(self.warning.stop)
        self.hostnames = patch('platform.node', side_effect=AssertionError('Hostname must not be read'))
        self.hostnames.start()
        self.addCleanup(self.hostnames.stop)

    def write_ledger(self):
        (self.directory / 'ledger.json').write_text(json.dumps(self.ledger), encoding='utf-8')

    def dialog(self, cls=ui.ExportDialog, language='en'):
        value = cls(directory=self.directory, language=language)
        self.addCleanup(value.close)
        return value

    def destination(self, path):
        return patch.object(ui.QFileDialog, 'getSaveFileName', return_value=(str(path), ''))

    def test_identity_is_optional_and_preserves_fresh_consent(self):
        settings.update_settings({'identity': {'display_name': 'Old'}, 'consent': {'export': True}}, self.directory)
        dialog = self.dialog(ui.IdentityDialog)
        settings.update_settings({'consent': {'export': False}, 'future_setting': 42}, self.directory)
        dialog.editors['display_name'].setText('  Máy dùng thử  ')
        dialog.save()
        saved = settings.read_settings(self.directory)
        self.assertEqual(saved['identity']['display_name'], 'Máy dùng thử')
        self.assertEqual(saved['identity']['deployment_date'], '')
        self.assertEqual(saved['language'], 'en')
        self.assertFalse(saved['consent']['export'])
        self.assertEqual(saved['future_setting'], 42)

    def test_invalid_deployment_date_does_not_write(self):
        dialog = self.dialog(ui.IdentityDialog, 'vi')
        before = (self.directory/'settings.json').read_bytes()
        for date in ('20260201', '2026-02-30', 'yesterday'):
            dialog.editors['deployment_date'].setText(date)
            dialog.save()
            self.assertEqual((self.directory/'settings.json').read_bytes(), before)
        self.assertEqual(self.warnings.call_count, 3)
        self.assertIn('YYYY-MM-DD', self.warnings.call_args.args[2])

    def test_identity_cancel_does_not_create_settings(self):
        dialog = self.dialog(ui.IdentityDialog)
        before = (self.directory/'settings.json').read_bytes()
        dialog.editors['display_name'].setText('Uncommitted')
        dialog.reject()
        self.assertEqual((self.directory/'settings.json').read_bytes(), before)

    def test_defaults_exclude_all_optional_metadata_and_hostname(self):
        settings.update_settings({'identity': {'display_name': 'Private'},
                                  'system_fields': ['system', 'machine']}, self.directory)
        dialog = self.dialog()
        self.assertFalse(dialog.save_pdf.isEnabled())
        self.assertTrue(all(not check.isChecked() for check in dialog.identity_checks.values()))
        self.assertTrue(all(not check.isChecked() for check in dialog.system_checks.values()))
        dialog.refresh_preview()
        self.assertEqual(dialog.reviewed_report['identity'], {})
        self.assertEqual(dialog.reviewed_report['system'], {})
        self.assertNotIn('hostname', dialog.system_checks)
        self.assertTrue(dialog.save_pdf.isEnabled())

    def test_individual_system_fields_and_inclusive_period(self):
        dialog = self.dialog()
        dialog.range_select.setCurrentIndex(1)
        dialog.start_date.setText('2026-02-01')
        dialog.end_date.setText('2026-02-02')
        dialog.system_checks['machine'].setChecked(True)
        with patch('platform.machine', return_value='synthetic-arch') as getter:
            dialog.refresh_preview()
        getter.assert_called_once()
        report = dialog.reviewed_report
        self.assertEqual(report['system'], {'machine': 'synthetic-arch'})
        self.assertEqual(report['stats']['awake_seconds'], 10800)
        self.assertIsNone(report['stats']['baseline_seconds'])
        self.assertEqual(len(report['daily']), 2)

    def test_bad_date_ranges_are_localized_and_disable_save(self):
        dialog = self.dialog(language='vi')
        dialog.range_select.setCurrentIndex(1)
        dialog.start_date.setText('2026-02-02')
        dialog.end_date.setText('2026-02-01')
        dialog.refresh_preview()
        self.assertFalse(dialog.save_pdf.isEnabled())
        self.assertIn('Ngày bắt đầu', self.warnings.call_args.args[2])
        dialog.start_date.clear()
        dialog.refresh_preview()
        self.assertIn('YYYY-MM-DD', self.warnings.call_args.args[2])

    def test_changed_controls_require_new_preview(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        dialog.identity_checks['department'].click()
        self.assertIsNone(dialog.reviewed_report)
        self.assertFalse(dialog.save_csv.isEnabled())
        self.assertEqual(dialog.preview.toPlainText(), '')

    def test_cancellation_and_overwrite_no_preserve_destination(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        target = self.directory / 'report.csv'
        target.write_text('keep me', encoding='utf-8')
        with self.destination(''), patch.object(report_engine, 'write_csv') as writer:
            dialog.save_report('csv')
            writer.assert_not_called()
        with self.destination(target), patch.object(ui.QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            dialog.save_report('csv')
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep me')

    def test_fresh_ledger_after_file_picker_requires_review(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        target = self.directory / 'report.csv'

        def picker(*args, **kwargs):
            self.ledger['total_seconds'] = 14400
            self.write_ledger()
            return str(target), ''

        with patch.object(ui.QFileDialog, 'getSaveFileName', side_effect=picker):
            dialog.save_report('csv')
        self.assertFalse(target.exists())
        self.assertEqual(dialog.reviewed_report['stats']['awake_seconds'], 14400)
        self.assertIn('Review', dialog.result.text())
        with self.destination(target):
            dialog.save_report('csv')
        self.assertTrue(target.exists())

    def test_latest_consent_removes_metadata_and_requires_review(self):
        settings.update_settings({'identity': {'display_name': 'Sensitive'}}, self.directory)
        dialog = self.dialog()
        dialog.identity_checks['display_name'].click()
        dialog.system_checks['system'].click()
        dialog.refresh_preview()
        settings.update_settings({'resources': False, 'export': {'identity': False}}, self.directory)
        target = self.directory / 'consent.csv'
        with self.destination(target), patch('platform.system', side_effect=AssertionError('Consent denied')):
            dialog.save_report('csv')
        self.assertFalse(target.exists())
        self.assertEqual(dialog.reviewed_report['identity'], {})
        self.assertEqual(dialog.reviewed_report['system'], {})
        with self.destination(target):
            dialog.save_report('csv')
        self.assertNotIn('Sensitive', target.read_text(encoding='utf-8-sig'))

    def test_report_consent_revoked_after_preview_prevents_save(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        settings.update_settings({'consent': {'export': False}}, self.directory)
        target = self.directory / 'blocked.pdf'
        with self.destination(target):
            dialog.save_report('pdf')
        self.assertFalse(target.exists())
        self.assertIsNone(dialog.reviewed_report)
        self.assertEqual(dialog.preview.toPlainText(), '')
        self.warnings.assert_called_once()

    def test_csv_suffix_overwrite_yes_and_write_failure(self):
        dialog = self.dialog()
        dialog.refresh_preview()
        target = self.directory / 'report'
        with self.destination(target):
            dialog.save_report('csv')
        self.assertTrue(target.with_suffix('.csv').exists())
        with self.destination(target), patch.object(ui.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(report_engine, 'write_csv', side_effect=OSError('synthetic write failure')):
            dialog.save_report('csv')
        self.assertIn('synthetic write failure', self.warnings.call_args.args[2])
        self.assertIsNone(dialog.reviewed_report)

    def test_pdf_uses_the_reviewed_html_and_creates_valid_pdf(self):
        dialog = self.dialog(language='vi')
        settings.update_settings({'language': 'vi'}, self.directory)
        dialog.refresh_preview()
        reviewed = copy.deepcopy(dialog.reviewed_report)
        target = self.directory / 'báo-cáo.pdf'
        with self.destination(target), patch.object(report_engine, 'report_html', wraps=report_engine.report_html) as render:
            dialog.save_report('pdf')
        self.assertEqual(len(render.call_args_list), 2)  # preview and PDF
        self.assertEqual(render.call_args_list[0].args[0], reviewed)
        self.assertEqual(render.call_args_list[1].args[0], reviewed)
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
        self.assertIn('Báo cáo', dialog.preview.toPlainText())

    def test_coverage_is_translated_read_only_evidence(self):
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        dialog = self.dialog(ui.CoverageDialog, 'vi')
        text = dialog.evidence.toPlainText()
        self.assertIn('Nguồn: Ghi trực tiếp', text)
        self.assertIn('Trạng thái: '+report_engine.tr('Unknown', 'vi'), text)
        self.assertIn('Không thể xác nhận dữ liệu đầy đủ', text)
        self.assertNotIn('Coverage cannot be proven', text)
        self.assertTrue(dialog.evidence.isReadOnly())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_narrow_vietnamese_dialogs_fit_and_keep_actions_reachable(self):
        for cls in (ui.ExportDialog, ui.IdentityDialog, ui.CoverageDialog):
            dialog = self.dialog(cls, 'vi')
            dialog.resize(390, 440)
            dialog.show()
            APP.processEvents()
            self.assertLessEqual(dialog.width(), 390)
            self.assertLessEqual(dialog.height(), 440)
            self.assertLessEqual(dialog.content.minimumSizeHint().width(), dialog.scroll.viewport().width())
            for index in range(dialog.actions.count()):
                button = dialog.actions.itemAt(index).widget()
                self.assertTrue(dialog.rect().contains(button.geometry()))
                self.assertTrue(button.accessibleName())
            for caption in dialog.findChildren(QLabel):
                if caption.buddy():
                    self.assertTrue(caption.buddy().accessibleName())
            dialog.close()


if __name__ == '__main__':
    unittest.main()
