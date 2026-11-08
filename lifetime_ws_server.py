"""Server end of the persistent live channel.

A computer connects with GET /v1/ws on the normal port. The existing HTTP handler has already parsed the
request on a pinned-TLS connection, so the upgrade reuses that connection: no new port or firewall rule.
Authentication happens during the upgrade; a bad credential gets a 403 before any data is accepted.
"""
import contextlib
import re
import socket
import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from websockets.exceptions import ConnectionClosed
from websockets.protocol import OPEN
from websockets.server import ServerProtocol
from websockets.sync.server import ServerConnection

import lifetime_ws as ws
from lifetime_protocol import ProtocolError, identifier
from lifetime_server_store import AuthenticationError, StoreError, ThrottledError

MAX_LINKS = 1024
SILENCE_SECONDS = 45        # no frame at all (telemetry or heartbeat) for this long ends the link
PING_SECONDS = 15
MAX_FRAMES_PER_SECOND = 40  # telemetry is at most 4 a second; this leaves room for acks and heartbeats


class _Owner:
    """Stands in for the websockets Server object, which only needs a shutdown flag."""
    def __init__(self):
        self.socket_closed = threading.Event()


class Link:
    def __init__(self, connection, device_id, credential):
        self.connection, self.device_id, self.credential = connection, device_id, credential
        self.rate_ms = None
        self.lock = threading.Lock()

    def send(self, kind, data=None, id=None):
        with self.lock:
            self.connection.send(ws.envelope(kind, data, id))

    def kill(self):
        with contextlib.suppress(OSError):
            self.connection.socket.shutdown(socket.SHUT_RDWR)


