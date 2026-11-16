"""Explicit company enrollment and retryable, bounded sync; standard library only.

SyncClient(agent).start_enrollment(bundle, identity, policy) is the consent gate.
step() performs at most one request and otherwise returns redacted local status.
No constructor, import or standalone capture initiates any network request.
Commands are persisted and acknowledged before any power action is executed.
"""
import contextlib
import http.client
import math
import secrets
import time
import uuid
import subprocess

import lifetime_protocol as protocol
import lifetime_tls as tls
from lifetime_agent import validate_policy

MAX_BACKOFF = 300
PENDING_POLL = 2    # seconds between approval checks, so a new computer is not left waiting for the 30 s report interval
# ...for the first two minutes. A request nobody approves keeps asking, so it then slows down: a few hundred
# computers left pending overnight would otherwise make hundreds of TLS handshakes a second for nothing.
PENDING_SLOW = ((120, 15), (3600, 60))      # (seconds waited, seconds between checks from then on)


def pending_poll(waited):
    return max([PENDING_POLL] + [seconds for after, seconds in PENDING_SLOW if waited >= after])
MAX_INTERVAL = 3600
REDISCOVER_EVERY = 600      # while the stored address fails, ask local discovery at most this often
TRUST = ('address', 'certificate_pem', 'fingerprint', 'spki')
LOST = ('certificate', 'hostname', 'tls', 'name', 'refused', 'timeout', 'dropped', 'network')


PROBE_EVERY = 15        # seconds between reachability checks while the server answers
PROBE_RETRY = 25        # longest wait between checks while it does not (10, 15, 20, then 25 s)
STATUS_HEARTBEAT = 60   # the status file is rewritten at least this often, so a reader can tell the agent is alive


class ResponseError(ValueError):
    """Untrusted or incompatible response (never includes response/secret values)."""


class ServerWatch:
    """Whether the server answers right now, for the desktop app's Server: Online / Offline chip.

    The agent's own sync runs only every reporting interval and backs off to five minutes while the server is away,
    so on its own cadence this checks with one pinned TLS handshake (lifetime_tls.verify_server: the stored
    certificate or a renewal carrying the pinned key, no application data, nothing learned is kept). It does not
    probe while the live socket is connected or a sync has just succeeded. When the server comes back it wakes the
    sync loop, so waiting reports go out at once instead of after the backoff. Only a category is recorded."""
    def __init__(self, agent, wake, live=lambda: False, probe=None, clock=time.time):
        self.agent, self.wake, self.live, self.clock = agent, wake, live, clock
        self.probe = probe or (lambda bundle: tls.verify_server(bundle, timeout=3))
        self.failures, self.seen, self.written, self.written_at = 0, None, None, 0.0

    @staticmethod
    def _epoch(stamp):
        import calendar
        try:
            return calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')) if stamp else None
        except (TypeError, ValueError, OverflowError):
            return None

    def check(self):
        """One check; returns the seconds until the next one."""
        with self.agent._lock:
            state = self.agent._load()
        if not state['enabled'] or state['phase'] not in ('pending', 'approved') or not state.get('bundle'):
            self.agent.reach, self.failures, self.written = None, 0, None
            return PROBE_EVERY
        now = self.clock()
        synced = self._epoch(state.get('last_sync'))
        self.seen = max(value for value in (self.seen, synced, 0) if value is not None) or None
        problem = None
        if self.live() or (state.get('status') == 'connected' and synced and now - synced < PROBE_EVERY):
            reachable = True
        else:
            try:
                self.probe(dict(state['bundle']))
                reachable = True
            except Exception as error:      # any failure is a category, never its text
                reachable, problem = False, error_kind(error)
        if reachable:
            self.seen, self.failures = now, 0
            if state.get('status') == 'unreachable':
                self.wake.set()             # the server is back: send what waited now, not after the backoff
        else:
            self.failures = min(self.failures + 1, 10)
        self.agent.reach = dict(server_reachable=reachable, server_problem=problem, checked_at=int(now),
                                server_seen_at=int(self.seen) if self.seen else None)
        if (reachable, problem) != self.written or now - self.written_at >= STATUS_HEARTBEAT:
            self.written, self.written_at = (reachable, problem), now
            self.agent.write_status()
        return PROBE_EVERY if reachable else min(PROBE_RETRY, 5 + 5 * self.failures)


