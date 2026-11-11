"""Machine accounting uses real temporary SQLite; only Windows ACLs are mocked."""
import copy
import errno
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
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

    def test_runtime_sync_remains_responsive_during_slow_sample(self):
        stop = threading.Event()
        client = Mock()
        synced = threading.Event()
        client.step.side_effect = lambda **kwargs: synced.set()
        def slow_sample():
            self.assertTrue(synced.wait(2), 'Sync was blocked by sampling')
            stop.set()
        with patch.object(self.machine, 'sample', side_effect=slow_sample) as sample:
            self.machine.run(stop, client)
        sample.assert_called_once()
        client.step.assert_called()

    def test_default_public_status_is_outside_private_root_with_read_only_user_grants(self):
        with patch.object(agent, 'machine_directory', return_value=self.root):
            if sys.platform == 'win32':
                acl = self.enterContext(patch.object(agent, '_windows_acl'))
            self.machine.write_status()
            public = agent.status_path()
        self.assertEqual(public.parent, self.root.parent)
        self.assertEqual(set(json.loads(public.read_text())), {'enabled', 'server', 'status', 'last_sync', 'pending', 'dropped', 'policy', 'server_name', 'phase', 'reported'})
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
        self.assertEqual(set(status), {'enabled', 'server', 'status', 'last_sync', 'pending', 'dropped', 'policy', 'server_name', 'phase', 'reported'})
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
        busy = OSError(13, 'sharing violation') if sys.platform == 'win32' else OSError(errno.EAGAIN, 'resource busy')
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


class ReplaceRunningAgentTests(AgentFixture):
    def test_a_new_connect_makes_the_old_reporter_leave_and_take_its_place(self):
        client = Mock()
        old = threading.Thread(target=self.machine.run, args=(threading.Event(), client), daemon=True)
        old.start()
        deadline = __import__('time').monotonic()+5
        while not (self.root/'agent.lock').exists() and __import__('time').monotonic() < deadline:
            __import__('time').sleep(0.05)
        self.assertTrue(agent.replace_running_agent(self.root, timeout=15))
        old.join(10)
        self.assertFalse(old.is_alive())
        self.assertFalse((self.root/agent.STOP_MARKER).exists())

    def test_nothing_running_returns_at_once(self):
        self.assertTrue(agent.replace_running_agent(self.root, timeout=2))


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


class PowerAndResourcesTests(AgentFixture):
    def test_all_probes_can_fail_without_breaking_sampling(self):
        import lifetime_signals as signals
        def broken():
            raise RuntimeError('unavailable')
        for name in ('probe_cpu', 'probe_ram', 'probe_disks', 'probe_gpu', 'probe_battery',
                     'probe_power', 'probe_network', 'probe_wifi', 'probe_identity', 'probe_counters', 'probe_hardware'):
            self.enterContext(patch.object(signals, name, new=broken))
        self.machine.set_policy(dict(history=False, resources=True))
        self.sample()
        readings = self.machine.batch()['readings']
        self.assertIsNone(readings['specs']['cpu_name'])
        self.assertTrue(all(v is None for v in readings['performance'].values()))

    def test_execute_power_only_fake_runner(self):
        runner = Mock()
        with patch.object(agent.sys, 'platform', 'win32'):
            for action, flag in (('restart', '/r'), ('shutdown', '/s')):
                agent.execute_power(action, runner)
                args = runner.call_args.args[0]
                self.assertEqual(args[:5], ['shutdown', flag, '/t', '60', '/c'])
                self.assertIn('Save your work.', args[5])
            agent.execute_power('sleep', runner)
            self.assertEqual(runner.call_args.args[0], ['rundll32.exe', 'powrprof.dll,SetSuspendState', '0,1,0'])
        with patch.object(agent.sys, 'platform', 'linux'), patch.object(agent.shutil, 'which', return_value='/sbin/shutdown'):
            for action, args in (('restart', ['shutdown', '-r', '+1']), ('shutdown', ['shutdown', '-h', '+1']), ('sleep', ['systemctl', 'suspend'])):
                agent.execute_power(action, runner)
                self.assertEqual(runner.call_args.args[0], args)
        with self.assertRaises(ValueError):
            agent.execute_power('unknown', runner)

    def test_control_consent_is_independent_and_defaults_off(self):
        self.assertFalse(self.machine._load()['policy'].get('control', False))
        self.machine.set_control_consent(True)
        self.assertTrue(self.machine._load()['policy']['control'])
        self.assertFalse(self.machine._load()['policy']['resources'])
        self.machine.set_health_consent(True)
        self.assertTrue(self.machine._load()['policy']['control'])
        self.machine.set_control_consent(False)
        self.assertTrue(self.machine._load()['policy']['health'])
        with self.assertRaises(ValueError):
            self.machine.set_control_consent(1)

    def test_specs_cache_live_performance_and_safe_probe_failure(self):
        import lifetime_signals as signals
        data = dict(probe_cpu=(20, 100), probe_ram=dict(total=1000, used=300),
                    probe_disks=[dict(path=(os.environ.get('SystemDrive', 'C:') + chr(92) if sys.platform == 'win32' else '/'), total=2000, used=500)],
                    probe_gpu=[dict(name='GPU', temperature=55)],
                    probe_battery=dict(wear=20, design_wh=50, watts=10),
                    probe_power=dict(percent=60, state='Charging'),
                    probe_network=[dict(InterfaceDescription='Wi-Fi')], probe_wifi=[dict(signal=80)],
                    probe_identity=dict(cpu='CPU'), probe_counters={},
                    probe_hardware=dict(cpu_cores=4, cpu_threads=8, mac_addresses=['AA:BB:CC:DD:EE:FF']))
        calls = []
        def fake(name):
            def collect():
                calls.append(name)
                return data[name]
            return collect
        for name in data:
            self.enterContext(patch.object(signals, name, new=fake(name)))
        self.machine.set_policy(dict(history=False, resources=True))
        with patch.object(agent.time, 'time', return_value=100000):
            self.sample()
        self.reopen()
        data['probe_cpu'] = (30, 200)
        data['probe_ram']['used'] = 400
        data['probe_power']['state'] = 'Discharging'
        data['probe_identity']['cpu'] = 'NEW CPU'
        with patch.object(agent.time, 'time', return_value=100001):
            self.sample(110)
        readings = self.machine.batch()['readings']
        self.assertEqual(readings['specs']['cpu_name'], 'CPU')
        self.assertEqual(readings['performance']['cpu_percent'], 90)
        self.assertEqual(readings['performance']['ram_used_bytes'], 400)
        self.assertEqual(readings['performance']['disk_free_bytes'], 1500)
        self.assertEqual(readings['performance']['network_kind'], 'wifi')
        self.assertFalse(readings['performance']['charging'])
        self.assertEqual(calls.count('probe_hardware'), 1)
        with patch.object(agent.time, 'time', return_value=121600):
            self.sample(120)
        self.assertEqual(self.machine.batch()['readings']['specs']['cpu_name'], 'NEW CPU')
        self.assertEqual(calls.count('probe_hardware'), 2)
        def broken():
            raise OSError('probe unavailable')
        with patch.object(signals, 'probe_ram', new=broken), patch.object(agent.time, 'time', return_value=121601):
            self.sample(130)
        self.assertIsNone(self.machine.batch()['readings']['performance']['ram_used_bytes'])
        with self.machine._transaction() as state:
            state['battery_capacity'] = dict(health=91, design=62, unit='Wh')
        data['probe_battery']['watts'] = 0.0
        with patch.object(agent.time, 'time', return_value=121602), patch('lifetime_health.probe_battery', side_effect=AssertionError('Cached capacity must not reprobe')):
            self.sample(140)
        reading = self.machine.batch()['readings']
        self.assertEqual(reading['performance']['battery_health_percent'], 91)
        self.assertEqual(reading['specs']['battery_design_wh'], 62)
        self.assertIsNone(reading['performance']['power_watts'])
        self.machine.set_policy(dict(history=False, resources=False))
        self.assertNotIn('specs', self.machine._load())
        self.assertNotIn('readings', self.machine.batch())



