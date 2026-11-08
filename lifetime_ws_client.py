"""The computer's end of the persistent live channel.

One WebSocket over the same pinned TLS and credential as the HTTPS sync. It sends live samples at the
rate the server asks for and receives commands the moment they are issued. If the socket cannot be
opened (an older server, a proxy, a closed network) the caller keeps using the HTTPS live path.
"""
import contextlib
import datetime as dt
import hmac
import hashlib
import random
import ssl
import subprocess
import time

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

import lifetime_protocol as protocol
import lifetime_ws as ws
from lifetime_tls import _address, decode_bundle

BACKOFF_CAP = 60
OLD_SERVER_RETRY = 300      # a server that answers 404 does not have the channel; ask again every five minutes


class WebSocketTransport(ws.TelemetryTransport):
    def __init__(self, connection):
        self.connection = connection

    def send_sample(self, sample):
        self.connection.send(ws.envelope('telemetry', sample))


class LiveLink:
    def __init__(self, agent, wake, sampler, stop, transport=WebSocketTransport):
        self.agent, self.wake, self.sampler, self.stop = agent, wake, sampler, stop
        self.transport = transport
        self.failures = 0
        self.retry_at = 0.0
        self.connected = False

    # ---- scheduling
    def due(self):
        return time.monotonic() >= self.retry_at

    def _later(self, error=None):
        self.failures = min(self.failures + 1, 10)
        delay = min(BACKOFF_CAP, 2 ** self.failures) * random.uniform(.75, 1.25)    # jitter keeps a fleet from reconnecting in step
        if isinstance(error, InvalidStatus) and error.response.status_code == 404:
            delay = OLD_SERVER_RETRY
        self.retry_at = time.monotonic() + delay

    # ---- one connection
    def _manager(self, state):
        value = decode_bundle(state['bundle']) if isinstance(state['bundle'], str) else state['bundle']
        _, host, port = _address(value['address'])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        context.load_verify_locations(cadata=value['certificate_pem'])
        authority = f'[{host}]' if ':' in host else host
        manager = connect(f"wss://{authority}:{port}{ws.PATH}?device={state['device_id']}", ssl=context,
                          additional_headers={'Authorization': 'Bearer ' + state['credential']},
                          open_timeout=5, ping_interval=15, ping_timeout=15, close_timeout=2,
                          max_size=ws.MAX_FRAME, compression=None, proxy=None, user_agent_header=None)
        return manager, value['fingerprint']

    def session(self, state, read_state, eligible):
        """Run one socket until it ends. Returns True if it connected, False if the caller should use HTTPS."""
        with contextlib.ExitStack() as stack:
            try:
                manager, fingerprint = self._manager(state)
                connection = stack.enter_context(manager)
                actual = hashlib.sha256(connection.socket.getpeercert(binary_form=True)).hexdigest()
                if not hmac.compare_digest(actual, fingerprint):
                    raise ssl.SSLCertVerificationError('Server certificate fingerprint mismatch')
            except Exception as error:
                self._later(error)
                return False
            self.failures, self.connected = 0, True
            try:
                self._pump(connection, state, read_state, eligible)
            except (ConnectionClosed, OSError, TimeoutError, ws.EnvelopeError, ValueError):
                pass
            finally:
                self.connected = False
                self.retry_at = time.monotonic() + random.uniform(.3, 1.0)      # reconnect promptly after a drop
        return True

    def _pump(self, connection, state, read_state, eligible):
        sink = self.transport(connection)
        connection.send(ws.envelope('hello', dict(version=ws.VERSION, capabilities=['telemetry', 'command'])))
        rate = ws.clamp_rate(10000)
        next_send = time.monotonic()
        next_check = 0.0
        slow = {}
        while not self.stop.is_set():
            now = time.monotonic()
            if now >= next_check:
                current = read_state()
                if not eligible(current) or current.get('credential') != state['credential']:
                    return      # disconnected, consent withdrawn or a new credential: close and start over
                slow = (current.get('readings') or {}).get('performance') or {}
                next_check = now + 1.0
            try:
                message = ws.parse(connection.recv(timeout=max(.001, min(next_send - now, 1.0))))
            except TimeoutError:
                message = None
            if message is not None:
                faster = self._on_message(connection, state, message, rate)
                if faster < rate:
                    next_send = min(next_send, now + faster / 1000)     # a viewer arrived: do not finish the slow wait first
                rate = faster
            now = time.monotonic()
            if now >= next_send:
                sink.send_sample(self.sampler.sample(slow))
                next_send = now + rate / 1000

    def _on_message(self, connection, state, message, rate):
        data = message.get('data')
        if message['type'] in ('hello', 'status') and isinstance(data, dict) and 'rate_ms' in data:
            return ws.clamp_rate(data['rate_ms'])
        if message['type'] == 'command':
            self._command(connection, message)
        return rate

    # ---- commands pushed by the server
    def _ack(self, connection, command_id, status, detail=''):
        connection.send(ws.envelope('ack', dict(id=command_id, status=status, detail=detail)))

    def _command(self, connection, message):
        """Dedupe by id, honour consent, answer first, then act. Never runs the same command twice."""
        command_id, data = message['id'], message.get('data')
        try:
            protocol.identifier(command_id)
            action, issued = data['action'], protocol.timestamp(data['issued_at'])
        except Exception:
            self._ack(connection, command_id, 'failed', 'Invalid command')
            return
        verdict = None
        with self.agent._transaction() as current:
            if command_id == current.get('last_command_id'):
                verdict = ('executed', 'Already handled')       # a re-delivery after a reconnect: acknowledge, do nothing
            elif action not in ('sleep', 'restart', 'shutdown', 'refresh'):
                verdict = ('unsupported', 'Unsupported command action')
            elif not -60 <= (dt.datetime.now(dt.timezone.utc) - issued).total_seconds() < 600:     # allow a minute of clock difference
                verdict = ('failed', 'Expired command')
            elif action != 'refresh' and not current['policy'].get('control'):
                verdict = ('declined', 'Remote power consent is required')
            if verdict is None:
                current['last_command_id'] = command_id
                verdict = ('executed', 'Command accepted')
                run = action
            else:
                run = None
        self._ack(connection, command_id, *verdict)
        if run == 'refresh':
            self.wake.set()         # the HTTPS sync sends a fresh full reading straight away
        elif run:
            from lifetime_agent import execute_power
            try:
                execute_power(run)  # sleep, restart and shutdown carry the existing one minute warning
            except (OSError, subprocess.SubprocessError, ValueError):
                self._ack(connection, command_id, 'failed', 'Power action failed')
