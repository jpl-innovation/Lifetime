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
import socket
import ssl
import stat
import sys
import threading

import lifetime_permissions as permissions
from lifetime_protocol import MAX_BYTES, ProtocolError, canonical, decode
from lifetime_server_store import (AuthenticationError, ConflictError, Store,
                                   StoreError, ThrottledError)
import lifetime_tls as tls

MAX_CONNECTIONS = 32
HANDSHAKE_TIMEOUT = 5
HEADER_TIMEOUT = 10
BODY_TIMEOUT = 10


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
    bundle = tls.decode_bundle(tls.encode_bundle(address, 'runtime-validation', pem))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context, bundle


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
            self.runtime._slots.release()

    def close_connections(self):
        with self._connections_lock:
            self._stopping = True
            for connection in self._connections:
                _shutdown(connection)

    def handle_error(self, request, client_address):
        pass


_ADMIN = {
    'overview': ('get_overview', (), ()),
    'devices': ('list_devices', (), ('limit', 'offset', 'search', 'group', 'status')),
    'device': ('get_device', ('device_id',), ('limit', 'after_seq')),
    'code': ('create_code', (), ('ttl', 'uses')),
    'approve': ('approve', ('request_id',), ()),
    'deny': ('deny', ('request_id',), ()),
    'revoke': ('revoke', ('device_id',), ()),
    'identity': ('update_identity', ('device_id', 'identity'), ()),
    'retain': ('retain', ('before',), ()),
    'backup': ('backup', ('path',), ()),
    'restore': ('restore', ('path',), ()),
    'configuration': ('get_configuration', (), ()),
    'set_configuration': ('update_configuration', ('reporting_interval',), ()),
    'refresh': ('request_refresh', ('device_id',), ()),
}


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
        if length > MAX_BYTES:
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

    def _dispatch(self):
        try:
            runtime = self.server.runtime
            if self.server.admin:
                if not hmac.compare_digest(self._bearer(), runtime._token):
                    raise _HTTPError(401)
                operation = self.path.removeprefix('/v1/')
                if self.path != '/v1/' + operation or operation not in _ADMIN:
                    raise _HTTPError(404)
            elif self.path not in ('/v1/enroll', '/v1/claim', '/v1/sync'):
                raise _HTTPError(404)
            if self.command != 'POST':
                raise _HTTPError(405)
            credential = self._bearer() if not self.server.admin and self.path == '/v1/sync' else None
            value = self._body()
            if self.server.admin:
                method, required, optional = _ADMIN[operation]
                _fields(value, required, optional)
                if operation in ('backup', 'restore'):
                    runtime._check_backup_path(value['path'])
                result = getattr(runtime._store, method)(**value)
            elif self.path == '/v1/enroll':
                result = runtime._store.enroll(value)
            elif self.path == '/v1/claim':
                _fields(value, ('request_id', 'poll_token'))
                result = runtime._store.claim(**value)
            else:
                if 'device_id' not in value:
                    raise ProtocolError('Missing device ID')
                result = runtime._store.ingest(value['device_id'], credential, value)
            self._reply(200, result)
        except _HTTPError as error:
            self.send_error(error.status)
        except ThrottledError:
            self.send_error(429)
        except AuthenticationError:
            self.send_error(403)
        except ConflictError:
            self.send_error(409)
        except (ProtocolError, StoreError, TypeError, ValueError, RecursionError):
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
                 port=8443, admin_port=0):
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
        self._token = None
        self._public_address = self._admin_address = None

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
        path = Path(value).resolve()
        protected = [self.directory / name for name in
                     ('admin-token', 'server-key.pem', 'server-cert.pem',
                      'server.sqlite', 'server.sqlite-wal', 'server.sqlite-shm')]
        if any(path == item.resolve() or
               (path.exists() and item.exists() and os.path.samefile(path, item))
               for item in protected):
            raise ProtocolError('Reserved runtime path')

    def start(self):
        with self._lock:
            if self._servers:
                return self
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                _protect_directory(self.directory)
                self._token = _admin_token(self.directory / 'admin-token')
                context, bundle = _certificate(self.directory, self.address)
                for name in ('server.sqlite', 'server.sqlite-wal', 'server.sqlite-shm'):
                    path = self.directory / name
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        _regular(path)
                        tls.protect_private(path)
                self._store = Store(self.directory / 'server.sqlite')
                public = _Server((self.host, self.port), self, context=context)
                self._servers.append(public)
                admin = _Server(('127.0.0.1', self.admin_port), self, admin=True)
                self._servers.append(admin)
                authority = f'[{self._hostname}]' if ':' in self._hostname else self._hostname
                self._public_address = f'https://{authority}:{public.server_port}'
                self._admin_address = f'http://127.0.0.1:{admin.server_port}'
                self._validate_certificate(context, bundle)
                for server in self._servers:
                    thread = threading.Thread(target=server.serve_forever,
                                              kwargs={'poll_interval': 0.05},
                                              name='Lifetime-listener')
                    thread.start()
                    self._threads.append(thread)
                return self
            except BaseException:
                self.stop()
                raise

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

    def stop(self):
        with self._lock:
            for server, thread in zip(self._servers, self._threads):
                server.shutdown()
                thread.join()
            for server in self._servers:
                server.close_connections()
            for server in self._servers:
                server.server_close()
            self._servers.clear()
            self._threads.clear()
            if self._store is not None:
                self._store.close()
                self._store = None
            self._token = None
            self._public_address = self._admin_address = None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lifetime Server console runtime')
    parser.add_argument('mode', nargs='?', choices=('console',), default='console')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--address', default='https://localhost:8443')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--admin-port', type=int, default=0)
    args = parser.parse_args(argv)
    configuration = vars(args)
    configuration.pop('mode')
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
