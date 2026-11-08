"""Explicit company enrollment and retryable, bounded sync; standard library only.

SyncClient(agent).start_enrollment(bundle, identity, policy) is the consent gate.
step() performs at most one request and otherwise returns redacted local status.
No constructor, import or standalone capture initiates any network request.
Only acknowledgments, reporting interval and a boolean refresh hint are accepted.
"""
import http.client
import math
import secrets
import time
import uuid

import lifetime_protocol as protocol
import lifetime_tls as tls
from lifetime_agent import validate_policy

MAX_BACKOFF = 300
MAX_INTERVAL = 3600


class ResponseError(ValueError):
    """Untrusted or incompatible response (never includes response/secret values)."""


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
                resume = (state['phase'] in ('enrolling', 'pending') and state['bundle'] == trust
                          and existing and existing['identity'] == identity and existing['policy'] == policy)
                if not resume:
                    self.agent._set_policy(state, policy)
                    state.update(bundle=trust, credential=None, enabled=True,
                                 phase='enrolling', status='enrolling', enrollment_sent=False,
                                 failures=0, next_attempt=0,
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
        allowed = required | ({'credential'} if claim else set())
        _fields(response, allowed, required)
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
        _fields(response, required | {'reporting_interval', 'refresh'}, required)
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
        return acknowledged

    def _exchange(self, state):
        phase = state['phase']
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
            try:
                kind, response, message = self._exchange(state)
                with self.agent._transaction() as current:
                    current.update(failures=0, next_attempt=now+current['interval'])
                    if kind == 'sync':
                        for record in message['records']:
                            self.agent._db.execute('DELETE FROM outbox WHERE seq=?', (record['seq'],))
                        version = response['policy_version']
                        if version > current['policy_version']:
                            current['policy_version'] = protocol.number(version+1, True)
                        current.update(last_sync=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                                       status='connected', enrollment=None, enrollment_sent=True)
                        current['bundle']['code'] = 'enrolled'
                        if 'reporting_interval' in response:
                            current['interval'] = response['reporting_interval']
                        current['next_attempt'] = now + (0 if response.get('refresh', False) else current['interval'])
                    else:
                        status = response['status']
                        current['enrollment_sent'] = True
                        if kind == 'claim' and status == 'approved':
                            current.update(credential=response['credential'], phase='approved',
                                           status='approved', next_attempt=0)
                        elif status in ('pending', 'approved'):
                            current.update(phase='pending', status='pending')
                        else:
                            current.update(phase=status, status=status, enabled=False, credential=None)
                            self.agent._set_policy(current, dict(history=False, resources=False))
            except (OSError, http.client.HTTPException, ValueError) as error:
                with self.agent._transaction() as current:
                    current['failures'] = min(current['failures']+1, 20)
                    delay = min(MAX_BACKOFF, 2**(current['failures']-1))
                    current.update(next_attempt=now+delay, status='unreachable')
                    status = getattr(error, 'status', None)
                    if status == 409:
                        current['status'] = 'conflict'
                    elif status in (401, 403) and state['phase'] == 'approved':
                        current.update(status='revoked', phase='revoked', enabled=False, credential=None,
                                       enrollment=None)
                        self.agent._set_policy(current, dict(history=False, resources=False))
            # This truncates prior policy snapshots and rotated/retired credentials.
            self.agent._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.agent.write_status()
            return self.agent.status()