def error_kind(error):
    """A short, secret-free category for a failed exchange, so people see why instead of a bare 'unreachable'.

    Only the category is stored and shown; the exception text can contain addresses and is never published."""
    import socket
    import ssl
    status = getattr(error, 'status', None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f'http-{status}'
    if isinstance(error, ssl.SSLCertVerificationError):
        code = getattr(error, 'verify_code', None)
        if code in (9, 10):
            return 'expired'            # the server's certificate is out of date, or this computer's clock is wrong
        if code in (62, 64):
            return 'hostname'           # the address used is not one of the names in the server's certificate
        return 'certificate'            # a different or replaced server identity: never accepted silently
    if isinstance(error, ssl.SSLError):
        return 'tls'
    if isinstance(error, socket.gaierror):
        return 'name'                   # the server's name could not be found
    if isinstance(error, ConnectionRefusedError):
        return 'refused'                # the computer answered, but nothing listens on the port
    if isinstance(error, (TimeoutError, socket.timeout)):
        return 'timeout'                # no answer: switched off, asleep or blocked by a firewall
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError, http.client.HTTPException)):
        return 'dropped'
    if isinstance(error, OSError):
        return 'network'                # no route, network down, adapter off
    if isinstance(error, ValueError):
        return 'protocol'               # a reply this version does not understand (often a version mismatch)
    return 'internal'


def _fields(value, allowed, required):
    if (not isinstance(value, dict) or set(value)-set(allowed)
            or set(required)-set(value)):
        raise ResponseError('Unsupported server response fields')


