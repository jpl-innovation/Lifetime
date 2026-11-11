"""S1 security regressions. Only loopback TLS and fake multicast sockets."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import sys
import tempfile
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid

import lifetime_connection as connection
import lifetime_discovery as discovery
import lifetime_permissions as permissions
import lifetime_protocol as protocol
import lifetime_server_store as storage
from lifetime_admin import AdminClient
from lifetime_agent import MachineAgent, reporting_directory
from lifetime_client import SyncClient
from lifetime_server import ServerRuntime

ROOT = Path(__file__).parent
FINGERPRINT = hashlib.sha256(b'synthetic test certificate').hexdigest()


def request(code):
    return dict(version=1, request_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), code=code,
                poll_token=secrets.token_urlsafe(32), identity={'display_name': 'Demo-PC-Test'},
                policy=dict(history=False, resources=False))


class CodeTests(unittest.TestCase):
    def test_roundtrip_check_character_case_grouping_and_aliases(self):
        code = connection.encode('01011', '010101', FINGERPRINT)
        self.assertEqual(connection.parts(code)[:2], ('01011', '010101'))
        self.assertEqual(connection.normalize(code.lower()), code)
        payload = code[4:].replace('-', '')
        self.assertEqual(connection.normalize('lts ' + payload.replace('0', 'O').replace('1', 'L')), code)
        self.assertEqual(connection.normalize('lts-' + payload.replace('1', 'I')), code)
        self.assertEqual(connection.normalize(payload), code)
        for index in range(len(payload)-1):
            replacement = '2' if payload[index] != '2' else '3'
            bad = payload[:index] + replacement + payload[index+1:]
            with self.subTest(index=index), self.assertRaises(ValueError):
                connection.normalize(bad)
        with self.assertRaises(ValueError):
            connection.normalize(code[:-1] + ('2' if code[-1] != '2' else '3'))

    def test_commitment_is_40_bits_and_binds_id_secret_and_certificate(self):
        self.assertEqual(len(connection.commitment(FINGERPRINT, '12345', '6789AB')), 8)
        value = connection.commitment(FINGERPRINT, '12345', '6789AB')
        for fingerprint, identifier, secret in (('0'*64, '12345', '6789AB'),
                                               (FINGERPRINT, '12346', '6789AB'),
                                               (FINGERPRINT, '12345', '6789AC')):
            self.assertNotEqual(connection.commitment(fingerprint, identifier, secret), value)

    def test_connection_line_and_address_validation(self):
        code = connection.issue(FINGERPRINT)
        line = connection.connection_line('demo-server:8443', code)
        self.assertEqual(connection.parse(line), ('https://demo-server:8443', code))
        self.assertEqual(connection.parse(code, '127.0.0.1'), ('https://127.0.0.1:8443', code))
        self.assertEqual(connection.parse(code, '[::1]')[0], 'https://[::1]:8443')
        for address in ('http://evil', 'https://user@evil', 'https://evil/path', 'https://evil?secret=1'):
            with self.assertRaises(ValueError):
                connection.parse(code, address)


class InvitationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='s1-test-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = storage.Store(self.root/'store.sqlite')
        self.addCleanup(lambda: self.store.close())

    def test_single_use_idempotence_and_hashed_secrets(self):
        issued = self.store.create_invitation(FINGERPRINT)
        message = request(issued['code'].lower())
        self.assertEqual(self.store.enroll(message)['status'], 'pending')
        self.assertEqual(self.store.enroll(message)['status'], 'pending')
        with self.assertRaises(storage.AuthenticationError):
            self.store.enroll(request(issued['code']))
        row = dict(self.store._db.execute('SELECT * FROM invitations').fetchone())
        self.assertEqual(row['remaining'], 0)
        self.assertNotIn(issued['code'], json.dumps(row))
        self.assertNotIn(connection.parts(issued['code'])[1], json.dumps(row))
        self.assertEqual(self.store.pending_enrollments()[0]['device_id'], message['device_id'])
        # Invitation listings never expose either hash or code.
        self.assertNotIn('hash', str(self.store.invitations()))

    def test_default_expiry_multiple_uses_revocation_and_restore(self):
        now = dt.datetime.now(dt.timezone.utc)
        with patch.object(storage, '_now', return_value=now):
            issued = self.store.create_invitation(FINGERPRINT, uses=2)
            self.assertEqual(storage._time(issued['expires_at']) - now, dt.timedelta(hours=24))
            self.store.enroll(request(issued['code']))
            self.store.enroll(request(issued['code']))
            self.assertEqual(self.store.invitations()[0]['remaining'], 0)
            expiring = self.store.create_invitation(FINGERPRINT, ttl=1)
            revoked = self.store.create_invitation(FINGERPRINT)
            self.store.revoke_invitation(revoked['invitation_id'])
            with self.assertRaises(storage.AuthenticationError):
                self.store.enroll(request(revoked['code']))
        with patch.object(storage, '_now', return_value=now + dt.timedelta(seconds=1)):
            with self.assertRaises(storage.AuthenticationError):
                self.store.enroll(request(expiring['code']))
        live = self.store.create_invitation(FINGERPRINT)
        self.store.backup(self.root/'backup.sqlite')
        self.store.restore(self.root/'backup.sqlite')
        self.assertEqual(self.store.invitations(), [])
        with self.assertRaises(storage.AuthenticationError):
            self.store.enroll(request(live['code']))

    def test_five_wrong_secrets_lock_invitation_durably(self):
        issued = self.store.create_invitation(FINGERPRINT)
        identifier, secret, _ = connection.parts(issued['code'])
        wrong = connection.encode(identifier, '000000' if secret != '000000' else '111111', FINGERPRINT)
        for _ in range(5):
            with self.assertRaises(storage.AuthenticationError):
                self.store.enroll(request(wrong))
        self.assertEqual(self.store.invitations()[0]['failures'], 5)
        self.store.close()
        self.store = storage.Store(self.root/'store.sqlite')
        with self.assertRaises(storage.AuthenticationError):
            self.store.enroll(request(issued['code']))

    def test_per_ip_backoff_and_global_limit_are_persistent_and_bounded(self):
        now = dt.datetime.now(dt.timezone.utc)
        code = connection.issue(FINGERPRINT)  # valid syntax, unknown invitation
        with patch.object(storage, '_now', return_value=now):
            for _ in range(3):
                with self.assertRaises(storage.AuthenticationError):
                    self.store.enroll(request(code), peer_ip='192.0.2.1')
            with self.assertRaises(storage.ThrottledError):
                self.store.enroll(request(code), peer_ip='192.0.2.1')
            self.assertNotIn('192.0.2.1', str(tuple(self.store._db.execute('SELECT * FROM guess_ips').fetchone())))
            for index in range(7):
                with self.assertRaises(storage.AuthenticationError):
                    self.store.enroll(request(code), peer_ip=f'192.0.2.{index+2}')
            self.assertEqual(self.store._db.execute('SELECT failures FROM throttle').fetchone()[0], 10)
            with self.assertRaises(storage.ThrottledError):
                self.store.enroll(request(code), peer_ip='192.0.2.200')
        with patch.object(storage, '_now', return_value=now + dt.timedelta(seconds=61)):
            live = self.store.create_invitation(FINGERPRINT)
            self.assertEqual(self.store.enroll(request(live['code']), peer_ip='192.0.2.1')['status'], 'pending')

    def test_concurrent_invitation_use_never_exceeds_limit(self):
        import concurrent.futures
        issued = self.store.create_invitation(FINGERPRINT, uses=3)
        def enroll(_):
            try:
                return self.store.enroll(request(issued['code']))['status']
            except storage.AuthenticationError:
                return 'rejected'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(enroll, range(8)))
        self.assertEqual(results.count('pending'), 3)


class DiscoveryTests(unittest.TestCase):
    def test_responder_joins_only_mdns_group_with_fake_socket(self):
        sock = Mock()
        responder = discovery.Responder('Demo', 'demo.local', 8443, FINGERPRINT, socket_factory=lambda *args: sock)
        with patch.object(responder, '_run'):
            self.assertTrue(responder.start())
            responder.stop()
        sock.bind.assert_called_once_with(('', 5353))
        self.assertIn(unittest.mock.call(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                      socket.inet_aton('224.0.0.251')+socket.inet_aton('0.0.0.0')), sock.setsockopt.call_args_list)
        sock.close.assert_called_once()

    def test_announce_parse_and_only_lifetime_queries_answered(self):
        packet = discovery.announcement('Demo-Server', 'demo.local', 8443, FINGERPRINT)
        parsed = discovery.parse(packet)
        self.assertEqual(parsed, [dict(instance='Demo-Server', host='demo.local', port=8443,
                                      address='https://demo.local:8443', fingerprint_prefix=FINGERPRINT[:10])])
        responder = discovery.Responder('Demo-Server', 'demo.local', 8443, FINGERPRINT)
        responder.sock = Mock()
        responder.answer(discovery.query(), ('127.0.0.1', 9999))
        responder.sock.sendto.assert_called_once_with(packet, ('127.0.0.1', 9999))
        responder.sock.reset_mock()
        responder.answer(discovery.query().replace(b'_lifetime', b'_printers'), ('127.0.0.1', 5353))
        responder.answer(packet, ('127.0.0.1', 5353))
        responder.sock.sendto.assert_not_called()
        self.assertNotIn(b'Demo-PC', packet)
        self.assertNotIn(FINGERPRINT.encode(), packet)

    def test_fake_socket_browse_multicast_parameters_and_timeout(self):
        packet = discovery.announcement('Demo-Server', 'demo.local', 8443, FINGERPRINT)
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        sock.recvfrom.side_effect = [(packet, ('127.0.0.1', 5353)), socket.timeout()]
        with patch.object(discovery.time, 'monotonic', side_effect=[0, .1, .2, 4]):
            found = discovery.discover(socket_factory=lambda *args: sock)
        self.assertEqual(len(found), 1)
        sock.sendto.assert_called_once_with(discovery.query(), discovery.GROUP)
        sock.setsockopt.assert_called_with(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)

    def test_malformed_compression_and_flood_bounds(self):
        packet = discovery.announcement('Demo', 'demo.local', 8443, FINGERPRINT)
        for data in (b'', packet[:17], b'X'*4097, struct.pack('!6H', 0, 0x8400, 0, 1, 0, 0)+b'\xc0\x0c'):
            self.assertEqual(discovery.parse(data), [])
        self.assertFalse(discovery.lifetime_query(b'\0'*12))


class RealConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='s1-tls-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))
        self.runtime = ServerRuntime(self.root/'server', address='https://127.0.0.1:8443', host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.admin = AdminClient(self.root/'server', urlsplit(self.runtime.admin_address).port)
        self.agent = MachineAgent.initialize(self.root/'agent')
        self.addCleanup(self.agent.close)

    def test_actual_local_tls_code_enrollment_approval_sync_and_revocation(self):
        issued = self.admin.call('invitation')
        self.assertEqual(self.admin.call('server_info')['fingerprint'], self.runtime.fingerprint)
        client = SyncClient(self.agent)
        status = client.start_connection(connection.connection_line(self.runtime.public_address, issued['code']), '', {}, dict(history=False, resources=False))
        self.assertEqual(status['status'], 'pending')
        enrollment = self.agent._load()['enrollment']
        self.assertIsNone(self.agent._load()['credential'])
        self.admin.call('approve', request_id=enrollment['request_id'])
        self.assertEqual(client.step(force=True)['status'], 'approved')
        self.agent.sample(100, 'demo-boot', dt.datetime.now(dt.timezone.utc).isoformat())
        self.assertEqual(client.step(force=True)['status'], 'connected')
        device = self.admin.call('device', device_id=self.agent.device_id)
        self.assertEqual(device['client_version'], protocol.CLIENT_VERSION)
        self.assertEqual(device['record_count'], 1)
        self.assertEqual(self.admin.call('invitations')[0]['remaining'], 0)
        self.admin.call('revoke', device_id=self.agent.device_id)
        self.agent.sample(110, 'demo-boot', dt.datetime.now(dt.timezone.utc).isoformat())
        self.assertEqual(client.step(force=True)['status'], 'revoked')
        self.assertEqual(len(self.agent.batch()['records']), 1)

    def test_commitment_mismatch_before_http_data_or_enrollment_state(self):
        wrong = connection.issue('0'*64)
        original = self.agent._load()
        with patch.object(self.runtime._store, 'enroll', wraps=self.runtime._store.enroll) as enroll, \
                patch('lifetime_tls.request_json') as send:
            with self.assertRaises(ssl.SSLCertVerificationError):
                SyncClient(self.agent).start_connection(wrong, self.runtime.public_address, {}, dict(history=False, resources=False))
            send.assert_not_called()
            enroll.assert_not_called()
        self.assertEqual(self.agent._load(), original)


class LinuxAgentPathTests(unittest.TestCase):
    def test_user_agent_path_and_opt_in_installer(self):
        with patch('lifetime_agent.sys.platform', 'linux'), patch.dict(os.environ, {'XDG_STATE_HOME': str(ROOT/'synthetic-user-state')}):
            self.assertEqual(reporting_directory(), ROOT/'synthetic-user-state/lifetime-agent')
        script = (ROOT/'install_linux.sh').read_text(encoding='utf-8')
        self.assertIn('company_agent=false', script)
        self.assertIn('--company-agent', script)
        self.assertIn('Install and start the company-agent user service? (y/N)', script)
        self.assertIn('lifetime-company-agent.service', script)
        self.assertIn('UMask=0077', script)
        self.assertIn('NoNewPrivileges=true', script)
        self.assertNotIn('enable-linger', script)



class LanFallbackTests(unittest.TestCase):
    def test_fake_sockets_scan_ceiling_and_reject_nonmatching_certificates(self):
        import hashlib
        from unittest.mock import MagicMock
        good, wrong = b'certificate for selected server', b'wrong server certificate'
        code = connection.issue(hashlib.sha256(good).hexdigest())
        probes = []
        def connect(endpoint, timeout):
            host, port = endpoint
            probes.append((host, port, timeout))
            if host not in ('192.0.2.1', '192.0.2.254'):
                raise OSError('closed')
            raw = MagicMock()
            raw.host = host
            raw.__enter__.return_value = raw
            return raw
        context = MagicMock()
        def wrap(raw, server_hostname):
            secured = MagicMock()
            secured.__enter__.return_value = secured
            secured.getpeercert.return_value = good if raw.host.endswith('.254') else wrong
            return secured
        context.wrap_socket.side_effect = wrap
        addresses = [(socket.AF_INET, 1, 6, '', (ip, 0)) for ip in
                     ('192.0.2.8', '192.0.2.9', '127.0.0.1', '169.254.1.2')]
        with patch.object(connection.socket, 'getaddrinfo', return_value=addresses), patch.object(connection.socket, 'create_connection', side_effect=connect), patch.object(connection.ssl, 'SSLContext', return_value=context), patch.object(connection.tls, 'encode_bundle', return_value='VERIFIED') as encode:
            self.assertEqual(connection.find_server(code), 'VERIFIED')
        encode.assert_called_once()
        self.assertEqual(encode.call_args.args[0], 'https://192.0.2.254:8443')
        self.assertTrue(all(host.startswith('192.0.2.') and port == 8443 and timeout == 0.4 for host, port, timeout in probes))
        self.assertIn('192.0.2.1', [host for host, _, _ in probes])
        # The mismatching certificate was read, but no HTTP method was used.
        for raw_call in context.wrap_socket.call_args_list:
            raw_call.args[0].send.assert_not_called()
            raw_call.args[0].sendall.assert_not_called()

    def test_no_matching_certificate_fails_closed(self):
        addresses = [(socket.AF_INET, 1, 6, '', ('192.0.2.9', 0))]
        from unittest.mock import MagicMock
        raw = MagicMock()
        with patch.object(connection.socket, 'getaddrinfo', return_value=addresses), patch.object(connection.socket, 'create_connection', return_value=raw), patch.object(connection, 'bootstrap', side_effect=ssl.SSLCertVerificationError('wrong server')):
            with self.assertRaises(ValueError):
                connection.find_server(connection.issue('0'*64))


if __name__ == '__main__':
    unittest.main()
