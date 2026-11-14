"""Counter epochs, conservative overlap handling, and durable accounting."""
import copy
import datetime as dt
import json
import sys
import tempfile
import types
import unittest
import test_compat  # enterContext on Python 3.10
from pathlib import Path
from unittest.mock import patch

import lifetime_agent as agent
import lifetime_ledger as schema
import lifetime_tracker as tracker

GUID = '5ac1eb30c2dcf111b6f6a0617c0d1720'
START = dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc)


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.ledger = tracker.new_ledger()
        self.order = 0
        self.history = self.enterContext(patch('lifetime_daily.boot_history', return_value=(None, [], True)))

    def sample(self, tick, identity='boot-a', **options):
        self.order += 1
        stamp = (START + dt.timedelta(seconds=self.order * 30)).isoformat()
        tracker.sample(self.ledger, tick, identity, stamp, **options)
        schema.validate(self.ledger)
        return self.ledger

    def test_rollback_does_not_recount_overlap_or_credit_again(self):
        self.sample(100)
        self.sample(50, credit_current=True)
        self.sample(80, credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 100)
        self.sample(110, credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 110)
        self.assertEqual(self.ledger['boot_accounted_seconds'], 110)
        self.assertEqual(self.ledger['current_session_seconds'], 10)
        self.assertEqual(self.ledger['longest_session_seconds'], 100)
        self.assertEqual(self.ledger['boots'], 1)
        self.assertEqual(self.ledger['awake_clock_rollbacks'], 1)
        self.assertEqual(sum(self.ledger['daily'].values()), 110)
        self.assertEqual(sum(s['seconds'] for s in self.ledger['sessions']), 110)
        self.assertEqual(self.ledger['coverage_gaps'][-1]['reason'], 'awake_clock_rollback')

    def test_multiple_rollbacks_and_desktop_restart_preserve_watermark(self):
        self.sample(100)
        self.sample(50)
        with tempfile.TemporaryDirectory() as root:
            tracker.save(root, self.ledger)
            self.ledger = tracker.read_ledger(root)
            self.sample(80, credit_current=True)
            self.sample(40)
            tracker.save(root, self.ledger)
            self.ledger = tracker.read_ledger(root)
            self.sample(90)
            self.assertEqual(self.ledger['total_seconds'], 100)
            self.sample(120)
        self.assertEqual(self.ledger['total_seconds'], 120)
        self.assertEqual(self.ledger['awake_clock_rollbacks'], 2)

    def test_rollback_with_history_disabled_is_still_durable(self):
        for tick in (100, 50, 80, 110):
            self.sample(tick, history=False)
        self.assertEqual(self.ledger['total_seconds'], 110)
        self.assertEqual(self.ledger['awake_clock_high_water'], 110)
        self.assertEqual(self.ledger['awake_clock_rollbacks'], 1)
        self.assertEqual(self.ledger['daily'], {})

    def test_real_reboot_resets_watermark_and_credits_new_boot_once(self):
        self.sample(100)
        self.sample(50)
        self.sample(20, 'boot-b')
        self.sample(20, 'boot-b', credit_current=True)
        self.sample(50, 'boot-b')
        self.assertEqual(self.ledger['total_seconds'], 150)
        self.assertEqual(self.ledger['boots'], 2)
        self.assertEqual(self.ledger['current_session_seconds'], 50)
        self.assertEqual(self.ledger['boot_accounted_seconds'], 50)
        self.assertEqual(self.ledger['awake_clock_high_water'], 50)

    def test_sleep_resume_wall_time_and_tracker_restart_add_no_sleep(self):
        self.sample(100)
        tracker.sample(self.ledger, 100, 'boot-a', (START + dt.timedelta(hours=8)).isoformat())
        self.ledger = tracker.validate_ledger(json.loads(json.dumps(self.ledger)))
        tracker.sample(self.ledger, 130, 'boot-a', (START + dt.timedelta(hours=8, seconds=30)).isoformat(), credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 130)
        self.assertEqual(self.ledger['boots'], 1)

    def test_forward_gap_and_recredit_never_restore_rejected_seconds(self):
        for tick in (100, 1000, 1030, 500, 530, 1040):
            self.sample(tick, credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 130)
        self.sample(1070)
        self.assertEqual(self.ledger['total_seconds'], 160)

    def test_legacy_inconsistent_accounted_total_is_preserved_without_inventing_a_watermark(self):
        self.sample(8309)
        self.ledger.update(total_seconds=70436, boot_accounted_seconds=70436)
        self.ledger.pop('awake_clock_high_water')
        old_daily = copy.deepcopy(self.ledger['daily'])
        self.sample(8339)
        self.assertEqual(self.ledger['total_seconds'], 70466)
        self.assertEqual(self.ledger['boot_accounted_seconds'], 70466)
        self.assertEqual(self.ledger['awake_clock_high_water'], 8339)
        self.assertEqual(sum(self.ledger['daily'].values()) - sum(old_daily.values()), 30)

    def test_windows_sequence_upgrade_does_not_credit_a_boot_twice(self):
        self.sample(100, GUID)
        self.sample(130, GUID + ':windows-boot:8', credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 130)
        self.assertEqual(self.ledger['boots'], 1)
        self.sample(160, GUID)  # temporarily unreadable registry
        self.assertEqual(self.ledger['boot_id'], GUID + ':windows-boot:8')
        self.sample(20, GUID + ':windows-boot:9')
        self.assertEqual(self.ledger['total_seconds'], 180)
        self.assertEqual(self.ledger['boots'], 2)
        self.sample(20, GUID + ':windows-boot:9', credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 180)

    def test_legacy_windows_upgrade_with_lower_counter_remains_ambiguous(self):
        self.sample(100, GUID)
        self.sample(50, GUID + ':windows-boot:8', credit_current=True)
        self.sample(80, GUID + ':windows-boot:8', credit_current=True)
        self.assertEqual(self.ledger['total_seconds'], 100)
        self.assertEqual(self.ledger['boots'], 1)

    def test_invalid_watermark_cannot_be_salvaged_as_optional_history(self):
        self.sample(100)
        for invalid in (-1, float('nan'), True, '100'):
            value = dict(self.ledger, awake_clock_high_water=invalid)
            with self.assertRaises(ValueError):
                schema.validate(value)
            with self.assertRaises(ValueError):
                schema.salvage(value, tracker.new_ledger())