class HardwareNameTests(unittest.TestCase):
    def test_windows_server_is_named_as_people_say_it(self):
        self.assertEqual(agent.os_names('Windows', '2025Server'), ('Windows Server', '2025'))
        self.assertEqual(agent.os_names('Windows', '2012ServerR2'), ('Windows Server', '2012 R2'))
        self.assertEqual(agent.os_names('Windows', '11'), ('Windows', '11'))
        self.assertEqual(agent.os_names('Linux', '6.8.0'), ('Linux', '6.8.0'))
        self.assertEqual(agent.os_names(None, None), (None, None))

    def test_identical_processors_are_named_once(self):
        import lifetime_signals as signals
        name = 'Intel(R) Core(TM) Ultra 9 275HX'
        self.assertEqual(signals.unique_names(name + ', ' + name), name)
        self.assertEqual(signals.unique_names(name), name)
        self.assertEqual(signals.unique_names('Xeon A, Xeon B, Xeon A'), 'Xeon A, Xeon B')   # different ones stay, in order
        self.assertEqual(signals.unique_names(''), '')


class ConnectionCliTests(unittest.TestCase):
    def test_policy_display_name_and_detached_background_reach_agent(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = Path(root)/'connect.txt'
            bundle.write_text('verified material', encoding='utf-8')
            machine = Mock()
            machine.status.return_value = dict(enabled=True)
            with patch.object(agent.MachineAgent, 'initialize', return_value=machine), patch('lifetime_tls.decode_bundle'), patch('lifetime_client.SyncClient') as client, patch.object(agent.subprocess, 'Popen') as spawn:
                agent.main(['--directory', root, '--enroll-bundle-file', str(bundle), '--policy-history', '--policy-resources', '--policy-health', '--policy-control', '--display-name', 'Office PC', '--background'])
            policy = dict(history=True, resources=True, health=True, control=True)
            machine.set_policy.assert_called_once_with(policy)
            client.return_value.start_enrollment.assert_called_once_with('verified material', dict(display_name='Office PC'), policy)
            self.assertEqual(spawn.call_args.args[0][-3:], ['--directory', root, '--run'])
            self.assertNotIn('--enroll-bundle-file', spawn.call_args.args[0])
            machine.close.assert_called_once()

    def test_packaged_background_agent_gets_its_own_extraction_folder(self):
        # The one-file program starts a copy of itself for --background. Without this variable the copy shares the
        # first one's temporary folder, which the first deletes on exit; the copy then fails on every file it loads
        # later and the computer sits at "unreachable" after it has been approved.
        with tempfile.TemporaryDirectory() as root:
            machine = Mock()
            with patch.object(agent, 'MachineAgent', return_value=machine), patch.object(agent.subprocess, 'Popen') as spawn, \
                    patch.object(agent.sys, 'frozen', True, create=True):
                agent.main(['--directory', root, '--background'])
            self.assertEqual(spawn.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_policy_changes_and_disconnect_use_agent_api(self):
        machine = Mock()
        with patch.object(agent, 'MachineAgent', return_value=machine):
            agent.main(['--directory', '.', '--set-policy', '--policy-history'])
            machine.set_policy.assert_called_once_with(dict(history=True, resources=False, health=False, control=False))
            agent.main(['--directory', '.', '--disable-reporting'])
            machine.disable_reporting.assert_called_once()


if __name__ == '__main__':
    unittest.main()
