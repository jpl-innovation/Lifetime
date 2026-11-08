"""P3 live contract, native probes, memory bounds and disposable agent delivery."""
import copy
import datetime as dt
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
from unittest.mock import Mock, patch
import uuid

import lifetime_live as live
import lifetime_server_store as storage
from lifetime_protocol import ProtocolError


class ProbeTests(unittest.TestCase):
    def test_native_sample_never_starts_a_process(self):
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('process')):
            sampler = live.Sampler()
            first = sampler.sample({'temperature_c': 48, 'power_watts': 7})
            self.assertIsNone(first['cpu_percent'])
            self.assertEqual(first['temperature_c'], 48)
            self.assertEqual(first['power_watts'], 7)
            self.assertEqual(set(sampler.sample()), set(live.FIELDS))
        self.assertNotIn('subprocess', Path(live.__file__).read_text())

    def test_linux_native_readings(self):
        files = {'/proc/stat': 'cpu 10 0 10 80 0 0 0 0\n',
                 '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 250 kB\n',
                 '/proc/net/dev': 'header\nheader\neth0: 100 0 0 0 0 0 0 0 200 0\n'}
        with patch.object(Path, 'read_text', lambda p, **kw: files[str(p).replace('\\', '/')]), patch.object(Path, 'iterdir', return_value=iter(())):
            sampler = live.Sampler()
            sample = dict.fromkeys(live.FIELDS)
            counter, network = sampler._linux(sample)
            self.assertEqual(counter, (80, 100))
            self.assertEqual(network, {'eth0': (100, 200)})
            self.assertEqual(sample['ram_percent'], 75)
            self.assertIsNone(sample['battery_percent'])

    def test_hostile_protocol(self):
        good = dict(device_id=str(uuid.uuid4()), sample=dict.fromkeys(live.FIELDS))
        live.validate_live(good)
        for field, value in [('cpu_percent', True), ('cpu_percent', 101), ('ram_percent', -1),
                             ('charging', 1), ('disk_free_bytes', 2.5), ('temperature_c', float('nan')),
                             ('power_watts', float('inf')), ('network_rx_bytes_sec', []),
                             ('battery_percent', '90'), ('ram_used_bytes', 10**1000)]:
            bad = copy.deepcopy(good)
            bad['sample'][field] = value
            with self.subTest(field=field, value=str(value)[:20]), self.assertRaises(ProtocolError):
                live.validate_live(bad)
        for bad in [None, [], {}, dict(good, extra=1), dict(good, device_id='x'*5000),
                    dict(good, sample={}), dict(good, device_id=[]), dict(good, sample=dict(good['sample'], extra=None))]:
            with self.subTest(bad=str(bad)[:50]), self.assertRaises(ProtocolError):
                live.validate_live(bad)

    def test_reporter_backoff_gating_and_command_wakeup(self):
        wake = threading.Event()
        reporter = live.Reporter(None, wake)
        state = dict(enabled=True, phase='approved', credential='secret', policy={'resources': True},
                     device_id=str(uuid.uuid4()), bundle={}, readings=None)
        request = Mock(side_effect=OSError('offline'))
        self.assertEqual([reporter.tick(state, request) for _ in range(8)], [1, 2, 4, 8, 16, 30, 30, 30])
        before = copy.deepcopy(state)
        request.side_effect = None
        request.return_value = dict(interval=1, sync_now=True)
        self.assertEqual(reporter.tick(state, request), 1)
        self.assertTrue(wake.is_set())
        self.assertEqual(state, before)
        for changes in [dict(enabled=False), dict(phase='pending'), dict(policy={'resources': False})]:
            request.reset_mock()
            reporter.tick(dict(state, **changes), request)
            request.assert_not_called()


