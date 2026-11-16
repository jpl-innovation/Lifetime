"""Storage tests use real temporary SQLite files; no server or network."""
import concurrent.futures
import copy
import datetime as dt
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from lifetime_protocol import ProtocolError
import lifetime_server_store as storage
from lifetime_server_store import (AuthenticationError, ConflictError, Store,
                                   StoreError, ThrottledError)

NOW = dt.datetime(2026, 11, 7, 12, tzinfo=dt.timezone.utc)
STAMP = '2026-11-07T12:00:00+00:00'
POLICY = {'history': False, 'resources': False}


def record(seq=1, observed=STAMP, measured=40, imported=20, stock=1000):
    return {'seq': seq, 'observed_at': observed,
            'awake_seconds': measured + imported, 'measured_seconds': measured,
            'imported_seconds': imported, 'totals': {'measured_seconds': stock,
            'imported_seconds': 200, 'baseline_seconds': 300}}


def message(device, records=None, policy=None, version=1, **extras):
    return dict(version=1, device_id=device, policy_version=version,
                policy=dict(POLICY if policy is None else policy),
                records=[record()] if records is None else records, **extras)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'server.sqlite'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.clock = patch.object(storage, '_now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def request(self, code=None, device=None, policy=None, **changes):
        if code is None:
            code = self.store.create_code()['code']
        value = dict(version=1, request_id=str(uuid.uuid4()),
                     device_id=device or str(uuid.uuid4()), code=code,
                     poll_token=secrets.token_urlsafe(32), identity={'display_name': 'Máy kiểm tra'},
                     policy=dict(POLICY if policy is None else policy))
        value.update(changes)
        return value

    def pair(self, device=None, policy=None):
        request = self.request(device=device, policy=policy)
        self.store.enroll(request)
        self.store.approve(request['request_id'])
        credential = self.store.claim(request['request_id'], request['poll_token'])['credential']
        return request, credential

    def downgrade_schema(self, version):
        """Reconstruct a pre-pagination schema with its original immutable data."""
        self.store._db.execute('ALTER TABLE devices DROP COLUMN health')
        self.store._db.execute('ALTER TABLE devices DROP COLUMN health_received_at')
        for table in ('invitations', 'guess_ips', 'events'):
            self.store._db.execute('DROP TABLE ' + table)
        self.store._db.execute('ALTER TABLE devices DROP COLUMN client_version')
        self.store._db.execute('ALTER TABLE devices DROP COLUMN last_problem')
        self.store._db.execute('ALTER TABLE devices DROP COLUMN last_problem_at')
        self.store._db.execute('DROP TABLE server_log')
        self.store._db.execute('ALTER TABLE settings DROP COLUMN server_names')
        self.store._db.execute('ALTER TABLE settings DROP COLUMN organisation')
        self.store._db.execute('ALTER TABLE settings DROP COLUMN reporting_interval')
        self.store._db.execute('ALTER TABLE devices DROP COLUMN refresh_requested')
        for index in ('records_usage', 'devices_order', 'devices_status', 'devices_group'):
            self.store._db.execute('DROP INDEX ' + index)
        for field in storage.DELTA_FIELDS:
            self.store._db.execute('ALTER TABLE records DROP COLUMN ' + field)
        for field in ('search_text', 'group_name'):
            self.store._db.execute('ALTER TABLE devices DROP COLUMN ' + field)
        if version == 1:
            self.store._db.execute('ALTER TABLE devices DROP COLUMN retained_before')
            self.store._db.execute('ALTER TABLE devices DROP COLUMN retired_seq')
            self.store._db.execute('DROP TABLE settings')
        self.store._db.execute('PRAGMA user_version=' + str(version))

    def thousand_records(self):
        request, credential = self.pair()
        device = request['device_id']
        for start in range(1, 1001, 100):
            records = [record(seq, stock=seq * 40) for seq in range(start, start + 100)]
            for value in records:
                value['totals']['imported_seconds'] = value['seq'] * 20
            self.store.ingest(device, credential, message(device, records))
        return device, credential

    def test_wal_full_sync_schema_migration_and_unknown_values(self):
        self.assertEqual(self.store._db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.store._db.execute('PRAGMA synchronous').fetchone()[0], 2)
        self.assertEqual(self.store._db.execute('PRAGMA user_version').fetchone()[0], storage.SCHEMA_VERSION)
        self.assertEqual(self.store.list_devices(), [])
        request = self.request()
        self.store.enroll(request)
        device = self.store.get_device(request['device_id'])
        for key in ('totals', 'last_received_at', 'last_observed_at', 'history', 'readings', 'retained_usage'):
            self.assertIsNone(device[key], key)
        self.assertEqual(device['reporting_status'], 'unavailable')
        self.assertEqual(device['coverage'], 'unknown')
        self.assertNotIn('records', self.store.list_devices()[0])

    def test_code_limits_and_only_hashes_persist(self):
        for kwargs in ({'ttl': 0}, {'ttl': True}, {'ttl': 86401}, {'uses': 0},
                       {'uses': 101}, {'uses': 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(StoreError):
                self.store.create_code(**kwargs)
        code = self.store.create_code()
        request = self.request(code['code'])
        self.store.enroll(request)
        self.store.approve(request['request_id'])
        credential = self.store.claim(request['request_id'], request['poll_token'])['credential']
        dump = '\n'.join(self.store._db.iterdump())
        for secret in (code['code'], request['poll_token'], credential):
            self.assertNotIn(secret, dump)
        self.assertNotIn('credential', json.dumps(self.store.list_devices()))
        self.assertEqual(len(self.store._db.execute('SELECT credential_hash FROM devices').fetchone()[0]), 64)
        with patch.object(storage, 'MAX_CODES', 1):
            self.store.create_code()
            with self.assertRaises(StoreError):
                self.store.create_code()

    def test_expired_invalid_and_used_codes(self):
        code = self.store.create_code(ttl=1)['code']
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=1)):
            with self.assertRaises(AuthenticationError):
                self.store.enroll(self.request(code))
        with self.assertRaises(AuthenticationError):
            self.store.enroll(self.request('wrong-code'))
        code = self.store.create_code()['code']
        self.store.enroll(self.request(code))
        with self.assertRaises(AuthenticationError):
            self.store.enroll(self.request(code))

    def test_exact_enrollment_retry_survives_used_code_and_restart(self):
        request = self.request()
        first = self.store.enroll(request)
        self.assertEqual(first, self.store.enroll(copy.deepcopy(request)))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(first, self.store.enroll(request))
        altered = dict(request, poll_token=secrets.token_urlsafe(32))
        with self.assertRaises(ConflictError):
            self.store.enroll(altered)
        with self.assertRaises(ConflictError):
            self.store.enroll(dict(request, identity={'display_name': 'Changed'}))
        self.assertEqual(self.store._db.execute('SELECT remaining FROM codes').fetchone()[0], 0)

    def test_throttling_is_durable_global_and_expires(self):
        for _ in range(storage.GUESS_LIMIT):
            with self.assertRaises(AuthenticationError):
                self.store.enroll(self.request('incorrect'))
        good = self.request()
        self.store.close()
        self.store = Store(self.path)
        with self.assertRaises(ThrottledError):
            self.store.enroll(good)
        self.assertEqual(self.store.list_devices(), [])
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=storage.GUESS_WINDOW)):
            self.assertEqual(self.store.enroll(good)['status'], 'pending')

    def test_pending_denial_and_approval_require_poll_secret(self):
        request = self.request()
        self.store.enroll(request)
        self.assertEqual(self.store.claim(request['request_id'], request['poll_token'])['status'], 'pending')
        with self.assertRaises(AuthenticationError):
            self.store.claim(request['request_id'], secrets.token_urlsafe(32))
        self.assertEqual(self.store.deny(request['request_id'])['status'], 'denied')
        self.assertEqual(self.store.deny(request['request_id'])['status'], 'denied')
        self.assertEqual(self.store.claim(request['request_id'], request['poll_token'])['status'], 'denied')
        with self.assertRaises(ConflictError):
            self.store.approve(request['request_id'])
        with self.assertRaises(AuthenticationError):
            self.store.ingest(request['device_id'], secrets.token_urlsafe(32), message(request['device_id']))

    def test_admin_can_find_pending_requests_and_pending_bound_is_atomic(self):
        code = self.store.create_code(uses=2)['code']
        first = self.request(code)
        second = self.request(code)
        with patch.object(storage, 'MAX_PENDING', 1):
            self.store.enroll(first)
            pending = self.store.list_devices()[0]['pending_requests']
            self.assertEqual(pending[0]['request_id'], first['request_id'])
            self.assertNotIn('poll_hash', pending[0])
            with self.assertRaises(StoreError):
                self.store.enroll(second)
            self.assertEqual(self.store._db.execute('SELECT remaining FROM codes').fetchone()[0], 1)
            self.store.deny(first['request_id'])
            self.assertEqual(self.store.enroll(second)['status'], 'pending')

    def test_claim_retry_rotates_until_first_successful_ingest(self):
        request, old = self.pair()
        self.assertEqual(self.store.approve(request['request_id'])['status'], 'approved')
        claimed = self.store.claim(request['request_id'], request['poll_token'])
        credential = claimed['credential']
        self.assertNotEqual(old, credential)
        with self.assertRaises(AuthenticationError):
            self.store.ingest(request['device_id'], old, message(request['device_id']))
        self.store.ingest(request['device_id'], credential, message(request['device_id']))
        with self.assertRaises(AuthenticationError):
            self.store.claim(request['request_id'], request['poll_token'])
        self.assertIsNone(self.store._db.execute('SELECT poll_hash FROM requests').fetchone()[0])
        self.assertEqual(self.store.enroll(request)['status'], 'completed')

    def test_revoke_and_reenroll_preserve_server_metadata_records_and_sequence(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        self.store.update_identity(device, {'display_name': 'Máy chủ', 'asset_tag': 'A-42'})
        self.assertEqual(self.store.revoke(device)['status'], 'revoked')
        with self.assertRaises(AuthenticationError):
            self.store.ingest(device, credential, message(device))
        newer, replacement = self.pair(device=device)
        info = self.store.get_device(device)
        self.assertEqual(info['identity']['display_name'], 'Máy chủ')
        self.assertEqual(info['identity']['asset_tag'], 'A-42')
        self.assertEqual(self.store.ingest(device, replacement, message(device))['duplicates'], [1])
        with self.assertRaises(ConflictError):
            self.store.ingest(device, replacement, message(device, [record(stock=999)]))
        with self.assertRaises(AuthenticationError):
            self.store.claim(request['request_id'], request['poll_token'])
        self.assertNotEqual(newer['request_id'], request['request_id'])

    def test_reenrollment_does_not_rewind_policy_and_stale_consent_stays_denied(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device, [], version=8))
        self.store.revoke(device)
        _, replacement = self.pair(device=device)
        stale = message(device, [], policy={'history': True, 'resources': True},
                        history={'daily': {'2026-11-07': 60}})
        ack = self.store.ingest(device, replacement, stale)
        self.assertEqual(ack['policy_version'], 8)
        self.assertIsNone(self.store.get_device(device)['history'])

    def test_pending_or_denied_reenrollment_does_not_revoke_active_device(self):
        request, credential = self.pair()
        newer = self.request(device=request['device_id'])
        self.store.enroll(newer)
        self.assertEqual(self.store.get_device(request['device_id'])['pending_requests'][0]['request_id'], newer['request_id'])
        self.store.deny(newer['request_id'])
        self.assertEqual(self.store.ingest(request['device_id'], credential, message(request['device_id']))['accepted'], [1])

    def test_only_latest_approved_enrollment_can_claim(self):
        first, old = self.pair()
        second, credential = self.pair(device=first['device_id'])
        with self.assertRaises(AuthenticationError):
            self.store.claim(first['request_id'], first['poll_token'])
        with self.assertRaises(AuthenticationError):
            self.store.ingest(first['device_id'], old, message(first['device_id']))
        self.store.ingest(second['device_id'], credential, message(second['device_id']))

    def test_sync_duplicate_out_of_order_and_stocks_not_summed(self):
        request, credential = self.pair()
        device = request['device_id']
        newer = record(3, '2026-11-06T00:00:00+00:00', stock=3000)
        self.store.ingest(device, credential, message(device, [newer, record(1)]))
        receipt = self.store.get_device(device)['records'][0]['received_at']
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=3)):
            ack = self.store.ingest(device, credential, message(device, [record(1), record(2, stock=2000)]))
        self.assertEqual(ack['accepted'], [2])
        self.assertEqual(ack['duplicates'], [1])
        info = self.store.get_device(device)
        self.assertEqual([r['seq'] for r in info['records']], [1, 2, 3])
        self.assertEqual(info['records'][0]['received_at'], receipt)
        self.assertEqual(info['totals']['measured_seconds'], 3000)
        self.assertEqual(info['totals']['baseline_seconds'], 300)
        self.assertEqual(info['retained_usage'], {'awake_seconds': 180, 'measured_seconds': 120, 'imported_seconds': 60})

    def test_thousand_records_are_paginated_with_full_counts_and_uninflated_stocks(self):
        device, credential = self.thousand_records()
        first = self.store.get_device(device)
        self.assertEqual(len(first['records']), 100)
        self.assertEqual(first['record_count'], 1000)
        self.assertTrue(first['records_truncated'])
        self.assertEqual(first['records_next_seq'], 100)
        expected_totals = {'measured_seconds': 40000, 'imported_seconds': 20000, 'baseline_seconds': 300}
        expected_usage = {'awake_seconds': 60000, 'measured_seconds': 40000, 'imported_seconds': 20000}
        seen, after = [], 0
        while True:
            page = self.store.get_device(device, limit=137, after_seq=after)
            self.assertLessEqual(len(page['records']), 137)
            self.assertEqual(page['record_count'], 1000)
            self.assertEqual(page['retained_usage'], expected_usage)
            self.assertEqual(page['totals'], expected_totals)
            seen.extend(value['seq'] for value in page['records'])
            if not page['records_truncated']:
                self.assertIsNone(page['records_next_seq'])
                break
            self.assertGreater(page['records_next_seq'], after)
            after = page['records_next_seq']
        self.assertEqual(seen, list(range(1, 1001)))
        last = self.store.get_device(device, after_seq=1000)
        self.assertEqual(last['records'], [])
        self.assertEqual(last['record_count'], 1000)
        self.assertFalse(last['records_truncated'])
        self.assertIsNone(last['records_next_seq'])
        maximum = self.store.get_device(device, limit=500)
        self.assertEqual(len(maximum['records']), 500)
        self.assertEqual(maximum['records_next_seq'], 500)
        self.assertLess(len(json.dumps(maximum).encode('utf-8')), storage.MAX_BYTES)
        # Retried immutable data cannot change deltas or cumulative stock totals.
        duplicate = record(1000, stock=40000)
        duplicate['totals']['imported_seconds'] = 20000
        self.assertEqual(self.store.ingest(device, credential, message(device, [duplicate]))['duplicates'], [1000])
        summary = self.store.list_devices()[0]
        self.assertEqual(summary['record_count'], 1000)
        self.assertEqual(summary['retained_usage'], expected_usage)
        self.assertEqual(summary['totals'], expected_totals)

    def test_list_uses_covering_aggregate_without_reading_record_payloads(self):
        device, _ = self.thousand_records()
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == 'records' and column == 'payload':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store._db.set_authorizer(authorize)
        try:
            summary = self.store.list_devices()[0]
        finally:
            self.store._db.set_authorizer(None)
        self.assertEqual(summary['record_count'], 1000)
        self.assertNotIn('records', summary)
        plan = self.store._db.execute('''EXPLAIN QUERY PLAN SELECT COUNT(*),SUM(awake_seconds),
            SUM(measured_seconds),SUM(imported_seconds) FROM records WHERE device_id=?''', (device,)).fetchall()
        self.assertTrue(any('COVERING INDEX' in row[3] for row in plan), plan)

    def test_list_omits_large_optional_bodies_but_keeps_availability_and_timestamps(self):
        allowed = {'history': True, 'resources': True}
        start = dt.date(2010, 1, 1)
        history = {'daily': {(start + dt.timedelta(days=index)).isoformat(): 60 for index in range(3660)}}
        readings = {'observed_at': STAMP, 'system': 'Windows'}
        code = self.store.create_code(uses=100)['code']
        first = None
        for _ in range(100):
            request = self.request(code, policy=allowed)
            self.store.enroll(request)
            self.store.approve(request['request_id'])
            credential = self.store.claim(request['request_id'], request['poll_token'])['credential']
            self.store.ingest(request['device_id'], credential,
                              message(request['device_id'], policy=allowed, history=history, readings=readings))
            first = first or request['device_id']
        summary = self.store.list_devices()
        self.assertEqual(len(summary), 100)
        self.assertLess(len(json.dumps(summary).encode('utf-8')), storage.MAX_BYTES)
        for device in summary:
            for category in ('history', 'readings'):
                self.assertNotIn(category, device)
                self.assertTrue(device[category + '_available'])
                self.assertIsNotNone(device[category + '_received_at'])
        detail = self.store.get_device(first)
        self.assertEqual(detail['history'], history)
        self.assertEqual(detail['readings'], readings)
        self.assertEqual(len(self.store.list_devices(limit=25)), 25)

    def test_device_pages_filters_and_server_identity_edits(self):
        requests = []
        code = self.store.create_code(uses=100)['code']
        for index in range(125):
            if index == 100:
                code = self.store.create_code(uses=100)['code']
            request = self.request(code, device=str(uuid.UUID(int=index + 1)), identity={
                'display_name': f'Máy {index:03d}',
                'department': 'Kỹ Thuật' if index < 62 else 'Sales',
                'asset_tag': 'literal%_tag' if index == 0 else ''})
            self.store.enroll(request)
            requests.append(request)
        first = requests[0]
        self.store.approve(first['request_id'])
        credential = self.store.claim(first['request_id'], first['poll_token'])['credential']
        self.store.ingest(first['device_id'], credential, message(first['device_id']))
        self.store.deny(requests[1]['request_id'])
        self.store.revoke(requests[2]['device_id'])
        self.assertEqual(len(self.store.list_devices()), 100)
        self.assertEqual([value['device_id'] for value in self.store.list_devices(limit=40, offset=100)],
                         [request['device_id'] for request in requests[100:]])
        self.assertEqual(self.store.list_devices(offset=125), [])
        self.assertEqual(len(self.store.list_devices(limit=500, search='MÁY')), 125)
        self.assertEqual(len(self.store.list_devices(limit=500, group='KỸ THUẬT')), 62)
        filtered = self.store.list_devices(limit=10, offset=5, search='máy', group='sales', status='pending')
        self.assertEqual([value['device_id'] for value in filtered],
                         [request['device_id'] for request in requests[67:77]])
        self.assertEqual(len(self.store.list_devices(limit=500, status='pending')), 122)
        self.assertEqual(len(self.store.list_devices(limit=500, status='unavailable')), 124)
        for status in ('approved', 'denied', 'revoked', 'reporting'):
            self.assertEqual(len(self.store.list_devices(status=status)), 1)
        self.assertEqual(self.store.list_devices(status='report-overdue'), [])
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=600)):
            self.assertEqual(len(self.store.list_devices(status='reporting')), 1)
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=601)):
            self.assertEqual(len(self.store.list_devices(status='report-overdue')), 1)
            self.assertEqual(self.store.list_devices(status='reporting'), [])
        self.assertEqual(len(self.store.list_devices(search='literal%_tag')), 1)
        self.assertEqual(self.store.list_devices(search="' OR 1=1 --"), [])
        self.store.update_identity(first['device_id'], {'display_name': 'Renamed', 'department': 'Operations'})
        self.assertEqual(self.store.list_devices(search='máy 000'), [])
        self.assertEqual(self.store.list_devices(group='operations', search='RENAMED')[0]['device_id'], first['device_id'])

    def test_pagination_rejects_invalid_limits_positions_and_filters(self):
        request, _ = self.pair()
        for kwargs in ({'limit': 0}, {'limit': 501}, {'limit': True}, {'limit': 1.5},
                       {'offset': -1}, {'offset': True}, {'offset': 2**63},
                       {'search': None}, {'search': 'x' * 4097}, {'group': []},
                       {'status': 'offline'}, {'status': 123}):
            with self.subTest(kwargs=str(kwargs)[:100]), self.assertRaises(StoreError):
                self.store.list_devices(**kwargs)
        for kwargs in ({'limit': 0}, {'limit': 501}, {'limit': False},
                       {'after_seq': -1}, {'after_seq': True}, {'after_seq': 1.5},
                       {'after_seq': 2**53}):
            with self.subTest(kwargs=kwargs), self.assertRaises(StoreError):
                self.store.get_device(request['device_id'], **kwargs)
        empty = self.store.get_device(request['device_id'])
        self.assertFalse(empty['records_truncated'])
        self.assertIsNone(empty['records_next_seq'])

    def test_conflict_rolls_back_entire_batch_policy_receipt_and_claim(self):
        request, credential = self.pair()
        device = request['device_id']
        # Duplicate within the same batch conflicts after a successful first INSERT.
        conflict = message(device, [record(1), record(1, measured=50)],
                           policy={'history': True, 'resources': True}, version=2)
        with self.assertRaises(ConflictError):
            self.store.ingest(device, credential, conflict)
        info = self.store.get_device(device)
        self.assertEqual(info['record_count'], 0)
        self.assertEqual(info['policy_version'], 1)
        self.assertIsNone(info['last_received_at'])
        self.assertIn('credential', self.store.claim(request['request_id'], request['poll_token']))

    def test_authentication_and_device_binding(self):
        one, credential = self.pair()
        two, _ = self.pair()
        for device, payload in ((two['device_id'], message(two['device_id'])),
                                (one['device_id'], message(two['device_id']))):
            with self.subTest(device=device), self.assertRaises(AuthenticationError):
                self.store.ingest(device, credential, payload)
        self.assertEqual([d['record_count'] for d in self.store.list_devices()], [0, 0])
        unknown = str(uuid.uuid4())
        with self.assertRaises(AuthenticationError):
            self.store.ingest(unknown, credential, message(unknown))

    def test_optional_readings_cannot_move_backwards_and_duplicate_names_stay_distinct(self):
        allowed = {'history': True, 'resources': True}
        request, credential = self.pair(policy=allowed)
        device = request['device_id']
        recent = {'observed_at': STAMP, 'system': 'Windows'}
        old = {'observed_at': '2026-01-01T00:00:00Z', 'system': 'Linux'}
        self.store.ingest(device, credential, message(device, policy=allowed, readings=recent))
        self.store.ingest(device, credential, message(device, [record(2)], policy=allowed, readings=old))
        self.assertEqual(self.store.get_device(device)['readings'], recent)
        other, other_credential = self.pair()
        self.store.ingest(other['device_id'], other_credential, message(other['device_id']))
        devices = self.store.list_devices()
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]['identity']['display_name'], devices[1]['identity']['display_name'])
        self.assertEqual(sorted(d['record_count'] for d in devices), [1, 2])

    def test_invalid_batches_leave_database_unchanged(self):
        request, credential = self.pair()
        device = request['device_id']
        bad = []
        for change in ({'seq': True}, {'seq': -1}, {'awake_seconds': 99},
                       {'measured_seconds': float('nan')}, {'imported_seconds': -1},
                       {'observed_at': '2026-11-07T12:00:00'}):
            bad.append(message(device, [dict(record(), **change)]))
        bad.extend([message(device, [record(i+1) for i in range(101)]),
                    dict(message(device), version=2),
                    dict(message(device), policy_version=True),
                    dict(message(device), ignored='x' * storage.MAX_BYTES),
                    dict(message(device), ignored='\ud800')])
        for payload in bad:
            with self.subTest(payload=str(payload)[:120]), self.assertRaises(ProtocolError):
                self.store.ingest(device, credential, payload)
        self.assertEqual(self.store.get_device(device)['record_count'], 0)

    def test_privacy_newer_policy_wins_stale_cannot_reenable(self):
        allowed = {'history': True, 'resources': True}
        request, credential = self.pair(policy=allowed)
        device = request['device_id']
        history = {'daily': {'2026-11-07': 60}}
        readings = {'observed_at': STAMP, 'system': 'Windows'}
        self.store.ingest(device, credential, message(device, policy=allowed, history=history, readings=readings))
        self.assertEqual(self.store.get_device(device)['history'], history)
        self.store.ingest(device, credential, message(device, [], version=2))
        stale = self.store.ingest(device, credential, message(device, [record(2)], policy=allowed, history=history, readings=readings))
        self.assertEqual(stale['policy_version'], 2)
        info = self.store.get_device(device)
        self.assertIsNone(info['history'])
        self.assertIsNone(info['readings'])
        raw = self.store._db.execute('SELECT history,readings FROM devices').fetchone()
        self.assertEqual(tuple(raw), (None, None))
        with self.assertRaises(ConflictError):
            self.store.ingest(device, credential, message(device, [], policy=allowed, version=2))
        self.store.ingest(device, credential, message(device, [], policy=allowed, version=3, readings=readings))
        self.assertEqual(self.store.get_device(device)['readings'], readings)

    def test_a_switch_turned_off_removes_that_data_from_every_path_and_from_the_next_backup(self):
        """History, readings and health each: stored, listed, in the fleet, insight and alert inputs and in a backup
        while shared; gone from all of them once the computer reports the switch off."""
        allowed = dict(history=True, resources=True, health=True)
        request, credential = self.pair(policy=allowed)
        device = request['device_id']
        today = NOW.astimezone().date().isoformat()
        shared = dict(
            history={'daily': {today: 4242}},
            readings={'observed_at': STAMP, 'specs': {'cpu_name': 'MARKER-CPU', 'ram_bytes': 8 * 2**30,
                                                       'mac_addresses': ['02:00:00:00:00:99']},
                      'performance': {'cpu_percent': 37, 'ram_used_bytes': 2**30, 'charging': False, 'power_watts': 11}},
            health=dict(version=1, observed_at=STAMP, shutdowns_30d=7, crashes_30d=2, battery_health=None,
                        cycle_count=None, last_crash=None))

        def everywhere():
            raw = self.store._db.execute('SELECT history,readings,health FROM devices').fetchone()
            detail, listed = self.store.get_device(device), self.store.list_devices()[0]
            fleet = self.store.fleet(day=today)['rows'][0]
            insight, alert = self.store.insight_inputs()[0], self.store.monitor_snapshot()[0]
            backup = self.root / 'consent-backup.sqlite'
            self.store.backup(backup)
            reader = sqlite3.connect(backup)
            try:
                saved = reader.execute('SELECT history,readings,health FROM devices').fetchone()
                samples = reader.execute('SELECT COUNT(*) FROM performance_samples').fetchone()[0]
            finally:
                reader.close()
            return dict(
                history=[raw[0], detail['history'], detail['today'], listed['today'], listed['history_available'],
                         fleet['today'], insight['history'], saved[0]],
                readings=[raw[1], detail['readings'], detail['specs'], detail['performance'], detail['performance_history'],
                          detail['summary']['cpu_name'], listed['summary']['cpu_name'], listed['readings_available'],
                          insight['readings'], insight['discharge'], insight['kind'], alert['metrics'], saved[1], samples,
                          self.store._db.execute('SELECT COUNT(*) FROM performance_samples').fetchone()[0]],
                health=[raw[2], detail['health'], listed['health_available'], fleet['health'], insight['health'],
                        alert['crashes_30d'], saved[2]])

        self.store.ingest(device, credential, message(device, policy=allowed, **shared))
        before = everywhere()
        for category, values in before.items():
            self.assertTrue(all(value not in (None, [], False, 0) for value in values), (category, values))
        version = 1
        for switch, category in (('health', 'health'), ('history', 'history'), ('resources', 'readings')):
            version += 1
            allowed = dict(allowed, **{switch: False})
            self.store.ingest(device, credential, message(device, [], policy=allowed, version=version))
            after = everywhere()
            self.assertTrue(all(value in (None, [], False, 0) for value in after[category]), (category, after[category]))
            for other in ('history', 'readings', 'health'):
                if allowed[dict(readings='resources').get(other, other)]:
                    self.assertEqual(after[other][:-1] if other != 'readings' else after[other][:12],
                                     before[other][:-1] if other != 'readings' else before[other][:12], other)
        with self.assertRaises(StoreError):         # Wake-on-LAN needed the MAC addresses that are gone too
            self.store.wake(device)

    def test_identity_owned_by_server_and_validated(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.update_identity(device, {'display_name': '', 'department': 'Kỹ thuật', 'deployment_date': '2026-01-02'})
        self.store.ingest(device, credential, message(device))
        info = self.store.get_device(device)
        self.assertEqual(info['identity']['department'], 'Kỹ thuật')
        self.assertEqual(info['identity']['display_name'], '')
        for identity in ({'username': 'someone'}, {'display_name': 'a' * 4097},
                         {'deployment_date': '2026-02-30'}, {'location': None}):
            with self.subTest(identity=str(identity)[:80]), self.assertRaises(ProtocolError):
                self.store.update_identity(device, identity)

    def test_groups_can_exist_before_any_computer_and_rename_moves_computers(self):
        self.store.add_group('  Lab A ')
        self.store.add_group('lab a')                       # same name, any case: not a second group
        self.assertEqual(self.store.list_groups(), [dict(name='Lab A', computers=0, created=True)])
        self.assertEqual(self.store.fleet()['groups'], ['Lab A'])
        request, _credential = self.pair()
        device = request['device_id']
        self.store.update_identity(device, {'display_name': 'PC 1', 'department': 'Lab A', 'location': 'Room 2'})
        self.assertEqual(self.store.list_groups(), [dict(name='Lab A', computers=1, created=True)])
        groups = self.store.rename_group('Lab A', 'Lab B')
        self.assertEqual(groups, [dict(name='Lab B', computers=1, created=True)])
        identity = self.store.get_device(device)['identity']
        self.assertEqual((identity['department'], identity['location']), ('Lab B', 'Room 2'))
        self.assertEqual([row['device_id'] for row in self.store.list_devices(group='lab b')], [device])
        self.store.remove_group('Lab B')                     # still used by a computer, so it stays listed
        self.assertEqual(self.store.list_groups(), [dict(name='Lab B', computers=1, created=False)])
        for bad in ('', '   ', 'x' * 121, 'a\nb', None):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.add_group(bad)

    def test_renaming_a_group_onto_another_groups_name_is_refused_not_merged(self):
        self.store.add_group('Lab A')
        self.store.add_group('Sales')
        request, _credential = self.pair()
        self.store.update_identity(request['device_id'], {'display_name': 'PC 1', 'department': 'Office'})
        for taken in (' sales ', 'SALES', 'office'):        # a created group, or one only a computer is in
            with self.subTest(taken=taken), self.assertRaises(ConflictError) as caught:
                self.store.rename_group('Lab A', taken)
            self.assertEqual(caught.exception.refusal, 'group-exists')
        self.assertEqual([g['name'] for g in self.store.list_groups()], ['Lab A', 'Office', 'Sales'])
        self.assertEqual([g['name'] for g in self.store.rename_group('Lab A', 'LAB A')], ['LAB A', 'Office', 'Sales'])

    def test_created_groups_survive_backup_and_restore_and_are_capped(self):
        self.store.add_group('Lab A')
        backup = self.root / 'groups.sqlite'
        self.store.backup(backup)
        self.store.add_group('Later')
        self.store.restore(backup)
        self.assertEqual([group['name'] for group in self.store.list_groups()], ['Lab A'])
        with patch.object(storage, 'MAX_GROUPS', 1), self.assertRaises(StoreError):
            self.store.add_group('One too many')

    def test_backup_from_before_the_groups_upgrade_still_restores(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.update_identity(device, {'display_name': 'Old PC', 'department': 'Lab A'})
        old = self.root / 'schema9.sqlite'
        self.store.backup(old)
        db = sqlite3.connect(old)
        db.execute('DROP TABLE named_groups')
        db.execute('PRAGMA user_version=9')
        db.commit()
        db.close()
        self.store.add_group('Created later')
        self.store.restore(old)
        self.assertEqual(self.store.get_device(device)['identity']['display_name'], 'Old PC')
        self.assertEqual(self.store.list_groups(), [dict(name='Lab A', computers=1, created=False)])
        db = sqlite3.connect(old)                      # the backup file itself is never changed
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)
        db.close()

    def test_reporting_status_uses_receipt_time_not_client_clock(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device, [record(observed='2099-01-01T00:00:00+00:00')]))
        self.assertEqual(self.store.list_devices()[0]['reporting_status'], 'reporting')
        with patch.object(storage, '_now', return_value=NOW + dt.timedelta(seconds=601)):
            self.assertEqual(self.store.list_devices()[0]['reporting_status'], 'report-overdue')
        self.store.revoke(device)
        self.assertEqual(self.store.list_devices()[0]['reporting_status'], 'unavailable')

    def test_retention_boundary_floor_replay_and_out_of_order_survivors(self):
        request, credential = self.pair()
        device = request['device_id']
        old = record(5, '2026-11-06T23:59:59+00:00')
        boundary = record(2, '2026-11-07T07:00:00+07:00', stock=2000)
        self.store.ingest(device, credential, message(device, [old, boundary, record(8)]))
        self.assertEqual(self.store.retain('2026-11-07T00:00:00Z'), {'deleted': 1})
        info = self.store.get_device(device)
        self.assertEqual(info['retired_seq'], 5)
        self.assertEqual([r['seq'] for r in info['records']], [2, 8])
        # A retained record below the floor still has conflict detection.
        self.assertEqual(self.store.ingest(device, credential, message(device, [boundary]))['duplicates'], [2])
        with self.assertRaises(ConflictError):
            self.store.ingest(device, credential, message(device, [dict(boundary, awake_seconds=0, measured_seconds=0, imported_seconds=0)]))
        # Tampering with a retired record's timestamp cannot resurrect it.
        ack = self.store.ingest(device, credential, message(device, [dict(old, observed_at=STAMP), record(4)]))
        self.assertEqual(ack['retired'], [5, 4])
        # A newly numbered, old observation is retired as well.
        self.assertEqual(self.store.ingest(device, credential, message(device, [record(9, '2020-01-01T00:00:00Z')]))['retired'], [9])
        self.assertEqual(self.store.get_device(device)['record_count'], 2)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get_device(device)['retired_seq'], 9)
        with self.assertRaises(StoreError):
            self.store.retain('2026-11-06T00:00:00Z')
        with self.assertRaises(StoreError):
            self.store.retain('2026-11-07T00:00:00')

    def test_retention_clears_optional_old_data_and_blocks_replay(self):
        allowed = {'history': True, 'resources': True}
        request, credential = self.pair(policy=allowed)
        device = request['device_id']
        old = '2026-11-06T00:00:00Z'
        history = {'daily': {'2026-11-06': 60, '2026-11-07': 20}, 'first_seen': old}
        readings = {'observed_at': old, 'system': 'Windows'}
        payload = message(device, policy=allowed, history=history, readings=readings)
        self.store.ingest(device, credential, payload)
        self.store.retain('2026-11-07T00:00:00Z')
        self.store.ingest(device, credential, payload)
        info = self.store.get_device(device)
        self.assertEqual(info['history'], {'daily': {'2026-11-07': 20}})
        self.assertIsNone(info['readings'])
        # New devices also inherit the existing retention boundary.
        newer, secret = self.pair()
        self.assertEqual(self.store.ingest(newer['device_id'], secret,
                         message(newer['device_id'], [record(observed=old)]))['retired'], [1])

    def test_retention_keeps_latest_stock_but_does_not_invent_retained_usage(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        self.store.retain('2026-11-08T00:00:00Z')
        info = self.store.get_device(device)
        self.assertEqual(info['record_count'], 0)
        self.assertIsNone(info['retained_usage'])
        self.assertEqual(info['totals'], record()['totals'])
        self.assertEqual(info['last_observed_at'], STAMP)

    def test_backup_restore_invalidates_all_credentials_tokens_and_codes(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        self.store.update_identity(device, {'display_name': 'Sao lưu'})
        self.store.retain('2026-11-07T00:00:00Z')
        pending = self.request()
        self.store.enroll(pending)
        unused_code = self.store.create_code()['code']
        backup = self.root / 'backup.sqlite'
        self.assertEqual(self.store.backup(backup)['schema_version'], storage.SCHEMA_VERSION)
        before_backup = backup.read_bytes()
        # A separate open connection must observe restored rows and revocations.
        peer = Store(self.path)
        self.addCleanup(peer.close)
        self.store.ingest(device, credential, message(device, [record(2)]))
        self.store.update_identity(device, {'display_name': 'Later'})
        self.store.restore(backup)
        self.assertEqual(backup.read_bytes(), before_backup)
        info = peer.get_device(device)
        self.assertEqual(info['record_count'], 1)
        self.assertEqual(info['identity']['display_name'], 'Sao lưu')
        self.assertEqual(info['status'], 'revoked')
        self.assertEqual(info['retained_before'], '2026-11-07T00:00:00.000000+00:00')
        with self.assertRaises(AuthenticationError):
            peer.ingest(device, credential, message(device))
        with self.assertRaises(AuthenticationError):
            peer.claim(pending['request_id'], pending['poll_token'])
        with self.assertRaises(AuthenticationError):
            peer.enroll(self.request(unused_code))
        self.assertEqual(peer._db.execute('SELECT count(*) FROM codes').fetchone()[0], 0)
        newer, secret = self.pair(device=device)
        self.assertEqual(self.store.ingest(device, secret, message(device))['duplicates'], [1])
        self.assertEqual(newer['device_id'], device)

    def test_restore_rejects_corruption_schema_and_digest_without_changing_live_data(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        valid = self.root / 'valid.sqlite'
        self.store.backup(valid)
        before = self.store.get_device(device)
        for mutation in ('version', 'schema', 'digest', 'policy', 'foreign-key', 'snapshot', 'snapshot-value', 'delta', 'search'):
            bad = self.root / (mutation + '.sqlite')
            bad.write_bytes(valid.read_bytes())
            db = sqlite3.connect(bad)
            try:
                if mutation == 'version':
                    db.execute('PRAGMA user_version=999')
                elif mutation == 'schema':
                    db.execute('CREATE TRIGGER dangerous AFTER INSERT ON records BEGIN DELETE FROM devices; END')
                elif mutation == 'digest':
                    db.execute("UPDATE records SET digest=?", ('0' * 64,))
                elif mutation == 'policy':
                    db.execute("UPDATE devices SET policy='{}'")
                elif mutation == 'snapshot':
                    db.execute('UPDATE devices SET latest_seq=99')
                elif mutation == 'snapshot-value':
                    db.execute('UPDATE devices SET latest_record=?', (json.dumps(record(stock=888)),))
                elif mutation == 'delta':
                    db.execute('UPDATE records SET awake_seconds=888')
                elif mutation == 'search':
                    db.execute("UPDATE devices SET search_text='wrong'")
                else:
                    db.execute('UPDATE records SET device_id=?', (str(uuid.uuid4()),))
                db.commit()
            finally:
                db.close()
            with self.subTest(mutation=mutation), self.assertRaises(StoreError):
                self.store.restore(bad)
            self.assertEqual(self.store.get_device(device), before)
        corrupt = self.root / 'corrupt.sqlite'
        corrupt.write_bytes(b'not a sqlite database')
        with self.assertRaises(StoreError):
            self.store.restore(corrupt)
        self.assertEqual(self.store.get_device(device), before)
        self.assertEqual(self.store.ingest(device, credential, message(device))['duplicates'], [1])

    def test_backup_atomic_replace_failure_preserves_destination_and_cleans_temp(self):
        destination = self.root / 'existing.sqlite'
        destination.write_bytes(b'existing-backup')
        with patch.object(storage.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.store.backup(destination)
        self.assertEqual(destination.read_bytes(), b'existing-backup')
        self.assertEqual(list(self.root.glob('*.tmp')), [])
        with patch.object(storage.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.backup(destination)
        self.assertEqual(destination.read_bytes(), b'existing-backup')
        self.assertEqual(list(self.root.glob('*.tmp')), [])
        for path in (self.path, str(self.path) + '-wal', str(self.path) + '-shm'):
            with self.subTest(path=path), self.assertRaises(StoreError):
                self.store.backup(path)
            with self.assertRaises(StoreError):
                self.store.restore(path)

    def test_restore_failure_rolls_back_deleted_live_rows_and_auth(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        backup = self.root / 'backup.sqlite'
        self.store.backup(backup)
        self.store.ingest(device, credential, message(device, [record(2)]))
        self.store._db.execute("CREATE TEMP TRIGGER fail_restore BEFORE INSERT ON devices BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.restore(backup)
        self.store._db.execute('DROP TRIGGER fail_restore')
        self.assertEqual(self.store.get_device(device)['record_count'], 2)
        self.assertEqual(self.store.ingest(device, credential, message(device))['duplicates'], [1])

    def test_version_one_migration_preserves_devices_records_and_authentication(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device))
        # Reconstruct the earlier schema in a real file, then reopen via Store.
        self.downgrade_schema(1)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store._db.execute('PRAGMA user_version').fetchone()[0], storage.SCHEMA_VERSION)
        self.assertEqual(self.store.get_device(device)['retired_seq'], 0)
        self.assertEqual(self.store.ingest(device, credential, message(device))['duplicates'], [1])
        self.store.backup(self.root / 'after-migration.sqlite')

    def test_version_two_migration_backfills_thousand_deltas_and_search_metadata(self):
        device, credential = self.thousand_records()
        self.store.update_identity(device, {'display_name': 'Máy cũ', 'department': 'Kỹ Thuật'})
        before = [tuple(row) for row in self.store._db.execute('SELECT seq,digest,payload FROM records ORDER BY seq')]
        self.downgrade_schema(2)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store._db.execute('PRAGMA user_version').fetchone()[0], storage.SCHEMA_VERSION)
        self.assertEqual(before, [tuple(row) for row in self.store._db.execute('SELECT seq,digest,payload FROM records ORDER BY seq')])
        summary = self.store.list_devices(group='KỸ THUẬT', search='MÁY CŨ')[0]
        self.assertEqual(summary['record_count'], 1000)
        self.assertEqual(summary['retained_usage'], {'awake_seconds': 60000, 'measured_seconds': 40000, 'imported_seconds': 20000})
        self.assertEqual(summary['totals']['measured_seconds'], 40000)
        duplicate = record(1, stock=40)
        duplicate['totals']['imported_seconds'] = 20
        self.assertEqual(self.store.ingest(device, credential, message(device, [duplicate]))['duplicates'], [1])
        self.store.backup(self.root / 'migrated.sqlite')

    def test_invalid_version_two_record_rolls_back_entire_migration(self):
        request, credential = self.pair()
        self.store.ingest(request['device_id'], credential, message(request['device_id']))
        self.downgrade_schema(2)
        self.store._db.execute("UPDATE records SET payload='{}'")
        self.store.close()
        with self.assertRaises(ProtocolError):
            Store(self.path)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertNotIn('awake_seconds', [row[1] for row in db.execute('PRAGMA table_info(records)')])
            self.assertNotIn('search_text', [row[1] for row in db.execute('PRAGMA table_info(devices)')])
        finally:
            db.close()

    def test_future_or_foreign_database_rejected_without_schema_changes(self):
        for kind in ('future', 'foreign', 'unversioned'):
            path = self.root / (kind + '.sqlite')
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE unrelated (value TEXT)')
            if kind == 'future':
                db.execute('PRAGMA user_version=999')
            elif kind == 'foreign':
                db.execute('PRAGMA application_id=1234')
            db.commit()
            db.close()
            before = path.read_bytes()
            with self.subTest(kind=kind), self.assertRaises(StoreError):
                Store(path)
            self.assertEqual(path.read_bytes(), before)

    def test_bounded_code_concurrent_connections(self):
        code = self.store.create_code(uses=3)['code']
        attempts = [self.request(code) for _ in range(9)]
        barrier = threading.Barrier(len(attempts))

        def enroll(request):
            peer = Store(self.path)
            try:
                barrier.wait(timeout=15)
                try:
                    return peer.enroll(request)['status']
                except AuthenticationError:
                    return 'rejected'
            finally:
                peer.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(attempts)) as executor:
            results = list(executor.map(enroll, attempts))
        self.assertEqual(results.count('pending'), 3)
        self.assertEqual(results.count('rejected'), 6)
        self.assertEqual(len(self.store.list_devices()), 3)

    def test_concurrent_initialization_and_exact_enrollment_retries(self):
        fresh = self.root / 'new.sqlite'
        barrier = threading.Barrier(6)

        def initialize(_):
            barrier.wait(timeout=15)
            peer = Store(fresh)
            try:
                return peer.list_devices()
            finally:
                peer.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            self.assertEqual(list(executor.map(initialize, range(6))), [[]] * 6)
        request = self.request()
        barrier = threading.Barrier(6)

        def enroll(_):
            peer = Store(self.path)
            try:
                barrier.wait(timeout=15)
                return peer.enroll(request)
            finally:
                peer.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(enroll, range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.store.list_devices()), 1)
        self.assertEqual(self.store._db.execute('SELECT remaining FROM codes').fetchone()[0], 0)

    def test_same_store_serializes_worker_threads(self):
        request, credential = self.pair()
        device = request['device_id']
        barrier = threading.Barrier(8)

        def ingest(_):
            barrier.wait(timeout=15)
            return self.store.ingest(device, credential, message(device))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(ingest, range(8)))
        self.assertEqual(sum(len(result['accepted']) for result in results), 1)
        self.assertEqual(sum(len(result['duplicates']) for result in results), 7)

    def test_concurrent_duplicate_and_conflicting_batches_are_atomic(self):
        request, credential = self.pair()
        device = request['device_id']
        barrier = threading.Barrier(6)

        def ingest(index):
            peer = Store(self.path)
            try:
                barrier.wait(timeout=15)
                try:
                    return peer.ingest(device, credential,
                        message(device, [record(1, measured=40 + index % 2), record(2)]))
                except ConflictError:
                    return 'conflict'
            finally:
                peer.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(ingest, range(6)))
        self.assertEqual(results.count('conflict'), 3)
        accepted = [r for r in results if isinstance(r, dict)]
        self.assertEqual(sum(len(r['accepted']) for r in accepted), 2)
        self.assertEqual(sum(len(r['duplicates']) for r in accepted), 4)
        self.assertEqual(self.store.get_device(device)['record_count'], 2)

    def test_revoke_serializes_against_already_started_ingest(self):
        request, credential = self.pair()
        device = request['device_id']
        peer = Store(self.path)
        self.addCleanup(peer.close)
        begun = threading.Event()
        # Hold the write lock: the other connection cannot authenticate early.
        with self.store._transaction() as db:
            db.execute("UPDATE devices SET credential_hash=NULL,status='revoked' WHERE device_id=?", (device,))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                def ingest():
                    begun.set()
                    return peer.ingest(device, credential, message(device))
                future = executor.submit(ingest)
                self.assertTrue(begun.wait(5))
                # Release transaction before waiting for the worker to finish.
                db.commit()
                with self.assertRaises(AuthenticationError):
                    future.result(timeout=15)
        self.assertEqual(self.store.get_device(device)['record_count'], 0)

    def test_actual_process_crash_mid_ingest_rolls_back_and_retry_commits_once(self):
        request, credential = self.pair()
        device = request['device_id']
        program = '''
import json, os, sys
from lifetime_server_store import Store
args = json.loads(sys.stdin.read())
store = Store(sys.argv[1])
store._db.create_function('crash_now', 0, lambda: os._exit(23))
store._db.execute("CREATE TEMP TRIGGER crash_mid_ingest BEFORE INSERT ON records WHEN NEW.seq=2 BEGIN SELECT crash_now(); END")
store.ingest(args['device'], args['credential'], args['message'])
'''
        payload = message(device, [record(1), record(2)])
        result = subprocess.run([sys.executable, '-c', program, str(self.path)],
                                input=json.dumps(dict(device=device, credential=credential, message=payload)),
                                text=True, capture_output=True, timeout=20,
                                cwd=Path(__file__).resolve().parent)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.store.close()
        self.store = Store(self.path)
        info = self.store.get_device(device)
        self.assertEqual(info['record_count'], 0)
        self.assertIsNone(info['last_received_at'])
        self.assertIsNotNone(self.store._db.execute('SELECT poll_hash FROM requests').fetchone()[0])
        self.assertEqual(self.store.ingest(device, credential, payload)['accepted'], [1, 2])
        self.assertEqual(self.store.ingest(device, credential, payload)['duplicates'], [1, 2])
        self.assertEqual(self.store._db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_actual_process_crash_mid_restore_preserves_live_rows_and_credentials(self):
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device, [record(1), record(2)]))
        backup = self.root / 'backup.sqlite'
        self.store.backup(backup)
        self.store.ingest(device, credential, message(device, [record(3)]))
        program = '''
import os, sys
from lifetime_server_store import Store
store = Store(sys.argv[1])
store._db.create_function('crash_now', 0, lambda: os._exit(24))
store._db.execute("CREATE TEMP TRIGGER crash_mid_restore BEFORE INSERT ON records WHEN NEW.seq=2 BEGIN SELECT crash_now(); END")
store.restore(sys.argv[2])
'''
        result = subprocess.run([sys.executable, '-c', program, str(self.path), str(backup)],
                                capture_output=True, text=True, timeout=20,
                                cwd=Path(__file__).resolve().parent)
        self.assertEqual(result.returncode, 24, result.stderr)
        self.assertEqual(self.store.get_device(device)['record_count'], 3)
        self.assertEqual(self.store.ingest(device, credential, message(device))['duplicates'], [1])


    def test_global_overview_counts_one_latest_stock_and_preserves_unknowns(self):
        empty = self.store.get_overview()
        self.assertIsNone(empty['totals'])
        self.assertEqual(empty['counts']['total'], 0)
        request, credential = self.pair()
        device = request['device_id']
        self.store.ingest(device, credential, message(device, [record(1, stock=100), record(2, stock=200)]))
        pending = self.request()
        self.store.enroll(pending)
        result = self.store.get_overview()
        self.assertEqual(result['counts']['total'], 2)
        self.assertEqual(result['counts']['pending'], 1)
        self.assertEqual(result['counts']['reporting'], 1)
        self.assertEqual(result['totals']['measured_seconds'], 200)
        self.assertEqual(result['known_devices'], 1)
        self.assertEqual(result['unknown_devices'], 1)
        self.assertLessEqual(len(result['recent_events']), 20)
        self.assertNotIn(credential, json.dumps(result))

    def test_reporting_configuration_and_refresh_are_persistent_hints(self):
        self.assertEqual(self.store.get_configuration()['reporting_interval'], 30)
        for bad in (True, 4, 3601, '30'):
            with self.assertRaises(StoreError):
                self.store.update_configuration(bad)
        self.store.update_configuration(3600)
        request, credential = self.pair()
        device = request['device_id']
        self.store.request_refresh(device)
        self.store.close()
        self.store = Store(self.path)
        result = self.store.ingest(device, credential, message(device))
        self.assertEqual(result['reporting_interval'], 3600)
        self.assertTrue(result['refresh'])
        self.assertFalse(self.store.ingest(device, credential, message(device))['refresh'])
        backup = self.root / 'configured.sqlite'
        self.store.backup(backup)
        self.store.update_configuration(10)
        self.store.restore(backup)
        self.assertEqual(self.store.get_configuration()['reporting_interval'], 3600)


