"""History accounting and summaries; all ledger writes use temporary directories."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
import importlib.util
from unittest.mock import patch

import lifetime_tracker as t


class HistoryTests(unittest.TestCase):
    def setUp(self):
        fake = patch('lifetime_daily.boot_history', return_value=(None, [], True))
        fake.start()
        self.addCleanup(fake.stop)

    def test_local_midnight_and_hour(self):
        ledger = t.new_ledger()
        # Naive timestamps are local, independent of the machine's time zone.
        tick = 100
        t.sample(ledger, tick, 'a', '2026-11-05T23:59:50')
        tick += 30
        t.sample(ledger, tick, 'a', '2026-11-06T00:00:20')
        t.sample(ledger, tick, 'a', '2026-11-06T00:00:20', credit_current=True)
        self.assertEqual(ledger['daily'], {'2026-11-05': 110, '2026-11-06': 20})
        self.assertEqual(ledger['hourly'][0], 20)
        self.assertEqual(sum(ledger['hourly']), 130)
        self.assertEqual(ledger['unattributed_seconds'], 0)
        self.assertEqual(sum(ledger['daily'].values()), ledger['total_seconds'])
        self.assertEqual(sum(ledger['hourly'])+ledger['unattributed_seconds'], ledger['total_seconds'])
        self.assertEqual(ledger['sessions'][0]['seconds'], 130)

    def test_old_ledger_does_not_invent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            old = t.new_ledger()
            for key in ('daily', 'hourly', 'sessions', 'history_started', 'unattributed_seconds'):
                old.pop(key)
            old.update(total_seconds=5000, current_session_seconds=100, last_clock=100,
                       boot_id='a', boots=1, boot_credited_for='a', boot_accounted_seconds=100)
            Path(directory, 'ledger.json').write_text(json.dumps(old))
            ledger = t.read_ledger(directory)
            self.assertEqual(ledger['daily'], {})
            t.sample(ledger, 130, 'a', '2026-11-06T12:00:00')
            self.assertEqual(ledger['total_seconds'], 5030)
            self.assertEqual(ledger['daily'], {'2026-11-06': 30})
            self.assertEqual(ledger['sessions'][0]['seconds'], 30)
            self.assertEqual(ledger['history_started'], '2026-11-06')
            t.save(directory, ledger)
            self.assertEqual(t.read_ledger(directory), ledger)

    def test_retention_and_rejected_gap(self):
        ledger = t.new_ledger()
        for index in range(405):
            day = dt.date(2025, 1, 1)+dt.timedelta(days=index)
            t.sample(ledger, 10, str(index), day.isoformat()+'T12:00:00')
        self.assertEqual(len(ledger['daily']), 400)
        self.assertEqual(len(ledger['sessions']), 100)
        total = ledger['total_seconds']
        t.sample(ledger, 1000, '404', day.isoformat()+'T13:00:00')
        self.assertEqual(ledger['total_seconds'], total)
        self.assertEqual(ledger['sessions'][-1]['seconds'], 0)
        t.sample(ledger, 1030, '404', day.isoformat()+'T13:00:30')
        self.assertEqual(ledger['sessions'][-1]['seconds'], 30)
        self.assertEqual(ledger['hourly'][13], 30)

    @unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'PyQt6 is required for GUI summaries')
    def test_summaries(self):
        from lifetime_gui import history_summary
        daily = {'2026-11-01': 3600, '2026-11-02': 7200, '2026-11-04': 3600,
                 '2026-09-01': 3600, '2026-11-03': 0}
        result = history_summary(daily, [{'seconds': 10}, {'seconds': 30}], dt.date(2026, 11, 6))
        self.assertEqual(result['week'], 14400)
        self.assertEqual(result['month'], 14400)
        self.assertEqual(result['streak'], 2)
        self.assertEqual(result['average'], 4500)
        self.assertEqual(result['median'], 20)
        self.assertEqual(result['busiest'][0], dt.date(2026, 11, 2))


if __name__ == '__main__':
    unittest.main()
