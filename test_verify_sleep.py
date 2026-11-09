"""Verify must count suspend-then-hibernate: the hibernate phase can last days and the kernel suspend messages miss it."""
import json
import unittest
from unittest.mock import patch

import lifetime_signals as s


def row(real, mono, kind):
    mid = s.SLEEP_START_ID if kind == 'start' else s.SLEEP_END_ID
    return json.dumps({'__REALTIME_TIMESTAMP': str(int(real * 1e6)), '__MONOTONIC_TIMESTAMP': str(int(mono * 1e6)),
                       'MESSAGE_ID': mid})


class SystemdSleepTests(unittest.TestCase):
    def test_suspend_then_hibernate_counts_every_phase(self):
        text = '\n'.join([
            row(1000, 100, 'start'), row(1600, 102, 'end'),            # suspend: 600 s real, 2 s monotonic -> 598 asleep
            row(1608, 110, 'start'), row(1700, 126, 'end'),            # hibernate 1: 92 - 16 -> 76 asleep
            row(2000, 400, 'start'), row(2000 + 217969, 416, 'end'),   # 60 hours of hibernate -> 217953 asleep
        ])
        self.assertAlmostEqual(s.parse_systemd_sleep(text), 598 + 76 + 217953)

    def test_no_records_or_unfinished_operation_is_unknown(self):
        self.assertIsNone(s.parse_systemd_sleep(''))
        self.assertIsNone(s.parse_systemd_sleep(row(1000, 100, 'start')))
        self.assertIsNone(s.parse_systemd_sleep('not json\n'))

    def test_verify_uses_hibernate_time(self):
        out = '\n'.join([row(1000, 100, 'start'), row(1000 + 10000, 130, 'end')])
        with patch.object(s, 'run_command', return_value=(0, out)), \
             patch.object(s, 'probe_clocks', return_value=dict(uptime=10100.0, awake=130.0)):
            result = s.verify_linux_awake()
        self.assertAlmostEqual(result['event_seconds'], 10100 - (10000 - 30))
        self.assertLess(result['difference'], 1)


if __name__ == '__main__':
    unittest.main()
