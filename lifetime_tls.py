"""Certificate generation and pinned HTTPS transport for Lifetime (no enrollment policy).

Clients use only the standard library. ``cryptography`` is imported only by the
server-side certificate helpers. A bundle is a versioned, URL-safe string containing
the explicit HTTPS origin, pairing code, certificate and its SHA-256 fingerprint.

Pinning: a computer first trusts exactly the certificate it paired with. After that
certificate has matched on a real connection, it also pins the certificate's public key
(``spki``, SHA-256 of the SubjectPublicKeyInfo). The server renews its certificate from
the same key, so a renewed certificate (new dates or names) is accepted only when it
carries the pinned key and passes the usual strict checks; a different key never is.
"""
import base64
import binascii
import datetime as dt
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
from urllib.parse import urlsplit

import lifetime_permissions as permissions


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024
MAX_CERTIFICATE_BYTES = 16 * 1024
BUNDLE_PREFIX = 'lifetime-enrollment-v1:'
CERTIFICATE_DAYS = 397                      # the longest validity browsers accept; renewal is automatic
RENEW_BEFORE = dt.timedelta(days=30)
MAX_NAMES = 16


def _hostname(value):
    if not value or '%' in value:
        raise ValueError('Invalid server hostname or IP address')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    # Require explicit, unambiguous ASCII DNS names (IDNA A-labels are accepted).
    if (len(value) > 253 or re.fullmatch(r'[0-9.]+', value)
            or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                       for label in value.split('.'))):
        raise ValueError('Invalid server hostname or IP address')
    return value.lower()


def _address(value):
    """Return a canonical HTTPS origin, hostname and port; never accept a base path."""
    if (not isinstance(value, str) or len(value) > 2048
            or not value.startswith('https://')
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
            or any(c in value for c in '\\@%?#')):
        raise ValueError('Server address must be an explicit HTTPS origin')
    try:
        parts = urlsplit(value)
        host = _hostname(parts.hostname)
        port = parts.port
    except (ValueError, TypeError):
        raise ValueError('Invalid HTTPS server address') from None
    if parts.path not in ('', '/') or not parts.netloc:
        raise ValueError('Server address cannot contain a path')
    authority = f'[{host}]' if ':' in host else host
    # Validate the original authority too: urlsplit accepts empty/signed ports.
    raw_host = rf'\[{re.escape(parts.hostname)}\]' if ':' in host else re.escape(parts.hostname)
    if not re.fullmatch(raw_host + r'(?::[0-9]{1,5})?', parts.netloc, re.IGNORECASE):
        raise ValueError('Invalid HTTPS server authority')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid HTTPS server port')
    if port is not None:
        authority += f':{port}'
    return f'https://{authority}', host, port or 443


def _certificate(pem):
    if (not isinstance(pem, str) or len(pem) > MAX_CERTIFICATE_BYTES
            or not re.fullmatch(r'-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+\r?\n'
                                r'-----END CERTIFICATE-----\s*', pem, re.ASCII)):
        raise ValueError('Bundle must contain exactly one PEM certificate')
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
    except (ValueError, ssl.SSLError):
        raise ValueError('Invalid bundle certificate') from None
    return hashlib.sha256(der).hexdigest()


def _der(data, offset, tag):
    """One DER element at ``offset`` with the expected tag: return (contents start, end)."""
    if data[offset] != tag:
        raise ValueError('Unexpected certificate structure')
    length, offset = data[offset + 1], offset + 2
    if length & 0x80:
        size = length & 0x7F
        if not 1 <= size <= 3:
            raise ValueError('Unexpected certificate length')
        length, offset = int.from_bytes(data[offset:offset + size], 'big'), offset + size
    if offset + length > len(data):
        raise ValueError('Truncated certificate')
    return offset, offset + length


