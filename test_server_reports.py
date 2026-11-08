import copy
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lifetime_server_reports import device_report, fleet_report
from lifetime_reports import write_csv, write_pdf
from qt_test_support import qt_app


def device(identifier='one', stock=100):
    return dict(device_id=identifier, identity={'display_name': '=Máy, "lễ tân"\n🖥'},
                policy={'history': True, 'resources': True},
                totals=dict(measured_seconds=stock, imported_seconds=20, baseline_seconds=500),
                last_observed_at='2026-11-07T12:00:00+07:00', last_received_at='2026-11-07T05:01:00Z',
                history=dict(daily={'2026-11-06': 30, '2026-11-07': 40}, imported_daily={'2026-11-06': 10}),
                readings=dict(system='Client OS', observed_at='2026-11-07T12:00:00+07:00'))


class ServerReportTests(unittest.TestCase):
    def test_stock_and_bounded_period(self):
        source = device()
        before = copy.deepcopy(source)
        with patch('platform.system', side_effect=AssertionError('host OS must not be read')):
            report = device_report(source)
            selected = device_report(source, '2026-11-06', '2026-11-06', system_fields=['system'])
        self.assertEqual(report['stats']['awake_seconds'], 120)
        self.assertEqual(report['stats']['baseline_seconds'], 500)
        self.assertEqual(report['identity'], {})
        self.assertEqual(report['system'], {})
        self.assertEqual(selected['stats']['awake_seconds'], 30)
        self.assertEqual(selected['stats']['measured_seconds'], 20)
        self.assertEqual(selected['stats']['imported_seconds'], 10)
        self.assertIsNone(selected['stats']['baseline_seconds'])
        self.assertEqual(selected['system'], {'system': 'Client OS'})
        self.assertEqual(source, before)

    def test_revocation_and_empty_ranges(self):
        source = device()
        source['policy'] = dict(history=False, resources=False)
        result = device_report(source, '2026-11-07', '2026-11-07', system_fields=['system'])
        self.assertIsNone(result['stats']['awake_seconds'])
        self.assertEqual(result['daily'], [])
        self.assertEqual(result['system'], {})
        self.assertIsNone(device_report(device(), '2020-01-01', '2020-01-01')['stats']['awake_seconds'])
        for first, last in [('2026-11-08', '2026-11-07'), ('bad', 'bad')]:
            with self.assertRaises(ValueError):
                device_report(device(), first, last)

    def test_unknown_and_known_zero(self):
        source = device()
        source['totals'] = None
        source['last_observed_at'] = None
        for key in ('awake_seconds', 'measured_seconds', 'imported_seconds', 'baseline_seconds'):
            self.assertIsNone(device_report(source)['stats'][key])
        source = device(stock=0)
        source['totals']['imported_seconds'] = 0
        self.assertEqual(device_report(source)['stats']['awake_seconds'], 0)

    def test_fleet_distinct_stocks_and_missing(self):
        unknown = device('unknown')
        unknown['totals'] = None
        unknown['last_observed_at'] = None
        report = fleet_report([device('a', 100), device('b', 200), unknown])
        self.assertEqual(report['stats']['awake_seconds'], 340)
        self.assertIn(('statistics', 'Devices with unavailable totals', 1), report['extra_rows'])
        self.assertIsNone(fleet_report([])['stats']['awake_seconds'])
        with self.assertRaises(ValueError):
            fleet_report([device('a'), device('a')])

    def test_unicode_csv_and_real_pdf(self):
        app = qt_app()
        report = device_report(device(), selected_identity=['display_name'], language='vi')
        with tempfile.TemporaryDirectory() as root:
            csv_path = Path(root) / 'report.csv'
            pdf_path = Path(root) / 'report.pdf'
            write_csv(report, csv_path)
            write_pdf(report, pdf_path)
            with csv_path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream))
            identity = next(row[2] for row in rows if row[1] == 'Display name')
            self.assertTrue(identity.startswith("'="))
            self.assertIn('Máy', identity)
            self.assertIn(b'/FontFile', pdf_path.read_bytes())


if __name__ == '__main__':
    unittest.main()