class SpecsControlTests(unittest.TestCase):
    setUp = StoreTests.setUp
    request = StoreTests.request
    pair = StoreTests.pair

    def online(self, control=True):
        self.policy = dict(history=False, resources=True, control=control)
        request, self.credential = self.pair(policy=self.policy)
        self.device = request['device_id']
        self.sync()

    def sync(self, **extra):
        return self.store.ingest(self.device, self.credential,
                                 message(self.device, [], policy=self.policy, **extra))

    def test_command_lifecycle_cancel_expire_and_refusal(self):
        self.online(False)
        with self.assertRaises(StoreError):
            self.store.send_command(self.device, 'shutdown')
        cmd = self.store.send_command(self.device, 'refresh')
        self.assertIn('pending_command', self.store.list_devices()[0]['attention'])
        with self.assertRaises(ConflictError):
            self.store.send_command(self.device, 'refresh')
        reply = self.sync()
        self.assertEqual(reply['command']['id'], cmd['command_id'])
        self.assertEqual(self.sync()['command'], reply['command'])
        result = dict(id=cmd['command_id'], status='done', detail='Command accepted')
        ack = self.sync(command_result=result)
        self.assertEqual(ack['command_ack'], dict(id=cmd['command_id'], status='done'))
        self.assertNotIn('command', ack)
        self.assertNotIn('command', self.sync(command_result=result))
        cmd = self.store.send_command(self.device, 'refresh')
        self.assertEqual(self.store.cancel_command(cmd['command_id'])['status'], 'cancelled')
        self.assertNotIn('command', self.sync())
        cmd = self.store.send_command(self.device, 'refresh')
        with patch.object(storage, '_now', return_value=NOW+dt.timedelta(minutes=10)):
            self.assertNotIn('command', self.sync())
            self.assertEqual(self.store.get_device(self.device)['commands'][0]['status'], 'expired')
        with patch.object(storage, '_now', return_value=NOW+dt.timedelta(minutes=21)):
            with self.assertRaises(StoreError):
                self.store.send_command(self.device, 'refresh')
        self.store.revoke(self.device)
        with self.assertRaises(StoreError):
            self.store.send_command(self.device, 'refresh')

    def test_all_power_actions_and_revoked_consent(self):
        self.online()
        for action in ('sleep', 'restart', 'shutdown'):
            cmd = self.store.send_command(self.device, action)
            self.assertEqual(self.sync()['command']['action'], action)
            self.sync(command_result=dict(id=cmd['command_id'], status='failed', detail='Power action failed'))
        cmd = self.store.send_command(self.device, 'restart')
        self.sync()
        self.policy['control'] = False
        reply = self.store.ingest(self.device, self.credential, message(self.device, [], policy=self.policy, version=2))
        self.assertNotIn('command', reply)
        self.assertEqual(self.store.get_device(self.device)['commands'][0]['status'], 'cancelled')

    def test_reports_and_command_checks_read_indexes_not_whole_tables(self):
        """A sync with readings, and the live hub's command checks, must not read every performance sample or every
        command ever sent: at 500 computers a week of samples is a million rows, read on every report."""
        self.online()
        self.store.send_command(self.device, 'refresh')
        self.store._samples_trimmed_at = float('inf')      # the hourly whole-table trim is the deliberate exception
        statements = []
        self.store._db.set_trace_callback(statements.append)
        try:
            self.assertEqual(self.store.pending_command_devices(), {self.device})
            self.assertEqual(self.store.take_command(self.device)['action'], 'refresh')
            self.assertEqual(self.store.pending_command_devices(), set())    # delivered: no longer waiting
            self.assertIsNone(self.store.take_command(self.device))
            perf = dict(cpu_percent=1.0, ram_used_bytes=1, disk_free_bytes=1)
            self.sync(readings=dict(observed_at=NOW.isoformat(), performance=perf))
        finally:
            self.store._db.set_trace_callback(None)
        checked, scans = 0, []
        for sql in statements:
            if sql.lstrip().upper().startswith(('SELECT', 'DELETE', 'UPDATE')) and (
                    'performance_samples' in sql or 'commands' in sql):
                checked += 1
                plan = [row[3] for row in self.store._db.execute('EXPLAIN QUERY PLAN ' + sql)]
                if 'SCAN performance_samples' in plan or 'SCAN commands' in plan:
                    scans.append(sql)
        self.assertGreater(checked, 3)
        self.assertEqual(scans, [])

    def test_week_old_samples_go_per_computer_and_silent_computers_at_the_hourly_trim(self):
        self.online()
        silent, _ = self.pair(policy=self.policy)
        insert = 'INSERT INTO performance_samples(device_id,observed_at,cpu_percent) VALUES (?,?,1)'
        old = storage._stamp(NOW - dt.timedelta(days=8))
        perf = dict(cpu_percent=1.0, ram_used_bytes=1, disk_free_bytes=1)
        count = lambda device: self.store._db.execute(
            'SELECT count(*) FROM performance_samples WHERE device_id=?', (device,)).fetchone()[0]
        self.sync(readings=dict(observed_at=NOW.isoformat(), performance=perf))     # the first report trims everything
        for device in (self.device, silent['device_id']):
            self.store._db.execute(insert, (device, old))
        later = NOW + dt.timedelta(minutes=10)
        with patch.object(storage, '_now', return_value=later):
            self.sync(readings=dict(observed_at=later.isoformat(), performance=perf))
        self.assertEqual((count(self.device), count(silent['device_id'])), (2, 1))   # own week-old sample gone at once
        self.store._samples_trimmed_at = 0.0                                          # an hour later
        later = NOW + dt.timedelta(minutes=20)
        with patch.object(storage, '_now', return_value=later):
            self.sync(readings=dict(observed_at=later.isoformat(), performance=perf))
        self.assertEqual(count(silent['device_id']), 0)

    def test_history_downsample_prune_attention_and_hours(self):
        self.online()
        specs = dict(cpu_name='CPU', ram_bytes=1000, disks=[dict(name='/', total_bytes=1000, free_bytes=50)],
                     mac_addresses=['AA:BB:CC:DD:EE:FF'])
        perf = dict(cpu_percent=42, ram_used_bytes=500, disk_free_bytes=50, battery_health_percent=60,
                    battery_percent=80, power_watts=12, temperature_c=90)
        for minutes in (0, 1, 4, 5, 6, 10):
            now = NOW+dt.timedelta(minutes=minutes)
            with patch.object(storage, '_now', return_value=now):
                self.sync(readings=dict(observed_at=now.isoformat(), specs=specs, performance=perf))
        with patch.object(storage, '_now', return_value=NOW+dt.timedelta(minutes=10)):
            detail = self.store.get_device(self.device)
            self.assertEqual(len(detail['performance_history']), 3)
            self.assertEqual(detail['performance_history'][0]['ram_percent'], 50)
            self.assertEqual(detail['performance_history'][0]['disk_free_percent'], 5)
            self.assertEqual(detail['specs'], specs)
            row = self.store.list_devices()[0]
            self.assertEqual(row['summary']['cpu_name'], 'CPU')
            self.assertEqual(set(row['attention']), {'disk_low', 'battery_worn', 'hot'})
        with patch.object(storage, '_now', return_value=NOW+dt.timedelta(minutes=21)):
            self.assertIn('offline', self.store.list_devices()[0]['attention'])
        for hours in (0, 169, True, 1.5):
            with self.assertRaises(StoreError):
                self.store.get_device(self.device, hours=hours)
        with patch.object(storage, '_now', return_value=NOW+dt.timedelta(days=8)):
            self.sync(readings=dict(observed_at=(NOW+dt.timedelta(days=8)).isoformat(), performance=perf))
            detail = self.store.get_device(self.device, hours=168)
            self.assertEqual(len(detail['performance_history']), 1)
            self.assertEqual(detail['specs'], specs)
            self.assertEqual(self.store._db.execute('SELECT count(*) FROM performance_samples').fetchone()[0], 1)
        # Consent revocation also scrubs stored history.
        self.policy['resources'] = False
        self.store.ingest(self.device, self.credential, message(self.device, [], policy=self.policy, version=2))
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM performance_samples').fetchone()[0], 0)

    def test_server_names_are_canonical_bounded_and_survive_backup(self):
        self.assertEqual(self.store.server_names(), dict(names=[]))
        self.assertEqual(self.store.server_names(['lifetime.school.local', '192.0.2.10'])['names'],
                         ['lifetime.school.local', '192.0.2.10'])
        for bad in (['Upper.Case'], ['a.local', 'a.local'], ['bad name'], 'a.local', [f'n{i}.local' for i in range(17)]):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(StoreError):
                self.store.server_names(bad)
        backup = self.root/'names-backup.sqlite'
        self.store.backup(backup)
        self.store.server_names([])
        self.store.restore(backup)
        self.assertEqual(self.store.server_names()['names'], ['lifetime.school.local', '192.0.2.10'])

    def test_last_problem_server_log_and_migration_from_v8(self):
        import lifetime_protocol as protocol
        self.online()
        problem = dict(kind='timeout', at='2026-11-15T01:00:00Z')
        self.sync(problem=problem)
        self.assertEqual(self.store.get_device(self.device)['last_problem'], dict(kind='timeout', at='2026-11-15T01:00:00.000000+00:00'))
        self.sync(problem=dict(kind='refused', at='2026-11-14T01:00:00Z'))         # an older report never replaces a newer one
        self.assertEqual(self.store.list_devices()[0]['last_problem']['kind'], 'timeout')
        for bad in (dict(kind='Timeout', at=problem['at']), dict(kind='http-999', at=problem['at']),
                    dict(kind='timeout', at='yesterday'), dict(kind='timeout'), dict(problem, text='secret')):
            with self.subTest(bad=bad), self.assertRaises(protocol.ProtocolError):
                self.sync(problem=bad)
        # Refusals: attached to a computer only when its credential proves it; otherwise anonymous.
        self.store.note_refused('/v1/sync', 'invalid', self.device, self.credential)
        self.assertEqual(self.store.get_device(self.device)['last_problem']['kind'], 'rejected')
        self.store.note_refused('/v1/sync', 'invalid', self.device, 'x' * 43)
        self.store.note_refused('/v1/sync', 'invalid', self.device, 'x' * 43)
        self.store.note_refused('/v1/enroll', 'code')
        log = self.store.server_log()
        self.assertEqual([(e['kind'], e['category'], e['device_id'], e['count']) for e in log],
                         [('enrollment-refused', 'code', None, 1), ('report-rejected', 'invalid', None, 2),
                          ('report-rejected', 'invalid', self.device, 1)])
        self.assertNotIn(self.credential, json.dumps(log))
        with self.assertRaises(StoreError):
            self.store.log_event('report-rejected', 'free text')
        for _ in range(storage.MAX_LOG + 5):
            self.store.log_event('server-started' if _ % 2 else 'server-stopped')
        self.assertLessEqual(len(self.store.server_log(limit=500)) + len(self.store.server_log(limit=500, offset=500)), storage.MAX_LOG + 1)
        backup = self.root/'problem-backup.sqlite'
        self.store.backup(backup)
        self.store.restore(backup)
        self.assertEqual(self.store.get_device(self.device)['last_problem']['kind'], 'rejected')
        # A version 8 database gains the columns and the log on open.
        self.store._db.execute('ALTER TABLE devices DROP COLUMN last_problem')
        self.store._db.execute('ALTER TABLE devices DROP COLUMN last_problem_at')
        self.store._db.execute('DROP TABLE server_log')
        self.store._db.execute('PRAGMA user_version=8')
        self.store.close()
        self.store = Store(self.path)
        self.assertIsNone(self.store.get_device(self.device)['last_problem'])
        self.assertEqual(self.store.server_log(), [])
        self.store.backup(backup)

    def test_migrate_v6_and_backup_with_commands(self):
        self.online()
        self.sync(readings=dict(observed_at=STAMP, system='Windows'))
        self.store._db.execute('DROP TABLE commands')
        self.store._db.execute('DROP TABLE performance_samples')
        self.store._db.execute('PRAGMA user_version=6')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get_device(self.device)['readings']['system'], 'Windows')
        self.store.send_command(self.device, 'sleep')
        backup = self.root/'specs-backup.sqlite'
        self.store.backup(backup)
        self.store.restore(backup)
        self.assertEqual(self.store.get_device(self.device)['commands'][0]['status'], 'cancelled')

    def test_wake_only_offline_with_consent_and_macs(self):
        self.online()
        self.sync(readings=dict(observed_at=STAMP, specs=dict(mac_addresses=['AA:BB:CC:DD:EE:FF'])))
        free = []

        def sending(*_, **__):
            # Bug B1: the packets (and a PowerShell start on Windows) went out inside the transaction, under the
            # store lock, so every other request waited. Another thread must be able to take the lock meanwhile.
            self.assertFalse(self.store._db.in_transaction)
            thread = threading.Thread(target=lambda: free.append(self.store._lock.acquire(timeout=2) and (self.store._lock.release() or True)))
            thread.start()
            thread.join()
            return ['192.0.2.255']
        with patch('lifetime_wol.send_magic_packet', side_effect=sending) as send, \
                patch('lifetime_wol.local_broadcasts', side_effect=sending) as broadcasts:
            with self.assertRaises(StoreError):
                self.store.wake(self.device)
            with patch.object(storage, '_now', return_value=NOW+dt.timedelta(minutes=11)):
                self.assertEqual(self.store.wake(self.device)['sent'], 1)
            send.assert_called_once_with('AA:BB:CC:DD:EE:FF', broadcasts=['192.0.2.255'])
            broadcasts.assert_called_once_with()         # looked up once, not once per address
        self.assertEqual(free, [True, True])
        self.assertEqual(self.store._db.execute("SELECT count(*) FROM events WHERE kind='wake'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