class WindowsIdentityTests(unittest.TestCase):
    def test_registry_boot_sequence_and_unavailable_fallback(self):
        registry = types.SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_READ=2, KEY_WOW64_64KEY=4, REG_DWORD=4)
        from unittest.mock import MagicMock
        registry.OpenKey = MagicMock()
        registry.QueryValueEx = MagicMock(return_value=(8, 4))
        def query(kind, buffer, size, result):
            buffer.raw = bytes.fromhex(GUID) + bytes(16)
            return 0
        dll = types.SimpleNamespace(ntdll=types.SimpleNamespace(NtQuerySystemInformation=query))
        with patch.object(sys, 'platform', 'win32'), patch.object(tracker.ctypes, 'windll', dll, create=True), patch.dict(sys.modules, winreg=registry):
            self.assertEqual(tracker.boot_id(), GUID + ':windows-boot:8')
            registry.QueryValueEx.return_value = (9, 4)
            self.assertEqual(tracker.boot_id(), GUID + ':windows-boot:9')
            registry.OpenKey.side_effect = PermissionError()
            self.assertEqual(tracker.boot_id(), GUID)


class AgentRollbackTests(unittest.TestCase):
    def test_sqlite_restart_and_outbox_use_only_new_counter_tail(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'protect_root'), patch('lifetime_daily.boot_history', return_value=(None, [], True)):
            machine = agent.MachineAgent.initialize(Path(directory) / 'agent')
            try:
                machine.sample(100, 'boot-a', START.isoformat())
                machine.sample(50, 'boot-a', (START + dt.timedelta(seconds=30)).isoformat())
                machine.close()
                machine = agent.MachineAgent(Path(directory) / 'agent')
                repeat = machine.sample(80, 'boot-a', (START + dt.timedelta(seconds=60)).isoformat())
                tail = machine.sample(110, 'boot-a', (START + dt.timedelta(seconds=90)).isoformat())
                self.assertEqual(repeat['awake_seconds'], 0)
                self.assertEqual(tail['awake_seconds'], 10)
                self.assertEqual(tail['totals']['measured_seconds'], 110)
                ledger = machine._load()['ledger']
                self.assertEqual(ledger['awake_clock_high_water'], 110)
                self.assertEqual(ledger['awake_clock_rollbacks'], 1)
            finally:
                machine.close()

    def test_explicit_import_preserves_watermark_and_legacy_boot_alias(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'protect_root'), patch('lifetime_daily.boot_history', return_value=(None, [], True)):
            root = Path(directory)
            source = agent._empty_ledger()
            tracker.sample(source, 100, GUID, START.isoformat(), history=False)
            tracker.sample(source, 50, GUID, (START + dt.timedelta(seconds=30)).isoformat(), history=False)
            path = root / 'ledger.json'
            path.write_text(json.dumps(source))
            machine = agent.MachineAgent.initialize(root / 'agent')
            try:
                with self.assertRaisesRegex(ValueError, 'ahead'):
                    machine.import_ledger(path, tick=40, boot=GUID + ':windows-boot:8')
                # The existing import guard rejects already inconsistent boot
                # accounting (100 credited, last observed clock only 50).
                with self.assertRaisesRegex(ValueError, 'inconsistent'):
                    machine.import_ledger(path, tick=80, boot=GUID + ':windows-boot:8')
                source.update(last_clock=100, boot_accounted_seconds=100, awake_clock_high_water=150)
                path.write_text(json.dumps(source))
                machine.import_ledger(path, tick=120, boot=GUID + ':windows-boot:8')
                record = machine.sample(120, GUID + ':windows-boot:8', (START + dt.timedelta(seconds=60)).isoformat())
                self.assertEqual(record['awake_seconds'], 0)
                self.assertEqual(record['totals']['measured_seconds'], 100)
                self.assertEqual(machine._load()['ledger']['awake_clock_high_water'], 150)
            finally:
                machine.close()


if __name__ == '__main__':
    unittest.main()
