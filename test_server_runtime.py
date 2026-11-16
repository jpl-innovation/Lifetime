"""Real local HTTPS/SQLite integrations; Windows ACL and SCM calls are mocked.

These tests explicitly mock the two protection helpers for temporary fixtures
on Windows. Production has no ACL bypass. Separate tests assert ACL arguments
and fail-closed startup. No Windows SCM integration is claimed here.
"""
import concurrent.futures
import contextlib
import copy
import datetime as dt
import http.client
import io
import json
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid

from lifetime_admin import AdminClient
import lifetime_permissions as permissions
from lifetime_protocol import MAX_BYTES, canonical, decode
import lifetime_server as server
import lifetime_tls as tls


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Explicit ACL mocks for non-elevated Windows fixtures only.
        if sys.platform == 'win32':
            self.acl = self.enterContext(patch.object(permissions, 'windows_acl'))
        self.runtime = server.ServerRuntime(self.root, address='https://127.0.0.1:8443',
                                            host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(lambda: self.runtime.stop())
        self.admin = AdminClient(self.root, urlsplit(self.runtime.admin_address).port, timeout=3)
        self.pem = (self.root / 'server-cert.pem').read_text(encoding='ascii')
        self.bundle = tls.decode_bundle(tls.encode_bundle(self.runtime.public_address, 'unused', self.pem))

    def public(self, operation, value, credential=None):
        return tls.request_json(self.bundle, '/v1/' + operation, value, credential, timeout=3)

    def status(self, status, operation, value, credential=None):
        with self.assertRaises(tls.HTTPSRequestError) as raised:
            self.public(operation, value, credential)
        self.assertEqual(raised.exception.status, status)

    def enrollment(self, code=None, device=None):
        if code is None:
            code = self.admin.call('code')['code']
        return dict(version=1, request_id=str(uuid.uuid4()),
                    device_id=device or str(uuid.uuid4()), code=code,
                    poll_token=secrets.token_urlsafe(32),
                    identity={'display_name': 'M\u00e1y th\u1eed nghi\u1ec7m'},
                    policy={'history': False, 'resources': False})

    def claim(self, request):
        return self.public('claim', {key: request[key] for key in ('request_id', 'poll_token')})

    def pair(self, device=None):
        request = self.enrollment(device=device)
        self.assertEqual(self.public('enroll', request)['status'], 'pending')
        self.admin.call('approve', request_id=request['request_id'])
        return request, self.claim(request)['credential']

    def message(self, request, seq=1):
        return dict(version=1, device_id=request['device_id'], policy_version=1,
                    policy=dict(request['policy']), records=[{
                        'seq': seq, 'observed_at': '2026-11-01T12:00:00+00:00',
                        'awake_seconds': 60, 'measured_seconds': 40, 'imported_seconds': 20,
                        'totals': {'measured_seconds': 1000 + seq,
                                   'imported_seconds': 200, 'baseline_seconds': 300}}])

    def wire(self, raw, admin=False):
        if admin:
            connection = socket.create_connection(('127.0.0.1', self.admin.port), 3)
        else:
            address = urlsplit(self.runtime.public_address)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cadata=self.pem)
            context.verify_flags |= ssl.VERIFY_X509_STRICT
            raw_socket = socket.create_connection((address.hostname, address.port), 3)
            connection = context.wrap_socket(raw_socket, server_hostname=address.hostname)
        with connection:
            connection.sendall(raw)
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response.status, response.read(MAX_BYTES + 1), dict(response.headers)

    def admin_raw(self, path, value, token=None):
        raw = canonical(value)
        token = token if token is not None else (self.root / 'admin-token').read_text()
        return self.wire((f'POST {path} HTTP/1.1\r\nHost: localhost\r\n'
                          f'Authorization: Bearer {token}\r\nContent-Type: application/json\r\n'
                          f'Content-Length: {len(raw)}\r\n\r\n').encode() + raw, admin=True)

    def test_pinned_enrollment_admin_approval_ingest_retry_conflict(self):
        request = self.enrollment()
        pending = self.public('enroll', request)
        self.assertEqual(pending, self.public('enroll', request))
        self.assertEqual(self.claim(request)['status'], 'pending')
        self.assertNotIn('credential', self.claim(request))
        devices = self.admin.call('devices')
        self.assertEqual(devices[0]['pending_requests'][0]['request_id'], request['request_id'])
        self.admin.call('approve', request_id=request['request_id'])
        credential = self.claim(request)['credential']
        message = self.message(request)
        accepted = self.public('sync', message, credential)
        self.assertEqual(accepted['accepted'], [1])
        self.assertEqual(self.public('sync', message, credential)['duplicates'], [1])
        self.assertEqual(self.public('sync', self.message(request, 3), credential)['accepted'], [3])
        self.assertEqual(self.public('sync', self.message(request, 2), credential)['accepted'], [2])
        changed = copy.deepcopy(message)
        changed['records'][0]['totals']['measured_seconds'] = 99
        self.status(409, 'sync', changed, credential)
        detail = self.admin.call('device', device_id=request['device_id'])
        self.assertEqual(detail['record_count'], 3)
        self.assertEqual(detail['totals']['measured_seconds'], 1003)
        self.assertEqual(detail['retained_usage']['awake_seconds'], 180)

    def test_authentication_revocation_and_device_isolation(self):
        first, credential = self.pair()
        second, second_credential = self.pair()
        message = self.message(first)
        self.status(401, 'sync', message)
        self.status(403, 'sync', message, secrets.token_urlsafe(32))
        self.status(403, 'sync', self.message(second), credential)
        self.admin.call('revoke', device_id=first['device_id'])
        self.status(403, 'sync', message, credential)
        self.assertEqual(self.public('sync', self.message(second), second_credential)['accepted'], [1])
        again, new_credential = self.pair(device=first['device_id'])
        self.assertEqual(self.public('sync', self.message(again), new_credential)['accepted'], [1])

    def test_invalid_denied_expired_used_and_throttled_enrollment(self):
        self.status(403, 'enroll', self.enrollment(code='wrong'))
        request = self.enrollment()
        self.public('enroll', request)
        self.status(403, 'enroll', self.enrollment(code=request['code']))
        self.admin.call('deny', request_id=request['request_id'])
        self.assertEqual(self.claim(request)['status'], 'denied')
        self.assertNotIn('credential', self.claim(request))
        claim = dict(request_id=request['request_id'], poll_token='x' * 43)
        self.status(403, 'claim', claim)
        import lifetime_server_store as storage
        code = self.admin.call('code', ttl=1)['code']
        with patch.object(storage, '_now', return_value=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=2)):
            self.status(403, 'enroll', self.enrollment(code=code))
        for _ in range(7):
            self.status(403, 'enroll', self.enrollment(code='wrong'))
        self.status(429, 'enroll', self.enrollment(code='wrong'))

    def test_concurrent_clients_bounded_use_code(self):
        code = self.admin.call('code', uses=2)['code']
        requests = [self.enrollment(code=code) for _ in range(6)]
        def enroll(request):
            try:
                return self.public('enroll', request)['status']
            except tls.HTTPSRequestError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(enroll, requests))
        self.assertEqual(results.count('pending'), 2)
        self.assertEqual(results.count(403), 4)

    def test_admin_is_separate_loopback_only_and_requires_token(self):
        self.assertEqual(urlsplit(self.runtime.admin_address).hostname, '127.0.0.1')
        listener = self.runtime._servers[1]
        self.assertFalse(listener.verify_request(None, ('192.0.2.3', 1234)))
        self.assertTrue(listener.verify_request(None, ('127.0.0.1', 1234)))
        token = (self.root / 'admin-token').read_text()
        for operation in server._ADMIN:
            self.status(400 if operation == 'live' else 404, operation, {}, token)
            self.assertEqual(self.admin_raw('/v1/' + operation, {}, token='wrong')[0], 401)
        status, raw, _ = self.wire(b'POST /v1/devices HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}', admin=True)
        self.assertEqual(status, 401)
        self.assertEqual(decode(raw), {'error': 'Request failed'})
        self.assertEqual(self.admin_raw('/v1/enroll', {})[0], 404)

    def test_live_transport_limits_authentication_and_revoke(self):
        from lifetime_live import FIELDS
        request = self.enrollment()
        request['policy']['resources'] = True
        self.public('enroll', request)
        self.admin.call('approve', request_id=request['request_id'])
        credential = self.claim(request)['credential']
        message = dict(device_id=request['device_id'], sample=dict.fromkeys(FIELDS))
        self.status(401, 'live', message)
        self.status(403, 'live', message, 'x'*40)
        self.status(413, 'live', dict(message, padding='x'*4096), credential)
        self.status(400, 'live', dict(message, extra=True), credential)
        self.assertEqual(self.public('live', message, credential)['interval'], 10)
        self.status(429, 'live', message, credential)
        result = self.admin.call('live', device_id=request['device_id'])
        self.assertEqual(len(result['ring']), 1)
        self.admin.call('revoke', device_id=request['device_id'])
        self.status(403, 'live', message, credential)
        self.assertEqual(self.admin.call('live')['devices'], [])

    def test_unknown_fields_and_methods_rejected(self):
        for operation, value in [('devices', {'username': 'administrator'}),
                                 ('device', {'device_id': str(uuid.uuid4()), 'records': []}),
                                 ('code', {'ttl': 600, 'extra': 'secret'}),
                                 ('approve', {'device_id': str(uuid.uuid4())})]:
            self.assertEqual(self.admin_raw('/v1/' + operation, value)[0], 400)
        request = self.enrollment()
        self.status(400, 'enroll', dict(request, extra='secret'))
        self.status(400, 'claim', {'request_id': request['request_id'], 'poll_token': request['poll_token'], 'version': 1})
        self.assertEqual(self.wire(b'GET /v1/enroll HTTP/1.1\r\nHost: localhost\r\n\r\n')[0], 405)

    def test_whitelist_passes_pagination_filters_exactly(self):
        with patch.object(self.runtime._store, 'get_overview', return_value={'devices': 1}) as query:
            self.assertEqual(self.admin.call('overview')['devices'], 1)     # plus the alert badge counts
        query.assert_called_once_with()
        arguments = dict(limit=2, offset=3, search='M\u00e1y', group='Sales', status='approved')
        with patch.object(self.runtime._store, 'list_devices', return_value=[]) as query:
            self.assertEqual(self.admin.call('devices', **arguments), [])
        query.assert_called_once_with(**arguments)
        arguments = dict(device_id=str(uuid.uuid4()), limit=2, after_seq=7)
        with patch.object(self.runtime._store, 'get_device', return_value={'records': [], 'truncated': False}) as query:
            self.assertEqual(self.admin.call('device', **arguments)['records'], [])
        query.assert_called_once_with(**arguments)
        with patch.object(self.runtime._store, 'get_configuration', return_value={'reporting_interval': 30}) as query:
            self.assertEqual(self.admin.call('configuration'), {'reporting_interval': 30})
        query.assert_called_once_with()
        with patch.object(self.runtime._store, 'update_configuration', return_value={'reporting_interval': 120}) as query:
            self.assertEqual(self.admin.call('set_configuration', reporting_interval=120),
                             {'reporting_interval': 120})
        query.assert_called_once_with(reporting_interval=120)
        device_id = str(uuid.uuid4())
        with patch.object(self.runtime._store, 'request_refresh', return_value={'device_id': device_id,
                                                                                'refresh_requested': True}) as query:
            self.assertEqual(self.admin.call('refresh', device_id=device_id),
                             {'device_id': device_id, 'refresh_requested': True})
        query.assert_called_once_with(device_id=device_id)

    def test_admin_identity_retention_backup_restore(self):
        request, credential = self.pair()
        self.public('sync', self.message(request), credential)
        identity = {'display_name': 'M\u00e1y COBACOM', 'department': 'Support'}
        updated = self.admin.call('identity', device_id=request['device_id'], identity=identity)
        self.assertEqual(updated['identity']['display_name'], identity['display_name'])
        backup = str(self.root / 'snapshot.sqlite')
        self.assertEqual(Path(self.admin.call('backup', path=backup)['path']), Path(backup).resolve())
        self.assertEqual(self.admin.call('retain', before='2026-11-02T00:00:00Z')['deleted'], 1)
        self.assertEqual(self.public('sync', self.message(request), credential)['retired'], [1])
        self.admin.call('restore', path=backup)
        self.status(403, 'sync', self.message(request), credential)
        self.assertEqual(self.admin.call('device', device_id=request['device_id'])['record_count'], 1)
        for name in ('admin-token', 'server-key.pem', 'server-cert.pem', 'server.sqlite', 'server.sqlite-wal'):
            self.assertEqual(self.admin_raw('/v1/backup', {'path': str(self.root / name)})[0], 400)

    def test_backup_and_restore_carry_monitoring_history_beside_the_database(self):
        request, credential = self.pair()
        self.public('sync', self.message(request), credential)
        self.runtime.monitor.tick()
        self.admin.call('set_schedule', scope='group', key='Lab', kind='always')
        backup = self.root / 'snapshot.sqlite'
        result = self.admin.call('backup', path=str(backup))
        companion = self.root / 'snapshot.monitoring.sqlite'
        self.assertEqual(Path(result['monitoring']), companion.resolve())
        self.admin.call('delete_schedule', scope='group', key='Lab')
        restored = self.admin.call('restore', path=str(backup))
        self.assertEqual(restored['monitoring'], 'restored')
        self.assertIn(('group', 'lab'), [(row['scope'], row['key']) for row in self.admin.call('schedules')])
        history = self.admin.call('presence', device_id=request['device_id'])
        self.assertEqual(history[-1]['state'], 'online')
        self.assertIsNotNone(history[-1]['end_at'])     # it ends at the backup; what followed is not invented
        # A damaged monitoring file stops the restore before the server database changes.
        self.admin.call('set_configuration', reporting_interval=120)
        companion.write_bytes(b'not a database' * 100)
        self.assertEqual(self.admin_raw('/v1/restore', {'path': str(backup)})[0], 400)
        self.assertEqual(self.admin.call('configuration')['reporting_interval'], 120)
        # Without the file beside it, the database is restored and monitoring stays as it is.
        companion.unlink()
        self.assertEqual(self.admin.call('restore', path=str(backup))['monitoring'], 'not-included')
        self.assertIn(('group', 'lab'), [(row['scope'], row['key']) for row in self.admin.call('schedules')])

    def test_alerts_through_the_admin_api_with_reserved_files_and_no_secret_in_replies(self):
        request, credential = self.pair()
        self.public('sync', self.message(request), credential)
        self.assertEqual(self.admin.call('overview')['alerts']['active'], 0)
        self.runtime.monitor.tick()
        alerts = self.admin.call('device', device_id=request['device_id'])['alerts']
        self.assertEqual((alerts['incidents'], alerts['status']['presence']), ([], 'online'))
        measured = self.admin.call('availability', device_ids=[request['device_id']])['devices'][0]
        self.assertEqual((measured['device_id'], measured['bad_s']), (request['device_id'], 0))
        self.assertEqual(self.admin_raw('/v1/availability', {'device_ids': []})[0], 400)
        saved = self.admin.call('save_check', check=dict(name='Printer', kind='tcp', host='192.168.1.50', port=9100))
        self.assertEqual(saved[0]['name'], 'Printer')
        self.assertEqual(self.runtime.checker._checks[saved[0]['id']]['port'], 9100)     # the worker has it
        self.assertEqual(self.admin.call('set_check_scopes', scopes=['private', 'public']), ['private', 'public'])
        self.assertEqual(self.runtime.checker.policy.refuse('127.0.0.1', self.runtime._servers[1].server_port),
                         'admin-port')
        self.assertIn(self.runtime._servers[1].server_port, self.runtime.checker.policy.blocked_ports)
        self.assertEqual(self.admin_raw('/v1/save_check', {'check': {'name': 'x', 'kind': 'http',
                                                                    'url': 'http://u:p@nas/'}})[0], 400)
        self.assertEqual(self.admin.call('delete_check', check_id=saved[0]['id']), [])
        rules = self.admin.call('set_alert_rule', rule='disk_low', threshold=15)
        self.assertEqual(next(rule for rule in rules if rule['rule'] == 'disk_low')['threshold'], 15)
        self.assertEqual(self.admin_raw('/v1/set_alert_rule', {'rule': 'disk_low', 'threshold': 500})[0], 400)
        self.assertEqual(self.admin.call('incidents', state='active'), [])
        settings = self.admin.call('set_notifications', email_enabled=True, smtp_host='mail.example', sender='a@b.example',
                                   recipients=['c@d.example'], password='S3cret-pass')
        self.assertTrue(settings['password_set'])
        self.assertNotIn('S3cret', json.dumps(settings) + json.dumps(self.admin.call('notifications')))
        for name in ('monitoring.sqlite', 'monitoring.sqlite-wal', 'alert-secrets.bin'):
            self.assertEqual(self.admin_raw('/v1/backup', {'path': str(self.root / name)})[0], 400)
        self.admin.call('delete', device_id=request['device_id'])
        self.assertEqual(self.admin.call('presence', device_id=request['device_id']), [])

    def test_real_pinned_tls_wrong_hostname_trust_anchor_and_pin_mismatch(self):
        # A different trust anchor fails before HTTP/application data is sent.
        other = self.root / 'other'
        tls.generate_certificate(other, 'https://localhost')
        pem = (other / 'server-cert.pem').read_text()
        wrong = tls.decode_bundle(tls.encode_bundle(self.runtime.public_address, 'unused', pem))
        with self.assertRaises(ssl.SSLCertVerificationError):
            tls.request_json(wrong, '/v1/enroll', {}, timeout=3)
        wrong = dict(self.bundle, address=self.runtime.public_address.replace('127.0.0.1', 'localhost'))
        with self.assertRaises(ssl.SSLCertVerificationError):
            tls.request_json(wrong, '/v1/enroll', {}, timeout=3)
        key = tls.spki_pin(ssl.PEM_cert_to_DER_cert(self.pem))
        with patch.object(tls, 'spki_pin', side_effect=[key, '0' * 64]):
            # The stored certificate's key is the pin; the peer's key (second call) must equal it.
            with self.assertRaisesRegex(ssl.SSLCertVerificationError, 'pinned key'):
                tls.request_json(self.bundle, '/v1/enroll', {}, timeout=3)

    def test_clean_restart_reuses_identity_token_and_records(self):
        request, credential = self.pair()
        self.public('sync', self.message(request), credential)
        before = {name: (self.root / name).read_bytes() for name in
                  ('admin-token', 'server-cert.pem', 'server-key.pem')}
        self.runtime.start()  # idempotent
        self.runtime.stop()
        self.runtime.stop()
        self.assertEqual(self.runtime._slots._value, server.MAX_CONNECTIONS)
        with self.assertRaises(RuntimeError):
            _ = self.runtime.public_address
        self.runtime.start()
        self.admin = AdminClient(self.root, urlsplit(self.runtime.admin_address).port)
        self.bundle['address'] = self.runtime.public_address
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertEqual(self.public('sync', self.message(request), credential)['duplicates'], [1])
        self.assertEqual(self.admin.call('device', device_id=request['device_id'])['record_count'], 1)

    def test_admin_bind_failure_cleans_up_without_shutdown_of_unstarted_server(self):
        self.runtime.stop()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen(1)
            self.runtime.admin_port = occupied.getsockname()[1]
            started = time.monotonic()
            with self.assertRaises(OSError):
                self.runtime.start()
            self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.runtime._servers, [])
        self.assertEqual(self.runtime._threads, [])
        self.assertIsNone(self.runtime._store)

    def test_new_address_at_start_renews_from_the_same_key_and_keeps_the_old_certificate(self):
        self.runtime.stop()
        before = (self.root / 'server-cert.pem').read_text()
        key = (self.root / 'server-key.pem').read_bytes()
        self.runtime = server.ServerRuntime(self.root, address='https://renamed.example:8443',
                                            host='127.0.0.1', port=0)
        self.runtime.start()
        info = tls.certificate_info((self.root / 'server-cert.pem').read_text())
        self.assertEqual(info['names'], ['renamed.example'])
        self.assertEqual(info['key'], tls.certificate_info(before)['key'])
        self.assertEqual((self.root / 'server-key.pem').read_bytes(), key)
        self.assertEqual((self.root / 'server-cert.previous.pem').read_text(), before)
        self.assertFalse((self.root / 'server-cert.new.pem').exists())

    def test_wrong_identity_fails_start_preserves_it_and_closes_listeners(self):
        self.runtime.stop()
        before = (self.root / 'server-cert.pem').read_bytes()
        started = time.monotonic()
        with patch.object(server.tls, 'renew_certificate', side_effect=ValueError('no')):
            self.runtime = server.ServerRuntime(self.root, address='https://wrong.example:8443',
                                                host='127.0.0.1', port=0)
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.runtime.start()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((self.root / 'server-cert.pem').read_bytes(), before)
        self.assertEqual(self.runtime._servers, [])
        self.assertEqual(self.runtime._threads, [])
        self.assertIsNone(self.runtime._store)

    def test_admin_names_renew_from_the_same_key_and_are_validated(self):
        before = tls.certificate_info(self.pem)
        request, credential = self.pair()           # this computer now pins the key
        info = self.admin.call('names', names=['LOCALHOST', 'lifetime.school.local', '192.0.2.10', 'localhost'])
        self.assertEqual(info['server_names'], ['localhost', 'lifetime.school.local', '192.0.2.10'])
        self.assertEqual(set(info['certificate']['names']), {'127.0.0.1', 'localhost', 'lifetime.school.local', '192.0.2.10'})
        self.assertEqual(info['key_fingerprint'], before['key'])
        self.assertNotEqual(info['fingerprint'], tls._certificate(self.pem))       # new certificate, same key
        self.assertIsNone(info['certificate']['warning'])
        # The paired computer keeps working through the renewed certificate, which it learns.
        self.assertEqual(self.public('sync', self.message(request), credential)['accepted'], [1])
        self.assertEqual(self.bundle['certificate_pem'], (self.root / 'server-cert.pem').read_text())
        for bad in (['bad name'], ['https://x.local'], [f'n{i}.local' for i in range(17)], 'localhost', [None]):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError):
                self.admin.call('names', names=bad)
        self.assertEqual(self.admin.call('names', names=[])['server_names'], [])
        self.assertEqual(self.admin.call('server_info')['certificate']['names'], ['127.0.0.1'])

    def test_capabilities_only_for_computers_that_ask_and_validation_stays_strict(self):
        from lifetime_protocol import CAPABILITIES
        self.assertEqual(self.public('enroll', self.enrollment())['capabilities'], list(CAPABILITIES))
        request, credential = self.pair()
        self.assertEqual(self.public('sync', self.message(request), credential)['capabilities'], list(CAPABILITIES))
        # An older computer does not ask, so its strict reply check never meets the new field.
        raw = canonical(self.message(request, seq=2))
        status, body, _ = self.wire(('POST /v1/sync HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                                     f'Authorization: Bearer {credential}\r\nContent-Type: application/json\r\n'
                                     f'Content-Length: {len(raw)}\r\n\r\n').encode() + raw)
        self.assertEqual(status, 200)
        self.assertEqual(decode(body)['accepted'], [2])
        self.assertNotIn('capabilities', decode(body))
        # Advertising never loosens validation: an unknown field is still refused as a whole.
        self.status(400, 'sync', dict(self.message(request, seq=3), unknown=True), credential)

    def test_server_log_and_last_problem_from_refused_reports_without_secrets(self):
        request, credential = self.pair()
        self.status(400, 'sync', dict(self.message(request), records=[{'seq': 1, 'secret-field': 'payload-text'}]), credential)
        self.status(403, 'sync', self.message(request), 'y' * 43)
        bad = self.enrollment(code='LTS-0000-0000-0000-0000-0000')
        self.status(403, 'enroll', bad)
        device = self.admin.call('device', device_id=request['device_id'])
        self.assertEqual(device['last_problem']['kind'], 'rejected')
        log = self.admin.call('server_log')
        self.assertEqual([(e['kind'], e['category'], e['device_id']) for e in log[:3]],
                         [('enrollment-refused', 'code', None), ('report-rejected', 'credential', None),
                          ('report-rejected', 'invalid', request['device_id'])])
        self.assertEqual(log[-1]['kind'], 'server-started')
        text = json.dumps(log)
        for secret in (credential, 'y' * 43, 'payload-text', request['poll_token'], 'LTS-0000'):
            self.assertNotIn(secret, text)
        self.runtime.stop()
        self.runtime.start()
        self.admin = AdminClient(self.root, urlsplit(self.runtime.admin_address).port, timeout=3)
        kinds = [e['kind'] for e in self.admin.call('server_log', limit=3)]
        self.assertEqual(kinds[:2], ['server-started', 'server-stopped'])

    def test_failed_renewal_keeps_the_current_certificate_and_warns(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.runtime.clock = lambda: now + dt.timedelta(days=370)   # 27 days left: renewal is due ...
        self.assertFalse(self.runtime._renew_certificate())          # ... but a certificate from the future fails the check
        self.assertEqual((self.root / 'server-cert.pem').read_text(), self.pem)
        self.assertFalse((self.root / 'server-cert.new.pem').exists())
        self.assertEqual(self.admin.call('server_info')['certificate']['warning'], 'renewal-failed')
        request, credential = self.pair()       # the current certificate is still served
        self.runtime.clock = lambda: dt.datetime.now(dt.timezone.utc)
        info = self.admin.call('names', names=['localhost'])            # the next renewal succeeds ...
        self.assertIsNone(info['certificate']['warning'])               # ... and clears the warning
        self.assertEqual(self.public('sync', self.message(request), credential)['accepted'], [1])

    def test_partially_started_listeners_are_joined_on_start_failure(self):
        self.runtime.stop()
        original = threading.Thread.start
        starts = []
        def fail_second_listener(thread):
            if thread.name == 'Lifetime-listener':
                starts.append(thread)
                if len(starts) == 2:
                    raise RuntimeError('Simulated thread start failure')
            original(thread)
        with patch.object(threading.Thread, 'start', fail_second_listener):
            with self.assertRaises(RuntimeError):
                self.runtime.start()
        self.assertEqual(len(starts), 2)
        self.assertFalse(starts[0].is_alive())
        self.assertEqual(self.runtime._servers, [])
        self.assertIsNone(self.runtime._store)

    def test_console_signal_stops_real_runtime_and_restores_handlers(self):
        self.runtime.stop()
        numbers = (server.signal.SIGINT, server.signal.SIGTERM)
        original = {number: server.signal.getsignal(number) for number in numbers}
        installed = dict(original)
        def signal_handler(number, handler):
            previous = installed[number]
            installed[number] = handler
            return previous
        def ready(*args, **kwargs):
            installed[server.signal.SIGTERM](server.signal.SIGTERM, None)
        with patch.object(server.signal, 'signal', side_effect=signal_handler), \
                patch('builtins.print', side_effect=ready):
            result = server.main(['console', '--directory', str(self.root),
                                  '--address', 'https://127.0.0.1:8443',
                                  '--host', '127.0.0.1', '--port', '0', '--admin-port', '0'])
        self.assertEqual(result, 0)
        self.assertEqual(installed, original)
        self.assertFalse(any(thread.name == 'Lifetime-listener' for thread in threading.enumerate()))

    def test_actual_bounded_device_and_record_pages(self):
        request, credential = self.pair()
        for seq in range(1, 4):
            self.public('sync', self.message(request, seq), credential)
        first = self.admin.call('device', device_id=request['device_id'], limit=2)
        self.assertEqual([row['seq'] for row in first['records']], [1, 2])
        self.assertTrue(first['records_truncated'])
        second = self.admin.call('device', device_id=request['device_id'], limit=2,
                                 after_seq=first['records_next_seq'])
        self.assertEqual([row['seq'] for row in second['records']], [3])
        self.assertFalse(second['records_truncated'])
        devices = self.admin.call('devices', limit=1, offset=0, search='M\u00e1y',
                                  group='', status='approved')
        self.assertEqual(len(devices), 1)
        self.assertEqual(self.admin.call('devices', limit=1, offset=1), [])

    def test_reject_ambiguous_framing_and_invalid_json(self):
        headers = b'POST /v1/enroll HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n'
        cases = [(b'Content-Length: 2\r\nContent-Length: 2\r\n', b'{}', 400),
                 (b'Content-Length: 2, 2\r\n', b'{}', 400),
                 (b'Content-Length: -1\r\n', b'', 400),
                 (b'Transfer-Encoding: chunked\r\nContent-Length: 2\r\n', b'{}', 400),
                 (b'Transfer-Encoding: identity\r\n', b'{}', 400),
                 (b'Content-Length: 1048577\r\n', b'', 413),
                 (b'Content-Length: 13\r\n', b'{"x":1,"x":2}', 400),
                 (b'Content-Length: 9\r\n', b'{"x":NaN}', 400),
                 (b'Content-Length: 2\r\n', b'[]', 400),
                 (b'Content-Length: 2\r\nExpect: 100-continue\r\n', b'{}', 400)]
        for framing, body, expected in cases:
            with self.subTest(framing=framing, body=body):
                status, raw, reply_headers = self.wire(headers + framing + b'\r\n' + body)
                self.assertEqual(status, expected)
                self.assertEqual(decode(raw), {'error': 'Request failed'})
                self.assertEqual(reply_headers['Cache-Control'], 'no-store')

    def test_admin_response_and_request_size_limits(self):
        with patch.object(self.runtime._store, 'list_devices', return_value=['x' * MAX_BYTES]):
            status, raw, _ = self.admin_raw('/v1/devices', {})
        self.assertEqual(status, 413)
        self.assertLess(len(raw), MAX_BYTES)
        token = (self.root / 'admin-token').read_text()
        header = (f'POST /v1/code HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\n'
                  'Content-Type: application/json\r\nContent-Length: 1048577\r\n\r\n').encode()
        self.assertEqual(self.wire(header, admin=True)[0], 413)

    def test_errors_are_generic_and_no_access_logs_or_secrets(self):
        output, errors = io.StringIO(), io.StringIO()
        secret = 'private-path-token-that-must-not-appear'
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            request, credential = self.pair()
            self.public('sync', self.message(request), credential)
            with patch.object(self.runtime._store, 'list_devices', side_effect=OSError(secret)):
                status, raw, _ = self.admin_raw('/v1/devices', {})
            self.status(403, 'sync', self.message(request), 'wrong')
            self.wire(b'NOT HTTP\r\n\r\n')
        self.assertEqual(status, 500)
        self.assertEqual(decode(raw), {'error': 'Request failed'})
        self.assertNotIn(secret, raw.decode())
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')
        self.assertFalse(list(self.root.glob('*.log')))

    def test_errors_are_reported_by_kind_never_by_their_text(self):
        """Bug B3: the admin command and the Windows service wrote the whole exception text, paths included."""
        import argparse
        import inspect
        import lifetime_server_service
        error = FileNotFoundError(2, 'No such file or directory', 'C:\\Users\\someone\\secret-place')
        self.assertEqual(server.error_kind(error), 'FileNotFoundError (ENOENT)')
        self.assertEqual(server.error_kind(RuntimeError('192.0.2.7 secret')), 'RuntimeError')
        arguments = argparse.Namespace(arguments='{}', operation='whoami', directory=str(self.root), admin_port=1, field=None)
        for raised, shown in ((error, 'FileNotFoundError (ENOENT)'), (ValueError('Administrative request failed (HTTP 500)'), 'HTTP 500'),
                              (json.JSONDecodeError('secret-typed-text', 'doc', 0), 'JSONDecodeError')):
            errors = io.StringIO()
            with patch.object(AdminClient, 'call', side_effect=raised), contextlib.redirect_stderr(errors):
                self.assertEqual(server._admin(arguments), 1)
            self.assertIn(shown, errors.getvalue())
            self.assertNotIn('secret', errors.getvalue())
        source = inspect.getsource(lifetime_server_service)
        self.assertIn("LogErrorMsg(f'Lifetime Server could not start: {error_kind(error)}')", source)
        self.assertNotIn('}: {error}', source)

    def test_slow_handshakes_count_towards_global_32_and_stop_closes_them(self):
        public = self.runtime._servers[0]
        sockets = []
        try:
            for _ in range(server.MAX_CONNECTIONS):
                sockets.append(socket.create_connection(('127.0.0.1', public.server_port), 3))
            # The slot is taken a moment before the connection is recorded, so wait for both.
            self.assertTrue(eventually(lambda: self.runtime._slots._value == 0 and len(public._connections) == 32))
            extra = socket.create_connection(('127.0.0.1', self.admin.port), 3)
            with extra:
                with contextlib.suppress(ConnectionResetError):
                    self.assertEqual(extra.recv(1), b'')
            started = time.monotonic()
            self.runtime.stop()
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(self.runtime._slots._value, 32)
            self.assertFalse(public._connections)
        finally:
            for connection in sockets:
                connection.close()

    def test_one_remote_address_cannot_hold_every_request_slot(self):
        """Audit L3: opening a connection needs no credential, so one host on the LAN could take all 32 slots."""
        public = self.runtime._servers[0]
        closed = []
        with patch.object(server.ThreadingHTTPServer, 'process_request'), \
                patch.object(public, 'shutdown_request', side_effect=closed.append):
            for number in range(server.MAX_PER_ADDRESS + 3):
                public.process_request(('greedy', number), ('192.0.2.5', 40000 + number))
            self.assertEqual(len(closed), 3)                                     # refused before a slot or a thread
            self.assertEqual(self.runtime._slots._value, server.MAX_CONNECTIONS - server.MAX_PER_ADDRESS)
            public.process_request('other', ('192.0.2.6', 40000))               # everyone else still gets in
            self.assertEqual(len(closed), 3)
            public._release('192.0.2.5')
            public.process_request('again', ('192.0.2.5', 50000))               # and so does the first, once one ends
            self.assertEqual(len(closed), 3)
            for address in ['192.0.2.5'] * server.MAX_PER_ADDRESS + ['192.0.2.6']:
                public._release(address)
        self.assertEqual(self.runtime._slots._value, server.MAX_CONNECTIONS)
        self.assertEqual(public._held, {})
        public._connections.clear()

    def test_handshake_header_and_body_deadlines_reclaim_slots(self):
        public = self.runtime._servers[0]
        with patch.object(server, 'HANDSHAKE_TIMEOUT', 0.2):
            with socket.create_connection(('127.0.0.1', public.server_port), 3) as connection:
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 31))
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 32))
                with contextlib.suppress(ConnectionResetError):
                    self.assertEqual(connection.recv(1), b'')
        with patch.object(server, 'HEADER_TIMEOUT', 0.2):
            with socket.create_connection(('127.0.0.1', self.admin.port), 3) as connection:
                connection.sendall(b'POST /v1/devices HTTP/1.1\r\nX: ')
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 31))
                # Trickle activity does not extend the absolute header deadline.
                for _ in range(3):
                    time.sleep(0.04)
                    connection.sendall(b'x')
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 32))
        with patch.object(server, 'BODY_TIMEOUT', 0.2):
            token = (self.root / 'admin-token').read_text()
            header = (f'POST /v1/code HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\n'
                      'Content-Type: application/json\r\nContent-Length: 20\r\n\r\n{').encode()
            with socket.create_connection(('127.0.0.1', self.admin.port), 3) as connection:
                connection.sendall(header)
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 31))
                self.assertTrue(eventually(lambda: self.runtime._slots._value == 32))


