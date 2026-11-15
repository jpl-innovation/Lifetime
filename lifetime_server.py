"""Bounded HTTPS reporting runtime and separate loopback administration API.

Only the server imports this module. Windows administrators and SYSTEM own the
data directory, private key and ``admin-token``. No request/access logs are kept.
Admin POST bodies are Store keyword arguments; successful replies are raw Store
results, including the list returned by ``/v1/devices``. Both directions are
limited to 1 MiB. Large queries must use the Store's pagination arguments.
"""
import argparse
import contextlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
from pathlib import Path
import re
import secrets
import signal
import platform
import socket
import ssl
import stat
import sys
import threading
import time
import datetime as dt
import uuid

import lifetime_access as access
import lifetime_permissions as permissions
import lifetime_server_backup as archive
from lifetime_protocol import CAPABILITIES, MAX_BYTES, ProtocolError, canonical, decode
from lifetime_server_store import (AuthenticationError, ConflictError, Store,
                                   StoreError, ThrottledError)
import lifetime_tls as tls

MAX_CONNECTIONS = 32
HANDSHAKE_TIMEOUT = 5
HEADER_TIMEOUT = 10
BODY_TIMEOUT = 10
CERTIFICATE_CHECK = 3600    # seconds between renewal checks while the server runs
MONITOR_TICK = 15           # seconds between alert evaluations
DATABASES = ('server.sqlite', 'server.sqlite-wal', 'server.sqlite-shm',
             'monitoring.sqlite', 'monitoring.sqlite-wal', 'monitoring.sqlite-shm',
             'access.sqlite', 'access.sqlite-wal', 'access.sqlite-shm')
POWER_WINDOW = 600          # seconds over which remote power requests are counted
POWER_PER_DEVICE = 3        # power commands (and Turn on) per computer in that window
POWER_PER_ACTOR = 10        # per named administrator or operator in that window


def monitoring_backup(path):
    """Monitoring history travels beside a dashboard backup: lifetime-backup.sqlite -> lifetime-backup.monitoring.sqlite."""
    path = Path(path)
    return path.with_name(path.stem + '.monitoring' + (path.suffix or '.sqlite'))


def _regular(path, directory=False):
    info = path.lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not directory and info.st_nlink != 1)):
        raise ValueError('Unsafe runtime path')


def _protect_directory(path):
    """Fail closed before creating SQLite/WAL or any secret material."""
    return permissions.protect_directory(path)


def _admin_token(path):
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                tls.protect_private(path)
                output.write(secrets.token_urlsafe(32).encode('ascii'))
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink()
            raise
    _regular(path)
    tls.protect_private(path)
    with path.open('rb') as source:
        raw = source.read(257)
    if not re.fullmatch(rb'[A-Za-z0-9_-]{43}', raw):
        raise ValueError('Invalid administrator token file')
    return raw.decode('ascii')


def _certificate(directory, address):
    cert, key = directory / 'server-cert.pem', directory / 'server-key.pem'
    if cert.exists() != key.exists():
        raise ValueError('Incomplete TLS identity; refusing to replace it')
    if not cert.exists():
        tls.generate_certificate(directory, address)
    _regular(cert)
    _regular(key)
    tls.protect_private(key)
    # A loopback TLS probe below checks validity, SAN, strict extensions, the
    # key match and the exact fingerprint using the normal pinned client.
    with cert.open('rb') as source:
        pem = source.read(tls.MAX_CERTIFICATE_BYTES + 1).decode('ascii')
    return _context(cert, key, address, pem)


def _context(cert, key, address, pem):
    bundle = tls.decode_bundle(tls.encode_bundle(address, 'runtime-validation', pem))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context, bundle


def _write_new(path, data):
    """Replace a public runtime file by exclusive creation (never through a link)."""
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _shutdown(connection):
    with contextlib.suppress(OSError):
        connection.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        connection.close()


@contextlib.contextmanager
def _deadline(connection, seconds):
    """Absolute deadline as well as socket idle timeout, including trickle I/O."""
    connection.settimeout(seconds)
    timer = threading.Timer(seconds, _shutdown, args=(connection,))
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()


