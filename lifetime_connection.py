"""Human enrollment codes and certificate bootstrap. Standard library only.

The out-of-band code authenticates the certificate before any HTTP bytes,
invitation secrets, identity or consent are sent. Discovery is never trusted.
"""
import hashlib
import hmac
import re
import secrets
import socket
import ssl

import lifetime_tls as tls

ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
CHECK = ALPHABET + '*~$=U'
PREFIX = 'lifetime-connect-v1:'


def _check(payload):
    number = 0
    for char in payload:
        number = (number * 32 + ALPHABET.index(char)) % 37
    return CHECK[number]


def normalize(code):
    if not isinstance(code, str) or len(code) > 100:
        raise ValueError('Invalid connection code')
    raw = re.sub(r'[-\s]', '', code.upper())
    if raw.startswith('LTS'):
        raw = raw[3:]
    raw = raw.translate(str.maketrans('OIL', '011'))
    if (len(raw) != 20 or any(c not in ALPHABET for c in raw[:-1])
            or not hmac.compare_digest(_check(raw[:-1]), raw[-1])):
        raise ValueError('Check the connection code')
    return 'LTS-' + '-'.join(raw[i:i+4] for i in range(0, 20, 4))


def parts(code):
    raw = normalize(code)[4:].replace('-', '')
    return raw[:5], raw[5:11], raw[11:19]


def commitment(fingerprint, invitation_id, secret):
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ValueError('Invalid certificate fingerprint')
    digest = hashlib.sha256(b'Lifetime connection v1\0' + bytes.fromhex(fingerprint)
                            + (invitation_id + secret).encode('ascii')).digest()
    value = int.from_bytes(digest[:5], 'big')
    return ''.join(ALPHABET[(value >> shift) & 31] for shift in range(35, -1, -5))


def encode(invitation_id, secret, fingerprint):
    if (len(invitation_id) != 5 or len(secret) != 6
            or any(c not in ALPHABET for c in invitation_id + secret)):
        raise ValueError('Invalid invitation components')
    payload = invitation_id + secret + commitment(fingerprint, invitation_id, secret)
    return normalize(payload + _check(payload))


def issue(fingerprint):
    return encode(''.join(secrets.choice(ALPHABET) for _ in range(5)),
                  ''.join(secrets.choice(ALPHABET) for _ in range(6)), fingerprint)


def address(value):
    """Allow a bare name/IP[:port] in the small dialog, canonicalize to HTTPS."""
    if not isinstance(value, str):
        raise ValueError('Enter a server address')
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
        # Bare IPv6 must be bracketed, as in any HTTPS authority.
        if not re.search(r':\d+$', value) and not value.endswith(']'):
            value += ':8443'
        elif value.endswith(']'):
            value += ':8443'
    return tls._address(value)[0]


def connection_line(origin, code):
    return PREFIX + address(origin) + ' ' + normalize(code)


def parse(value, origin=''):
    if not isinstance(value, str) or len(value) > 2300:
        raise ValueError('Invalid connection line')
    value = value.strip()
    if value.startswith(PREFIX):
        fields = value[len(PREFIX):].split()
        if len(fields) != 2:
            raise ValueError('Invalid connection line')
        origin, value = fields
    return address(origin), normalize(value)


def bootstrap(origin, code, timeout=5):
    """Fetch only a TLS certificate, verify commitment, then build strict pin.

    The first handshake cannot use PKI validation for a private self-signed CA.
    It carries no application data. All subsequent requests use request_json's
    existing strict CA, hostname, validity, extension and exact SHA-256 checks.
    """
    origin, code = parse(code, origin)
    _, host, port = tls._address(origin)
    invitation_id, secret, expected = parts(code)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
        raise ValueError('Invalid bootstrap timeout')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secured:
            der = secured.getpeercert(binary_form=True)
    if not der or len(der) > tls.MAX_CERTIFICATE_BYTES:
        raise ValueError('Invalid server certificate')
    fingerprint = hashlib.sha256(der).hexdigest()
    if not hmac.compare_digest(commitment(fingerprint, invitation_id, secret), expected):
        raise ssl.SSLCertVerificationError('Connection code does not match this server')
    return tls.encode_bundle(origin, code, ssl.DER_cert_to_PEM_cert(der))


def format_code(value):
    """Format partial human input without treating it as a valid code."""
    raw = re.sub(r'[-\s]', '', value.upper())
    if raw in ('L', 'LT', 'LTS'):
        return raw
    if raw.startswith('LTS'):
        raw = raw[3:]
    raw = raw.translate(str.maketrans('OIL', '011'))[:20]
    return 'LTS-' + '-'.join(raw[i:i+4] for i in range(0, len(raw), 4)) if raw else ''


def find_server(code):
    """Bounded LAN fallback. Only bootstrap's commitment match is trusted."""
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor, as_completed
    code = normalize(code)
    networks = set()
    for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = ipaddress.IPv4Address(item[4][0])
        if not ip.is_loopback and not ip.is_link_local and not ip.is_unspecified:
            networks.add(ipaddress.IPv4Network(str(ip) + '/24', strict=False))
    # ponytail: search only each local IPv4 /24, port 8443, at most 64 threads.
    def probe(host):
        try:
            with socket.create_connection((host, 8443), timeout=0.4):
                pass
            return bootstrap('https://' + host + ':8443', code, timeout=0.4)
        except (OSError, ValueError):
            return None
    with ThreadPoolExecutor(max_workers=64) as pool:
        jobs = [pool.submit(probe, str(host)) for network in sorted(networks) for host in network.hosts()]
        for job in as_completed(jobs):
            result = job.result()
            if result:
                for pending in jobs:
                    pending.cancel()
                return result
    raise ValueError('No matching server')
