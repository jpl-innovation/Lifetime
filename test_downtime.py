"""Downtime per day (asleep / off / unknown, restarts, shutdowns, unexpected stops). Synthetic data only."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import lifetime_downtime as downtime
import lifetime_import as imp
import lifetime_import_windows as windows
import lifetime_ledger as schema
import lifetime_tracker as tracker

UTC = dt.timezone.utc
TZ = dt.timezone(dt.timedelta(hours=7))
H = 3600


def at(text, tz=TZ):
    value = dt.datetime.fromisoformat(text)
    return (value if value.tzinfo else value.replace(tzinfo=tz)).timestamp()


def boot(start, end, sleeps=(), clean=True, tz=TZ):
    events = []
    for a, b in sleeps:
        events += [(at(a, tz), 'sleep'), (at(b, tz), 'wake')]
    return dict(id=start, start=at(start, tz), end=at(end, tz), events=events, clean=clean)


def new_york():
    """A real TZif for the 2026 US transitions (8 March, 1 November): Windows Python ships no IANA database."""
    transitions = [int(dt.datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp()),
                   int(dt.datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp())]
    tzif = (b'TZif\0' + bytes(15) + struct.pack('>6I', 0, 0, 0, 2, 2, 8) + struct.pack('>2i', *transitions)
            + bytes([1, 0]) + struct.pack('>iBBiBB', -18000, 0, 0, -14400, 1, 4) + b'EST\0EDT\0')
    return ZoneInfo.from_file(io.BytesIO(tzif), key='Test/New_York_2026')


def entry(**values):
    return {**dict.fromkeys(downtime.FIELDS, 0), **values}


class FromBootsTests(unittest.TestCase):
    def test_sleep_off_restart_shutdown_and_unexpected_stop(self):
        boots = [boot('2026-11-18T08:00', '2026-11-18T22:00', sleeps=[('2026-11-18T12:00', '2026-11-18T13:00')]),
                 # clean, next start 5 minutes later: a restart
                 boot('2026-11-18T22:05', '2026-11-19T01:00', sleeps=[('2026-11-18T23:00', '2026-11-19T00:30')]),
                 # clean, next start 7 hours later: a shutdown (off), then an unclean end followed by Kernel-Power 41
                 boot('2026-11-19T08:00', '2026-11-19T10:00', clean=False),
                 boot('2026-11-19T12:00', '2026-11-19T14:00')]
        extras = dict(stops=[at('2026-11-19T12:01')])
        result = downtime.from_boots(boots, extras, now=at('2026-11-19T14:00'), tz=TZ)
        day18, day19 = result['days']['2026-11-18'], result['days']['2026-11-19']
        self.assertEqual(day18['asleep_s'], 1*H + 1*H)               # noon hour and 23:00-24:00
        self.assertEqual(day19['asleep_s'], 0.5*H)
        self.assertEqual(day18['off_s'], 5*60)                        # the restart
        self.assertEqual(day19['off_s'], 7*H)                         # 01:00-08:00
        self.assertEqual((day18['restarts'], day18['shutdowns']), (1, 0))
        self.assertEqual((day19['restarts'], day19['shutdowns'], day19['unexpected_stops']), (0, 1, 1))
        kinds = [e['kind'] for e in result['events']]
        self.assertEqual(kinds.count('unexpected'), 1)
        self.assertEqual(kinds.count('start'), 4)
        self.assertEqual(result['start'], boots[0]['start'])
        # 10:00-12:00 after the unclean end is not counted as off: nothing says when it stopped.
        self.assertEqual(day19['off_s'] + day19['asleep_s'], 7.5*H)

    def test_unclean_end_without_a_stop_record_counts_nothing(self):
        result = downtime.from_boots([boot('2026-11-18T08:00', '2026-11-18T10:00', clean=False),
                                      boot('2026-11-18T12:00', '2026-11-18T14:00')], {}, tz=TZ)
        self.assertEqual(result['days'], {})
        self.assertNotIn('unexpected', [e['kind'] for e in result['events']])

    def test_shutdown_request_then_hibernation_is_fast_startup_shutdown(self):
        boots = [boot('2026-11-21T08:00', '2026-11-22T09:00', sleeps=[('2026-11-21T23:00:03', '2026-11-22T08:00')])]
        extras = dict(requests=[(at('2026-11-21T23:00'), dict(kind='shutdown', by='Start menu', reason=''))],
                      wakes=[(at('2026-11-22T08:00'), 'Power Button')])
        result = downtime.from_boots(boots, extras, tz=TZ)
        self.assertEqual(result['days']['2026-11-21']['shutdowns'], 1)
        self.assertEqual(result['days']['2026-11-21']['asleep_s'], 0)
        self.assertAlmostEqual(result['days']['2026-11-21']['off_s'] + result['days']['2026-11-22']['off_s'], 9*H - 3, places=3)
        kinds = [(e['kind'], e['by']) for e in result['events']]
        self.assertIn(('shutdown', 'Start menu'), kinds)

    def test_wake_source_and_requested_restart(self):
        boots = [boot('2026-11-20T08:00', '2026-11-20T20:00', sleeps=[('2026-11-20T12:00', '2026-11-20T13:00')]),
                 boot('2026-11-20T21:00', '2026-11-20T22:00')]
        extras = dict(requests=[(at('2026-11-20T19:59'), dict(kind='restart', by='Windows Update', reason='Planned'))],
                      wakes=[(at('2026-11-20T13:00:30'), 'USB keyboard')])
        result = downtime.from_boots(boots, extras, tz=TZ)
        events = {e['kind']: e for e in result['events']}
        self.assertEqual(events['wake']['source'], 'USB keyboard')
        self.assertEqual((events['restart']['by'], events['restart']['reason']), ('Windows Update', 'Planned'))
        self.assertEqual(result['days']['2026-11-20']['restarts'], 1)   # asked for a restart, even an hour later

    def test_local_days_follow_daylight_saving(self):
        zone = new_york()
        self.assertEqual(downtime.day_length(dt.date(2026, 11, 1), zone), 25*H)
        self.assertEqual(downtime.day_length(dt.date(2026, 3, 8), zone), 23*H)
        boots = [boot('2026-10-31T22:00', '2026-10-31T23:00', tz=zone), boot('2026-11-01T23:00', '2026-11-01T23:30', tz=zone)]
        result = downtime.from_boots(boots, {}, tz=zone)
        self.assertEqual(result['days']['2026-11-01']['off_s'], 24*H)     # midnight to 23:00 on a 25-hour day
        ledger = dict(daily={'2026-11-01': 0.5*H})
        downtime.apply(ledger, result, now=at('2026-11-02T12:00', zone), tz=zone)
        self.assertEqual(ledger['downtime_daily']['2026-11-01']['unknown_s'], 25*H - 24*H - 0.5*H)


class ApplyTests(unittest.TestCase):
    NOW = at('2026-11-20T12:00')

    def evidence(self):
        return downtime.from_boots([boot('2026-11-18T08:00', '2026-11-18T20:00'), boot('2026-11-19T08:00', '2026-11-20T12:00')],
                                   {}, tz=TZ)

    def test_contract_shape_and_day_length(self):
        ledger = dict(daily={'2026-11-18': 12*H, '2026-11-19': 16*H, '2026-11-20': 4*H})
        downtime.apply(ledger, self.evidence(), now=self.NOW, tz=TZ)
        self.assertEqual(schema.problems(dict(total_seconds=0, downtime_daily=ledger['downtime_daily'])), [])
        days = ledger['downtime_daily']
        self.assertEqual(sorted(days), ['2026-11-18', '2026-11-19', '2026-11-20'])
        self.assertEqual(days['2026-11-18'], entry(off_s=4*H, unknown_s=8*H, shutdowns=1))   # 20:00-24:00 off, 00:00-08:00 unknown
        self.assertEqual(days['2026-11-19'], entry(off_s=8*H, unknown_s=0))
        self.assertEqual(days['2026-11-20'], entry(unknown_s=8*H))                             # only the 12 h passed so far
        for key, value in days.items():
            awake = ledger['daily'][key]
            self.assertLessEqual(awake + value['asleep_s'] + value['off_s'] + value['unknown_s'], 24*H)
            self.assertTrue(all(type(value[f]) is int and value[f] >= 0 for f in downtime.FIELDS))

    def test_awake_wins_when_awake_time_moves_later(self):
        ledger = dict(daily={})
        downtime.apply(ledger, self.evidence(), now=self.NOW, tz=TZ)
        self.assertEqual(ledger['downtime_daily']['2026-11-19']['unknown_s'], 16*H)
        ledger['daily'] = {'2026-11-19': 20*H}          # an import or repair placed more awake time on this day
        settled = downtime.for_server(ledger, now=self.NOW, tz=TZ)
        self.assertEqual(settled['2026-11-19'], entry(off_s=4*H, unknown_s=0))

    def test_reruns_are_stable_edge_day_only_improves_and_old_days_are_kept(self):
        ledger = dict(daily={}, downtime_daily={'2026-01-05': entry(off_s=86400)})
        downtime.apply(ledger, self.evidence(), now=self.NOW, tz=TZ)
        first = copy.deepcopy(ledger['downtime_daily'])
        downtime.apply(ledger, self.evidence(), now=self.NOW, tz=TZ)
        self.assertEqual(ledger['downtime_daily'], first)
        self.assertIn('2026-01-05', ledger['downtime_daily'])        # rotated out of the log, still kept
        # A later, partial read (it starts at the 19 Nov boot) must not make 18 Nov worse; 19 Nov is its edge day.
        partial = downtime.from_boots([boot('2026-11-19T08:00', '2026-11-20T12:00')], {}, tz=TZ)
        downtime.apply(ledger, partial, now=self.NOW, tz=TZ)
        self.assertEqual(ledger['downtime_daily']['2026-11-19'], first['2026-11-19'])
        self.assertEqual(ledger['downtime_daily']['2026-11-18'], first['2026-11-18'])

    def test_at_most_ten_years_are_kept(self):
        old = {(dt.date(2026, 11, 20) - dt.timedelta(days=i)).isoformat(): entry(unknown_s=86400) for i in range(4000)}
        kept = downtime.settle(old, {}, now=self.NOW, tz=TZ)
        self.assertEqual(len(kept), downtime.MAX_DAYS)

    def test_summary_counts_missing_days_as_unknown(self):
        ledger = dict(daily={'2026-11-18': 6*H, '2026-11-17': 2*H},
                      downtime_daily={'2026-11-18': entry(asleep_s=10*H, off_s=4*H, unknown_s=4*H, restarts=1, unexpected_stops=1)})
        summary = downtime.summarize(ledger, dt.date(2026, 11, 16), dt.date(2026, 11, 18), now=self.NOW, tz=TZ)
        totals = summary['totals']
        self.assertEqual((totals['awake'], totals['asleep'], totals['off']), (8*H, 10*H, 4*H))
        self.assertEqual(totals['unknown'], 24*H + 22*H + 4*H)
        self.assertAlmostEqual(totals['share'], 8 / 22)
        self.assertEqual(totals['between_stops'], 8*H)
        self.assertEqual([row['evidence'] for row in summary['rows']], [False, True, True])

    def test_longest_spans_from_events(self):
        events = [dict(time=0, kind='start'), dict(time=5*H, kind='sleep'), dict(time=6*H, kind='wake'),
                  dict(time=8*H, kind='shutdown'), dict(time=20*H, kind='start'), dict(time=21*H, kind='unexpected'),
                  dict(time=30*H, kind='start')]
        self.assertEqual(downtime.longest_spans(events, 0, 31*H), dict(awake=5*H, off=12*H))
        self.assertEqual(downtime.longest_spans(events, 2*H, 9*H), dict(awake=3*H, off=1*H))


class LedgerFormatTests(unittest.TestCase):
    def ledger(self):
        value = tracker.new_ledger()
        value['downtime_daily'] = {'2026-11-20': entry(asleep_s=25200, off_s=3600, restarts=1, shutdowns=1)}
        value['downtime_sources'] = dict(log='windows-log', log_from='2026-07-03')
        return value

    def test_new_key_validates_and_bad_entries_are_named_and_salvaged(self):
        self.assertEqual(schema.problems(self.ledger()), [])
        for bad in ({'2026-11-20': entry(asleep_s=-1)}, {'2026-11-20': entry(asleep_s=1.5)}, {'20-11-2026': entry()},
                    {'2026-11-20': entry(asleep_s=50000, off_s=50000)}, {'2026-11-20': {'asleep_s': 1}}, []):
            value = dict(self.ledger(), downtime_daily=bad)
            self.assertEqual(schema.problems(value)[0][0], 'downtime_daily', bad)
        damaged = dict(self.ledger(), downtime_daily={'2026-11-20': entry(off_s=1), '2026-11-21': entry(off_s=-5)})
        repaired, notes = schema.salvage(damaged, {})
        self.assertEqual(list(repaired['downtime_daily']), ['2026-11-20'])
        self.assertTrue(notes)

    def test_a_ledger_without_the_key_still_opens(self):
        value = tracker.new_ledger()
        self.assertNotIn('downtime_daily', tracker.validate_ledger(value))

    @unittest.skipUnless(Path(__file__).with_name('.git').exists() or Path(__file__).parent.joinpath('.git').exists(), 'needs git')
    def test_the_previous_release_validator_opens_the_new_format(self):
        """1.2.1's strict validator ignores the new keys, so going back a version keeps the ledger readable."""
        try:
            source = subprocess.run(['git', 'show', 'c47b133:lifetime_ledger.py'], capture_output=True, text=True,
                                    cwd=Path(__file__).parent, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('previous release source not available')
        spec = importlib.util.spec_from_loader('lifetime_ledger_1_2_1', loader=None)
        old = importlib.util.module_from_spec(spec)
        exec(source, old.__dict__)
        self.assertEqual(old.problems(self.ledger()), [])
        old.validate(self.ledger())


class AwakeUnchangedTests(unittest.TestCase):
    """Downtime code must never change awake-time accounting: compare everything but the new keys, byte for byte."""
    @staticmethod
    def awake_bytes(ledger):
        return json.dumps({k: v for k, v in ledger.items() if k not in ('downtime_daily', 'downtime_sources')},
                          sort_keys=True).encode()

    def test_tracker_refresh_changes_only_downtime_keys(self):
        with patch('lifetime_daily.boot_history', return_value=(None, [], True)):
            plain, hooked = tracker.new_ledger(), tracker.new_ledger()
            found = ([boot('2026-11-18T08:00', '2026-11-18T20:00', sleeps=[('2026-11-18T12:00', '2026-11-18T13:00')]),
                      boot('2026-11-19T08:00', '2026-11-20T12:00')], {}, dict(log='windows-log'), at('2026-11-20T12:00'))
            tick = 100
            for minute in range(0, 600, 1):
                stamp = (dt.datetime(2026, 11, 20, 5, tzinfo=UTC) + dt.timedelta(minutes=minute)).isoformat()
                tick += 60
                for ledger in (plain, hooked):
                    tracker.sample(ledger, tick, 'boot-a', stamp)
                if minute % 60 == 0:
                    tracker.refresh_downtime(hooked, found)
                    tracker.refresh_downtime(hooked, ValueError('log denied'))
                    tracker.refresh_downtime(hooked, found)
            self.assertIn('downtime_daily', hooked)
            self.assertEqual(self.awake_bytes(plain), self.awake_bytes(hooked))
            tracker.validate_ledger(hooked)

    def test_failures_leave_the_ledger_as_it_was(self):
        ledger = tracker.new_ledger()
        before = json.dumps(ledger, sort_keys=True)
        tracker.refresh_downtime(ledger, ('not', 'a', 'result'))
        self.assertEqual(json.dumps(ledger, sort_keys=True), before)
        tracker.refresh_downtime(ledger, PermissionError('denied'))
        self.assertEqual(ledger['downtime_sources'], dict(note='unavailable'))
        self.assertNotIn('downtime_daily', ledger)

    def test_import_with_downtime_keeps_awake_totals_identical(self):
        provider = lambda: ([boot('2026-08-30T22:00', '2026-08-31T02:00', sleeps=[('2026-08-30T23:00', '2026-08-31T00:00')])],
                            at('2026-11-06T08:00'))
        power = lambda since=None: (provider()[0], dict(stops=[]), dict(log='windows-log'))
        outputs = []
        for extra in ((), (power,)):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'ledger.json'
                ledger = tracker.new_ledger()
                ledger.update(total_seconds=600, boots=1, daily={'2026-11-06': 600}, tracking_started='2026-11-06')
                path.write_text(json.dumps(ledger))
                with patch('sys.argv', ['import', '--apply', '--ledger', str(path)]), contextlib.redirect_stdout(io.StringIO()):
                    imp.main(provider, *extra)
                outputs.append(json.loads(path.read_text()))
        self.assertNotIn('downtime_daily', outputs[0])
        self.assertIn('2026-08-30', outputs[1]['downtime_daily'])
        self.assertEqual(outputs[1]['downtime_daily']['2026-08-30']['asleep_s'], 1*H)
        self.assertEqual(self.awake_bytes(outputs[0]), self.awake_bytes(outputs[1]))


class WindowsLogTests(unittest.TestCase):
    @staticmethod
    def event(provider, identity, when, **data):
        fields = ''.join(f'<Data Name="{key}">{value}</Data>' for key, value in data.items())
        return (f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
                f'<System><Provider Name="{provider}"/><EventID>{identity}</EventID>'
                f'<TimeCreated SystemTime="{when}"/></System><EventData>{fields}</EventData></Event>')

    def test_stops_are_one_per_incident_and_wake_sources_are_kept(self):
        raw = '<Events>' + ''.join([
            self.event('Microsoft-Windows-Kernel-General', 12, '2026-11-18T01:00:00Z'),
            self.event('Microsoft-Windows-Power-Troubleshooter', 1, '2026-11-18T03:00:05Z', SleepTime='2026-11-18T02:00:00Z',
                       WakeTime='2026-11-18T03:00:00Z', WakeSourceText='Power Button'),
            self.event('Microsoft-Windows-Kernel-General', 12, '2026-11-18T05:00:00Z'),
            self.event('Microsoft-Windows-Kernel-Power', 41, '2026-11-18T05:00:20Z'),
            self.event('EventLog', 6008, '2026-11-18T05:00:40Z')]) + '</Events>'
        extras = windows.new_extras()
        boots, _ = windows._parse_events(windows._tap(windows._xml_events(io.StringIO(raw)), extras))
        self.assertEqual(len(boots), 2)
        self.assertEqual(len(extras['stops']), 1)
        self.assertEqual(extras['wakes'], [(at('2026-11-18T03:00:00+00:00'), 'Power Button')])
        result = downtime.from_boots(boots, extras, tz=UTC)
        self.assertEqual(result['days']['2026-11-18']['unexpected_stops'], 1)
        self.assertEqual(result['days']['2026-11-18']['asleep_s'], 1*H)

    def test_filtered_query_names_every_power_event_and_a_start_time(self):
        query = windows.power_query(at('2026-11-20T00:00:00+00:00'))
        for identity in (12, 13, 41, 42, 107, 1074, 6008):
            self.assertIn(f'EventID={identity}', query)
        self.assertIn("TimeCreated[@SystemTime>='2026-11-20T00:00:00.000Z']", query)
        with patch.object(windows, '_query_system', side_effect=OSError('Access is denied')):
            with self.assertRaisesRegex(ValueError, 'Cannot read the System event log'):
                windows.windows_power()


class LinuxJournalTests(unittest.TestCase):
    def test_unclean_end_is_an_unexpected_stop_and_the_current_boot_is_open(self):
        boots = [dict(id='a', start=at('2026-11-18T08:00'), end=at('2026-11-18T10:00'), index=-2),
                 dict(id='b', start=at('2026-11-18T12:00'), end=at('2026-11-18T14:00'), index=-1),
                 dict(id='c', start=at('2026-11-18T15:00'), end=at('2026-11-18T16:00'), index=0)]
        def details(item, shutdowns):
            item.update(events=[], clean=item['id'] == 'b')
        with patch.object(imp, '_journal_boots', return_value=(boots, boots[2])), \
                patch.object(imp, '_wtmp_shutdowns', return_value=[]), patch.object(imp, '_power_details', side_effect=details):
            chosen, extras, info = imp.linux_power()
        self.assertEqual(info['log'], 'journal')
        self.assertEqual(extras['stops'], [at('2026-11-18T12:00')])
        self.assertFalse(chosen[-1]['clean'])
        result = downtime.from_boots(chosen, extras, tz=TZ)
        day = result['days']['2026-11-18']
        # a: unclean, then b starts: an unexpected stop with the gap left unknown; b: clean, c an hour later: a shutdown
        self.assertEqual((day['unexpected_stops'], day['restarts'], day['shutdowns'], day['off_s']), (1, 0, 1, 1*H))
        with patch.object(imp, '_journal_boots', return_value=(boots, boots[2])), \
                patch.object(imp, '_wtmp_shutdowns', return_value=[]), patch.object(imp, '_power_details', side_effect=details):
            self.assertEqual([b['id'] for b in imp.linux_power(since=at('2026-11-18T13:00'))[0]], ['a', 'b', 'c'])   # and the boot before


class DeeperImportTests(unittest.TestCase):
    """Event 1074, archived System logs and rotated wtmp files, from recorded-shape synthetic data."""
    event = staticmethod(WindowsLogTests.event)

    @staticmethod
    def record(provider, identity, when, record, **data):
        fields = ''.join(f'<Data Name="{key}">{value}</Data>' for key, value in data.items())
        return (f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="{provider}"/>'
                f'<EventID>{identity}</EventID><TimeCreated SystemTime="{when}"/><EventRecordID>{record}</EventRecordID>'
                f'</System><EventData>{fields}</EventData></Event>')

    def test_1074_says_who_asked_and_english_type_only(self):
        raw = '<Events>' + ''.join([
            self.event('Microsoft-Windows-Kernel-General', 12, '2026-11-18T01:00:00Z'),
            self.event('User32', 1074, '2026-11-18T10:00:00Z', param1='C:\\Windows\\System32\\shutdown.exe (TEST-PC)',
                       param2='TEST-PC', param3='Operating System: Upgrade (Planned)', param4='0x80020003',
                       param5='restart', param6='', param7='EXAMPLE\\someone'),
            self.event('Microsoft-Windows-Kernel-General', 13, '2026-11-18T10:00:30Z'),
            self.event('Microsoft-Windows-Kernel-General', 12, '2026-11-18T11:00:00Z'),
            self.event('User32', 1074, '2026-11-18T12:00:00Z', param1='C:\\Windows\\explorer.exe (TEST-PC)',
                       param3='Other (Unplanned)', param5='Neu starten', param7='EXAMPLE\\someone'),
            self.event('Microsoft-Windows-Kernel-General', 13, '2026-11-18T12:00:10Z'),
            self.event('Microsoft-Windows-Kernel-General', 12, '2026-11-18T14:00:00Z')]) + '</Events>'
        extras = windows.new_extras()
        boots, _ = windows._parse_events(windows._tap(windows._xml_events(io.StringIO(raw)), extras))
        self.assertEqual([r[1]['kind'] for r in extras['requests']], ['restart', None])
        self.assertEqual(extras['requests'][0][1]['by'], 'shutdown.exe (EXAMPLE\\someone)')
        self.assertEqual(extras['requests'][0][1]['reason'], 'Operating System: Upgrade (Planned)')
        result = downtime.from_boots(boots, extras, tz=UTC)
        day = result['days']['2026-11-18']
        # An hour off, but Windows was asked to restart; the localized type falls back to the 10-minute rule.
        self.assertEqual((day['restarts'], day['shutdowns']), (1, 1))
        restart = next(e for e in result['events'] if e['kind'] == 'restart')
        self.assertEqual(restart['by'], 'shutdown.exe (EXAMPLE\\someone)')
        # Names and accounts stay in the event list; the saved day has numbers only.
        ledger = dict(daily={})
        downtime.apply(ledger, result, now=at('2026-11-19T00:00:00+00:00'), tz=UTC)
        self.assertNotIn('someone', json.dumps(ledger))
        self.assertNotIn('TEST-PC', json.dumps(ledger))

    @staticmethod
    def fake_wevtutil(outputs):
        def run(args, **kwargs):
            code, out = outputs[Path(args[2]).name]
            return subprocess.CompletedProcess(args, code, out.encode('utf-16-le'), b'Access is denied.' if code else b'')
        return run

    def test_archives_are_read_once_in_record_order_before_the_live_log(self):
        folder = Path(tempfile.mkdtemp())
        for name in ('Archive-System-2026-01-01.evtx', 'Archive-System-2026-03-01.evtx', 'Application.evtx'):
            (folder / name).write_bytes(b'')
        start = lambda when, record: self.record('Microsoft-Windows-Kernel-General', 12, when, record)
        stop = lambda when, record: self.record('Microsoft-Windows-Kernel-General', 13, when, record)
        outputs = {'Archive-System-2026-01-01.evtx': (0, '<Events>' + start('2026-01-02T08:00:00Z', 10) + stop('2026-01-02T18:00:00Z', 11) + '</Events>'),
                   # the second archive repeats record 11 and holds one event already in the live log
                   'Archive-System-2026-03-01.evtx': (0, '<Events>' + stop('2026-01-02T18:00:00Z', 11) + start('2026-03-02T08:00:00Z', 20)
                                                       + stop('2026-03-02T09:00:00Z', 21) + start('2026-07-03T08:00:00Z', 30) + '</Events>')}
        with patch.object(windows.subprocess, 'run', side_effect=self.fake_wevtutil(outputs)) as run:
            events, info = windows.archive_events(before=at('2026-07-03T00:00:00+00:00'), folder=folder)
        self.assertEqual(info, dict(archives=2, archive_note='read'))
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all('/lf:true' in call.args[0] and 'EventID=1074' in ' '.join(call.args[0]) for call in run.call_args_list))
        self.assertEqual([int(e.find('{*}System/{*}EventRecordID').text) for e in events], [10, 11, 20, 21])
        boots, _ = windows._parse_events(iter(events))
        self.assertEqual(len(boots), 2)
        self.assertTrue(all(b['clean'] for b in boots))

    def test_archives_are_read_once_and_ordered_by_file_when_record_numbers_restart(self):
        folder = Path(tempfile.mkdtemp())
        for name in ('Archive-System-2026-01-01.evtx', 'Archive-System-2026-03-01.evtx'):
            (folder / name).write_bytes(b'x')
        start = lambda when, record: self.record('Microsoft-Windows-Kernel-General', 12, when, record)
        stop = lambda when, record: self.record('Microsoft-Windows-Kernel-General', 13, when, record)
        outputs = {'Archive-System-2026-01-01.evtx': (0, '<Events>' + start('2026-01-02T08:00:00Z', 100) + stop('2026-01-02T18:00:00Z', 101) + '</Events>'),
                   # the log was cleared in between: numbering starts again
                   'Archive-System-2026-03-01.evtx': (0, '<Events>' + start('2026-03-02T08:00:00Z', 5) + stop('2026-03-02T09:00:00Z', 6) + '</Events>')}
        with patch.object(windows.subprocess, 'run', side_effect=self.fake_wevtutil(outputs)) as run:
            events, info = windows.archive_events(folder=folder)
            self.assertEqual([int(e.find('{*}System/{*}EventRecordID').text) for e in events], [100, 101, 5, 6])
            again, _ = windows.archive_events(folder=folder)
            self.assertEqual(run.call_count, 2)                    # unchanged archives are not read again
            (folder / 'Archive-System-2026-03-01.evtx').write_bytes(b'changed')
            windows.archive_events(folder=folder)
            self.assertEqual(run.call_count, 3)
        self.assertEqual(len(again), 4)
        boots, _ = windows._parse_events(iter(events))
        self.assertEqual([b['clean'] for b in boots], [True, True])

    def test_archives_absent_or_denied_never_fail(self):
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(windows.archive_events(folder=empty), ([], dict(archives=0, archive_note='none')))
        self.assertEqual(windows.archive_events(folder=empty / 'missing'), ([], dict(archives=0, archive_note='none')))
        with patch.object(windows.os, 'listdir', side_effect=PermissionError('denied')):
            self.assertEqual(windows.archive_events(folder=empty), ([], dict(archives=0, archive_note='denied')))
        (empty / 'Archive-System-1.evtx').write_bytes(b'')
        with patch.object(windows.subprocess, 'run', side_effect=self.fake_wevtutil({'Archive-System-1.evtx': (5, '')})):
            self.assertEqual(windows.archive_events(folder=empty), ([], dict(archives=0, archive_note='denied')))

    def test_windows_power_reads_archives_then_the_live_log(self):
        archived = [ET.fromstring(self.record('Microsoft-Windows-Kernel-General', 12, '2026-01-02T08:00:00Z', 1)),
                    ET.fromstring(self.record('Microsoft-Windows-Kernel-General', 13, '2026-01-02T18:00:00Z', 2))]
        live = '<Events>' + self.event('Microsoft-Windows-Kernel-General', 12, '2026-07-03T08:00:00Z') + '</Events>'
        original = subprocess.Popen
        def popen(args, **kwargs):
            self.assertTrue(any(str(a).startswith('/q:') for a in args))
            return original([sys.executable, '-c', f'import sys; sys.stdout.buffer.write({live.encode()!r})'], **kwargs)
        with patch.object(windows, 'archive_events', return_value=(archived, dict(archives=1, archive_note='read'))), \
                patch.object(windows, '_first_live_event', return_value=at('2026-07-03T08:00:00+00:00')), \
                patch.object(windows.subprocess, 'Popen', side_effect=popen):
            boots, extras, info = windows.windows_power()
        self.assertEqual(len(boots), 2)
        self.assertEqual(info, dict(log='windows-log', archives=1, archive_note='read'))
        # Without a known start of the live log, archives are skipped (they could repeat a boot).
        with patch.object(windows, 'archive_events') as archives, patch.object(windows, '_first_live_event', return_value=None), \
                patch.object(windows.subprocess, 'Popen', side_effect=popen):
            only_live, _, skipped = windows.windows_power()
        archives.assert_not_called()
        self.assertEqual((len(only_live), skipped['archive_note']), (1, 'error'))
        result = downtime.from_boots(boots, extras, tz=UTC)
        self.assertEqual(result['days']['2026-01-02']['shutdowns'], 1)
        self.assertEqual(downtime.sources_for(result, tz=UTC, **info)['log_from'], '2026-01-02')

    def test_a_damaged_archive_never_stops_the_import_or_the_hourly_read(self):
        poisoned = [ET.fromstring(self.record('Microsoft-Windows-Kernel-General', 12, '2026-01-02T08:00:00Z', 1)),
                    ET.fromstring(self.record('Microsoft-Windows-Power-Troubleshooter', 1, '2026-01-02T09:00:00Z', 2,
                                              SleepTime='2026-01-02T12:00:00Z', WakeTime='2026-01-02T10:00:00Z'))]
        live = ('<Events>' + self.event('Microsoft-Windows-Kernel-General', 12, '2026-07-03T08:00:00Z')
                + self.event('Microsoft-Windows-Kernel-General', 13, '2026-07-03T18:00:00Z')
                + self.event('Microsoft-Windows-Kernel-General', 12, '2026-07-04T08:00:00Z') + '</Events>')
        original = subprocess.Popen
        def popen(args, **kwargs):
            return original([sys.executable, '-c', f'import sys; sys.stdout.buffer.write({live.encode()!r})'], **kwargs)
        with patch.object(windows, 'archive_events', return_value=(poisoned, dict(archives=1, archive_note='read'))), \
                patch.object(windows, '_first_live_event', return_value=at('2026-07-03T08:00:00+00:00')), \
                patch.object(windows, '_native_boot_time', return_value=at('2026-07-04T08:00:00+00:00')), \
                patch.object(windows.subprocess, 'Popen', side_effect=popen), contextlib.redirect_stdout(io.StringIO()):
            boots, _, info = windows.windows_power()
            self.assertEqual((len(boots), info['archives'], info['archive_note']), (2, 0, 'error'))
            boots, current = windows.windows_boots()
        result = imp.rebuild(boots, current, tracker.new_ledger(), tz=UTC)
        self.assertEqual(result['seconds'], 10*H)            # the live log alone, imported as before

    WTMP_OLD = ('reboot   system boot  6.8.0-45-generic Mon Nov  2 08:00:00 2026 - Mon Nov  2 17:00:00 2026  (09:00)\n'
                'shutdown system down  6.8.0-45-generic Mon Nov  2 17:00:00 2026 - Tue Nov  3 08:00:00 2026  (15:00)\n'
                'reboot   system boot  6.8.0-45-generic Tue Nov  3 08:00:00 2026 - crash                     (01:00)\n')
    WTMP_NOW = 'reboot   system boot  6.8.0-45-generic Wed Nov  4 08:00:00 2026   still running\n'

    def test_rotated_wtmp_including_gzip_is_read_with_last_f(self):
        import gzip
        folder = Path(tempfile.mkdtemp())
        (folder / 'wtmp.1').write_bytes(b'one')
        with gzip.open(folder / 'wtmp.2.gz', 'wb') as stream:
            stream.write(b'two')
        (folder / 'wtmp.3.gz').write_bytes(b'not gzip')       # damaged: skipped, never an error
        seen, copies = [], []
        def command(args, no_matches=False):
            if '-f' not in args:
                return self.WTMP_NOW
            path = Path(args[args.index('-f') + 1])
            seen.append(path.read_bytes())
            copies.append(path)
            return self.WTMP_OLD if path.read_bytes() == b'two' else ''
        with patch.object(imp, 'command', side_effect=command):
            shutdowns, records, rotated = imp.read_wtmp(folder)
        self.assertEqual(seen, [b'one', b'two'])
        self.assertEqual(rotated, 2)
        self.assertFalse(copies[1].exists())          # the decompressed copy is removed
        self.assertEqual(shutdowns, [at('2026-11-02T17:00:00+00:00')])
        self.assertEqual([(r['crash'], r['end'] is None) for r in records], [(False, False), (True, True), (False, True)])
        with patch.object(imp, 'command', side_effect=OSError('no last')):
            self.assertEqual(imp.read_wtmp(folder), ([], [], 0))

    def test_linux_boots_older_than_the_journal_come_from_wtmp(self):
        journal = [dict(id='j', start=at('2026-11-04T08:00:00+00:00'), end=at('2026-11-04T12:00:00+00:00'), index=0)]
        records = imp.parse_wtmp(self.WTMP_OLD + self.WTMP_NOW)
        def details(item, shutdowns):
            item.update(events=[], clean=False)
        with patch.object(imp, '_journal_boots', return_value=(journal, journal[0])), \
                patch.object(imp, 'read_wtmp', return_value=([at('2026-11-02T17:00:00+00:00')], records, 3)), \
                patch.object(imp, '_power_details', side_effect=details):
            boots, extras, info = imp.linux_power()
        self.assertEqual(info, dict(log='journal', wtmp=3))
        self.assertEqual([b['id'] for b in boots], ['wtmp', 'wtmp', 'j'])
        result = downtime.from_boots(boots, extras, tz=UTC)
        self.assertEqual(result['days']['2026-11-02']['shutdowns'], 1)
        self.assertEqual(result['days']['2026-11-02']['off_s'], 7*H)
        self.assertEqual(result['days']['2026-11-03']['off_s'], 8*H)
        self.assertEqual(result['days']['2026-11-04']['unexpected_stops'], 1)      # the crash record, found at the next start