class _HTTPError(Exception):
    def __init__(self, status):
        self.status = status


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = False
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address, runtime, admin=False, context=None):
        self.runtime, self.admin, self.context = runtime, admin, context
        self._connections = set()
        self._detached = set()      # long-lived sockets that have handed their request slot back
        self._connections_lock = threading.Lock()
        self._stopping = False
        if ':' in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, _Handler)

    def verify_request(self, request, client_address):
        return not self.admin or ipaddress.ip_address(client_address[0]).is_loopback

    def process_request(self, request, client_address):
        # Acquire before starting a worker or attempting a TLS handshake.
        if not self.runtime._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._connections_lock:
                self._connections.discard(request)
            self.runtime._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        connection = request
        try:
            with self._connections_lock:
                if self._stopping:
                    return
                if self.context:
                    connection = self.context.wrap_socket(request, server_side=True,
                                                          do_handshake_on_connect=False)
                    self._connections.discard(request)
                    self._connections.add(connection)
            if self.context:
                with _deadline(connection, HANDSHAKE_TIMEOUT):
                    connection.do_handshake()
            self.finish_request(connection, client_address)
        except Exception:
            # Never echo paths, tokens, request bodies or network identifiers.
            pass
        finally:
            self.shutdown_request(connection)
            with self._connections_lock:
                self._connections.discard(connection)
                detached = connection in self._detached
                self._detached.discard(connection)
            if not detached:
                self.runtime._slots.release()

    def detach(self, connection):
        """A live socket stays open for hours: give its short-request slot back right away."""
        with self._connections_lock:
            if connection in self._detached:
                return
            self._detached.add(connection)
        self.runtime._slots.release()

    def close_connections(self):
        with self._connections_lock:
            self._stopping = True
            for connection in self._connections:
                _shutdown(connection)

    def handle_error(self, request, client_address):
        pass


_ADMIN = {
    'live': ('live', (), ('device_id',)),
    'send_command': ('send_command', ('device_id', 'action'), ()),
    'cancel_command': ('cancel_command', ('command_id',), ()),
    'wake': ('wake', ('device_id',), ()),
    'overview': ('get_overview', (), ()),
    'devices': ('list_devices', (), ('limit', 'offset', 'search', 'group', 'status', 'sort', 'descending')),
    'device': ('get_device', ('device_id',), ('limit', 'after_seq', 'hours')),
    'code': ('create_code', (), ('ttl', 'uses')),
    'approve': ('approve', ('request_id',), ()),
    'deny': ('deny', ('request_id',), ()),
    'revoke': ('revoke', ('device_id',), ()),
    'delete': ('delete_device', ('device_id',), ()),
    'identity': ('update_identity', ('device_id', 'identity'), ()),
    'retain': ('retain', ('before',), ()),
    'backup': ('backup', ('path',), ()),
    'restore': ('restore', ('path',), ()),
    'configuration': ('get_configuration', (), ()),
    'set_configuration': ('update_configuration', ('reporting_interval',), ()),
    'refresh': ('request_refresh', ('device_id',), ()),
    'invitations': ('invitations', (), ('limit', 'offset')),
    'revoke_invitation': ('revoke_invitation', ('invitation_id',), ()),
    'invitation': ('create_invitation', (), ('ttl', 'uses')),
    'fleet': ('fleet', (), ('day', 'days', 'limit', 'offset', 'search', 'group', 'status', 'sort', 'descending')),
    'server_info': (None, (), ()),
    'pending_enrollments': ('pending_enrollments', (), ('limit', 'offset')),
    'organisation': ('organisation', (), ('name',)),
    'names': (None, ('names',), ()),
    'server_log': ('server_log', (), ('limit', 'offset')),
    # Monitoring (alerts): methods of the runtime's Monitor, the only writer of monitoring.sqlite.
    'presence': ('monitor.presence', ('device_id',), ('limit',)),
    'alert_rules': ('monitor.rules', (), ()),
    'set_alert_rule': ('monitor.set_rule', ('rule',), ('enabled', 'threshold', 'minutes', 'version')),
    'incidents': ('monitor.incidents', (), ('state', 'device_id', 'limit', 'offset')),
    'schedules': ('monitor.schedules', (), ()),
    'set_schedule': ('monitor.set_schedule', ('scope', 'kind'), ('key', 'timezone', 'start', 'end', 'weekdays', 'holidays')),
    'delete_schedule': ('monitor.delete_schedule', ('scope',), ('key',)),
    'maintenance': ('monitor.maintenance', (), ('include_past',)),
    'add_maintenance': ('monitor.add_maintenance', ('scope', 'start', 'end'), ('key', 'note')),
    'delete_maintenance': ('monitor.delete_maintenance', ('window_id',), ()),
    'incident': ('monitor.incident', ('incident_id',), ()),
    'acknowledge': ('monitor.acknowledge', ('incident_id', 'by'), ()),
    'alert_summary': ('monitor.summary', (), ()),
    'notifications': ('monitor.notifications', (), ()),
    'set_notifications': ('monitor.set_notifications', (), (
        'email_enabled', 'smtp_host', 'smtp_port', 'security', 'username', 'sender', 'recipients', 'webhook_enabled',
        'summary_enabled', 'summary_hour', 'language', 'password', 'webhook_url')),
    'test_notification': ('monitor.test_notification', ('channel',), ()),
    'deliveries': ('monitor.deliveries', (), ('limit', 'offset')),
    'time_zones': ('monitor.time_zones', (), ()),
    'availability': ('monitor.availability', ('device_ids',), ('start', 'end', 'as_of', 'daily')),
    'set_coverage_floor': ('monitor.set_coverage_floor', ('percent',), ()),
    'coverage_floor': ('monitor.coverage_floor', (), ()),
    'checks': ('monitor.checks', (), ()),
    'save_check': ('monitor.save_check', ('check',), ('check_id',)),
    'delete_check': ('monitor.delete_check', ('check_id',), ()),
    'check_scopes': ('monitor.check_scopes', (), ()),
    'set_check_scopes': ('monitor.set_check_scopes', ('scopes',), ()),
    # Access: who is calling, roles, the audit trail and archive backups (methods of the runtime).
    'whoami': (None, (), ()),
    'roles': (None, (), ()),
    'set_role': (None, ('principal', 'role'), ()),
    'delete_role': (None, ('sid',), ()),
    'audit': (None, (), ('limit', 'before')),
    'verify_audit': (None, (), ()),
    'backup_archive': (None, ('path',), ()),
    'verify_backup': (None, ('path',), ()),
    'stage_restore': (None, ('path',), ()),
}