def spki_pin(der):
    """SHA-256 of the certificate's SubjectPublicKeyInfo: the same for every certificate made from one key."""
    try:
        start, _ = _der(der, 0, 0x30)                   # Certificate
        position, _ = _der(der, start, 0x30)            # TBSCertificate
        if der[position] == 0xA0:                       # [0] version
            position = _der(der, position, 0xA0)[1]
        for tag in (0x02, 0x30, 0x30, 0x30, 0x30):      # serial, signature, issuer, validity, subject
            position = _der(der, position, tag)[1]
        end = _der(der, position, 0x30)[1]              # subjectPublicKeyInfo
    except (IndexError, TypeError):
        raise ValueError('Invalid certificate') from None
    return hashlib.sha256(der[position:end]).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field')
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError('Non-finite JSON number')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite JSON number')
    return result


def _load_json(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=_invalid_constant, parse_float=_finite_float)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('Invalid JSON object') from None
    if not isinstance(value, dict):
        raise ValueError('JSON message must be an object')
    return value


def _bundle(value):
    fields = {'address', 'code', 'certificate_pem', 'fingerprint'}
    if not isinstance(value, dict) or not fields <= set(value) <= fields | {'spki'}:
        raise ValueError('Invalid enrollment bundle fields')
    address, _, _ = _address(value['address'])
    code = value['code']
    if (not isinstance(code, str) or not 1 <= len(code) <= 256
            or any(ord(c) < 33 or ord(c) > 126 for c in code)):
        raise ValueError('Invalid pairing code')
    fingerprint = _certificate(value['certificate_pem'])
    supplied = value['fingerprint']
    if (not isinstance(supplied, str) or not re.fullmatch('[0-9a-f]{64}', supplied)
            or not hmac.compare_digest(supplied, fingerprint)):
        raise ValueError('Certificate fingerprint mismatch')
    result = dict(address=address, code=code, certificate_pem=value['certificate_pem'],
                  fingerprint=fingerprint)
    if 'spki' in value:     # the stored key pin always belongs to the stored certificate
        key = value['spki']
        if (not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key)
                or not hmac.compare_digest(key, spki_pin(ssl.PEM_cert_to_DER_cert(value['certificate_pem'])))):
            raise ValueError('Certificate key mismatch')
        result['spki'] = key
    return result


