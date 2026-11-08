"""Machine accounting uses real temporary SQLite; only Windows ACLs are mocked."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import lifetime_agent as agent
import lifetime_permissions as permissions
import lifetime_tracker as tracker

STAMP = '2026-11-07T12:00:00+00:00'


class AgentFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'machine'
        if sys.platform == 'win32':
            self.enterContext(patch.object(agent, 'protect_root'))
        self.machine = agent.MachineAgent.initialize(self.root)
        self.addCleanup(lambda: self.machine.close())

    def reopen(self):
        self.machine.close()
        self.machine = agent.MachineAgent(self.root)

    def sample(self, tick=100, boot='boot-a', **kwargs):
        stamp = (dt.datetime.fromisoformat(STAMP)+dt.timedelta(seconds=tick)).isoformat()
        return self.machine.sample(tick, boot, stamp, **kwargs)

    def source(self, **changes):
        value = agent._empty_ledger()
        value.update(total_seconds=100, baseline_seconds=50, boot_id='boot-a', last_clock=100,
                     boot_accounted_seconds=100, boot_credited_for='boot-a', boots=1,
                     last_update=STAMP, first_seen='2026-01-01', first_seen_source='tracking-start',
                     daily={'2026-11-07': 100}, imported={'seconds': 30, 'daily': {'2026-11-07': 30}})
        value.update(changes)
        path = Path(self.temporary.name) / 'user-ledger.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path


class MachineTests(AgentFixture):
    def test_initialization_is_explicit_and_separate_from_user_data(self):
        with patch.object(agent.tracker, 'data_dir', side_effect=AssertionError('per-user data read')):
            missing = Path(self.temporary.name) / 'missing'
            with self.assertRaises(FileNotFoundError):
                agent.MachineAgent(missing)
            self.assertFalse(missing.exists())
            with self.assertRaises(FileExistsError):
                agent.MachineAgent.initialize(self.root)
        self.assertFalse(self.machine.status()['enabled'])
        with self.machine._lock:
            self.assertEqual(self.machine._load()['policy'], dict(history=False, resources=False))
        self.assertEqual(self.machine._db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.machine._db.execute('PRAGMA synchronous').fetchone()[0], 2)

    def test_uuid_sequence_ledger_and_outbox_survive_restart(self):
        device = self.machine.device_id
        self.sample(100)
        second = self.sample(130)
        self.assertEqual(second['measured_seconds'], 30)
        self.reopen()
        self.assertEqual(self.machine.device_id, device)
        third = self.sample(140)
        self.assertEqual(third['seq'], 3)
        self.assertEqual(self.machine.ledger()['total_seconds'], 140)
        self.assertEqual([r['seq'] for r in self.machine.batch()['records']], [1, 2, 3])

    def test_tracker_reboot_fast_startup_clock_gap_and_backwards_rules(self):
        self.sample(100)
        self.assertEqual(self.sample(110)['awake_seconds'], 10)
        self.assertEqual(self.sample(410)['awake_seconds'], 300)
        self.assertEqual(self.sample(711)['awake_seconds'], 0)
        self.assertEqual(self.sample(700)['awake_seconds'], 0)
        self.assertEqual(self.sample(20, 'boot-b')['awake_seconds'], 20)
        self.assertEqual(self.sample(25, 'boot-b')['awake_seconds'], 5)
        self.assertEqual(self.machine.ledger()['boots'], 2)

    def test_disabled_samplers_and_os_estimates_are_never_called(self):
        sampler = Mock(side_effect=AssertionError('disabled optional sampler called'))
        with patch.object(tracker, 'estimate_first_seen', sampler):
            self.sample(history_sampler=sampler, resources_sampler=sampler)
        sampler.assert_not_called()
        self.assertNotIn('history', self.machine.batch())
        self.assertNotIn('readings', self.machine.batch())
        self.assertEqual(self.machine.ledger()['daily'], {})
        self.assertIsNone(self.machine.ledger()['first_seen'])

    def test_transaction_failure_rolls_back_ledger_sequence_and_outbox(self):
        original = self.machine.ledger()
        self.machine._db.execute("CREATE TRIGGER full BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'disk full'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.sample()
        self.assertEqual(self.machine.ledger(), original)
        self.assertEqual(self.machine.status()['pending'], 0)
        self.machine._db.execute('DROP TRIGGER full')
        self.assertEqual(self.sample()['seq'], 1)

    def test_queue_drops_oldest_durably_without_losing_stock(self):
        with self.machine._transaction() as state:
            state.update(max_records=2)
        self.sample(100)
        self.sample(110)
        self.sample(120)
        self.reopen()
        self.assertEqual(self.machine.status()['dropped'], 1)
        self.assertEqual([r['seq'] for r in self.machine.batch()['records']], [2, 3])
        self.assertEqual(self.machine.batch()['records'][-1]['totals']['measured_seconds'], 120)
        self.assertEqual(self.machine.ledger()['total_seconds'], 120)
        with self.machine._transaction() as state:
            state['max_bytes'] = 1
        self.sample(130)
        self.assertEqual(self.machine.status()['pending'], 0)
        self.assertEqual(self.machine.status()['dropped'], 4)
        self.assertEqual(self.machine.ledger()['total_seconds'], 130)

    def test_policy_revocation_scrubs_snapshots_preserves_immutable_core(self):
        self.machine.set_policy(dict(history=True, resources=True))
        resource = Mock(return_value={'system': 'OPTIONAL-SECRET-MARKER'})
        end = dt.datetime.fromisoformat(STAMP).timestamp()+100
        self.sample(100, resources_sampler=resource, boot_evidence=(end-100, [(end-100, end)], False))
        before = self.machine.batch()['records']
        self.assertIn('history', self.machine.batch())
        self.assertIn('readings', self.machine.batch())
        with self.machine._lock:
            version = self.machine._load()['policy_version']
        self.machine.set_policy(dict(history=False, resources=False))
        self.assertEqual(self.machine.batch()['records'], before)
        self.assertNotIn('history', self.machine.batch())
        self.assertNotIn('readings', self.machine.batch())
        self.assertEqual(self.machine.ledger()['daily'], {})
        self.assertEqual(self.machine.ledger()['sessions'], [])
        self.assertEqual(self.machine.ledger()['total_seconds'], 100)
        with self.machine._lock:
            self.assertGreater(self.machine._load()['policy_version'], version)
        self.assertNotIn('OPTIONAL-SECRET-MARKER', '\n'.join(self.machine._db.iterdump()))
        self.reopen()
        disabled = Mock(side_effect=AssertionError('revoked sampler'))
        self.sample(110, history_sampler=disabled, resources_sampler=disabled)
        disabled.assert_not_called()

    def test_runtime_holds_a_single_instance_file_lock(self):
        stop = threading.Event()
        stop.set()
        with tracker.file_lock(self.root, 'agent.lock', blocking=False):
            with self.assertRaises(OSError):
                self.machine.run(stop)

    def test_runtime_honors_short_reporting_deadline_without_real_wait(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        client = Mock()
        def scheduled():
            with self.machine._transaction() as state:
                state.update(enabled=True, next_attempt=1005)
        client.step.side_effect = scheduled
        with patch.object(self.machine, 'sample') as sample, patch.object(agent.time, 'time', return_value=1000):
            self.machine.run(stop, client)
        sample.assert_called_once()
        stop.wait.assert_called_once_with(5)

    def test_default_public_status_is_outside_private_root_with_read_only_user_grants(self):
        with patch.object(agent, 'machine_directory', return_value=self.root):
            if sys.platform == 'win32':
                acl = self.enterContext(patch.object(agent, '_windows_acl'))
            self.machine.write_status()
            public = agent.status_path()
        self.assertEqual(public.parent, self.root.parent)
        self.assertEqual(set(json.loads(public.read_text())), {'enabled', 'server', 'status', 'last_sync', 'pending', 'dropped'})
        if sys.platform == 'win32':
            self.assertEqual(acl.call_args_list[0].args[2][-1], '*S-1-5-32-545:(RX)')
            self.assertEqual(acl.call_args_list[1].args[2][-1], '*S-1-5-32-545:(R)')
        else:
            self.assertEqual(public.stat().st_mode & 0o777, 0o644)

    def test_status_is_allowlisted_and_resolver_has_no_side_effects(self):
        missing = Path(self.temporary.name) / 'not-initialized'
        self.assertEqual(agent.status_path(missing), missing / 'status.json')
        self.assertFalse(missing.exists())
        with patch.object(agent, 'machine_directory', return_value=self.root):
            self.assertEqual(agent.status_path(), self.root.parent / ('Agent-status.json' if sys.platform == 'win32'
                                                                    else 'lifetime-agent-status.json'))
            self.assertEqual(agent.status_path(self.root), agent.status_path())
        self.sample()
        status = json.loads(agent.status_path(self.root).read_text())
        self.assertEqual(set(status), {'enabled', 'server', 'status', 'last_sync', 'pending', 'dropped'})
        self.assertNotIn(self.machine.device_id, json.dumps(status))

    def test_disable_reporting_keeps_uuid_stock_and_core_queue(self):
        self.sample()
        device, record = self.machine.device_id, self.machine.batch()['records']
        with patch('lifetime_tls.request_json', side_effect=AssertionError('disable network')):
            status = self.machine.disable_reporting()
        self.assertFalse(status['enabled'])
        self.assertEqual(self.machine.device_id, device)
        self.assertEqual(self.machine.batch()['records'], record)
        self.assertEqual(self.machine.ledger()['total_seconds'], 100)

    def test_reporting_lock_retries_busy_acquisition_until_success(self):
        busy = OSError(13, 'sharing violation')
        (self.root / 'reporting.lock').touch()
        entered = []
        real_lock = tracker.file_lock
        def acquire(*args, **kwargs):
            entered.append(True)
            if len(entered) <= 11:
                raise busy
            return real_lock(*args, **kwargs)
        with patch.object(agent.tracker, 'file_lock', side_effect=acquire), \
                patch.object(agent.time, 'sleep') as sleep:
            with self.machine.reporting_lock():
                pass
        self.assertEqual(len(entered), 12)
        self.assertEqual(sleep.call_count, 11)

    def test_reporting_lock_propagates_nonbusy_acquisition_error(self):
        failure = OSError(5, 'access denied')
        with patch.object(agent.tracker, 'file_lock', side_effect=failure), \
                patch.object(agent.time, 'sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                with self.machine.reporting_lock():
                    self.fail('lock body entered')
        self.assertIs(caught.exception, failure)
        sleep.assert_not_called()

    def test_reporting_lock_does_not_retry_body_oserror(self):
        failure = OSError(13, 'sharing violation')
        with patch.object(agent.time, 'sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                with self.machine.reporting_lock():
                    raise failure
        self.assertIs(caught.exception, failure)
        sleep.assert_not_called()


class ImportTests(AgentFixture):
    def test_explicit_import_preserves_sources_and_same_boot_boundary(self):
        path = self.source()
        original = path.read_bytes()
        self.assertTrue(self.machine.import_ledger(path, boot='boot-a', tick=120, timestamp=STAMP))
        initial = self.machine.batch()['records'][0]
        self.assertEqual(initial['measured_seconds'], 70)
        self.assertEqual(initial['imported_seconds'], 30)
        self.assertEqual(initial['totals']['baseline_seconds'], 50)
        self.assertEqual(self.sample(120)['measured_seconds'], 20)
        self.assertEqual(self.machine.ledger()['total_seconds'], 120)
        self.reopen()
        self.assertFalse(self.machine.import_ledger(path, boot='boot-a', tick=130))
        self.assertEqual(self.machine.ledger()['total_seconds'], 120)
        self.assertEqual(path.read_bytes(), original)

    def test_import_previous_boot_then_credit_new_boot_once(self):
        self.machine.import_ledger(self.source(), boot='boot-b', tick=20, timestamp=STAMP)
        self.assertEqual(self.sample(20, 'boot-b')['measured_seconds'], 20)
        self.assertEqual(self.sample(25, 'boot-b')['measured_seconds'], 5)
        self.assertEqual(self.machine.ledger()['total_seconds'], 125)

    def test_import_rejects_unknown_invalid_and_ahead_boundaries(self):
        cases = [dict(boot_id=None), dict(boot_id='unknown'), dict(boot_id='session:1'),
                 dict(last_clock=None), dict(last_clock=True), dict(last_clock=-1),
                 dict(last_clock=150), dict(last_update=None), dict(boot_accounted_seconds=101),
                 dict(total_seconds=True), dict(imported={'seconds': 101}),
                 dict(boot_credited_for='other-boot')]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.machine.import_ledger(self.source(**change), boot='boot-a', tick=120)
            self.assertEqual(self.machine.status()['pending'], 0)
            self.assertEqual(self.machine.ledger()['total_seconds'], 0)

    def test_different_changed_or_late_imports_are_refused(self):
        path = self.source()
        self.machine.import_ledger(path, boot='boot-a', tick=120)
        other = path.with_name('another-user.json')
        other.write_bytes(path.read_bytes())
        with self.assertRaises(ValueError):
            self.machine.import_ledger(other, boot='boot-a', tick=120)
        self.source(total_seconds=105)
        with self.assertRaises(ValueError):
            self.machine.import_ledger(path, boot='boot-a', tick=120)
        self.assertEqual(self.machine.ledger()['total_seconds'], 100)

    def test_no_import_after_first_machine_sample(self):
        self.sample()
        with self.assertRaises(ValueError):
            self.machine.import_ledger(self.source(), boot='boot-a', tick=120)


class PermissionTests(unittest.TestCase):
    def test_windows_private_grants_and_failure_propagation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'private')
            path.touch()
            with patch.object(permissions.sys, 'platform', 'win32'), patch.object(permissions, 'windows_acl') as acl:
                permissions.protect_private(path)
                self.assertNotIn('BU', acl.call_args.args[1])
                self.assertEqual(acl.call_args.args[2], ['*S-1-5-32-544:(F)', '*S-1-5-18:(F)'])
                acl.side_effect = PermissionError('elevation required')
                with self.assertRaises(PermissionError):
                    permissions.protect_directory(root)

    @unittest.skipIf(sys.platform == 'win32', 'Requires actual POSIX permissions')
    def test_real_posix_modes_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'private')
            path.write_text('secret')
            permissions.protect_directory(root)
            permissions.protect_private(path)
            self.assertEqual(Path(root).stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(root, 'linked')
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                permissions.protect_private(link)


if __name__ == '__main__':
    unittest.main()