def _status(error):
    """The HTTP status for an error, on either admin channel (never its text)."""
    if isinstance(error, _HTTPError):
        return error.status
    for kind, status in ((ThrottledError, 429), (AuthenticationError, 403), (ConflictError, 409),
                         ((ProtocolError, StoreError, TypeError, ValueError, RecursionError), 400),
                         ((TimeoutError, ConnectionError), 408)):
        if isinstance(error, kind):
            return status
    return 500


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or set(value) - set(required) - set(optional)
            or set(required) - set(value)):
        raise ProtocolError('Invalid fields')


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'Lifetime'
    sys_version = ''

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        # Covers the request line and all headers, even byte-at-a-time senders.
        try:
            with _deadline(self.connection, HEADER_TIMEOUT):
                self.raw_requestline = self.rfile.readline(65537)
                if len(self.raw_requestline) > 65536:
                    self.requestline = self.request_version = self.command = ''
                    self.send_error(414)
                    return
                if not self.raw_requestline:
                    return
                if not self.parse_request():
                    return
            self.close_connection = True
            self._dispatch()
        except (OSError, ValueError):
            self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        self._reply(code, {'error': 'Request failed'})

    def _reply(self, status, value):
        # The stdlib parser falls back to HTTP/0.9 on malformed request lines;
        # always send a framed HTTP error instead of a bare JSON body.
        if self.request_version == 'HTTP/0.9':
            self.request_version = 'HTTP/1.0'
        raw = canonical(value)
        if len(raw) > MAX_BYTES:
            status, raw = 413, canonical({'error': 'Response too large; use pagination'})
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if getattr(self, 'command', '') != 'HEAD':
            with _deadline(self.connection, BODY_TIMEOUT):
                self.wfile.write(raw)

    def _bearer(self):
        headers = self.headers.get_all('Authorization', [])
        if len(headers) != 1 or not re.fullmatch(r'Bearer [\x21-\x7e]{1,256}', headers[0]):
            raise _HTTPError(401)
        return headers[0][7:]

    def _body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1:
            raise _HTTPError(400)
        if not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
            raise _HTTPError(400)
        length = int(lengths[0])
        if length > (4096 if self.path == '/v1/live' else MAX_BYTES):
            raise _HTTPError(413)
        if (self.headers.get_content_type() != 'application/json'
                or len(self.headers.get_all('Content-Type', [])) != 1
                or self.headers.get('Content-Encoding', 'identity').lower() != 'identity'
                or self.headers.get_all('Expect')):
            raise _HTTPError(400)
        with _deadline(self.connection, BODY_TIMEOUT):
            raw = self.rfile.read(length)
        if len(raw) != length:
            raise _HTTPError(400)
        value = decode(raw)
        if not isinstance(value, dict) or len(canonical(value)) > MAX_BYTES:
            raise ProtocolError('Invalid JSON object')
        return value

    def _refused(self, category, credential, value):
        """Note a refused computer request in the server log: a category only, never the payload or a secret."""
        if self.server.admin or self.path not in ('/v1/enroll', '/v1/claim', '/v1/sync', '/v1/live', '/v1/leave'):
            return
        if category == 'credential' and self.path in ('/v1/enroll', '/v1/claim'):
            category = 'code'
        device = value.get('device_id') if isinstance(value, dict) else None
        with contextlib.suppress(Exception):
            self.server.runtime._store.note_refused(self.path, category, device, credential)

    def _dispatch(self):
        credential = value = None
        try:
            runtime = self.server.runtime
            if self.server.admin:
                port = self.server.server_port
                loopback = ('127.0.0.1', 'localhost', '[::1]')
                if self.headers.get('Host') not in (*loopback, *(f'{name}:{port}' for name in loopback)):
                    raise _HTTPError(403)       # a rebound DNS name would show up here
                if not hmac.compare_digest(self._bearer(), runtime._token):
                    raise _HTTPError(401)
                if self.path == '/v1/stream':
                    if self.command != 'GET' or runtime.hub is None:
                        raise _HTTPError(404)
                    runtime.hub.upgrade_admin(self)
                    return
                operation = self.path.removeprefix('/v1/')
                if self.path != '/v1/' + operation or operation not in _ADMIN:
                    raise _HTTPError(404)
            elif self.path.split('?', 1)[0] == '/v1/ws':
                if self.command != 'GET' or runtime.hub is None:
                    raise _HTTPError(404)       # an older server or a disabled channel: the computer falls back to HTTPS
                runtime.hub.upgrade(self)
                return
            elif self.path not in ('/v1/enroll', '/v1/claim', '/v1/sync', '/v1/live', '/v1/leave'):
                raise _HTTPError(404)
            if self.command != 'POST':
                raise _HTTPError(405)
            credential = self._bearer() if not self.server.admin and self.path in ('/v1/sync', '/v1/live', '/v1/leave') else None
            value = self._body()
            if self.server.admin:
                result = runtime.admin_call(operation, value, access.TOKEN_ACTOR)
            elif self.path == '/v1/enroll':
                result = runtime._store.enroll(value, peer_ip=self.client_address[0])
            elif self.path == '/v1/claim':
                _fields(value, ('request_id', 'poll_token'))
                result = runtime._store.claim(**value)
            else:
                if 'device_id' not in value:
                    raise ProtocolError('Missing device ID')
                if self.path == '/v1/leave':
                    _fields(value, ('device_id',))
                    result = runtime._store.leave(value['device_id'], credential)
                else:
                    method = runtime._store.ingest_live if self.path == '/v1/live' else runtime._store.ingest
                    result = method(value['device_id'], credential, value)
            if (self.path in ('/v1/enroll', '/v1/claim', '/v1/sync') and not self.server.admin
                    and self.headers.get_all('Lifetime-Accept') == ['capabilities']):
                result = dict(result, capabilities=list(CAPABILITIES))     # only a computer that asked gets the new field
            self._reply(200, result)
        except _HTTPError as error:
            if error.status in (400, 401, 413):
                self._refused('credential' if error.status == 401 else 'invalid', credential, value)
            self.send_error(error.status)
        except ThrottledError:
            self._refused('throttled', credential, value)
            self.send_error(429)
        except AuthenticationError:
            self._refused('credential', credential, value)
            self.send_error(403)
        except ConflictError:
            self._refused('conflict', credential, value)
            self.send_error(409)
        except (ProtocolError, StoreError, TypeError, ValueError, RecursionError):
            self._refused('invalid', credential, value)
            self.send_error(400)
        except (TimeoutError, ConnectionError):
            self.send_error(408)
        except Exception:
            self.send_error(500)