def encode_bundle(address, code, certificate_pem):
    """Encode trust material for explicit, out-of-band enrollment (contains a secret)."""
    value = _bundle(dict(address=address, code=code, certificate_pem=certificate_pem,
                         fingerprint=_certificate(certificate_pem)))
    raw = json.dumps(dict(version=1, **value), separators=(',', ':'),
                     ensure_ascii=True).encode('ascii')
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError('Enrollment bundle is too large')
    return BUNDLE_PREFIX + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_bundle(value):
    """Validate a versioned bundle and return its four transport fields."""
    if (not isinstance(value, str) or not value.startswith(BUNDLE_PREFIX)
            or len(value) > len(BUNDLE_PREFIX) + (MAX_BUNDLE_BYTES + 2) // 3 * 4):
        raise ValueError('Invalid enrollment bundle')
    encoded = value[len(BUNDLE_PREFIX):]
    if not re.fullmatch('[A-Za-z0-9_-]+', encoded):
        raise ValueError('Invalid enrollment bundle encoding')
    try:
        raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Invalid enrollment bundle encoding') from None
    if (len(raw) > MAX_BUNDLE_BYTES
            or base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') != encoded):
        raise ValueError('Invalid enrollment bundle encoding')
    data = _load_json(raw)
    version = data.pop('version', None)
    if type(version) is not int or version != 1:
        raise ValueError('Unsupported enrollment bundle version')
    return _bundle(data)


def protect_private(path):
    """Restrict a regular private file through the shared permission layer."""
    return permissions.protect_private(path)


def server_names(names):
    """Validate and de-duplicate certificate names (DNS names or IPs), keeping their order."""
    if not isinstance(names, (list, tuple)) or not 1 <= len(names) <= MAX_NAMES + 1:
        raise ValueError('Invalid server names')
    result = []
    for name in names:
        if not isinstance(name, str):
            raise ValueError('Invalid server names')
        name = _hostname(name.strip())
        if name not in result:
            result.append(name)
    return result


def _issue(key, names, now=None):
    """A self-signed certificate for ``names`` (first one is the main address), signed by ``key``."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Lifetime Server')])
    san = []
    for host in server_names(names):
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            san.append(x509.DNSName(host))
    now = now or dt.datetime.now(dt.timezone.utc)
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - dt.timedelta(minutes=5))
                   .not_valid_after(now + dt.timedelta(days=CERTIFICATE_DAYS))
                   .add_extension(x509.SubjectAlternativeName(san), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                  key_encipherment=True, data_encipherment=False, key_agreement=False,
                                  key_cert_sign=True, crl_sign=True, encipher_only=False,
                                  decipher_only=False), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
                   .sign(key, hashes.SHA256()))


def generate_certificate(directory, address, now=None):
    """Create one self-signed TLS trust anchor with a DNS or IP SAN, exclusively.

    ``address`` may be an HTTPS origin or a bare DNS name/IP. The key is protected
    before any secret bytes are written. No existing identity is ever replaced.
    ``now`` is the issue time (tests back-date certificates with it).
    """
    if not isinstance(address, str):
        raise ValueError('Invalid server address')
    host = _address(address)[1] if '://' in address else _hostname(address)
    # Server-only dependency: never needed to decode a bundle or make a request.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = _issue(key, [host], now)
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode('ascii')
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_file, cert_file = root / 'server-key.pem', root / 'server-cert.pem'
    created = []
    try:
        for path, content in ((key_file, private), (cert_file, pem.encode('ascii'))):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            created.append(path)
            with os.fdopen(fd, 'wb') as output:
                if path == key_file:
                    protect_private(path)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise
    return dict(cert_file=str(cert_file), key_file=str(key_file), certificate_pem=pem,
                fingerprint=hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest())


def renew_certificate(directory, names, now=None):
    """A new self-signed certificate (PEM) for ``names`` from the server's existing key.

    Nothing is written: the caller validates the result before it replaces the old one.
    The key, and so the pin every computer holds, stays the same.
    """
    from cryptography.hazmat.primitives import serialization
    with (Path(directory) / 'server-key.pem').open('rb') as source:
        key = serialization.load_pem_private_key(source.read(64 * 1024), password=None)
    return _issue(key, names, now).public_bytes(serialization.Encoding.PEM).decode('ascii')


def certificate_info(pem):
    """Expiry (aware UTC), names and key pin of a PEM certificate (server side only)."""
    from cryptography import x509
    certificate = x509.load_pem_x509_certificate(pem.encode('ascii'))
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    names = [str(name) for name in san.get_values_for_type(x509.DNSName)]
    names += [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
    return dict(expires=certificate.not_valid_after_utc, names=names,
                key=spki_pin(ssl.PEM_cert_to_DER_cert(pem)))


class HTTPSRequestError(ValueError):
    """An HTTP failure, without echoing potentially secret response content."""

    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTPS request failed (HTTP {status})')


def peer_certificate(host, port, timeout):
    """Read the server's certificate (DER) without trusting it; no application data is sent."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secured:
            der = secured.getpeercert(binary_form=True)
    if not der or len(der) > MAX_CERTIFICATE_BYTES:
        raise ValueError('Invalid server certificate')
    return der


def check_peer(der, value):
    """Raise unless the server's certificate carries the pinned key, or for a legacy pin, is the pinned certificate."""
    try:
        if 'spki' in value:
            matches = hmac.compare_digest(spki_pin(der), value['spki'])
        else:
            matches = hmac.compare_digest(hashlib.sha256(der).hexdigest(), value['fingerprint'])
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise ssl.SSLCertVerificationError('Server key does not match the pinned key' if 'spki' in value
                                           else 'Server certificate fingerprint mismatch')


def _connect(host, port, pem, timeout):
    """A fully verified connection that trusts only ``pem``: chain, validity, name, strict X.509, TLS 1.2+."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.load_verify_locations(cadata=pem)
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        connection.connect()
    except BaseException:
        connection.close()
        raise
    return connection


def _pinned(value, timeout):
    """Open a verified connection to the pinned server; return it and the trust material to keep.

    The stored certificate is the only trust anchor, as before. With a key pin, a certificate the
    server renewed from the same key is learned: one handshake without application data reads it,
    it must carry the pinned key, and the connection is then made again, fully verified, trusting only
    that certificate. A legacy certificate pin is compared exactly and becomes a key pin only after it
    matched on this connection, so no key is ever trusted that was not trusted already.
    """
    _, host, port = _address(value['address'])
    try:
        connection = _connect(host, port, value['certificate_pem'], timeout)
    except ssl.SSLCertVerificationError:
        if 'spki' not in value:
            raise
        der = peer_certificate(host, port, timeout)
        check_peer(der, value)
        if hmac.compare_digest(hashlib.sha256(der).hexdigest(), value['fingerprint']):
            raise       # the pinned certificate itself failed (its name or dates): nothing new to learn
        pem = ssl.DER_cert_to_PEM_cert(der)
        value = dict(value, certificate_pem=pem, fingerprint=_certificate(pem))
        connection = _connect(host, port, pem, timeout)
    try:
        der = connection.sock.getpeercert(binary_form=True)
        check_peer(der, value)
        if 'spki' not in value:
            value = dict(value, spki=spki_pin(der))
        elif not hmac.compare_digest(hashlib.sha256(der).hexdigest(), value['fingerprint']):
            pem = ssl.DER_cert_to_PEM_cert(der)     # a renewed certificate that still chains to the stored one
            value = dict(value, certificate_pem=pem, fingerprint=_certificate(pem))
    except BaseException:
        connection.close()
        raise
    return connection, value


def verify_server(bundle, timeout=5):
    """Prove that ``bundle['address']`` is the pinned server (name and key); sends no application data.

    Returns the trust material to keep. Used to accept an address found by local discovery.
    """
    connection, trusted = _pinned(_bundle(bundle), timeout)
    connection.close()
    return trusted


def request_json(bundle, path, payload, credential=None, timeout=10):
    """POST a bounded JSON object after strict TLS and pin verification.

    Accept an encoded bundle or the validated dictionary returned by decode_bundle.
    A dictionary is updated in place when its pin moved from the certificate to the key,
    or when the server's renewed certificate was learned; callers persist it.
    No proxy lookup, redirects, retries or enrollment decisions are performed.
    Network/TLS errors propagate; HTTP failures raise HTTPSRequestError with status.
    """
    value = decode_bundle(bundle) if isinstance(bundle, str) else _bundle(bundle)
    if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
            or len(path) > 2048 or any(ord(c) <= 32 or ord(c) >= 127 for c in path)
            or '\\' in path or '#' in path
            or re.search(r'%(?![0-9A-Fa-f]{2})', path)):
        raise ValueError('Request path must be an origin-relative HTTP path')
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('Timeout must be a positive finite number')
    if credential is not None and (not isinstance(credential, str) or not 1 <= len(credential) <= 4096
                                  or any(ord(c) < 33 or ord(c) > 126 for c in credential)):
        raise ValueError('Invalid device credential')
    if not isinstance(payload, dict):
        raise ValueError('Request JSON must be an object')
    try:
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':')).encode('ascii')
    except (ValueError, TypeError, RecursionError):
        raise ValueError('Invalid request JSON') from None
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError('Request exceeds the 1 MiB limit')
    connection, trusted = _pinned(value, timeout)
    try:
        if isinstance(bundle, dict) and trusted != value:
            bundle.update(trusted)
        # Ask for the server's capability list; an older server ignores the header.
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'Connection': 'close', 'Lifetime-Accept': 'capabilities'}
        if credential is not None:
            headers['Authorization'] = f'Bearer {credential}'
        connection.request('POST', path, body=body, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise HTTPSRequestError(response.status)
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('Unsupported response content encoding')
        length = response.getheader('Content-Length')
        if length is not None and (not re.fullmatch('[0-9]+', length)
                                   or len(length) > 10 or int(length) > MAX_MESSAGE_BYTES):
            raise ValueError('Invalid or oversized response length')
        raw = response.read(MAX_MESSAGE_BYTES + 1)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError('Response exceeds the 1 MiB limit')
        if length is not None and len(raw) != int(length):
            raise ValueError('Incomplete JSON response')
        return _load_json(raw)
    finally:
        connection.close()
