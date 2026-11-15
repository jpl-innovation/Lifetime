"""Agentless checks for devices without Lifetime (printers, routers, NAS, websites): ping, TCP connect and HTTP(S).

One worker thread runs one asyncio loop with a global concurrency cap, a minimum spacing per target, jitter on every
interval, a deadline on every attempt and a bounded result queue. The Monitor (the single monitoring writer) drains
the queue; nothing here opens a database or the awake-time ledger.

Network policy (SSRF): a check may reach only addresses inside the administrator's allowed scopes ('private' LAN
ranges, 'public' internet addresses, or explicit CIDRs). Loopback and link-local addresses are refused unless an
explicit CIDR names them; cloud metadata addresses, the server's own admin port, unspecified, multicast and reserved
addresses are always refused. Every attempt and every redirect resolves the name again, checks every address it
resolves to and connects to the checked address itself (no second lookup). Requests never carry credentials,
cookies or a body; a URL with a user name or password is refused.

Ping uses the operating system's ping with a fixed argument list and no shell, on an address that passed the policy:
Windows ``ping -n 1 -w <ms> <ip>`` (success needs a TTL= reply line, since Windows also exits 0 for some
"unreachable" replies), elsewhere ``ping -c 1 -W <s> <ip>`` (exit status 0 means a reply).
"""
import asyncio
import ipaddress
import queue
import random
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import datetime as dt
from urllib.parse import urljoin, urlsplit

KINDS = ('ping', 'tcp', 'http')
CONCURRENCY = 16            # attempts in flight at once, all checks together
QUEUE_SIZE = 1000           # results waiting for the monitor; scheduling pauses while it is full
SPACING = 2.0               # seconds between two attempts to the same target
MAX_CHECKS = 500
SCOPE_WORDS = ('private', 'public')
PRIVATE = tuple(ipaddress.ip_network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10',
                                                      'fc00::/7'))
METADATA = tuple(ipaddress.ip_address(item) for item in ('169.254.169.254', 'fd00:ec2::254', '100.100.100.200'))
HOST = re.compile(r'(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?')
DEFAULTS = dict(kind='tcp', host='', port=443, url='', method='GET', status_min=200, status_max=399, keyword='',
                verify_tls=True, redirects=3, max_bytes=262144, interval=60, timeout=5, group='', enabled=True)
CATEGORIES = ('ok', 'dns', 'blocked', 'refused', 'timeout', 'unreachable', 'tls', 'status', 'oversize', 'keyword', 'error')


class CheckError(ValueError):
    """An invalid check or network scope."""


def _text(value, limit, name, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) \
            or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CheckError(f'Invalid {name}')
    return value.strip()


def _number(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise CheckError(f'Invalid {name}')
    return value


def _host(value):
    value = _text(value, 253, 'host').strip('[]')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if not HOST.fullmatch(value):
            raise CheckError('Invalid host') from None
        return value.lower()


def _url(value):
    value = _text(value, 2048, 'address')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or ' ' in value:
        raise CheckError('The address must start with http:// or https://')
    if parts.username is not None or parts.password is not None:
        raise CheckError('Addresses with a user name or password are not allowed')
    try:
        parts.port
    except ValueError:
        raise CheckError('Invalid port') from None
    _host(parts.hostname)
    return value


def validate(check):
    """A complete, normalized check from the dashboard's fields (unknown fields are refused)."""
    if not isinstance(check, dict) or set(check) - set(DEFAULTS) - {'name'}:
        raise CheckError('Invalid check')
    value = dict(DEFAULTS, **check)
    result = dict(name=_text(value.get('name'), 80, 'name'), kind=value['kind'],
                  interval=_number(value['interval'], 30, 86400, 'interval'),
                  timeout=_number(value['timeout'], 1, 30, 'timeout'), group=_text(value['group'], 128, 'group', True))
    if value['kind'] not in KINDS:
        raise CheckError('Invalid kind')
    if type(value['enabled']) is not bool:
        raise CheckError('Invalid enabled flag')
    result['enabled'] = value['enabled']
    if value['kind'] == 'http':
        if value['method'] not in ('GET', 'HEAD') or type(value['verify_tls']) is not bool:
            raise CheckError('Invalid request')
        low, high = _number(value['status_min'], 100, 599, 'status'), _number(value['status_max'], 100, 599, 'status')
        if low > high:
            raise CheckError('Invalid status range')
        keyword = _text(value['keyword'], 100, 'keyword', True)
        if keyword and value['method'] == 'HEAD':
            raise CheckError('A keyword needs GET')
        result.update(url=_url(value['url']), method=value['method'], status_min=low, status_max=high, keyword=keyword,
                      verify_tls=value['verify_tls'], redirects=_number(value['redirects'], 0, 5, 'redirect limit'),
                      max_bytes=_number(value['max_bytes'], 1024, 1048576, 'size limit'))
    else:
        result['host'] = _host(value['host'])
        if value['kind'] == 'tcp':
            result['port'] = _number(value['port'], 1, 65535, 'port')
    return result


def target(check):
    """What the rate limit counts as one target: the host (and port), or the URL's host and port."""
    if check['kind'] == 'http':
        parts = urlsplit(check['url'])
        return (parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80))
    return (check['host'], check.get('port'))