class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.Store(Path(self.tmp.name)/'live.sqlite')
        self.addCleanup(self.store.close)
        request = dict(version=1, request_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                       code=self.store.create_code()['code'], poll_token=secrets.token_urlsafe(32), identity={},
                       policy=dict(history=False, resources=True, control=True))
        self.store.enroll(request)
        self.store.approve(request['request_id'])
        self.credential = self.store.claim(request['request_id'], request['poll_token'])['credential']
        self.identifier = request['device_id']
        self.message = dict(device_id=self.identifier, sample=dict.fromkeys(live.FIELDS))
        self.now = dt.datetime.now(dt.timezone.utc)
        self.clock = self.enterContext(patch.object(storage, '_now', return_value=self.now))

    def post(self, seconds=0):
        self.clock.return_value = self.now+dt.timedelta(seconds=seconds)
        return self.store.ingest_live(self.identifier, self.credential, self.message)

    def test_ring_bound_rate_limit_and_no_sqlite_writes(self):
        sql = []
        self.store._db.set_trace_callback(sql.append)
        for second in range(310):
            self.post(second)
        self.assertEqual(len(self.store.live(self.identifier)['ring']), 300)
        self.assertTrue(all(q.startswith('SELECT') for q in sql), sql)
        with self.assertRaises(storage.ThrottledError):
            self.post(309)
        first = self.store.live(self.identifier)['ring'][0]['observed_at']
        self.assertEqual(storage._time(first), self.now+dt.timedelta(seconds=10))

    def test_demand_expiry_and_focus_mapping(self):
        with patch('time.monotonic', return_value=100) as clock:
            self.assertEqual(self.post()['interval'], 10)
            self.store.live()
            self.assertEqual(self.post(1)['interval'], 5)
            self.store.live(self.identifier)
            self.assertEqual(self.post(2)['interval'], 1)
            clock.return_value = 121
            self.assertEqual(self.post(3)['interval'], 10)

    def test_revoke_and_consent_invalidate_cached_credential_immediately(self):
        self.post()
        self.assertIn(self.identifier, self.store._live_credentials)
        self.store.revoke(self.identifier)
        with self.assertRaises(storage.AuthenticationError):
            self.post(1)
        self.assertNotIn(self.identifier, self.store._live_samples)
        self.assertEqual(self.store.live()['devices'], [])

    def test_permission_revocation_clears_live(self):
        self.post()
        self.store.ingest(self.identifier, self.credential, dict(version=1, device_id=self.identifier,
            policy_version=2, policy=dict(history=False, resources=False), records=[]))
        with self.assertRaises(storage.AuthenticationError):
            self.post(1)
        self.assertEqual(self.store.live(self.identifier)['ring'], [])

    def test_live_marks_online_and_pending_command_prompts_sync(self):
        self.post()
        device = self.store.get_device(self.identifier)
        self.assertEqual(device['reporting_status'], 'reporting')
        self.assertEqual(device['last_received_at'], storage._stamp(self.now))
        self.assertEqual(self.store.get_overview()['counts']['reporting'], 1)
        self.assertEqual(self.store.fleet()['counts']['connected'], 1)
        self.assertEqual(len(self.store.list_devices(status='reporting')), 1)
        self.assertEqual(self.store.list_devices(status='unavailable'), [])
        self.store.send_command(self.identifier, 'restart')
        self.assertTrue(self.post(1)['sync_now'])
        with self.assertRaises(storage.AuthenticationError):
            self.store.ingest_live(self.identifier, 'x'*40, self.message)

    def test_leave_revokes_with_credential_and_wrong_credential_is_refused(self):
        with self.assertRaises(storage.AuthenticationError):
            self.store.leave(self.identifier, 'x'*40)
        self.assertEqual(self.store.get_device(self.identifier)['status'], 'approved')
        self.assertEqual(self.store.leave(self.identifier, self.credential)['status'], 'revoked')
        self.assertEqual(self.store.get_device(self.identifier)['status'], 'revoked')
        with self.assertRaises(storage.AuthenticationError):
            self.store.leave(self.identifier, self.credential)

    def test_delete_removes_the_computer_and_its_credential_stops_working(self):
        self.post()
        self.assertEqual(self.store.delete_device(self.identifier), dict(device_id=self.identifier, deleted=True))
        self.assertEqual(self.store.list_devices(), [])
        with self.assertRaises(storage.AuthenticationError):
            self.store.leave(self.identifier, self.credential)

    def test_quiet_live_computer_shows_offline_within_a_minute_not_ten(self):
        self.post()
        self.store._db.execute('UPDATE devices SET last_received_at=? WHERE device_id=?', (storage._stamp(self.now), self.identifier))  # as after a normal report
        self.clock.return_value = self.now+dt.timedelta(seconds=15)
        self.assertEqual(self.store.get_device(self.identifier)['reporting_status'], 'reporting')
        self.clock.return_value = self.now+dt.timedelta(seconds=45)
        self.assertEqual(self.store.get_device(self.identifier)['reporting_status'], 'report-overdue')
        self.assertEqual(self.store.fleet()['counts']['overdue'], 1)


if __name__ == '__main__':
    unittest.main()

