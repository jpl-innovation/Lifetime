"""Certificate generation and pinned HTTPS transport for Lifetime (no enrollment policy).

Clients use only the standard library. ``cryptography`` is imported exclusively by
``generate_certificate``. A bundle is a versioned, URL-safe string containing the
explicit HTTPS origin, pairing code, certificate and its SHA-256 fingerprint.
Certificate rotation requires a new bundle; certificates are never overwritten.
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
import ssl
from urllib.parse import urlsplit

import lifetime_permissions as permissions


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024
MAX_CERTIFICATE_BYTES = 16 * 1024
BUNDLE_PREFIX = 'lifetime-enrollment-v1:'


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
    if not isinstance(value, dict) or set(value) != {
            'address', 'code', 'certificate_pem', 'fingerprint'}:
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
    return dict(address=address, code=code, certificate_pem=value['certificate_pem'],
                fingerprint=fingerprint)


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


def generate_certificate(directory, address):
    """Create one self-signed TLS trust anchor with a DNS or IP SAN, exclusively.

    ``address`` may be an HTTPS origin or a bare DNS name/IP. The key is protected
    before any secret bytes are written. No existing identity is ever replaced.
    """
    if not isinstance(address, str):
        raise ValueError('Invalid server address')
    host = _address(address)[1] if '://' in address else _hostname(address)
    # Server-only dependency: never needed to decode a bundle or make a request.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Lifetime Server')])
    try:
        san = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - dt.timedelta(minutes=5))
                   .not_valid_after(now + dt.timedelta(days=365))
                   .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                  key_encipherment=True, data_encipherment=False, key_agreement=False,
                                  key_cert_sign=True, crl_sign=True, encipher_only=False,
                                  decipher_only=False), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
                   .sign(key, hashes.SHA256()))
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


class HTTPSRequestError(ValueError):
    """An HTTP failure, without echoing potentially secret response content."""

    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTPS request failed (HTTP {status})')


def request_json(bundle, path, payload, credential=None, timeout=10):
    """POST a bounded JSON object after strict TLS and fingerprint verification.

    Accept an encoded bundle or the validated dictionary returned by decode_bundle.
    No proxy lookup, redirects, retries or enrollment decisions are performed.
    Network/TLS errors propagate; HTTP failures raise HTTPSRequestError with status.
    """
    value = decode_bundle(bundle) if isinstance(bundle, str) else _bundle(bundle)
    _, host, port = _address(value['address'])
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
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.load_verify_locations(cadata=value['certificate_pem'])
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, value['fingerprint']):
            raise ssl.SSLCertVerificationError('Server certificate fingerprint mismatch')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'Connection': 'close'}
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
