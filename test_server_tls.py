"""TLS trust/transport regressions; all files and servers are isolated test fixtures.

Mocked transport checks run without cryptography. Real HTTPS integration tests use
the optional server dependency, and explicitly mock Windows ACLs for temporary keys.
"""
import base64
import contextlib
import datetime as dt
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import lifetime_permissions as permissions
import lifetime_tls as tls


class AddressTests(unittest.TestCase):
    def test_explicit_https_origins(self):
        cases = {'https://SERVER.example:8443/': ('https://server.example:8443', 'server.example', 8443),
                 'https://127.0.0.1': ('https://127.0.0.1', '127.0.0.1', 443),
                 'https://[2001:db8::1]:443': ('https://[2001:db8::1]:443', '2001:db8::1', 443),
                 'https://localhost': ('https://localhost', 'localhost', 443)}
        for address, expected in cases.items():
            with self.subTest(address=address):
                self.assertEqual(tls._address(address), expected)

    def test_ambiguous_or_non_origin_addresses_rejected(self):
        for address in (None, 1, '', 'server', 'http://server', '//server', 'HTTPS://server',
                        ' https://server', 'https://server\n', 'https://user@server',
                        'https://user:pass@server', 'https://server/path', 'https://server//',
                        'https://server?', 'https://server#', 'https://server?x=1',
                        'https://server/#x', 'https://server\\evil', 'https://server:0',
                        'https://server:65536', 'https://server:', 'https://server:+443',
                        'https://server:-1', 'https://server: 443', 'https://server:443:443',
                        'https://', 'https://[::1', 'https://::1', 'https://[::1]evil',
                        'https://[::1]:', 'https://[fe80::1%25eth0]', 'https://%73erver',
                        'https://server..example', 'https://-server', 'https://server_',
                        'https://127.1', 'https://0177.0.0.1', 'https://2130706433',
                        'https://m\u00e1y.example', 'https://server\t.example'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                tls._address(address)

    def test_client_import_does_not_load_server_dependency(self):
        script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] == 'cryptography':
        raise AssertionError('client imported cryptography')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import lifetime_tls
