import copy
import csv
import re
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


def measured(identifier='one', **values):
    """An availability result as the admin call returns it: 20 h online, 2 h offline, 2 h unknown."""
    import lifetime_availability as availability
    result = availability.summarise(dict(good_s=72000, bad_s=7200, unknown_s=7200, excluded_s=0))
    result.update(device_id=identifier, name=identifier, group='Sales', start='2026-11-06', end='2026-11-07',
                  timezone='Asia/Ho_Chi_Minh', zone_source='group', zone_ok=True, schedule='always', laptop=False,
                  daily=[dict(date='2026-11-06', good_s=36000, bad_s=7200, unknown_s=0, excluded_s=0)],
                  incidents=[dict(rule='offline', state='resolved', started_at='2026-11-06T03:00:00+00:00',
                                  opened_at='2026-11-06T03:15:00+00:00', resolved_at='2026-11-06T05:00:00+00:00', value=None)])
    result.update(values)
    return result


def worn(identifier='one'):
    source = device(identifier)
    source['policy']['health'] = True
    source['health'] = dict(battery_health=52, cycle_count=900, crashes_30d=4, shutdowns_30d=1,
                            observed_at='2026-11-07T12:00:00+07:00')
    source['readings'].update(specs=dict(disks=[dict(name='C:', total_bytes=100)]), performance=dict(disk_free_bytes=5))
    return source


