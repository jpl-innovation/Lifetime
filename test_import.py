"""Historical reconstruction, isolation and event parsing regression tests."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import lifetime_import as imp
import lifetime_import_windows as windows
import lifetime_tracker as tracker


TZ = dt.timezone(dt.timedelta(hours=7))


def stamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))  # 'Z' is only accepted natively from Python 3.11
    return (parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed).timestamp()


def boot(start='2026-08-30T22:00:00', end='2026-08-31T02:00:00', events=(), clean=True):
    return dict(id='past', start=stamp(start), end=stamp(end), events=[(stamp(t), k) for t, k in events], clean=clean)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.ledger = tracker.new_ledger()
        self.ledger.update(total_seconds=600, boots=1, daily={'2026-11-06': 600},
                           hourly=[25]*24, tracking_started='2026-11-06',
                           sessions=[dict(start='2026-11-06T12:00:00+07:00', seconds=600)],
                           current_session_seconds=600, longest_session_seconds=600)

    def build(self, boots, ledger=None):
        return imp.rebuild(boots, stamp('2026-11-06T08:00:00'), ledger or self.ledger, tz=TZ)

    def test_no_sleep_midnight(self):
        result = self.build([boot()])
        self.assertEqual(result['seconds'], 14400)
        self.assertEqual(result['daily'], {'2026-08-30': 7200, '2026-08-31': 7200})
        self.assertEqual(sum(result['hourly']), result['seconds'])

    def test_sleep_inside_and_crossing_midnight(self):
        for events, daily in [([('2026-08-30T22:30:00', 'sleep'), ('2026-08-30T23:30:00', 'wake')],
                               {'2026-08-30': 3600, '2026-08-31': 7200}),
                              ([('2026-08-30T23:00:00', 'sleep'), ('2026-08-31T01:00:00', 'wake')],
                               {'2026-08-30': 3600, '2026-08-31': 3600})]:
            result = self.build([boot(events=events)])
            self.assertEqual(result['daily'], daily)
            self.assertEqual(sum(daily.values()), result['seconds'])
            self.assertEqual(sum(result['hourly']), result['seconds'])

    def test_unclean_unmatched_and_duplicate_events(self):
        result = self.build([boot(events=[('2026-08-30T21:00:00', 'wake'),
                                          ('2026-08-30T23:00:00', 'sleep'),
                                          ('2026-08-30T23:10:00', 'sleep')], clean=False)])
        self.assertEqual(result['seconds'], 3600)
        self.assertEqual(result['sleep_seconds'], 10800)
        self.assertEqual(result['unclean_boots'], 1)

    def test_idempotent_and_live_progress(self):
        first = imp.merge(self.ledger, self.build([boot()]))
        second = imp.merge(first, self.build([boot()], first))
        self.assertEqual(first, second)
        first['total_seconds'] += 30
        first['daily']['2026-11-06'] += 30
        first['hourly'][12] += 30
        first['sessions'][-1]['seconds'] += 30
        self.assertEqual(first, imp.merge(first, self.build([boot()], first)))
        removed = imp.merge(first, self.build([], first))
        self.assertEqual(removed['total_seconds'], 630)
        self.assertEqual(removed['daily'], {'2026-11-06': 630})
        self.assertEqual(removed['boots'], 1)

    def test_excludes_current_and_tracking_overlap(self):
        boots = [boot(), boot('2026-11-06T01:00:00', '2026-11-06T05:00:00'),
                 boot('2026-11-06T08:00:00', '2026-11-06T12:00:00')]
        self.assertEqual(self.build(boots)['boots'], 1)

    def test_session_cap_does_not_cap_totals(self):
        start = stamp('2026-08-30T00:00:00')
        boots = [dict(start=start+i*60, end=start+i*60+30, events=[], clean=True) for i in range(120)]
        result = self.build(boots)
        self.assertEqual(result['seconds'], 3600)
        merged = imp.merge(self.ledger, result)
        self.assertEqual(len(merged['sessions']), 100)
        self.assertFalse(merged['sessions'][-1].get('imported'))

    def test_journal_parsers(self):
        identity = 'a'*32
        raw = json.dumps([dict(index=-1, boot_id=identity, first_entry=1000000, last_entry=2000000)])
        self.assertEqual(imp.parse_boots(raw)[0]['end'], 2)
        raw = f' -1 {identity} Sun 2026-08-30 15:00:00 UTC—Sun 2026-08-30 19:00:00 UTC'
        self.assertEqual(imp.parse_boots(raw)[0]['start'], stamp('2026-08-30T22:00:00'))
        for entry, exit in [('PM: suspend entry (s2idle)', 'PM: suspend exit'),
                            ('PM: hibernation entry', 'PM: hibernation exit'),
                            ('Suspending...', "Operation 'suspend' finished.")]:
            raw = '\n'.join(json.dumps(dict(MESSAGE=m, __REALTIME_TIMESTAMP=t)) for m,t in [(entry,1000000),(exit,2000000)])
            self.assertEqual(imp.parse_events(raw, True), [(1, 'sleep'), (2, 'wake')])

    def test_cli_copy_backup_and_dry_run(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path = Path(tmp)/'custom.json'
            path.write_text(json.dumps(self.ledger))
            before = path.read_bytes()
            provider = lambda: ([boot()], stamp('2026-11-06T08:00:00'))
            with patch('sys.argv', ['import', '--dry-run', '--ledger', str(path)]):
                imp.main(provider)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            self.assertEqual(path.read_bytes(), before)
            with patch('sys.argv', ['import', '--apply', '--ledger', str(path)]):
                imp.main(provider)
            self.assertEqual(path.with_name('custom.json.bak-before-import').read_bytes(), before)
            self.assertEqual(json.loads(path.read_text())['total_seconds'], 15000)

    def test_windows_xml(self):
        def event(provider, identity, hour, data=''):
            return f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Microsoft-Windows-{provider}"/><EventID>{identity}</EventID><TimeCreated SystemTime="2026-08-30T{hour}:00:00Z"/></System>{data}</Event>'
        raw = '<Events>' + ''.join([event('Kernel-General',12,'10'), event('Kernel-Boot',27,'10'),
              event('Kernel-Power',42,'11'), event('Kernel-Power',1,'12'),
              event('Power-Troubleshooter',1,'14','<EventData><Data Name="WakeTime">2026-08-30T13:00:00Z</Data></EventData>'),
              event('Kernel-Power',107,'14'), event('Kernel-General',13,'15'),
              event('Kernel-General',12,'16'), event('Other',99,'17'), event('Kernel-General',12,'18')]) + '</Events>'
        boots = windows.parse_windows_xml(raw)
        self.assertEqual(len(boots), 3)
        self.assertTrue(boots[0]['clean'])
        self.assertEqual(sum(b-a for a,b in imp.awake_spans(boots[0])), 3*3600)
        self.assertFalse(boots[1]['clean'])
        self.assertEqual(boots[1]['end']-boots[1]['start'], 3600)


    def test_explicit_timezone_independent_of_host(self):
        daily, hourly = {}, [0.0]*24
        imp.allocate(stamp('2026-08-30T23:30:00Z'), stamp('2026-08-31T00:30:00Z'),
                     daily, hourly, tz=dt.timezone.utc)
        self.assertEqual(daily, {'2026-08-30': 1800, '2026-08-31': 1800})
        self.assertEqual(hourly[23], 1800)
        self.assertEqual(hourly[0], 1800)
        result = self.build([boot()])
        self.assertTrue(result['sessions'][0]['start'].endswith('+07:00'))

    def test_tracking_cutoff_uses_explicit_timezone(self):
        ledger = dict(self.ledger, tracking_started='2026-08-31')
        result = self.build([boot('2026-08-30T22:00:00', '2026-08-30T23:59:00'),
                             boot('2026-08-31T00:00:00', '2026-08-31T00:01:00')], ledger)
        self.assertEqual(result['boots'], 1)
        self.assertEqual(result['seconds'], 7140)

    def test_overlapping_boots_refused(self):
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            self.build([boot(), boot('2026-08-31T01:00:00', '2026-08-31T03:00:00')])

    def test_silent_gap_not_counted(self):
        test_silent_gap_not_counted()

    def test_cli_missing_ledger_preview_does_not_create_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path = Path(tmp)/'missing.json'
            with patch('sys.argv', ['import', '--dry-run', '--ledger', str(path)]), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                imp.main(lambda: ([boot()], stamp('2026-11-06T08:00:00')))
            self.assertIn('No ledger at', output.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])


class WindowsEventTests(unittest.TestCase):
    @staticmethod
    def event(provider, identity, when, **data):
        fields = ''.join(f'<Data Name="{key}">{value}</Data>' for key, value in data.items())
        return (f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
                f'<System><Provider Name="{provider}"/><EventID>{identity}</EventID>'
                f'<TimeCreated SystemTime="{when}"/></System><EventData>{fields}</EventData></Event>')

    def parse(self, *events):
        return windows.parse_windows_xml(''.join(events))

    def start(self, when='2026-08-30T10:00:00Z'):
        return self.event('Microsoft-Windows-Kernel-General', 12, when)

    def stop(self, when='2026-08-31T15:00:00Z'):
        return self.event('Microsoft-Windows-Kernel-General', 13, when)

    def test_hibernate_and_fast_startup_buffered_107(self):
        for state, boot_type in [('5', '2'), ('6', '1')]:
            with self.subTest(state=state):
                boots = self.parse(self.start(),
                    self.event('Microsoft-Windows-Kernel-Power', 42, '2026-08-30T11:00:00Z', TargetState=state),
                    self.event('Microsoft-Windows-Kernel-Power', 107, '2026-08-30T11:00:03Z', TargetState=state),
                    self.event('Microsoft-Windows-Kernel-Boot', 27, '2026-08-31T13:00:00Z', BootType=boot_type),
                    self.stop())
                self.assertEqual(len(boots), 1)
                self.assertEqual(sum(b-a for a,b in imp.awake_spans(boots[0])), 3*3600)

    def test_missing_hibernate_wake_stays_asleep(self):
        boots = self.parse(self.start(),
            self.event('Microsoft-Windows-Kernel-Power', 42, '2026-08-30T11:00:00Z', TargetState='5'),
            self.event('Microsoft-Windows-Kernel-Power', 107, '2026-08-30T11:00:03Z', TargetState='5'),
            self.stop())
        self.assertEqual(sum(b-a for a,b in imp.awake_spans(boots[0])), 3600)

    def test_troubleshooter_recovers_missing_sleep_and_overrides_early_resume(self):
        for include_entry in (False, True):
            with self.subTest(include_entry=include_entry):
                entry = [self.event('Microsoft-Windows-Kernel-Power', 42, '2026-08-30T11:00:00Z')] if include_entry else []
                boots = self.parse(self.start(), *entry,
                    self.event('Microsoft-Windows-Kernel-Power', 107, '2026-08-30T11:00:03Z'),
                    self.event('Microsoft-Windows-Power-Troubleshooter', 1, '2026-08-31T14:00:00Z',
                               SleepTime='2026-08-30T11:00:00Z', WakeTime='2026-08-31T13:00:00Z'),
                    self.stop())
                self.assertEqual(sum(b-a for a,b in imp.awake_spans(boots[0])), 3*3600)

    def test_clean_shutdown_overrides_future_buffered_observation(self):
        boots = self.parse(self.start(),
            self.event('Other', 99, '2026-08-31T18:00:00Z'),
            self.stop(), self.start('2026-08-31T16:00:00Z'))
        self.assertEqual(boots[0]['end'], stamp('2026-08-31T15:00:00Z'))
        self.assertTrue(boots[0]['clean'])

    def test_eventlog_startup_before_kernel_start_is_not_previous_activity(self):
        boots = self.parse(self.start(), self.event('Other', 99, '2026-08-30T11:00:00Z'),
            self.event('EventLog', 6005, '2026-08-31T16:01:00Z'),
            self.start('2026-08-31T16:00:00Z'))
        self.assertEqual(boots[0]['end'], stamp('2026-08-30T11:00:00Z'))
        self.assertFalse(boots[0]['clean'])

    def test_unicode_utf16_and_powershell_wrapper(self):
        raw = '<Events>' + self.start() + self.event('Other', 1, '2026-08-30T12:00:00Z', Message='睡眠 Tiếng Việt') + self.stop() + '</Events>'
        boots = windows.parse_windows_xml(('\ufeff<?xml version="1.0"?>' + raw).encode('utf-16'))
        self.assertEqual(len(boots), 1)
        self.assertTrue(boots[0]['clean'])

    def test_current_boot_does_not_exclude_other_boots_within_five_minutes(self):
        boots, stats = windows._parse_events(windows._xml_events(io.StringIO('<Events>' +
            self.start('2026-08-30T10:00:00Z') + self.stop('2026-08-30T10:01:00Z') +
            self.start('2026-08-30T10:02:00Z') + '</Events>')))
        with patch.object(windows, '_query_system', return_value=(boots, stats)), \
                patch.object(windows, '_native_boot_time', return_value=stamp('2026-08-30T10:02:00Z')), \
                contextlib.redirect_stdout(io.StringIO()):
            boots, current = windows.windows_boots()
        result = imp.rebuild(boots, current, tracker.new_ledger(), tz=TZ)
        self.assertEqual(result['boots'], 1)
        self.assertEqual(result['seconds'], 60)

    def test_native_boot_permission_failure_uses_conservative_log_boundary(self):
        boots, stats = windows._parse_events(windows._xml_events(io.StringIO('<Events>' + self.start() + '</Events>')))
        with patch.object(windows, '_query_system', return_value=(boots, stats)), \
                patch.object(windows, '_native_boot_time', side_effect=OSError('Access denied')), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as output:
            boots, current = windows.windows_boots()
        self.assertTrue(boots[-1]['is_current'])
        self.assertEqual(current, boots[-1]['start'])
        self.assertIn('conservatively excluding', output.getvalue())

    def test_missing_start_and_bad_timestamps_fail_clearly(self):
        with patch.object(windows, '_query_system', return_value=([], {})):
            with self.assertRaisesRegex(ValueError, 'No Kernel-General 12'):
                windows.windows_boots()
        with self.assertRaisesRegex(ValueError, 'Invalid System log event metadata'):
            self.parse(self.start('not-a-date'))
        with self.assertRaisesRegex(ValueError, 'SleepTime is later'):
            self.parse(self.start(), self.event('Microsoft-Windows-Power-Troubleshooter', 1,
                '2026-08-31T14:00:00Z', SleepTime='2026-08-31T13:00:00Z', WakeTime='2026-08-30T13:00:00Z'))

    def test_system_permission_failure_has_no_traceback(self):
        original_popen = subprocess.Popen
        def denied_process(*args, **kwargs):
            return original_popen([sys.executable, '-c',
                "import sys; sys.stderr.buffer.write('Access is denied. 拒绝访问'.encode('utf-16')); sys.exit(5)"], **kwargs)
        with patch.object(windows.subprocess, 'Popen', side_effect=denied_process), \
                patch('sys.argv', ['import', '--dry-run']), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                imp.main(windows.windows_boots)
        self.assertEqual(error.exception.code, 1)
        self.assertIn('event log', output.getvalue())
        self.assertIn('no history was imported', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())


def test_silent_gap_not_counted():
    """Regression: a boot with a long journal silence and no logged sleep must not count as awake."""
    import lifetime_import as li
    stamps = [1000 + i * 60 for i in range(11)] + [1000 + 600 + 5 * 86400]  # 10 min of logs, then 5 days of silence
    boot = dict(id='x', start=1000, end=1000 + 600 + 5 * 86400, events=[], activity=stamps)
    total = sum(b - a for a, b in li.awake_spans(boot))
    assert total == 600, total


class EventTimestampTests(unittest.TestCase):
    def test_windows_event_timestamps_parse_on_every_python(self):
        """Event logs write 7 fractional digits and a Z suffix; Python 3.10 cannot parse those natively."""
        base = dt.datetime(2026, 8, 30, 10, 2, tzinfo=dt.timezone.utc).timestamp()
        for text, extra in (('2026-08-30T10:02:00Z', 0), ('2026-08-30T10:02:00.5Z', .5),
                            ('2026-08-30T10:02:00.1234567Z', .123456), ('2026-08-30T10:02:00.123456789+00:00', .123456)):
            self.assertAlmostEqual(windows._timestamp(text), base + extra, places=6, msg=text)


if __name__ == '__main__':
    unittest.main()