class SendingTests(unittest.TestCase):
    """Client side only: what a connected computer adds to history, and when."""
    NOW = dt.datetime.now().timestamp()

    def state(self, history=True, days=3):
        today = dt.date.today()
        ledger = tracker.new_ledger()
        ledger['daily'] = {(today - dt.timedelta(days=i)).isoformat(): 3600.0 for i in range(1, days + 1)}
        ledger['downtime_daily'] = {(today - dt.timedelta(days=i)).isoformat(): entry(asleep_s=3600, off_s=3600, unknown_s=1000, restarts=1)
                                    for i in range(1, days + 1)}
        return dict(policy=dict(history=history, resources=False), ledger=ledger)

    @staticmethod
    def message():
        return dict(version=1, device_id='d', policy_version=1, policy=dict(history=True, resources=False), records=[],
                    history=dict(daily={}, imported_daily={}, first_seen=None, first_seen_source='unknown',
                                 recording_started=None, daily_estimated=False))

    def test_only_for_a_server_that_advertises_downtime_and_only_with_history_sharing(self):
        from lifetime_client import add_downtime
        import lifetime_protocol as protocol
        message = self.message()
        self.assertIs(add_downtime(message, self.state(), ['history.detail', 'health.detail']), message)
        self.assertIs(add_downtime(message, self.state(), []), message)
        self.assertIs(add_downtime(message, self.state(history=False), ['downtime']), message)
        without_history = {k: v for k, v in message.items() if k != 'history'}
        self.assertIs(add_downtime(without_history, self.state(), ['downtime']), without_history)
        sent = add_downtime(message, self.state(), ['downtime', 'history.detail'])
        days = sent['history']['downtime_daily']
        self.assertEqual(len(days), 3)
        self.assertEqual(downtime.problems(days), [])
        self.assertTrue(all(set(day) == set(downtime.FIELDS) for day in days.values()))
        # unknown is re-settled against the awake time in this message (none here): 24 h minus 1 h asleep, 1 h off
        self.assertEqual(next(iter(days.values()))['unknown_s'], 86400 - 2 * 3600)
        self.assertNotIn('downtime_daily', message['history'])         # the original message is unchanged
        # An old server never gets it, even through the existing optional-part filter.
        self.assertNotIn('downtime_daily', protocol.without_extended(add_downtime(message, self.state(), []))['history'])

    def test_days_sent_pass_the_server_rule_against_the_message_own_awake_snapshot(self):
        """Review item 10: the message carries an older history.daily than the ledger; on a 25-hour day even a few
        seconds more awake in that snapshot made asleep + off + unknown + awake pass 90,000 and the whole sync fail."""
        from lifetime_client import add_downtime
        zone = new_york()
        state = self.state()
        state['ledger']['daily']['2026-11-01'] = 2*H
        state['ledger']['downtime_daily']['2026-11-01'] = entry(asleep_s=10*H, off_s=2*H, unknown_s=13*H, restarts=10001)
        state['ledger']['downtime_daily']['2026-11-02'] = {'asleep_s': -1}          # damaged: never sent
        message = self.message()
        message['history']['daily'] = {**{k: v + 5 for k, v in state['ledger']['daily'].items()}, '2026-11-01': 3*H}
        sent = add_downtime(message, state, ['downtime'], tz=zone)['history']
        self.assertIn('2026-11-01', sent['downtime_daily'])
        self.assertEqual(sent['downtime_daily']['2026-11-02']['asleep_s'], 0)      # repaired, never sent as it was
        for key, day in sent['downtime_daily'].items():
            self.assertTrue(downtime.server_day_ok(day, sent['daily'].get(key, 0)), key)
            self.assertLessEqual(day['asleep_s'] + day['off_s'] + day['unknown_s'] + sent['daily'].get(key, 0), 90000)
        self.assertEqual(sent['downtime_daily']['2026-11-01']['restarts'], downtime.MAX_COUNT)
        self.assertFalse(downtime.valid_day(entry(restarts=10001)))
        self.assertFalse(downtime.server_day_ok(entry(unknown_s=86400), 3601 + 86400 - 86400 + 3600))

    def test_stays_under_the_transport_limit_by_dropping_oldest_days(self):
        from lifetime_client import add_downtime
        import lifetime_protocol as protocol
        message = self.message()
        message['history']['padding'] = 'x' * (protocol.MAX_BYTES - 200000)
        sent = add_downtime(message, self.state(days=3600), ['downtime'])
        days = sent['history']['downtime_daily']
        self.assertLess(len(days), 3600)
        self.assertLessEqual(len(protocol.canonical(sent)), protocol.MAX_BYTES)
        self.assertEqual(max(days), (dt.date.today() - dt.timedelta(days=1)).isoformat())   # newest days are kept

    def test_machine_agent_keeps_downtime_in_its_own_ledger_without_breaking_its_outbox(self):
        import lifetime_agent as agent
        import lifetime_protocol as protocol
        root = Path(tempfile.mkdtemp()) / 'machine'
        with patch.object(agent, 'protect_root'):
            machine = agent.MachineAgent.initialize(root)
        self.addCleanup(machine.close)
        machine.set_policy(dict(history=True, resources=False))
        found = ([boot('2026-11-18T08:00', '2026-11-18T20:00', sleeps=[('2026-11-18T12:00', '2026-11-18T13:00')]),
                  boot('2026-11-19T08:00', '2026-11-20T12:00')], {}, dict(log='windows-log'), at('2026-11-20T12:00'))
        machine.power_reader = lambda since: found
        stamp = lambda tick: (dt.datetime(2026, 11, 20, 5, tzinfo=UTC) + dt.timedelta(seconds=tick)).isoformat()
        machine.sample(100, 'boot-a', stamp(100))
        machine._power_job.result(timeout=5)
        machine.sample(130, 'boot-a', stamp(130))
        ledger = machine.ledger()
        self.assertEqual(ledger['downtime_daily']['2026-11-18']['asleep_s'], 3600)
        self.assertNotIn('downtime_daily', machine.batch()['history'])      # the outbox format is unchanged (validated)
        protocol.validate_sync(machine.batch())
        machine.set_policy(dict(history=False, resources=False))
        self.assertNotIn('downtime_daily', machine.ledger())
        self.assertNotIn('downtime_sources', machine.ledger())
        self.assertIsNone(agent.MachineAgent.power_reader)       # off unless the reporting loop runs