def _secret(value):
    if (not isinstance(value, str) or not 32 <= len(value) <= 256
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ResponseError('Invalid enrollment credential')
    return value


class SyncClient:
    def __init__(self, agent, timeout=10):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 60):
            raise ValueError('Client timeout must be positive and at most 60 seconds')
        self.agent = agent
        self.timeout = timeout
        self._rediscover_at = 0.0

    def _rediscover(self, bundle):
        """The stored address stopped working: ask local discovery where the server is now.

        A discovered address is only a hint. It is kept only if a fully verified connection to it
        presents the pinned key and a certificate that names that address; nothing else is sent."""
        from lifetime_connection import address
        from lifetime_discovery import discover
        candidates = []
        for server in discover(timeout=2):
            for value in (server.get('address'), server.get('source') and f"https://{server['source']}:{server['port']}"):
                try:
                    value = address(value)
                except (TypeError, ValueError):
                    continue
                if value != bundle['address'] and value not in candidates:
                    candidates.append(value)
        for candidate in candidates[:8]:
            try:
                return tls.verify_server(dict(bundle, address=candidate), timeout=min(3, self.timeout))
            except (OSError, ValueError):
                continue
        return None

    def start_connection(self, code, address, identity, policy):
        from lifetime_connection import bootstrap
        # Validate local consent/identity before even the certificate probe.
        identity = protocol.validate_identity(identity)
        policy = validate_policy(policy)
        bundle = bootstrap(address, code, timeout=min(5, self.timeout))
        return self.start_enrollment(bundle, identity, policy)

    def start_enrollment(self, bundle, identity, policy):
        """Persist UUIDs, polling secret and consent BEFORE the first request.

        Repeating the same pending enrollment resumes its identical request.
        Explicit re-enrollment preserves the device UUID and sequence namespace.
        """
        trust = tls.decode_bundle(bundle)
        identity = protocol.validate_identity(identity)
        policy = validate_policy(policy)
        with self.agent.reporting_lock():
            with self.agent._transaction() as state:
                existing = state['enrollment']
                stored = {key: value for key, value in (state['bundle'] or {}).items() if key != 'spki'}
                resume = (state['phase'] in ('enrolling', 'pending') and stored == trust
                          and existing and existing['identity'] == identity and existing['policy'] == policy)
                if not resume:
                    self.agent._set_policy(state, policy)
                    state.pop('problem', None)      # a failure of the earlier connection is not this one's problem
                    state.update(reported=False, bundle=trust, credential=None, enabled=True,
                                 phase='enrolling', status='enrolling', enrollment_sent=False,
                                 last_error=None, last_error_at=None,
                                 failures=0, next_attempt=0, capabilities=[], trimmed=False, legacy_server_until=0,
                                 pending_since=None,
                                 enrollment=dict(version=1, request_id=str(uuid.uuid4()),
                                                 device_id=state['device_id'], code=trust['code'],
                                                 poll_token=secrets.token_urlsafe(32),
                                                 identity=identity, policy=policy))
                else:
                    state.update(enabled=True, failures=0, next_attempt=0)
            self.agent._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.agent.write_status()
        return self.step(force=True)

    def _enrollment_reply(self, response, state, *, claim):
        required = {'request_id', 'device_id', 'status'}
        allowed = required | {'capabilities'} | ({'credential'} if claim else set())
        _fields(response, allowed, required)
        self._capabilities(response)
        if (response['device_id'] != state['device_id']
                or response['request_id'] != state['enrollment']['request_id']
                or response['status'] not in ('pending', 'approved', 'denied', 'revoked')):
            raise ResponseError('Enrollment response does not match the pending request')
        approved = response['status'] == 'approved'
        if ('credential' in response) != (claim and approved):
            raise ResponseError('Unexpected enrollment credential')
        if claim and approved:
            _secret(response['credential'])

    def _ack_reply(self, response, state, message):
        required = {'device_id', 'accepted', 'duplicates', 'retired', 'policy_version', 'received_at'}
        _fields(response, required | {'reporting_interval', 'refresh', 'command', 'command_ack', 'capabilities'}, required)
        self._capabilities(response)
        if response['device_id'] != state['device_id']:
            raise ResponseError('Acknowledgment device mismatch')
        version = protocol.number(response['policy_version'], True)
        if version < message['policy_version']:
            raise ResponseError('Server acknowledged a stale policy version')
        protocol.timestamp(response['received_at'])
        sent = {record['seq'] for record in message['records']}
        acknowledged = set()
        for field in ('accepted', 'duplicates', 'retired'):
            values = response[field]
            if not isinstance(values, list) or len(values) > protocol.MAX_RECORDS:
                raise ResponseError('Invalid acknowledgment list')
            for seq in values:
                protocol.number(seq, True)
                if seq not in sent or seq in acknowledged:
                    raise ResponseError('Acknowledgment contains an unsent or repeated sequence')
                acknowledged.add(seq)
        if acknowledged != sent:
            raise ResponseError('Acknowledgment omitted sent records')
        if 'reporting_interval' in response and (type(response['reporting_interval']) is not int
                                                or not 5 <= response['reporting_interval'] <= MAX_INTERVAL):
            raise ResponseError('Invalid reporting interval')
        if 'refresh' in response and type(response['refresh']) is not bool:
            raise ResponseError('Invalid refresh hint')
        if 'command_ack' in response:
            ack = response['command_ack']
            _fields(ack, {'id', 'status'}, {'id', 'status'})
            if not message.get('command_result') or ack['id'] != message['command_result']['id'] or ack['status'] not in ('done', 'failed', 'cancelled', 'expired'):
                raise ResponseError('Invalid command acknowledgment')
        if 'command' in response:
            command = response['command']
            _fields(command, {'id', 'action', 'issued_at'}, {'id', 'action', 'issued_at'})
            protocol.identifier(command['id'])
            issued = protocol.timestamp(command['issued_at'])
            if not isinstance(command['action'], str) or len(command['action']) > 128:
                raise ResponseError('Invalid command action')
            age = (protocol.timestamp(response['received_at'])-issued).total_seconds()
            if not 0 <= age < 600:
                raise ResponseError('Expired command')
        return acknowledged

    @staticmethod
    def _capabilities(response):
        """What the server said it accepts; a reply without a list comes from a 1.1 server."""
        try:
            return protocol.validate_capabilities(response.get('capabilities', []))
        except ValueError:
            raise ResponseError('Invalid capability list') from None

    def _exchange(self, state):
        phase = state['phase']
        self._sent_extended = False
        self._full = None
        if phase == 'enrolling':
            message = state['enrollment']
            response = tls.request_json(state['bundle'], '/v1/enroll', message, timeout=self.timeout)
            self._enrollment_reply(response, state, claim=False)
            return 'enroll', response, message
        if phase == 'pending':
            message = dict(request_id=state['enrollment']['request_id'],
                           poll_token=state['enrollment']['poll_token'])
            response = tls.request_json(state['bundle'], '/v1/claim', message, timeout=self.timeout)
            self._enrollment_reply(response, state, claim=True)
            return 'claim', response, message
        if phase != 'approved' or not state['credential']:
            raise ResponseError('No approved reporting credential')
        message = self.agent.batch()
        message['client_version'] = protocol.CLIENT_VERSION
        # Send only what this server said it accepts (remembered from its last reply; none known = 1.1).
        # After a 400 for optional content, send the 1.1 format for a day so reporting never stalls.
        accepted = state.get('capabilities') or []
        if time.time() < state.get('legacy_server_until', 0):
            accepted = []
        self._full = message
        if state.get('problem'):        # why the last attempt failed, for the server's device page (category only)
            message = dict(message, problem=state['problem'])
        message = protocol.without_extended(message, accepted)
        self._sent_extended = message != protocol.without_extended(message)
        response = tls.request_json(state['bundle'], '/v1/sync', message,
                                    credential=state['credential'], timeout=self.timeout)
        self._ack_reply(response, state, message)
        return 'sync', response, message

    def step(self, *, force=False):
        """Attempt enrollment, claim or sync; retain data on every unverified failure.

        The local lock serializes sending with consent revocation: after set_policy
        returns, an in-flight request cannot send an old optional snapshot.
        Backoff is durable and bounded, and sampling itself never uses the network.
        """
        with self.agent.reporting_lock():
            state = self.agent._load()
            if not state['enabled'] or state['phase'] not in ('enrolling', 'pending', 'approved'):
                return self.agent.status()
            now = time.time()
            if not force and 0 < state['next_attempt']-now <= MAX_INTERVAL:
                return self.agent.status()
            lost = None
            try:
                power_action = None
                kind, response, message = self._exchange(state)
                with self.agent._transaction() as current:
                    self._keep_trust(current, state['bundle'])
                    current.update(failures=0, next_attempt=now+current['interval'],
                                   capabilities=self._capabilities(response))
                    if kind == 'sync':
                        sent_result = message.get('command_result')
                        if sent_result and current.get('command_result') == sent_result:
                            current.pop('command_result', None)
                            pending = current.pop('pending_power', None)
                            if (pending and pending['id'] == sent_result['id'] and current['policy'].get('control')
                                    and response.get('command_ack') == dict(id=sent_result['id'], status='done')
                                    and 0 <= (protocol.timestamp(response['received_at'])-protocol.timestamp(pending['issued_at'])).total_seconds() < 600):
                                power_action = pending['action']
                        command = response.get('command')
                        if command and command['id'] != current.get('last_command_id'):
                            action = command['action']
                            status, detail = 'done', 'Command accepted'
                            if action not in ('sleep', 'restart', 'shutdown', 'refresh'):
                                status, detail = 'failed', 'Unsupported command action'
                            elif action != 'refresh' and not current['policy'].get('control'):
                                status, detail = 'declined', 'Remote power consent is required'
                            elif action != 'refresh':
                                current['pending_power'] = dict(id=command['id'], action=action, issued_at=command['issued_at'])
                            current['last_command_id'] = command['id']
                            current['command_result'] = dict(id=command['id'], status=status, detail=detail)
                        for record in message['records']:
                            self.agent._db.execute('DELETE FROM outbox WHERE seq=?', (record['seq'],))
                        version = response['policy_version']
                        if version > current['policy_version']:
                            current['policy_version'] = protocol.number(version+1, True)
                        if 'readings' in message or not current['policy'].get('resources'):
                            current['reported'] = True
                        current.update(last_sync=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                                       status='connected', enrollment=None, enrollment_sent=True,
                                       last_error=None, last_error_at=None)
                        current.pop('problem', None)    # told the server (or it cannot take it); either way it is history
                        current['bundle']['code'] = 'enrolled'
                        if 'reporting_interval' in response:
                            current['interval'] = response['reporting_interval']
                        current['next_attempt'] = now + (0 if response.get('refresh', False) else current['interval'])
                        if current.get('command_result'):
                            current['next_attempt'] = 0
                        # Disclose trimming ("server is older, some details are not shared"), never silently.
                        accepted = [] if now < current.get('legacy_server_until', 0) else current['capabilities']
                        current['trimmed'] = protocol.without_extended(self._full, accepted) != self._full
                        if message != self._full and not current['trimmed']:
                            current['next_attempt'] = 0     # the server takes more than was sent: send it now
                    else:
                        status = response['status']
                        current['enrollment_sent'] = True
                        # The server answered: an earlier failed attempt (say an old code's http-403) is over, and is
                        # not reported as the new computer record's "last problem" either.
                        current.update(last_error=None, last_error_at=None)
                        current.pop('problem', None)
                        if kind == 'claim' and status == 'approved':
                            current.update(credential=response['credential'], phase='approved',
                                           status='approved', next_attempt=0)
                        elif status in ('pending', 'approved'):
                            since = current.get('pending_since') or now
                            current.update(phase='pending', status='pending', pending_since=since,
                                           next_attempt=now+pending_poll(now-since))
                        else:
                            current.update(phase=status, status=status, enabled=False, credential=None)
                            self.agent._set_policy(current, dict(history=False, resources=False))
                if power_action:
                    from lifetime_agent import execute_power
                    try:
                        execute_power(power_action)
                    except (OSError, subprocess.SubprocessError):
                        with self.agent._transaction() as current:
                            current['command_result'] = dict(id=sent_result['id'], status='failed', detail='Power action failed')
                            current['next_attempt'] = 0
            except (OSError, http.client.HTTPException, ValueError) as error:
                with self.agent._transaction() as current:
                    self._keep_trust(current, state['bundle'])
                    current['failures'] = min(current['failures']+1, 20)
                    delay = min(MAX_BACKOFF, 2**(current['failures']-1))
                    current.update(next_attempt=now+delay, status='unreachable',
                                   last_error=error_kind(error), last_error_at=int(now))
                    with contextlib.suppress(protocol.ProtocolError):
                        current['problem'] = protocol.validate_problem(dict(
                            kind=current['last_error'], at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))))
                    if (current['last_error'] in LOST and current['failures'] >= 2
                            and time.monotonic() >= self._rediscover_at):
                        lost = dict(current['bundle'])
                    status = getattr(error, 'status', None)
                    if status in (401, 403) and state['phase'] in ('enrolling', 'pending'):
                        current.update(status='invalid-code', phase='disabled', enabled=False,
                                       enrollment=None, credential=None)
                        self.agent._set_policy(current, dict(history=False, resources=False))
                    elif status == 400 and getattr(self, '_sent_extended', False):
                        # The server refused optional detail it had advertised: resend the 1.1 format for a day.
                        current.update(legacy_server_until=now+86400, next_attempt=0, trimmed=True)
                    elif status == 409:
                        current['status'] = 'conflict'
                    elif status in (401, 403) and state['phase'] == 'approved':
                        current.update(status='revoked', phase='revoked', enabled=False, credential=None,
                                       enrollment=None)
                        self.agent._set_policy(current, dict(history=False, resources=False))
            if lost is not None:
                # Outside the state transaction: discovery and its verified probes take a few seconds.
                self._rediscover_at = time.monotonic() + REDISCOVER_EVERY
                found = self._rediscover(lost)
                if found is not None:
                    with self.agent._transaction() as current:
                        self._keep_trust(current, found)
                        current['next_attempt'] = 0
            # This truncates prior policy snapshots and rotated/retired credentials.
            self.agent._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.agent.write_status()
            return self.agent.status()

    @staticmethod
    def _keep_trust(current, bundle):
        """Persist a key pin, renewed certificate or rediscovered address learned on a verified connection."""
        if current.get('bundle') and bundle:
            current['bundle'].update({key: bundle[key] for key in TRUST if key in bundle})
