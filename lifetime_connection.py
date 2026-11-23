"""Human enrollment codes and certificate bootstrap. Standard library only.

The out-of-band code authenticates the certificate before any HTTP bytes,
invitation secrets, identity or consent are sent. Discovery is never trusted.
"""
import hashlib
import hmac
import os
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
    existing strict CA, hostname, validity and extension checks and the pin.

    The commitment covers the whole certificate (unchanged since v1, so old and new
    computers and servers interoperate). The certificate fixes its public key, so the
    key pin taken from it after the first verified connection is equally authenticated.
    A certificate renewed after the code was issued no longer matches: the server
    issues codes against its current certificate, and an older code must be replaced.
    """
    origin, code = parse(code, origin)
    _, host, port = tls._address(origin)
    invitation_id, secret, expected = parts(code)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
        raise ValueError('Invalid bootstrap timeout')
    der = tls.peer_certificate(host, port, timeout)
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


def certificate_details(host, port, pem, timeout):
    """``pem`` as served at host:port (names, dates), read over a handshake that trusts only ``pem`` (chain,
    validity, strict X.509) but not yet a name. No application data is sent."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.check_hostname = False
    context.load_verify_locations(cadata=pem)
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secured:
            return secured.getpeercert() or {}


def certificate_names(host, port, pem, timeout):
    """The DNS names and IP addresses of ``pem`` as served at host:port (see certificate_details)."""
    names = certificate_details(host, port, pem, timeout).get('subjectAltName', ())
    return ([value for kind, value in names if kind == 'DNS'],
            [value.strip() for kind, value in names if kind == 'IP Address'])


def named_origin(bundle, host, port, code, timeout=2):
    """A server found by scanning addresses is reached at an address its certificate may not name.

    Every later request checks the name strictly, so an address the certificate does not cover would
    fail for ever ("hostname") and the server would never see the request to approve. Keep the address
    when the certificate covers it; otherwise move to one of its names that reaches the same server and
    passes the code check again; otherwise this server cannot be used from here.
    """
    import ipaddress
    dns, ips = certificate_names(host, port, tls.decode_bundle(bundle)['certificate_pem'], timeout)
    found = ipaddress.ip_address(host)
    if any(_same_ip(value, found) for value in ips):
        return bundle
    for name in dns:
        if '*' in name:
            continue
        try:
            return bootstrap(f'https://{name}:{port}', code, timeout=timeout)
        except (OSError, ValueError):
            continue
    raise ValueError('The server certificate does not name an address this computer can reach')


def _same_ip(value, found):
    import ipaddress
    try:
        return ipaddress.ip_address(value) == found
    except ValueError:
        return False


def _route_ipv4():
    """The IPv4 addresses this computer would send from (a UDP connect sends nothing)."""
    found = []
    for target in (('8.8.8.8', 53), ('224.0.0.251', 5353)):     # the default route, else the LAN multicast route
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                found.append(probe.getsockname()[0])
        except OSError:
            pass
    return found


def local_ipv4(named=True):
    """This computer's LAN IPv4 addresses. Debian and Ubuntu resolve the host name to 127.0.1.1, so the name
    alone finds no network there: the addresses the routes use are asked for as well. ``named=False`` gives the
    route addresses only (a host name also lists virtual adapters whose addresses change at every start)."""
    import ipaddress
    try:
        named = [item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)] if named else []
    except OSError:
        named = []
    result = []
    for value in named + _route_ipv4():
        ip = ipaddress.IPv4Address(value)
        if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast) and value not in result:
            result.append(value)
    return result


def find_server(code):
    """Bounded LAN fallback. Only bootstrap's commitment match is trusted."""
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor, as_completed
    code = normalize(code)
    networks = {ipaddress.IPv4Network(ip + '/24', strict=False) for ip in local_ipv4()}
    # ponytail: search only each local IPv4 /24, port 8443, at most 64 threads.
    def probe(host):
        try:
            with socket.create_connection((host, 8443), timeout=0.4):
                pass
            return named_origin(bootstrap('https://' + host + ':8443', code, timeout=0.4), host, 8443, code)
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


# What a copied report or log line must never carry. Masking too much is harmless; missing one is not. Every pattern
# is bounded (no unbounded run before a keyword) and lines are cut at MAX_LINE, so scrubbing stays linear in time.
MAX_LINE = 4000
_TOKEN_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_+/=-'
# A code: optional LTS, then five groups of four with one separator style (spaces, tabs, new lines, dots, dashes or
# underscores), or twenty characters in a row. Found at every position (a look-ahead), so a failed candidate that
# starts on a word before a code never hides the code.
_CODE = re.compile(r'(?i)(?<![0-9A-Z])(?=(?P<all>(?:(?P<lts>LTS)[ \t._-]{0,4})?(?P<body>[0-9A-Z]{4}(?P<sep>[ \t\r\n._-]{1,4})'
                   r'[0-9A-Z]{4}(?:(?P=sep)[0-9A-Z]{4}){2}(?P=sep)[0-9A-Z]{3}[0-9A-Z*~$=]|[0-9A-Z]{19}[0-9A-Z*~$=]))(?![0-9A-Z]))')
_PARTIAL = re.compile(r'(?i)(?<![0-9A-Z])LTS(?![ \t._-]{0,4}\*)(?:[ \t._-]{0,4}[0-9A-Z*~$=]){4,20}(?![0-9A-Z])')
_KEY = (r'(?<![\w-])[\w-]{0,40}(?:token|passw\w{0,10}|pass|pwd|secret|credential|serial|webhook|api[_-]?key|authorization'
        r'|username)[\w-]{0,40}(?: number| no\.?| key| url| id)?(?:\\?["\'])?')