class UptimeReportTests(unittest.TestCase):
    """Desktop report (PDF/CSV): the Uptime and downtime section for the chosen period."""
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def ledger():
        value = tracker.new_ledger()
        value['daily'] = {'2026-11-17': 8 * H, '2026-11-18': 6 * H}
        value['total_seconds'] = 14 * H
        value['last_update'] = '2026-11-20T05:00:00+00:00'
        value['downtime_daily'] = {'2026-11-18': entry(asleep_s=10 * H, off_s=4 * H, unknown_s=4 * H, restarts=1, shutdowns=1,
                                                       unexpected_stops=1)}
        value['downtime_sources'] = dict(log='windows-log', log_from='2026-11-18', archives=0, archive_note='denied')
        return value

    @staticmethod
    def build(ledger, history=True, **kwargs):
        import lifetime_reports as reports
        return reports.build_report(ledger, dict(completed=True, history=history), **kwargs)

    def test_totals_counts_and_coverage_for_the_period(self):
        report = self.build(self.ledger(), start='2026-11-16', end='2026-11-18')
        values = report['uptime']['values']
        self.assertEqual((values['awake'], values['asleep'], values['off']), (14 * H, 10 * H, 4 * H))
        self.assertEqual(values['unknown'], sum(int(downtime.day_length(dt.date(2026, 11, d))) for d in (16, 17, 18)) - 28 * H)
        self.assertEqual((values['restarts'], values['shutdowns'], values['unexpected_stops']), (1, 1, 1))
        self.assertEqual(values['share'], round(14 / 28 * 100, 1))
        self.assertEqual(values['between_stops'], 14 * H)
        self.assertIsNone(values['longest_awake'])          # no power events given
        self.assertEqual((values['days'], values['evidence_days']), (3, 2))
        self.assertTrue(any('never counted as zero' in note for note in report['notes']))
        events = [dict(time=at('2026-11-18T08:00'), kind='start'), dict(time=at('2026-11-18T11:00'), kind='shutdown'),
                  dict(time=at('2026-11-18T12:00'), kind='start')]
        with_events = self.build(self.ledger(), start='2026-11-16', end='2026-11-18', power_events=events)
        self.assertEqual(with_events['uptime']['values']['longest_off'], 1 * H)

    def test_csv_and_pdf_carry_the_section_in_both_languages(self):
        import csv
        import lifetime_reports as reports
        for language, heading, row in (('en', 'Uptime and downtime', 'Unexpected stops (Kernel-Power 41 / EventLog 6008)'),
                                       ('vi', 'Thời gian hoạt động và ngừng hoạt động', 'Lần dừng bất thường (Kernel-Power 41 / EventLog 6008)')):
            report = self.build(self.ledger(), start='2026-11-16', end='2026-11-18', language=language)
            directory = Path(tempfile.mkdtemp())
            reports.write_csv(report, directory / 'report.csv')
            with (directory / 'report.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = [line for line in csv.reader(stream) if line[0] == 'uptime']
            self.assertIn(['uptime', 'Restarts', '1', '', ''], rows)
            self.assertIn(['uptime', 'Archived System logs', reports.tr('archived logs could not be read', language), '', ''], rows)
            page = reports.report_html(report)
            self.assertIn(heading, page)
            self.assertIn(row, page)
            reports.write_pdf(report, directory / 'report.pdf')
            self.assertTrue((directory / 'report.pdf').read_bytes().startswith(b'%PDF-'))

    def test_no_section_without_history_consent_and_stable_between_preview_and_save(self):
        self.assertIsNone(self.build(self.ledger(), history=False, start='2026-11-16', end='2026-11-18')['uptime'])
        first = self.build(self.ledger())
        second = self.build(self.ledger())
        self.assertEqual(first['uptime'], second['uptime'])          # all data: as of the last save, not "now"
        self.assertIsNone(self.build(tracker.new_ledger())['uptime'])


class HourlyReadTests(unittest.TestCase):
    """The real hourly sequence (tracker and agent) must end where one full read ends (review finding 1).

    Three local days: an office day (boot 08:00, lunch sleep 12-13, shut down 22:00); a day with two restarts and a
    sleep after the second; another office day. Reads every running hour, through tracker.refresh_downtime."""
    DAYS = ('2026-11-16', '2026-11-17', '2026-11-18')

    @staticmethod
    def local(text):
        return dt.datetime.fromisoformat(text).timestamp()     # the computer's own zone, as the tracker uses

    def truth(self):
        t = self.local
        boots, requests = [], []
        for day in self.DAYS:
            if day == '2026-11-17':
                boots += [dict(id=day + 'a', start=t(f'{day}T08:00'), end=t(f'{day}T10:00'), clean=True, events=[]),
                          dict(id=day + 'b', start=t(f'{day}T10:02'), end=t(f'{day}T15:00'), clean=True, events=[]),
                          dict(id=day + 'c', start=t(f'{day}T15:02'), end=t(f'{day}T22:00'), clean=True,
                               events=[(t(f'{day}T18:00'), 'sleep'), (t(f'{day}T19:00'), 'wake')])]
                requests += [(t(f'{day}T09:59:50'), dict(kind='restart', by='a', reason='')),
                             (t(f'{day}T14:59:50'), dict(kind='restart', by='a', reason=''))]
            else:
                boots.append(dict(id=day, start=t(f'{day}T08:00'), end=t(f'{day}T22:00'), clean=True,
                                  events=[(t(f'{day}T12:00'), 'sleep'), (t(f'{day}T13:00'), 'wake')]))
            requests.append((t(f'{day}T21:59:30'), dict(kind='shutdown', by='explorer.exe', reason='')))
        return boots, requests

    def seen(self, now):
        """What the logs hold at ``now``: the running boot ends at now and is not clean yet."""
        boots, requests = self.truth()
        result = []
        for boot in boots:
            if boot['start'] > now:
                continue
            running = boot['end'] > now
            result.append(dict(boot, end=min(boot['end'], now), clean=boot['clean'] and not running,
                               events=[e for e in boot['events'] if e[0] <= now]))
        return result, [r for r in requests if r[0] <= now]

    def windows_reader(self, since, now):
        """windows_power(since): only events at or after ``since``, and a boot exists only with its start event."""
        boots, requests = self.seen(now)
        boots = [b for b in boots if since is None or b['start'] >= since]
        return boots, dict(requests=[r for r in requests if since is None or r[0] >= since], stops=[], wakes=[]), dict(log='windows-log')

    def linux_reader(self, since, now):
        """The real linux_power(since), over a journal that holds what had happened by ``now``."""
        boots, _ = self.seen(now)
        journal = [dict(id=b['id'], start=b['start'], end=b['end'], index=i - len(boots) + 1) for i, b in enumerate(boots)]
        truth = {b['id']: b for b in boots}
        def details(item, shutdowns):
            item.update(events=truth[item['id']]['events'], clean=truth[item['id']]['clean'])
        with patch.object(imp, '_journal_boots', return_value=(journal, journal[-1])), \
                patch.object(imp, 'read_wtmp', return_value=([], [], 0)), patch.object(imp, '_power_details', side_effect=details):
            return imp.linux_power(since)

    def awake(self, day, now):
        boots, _ = self.seen(now)
        total = 0.0
        for boot in boots:
            spans = imp._boot_spans(boot)
            total += sum(seconds for a, b in spans for d_, seconds in downtime.split(a, b) if d_.isoformat() == day)
        return total

    def run_hourly(self, platform, reader, read_days=None):
        ledger = dict(daily={}, downtime_daily={}, downtime_sources={})
        now = None
        for day in self.DAYS:
            if read_days is not None and day not in read_days:
                continue                # Lifetime was not running that day
            for hour in range(8, 22):
                now = self.local(f'{day}T{hour:02d}:30')
                ledger['daily'] = {d_: self.awake(d_, now) for d_ in self.DAYS if d_ <= day}
                since = tracker.power_since(ledger, now, platform)
                boots, extras, info = reader(since, now)
                tracker.refresh_downtime(ledger, (boots, extras, info, now))
        full = dict(daily=ledger['daily'])
        boots, extras, info = reader(None, now)
        downtime.apply(full, downtime.from_boots(boots, extras, now), now=now)
        return ledger['downtime_daily'], full['downtime_daily']

    def test_hourly_windows_reads_equal_one_full_read(self):
        hourly, full = self.run_hourly('win32', self.windows_reader)
        self.assertEqual(hourly, full)
        self.assertEqual((full['2026-11-16']['shutdowns'], full['2026-11-16']['asleep_s'], full['2026-11-16']['unknown_s']), (1, H, 8*H))
        self.assertEqual((full['2026-11-17']['restarts'], full['2026-11-17']['shutdowns'], full['2026-11-17']['asleep_s']), (2, 1, H))
        self.assertEqual(full['2026-11-17']['off_s'], 2*H + 4*60 + 8*H)     # 22-24, two 2-minute restarts, 00-08

    def test_hourly_linux_reads_equal_one_full_read(self):
        hourly, full = self.run_hourly('linux', self.linux_reader)
        self.assertEqual(hourly, full)
        self.assertEqual(full['2026-11-17']['restarts'], 2)

    def test_linux_catches_up_on_days_the_tracker_did_not_run(self):
        self.DAYS = ('2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20')
        hourly, full = self.run_hourly('linux', self.linux_reader, read_days=(self.DAYS[0], self.DAYS[4]))
        self.assertEqual(hourly, full)
        self.assertEqual(hourly['2026-11-16']['shutdowns'], 1)

    def test_partial_first_day_never_replaces_a_fuller_saved_day(self):
        """The old rule's failure: a read starting at the newest boot made that day a partly seen first day."""
        now = self.local('2026-11-17T21:30')
        ledger = dict(daily={}, downtime_daily={}, downtime_sources={})
        boots, extras, info = self.windows_reader(None, now)
        downtime.apply(ledger, downtime.from_boots(boots, extras, now), now=now)
        saved = copy.deepcopy(ledger['downtime_daily'])
        boots, extras, info = self.windows_reader(self.local('2026-11-17T15:00'), now)
        downtime.apply(ledger, downtime.from_boots(boots, extras, now), now=now)
        self.assertEqual(ledger['downtime_daily'], saved)


class LedgerLockTests(unittest.TestCase):
    """Review finding 2: a slow power-log read must never hold the ledger lock, and a busy lock never stops the tracker."""
    def test_import_reads_the_power_log_before_taking_the_ledger_lock(self):
        directory = Path(tempfile.mkdtemp())
        (directory / 'ledger.json').write_text(json.dumps(tracker.new_ledger()))
        seen = {}
        def power(since=None):
            try:
                with tracker.file_lock(directory, 'ledger.lock', blocking=False):
                    seen['free'] = True
            except OSError:
                seen['free'] = False
            return [boot('2026-08-30T22:00', '2026-08-31T02:00')], dict(stops=[]), dict(log='journal')
        provider = lambda: ([boot('2026-08-30T22:00', '2026-08-31T02:00')], at('2026-11-06T08:00'))
        with patch('sys.argv', ['import', '--apply', '--data-dir', str(directory)]), contextlib.redirect_stdout(io.StringIO()):
            imp.main(provider, power)
        self.assertEqual(seen, {'free': True})
        self.assertIn('2026-08-30', json.loads((directory / 'ledger.json').read_text())['downtime_daily'])

    def test_tracker_survives_a_ledger_lock_timeout_without_losing_awake_time(self):
        import threading
        import lifetime_settings as settings
        directory = Path(tempfile.mkdtemp())
        settings.save_settings(dict(settings.defaults(), completed=True, history=True), directory)
        ticks = iter(range(1000, 100000, 60))
        clock = {'tick': 1000}
        def awake_clock():
            clock['tick'] = next(ticks)
            return clock['tick']
        real_lock = tracker.file_lock
        calls = {'ledger': 0}
        def file_lock(where, name, blocking=True):
            if name == 'ledger.lock':
                calls['ledger'] += 1
                if calls['ledger'] in (2, 3):          # another program holds it: Windows gives up with Errno 36
                    raise OSError(36, 'Resource deadlock avoided')
            return real_lock(where, name, blocking)
        class Stopper(threading.Event):
            waits = 0
            def wait(self, timeout=None):
                type(self).waits += 1
                return type(self).waits > 5
        with patch.object(tracker, 'file_lock', side_effect=file_lock), patch.object(tracker, 'awake_clock', side_effect=awake_clock), \
                patch.object(tracker, 'boot_id', return_value='boot-a'), patch.object(tracker.threading, 'Event', Stopper), \
                patch.object(tracker, 'read_power', return_value=ValueError('no log in tests')), \
                patch('lifetime_daily.boot_history', return_value=(None, [], True)):
            tracker.run(directory)
        ledger = tracker.read_ledger(directory)
        self.assertEqual(ledger['total_seconds'], clock['tick'])       # every second of the awake clock is counted
        self.assertFalse(ledger.get('coverage_gaps'))


class WrongClockTests(unittest.TestCase):
    """Review finding 4: a boot logged with a reset clock (2018) must not wipe saved days or move where data starts."""
    def test_a_reset_clock_boot_does_not_replace_saved_days(self):
        saved = {'2026-03-02': entry(asleep_s=30000, off_s=20000, restarts=1, shutdowns=2)}
        ledger = dict(daily={'2026-03-02': 36400}, downtime_daily=dict(saved), downtime_sources=dict(log_from='2026-01-01'))
        # In the order logged: a normal boot, then one whose start was logged with the clock reset to 2018.
        boots = [dict(start=at('2026-09-01T08:00'), end=at('2026-09-09T18:00'), events=[], clean=True),
                 dict(start=at('2018-01-01T00:00'), end=at('2026-09-10T18:00'), events=[], clean=True),
                 dict(start=at('2026-09-11T08:00'), end=at('2026-11-20T12:00'), events=[], clean=False)]
        now = at('2026-11-20T12:00')
        result = downtime.from_boots(boots, {}, now, TZ)
        downtime.apply(ledger, result, downtime.sources_for(result, 'windows-log', now=now, tz=TZ), now, TZ)
        self.assertEqual(ledger['downtime_daily']['2026-03-02'], saved['2026-03-02'])
        self.assertEqual(min(k for k in ledger['downtime_daily'] if k > '2026-03-02'), '2026-09-01')
        self.assertFalse(any('2018' <= k < '2026' for k in ledger['downtime_daily']))
        self.assertEqual(ledger['downtime_sources']['log_from'], '2026-01-01')
        self.assertEqual(downtime.sources_for(result, 'windows-log', now=now, tz=TZ)['log_from'], '2026-09-01')
        self.assertEqual(result['days']['2026-09-10']['shutdowns'], 1)       # its clean shutdown still counts
        self.assertEqual(result['days']['2026-09-09']['off_s'], 0)          # when it really started is unknown
        self.assertEqual(result['days']['2026-09-10']['off_s'], 6*H)

    def test_a_long_boot_with_no_sleep_is_not_turned_into_off_time(self):
        boots = [boot('2026-08-01T08:00', '2026-08-01T18:00'),
                 boot('2026-08-02T08:00', '2026-10-01T18:00'),          # 60 days on, no sleep logged, shut down cleanly
                 boot('2026-10-02T08:00', '2026-10-02T09:00')]
        result = downtime.from_boots(boots, {}, tz=TZ)
        off = sum(day['off_s'] for day in result['days'].values())
        self.assertEqual(off, 28*H)                              # both nights: a long boot is not a wrong clock
        self.assertEqual(sum(day['shutdowns'] for day in result['days'].values()), 2)

    def test_a_server_running_for_months_keeps_its_start(self):
        now = at('2026-11-20T12:00')
        boots = [dict(start=at('2026-09-01T08:00'), end=at('2026-09-25T18:00'), events=[], clean=True),
                 dict(start=at('2026-09-28T08:00'), end=now, events=[], clean=False)]      # up 53 days, no sleep
        result = downtime.from_boots(boots, dict(boot_time=at('2026-09-28T08:01')), now, TZ)
        self.assertEqual([e['kind'] for e in result['events']], ['start', 'shutdown', 'start'])
        self.assertEqual(result['days']['2026-09-26']['off_s'], 24*H)
        self.assertEqual(downtime.longest_spans(result['events'], at('2026-09-01T00:00'), now)['awake'], now - at('2026-09-28T08:00'))

    def test_the_running_boot_takes_the_system_boot_time_when_its_logged_start_is_wrong(self):
        now = at('2026-11-20T12:00')
        boots = [dict(start=at('2026-11-10T08:00'), end=at('2026-11-10T18:00'), events=[], clean=True),
                 dict(start=at('2018-01-01T00:00'), end=now, events=[], clean=False)]
        result = downtime.from_boots(boots, dict(boot_time=at('2026-11-11T08:00')), now, TZ)
        self.assertEqual(result['days']['2026-11-10']['off_s'], 6*H)
        self.assertEqual(result['days']['2026-11-11']['off_s'], 8*H)
        self.assertEqual(result['start'], at('2026-11-10T08:00'))

    def test_a_day_with_nothing_new_keeps_what_was_seen(self):
        ledger = dict(daily={}, downtime_daily={'2026-11-18': entry(off_s=8*H, unknown_s=16*H, shutdowns=1)})
        result = downtime.from_boots([boot('2026-11-17T08:00', '2026-11-20T12:00')], {}, tz=TZ)
        downtime.apply(ledger, result, now=at('2026-11-20T12:00'), tz=TZ)
        self.assertEqual(ledger['downtime_daily']['2026-11-18']['shutdowns'], 1)


class StopPlacementTests(unittest.TestCase):
    """Review finding 5: an unexpected stop is counted on the day it was found, the next start."""
    def test_stop_is_counted_and_listed_at_the_next_start(self):
        boots = [boot('2026-11-18T08:00', '2026-11-18T10:00', clean=False), boot('2026-11-19T09:00', '2026-11-19T12:00')]
        result = downtime.from_boots(boots, dict(stops=[at('2026-11-19T09:00:20')]), tz=TZ)
        self.assertEqual(result['days']['2026-11-19']['unexpected_stops'], 1)
        self.assertNotIn('unexpected_stops', {k for k, v in result['days'].get('2026-11-18', {}).items() if v})
        kinds = [(e['kind'], e['time']) for e in result['events']]
        self.assertEqual(kinds[1:3], [('unexpected', at('2026-11-19T09:00')), ('start', at('2026-11-19T09:00'))])
        spans = downtime.longest_spans(result['events'], at('2026-11-18T00:00'), at('2026-11-19T12:00'))
        self.assertEqual(spans['awake'], 3*H)       # the unknown night is not counted as awake


class TimeLimitTests(unittest.TestCase):
    """Review finding 8: a hung wevtutil or journalctl must end with an error, never block the hourly read."""
    def test_a_hung_wevtutil_is_stopped(self):
        import time as clock
        original = subprocess.Popen
        def hung(args, **kwargs):
            return original([sys.executable, '-c', 'import sys, time; sys.stdout.write("<Events>"); sys.stdout.flush(); time.sleep(60)'], **kwargs)
        started = clock.monotonic()
        with patch.object(windows.subprocess, 'Popen', side_effect=hung):
            with self.assertRaises(ValueError):
                windows._query_system(windows.new_extras(), windows.power_query(), timeout=1)
        self.assertLess(clock.monotonic() - started, 20)

    def test_a_hung_journalctl_is_stopped(self):
        with patch.object(imp.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['journalctl'], 300)):
            with self.assertRaisesRegex(ValueError, 'did not answer'):
                imp.command(['journalctl', '--list-boots'])
            self.assertIsInstance(tracker.read_power(None, lambda since: imp.command(['journalctl'])), ValueError)