class ServerRuntime:
    """start/stop are idempotent; stop closes listeners, workers and SQLite.

    ``address`` is the advertised HTTPS origin/certificate hostname, independent
    of the bind host. A port of zero selects an available port for local tests.
    ``public_address`` reflects the actual port; ``admin_address`` is always
    ``http://127.0.0.1:<port>``. Properties are available after start only.
    """
    def __init__(self, directory, address='https://localhost:8443', host='0.0.0.0',
                 port=8443, admin_port=0, clock=None):
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))     # injectable for renewal tests
        self.certificate = {}
        self._renew_lock = threading.Lock()
        self._maintenance = None
        self._maintenance_stop = threading.Event()
        self._watcher = None
        self.monitor = None
        self.checker = None
        self.access = None
        self._pipe = None
        self._access_lock = threading.Lock()     # role changes and power commands: a revocation is never overtaken
        self._power_log = []                     # (monotonic time, device, actor SID) of recent power requests
        self.directory = Path(directory).absolute()
        self.address, self._hostname, _ = tls._address(address)
        for number in (port, admin_port):
            if type(number) is not int or not 0 <= number <= 65535:
                raise ValueError('Invalid listener port')
        self.host, self.port, self.admin_port = host, port, admin_port
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._servers, self._threads = [], []
        self._store = None
        self.hub = None
        self._token = None
        self._public_address = self._admin_address = None
        self._discovery = None
        self.fingerprint = None
        self._resource_state = {}
        self.started_at = None
        self._started_clock = None

    @property
    def public_address(self):
        if self._public_address is None:
            raise RuntimeError('Runtime is stopped')
        return self._public_address

    @property
    def admin_address(self):
        if self._admin_address is None:
            raise RuntimeError('Runtime is stopped')
        return self._admin_address

    def _check_backup_path(self, value):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ProtocolError('Invalid backup path')
        protected = [self.directory / name for name in
                     ('admin-token', 'server-key.pem', 'server-cert.pem', 'server-cert.new.pem',
                      'server-cert.previous.pem', 'alert-secrets.bin', *DATABASES, *archive.RESERVED)]
        for path in (Path(value).resolve(), monitoring_backup(Path(value).resolve())):
            if any(path == item.resolve() or
                   (path.exists() and item.exists() and os.path.samefile(path, item))
                   for item in protected):
                raise ProtocolError('Reserved runtime path')

    def backup(self, path):
        """The server database, and monitoring history and alert settings beside it (never alert-secrets.bin)."""
        result = self._store.backup(path)
        return dict(result, monitoring=self.monitor.backup(monitoring_backup(result['path']))['path'])

    def restore(self, path):
        """Restore the server database and, when the backup has its monitoring file beside it, monitoring too.
        Both are checked before anything changes; without that file the current monitoring data stays."""
        companion = monitoring_backup(Path(path).resolve())
        snapshot = self.monitor.read_backup(companion) if companion.is_file() else None
        try:
            result = self._store.restore(path)
            if snapshot is not None:
                self.monitor.restore(snapshot)
        finally:
            if snapshot is not None:
                snapshot.close()
        self._renew_certificate()       # the restored names may differ
        return dict(result, monitoring='restored' if snapshot is not None else 'not-included')

    # ---- one path for every admin request, whichever channel it came on ----

    def admin_call(self, operation, value, identity):
        """Run one admin operation for a caller its channel has already identified (a Windows identity from the pipe,
        or ``access.TOKEN_ACTOR`` for the token). Fields are checked, the role must allow the operation (default
        deny), and every state-changing or refused request is written to the audit trail with its result."""
        if not isinstance(operation, str) or operation not in _ADMIN:
            raise _HTTPError(404)
        method, required, optional = _ADMIN[operation]
        roles = self.access.roles_for(identity)
        target, correlation = access.target_of(value), str(uuid.uuid4())
        if operation not in access.operations(roles):       # before any validation: a refused caller learns nothing
            self.access.record(identity, roles, operation, target, 'denied', correlation)
            raise _HTTPError(403)
        _fields(value, required, optional)
        if not access.state_changing(operation):
            return self._execute(operation, method, value, identity)
        if operation == 'acknowledge' and identity.get('sid'):
            value = dict(value, by=identity['name'][:64])        # the Windows account, never a name the client typed
        power = operation == 'wake' or (operation == 'send_command' and value.get('action') != 'refresh')
        try:
            with self._access_lock if power or operation in ('set_role', 'delete_role') else contextlib.nullcontext():
                if power:
                    roles = self._power_gate(operation, value, identity)
                result = self._execute(operation, method, value, identity)
        except Exception as error:
            self.access.record(identity, roles, operation, target, 'failed-%d' % _status(error), correlation)
            raise
        if operation == 'send_command' and isinstance(result, dict) and result.get('command_id'):
            correlation = result['command_id']
        try:
            self.access.record(identity, roles, operation, target, 'ok', correlation)
        except Exception:
            if power and operation == 'send_command':       # an unrecorded remote command does not go out
                with contextlib.suppress(Exception):
                    self._store.cancel_command(correlation)
            raise
        return result

    def _power_gate(self, operation, value, identity):
        """Remote power needs a named Windows account whose role still allows it now (checked under the same lock as
        role changes), and stays within the per-computer and per-account rate limits."""
        if not identity.get('sid'):
            raise _HTTPError(403)       # the shared token cannot say who is asking
        roles = self.access.roles_for(identity)
        if operation not in access.operations(roles):
            raise _HTTPError(403)
        now = time.monotonic()
        self._power_log = [entry for entry in self._power_log if now - entry[0] < POWER_WINDOW]
        device = value.get('device_id')
        if (sum(entry[1] == device for entry in self._power_log) >= POWER_PER_DEVICE
                or sum(entry[2] == identity['sid'] for entry in self._power_log) >= POWER_PER_ACTOR):
            raise ThrottledError('Too many remote power requests')
        self._power_log.append((now, device, identity['sid']))
        return roles

    def _execute(self, operation, method, value, identity):
        if operation in ('backup', 'restore', 'backup_archive', 'verify_backup', 'stage_restore'):
            self._check_backup_path(value['path'])
        if operation == 'server_info':
            return self.server_info()
        if operation in ('backup', 'restore'):
            return getattr(self, operation)(value['path'])
        if operation == 'names':
            return self.set_names(**value)
        if operation == 'invitation':
            return self._store.create_invitation(self.fingerprint, **value)
        if operation == 'whoami':
            return self.whoami(identity)
        if operation == 'roles':
            return self.access.roles()
        if operation == 'set_role':
            return self.set_role(identity, **value)
        if operation == 'delete_role':
            return self.delete_role(**value)
        if operation == 'audit':
            return self.access.audit(**value)
        if operation == 'verify_audit':
            return self.access.verify()
        if operation in ('backup_archive', 'verify_backup', 'stage_restore'):
            if operation == 'backup_archive':
                return archive.create(self, value['path'])
            if operation == 'verify_backup':
                return archive.verify(value['path'], self.directory)
            return archive.stage(self.directory, value['path'])
        if method.startswith('monitor.'):
            return getattr(self.monitor, method[8:])(**value)
        result = getattr(self._store, method)(**value)
        if operation == 'delete':
            self.monitor.forget(value['device_id'])
        elif operation == 'overview':      # alert badges ride along with the counters
            result = dict(result, alerts=self.monitor.summary())
        elif operation == 'device':
            result = dict(result, alerts=self.monitor.device_alerts(value['device_id']))
        return result

    def whoami(self, identity):
        roles = self.access.roles_for(identity)
        return dict(name=identity.get('name'), sid=identity.get('sid'), channel=identity.get('channel', 'pipe'),
                    roles=sorted(roles), role=access.role_label(roles), builtin=access.builtin_admin(identity),
                    operations=sorted(access.operations(roles) & set(_ADMIN)))

    def set_role(self, identity, principal, role):
        """Give a Windows account or group a role (resolved to its SID here, never trusted from the dashboard).
        Lowering or removing a role that allowed remote power cancels every power command still waiting."""
        if sys.platform != 'win32':
            raise ProtocolError('Roles are for Windows accounts')
        import lifetime_pipe
        sid, name, kind = lifetime_pipe.lookup_account(principal)
        previous = self.access.set_role(sid, name, kind, role, identity.get('name') or 'unknown')
        if previous in ('admin', 'operator') and role not in ('admin', 'operator'):
            self._store.cancel_power_commands()
        return self.access.roles()

    def delete_role(self, sid):
        if self.access.delete_role(sid) in ('admin', 'operator'):
            self._store.cancel_power_commands()
        return self.access.roles()

    def _pipe_request(self, identity, raw):
        """A request on the Windows pipe: {operation, arguments} -> {status, result}. The identity is the one Windows
        reported for the pipe's client; nothing in the request can change it."""
        identity = dict(identity, channel='pipe')
        try:
            request = decode(raw)
            if (not isinstance(request, dict) or set(request) != {'operation', 'arguments'}
                    or not isinstance(request['operation'], str)):
                raise ProtocolError('Invalid admin request')
            reply = canonical(dict(status=200, result=self.admin_call(request['operation'], request['arguments'], identity)))
        except Exception as error:
            reply = canonical(dict(status=_status(error)))
        if len(reply) > MAX_BYTES:
            reply = canonical(dict(status=413))
        return reply

    def start(self):
        with self._lock:
            if self._servers:
                return self
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                _protect_directory(self.directory)
                self._token = _admin_token(self.directory / 'admin-token')
                staged = archive.prepare_staged(self.directory)     # a restore waiting for this start: key first
                context, bundle = _certificate(self.directory, self.address)
                for name in DATABASES:
                    path = self.directory / name
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        _regular(path)
                        tls.protect_private(path)
                self.access = access.Access(self.directory / 'access.sqlite')
                self._store = Store(self.directory / 'server.sqlite')
                from lifetime_monitor import Monitor
                self.monitor = Monitor(self.directory / 'monitoring.sqlite', self._store, clock=self.clock,
                                       server_name=socket.gethostname()[:63]).start()
                if staged == 'failed':
                    self._store.log_event('restore-failed')
                elif staged is not None:
                    archive.finish_staged(self, staged)        # before any listener opens
                if self._renew_certificate():       # due, or the names changed: same key, new certificate
                    context, bundle = _certificate(self.directory, self.address)
                self.fingerprint = bundle['fingerprint']
                try:
                    from lifetime_ws_server import LiveHub
                    self.hub = LiveHub(self._store)
                except ImportError:     # without the websockets package computers simply use HTTPS
                    self.hub = None
                self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()
                self._started_clock = time.monotonic()
                public = _Server((self.host, self.port), self, context=context)
                self._servers.append(public)
                admin = _Server(('127.0.0.1', self.admin_port), self, admin=True)
                self._servers.append(admin)
                authority = f'[{self._hostname}]' if ':' in self._hostname else self._hostname
                self._public_address = f'https://{authority}:{public.server_port}'
                self._admin_address = f'http://127.0.0.1:{admin.server_port}'
                from lifetime_checks import Checker
                self.monitor.blocked_ports = {admin.server_port}     # checks never reach the admin listener
                self.checker = self.monitor.attach_checker(Checker()).start()
                self._validate_certificate(context, bundle)
                if sys.platform == 'win32':
                    self._start_pipe(admin.server_port)
                for server in self._servers:
                    thread = threading.Thread(target=server.serve_forever,
                                              kwargs={'poll_interval': 0.05},
                                              name='Lifetime-listener')
                    thread.start()
                    self._threads.append(thread)
                self._maintenance_stop.clear()
                self._maintenance = threading.Thread(target=self._maintain, name='Lifetime-certificate', daemon=True)
                self._maintenance.start()
                self._watcher = threading.Thread(target=self._watch, name='Lifetime-alerts', daemon=True)
                self._watcher.start()
                self._store.log_event('server-started')
                # Loopback tests never multicast. LAN/VPN installations announce
                # only public service metadata; failure leaves manual pairing.
                try:
                    loopback = ipaddress.ip_address(self.host).is_loopback
                except ValueError:
                    loopback = self.host.lower() == 'localhost'
                if not loopback:
                    from lifetime_discovery import Responder
                    responder = Responder('Lifetime-' + socket.gethostname()[:40], self._hostname,
                                          public.server_port, self.fingerprint)
                    if responder.start():
                        self._discovery = responder
                return self
            except BaseException:
                self.stop()
                raise

    def _start_pipe(self, port):
        """The identity-bound admin channel. If it cannot start (for example another program took its name first),
        the server still runs with the token channel only, and the server log says so."""
        import lifetime_pipe
        try:
            own = lifetime_pipe.current_identity()['sid']
            self._pipe = lifetime_pipe.PipeServer(lifetime_pipe.pipe_name(port), self._pipe_request,
                                                  lambda: self.access.pipe_descriptor(own)).start()
        except OSError:
            self._pipe = None
            self._store.log_event('admin-pipe-unavailable')

    def _refresh_host_readings(self):
        """Hardware probes take seconds, so they run aside; requests return the last result."""
        with self._lock:
            if getattr(self, '_host_busy', False) or time.monotonic() - getattr(self, '_host_at', -60) < 30:
                return
            self._host_busy = True
        def collect():
            from lifetime_agent import _resources
            try:
                readings = _resources(self._resource_state)
            except Exception:
                readings = None
            with self._lock:
                if readings is not None:
                    self._host_readings = readings
                self._host_at, self._host_busy = time.monotonic(), False
        threading.Thread(target=collect, daemon=True, name='lifetime-host-readings').start()

    def server_info(self):
        from lifetime_protocol import CLIENT_VERSION
        self._refresh_host_readings()
        with self._lock:
            readings = getattr(self, '_host_readings', None) or {}
            overview = self._store.get_overview()
            result = dict(address=self.public_address, host=self.host,
                          port=self._servers[0].server_port, fingerprint=self.fingerprint,
                          certificate_fingerprint=self.fingerprint,
                          name=socket.gethostname(), hostname=socket.gethostname(),
                          os=readings.get('system') or platform.system(), specs=readings.get('specs'), performance=readings.get('performance'),
                          server_version=CLIENT_VERSION, uptime=max(0, time.monotonic()-self._started_clock),
                          started_at=self.started_at,
                          listening_addresses=[dict(address=s.server_address[0], port=s.server_port) for s in self._servers],
                          database_size=sum(p.stat().st_size for p in (self._store.path, Path(str(self._store.path)+'-wal')) if p.exists()),
                          device_counts=overview['counts'],
                          reporting_interval=self._store.get_configuration()['reporting_interval'],
                          discovery=self._discovery is not None,
                          certificate=dict(self.certificate), key_fingerprint=self.certificate.get('key'),
                          server_names=self._store.server_names()['names'])
            result.update(self._store.organisation())
            return result

    @staticmethod
    def _validate_certificate(context, bundle):
        # Probe a temporary loopback socket: no DNS lookup or LAN connection,
        # with hostname verification against the advertised name and strict pin.
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(HANDSHAKE_TIMEOUT)
        def accept():
            with contextlib.suppress(OSError):
                connection, _ = listener.accept()
                with connection:
                    with context.wrap_socket(connection, server_side=True,
                                             do_handshake_on_connect=False) as secured:
                        secured.settimeout(HANDSHAKE_TIMEOUT)
                        secured.do_handshake()
        thread = threading.Thread(target=accept, name='Lifetime-certificate-check')
        thread.start()
        try:
            client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client.minimum_version = ssl.TLSVersion.TLSv1_2
            client.verify_flags |= ssl.VERIFY_X509_STRICT
            client.load_verify_locations(cadata=bundle['certificate_pem'])
            with socket.create_connection(listener.getsockname(), HANDSHAKE_TIMEOUT) as raw:
                with client.wrap_socket(raw, server_hostname=tls._address(bundle['address'])[1]) as secured:
                    import hashlib
                    actual = hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest()
                    if not hmac.compare_digest(actual, bundle['fingerprint']):
                        raise ValueError('Invalid TLS identity')
        finally:
            listener.close()
            thread.join()

    def _watch(self):
        while not self._maintenance_stop.wait(MONITOR_TICK):
            with contextlib.suppress(Exception):    # a failing pass is retried at the next tick
                self.monitor.tick()
            with contextlib.suppress(Exception):
                self.monitor.deliver()

    def _maintain(self):
        while not self._maintenance_stop.wait(CERTIFICATE_CHECK):
            with contextlib.suppress(Exception):
                self._renew_certificate()

    def _renew_certificate(self):
        """Issue a new certificate from the same key 30 days before expiry, or when the names changed.

        The new certificate passes the same loopback check as at start (strict TLS, its name) before it
        replaces the old one, which is kept as server-cert.previous.pem. If anything fails, the current
        certificate stays in use, the dashboard shows a warning, and the next check tries again.
        Returns True when a new certificate is in use.
        """
        with self._renew_lock:
            path = self.directory / 'server-cert.pem'
            with path.open('rb') as source:
                old = source.read(tls.MAX_CERTIFICATE_BYTES + 1)
            info = tls.certificate_info(old.decode('ascii'))
            names = tls.server_names([self._hostname, *self._store.server_names()['names']])
            now = self.clock()
            self.certificate.update(expires=info['expires'].isoformat(), names=info['names'], key=info['key'])
            if info['expires'] - now > tls.RENEW_BEFORE and set(info['names']) == set(names):
                return False
            staged = self.directory / 'server-cert.new.pem'
            try:
                pem = tls.renew_certificate(self.directory, names, now)
                _write_new(staged, pem.encode('ascii'))
                context, bundle = _context(staged, self.directory / 'server-key.pem', self.address, pem)
                self._validate_certificate(context, bundle)
                _write_new(self.directory / 'server-cert.previous.pem', old)
                os.replace(staged, path)
            except Exception:
                with contextlib.suppress(OSError):
                    staged.unlink()
                self.certificate['warning'] = 'renewal-failed'
                with contextlib.suppress(Exception):
                    self._store.log_event('certificate-renewal-failed')
                return False
            with contextlib.suppress(Exception):
                self._store.log_event('certificate-renewed', 'names' if set(info['names']) != set(names) else 'expiry')
            renewed = tls.certificate_info(pem)
            self.certificate.update(expires=renewed['expires'].isoformat(), names=renewed['names'],
                                    warning=None, renewed_at=now.isoformat())
            if self._servers:
                self._servers[0].context = context      # new handshakes only; open connections finish as they are
                self.fingerprint = bundle['fingerprint']
            if self._discovery:
                from lifetime_discovery import announcement
                self._discovery.packet = announcement(self._discovery.instance, self._hostname,
                                                      self._servers[0].server_port, self.fingerprint)
            return True

    def set_names(self, names):
        """Extra names and IPs for the certificate (the address name always comes first); renews now."""
        if not isinstance(names, list) or len(names) > tls.MAX_NAMES:
            raise ProtocolError('Invalid server names')
        before = self._store.server_names()['names']
        after = self._store.server_names(tls.server_names([self._hostname, *names])[1:])['names']
        if after != before:
            self._store.log_event('names-changed')
        self._renew_certificate()
        return self.server_info()

    def stop(self):
        with self._lock:
            self._maintenance_stop.set()
            if self._maintenance is not None:
                self._maintenance.join()
                self._maintenance = None
            if self._watcher is not None:
                self._watcher.join()
                self._watcher = None
            if self.checker is not None:
                self.checker.stop()
                self.checker = None
            if self._pipe is not None:
                self._pipe.stop()
                self._pipe = None
            if self.hub is not None:
                self.hub.stop()
                self.hub = None
            if self._discovery:
                self._discovery.stop()
                self._discovery = None
            for server, thread in zip(self._servers, self._threads):
                server.shutdown()
                thread.join()
            for server in self._servers:
                server.close_connections()
            for server in self._servers:
                server.server_close()
            started = bool(self._servers)
            self._servers.clear()
            self._threads.clear()
            if self.monitor is not None:
                with contextlib.suppress(Exception):
                    self.monitor.close()
                self.monitor = None
            if self._store is not None:
                if started:
                    with contextlib.suppress(Exception):
                        self._store.log_event('server-stopped')
                self._store.close()
                self._store = None
            if self.access is not None:
                self.access.close()
                self.access = None
            self._token = None
            self._public_address = self._admin_address = None