_LINE = (
    (re.compile(r'lifetime-enrollment-v1:\S*'), 'lifetime-enrollment-v1:****'),
    (re.compile(r'(?i)\b([a-z][a-z0-9+.-]{1,15}://)[^\s/@"\'<>]{1,200}@'), r'\1****@'),       # user:password@
    (re.compile(r'(?i)\b(https?://[^\s/?#"\'<>]{1,255})[/?#][^\s"\'<>]+'), r'\1/****'),      # paths and queries
    (re.compile(r'(?i)\bBearer\s+\S+'), 'Bearer ****'),
    (re.compile(r'(?i)(' + _KEY + r'(?:[ \t]*[:=][ \t]*|[ \t]+is[ \t]+))'
                r'("(?:[^"\\]|\\.)*"|\\"(?:[^"\\]|\\[^"])*\\"|\'[^\']*\'|[^\s,;]+)'), r'\1****'),
    (re.compile(r'(?i)\b([a-z]:\\{1,2}(?:users|documents and settings)\\{1,2})[^\\/\r\n"\']+'), r'\1****'),
    (re.compile(r'(?<![\w.-])(/home/|/Users/)[^/\s"\']+'), r'\1****'),
)
_KEY_ALONE = re.compile(r'(?i)' + _KEY + r'[ \t]*[:=][ \t]*$')       # its value is on the next line
_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def _codes(text):
    """Mask code-shaped strings: after LTS always, else only when the check character is right (so a host name like
    DESK-TOP1-ABCD-EF12-3456 stays)."""
    spans = []
    for match in _CODE.finditer(text):
        if not match.group('lts'):
            try:
                normalize(re.sub(r'[ \t\r\n._-]', '', match.group('body')))
            except ValueError:
                continue
        spans.append(match.span('all'))
    parts, last = [], 0
    for start, end in spans:
        if start >= last:
            parts += [text[last:start], 'LTS-****']
        last = max(last, end)
    return ''.join(parts) + text[last:]


def _token(match):
    value = match.group(0)
    public = re.fullmatch('[0-9a-fA-F]{64}', value) or _UUID.fullmatch(value)       # SHA-256 fingerprints, device IDs
    return value if public else '****'


def scrub(text):
    """``text`` without what scrub masks (see docs/notes/1.3.0-diagnostics.md): connection codes, enrollment bundles,
    Bearer values, values of secret-looking keys, URL user names, passwords, paths and queries, long random tokens
    and user names in home folder paths. Lines longer than MAX_LINE are cut. Structured data is also limited to named
    fields by its callers."""
    lines = []
    for line in str(text).split('\n'):
        if len(line) > MAX_LINE:        # never leave the start of a cut token behind
            line = line[:MAX_LINE].rstrip(_TOKEN_CHARS) + ' …'
        lines.append(line)
    text = _PARTIAL.sub('LTS-****', _codes('\n'.join(lines)))
    result, hidden = [], False
    for line in text.split('\n'):
        if hidden and line.strip():
            line, hidden = line[:len(line) - len(line.lstrip())] + '****', False
        elif not hidden:
            hidden = bool(_KEY_ALONE.search(line))
        for pattern, replacement in _LINE:
            line = pattern.sub(replacement, line)
        result.append(re.sub(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])', _token, line))
    return '\n'.join(result)


def plain(value, limit=300):
    """``value`` as one line of at most ``limit`` printable characters: names from the network can carry new lines or
    control characters that would forge lines of their own in a report."""
    return ''.join(char for char in str(value) if char.isprintable())[:limit]


# Diagnose connection and Self check lines: {state, text, fix, values}, text and fix being catalogue keys.
CHECK_WORDS = {'good': 'Passed', 'warning': 'Check', 'danger': 'Problem', 'info': 'Info', 'neutral': 'Skipped'}


def check_lines(checks, t):
    """Diagnosis or Self check lines as plain text in the language of ``t``: '[Problem] cause' then what to do."""
    lines = []
    for check in checks:
        values = {key: plain(value) for key, value in (check.get('values') or {}).items()}
        lines.append(f"[{t(CHECK_WORDS.get(check['state'], 'Info'))}] {t(check['text'], **values)}")
        if check.get('fix'):
            lines.append('    ' + t('What to do: {fix}', fix=t(check['fix'], **values)))
    return lines


# PowerShell writes its output in the console's OEM code page unless told otherwise.
UTF8_OUTPUT = '[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); '


def run_read(command, run=None, timeout=5):
    """(exit code, output) of a read-only command (lifetime_signals.run_command: UTF-8, undecodable bytes replaced),
    or None when it cannot run or its output cannot be read. ``run`` replaces subprocess.run in tests."""
    import subprocess
    if run is None:
        from lifetime_signals import run_command
        return run_command(command, timeout)
    try:
        result = run(command, capture_output=True, timeout=timeout, encoding='utf-8', errors='replace',
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None if result.stdout is None else (result.returncode, result.stdout)


def log_tail(paths, lines=200):
    """[(file name, its last ``lines`` lines)] of each log in ``paths`` that exists and may be read."""
    found = []
    for path in paths:
        try:
            with open(path, 'rb') as source:
                source.seek(0, 2)
                source.seek(max(0, source.tell() - 256 * 1024))
                text = source.read().decode('utf-8', 'replace')
        except OSError:
            continue
        found.append((os.path.basename(path), text.splitlines()[-lines:]))
    return found


def report(sections):
    """Copy diagnostics: titled sections of lines as plain text, scrubbed (see scrub)."""
    return scrub('\n\n'.join('\n'.join([f'== {title} =='] + [str(line) for line in lines]) for title, lines in sections))