class OutboxSizeTests(unittest.TestCase):
    """Review finding 9: with 10 years of daily totals, one history copy per outbox row filled 16 MB in ~85 records."""
    def test_only_the_newest_row_keeps_a_history_snapshot_and_no_record_is_lost(self):
        import lifetime_agent as agent
        import lifetime_protocol as protocol
        root = Path(tempfile.mkdtemp()) / 'machine'
        with patch.object(agent, 'protect_root'):
            machine = agent.MachineAgent.initialize(root, max_bytes=2 * 1024 * 1024)
        self.addCleanup(machine.close)
        machine.set_policy(dict(history=True, resources=False))
        today = dt.date(2026, 11, 20)
        with machine._transaction() as state:
            state['ledger']['daily'] = {(today - dt.timedelta(days=i)).isoformat(): 3600.0 for i in range(1, 3660)}
        for i in range(60):
            stamp = (dt.datetime(2026, 11, 20, 5, tzinfo=UTC) + dt.timedelta(seconds=30 * i)).isoformat()
            machine.sample(100 + 30 * i, 'boot-a', stamp)
        rows = machine._db.execute('SELECT seq, history IS NOT NULL FROM outbox ORDER BY seq').fetchall()
        self.assertEqual(len(rows), 60)                                  # every record kept (2 MB outbox)
        self.assertEqual([seq for seq, has in rows if has], [rows[-1][0]])
        self.assertEqual(machine._load()['dropped'], 0)
        message = machine.batch()
        protocol.validate_sync(message)
        self.assertEqual(len(message['records']), 60)
        self.assertEqual(len(message['history']['daily']), 3660)         # the newest snapshot travels with the batch


if __name__ == '__main__':
    unittest.main()
