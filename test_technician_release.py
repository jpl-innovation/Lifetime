"""Technician features integrated with the actual release consent/accounting."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qt_test_support import qt_app
import lifetime_i18n as i18n
import lifetime_reports as reports
import lifetime_settings as settings
import lifetime_tracker as tracker
from lifetime_setup import SetupDialog


class ReleaseTechnicianTests(unittest.TestCase):
    def test_untranslated_qt_page_names_keep_pdf_layout_valid(self):
        from PyQt6.QtGui import QPageSize
        from lifetime_i18n_qt import native_translator
        native_translator()
        for language in ('en', 'vi'):
            i18n.set_language(language)
            self.assertEqual(self.app.translate('UnrelatedQtContext', 'A4'), 'A4')
            page = QPageSize(QPageSize.PageSizeId.A4)
            self.assertTrue(page.isValid())
            self.assertGreater(page.size(QPageSize.Unit.Millimeter).width(), 0)

    def test_pdf_contains_text_after_the_real_release_ui_installs_its_translator(self):
        import lifetime_gui as gui
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'LIFETIME_DATA_DIR': directory}):
            preferences = settings.update_settings(dict(completed=True, history=True, resources=False, language='vi'), directory)
            ledger = self.ledger()
            tracker.atomic_json(Path(directory)/'ledger.json', ledger)
            window = gui.Window(preferences)
            window.worker.timer.stop()
            window.timer.stop()
            try:
                report = reports.build_report(ledger, preferences, language='vi')
                output = Path(directory)/'report.pdf'
                reports.write_pdf(report, output)
                self.assertIn(b'/FontFile', output.read_bytes())
            finally:
                window.close()

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def ledger(self):
        result = tracker.new_ledger()
        end = dt.datetime.fromisoformat('2026-11-07T12:00:00').timestamp()
        tracker.sample(result, 3600, 'fixture', '2026-11-07T12:00:00',
                       boot_evidence=(end-3600, [(end-3600, end)], False))
        return result

    def tearDown(self):
        i18n.set_language('en')

    def test_pending_first_run_cannot_export_or_read_optional_information(self):
        with patch.object(reports.platform, 'system', side_effect=AssertionError('probe read')):
            with self.assertRaises(ValueError):
                reports.build_report(self.ledger(), settings.defaults(), system_fields=['system'])

    def test_revoked_real_history_permission_hides_old_dated_data(self):
        ledger = self.ledger()
        ledger['imported'] = dict(seconds=30, boots=1, first_day='1990-01-01', last_day='1990-01-02', daily={'1990-01-01': 30})
        ledger['daily']['1990-01-01'] = 30
        preferences = settings.normalize(dict(completed=True, history=False))
        original = copy.deepcopy(ledger)
        report = reports.build_report(ledger, preferences, '1990-01-01', '2026-11-07')
        self.assertEqual(report['daily'], [])
        self.assertIsNone(report['stats']['awake_seconds'])
        self.assertIsNone(report['stats']['session_starts'])
        self.assertIsNone(report['coverage']['earliest_record'])
        self.assertIn('History collection is disabled', ' '.join(report['notes']))
        self.assertEqual(ledger, original)

    def test_resources_denied_does_not_collect_even_explicit_export_fields(self):
        prefs = settings.normalize(dict(completed=True, resources=False))
        with patch.object(reports.platform, 'system', side_effect=AssertionError('probe read')):
            report = reports.build_report(self.ledger(), prefs, system_fields=['system'])
        self.assertEqual(report['system'], {})

    def test_new_release_allocation_notes_are_accurate_and_bilingual(self):
        ledger = self.ledger()
        ledger['daily_allocation']['estimated'] = True
        prefs = settings.normalize(dict(completed=True, history=True))
        en = reports.report_html(reports.build_report(ledger, prefs))
        vi = reports.report_html(reports.build_report(ledger, prefs, language='vi'))
        self.assertIn('Live intervals are split across local days', en)
        self.assertIn('Daily allocation includes estimates', en)
        self.assertNotIn('day can therefore exceed 24 hours', en)
        self.assertIn('Phân bổ theo ngày có phần ước tính', vi)

    def test_verify_is_scoped_to_current_boot_and_never_promotes_coverage(self):
        prefs = settings.normalize(dict(completed=True, history=True))
        result = reports.build_report(self.ledger(), prefs, verification=dict(event_seconds=3600, awake_seconds=3600, difference=0, captured='2026-11-07T12:00:00+07:00'))
        self.assertEqual(result['coverage']['status'], 'unknown')
        self.assertIn('current boot only', ' '.join(result['notes']))
        self.assertIn('Current-boot Verify difference', reports.report_html(result))

    def test_settings_interview_keeps_identity_edited_while_open(self):
        import lifetime_gui as gui
        with tempfile.TemporaryDirectory() as directory:
            settings.save_settings(dict(completed=True, history=True, identity={'display_name': 'Old'}), directory)
            dialog = SetupDialog(gui.THEMES['dark'], settings.load_settings(directory), directory)
            settings.update_settings({'identity': {'display_name': 'Reception', 'asset_tag': 'PC-014'}}, directory)
            choices = dialog.choices()
            settings.save_settings(choices, directory)
            self.assertEqual(settings.load_settings(directory)['identity']['display_name'], 'Reception')
            self.assertEqual(settings.load_settings(directory)['identity']['asset_tag'], 'PC-014')
            dialog.close()

    def test_language_preference_does_not_grant_first_run_consent(self):
        import lifetime_gui as gui
        with tempfile.TemporaryDirectory() as directory:
            dialog = SetupDialog(gui.THEMES['dark'], settings.defaults(), directory)
            dialog.change_language('vi')
            value = settings.load_settings(directory)
            self.assertFalse(value['completed'])
            self.assertFalse(value['history'])
            self.assertFalse(value['resources'])
            dialog.close()

    def test_dated_hourly_corruption_is_reported_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'ledger.json'
            ledger = self.ledger()
            ledger['dated_hourly'] = {'2026-11-07': [1]*23}
            raw = json.dumps(ledger)
            path.write_text(raw, encoding='utf-8')
            with self.assertRaises(ValueError):
                tracker.read_ledger(directory)
            self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_history_denied_does_not_add_gap_timeline_or_reenable_collection(self):
        ledger = tracker.new_ledger()
        tracker.sample(ledger, 100, 'a', '2026-11-07T12:00:00', history=False)
        tracker.sample(ledger, 1000, 'a', '2026-11-07T12:00:30', history=False)
        self.assertEqual(ledger['coverage_gaps'], [])
        self.assertEqual(ledger['daily'], {})
        self.assertEqual(ledger['sessions'], [])
        self.assertEqual(ledger['total_seconds'], 100)


if __name__ == '__main__':
    unittest.main()