assert lifetime_tls._address('https://localhost')[1] == 'localhost'
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class PrivateFileTests(unittest.TestCase):
    def test_windows_acl_uses_well_known_admin_and_system_sids(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'private')
            path.touch()
            with patch.object(permissions.sys, 'platform', 'win32'), \
                    patch.object(permissions, 'windows_acl') as apply_acl:
                tls.protect_private(path)
            apply_acl.assert_called_once_with(
                path, 'D:P(A;;FA;;;BA)(A;;FA;;;SY)',
                ['*S-1-5-32-544:(F)', '*S-1-5-18:(F)'])

    def test_windows_acl_failure_propagates(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'private')
            path.touch()
            with patch.object(permissions.sys, 'platform', 'win32'), \
                    patch.object(permissions, 'windows_acl', side_effect=OSError('ACL failed')):
                with self.assertRaises(OSError):
                    tls.protect_private(path)

    @unittest.skipIf(sys.platform == 'win32', 'Real POSIX permissions require POSIX')
    def test_posix_private_mode_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'private')
            path.write_bytes(b'secret')
            path.chmod(0o666)
            tls.protect_private(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(root, 'link')
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                tls.protect_private(link)
            with self.assertRaises(ValueError):
                tls.protect_private(root)


class StdlibCertificateFixture(unittest.TestCase):
    """Use an OS trust-store certificate as public test data; no server dependency."""

    @classmethod
    def setUpClass(cls):
        certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
        if not certificates:
            raise unittest.SkipTest('No OS certificate available for stdlib-only fixtures')
        cls.der = certificates[0]
        cls.pem = ssl.DER_cert_to_PEM_cert(cls.der)
        cls.fingerprint = hashlib.sha256(cls.der).hexdigest()

    def bundle(self):
        return dict(address='https://localhost:8443', code='one-time-code',
                    certificate_pem=self.pem, fingerprint=self.fingerprint)


class BundleTests(StdlibCertificateFixture):
    def encode_raw(self, raw):
        if isinstance(raw, dict):
            raw = json.dumps(raw).encode()
        return tls.BUNDLE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip('=')

    def test_bundle_roundtrip_with_stdlib_only(self):
        encoded = tls.encode_bundle('https://LOCALHOST:8443/', 'one-time-code', self.pem)
        self.assertEqual(tls.decode_bundle(encoded), self.bundle())
        self.assertEqual(tls._certificate(self.pem), self.fingerprint)

    def test_certificate_and_fingerprint_tampering(self):
        for change in (dict(fingerprint='0'*64), dict(fingerprint=self.fingerprint.upper()),
                       dict(certificate_pem='not a certificate'), dict(certificate_pem=self.pem * 2),
                       dict(certificate_pem=self.pem + 'untrusted trailing data'),
                       dict(certificate_pem='-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----\n')):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                tls.decode_bundle(self.encode_raw(dict(version=1, **(self.bundle() | change))))

    def test_bad_bundle_shapes_versions_and_codes(self):
        cases = [[], {}, dict(version=2, **self.bundle()), dict(version=True, **self.bundle()),
                 dict(version=1.0, **self.bundle()), self.bundle(),
                 dict(version=1, **self.bundle(), extra='field')]
        cases += [dict(version=1, **(self.bundle() | dict(code=code)))
                  for code in ('', 'a\r\nb', 'contains space', 'x'*257, None, 123)]
        for value in cases:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                tls.decode_bundle(self.encode_raw(json.dumps(value).encode()))
        with self.assertRaises(ValueError):
            tls.decode_bundle(self.encode_raw(b'{"version":1,"version":1}'))

    def test_malformed_encoding_and_size(self):
        valid = tls.encode_bundle('https://localhost', 'code', self.pem)
        for value in (None, 123, '', 'random', valid+'=', valid+' ',
                      tls.BUNDLE_PREFIX+'!', tls.BUNDLE_PREFIX+'a',
                      tls.BUNDLE_PREFIX+'x'*(tls.MAX_BUNDLE_BYTES*2)):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                tls.decode_bundle(value)


class TransportTests(StdlibCertificateFixture):
    def setUp(self):
        self.connection = Mock()
        self.connection.sock.getpeercert.return_value = self.der
        self.response = Mock(status=200)
        self.reply(b'{"ok":true}')
        self.connection.getresponse.return_value = self.response
        self.factory = patch.object(tls.http.client, 'HTTPSConnection', return_value=self.connection)
        self.connect_class = self.factory.start()
        self.addCleanup(self.factory.stop)

    def reply(self, raw, headers=None):
        headers = headers or {}
        self.response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
        self.response.read.side_effect = io.BytesIO(raw).read

    def test_verified_context_and_pin_precede_request_and_credential(self):
        events = []
        self.connection.connect.side_effect = lambda: events.append('handshake')
        self.connection.sock.getpeercert.side_effect = lambda **kw: events.append('pin') or self.der
        self.connection.request.side_effect = lambda *a, **kw: events.append('request')
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://evil.invalid:1'}):
            result = tls.request_json(self.bundle(), '/v1/report', {'text': 'Ti\u1ebfng Vi\u1ec7t'}, 'device-secret')
        self.assertEqual(result, {'ok': True})
        self.assertEqual(events, ['handshake', 'pin', 'request'])
        self.assertEqual(self.connect_class.call_args.args, ('localhost', 8443))
        context = self.connect_class.call_args.kwargs['context']
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertEqual(context.cert_store_stats()['x509'], 1)
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer device-secret')
        self.connection.close.assert_called_once()

    def test_encoded_bundle_is_accepted(self):
        encoded = tls.encode_bundle('https://localhost:8443', 'one-time-code', self.pem)
        self.assertEqual(tls.request_json(encoded, '/v1/enroll', {}), {'ok': True})
        self.assertNotIn('Authorization', self.connection.request.call_args.kwargs['headers'])

    def test_wrong_peer_pin_sends_no_http_bytes(self):
        self.connection.sock.getpeercert.return_value = b'different certificate'
        with self.assertRaises(ssl.SSLCertVerificationError):
            tls.request_json(self.bundle(), '/v1/enroll', {'code': 'pairing-secret'}, 'device-secret')
        self.connection.request.assert_not_called()
        self.connection.close.assert_called_once()

    def test_failed_handshake_sends_no_http_bytes(self):
        self.connection.connect.side_effect = ssl.SSLCertVerificationError('hostname mismatch')
        with self.assertRaises(ssl.SSLCertVerificationError):
            tls.request_json(self.bundle(), '/v1/report', {}, 'device-secret')
        self.connection.request.assert_not_called()
        self.connection.close.assert_called_once()

    def test_bundle_dictionary_is_revalidated_before_connecting(self):
        with self.assertRaises(ValueError):
            tls.request_json(self.bundle() | dict(fingerprint='0'*64), '/v1/report', {})
        self.connect_class.assert_not_called()

    def test_invalid_requests_fail_before_network(self):
        cases = [dict(path=p) for p in ('https://evil/', '//evil/', 'relative', '/x\r\nInjected: yes',
                                        '/x#fragment', '/bad\\path', '/bad%XX', '/sp ace', '/ti\u1ebfng')]
        cases += [dict(timeout=t) for t in (0, -1, True, None, float('inf'), float('nan'))]
        cases += [dict(credential=c) for c in ('', 'a b', 'x\r\ny', '\u1ec7', 123, 'x'*4097)]
        cases += [dict(payload=p) for p in ([], None, {'x': float('nan')}, {'x': float('inf')}, {'x': object()})]
        for change in cases:
            with self.subTest(change=str(change)[:80]), self.assertRaises(ValueError):
                tls.request_json(self.bundle(), **(dict(path='/v1/report', payload={}) | change))
        self.connect_class.assert_not_called()

    def test_request_limit_is_bytes_and_boundary_is_inclusive(self):
        payload = {'x': 'a'*(tls.MAX_MESSAGE_BYTES - 8)}
        tls.request_json(self.bundle(), '/v1/report', payload)
        self.assertEqual(len(self.connection.request.call_args.kwargs['body']), tls.MAX_MESSAGE_BYTES)
        self.connect_class.reset_mock()
        with self.assertRaises(ValueError):
            tls.request_json(self.bundle(), '/v1/report', {'x': payload['x']+'a'})
        self.connect_class.assert_not_called()
        with self.assertRaises(ValueError):
            tls.request_json(self.bundle(), '/v1/report', {'x': '\u1ec7'*(tls.MAX_MESSAGE_BYTES//6)})

    def test_redirects_and_http_errors_never_follow_or_echo_secrets(self):
        for status in (301, 302, 307, 308, 400, 401, 403, 500):
            self.response.status = status
            self.reply(b'{"error":"device-secret pairing-secret"}', {'Location': 'https://evil.invalid'})
            with self.subTest(status=status), self.assertRaises(tls.HTTPSRequestError) as caught:
                tls.request_json(self.bundle(), '/v1/report', {}, 'device-secret')
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('secret', str(caught.exception))
            self.response.read.assert_not_called()

    def test_response_size_limit_with_and_without_content_length(self):
        for headers in ({}, {'Content-Length': str(tls.MAX_MESSAGE_BYTES+1)},
                        {'Content-Length': '-1'}, {'Content-Length': 'bad'},
                        {'Content-Length': '9'*100}, {'Content-Encoding': 'gzip'}):
            self.reply(b'x'*(tls.MAX_MESSAGE_BYTES+1), headers)
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                tls.request_json(self.bundle(), '/v1/report', {})
        self.reply(b'{"x":"' + b'a'*(tls.MAX_MESSAGE_BYTES-8) + b'"}',
                   {'Content-Length': str(tls.MAX_MESSAGE_BYTES)})
        self.assertEqual(len(tls.request_json(self.bundle(), '/v1/report', {})['x']), tls.MAX_MESSAGE_BYTES-8)
        self.assertEqual(self.response.read.call_args.args, (tls.MAX_MESSAGE_BYTES+1,))

    def test_malformed_or_truncated_json_and_connection_loss(self):
        for raw, headers in ((b'[]', {}), (b'null', {}), (b'\xff', {}), (b'{', {}),
                             (b'{"x":NaN}', {}), (b'{"x":1e9999}', {}),
                             (b'{"x":1,"x":2}', {}),
                             (b'{}', {'Content-Length': '10'})):
            self.reply(raw, headers)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                tls.request_json(self.bundle(), '/v1/report', {})
        self.response.read.side_effect = ConnectionResetError('connection lost')
        with self.assertRaises(ConnectionResetError):
            tls.request_json(self.bundle(), '/v1/report', {})


@contextlib.contextmanager
def temporary_key_acl():
    """Explicit test-only ACL mock: non-admin Windows users can load temporary keys."""
    if sys.platform == 'win32':
        with patch.object(permissions, 'windows_acl') as mocked:
            yield mocked
    else:
        yield None


class RealTLSFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('cryptography') is None:
            raise unittest.SkipTest('cryptography server dependency is not installed')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        with temporary_key_acl():
            cls.identity = tls.generate_certificate(cls.root / 'ip', 'https://127.0.0.1')
            cls.dns_identity = tls.generate_certificate(cls.root / 'dns', 'localhost')
            cls.rotated_identity = tls.generate_certificate(cls.root / 'rotated', '127.0.0.1')


class CertificateTests(RealTLSFixture):
    def test_explicit_extensions_and_fingerprint(self):
        from cryptography import x509
        from cryptography.x509.oid import ExtendedKeyUsageOID
        for identity, expected_type in ((self.identity, x509.IPAddress), (self.dns_identity, x509.DNSName)):
            cert = x509.load_pem_x509_certificate(identity['certificate_pem'].encode())
            extensions = cert.extensions
            self.assertEqual(cert.subject, cert.issuer)
            self.assertTrue(extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                            .get_values_for_type(expected_type))
            self.assertTrue(extensions.get_extension_for_class(x509.BasicConstraints).critical)
            usage = extensions.get_extension_for_class(x509.KeyUsage)
            self.assertTrue(usage.critical)
            self.assertTrue(usage.value.digital_signature)
            self.assertTrue(usage.value.key_cert_sign)
            self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH,
                          extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
            self.assertEqual(extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest,
                             extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier)
            self.assertEqual(tls._certificate(identity['certificate_pem']), identity['fingerprint'])
            self.assertEqual(Path(identity['cert_file']).read_text(), identity['certificate_pem'])

    def test_existing_identity_is_not_rotated_or_overwritten(self):
        before = {p: Path(p).read_bytes() for p in (self.identity['key_file'], self.identity['cert_file'])}
        with temporary_key_acl(), self.assertRaises(FileExistsError):
            tls.generate_certificate(self.root / 'ip', '127.0.0.1')
        for path, raw in before.items():
            self.assertEqual(Path(path).read_bytes(), raw)

    def test_acl_failure_happens_before_secret_write_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(path):
                self.assertEqual(Path(path).read_bytes(), b'')
                raise OSError('ACL failed')
            with patch.object(permissions, 'protect_private', side_effect=fail), self.assertRaises(OSError):
                tls.generate_certificate(directory, 'localhost')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_certificate_preserved_if_key_was_new(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory, 'server-cert.pem')
            cert.write_bytes(b'existing identity')
            with temporary_key_acl(), self.assertRaises(FileExistsError):
                tls.generate_certificate(directory, 'localhost')
            self.assertEqual(cert.read_bytes(), b'existing identity')
            self.assertFalse(Path(directory, 'server-key.pem').exists())

    @unittest.skipIf(sys.platform == 'win32', 'Real POSIX permissions require POSIX')
    def test_generated_key_is_mode_600(self):
        self.assertEqual(Path(self.identity['key_file']).stat().st_mode & 0o777, 0o600)


class HTTPSIntegrationTests(RealTLSFixture):
    def issued_identity(self, directory, *, expired=False):
        """A different leaf that chains to the pinned anchor, for the live pin test."""
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        issuer = x509.load_pem_x509_certificate(self.identity['certificate_pem'].encode())
        issuer_key = serialization.load_pem_private_key(Path(self.identity['key_file']).read_bytes(), password=None)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Alternate Lifetime Server')]))
                .issuer_name(issuer.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now-dt.timedelta(days=2))
                .not_valid_after(now-dt.timedelta(days=1) if expired else now+dt.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),
                               critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                               key_encipherment=True, data_encipherment=False, key_agreement=False,
                               key_cert_sign=False, crl_sign=False, encipher_only=False,
                               decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
                .sign(issuer_key, hashes.SHA256()))
        cert_file = Path(directory, 'test-cert.pem')
        key_file = Path(directory, 'test-key.pem')
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        return dict(cert_file=str(cert_file), key_file=str(key_file))

    @contextlib.contextmanager
    def server(self, identity, handler=None):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.path, self.headers.get('Authorization'), json.loads(raw)))
                if handler is not None:
                    handler(self)
                    return
                body = json.dumps({'ok': True, 'text': 'Ti\u1ebfng Vi\u1ec7t'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(identity['cert_file'], identity['key_file'])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs=dict(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield server.server_port, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def bundle_for(self, identity, port, host='127.0.0.1'):
        return tls.encode_bundle(f'https://{host}:{port}', 'pairing-secret', identity['certificate_pem'])

    def test_real_strict_self_signed_ip_and_dns_verification(self):
        for identity, host in ((self.identity, '127.0.0.1'), (self.dns_identity, 'localhost')):
            with self.subTest(host=host), self.server(identity) as (port, requests):
                result = tls.request_json(self.bundle_for(identity, port, host), '/v1/ingest',
                                          {'code': 'pairing-secret'}, 'device-secret')
                self.assertEqual(result, {'ok': True, 'text': 'Ti\u1ebfng Vi\u1ec7t'})
                self.assertEqual(requests, [('/v1/ingest', 'Bearer device-secret', {'code': 'pairing-secret'})])

    def test_real_hostname_mismatch_never_receives_secret(self):
        with self.server(self.identity) as (port, requests):
            with self.assertRaises(ssl.SSLCertVerificationError):
                tls.request_json(self.bundle_for(self.identity, port, 'localhost'), '/v1/ingest',
                                 {'code': 'pairing-secret'}, 'device-secret')
            self.assertEqual(requests, [])

    def test_real_untrusted_rotated_certificate_never_receives_secret(self):
        with self.server(self.rotated_identity) as (port, requests):
            with self.assertRaises(ssl.SSLCertVerificationError):
                tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest',
                                 {'code': 'pairing-secret'}, 'device-secret')
            self.assertEqual(requests, [])

    def test_real_chain_valid_but_wrong_leaf_fingerprint_never_receives_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            alternate = self.issued_identity(directory)
            with self.server(alternate) as (port, requests):
                # First prove normal strict chain/hostname verification succeeds.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.verify_flags |= ssl.VERIFY_X509_STRICT
                context.load_verify_locations(cadata=self.identity['certificate_pem'])
                connection = tls.http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=2)
                try:
                    connection.connect()
                    self.assertNotEqual(hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest(),
                                        self.identity['fingerprint'])
                finally:
                    connection.close()
                with self.assertRaisesRegex(ssl.SSLCertVerificationError, 'fingerprint mismatch'):
                    tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest',
                                     {'code': 'pairing-secret'}, 'device-secret')
                self.assertEqual(requests, [])

    def test_real_expired_certificate_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as directory:
            alternate = self.issued_identity(directory, expired=True)
            with self.server(alternate) as (port, requests):
                with self.assertRaises(ssl.SSLCertVerificationError) as caught:
                    tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest',
                                     {'code': 'pairing-secret'}, 'device-secret')
                self.assertEqual(caught.exception.verify_code, 10)  # X509_V_ERR_CERT_HAS_EXPIRED
                self.assertEqual(requests, [])

    def test_real_redirect_is_not_followed(self):
        def redirect(handler):
            handler.send_response(307)
            handler.send_header('Location', 'https://127.0.0.1:1/steal')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
        with self.server(self.identity, redirect) as (port, requests):
            with self.assertRaises(tls.HTTPSRequestError) as caught:
                tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest', {})
            self.assertEqual(caught.exception.status, 307)
            self.assertEqual(len(requests), 1)

    def test_real_slow_response_times_out(self):
        def slow(handler):
            time.sleep(0.2)
        with self.server(self.identity, slow) as (port, requests):
            with self.assertRaises(TimeoutError):
                tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest', {}, timeout=0.05)
            self.assertEqual(len(requests), 1)

    def test_real_oversized_chunked_response_rejected(self):
        def oversized(handler):
            handler.send_response(200)
            handler.send_header('Transfer-Encoding', 'chunked')
            handler.end_headers()
            body = b'x' * (tls.MAX_MESSAGE_BYTES+1)
            handler.wfile.write(f'{len(body):X}\r\n'.encode() + body + b'\r\n0\r\n\r\n')
        with self.server(self.identity, oversized) as (port, requests):
            with self.assertRaisesRegex(ValueError, '1 MiB'):
                tls.request_json(self.bundle_for(self.identity, port), '/v1/ingest', {})
            self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