def _offline(args):
    """Maintenance that needs no running server. Prints a JSON report; exit 0 when it is good."""
    import json
    try:
        if args.mode == 'verify-audit':
            report = access.verify_directory(args.directory)
        elif not args.archive:
            report = dict(ok=False, problem='archive-required')
        elif args.mode == 'verify-backup':
            report = archive.verify(args.archive, args.directory)
        else:
            report = archive.stage(args.directory, args.archive)
    except (OSError, ValueError) as error:
        report = dict(ok=False, problem=type(error).__name__)
    print(json.dumps(report, indent=1), flush=True)
    return 0 if report.get('ok') else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lifetime Server console runtime and offline maintenance')
    parser.add_argument('mode', nargs='?', choices=('console', 'restore', 'verify-backup', 'verify-audit'),
                        default='console', help='restore stages an archive for the next start; verify-backup and '
                        'verify-audit only read')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--archive', help='backup archive (.zip) for restore and verify-backup')
    parser.add_argument('--address', default='https://localhost:8443')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--admin-port', type=int, default=0)
    args = parser.parse_args(argv)
    if args.mode != 'console':
        return _offline(args)
    configuration = vars(args)
    configuration.pop('mode')
    configuration.pop('archive')
    stopped = threading.Event()
    previous = {}
    for number in (signal.SIGINT, signal.SIGTERM):
        previous[number] = signal.signal(number, lambda *_: stopped.set())
    runtime = None
    try:
        runtime = ServerRuntime(**configuration)
        runtime.start()
        print(f'Lifetime Server: {runtime.public_address}; admin: {runtime.admin_address}', flush=True)
        stopped.wait()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('Lifetime Server could not start or continue.', file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.stop()
        for number, handler in previous.items():
            signal.signal(number, handler)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
