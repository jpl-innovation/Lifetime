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
            self.status(404, operation, {}, token)
            self.assertEqual(self.admin_raw('/v1/' + operation, {}, token='wrong')[0], 401)
        status, raw, _ = self.wire(b'POST /v1/devices HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}', admin=True)
        self.assertEqual(status, 401)
        self.assertEqual(decode(raw), {'error': 'Request failed'})
        self.assertEqual(self.admin_raw('/v1/enroll', {})[0], 404)

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
            self.assertEqual(self.admin.call('overview'), {'devices': 1})
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
        with patch.object(tls.hashlib, 'sha256') as digest:
            # Bundle verification uses the first hash, peer-pin check the second.
            digest.side_effect = [Mock(hexdigest=lambda: self.bundle['fingerprint']),
                                  Mock(hexdigest=lambda: '0' * 64)]
            with self.assertRaises(ssl.SSLCertVerificationError):
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

    def test_wrong_certificate_hostname_fails_start_preserves_identity_and_closes_listeners(self):
        self.runtime.stop()
        before = (self.root / 'server-cert.pem').read_bytes()
        self.runtime = server.ServerRuntime(self.root, address='https://wrong.example:8443',
                                            host='127.0.0.1', port=0)
        started = time.monotonic()
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.runtime.start()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((self.root / 'server-cert.pem').read_bytes(), before)
        self.assertEqual(self.runtime._servers, [])
        self.assertEqual(self.runtime._threads, [])
        self.assertIsNone(self.runtime._store)

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

    def test_slow_handshakes_count_towards_global_32_and_stop_closes_them(self):
        public = self.runtime._servers[0]
        sockets = []
        try:
            for _ in range(server.MAX_CONNECTIONS):
                sockets.append(socket.create_connection(('127.0.0.1', public.server_port), 3))
            self.assertTrue(eventually(lambda: self.runtime._slots._value == 0))
            self.assertEqual(len(public._connections), 32)
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


if __name__ == '__main__':
    unittest.main()