def validate_scopes(scopes):
    if not isinstance(scopes, list) or len(scopes) > 64:
        raise CheckError('Invalid network scopes')
    result = []
    for scope in scopes:
        scope = _text(scope, 64, 'network scope').lower()
        if scope not in SCOPE_WORDS:
            try:
                scope = str(ipaddress.ip_network(scope, strict=False))
            except ValueError:
                raise CheckError('Invalid network scope') from None
        if scope not in result:
            result.append(scope)
    return result


class Policy:
    """Which addresses a check may reach. ``blocked_ports``: ports refused on loopback (the admin listener)."""

    def __init__(self, scopes=('private',), blocked_ports=()):
        scopes = validate_scopes(list(scopes))
        self.words = {scope for scope in scopes if scope in SCOPE_WORDS}
        self.networks = [ipaddress.ip_network(scope) for scope in scopes if scope not in SCOPE_WORDS]
        self.blocked_ports = set(blocked_ports)

    def refuse(self, address, port=None):
        """None when allowed, else a short reason (never shown with the address to anyone but the administrator)."""
        ip = ipaddress.ip_address(address)
        if getattr(ip, 'ipv4_mapped', None):
            ip = ip.ipv4_mapped
        if ip in METADATA or ip.is_unspecified or ip.is_multicast or (ip.version == 4 and ip == ipaddress.ip_address('255.255.255.255')):
            return 'always-blocked'
        if ip.is_loopback and port is not None and port in self.blocked_ports:
            return 'admin-port'
        explicit = any(ip in network for network in self.networks)
        if (ip.is_loopback or ip.is_link_local) and not explicit:
            return 'loopback-or-link-local'
        if ip.is_reserved and not explicit:
            return 'reserved'
        if explicit or ('private' in self.words and any(ip in network for network in PRIVATE)) \
                or ('public' in self.words and ip.is_global):
            return None
        return 'outside-scopes'


# ---- one attempt ----

def _result(category, started, detail=''):
    return dict(ok=category == 'ok', category=category, detail=detail,
                latency_ms=round((time.monotonic() - started) * 1000, 1) if category == 'ok' else None)


