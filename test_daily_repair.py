"""Deterministic boot allocation and migration tests. Every write is temporary."""
import copy
import datetime as dt
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lifetime_daily as daily
import lifetime_tracker as tracker


def stamp(value):
    return dt.datetime.fromisoformat(value).timestamp()


class DailyTests(unittest.TestCase):
    def test_multi_day_sleep_events_and_scaled_credit(self):
        start, end = stamp('2026-11-01T20:00:00'), stamp('2026-11-04T02:00:00')
        events = [(stamp('2026-11-01T22:00:00'), 'sleep'),
                  (stamp('2026-11-02T08:00:00'), 'wake'),
                  (stamp('2026-11-02T23:00:00'), 'sleep'),
                  (stamp('2026-11-03T09:00:00'), 'wake')]
        spans = daily.awake_intervals(start, end, events)
        credit = sum(b-a for a, b in spans)*.9
        days, hours, dated = {}, [0.0]*24, {}
        provenance = daily.distribute(credit, end, days, hours, (start, spans, False), dated=dated)
        self.assertFalse(provenance['estimated'])
        self.assertEqual(days['2026-11-01'], 2*3600*.9)
        self.assertEqual(days['2026-11-02'], 15*3600*.9)
        self.assertEqual(days['2026-11-03'], 15*3600*.9)
        self.assertEqual(days['2026-11-04'], 2*3600*.9)
        self.assertAlmostEqual(math.fsum(days.values()), credit)
        self.assertAlmostEqual(math.fsum(hours), credit)
        self.assertTrue(all(v <= 86400 for v in days.values()))
        self.assertEqual(set(days), set(dated))

    def test_midnight_live_delta_splits_both_days_and_hours(self):
        ledger = tracker.new_ledger()
        end = stamp('2026-11-05T23:59:50')
        tracker.sample(ledger, 10, 'a', '2026-11-05T23:59:50', boot_evidence=(end-10, [(end-10, end)], False))
        tracker.sample(ledger, 40, 'a', '2026-11-06T00:00:20')
        self.assertEqual(ledger['daily'], {'2026-11-05': 20, '2026-11-06': 20})
        self.assertEqual(ledger['hourly'][23], 20)
        self.assertEqual(ledger['hourly'][0], 20)

    def test_initial_tracker_credit_uses_boot_evidence(self):
        start, end = stamp('2026-10-16T15:00:00'), stamp('2026-11-06T19:00:00')
        spans = [(start, start+3600), (end-7200, end)]
        ledger = tracker.new_ledger()
        tracker.sample(ledger, 10800, 'a', '2026-11-06T19:00:00', boot_evidence=(start, spans, False))
        self.assertEqual(ledger['daily'], {'2026-10-16': 3600, '2026-11-06': 7200})
        self.assertEqual(sum(ledger['hourly']), 10800)
        self.assertEqual(ledger['total_seconds'], 10800)

    def test_no_logs_even_wall_time_fallback(self):
        start, end = stamp('2026-10-16T15:00:00'), stamp('2026-11-06T19:00:00')
        days, hours = {}, [0.0]*24
        result = daily.distribute(153*3600, end, days, hours, (start, [], True))
        self.assertTrue(result['estimated'])
        self.assertAlmostEqual(days['2026-10-17'], days['2026-10-18'])
        self.assertAlmostEqual(sum(days.values()), 153*3600)
        self.assertAlmostEqual(sum(hours), 153*3600)
        self.assertLessEqual(max(days.values()), 86400)

    def test_scaling_that_overfills_a_day_uses_capped_estimate(self):
        start, end = stamp('2026-11-01T00:00:00'), stamp('2026-11-04T00:00:00')
        days, hours = {'2026-11-01': 86000}, [0.0]*24
        result = daily.distribute(30*3600, end, days, hours, (start, [(start, start+86400)], False))
        self.assertTrue(result['estimated'])
        self.assertLessEqual(max(days.values()), 86400)
        self.assertAlmostEqual(sum(days.values()), 86000+30*3600)

    def test_unfinished_sleep_and_duplicate_events(self):
        self.assertEqual(daily.awake_intervals(0, 100, [(20, 'sleep'), (21, 'sleep'), (50, 'wake'), (51, 'wake'), (90, 'sleep')]), [(0, 20), (50, 90)])

    def test_scaled_full_days_preserve_logged_pattern(self):
        start, end = stamp('2026-11-01T00:00:00'), stamp('2026-11-04T00:00:00')
        days, hours = {}, [0.0]*24
        spans = [(start, start+86400), (start+2*86400, start+2*86400+3600)]
        result = daily.distribute(90030, end, days, hours, (start, spans, False))
        self.assertEqual(result['source'], 'logged-intervals')
        self.assertEqual(days['2026-11-01'], 86400)
        self.assertEqual(days['2026-11-03'], 3630)
        self.assertNotIn('2026-11-02', days)
        self.assertAlmostEqual(sum(hours), 90030)

    def test_windows_native_boot_time_and_event_provider_filter(self):
        data = dict(boot=100, covered=True, events=[
            dict(time=120, id=42, provider='Microsoft-Windows-Kernel-Power'),
            dict(time=125, id=107, provider='Microsoft-Windows-Kernel-Power'),
            dict(time=130, id=27, provider='Other'),
            dict(time=150, id=27, provider='Microsoft-Windows-Kernel-Boot')])
        with patch.object(daily.sys, 'platform', 'win32'), \
                patch('lifetime_import_windows._native_boot_time', return_value=100), \
                patch('lifetime_signals.powershell', return_value=json.dumps(data)) as query:
            self.assertEqual(daily.boot_history(200), (100, [(100, 120), (150, 200)], False))
            self.assertNotIn('Get-CimInstance', query.call_args.args[0])
            data['covered'] = False
            query.return_value = json.dumps(data)
            self.assertEqual(daily.boot_history(200), (100, [(100, 200)], True))

    def test_linux_journal_realtime_monotonic_pairs(self):
        raw = '\n'.join(json.dumps(dict(__REALTIME_TIMESTAMP=str(wall*1000000),
                                        __MONOTONIC_TIMESTAMP=str(mono*1000000)))
                        for wall, mono in [(100, 0), (200, 100), (400, 200)])
        with patch.object(daily.sys, 'platform', 'linux'), patch.object(daily.time, 'time', return_value=500), \
                patch.object(Path, 'read_text', return_value='400 0'), \
                patch('lifetime_signals.command', return_value=raw):
            self.assertEqual(daily.boot_history(500), (100, [(100, 300), (400, 500)], False))

    def legacy(self):
        ledger = tracker.new_ledger()
        ledger.update(total_seconds=153*3600+60, baseline_seconds=7200, boots=2,
                      daily={'2026-11-06': 153*3600+60}, hourly=[60]+[0]*23,
                      sessions=[dict(start='2026-11-06T19:00:00', seconds=153*3600+60)],
                      unattributed_seconds=153*3600)
        return ledger

    def test_repair_totals_backup_idempotence_and_import_preservation(self):
        before = self.legacy()
        before['imported'] = {'daily': {'2026-06-01': 3600}, 'hourly': [150]*24}
        before['daily']['2026-06-01'] = 3600
        before['total_seconds'] += 3600
        before['hourly'] = [v+150 for v in before['hourly']]
        start, end = stamp('2026-10-16T15:00:00'), stamp('2026-11-06T19:00:00')
        evidence = (start, [(start, end)], True)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'ledger.json'
            tracker.atomic_json(path, before)
            raw = path.read_bytes()
            tracker.atomic_json(Path(root)/'status.json', {'heartbeat': 1})
            after = tracker.repair_daily(root, evidence)
            for field in ('total_seconds', 'baseline_seconds', 'boots', 'sessions', 'imported',
                          'current_session_seconds', 'longest_session_seconds', 'last_clock'):
                self.assertEqual(after[field], before[field], field)
            self.assertLessEqual(max(after['daily'].values()), 86400)
            self.assertAlmostEqual(sum(after['daily'].values()), sum(before['daily'].values()))
            self.assertAlmostEqual(sum(after['hourly']), before['total_seconds'])
            self.assertEqual((Path(root)/'ledger.json.bak-before-daily-repair').read_bytes(), raw)
            repaired_bytes = path.read_bytes()
            self.assertEqual(tracker.repair_daily(root, evidence), after)
            self.assertEqual(path.read_bytes(), repaired_bytes)
            self.assertEqual(json.loads((Path(root)/'status.json').read_text()), {'heartbeat': 1})

    def test_disabled_history_credit_is_not_redistributed(self):
        ledger = tracker.new_ledger()
        tracker.sample(ledger, 100, 'a', '2026-11-06T12:00:00', history=False)
        tracker.sample(ledger, 130, 'a', '2026-11-06T12:00:30', history=True)
        with tempfile.TemporaryDirectory() as root:
            tracker.atomic_json(Path(root)/'ledger.json', ledger)
            after = tracker.repair_daily(root)
            self.assertEqual(after['daily'], ledger['daily'])
            self.assertEqual(after['hourly'], ledger['hourly'])
            self.assertEqual(after['unattributed_seconds'], 100)

    def test_repair_failure_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'ledger.json'
            tracker.atomic_json(path, self.legacy())
            raw = path.read_bytes()
            end = stamp('2026-11-06T19:00:00')
            with self.assertRaises(ValueError):
                tracker.repair_daily(root, (end-10, [(end-10, end)], False))
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse((Path(root)/'ledger.json.bak-before-daily-repair').exists())


if __name__ == '__main__':
    unittest.main()