class ProtectionTests(unittest.TestCase):
    def test_directory_acl_well_known_sids_and_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(permissions.sys, 'platform', 'win32'), \
                    patch.object(permissions, 'windows_acl') as apply_acl:
                server._protect_directory(root)
                apply_acl.assert_called_once_with(
                    root, 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)',
                    ['*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)'])
                apply_acl.side_effect = OSError('ACL failed')
                with self.assertRaises(OSError):
                    server._protect_directory(root)

    def test_start_fails_before_database_on_directory_or_secret_acl_failure(self):
        for helper in ('protect_directory', 'protect_private'):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as temporary:
                runtime = server.ServerRuntime(temporary, host='127.0.0.1', port=0)
                with contextlib.ExitStack() as stack:
                    if helper == 'protect_private':
                        stack.enter_context(patch.object(permissions, 'protect_directory'))
                    stack.enter_context(patch.object(permissions, helper,
                                                     side_effect=OSError('ACL failed')))
                    store = stack.enter_context(patch.object(server, 'Store'))
                    with self.assertRaises(OSError):
                        runtime.start()
                store.assert_not_called()
                self.assertEqual(runtime._servers, [])
                self.assertFalse((Path(temporary) / 'admin-token').exists())

    def test_key_acl_failure_keeps_identity_and_never_opens_database(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(permissions, 'windows_acl'):
            root = Path(temporary)
            tls.generate_certificate(root, 'https://localhost')
            server._admin_token(root / 'admin-token')
            key = (root / 'server-key.pem').read_bytes()
            real_protect_private = permissions.protect_private
            def fail_key(path):
                if Path(path).name == 'server-key.pem':
                    raise OSError('key ACL failed')
                return real_protect_private(path)
            with patch.object(permissions, 'protect_private', side_effect=fail_key), \
                    patch.object(server, 'Store') as store:
                runtime = server.ServerRuntime(root, host='127.0.0.1', port=0)
                with self.assertRaises(OSError):
                    runtime.start()
                store.assert_not_called()
            self.assertEqual((root / 'server-key.pem').read_bytes(), key)

    def test_corrupt_admin_token_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(permissions, 'windows_acl'):
            path = Path(temporary) / 'admin-token'
            path.write_bytes(b'bad-secret\n')
            with self.assertRaises(ValueError):
                server._admin_token(path)
            self.assertEqual(path.read_bytes(), b'bad-secret\n')

    def test_service_runs_the_base_interpreter_not_a_venv_launcher_or_pythonservice(self):
        import lifetime_server_service as service
        root = Path(tempfile.gettempdir())
        base, venv = str(root / 'Python312' / 'python.exe'), str(root / 'server' / '.venv')
        with patch.object(sys, '_base_executable', base, create=True), patch.object(sys, 'prefix', venv), \
                patch.object(sys, 'base_prefix', str(Path(base).parent)):
            executable, arguments = service.host_command()
        self.assertEqual(executable, base)       # never <venv>\Scripts\python.exe, a launcher Windows rejects
        self.assertTrue(arguments.startswith('-E -s "'))
        self.assertIn('lifetime_server_service.py" --service-host "', arguments)
        self.assertTrue(arguments.endswith(f'"{Path(venv).resolve()}"'))
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(service.host_command(), (None, None))    # a packaged build is its own host

    def test_service_host_puts_the_environment_packages_first(self):
        import lifetime_server_service as service
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary) / 'Lib' / 'site-packages'
            packages.mkdir(parents=True)
            (packages / 'extra-entry').mkdir()
            (packages / 'marker.pth').write_text('extra-entry\n')
            saved = list(sys.path)
            try:
                service.use_environment(temporary)
                self.assertEqual(Path(sys.path[0]), packages)
                self.assertIn(packages / 'extra-entry', [Path(entry) for entry in sys.path[:2]])
            finally:
                sys.path[:] = saved
            with self.assertRaises(SystemExit):
                service.use_environment(Path(temporary) / 'missing')

    def test_runtime_and_service_imports_do_not_require_pywin32_or_crypto(self):
        script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('cryptography', 'win32service', 'win32serviceutil', 'servicemanager'):
        raise AssertionError('eager server dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import lifetime_server
import lifetime_server_service
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class PowerRuntimeTests(unittest.TestCase):
    setUp = RuntimeTests.setUp

    def test_server_info_uses_shared_collector_and_admin_routes(self):
        readings = dict(system='Test OS', specs=dict(cpu_name='Server CPU'), performance=dict(cpu_percent=15))
        with patch('lifetime_agent._resources', return_value=readings) as collect:
            info = self.admin.call('server_info')
        self.assertEqual(info['specs']['cpu_name'], 'Server CPU')
        self.assertEqual(info['performance']['cpu_percent'], 15)
        self.assertEqual(info['certificate_fingerprint'], self.runtime.fingerprint)
        self.assertGreater(info['database_size'], 0)
        self.assertGreaterEqual(info['uptime'], 0)
        self.assertEqual(info['device_counts']['total'], 0)
        self.assertEqual(len(info['listening_addresses']), 2)
        collect.assert_called_once_with(self.runtime._resource_state)
        for operation, method, args in (
                ('send_command', 'send_command', dict(device_id=str(uuid.uuid4()), action='refresh')),
                ('cancel_command', 'cancel_command', dict(command_id=str(uuid.uuid4()))),
                ('wake', 'wake', dict(device_id=str(uuid.uuid4())))):
            with patch.object(self.runtime._store, method, return_value={'ok': True}) as call:
                if operation == 'wake' and sys.platform != 'win32':     # power needs a named Windows account
                    with self.assertRaisesRegex(ValueError, 'HTTP 403'):
                        self.admin.call(operation, **args)
                    continue
                self.assertEqual(self.admin.call(operation, **args), {'ok': True})
                call.assert_called_once_with(**args)

    def test_magic_packet_bytes_and_broadcasts_without_network(self):
        from lifetime_wol import send_magic_packet
        fake = Mock()
        with patch('lifetime_wol.local_broadcasts', return_value=['255.255.255.255', '192.168.7.255']):
            self.assertEqual(send_magic_packet('AA:BB:CC:DD:EE:FF', sock_factory=lambda *args: fake), 102)
        expected = bytes.fromhex('FF'*6+'AABBCCDDEEFF'*16)
        self.assertEqual(fake.sendto.call_count, 2)
        self.assertEqual(fake.sendto.call_args_list[0].args, (expected, ('255.255.255.255', 9)))
        self.assertEqual(fake.sendto.call_args_list[1].args, (expected, ('192.168.7.255', 9)))
        fake.close.assert_called_once()
        with self.assertRaises(ValueError):
            send_magic_packet('not-a-mac', sock_factory=lambda *args: fake)


if __name__ == '__main__':
    unittest.main()