async def _resolve(host, port, policy):
    """The first address for host, after checking every address it resolves to. (address, None) or (None, category)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return None, 'dns'
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        return None, 'dns'
    if any(policy.refuse(address.split('%')[0], port) for address in addresses):
        return None, 'blocked'
    return addresses[0], None


async def _tcp(check, policy, started):
    address, problem = await _resolve(check['host'], check['port'], policy)
    if problem:
        return _result(problem, started)
    try:
        _, writer = await asyncio.open_connection(address, check['port'])
    except ConnectionRefusedError:
        return _result('refused', started)
    except OSError:
        return _result('unreachable', started)
    writer.close()
    return _result('ok', started)


def ping_command(address, timeout):
    """The fixed argument list for one echo request (no shell; ``address`` is an already checked IP)."""
    ipaddress.ip_address(address)
    if sys.platform == 'win32':
        return ['ping', '-n', '1', '-w', str(timeout * 1000), address]
    return ['ping', '-c', '1', '-W', str(timeout), address]


async def _ping(check, policy, started):
    address, problem = await _resolve(check['host'], None, policy)
    if problem:
        return _result(problem, started)
    command = ping_command(address, check['timeout'])
    executable = shutil.which(command[0])
    if not executable:
        return _result('error', started, 'no-ping')
    process = await asyncio.create_subprocess_exec(executable, *command[1:], stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await process.communicate()
    finally:
        if process.returncode is None:      # cancelled at the deadline: never leave a ping behind
            process.kill()
            await process.wait()
    replied = process.returncode == 0 and (sys.platform != 'win32' or b'TTL=' in output[:4096].upper())
    return _result('ok' if replied else 'unreachable', started)


def _tls_context(verify):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if not verify:              # the administrator's explicit choice, e.g. a printer's self-signed certificate
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


async def _http(check, policy, started):
    url = check['url']
    for hop in range(check['redirects'] + 1):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None \
                or parts.password is not None:
            return _result('blocked', started, 'redirect')
        secure = parts.scheme == 'https'
        port = parts.port or (443 if secure else 80)
        address, problem = await _resolve(parts.hostname, port, policy)
        if problem:
            return _result(problem, started)
        try:
            reader, writer = await asyncio.open_connection(
                address, port, ssl=_tls_context(check['verify_tls']) if secure else None,
                server_hostname=parts.hostname if secure else None)
        except ssl.SSLError:
            return _result('tls', started)
        except ConnectionRefusedError:
            return _result('refused', started)
        except OSError:
            return _result('unreachable', started)
        try:
            path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            authority = parts.hostname if ':' not in parts.hostname else f'[{parts.hostname}]'
            authority += f':{parts.port}' if parts.port else ''
            writer.write(f"{check['method']} {path} HTTP/1.1\r\nHost: {authority}\r\nUser-Agent: Lifetime-Check/1\r\n"
                         f"Accept: */*\r\nConnection: close\r\n\r\n".encode('ascii', 'strict'))
            await writer.drain()
            head = await reader.readuntil(b'\r\n\r\n')          # bounded by the reader's 64 KiB limit
            lines = head.decode('iso-8859-1').split('\r\n')
            match = re.fullmatch(r'HTTP/1\.[01] ([1-5][0-9]{2})(?: .*)?', lines[0])
            if not match:
                return _result('error', started, 'response')
            status = int(match.group(1))
            headers = {}
            for line in lines[1:]:
                name, _, value = line.partition(':')
                headers.setdefault(name.strip().lower(), value.strip())
            if 300 <= status < 400 and headers.get('location') and hop < check['redirects']:
                url = urljoin(url, headers['location'])
                continue            # the next hop is resolved and checked again
            body = b''
            if check['method'] == 'GET':
                while len(body) <= check['max_bytes']:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    body += chunk
                if len(body) > check['max_bytes']:
                    return _result('oversize', started)
            if not check['status_min'] <= status <= check['status_max']:
                return _result('status', started, str(status))
            if check['keyword'] and check['keyword'].encode('utf-8') not in body:
                return _result('keyword', started, str(status))
            result = _result('ok', started, str(status))
            return result
        except ssl.SSLError:
            return _result('tls', started)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError, ValueError):
            return _result('error', started, 'response')
        except ConnectionError:
            return _result('unreachable', started)
        finally:
            writer.close()
    return _result('error', started, 'redirects')


async def attempt(check, policy):
    """One check within its deadline; always returns a result, never raises."""
    started = time.monotonic()
    run = dict(ping=_ping, tcp=_tcp, http=_http)[check['kind']]
    try:
        return await asyncio.wait_for(run(check, policy, started), check['timeout'])
    except (asyncio.TimeoutError, TimeoutError):
        return _result('timeout', started)
    except Exception:       # a check never stops the worker
        return _result('error', started)


# ---- the worker ----

class Checker:
    """Runs the configured checks forever in one thread; ``results`` is drained by the Monitor."""

    def __init__(self, policy=None, concurrency=CONCURRENCY, queue_size=QUEUE_SIZE, spacing=SPACING):
        self.policy = policy or Policy()
        self.concurrency, self.spacing = concurrency, spacing
        self.results = queue.Queue(maxsize=queue_size)
        self.peak = self.active = 0
        self._lock = threading.Lock()
        self._checks, self._due, self._last = {}, {}, {}
        self._running = set()
        self._stop = threading.Event()
        self._thread = None

    def configure(self, checks, policy=None):
        """Replace the checks ({id: validated check}) and optionally the policy; new checks start soon, with jitter."""
        now = time.monotonic()
        with self._lock:
            if policy is not None:
                self.policy = policy
            for identifier, check in checks.items():
                if self._checks.get(identifier) != check:
                    self._due[identifier] = now + random.uniform(0, min(check['interval'], 5) / 5)
            self._checks = {key: value for key, value in checks.items() if value.get('enabled', True)}
            self._due = {key: value for key, value in self._due.items() if key in self._checks}

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), name='Lifetime-checks', daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=40)
            self._thread = None

    async def _main(self):
        tasks = set()
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                due = sorted((when, key) for key, when in self._due.items() if when <= now and key not in self._running)
                policy = self.policy
                for _, key in due:
                    if self.active >= self.concurrency or self.results.full():
                        break       # backpressure: the rest wait for a free slot or a drained queue
                    check = self._checks[key]
                    goal = target(check)
                    if now - self._last.get(goal, -1e9) < self.spacing:
                        self._due[key] = self._last[goal] + self.spacing      # per-target rate limit
                        continue
                    self._last[goal] = now
                    self._due[key] = now + check['interval'] + random.uniform(0, min(check['interval'] * 0.1, 5))
                    self._running.add(key)
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                    task = asyncio.create_task(self._run(key, check, policy))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            await asyncio.sleep(0.05)
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, key, check, policy):
        try:
            result = await attempt(check, policy)
            result.update(check_id=key, at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
            try:
                self.results.put_nowait(result)
            except queue.Full:
                pass        # cannot happen while scheduling respects the full queue; never block the loop
        finally:
            with self._lock:
                self.active -= 1
                self._running.discard(key)

    def drain(self, limit=2000):
        items = []
        while len(items) < limit:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                break
        return items