class UptimeReportTests(unittest.TestCase):
    """1.3: any period with an "Uptime & downtime" section beside the previous period of the same length."""
    AS_OF = '2026-11-23T05:00:00+00:00'

    def computers(self):
        off = dict(asleep_s=28800, off_s=14400, unknown_s=0, restarts=1, shutdowns=1, unexpected_stops=1)
        new, old = device('new'), device('old')
        new['history'] = dict(daily={'2026-11-10': 36000, '2026-11-01': 18000},
                              downtime_daily={'2026-11-10': off, '2026-11-01': dict(off, unexpected_stops=0)})
        old['history'] = dict(daily={'2026-11-10': 7200})
        return new, old

    def rows(self, report):
        return {title: value for section, title, value in report['extra_rows'] if section == 'uptime'}

    def test_a_computer_compared_with_the_previous_period(self):
        new, _ = self.computers()
        compared = dict(current=[measured('new', outages=2, longest_outage_s=5400)],
                        previous=[measured('new', good_s=7200, bad_s=0, unknown_s=0, outages=0, longest_outage_s=0)],
                        as_of=self.AS_OF)
        report = device_report(new, '2026-11-08', '2026-11-14', uptime=compared)
        rows = self.rows(report)
        self.assertEqual(rows['Uptime & downtime'], '2026-11-08 to 2026-11-14, compared with 2026-11-01 to 2026-11-07')
        self.assertEqual(rows['Awake (hours)'], '10.0 (previous period: 5.0)')
        self.assertEqual(rows['Asleep (hours)'], '8.0 (previous period: 8.0)')
        self.assertEqual(rows['Unknown (hours)'], f'{(6 * 86400 + 86400 - 36000 - 43200) / 3600:.1f} (previous period: '
                                                  f'{(6 * 86400 + 86400 - 18000 - 43200) / 3600:.1f})')
        self.assertEqual(rows['Unexpected stops'], '1 (previous period: 0)')
        self.assertEqual(rows['Availability in expected hours'], '90.909 % (previous period: 100 %)')
        self.assertEqual(rows['Outages in expected hours'], '2 (previous period: 0)')
        self.assertEqual(rows['Longest outage (hours)'], '1.5 (previous period: 0.0)')
        self.assertIn('previous period of the same length', ' '.join(report['notes']))

    def test_group_or_fleet_sums_and_an_old_computer_counts_as_unknown(self):
        new, old = self.computers()
        compared = dict(current=[measured('new'), measured('old')], previous=[], as_of=self.AS_OF)
        report = fleet_report([new, old], '2026-11-08', '2026-11-14', group='Sales', uptime=compared)
        rows = self.rows(report)
        self.assertEqual(rows['Awake (hours)'], '12.0 (previous period: 5.0)')
        self.assertEqual(rows['Computers that report sleep and off time'], '1 / 2 (previous period: 1 / 2)')
        self.assertEqual(rows['Availability in expected hours'], '90.909 % (previous period: Not available)')
        self.assertEqual([value for section, title, value in report['extra_rows'] if (section, title) == ('uptime-by-computer', 'Computer')],
                         ['=Máy, "lễ tân"\n🖥'] * 2)
        self.assertIn(('report', 'Report scope', 'Group: Sales'), report['extra_rows'])
        everything = fleet_report([new, old], uptime=dict(current=[], previous=None, as_of=self.AS_OF))
        rows = self.rows(everything)
        self.assertEqual(rows['Uptime & downtime'], '2026-11-01 to 2026-11-23')         # all history: no comparison
        self.assertEqual(rows['Awake (hours)'], '17.0')
        self.assertIn('needs a chosen period', ' '.join(everything['notes']))

    def test_vietnamese_and_csv(self):
        new, _ = self.computers()
        report = device_report(new, '2026-11-08', '2026-11-14', language='vi',
                               uptime=dict(current=[], previous=[], as_of=self.AS_OF))
        rows = self.rows(report)
        self.assertEqual(rows['Awake (hours)'], '10.0 (khoảng trước: 5.0)')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'uptime.csv'
            write_csv(report, path)
            text = path.read_text(encoding='utf-8-sig')
        self.assertIn('uptime,Awake (hours),10.0 (khoảng trước: 5.0)', text)


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

    def test_availability_beside_awake_time_with_incidents_health_and_replacement_reasons(self):
        from lifetime_protocol import CLIENT_VERSION
        report = device_report(worn(), availability=measured(), thresholds=dict(battery_worn=60, disk_low=10, crashes=3))
        rows = {(section, key): value for section, key, value in report['extra_rows']}
        self.assertEqual(report['stats']['awake_seconds'], 120)             # awake time is unchanged and separate
        self.assertEqual(rows[('availability', 'Observed availability')], '90.909 %')     # 72000 / 79200
        self.assertEqual(rows[('availability', 'Monitoring coverage')], '91.667 %')
        self.assertEqual(rows[('availability', 'Unknown, not observed (seconds)')], 7200)
        self.assertEqual(rows[('availability', 'Calendar time zone')], 'Asia/Ho_Chi_Minh (from its group’s schedule)')
        self.assertIn('Computer offline · 2026-11-06 03:00 UTC – 2026-11-06 05:00 UTC · Resolved', rows[('incidents', 'Incident')])
        self.assertEqual(rows[('health', 'Battery health (%)')], 52)
        self.assertEqual(rows[('replacement', 'Replacement candidate')], 'Yes')
        reasons = [value for section, key, value in report['extra_rows'] if key == 'Reason']
        self.assertEqual(reasons, ['Battery health 52 % is below 60 %', 'System disk has less than 10 % free',
                                   '4 crashes in 30 days (limit 3)'])
        self.assertIn(('report', 'Data source', 'Lifetime Server monitoring and the latest snapshot each computer permits'),
                      report['extra_rows'])
        self.assertEqual(report['version'], CLIENT_VERSION)
        self.assertIn('Time zone: Asia/Ho_Chi_Minh', report['footer'])
        self.assertTrue(any('different measures' in note for note in report['notes']))
        # Not shared: no health rows from hidden data, no reasons, and no headline without evidence.
        private = worn()
        private['policy'] = dict(history=True, resources=False, health=False)
        hidden = device_report(private, availability=measured(status='insufficient'))
        values = {key: value for _, key, value in hidden['extra_rows']}
        self.assertEqual((values['Battery health (%)'], values['Replacement candidate']), (None, 'No'))
        self.assertTrue(values['Observed availability'].startswith('Insufficient evidence (observed 90.909 %'))
        laptop = device_report(device(), availability=measured(status='not-scheduled', availability=None, coverage=None))
        self.assertIn(('availability', 'Observed availability', 'Not expected online in this period'), laptop['extra_rows'])

    def test_group_and_fleet_add_seconds_and_list_candidates(self):
        report = fleet_report([worn('a'), device('b')], availability=[measured('a'), measured('b', good_s=0, bad_s=79200,
                              availability=0.0)], group='Sales', language='vi')
        rows = report['extra_rows']
        self.assertEqual(rows[0], ('report', 'Report scope', 'Nhóm: Sales'))
        values = {key: value for section, key, value in rows if section == 'availability'}
        self.assertEqual(values['Observed online (seconds)'], 72000)
        self.assertEqual(values['Observed offline (seconds)'], 86400)
        self.assertEqual(values['Observed availability'], '45.455 %')      # 72000 / 158400
        names = [value for section, key, value in rows if section == 'replacement' and key == 'Computer']
        self.assertEqual(names, [worn('a')['identity']['display_name']])
        # Each computer's availability follows its own awake-time rows.
        keys = [key for section, key, value in rows if section == 'fleet']
        first, second = keys.index('Device'), keys.index('Device', keys.index('Device') + 1)
        self.assertIn('Observed availability', keys[first:second])
        self.assertIn('Tình trạng pin 52 % thấp hơn 60 %', [value for _, key, value in rows if key == 'Reason'])
        self.assertIn('Múi giờ: Asia/Ho_Chi_Minh', report['footer'])

    def test_vietnamese_pdf_with_a_footer_on_every_page(self):
        app = qt_app()      # noqa: F841 (must stay alive while writing)
        days = [dict(date=f'2026-10-{day:02}', good_s=86400, bad_s=0, unknown_s=0, excluded_s=0) for day in range(1, 32)]
        report = device_report(worn(), availability=measured(daily=days * 3), language='vi')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'report.pdf'
            from PyQt6 import QtGui
            footers = []

            class Recording(QtGui.QPainter):
                def drawText(self, *arguments):
                    footers.append(arguments[-1])
                    return super().drawText(*arguments)
            with patch.object(QtGui, 'QPainter', Recording):
                write_pdf(report, path)
            data = path.read_bytes()
            csv_path = Path(root) / 'report.csv'
            write_csv(report, csv_path)
            text = csv_path.read_text(encoding='utf-8-sig')
        self.assertTrue(data.startswith(b'%PDF-') and b'%%EOF' in data[-1024:] and b'/FontFile' in data)
        self.assertGreaterEqual(len(re.findall(rb'/Type\s*/Page[^s]', data)), 2)
        self.assertGreaterEqual(len(footers), 2)        # one footer per page, each naming zone, source, version, page
        for page, line in enumerate(footers, 1):
            self.assertIn('Múi giờ: Asia/Ho_Chi_Minh', line)
            self.assertIn('Nguồn: Dữ liệu theo dõi', line)
            self.assertTrue(line.startswith('Lifetime Server ') and line.endswith(f'{page}/{len(footers)}'))
        self.assertIn('Dữ liệu theo dõi của Lifetime Server', text)      # CSV field names stay English; values are translated
        self.assertIn('Tình trạng pin 52 % thấp hơn 60 %', text)

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
