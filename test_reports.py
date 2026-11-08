"""Report accuracy, privacy, Unicode, boundary handling and atomic output."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import csv
import datetime as dt
import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication
import lifetime_reports as reports
import lifetime_settings as settings
import lifetime_tracker as tracker


class ReportTests(unittest.TestCase):
    def build(self, ledger, preferences=None, *args, **kwargs):
        current = dict(completed=True, history=True, resources=True)
        current.update(preferences or {})
        return reports.build_report(ledger, current, *args, **kwargs)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def ledger(self):
        value = tracker.new_ledger()
        end = dt.datetime.fromisoformat('2026-11-05T23:59:50').timestamp()
        tracker.sample(value, 100, 'a', '2026-11-05T23:59:50', boot_evidence=(end-100, [(end-100, end)], False))
        tracker.sample(value, 130, 'a', '2026-11-06T00:00:20')
        value['baseline_seconds'] = 500
        return value

    def test_empty_tracked_reconstructed_and_combined_sources(self):
        empty = tracker.new_ledger()
        self.assertEqual(reports.coverage(empty)['status'], 'unknown')
        self.assertIsNone(self.build(empty, {})['stats']['awake_seconds'])
        direct = self.ledger()
        self.assertEqual(reports.coverage(direct)['source'], 'tracked')
        imported = copy.deepcopy(empty)
        imported.update(total_seconds=3600, daily={'2026-10-01': 3600},
                        imported={'seconds': 3600, 'boots': 1, 'first_day': '2026-10-01',
                                  'last_day': '2026-10-01', 'daily': {'2026-10-01': 3600}})
        self.assertEqual(reports.coverage(imported)['source'], 'reconstructed')
        imported['total_seconds'] += 30
        imported['daily']['2026-11-06'] = 30
        self.assertEqual(reports.coverage(imported)['source'], 'combined')
        period = self.build(imported, {}, '2026-10-01', '2026-10-01')
        self.assertEqual(period['coverage']['source'], 'reconstructed')
        for ledger in (empty, direct, imported):
            self.assertNotEqual(reports.coverage(ledger)['status'], 'complete')

    def test_period_totals_preserve_existing_midnight_meaning(self):
        value = self.ledger()
        self.assertEqual(self.build(value, {}, '2026-11-05', '2026-11-05')['stats']['awake_seconds'], 110)
        self.assertEqual(self.build(value, {}, '2026-11-06', '2026-11-06')['stats']['awake_seconds'], 20)
        period = self.build(value, {}, '2026-11-05', '2026-11-06')
        self.assertEqual(period['stats']['awake_seconds'], 130)
        self.assertIsNone(period['stats']['baseline_seconds'])
        self.assertIsNone(period['stats']['longest_session_seconds'])
        self.assertEqual(self.build(value, {})['stats']['baseline_seconds'], 500)

    def test_missing_period_rows_are_unavailable_not_zero_or_guessed(self):
        value = self.ledger()
        period = self.build(value, {}, '2020-01-01', '2020-01-02')
        self.assertIsNone(period['stats']['awake_seconds'])
        self.assertIsNone(period['first_record'])
        self.assertIn('No recorded data', ' '.join(period['notes']))
        for start, end in [('2026-11-07', '2026-11-06'), ('2026-02-30', '2026-11-06'), ('20261105', '2026-11-06'), ('2026-11-05', None)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.build(value, {}, start, end)

    def test_metadata_opt_in_consent_denial_and_no_host_collection(self):
        value = self.ledger()
        prefs = {'identity': {'display_name': 'private', 'asset_tag': 'PC-014'}}
        with patch.object(reports.platform, 'node', side_effect=AssertionError('hostname read')):
            report = self.build(value, prefs)
        self.assertEqual(report['identity'], {})
        self.assertEqual(report['system'], {})
        with patch.object(reports.platform, 'machine', return_value='x86_64'):
            report = self.build(value, prefs, selected_identity=['asset_tag'], system_fields=['machine'])
        self.assertEqual(report['identity'], {'asset_tag': 'PC-014'})
        self.assertEqual(report['system'], {'machine': 'x86_64'})
        prefs['resources'] = False
        prefs['export'] = {'identity': False}
        report = self.build(value, prefs, selected_identity=['display_name'], system_fields=['machine'])
        self.assertEqual(report['identity'], {})
        self.assertEqual(report['system'], {})
        prefs['consent'] = {'export': False}
        with self.assertRaises(ValueError):
            self.build(value, prefs)

    def test_formula_injection_rfc_quoting_unicode_and_html_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            for text in ['=1+1', ' +SUM(A1)', '-cmd', '@SUM(1)', '\t=1', '\n=1', '\x00=1', '\ufeff=1', 'Nguyễn 🖥️, "PC"\nReception', '<img src="https://example.invalid/x">']:
                with self.subTest(text=ascii(text)):
                    report = self.build(self.ledger(), {'identity': {'display_name': text}}, selected_identity=['display_name'], language='vi')
                    path = Path(directory, 'report.csv')
                    reports.write_csv(report, path)
                    self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
                    with path.open(encoding='utf-8-sig', newline='') as stream:
                        row = next(row for row in csv.reader(stream) if row[1] == 'Display name')
                    expected = reports._csv_safe(text)
                    self.assertEqual(row[2], expected)
                    if text.lstrip(' \t\r\n\ufeff').startswith(('=', '+', '-', '@')):
                        self.assertTrue(row[2].startswith("'"))
                    self.assertNotIn('<img src=', reports.report_html(report))
                    self.assertIn('Báo cáo', reports.report_html(report))

    def test_snapshot_no_ledger_settings_or_tracking_mutation(self):
        ledger, prefs = self.ledger(), {'identity': {'asset_tag': 'PC-014'}, 'consent': {'tracking': False}}
        before_ledger, before_settings = copy.deepcopy(ledger), copy.deepcopy(prefs)
        with tempfile.TemporaryDirectory() as directory:
            report = self.build(ledger, prefs, selected_identity=['asset_tag'])
            reports.write_csv(report, Path(directory, 'report.csv'))
            reports.write_pdf(report, Path(directory, 'report.pdf'))
        self.assertEqual(ledger, before_ledger)
        self.assertEqual(prefs, before_settings)

    def test_output_failures_preserve_destination_and_clean_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'report.csv')
            path.write_bytes(b'previous valid report')
            report = self.build(self.ledger(), {})
            with patch.object(reports.os, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    reports.write_csv(report, path)
            self.assertEqual(path.read_bytes(), b'previous valid report')
            def full_disk(temporary):
                temporary.write_bytes(b'partial')
                raise OSError(errno.ENOSPC, 'No space left')
            with self.assertRaises(OSError):
                reports._atomic_output(path, full_disk)
            self.assertEqual(path.read_bytes(), b'previous valid report')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaises(OSError):
                reports.write_csv(report, Path(directory, 'missing', 'report.csv'))

    def test_pdf_reopens_with_font_resources_and_complete_trailer_both_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('en', 'vi'):
                report = self.build(self.ledger(), {'identity': {'display_name': 'Nguyễn 🖥️ '+('x'*2000)}}, selected_identity=['display_name'], language=language)
                path = Path(directory, language+'.pdf')
                reports.write_pdf(report, path)
                data = path.read_bytes()
                self.assertTrue(data.startswith(b'%PDF-'))
                self.assertIn(b'%%EOF', data[-1024:])
                self.assertIn(b'/Font', data)
                self.assertIn(b'/FontFile', data)  # a blank page can still have an empty /Font dictionary
                self.assertIn(b'/Type /Page', data)

    def test_large_history_has_bounded_preview_and_complete_csv(self):
        ledger = self.ledger()
        first = dt.date(1990, 1, 1)
        ledger['daily'] = {(first+dt.timedelta(days=i)).isoformat(): 1_000_000_000 for i in range(10000)}
        report = self.build(ledger, {}, '1990-01-01', '2020-01-01')
        self.assertEqual(report['stats']['awake_seconds'], 10_000_000_000_000)
        self.assertEqual(len(report['daily']), 10000)
        self.assertLess(len(reports.report_html(report)), 30000)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'large.csv')
            reports.write_csv(report, path)
            with path.open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(sum(row[0] == 'daily' for row in csv.reader(stream)), 10000)


if __name__ == '__main__':
    unittest.main()