class LiveHub:
    def __init__(self, store):
        self.store = store
        self.links = {}
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(MAX_LINKS)
        self.nudge = threading.Event()
        self.stopping = threading.Event()
        self.dirty = set()
        self.listeners = []         # callables(event dict), used by the local administration stream
        store.hooks.update(clear=self.drop, nudge=self.nudge.set, command=self._command_created)
        self.ticker = threading.Thread(target=self._tick, name='lifetime-hub', daemon=True)
        self.ticker.start()

    # ---- events for in-process listeners (the administration stream)
    def _emit(self, event):
        for listener in list(self.listeners):
            with contextlib.suppress(Exception):
                listener(event)

    def connected(self, device_id):
        with self.lock:
            return device_id in self.links

    # ---- upgrade
    def upgrade(self, handler):
        """Called on the HTTP handler's thread with a parsed GET /v1/ws request."""
        self._guarded(handler, self._authenticate_computer, self._computer_session)

    def upgrade_admin(self, handler):
        """The administration stream: the HTTP layer has already checked the bearer token on loopback."""
        def authenticate(conn, request, who):
            if urlsplit(request.path).path != '/v1/stream':
                return conn.respond(HTTPStatus.NOT_FOUND, 'Not found\n')
            if request.headers.get('Origin') is not None:
                return conn.respond(HTTPStatus.FORBIDDEN, 'Forbidden\n')
            return None
        self._guarded(handler, authenticate, self._admin_session)

    def _guarded(self, handler, authenticate, session):
        if self.stopping.is_set() or not self.slots.acquire(blocking=False):
            handler.send_error(503)
            return
        try:
            self._upgrade(handler, authenticate, session)
        finally:
            self.slots.release()

    def _authenticate_computer(self, conn, request, who):
        try:
            parts = urlsplit(request.path)
            if parts.path != ws.PATH:
                return conn.respond(HTTPStatus.NOT_FOUND, 'Not found\n')
            if request.headers.get('Origin') is not None:
                raise AuthenticationError('Browsers are not clients')
            device_id = identifier(parse_qs(parts.query)['device'][0])
            header = request.headers.get('Authorization', '')
            match = re.fullmatch(r'Bearer ([\x21-\x7e]{1,256})', header)
            if not match:
                raise AuthenticationError('Missing credential')
            self.store.authenticate_live(device_id, match.group(1))
            who.update(device_id=device_id, credential=match.group(1))
        except Exception:
            return conn.respond(HTTPStatus.FORBIDDEN, 'Forbidden\n')
        return None

    def _computer_session(self, connection, who):
        self._session(connection, who['device_id'], who['credential'])

    def _upgrade(self, handler, authenticate, session):
        sock = handler.connection
        raw = handler.raw_requestline + b''.join(
            f'{name}: {value}\r\n'.encode('latin-1', 'replace') for name, value in handler.headers.items()) + b'\r\n'
        sock.settimeout(None)
        connection = ServerConnection(sock, ServerProtocol(max_size=ws.MAX_FRAME), _Owner(),
                                      ping_interval=PING_SECONDS, ping_timeout=PING_SECONDS,
                                      close_timeout=2, max_queue=16)
        with connection.protocol_mutex:     # the request is already read, so hand it over
            connection.protocol.receive_data(raw)
            for event in connection.protocol.events_received():
                connection.process_event(event)
        who = {}
        try:
            connection.handshake(lambda conn, request: authenticate(conn, request, who), None, None, 5)
            if connection.protocol.state is not OPEN:
                connection.close_socket()
                return
            # Authenticated: long-lived links must not occupy the short-request slots that enrolment and sync use.
            handler.server.detach(sock)
            connection.start_keepalive()
            with connection:
                session(connection, who)
        except Exception:
            connection.close_socket()

    # ---- one connected computer
    def _session(self, connection, device_id, credential):
        link = Link(connection, device_id, credential)
        with self.lock:
            previous, self.links[device_id] = self.links.get(device_id), link
        if previous:
            previous.kill()                 # a reconnect replaces a half-dead older socket at once
        self.store.note_live_link(device_id, True)
        try:
            link.rate_ms = self.store.live_rate_ms(device_id)
            link.send('hello', dict(version=ws.VERSION, rate_ms=link.rate_ms, heartbeat=PING_SECONDS))
            self._deliver(link)
            self._emit(dict(type='status', device_id=device_id, online=True))
            window_start, frames = time.monotonic(), 0
            while not self.stopping.is_set():
                try:
                    raw = connection.recv(timeout=SILENCE_SECONDS)
                except TimeoutError:
                    break
                now = time.monotonic()
                if now - window_start >= 1:
                    window_start, frames = now, 0
                frames += 1
                if frames > MAX_FRAMES_PER_SECOND:
                    connection.close(1008, 'Too many messages')
                    break
                self._handle(link, ws.parse(raw))
        except (ConnectionClosed, ws.EnvelopeError, ProtocolError, AuthenticationError, StoreError, OSError):
            pass
        finally:
            with self.lock:
                current = self.links.get(device_id) is link
                if current:
                    del self.links[device_id]
            if current:
                self.store.note_live_link(device_id, False)     # a closed link means offline now
                self._emit(dict(type='status', device_id=device_id, online=False))

    def _handle(self, link, message):
        kind, data = message['type'], message.get('data')
        if kind == 'telemetry':
            try:
                self.store.ingest_live(link.device_id, link.credential,
                                       dict(device_id=link.device_id, sample=data), min_gap=.2)
            except ThrottledError:
                return
            self._emit(dict(type='telemetry', device_id=link.device_id,
                            sample=dict(data, observed_at=self.store.last_live_time(link.device_id)), ts=message.get('ts')))
            self._rate(link)
        elif kind == 'ack':
            if (not isinstance(data, dict) or data.get('status') not in ws.ACKS
                    or not isinstance(message.get('id'), str) or not isinstance(data.get('id'), str)):
                raise ws.EnvelopeError('Invalid acknowledgement')
            detail = data.get('detail') if isinstance(data.get('detail'), str) else ''
            self.store.command_ack(link.device_id, data['id'], data['status'], detail[:200])
            self._emit(dict(type='ack', device_id=link.device_id, command=data['id'], status=data['status']))
        # 'hello' and 'status' from a computer are heartbeats; the receive timeout is their only effect

    def _rate(self, link):
        wanted = self.store.live_rate_ms(link.device_id)
        if wanted != link.rate_ms:
            link.send('status', dict(rate_ms=wanted))
            link.rate_ms = wanted

    def _deliver(self, link):
        command = self.store.take_command(link.device_id)
        if not command:
            return
        try:
            link.send('command', dict(action=command['action'], issued_at=command['issued_at']), id=command['id'])
        except Exception:
            self.store.requeue_command(command['id'])   # not sent, so the next connection can still deliver it
            raise

    # ---- local administration stream (the server window)
    def _admin_session(self, connection, who):
        stream = _AdminStream(connection)
        self.listeners.append(stream.offer)
        try:
            with self.lock:
                online = sorted(self.links)
            connection.send(ws.envelope('hello', dict(version=ws.VERSION, online=online)))
            stream.start()
            while not self.stopping.is_set():
                try:
                    message = ws.parse(connection.recv(timeout=SILENCE_SECONDS))
                except TimeoutError:
                    break
                data = message.get('data')
                if message['type'] == 'status' and isinstance(data, dict) and 'watch' in data:
                    watch = data['watch']
                    self.store.watch(identifier(watch) if watch else None)     # a viewer is looking: send faster
        except (ConnectionClosed, ws.EnvelopeError, ProtocolError, OSError):
            pass
        finally:
            with contextlib.suppress(ValueError):
                self.listeners.remove(stream.offer)
            stream.close()

    # ---- hooks and housekeeping
    def _command_created(self, device_id):
        self.dirty.add(device_id)
        self.nudge.set()

    def drop(self, device_id):
        """Revoked, deleted or re-approved: the old credential must not keep a socket open."""
        with self.lock:
            link = self.links.get(device_id)
        if link:
            link.kill()

    def _tick(self):
        beat = 0
        while not self.stopping.is_set():
            self.nudge.wait(1.0)
            self.nudge.clear()
            beat += 1
            with self.lock:
                links = dict(self.links)
            dirty, self.dirty = self.dirty, set()
            for device_id, link in links.items():
                try:
                    self._rate(link)
                    if device_id in dirty or beat % 10 == 0:
                        self._deliver(link)
                except Exception:
                    link.kill()

    def stop(self):
        self.stopping.set()
        self.nudge.set()
        with self.lock:
            links = list(self.links.values())
        for link in links:
            link.kill()


class _AdminStream:
    """Latest-wins delivery to one viewer: telemetry for a device replaces an unsent older sample."""
    COALESCE = .2

    def __init__(self, connection):
        self.connection = connection
        self.pending, self.lock = {}, threading.Lock()
        self.ready, self.closed = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, name='lifetime-admin-stream', daemon=True)

    def start(self):
        self.thread.start()

    def offer(self, event):
        key = ('ack', event.get('command')) if event['type'] == 'ack' else (event['type'], event.get('device_id'))
        with self.lock:
            self.pending[key] = event
        self.ready.set()

    def _run(self):
        while not self.closed.is_set():
            self.ready.wait(1.0)
            if self.closed.wait(self.COALESCE):
                return
            self.ready.clear()
            with self.lock:
                events, self.pending = list(self.pending.values()), {}
            try:
                for event in events:
                    self.connection.send(ws.envelope(event['type'], event))
            except Exception:
                return

    def close(self):
        self.closed.set()
        self.ready.set()
