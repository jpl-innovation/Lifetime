"""Downtime per day (asleep / off / unknown, restarts, shutdowns, unexpected stops). Synthetic data only."""
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
            self.assertEqual([b['id'] for b in imp.linux_power(since=at('2026-11-18T13:00'))[0]], ['b', 'c'])


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
                patch.object(windows, '_first_live_event', return_value=None), \
                patch.object(windows.subprocess, 'Popen', side_effect=popen):
            boots, extras, info = windows.windows_power()
        self.assertEqual(len(boots), 2)
        self.assertEqual(info, dict(log='windows-log', archives=1, archive_note='read'))
        result = downtime.from_boots(boots, extras, tz=UTC)
        self.assertEqual(result['days']['2026-01-02']['shutdowns'], 1)
        self.assertEqual(downtime.sources_for(result, tz=UTC, **info)['log_from'], '2026-01-02')

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
        self.assertEqual(result['days']['2026-11-03']['unexpected_stops'], 1)      # the crash record


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
        # unknown is re-settled against the day's awake time before sending: 24 h minus 1 h awake, 1 h asleep, 1 h off
        self.assertEqual(next(iter(days.values()))['unknown_s'], 86400 - 3 * 3600)
        self.assertNotIn('downtime_daily', message['history'])         # the original message is unchanged
        # An old server never gets it, even through the existing optional-part filter.
        self.assertNotIn('downtime_daily', protocol.without_extended(add_downtime(message, self.state(), []))['history'])

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


if __name__ == '__main__':
    unittest.main()
