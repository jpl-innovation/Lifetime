"""Local, transactional storage for Lifetime Server (no network or logging).

All public results are JSON-compatible dictionaries, except list_devices (list)
and close (None). Secret material is returned only by create_code and claim.
Identity is seeded at first enrollment and thereafter owned by the server.
Usage records contain deltas; ``totals`` is the highest-sequence stock snapshot,
never a sum. Missing snapshots, optional data and receipt times are None.
"""
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import uuid

from lifetime_protocol import ProtocolError, validate_identity, validate_sync

SCHEMA_VERSION = 4
APPLICATION_ID = 0x4C544D45
MAX_BYTES = 1024 * 1024
MAX_CODES = 1000
MAX_PENDING = 1000
MAX_CODE_TTL = 86400
MAX_CODE_USES = 100
GUESS_LIMIT = 10
GUESS_WINDOW = 60
OVERDUE_SECONDS = 600
MAX_PAGE = 500
DELTA_FIELDS = ('awake_seconds', 'measured_seconds', 'imported_seconds')


class StoreError(ValueError):
    """Invalid store operation or backup."""


class ConflictError(StoreError):
    """Immutable data or enrollment state conflicts with an earlier request."""


class AuthenticationError(PermissionError):
    """Enrollment, polling or device authentication failed."""


class ThrottledError(AuthenticationError):
    """The persistent, server-wide enrollment guessing budget is exhausted."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _stamp(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec='microseconds')


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(dt.timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise StoreError('Expected a timezone-aware ISO timestamp') from None


def _json(value):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
        text.encode('utf-8')
        return text
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ProtocolError('Invalid JSON value') from None


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ProtocolError('Expected a canonical UUID') from None
    return value


def _secret(value, minimum=32):
    if not isinstance(value, str) or not minimum <= len(value) <= 256:
        raise AuthenticationError('Invalid secret')
    try:
        value.encode('ascii')
    except UnicodeError:
        raise AuthenticationError('Invalid secret') from None
    return value


def _policy(value):
    if (not isinstance(value, dict) or set(value) != {'history', 'resources'}
            or any(type(v) is not bool for v in value.values())):
        raise ProtocolError('Invalid reporting policy')
    return value


def _identity_columns(device_id, identity):
    return ('\n'.join([device_id, *identity.values()]).casefold(),
            identity.get('department', '').casefold())


def _page(limit, position, maximum=2**63 - 1):
    if (type(limit) is not int or not 1 <= limit <= MAX_PAGE
            or type(position) is not int or not 0 <= position <= maximum):
        raise StoreError('Invalid pagination limit or position')


def _migrate(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        app_id = db.execute('PRAGMA application_id').fetchone()[0]
        if version > SCHEMA_VERSION or version < 0:
            raise StoreError('Unsupported database version')
        if app_id not in (0, APPLICATION_ID) or (version and app_id != APPLICATION_ID):
            raise StoreError('Not a Lifetime Server database')
        if not version and db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise StoreError('Refusing to initialize a nonempty database')
        if version < 1:
            statements = (
                '''CREATE TABLE devices (
                    device_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','revoked')),
                    credential_hash TEXT, policy_version INTEGER NOT NULL,
                    policy TEXT NOT NULL, created_at TEXT NOT NULL,
                    last_received_at TEXT, latest_seq INTEGER NOT NULL DEFAULT 0,
                    latest_record TEXT, history TEXT, history_received_at TEXT,
                    readings TEXT, readings_received_at TEXT)''',
                '''CREATE TABLE codes (code_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL, remaining INTEGER NOT NULL CHECK(remaining >= 0))''',
                '''CREATE TABLE requests (request_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    request_digest TEXT NOT NULL, poll_hash TEXT, policy TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('pending','approved','denied','revoked','completed')),
                    created_at TEXT NOT NULL)''',
                '''CREATE TABLE records (device_id TEXT NOT NULL REFERENCES devices(device_id),
                    seq INTEGER NOT NULL CHECK(seq >= 1), digest TEXT NOT NULL,
                    observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(device_id, seq))''',
                'CREATE INDEX records_observed ON records(observed_at)',
                'CREATE INDEX requests_device ON requests(device_id)',
                '''CREATE TABLE throttle (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    window_start TEXT NOT NULL, failures INTEGER NOT NULL CHECK(failures >= 0))''',
            )
            for statement in statements:
                db.execute(statement)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=1')
        if version < 2:
            db.execute('ALTER TABLE devices ADD COLUMN retired_seq INTEGER NOT NULL DEFAULT 0')
            db.execute('ALTER TABLE devices ADD COLUMN retained_before TEXT')
            db.execute('CREATE TABLE settings (singleton INTEGER PRIMARY KEY CHECK(singleton=1), retained_before TEXT)')
            db.execute('INSERT INTO settings VALUES (1,NULL)')
            db.execute('PRAGMA user_version=2')
        if version < 3:
            for field in DELTA_FIELDS:
                db.execute(f'ALTER TABLE records ADD COLUMN {field} REAL NOT NULL DEFAULT 0 CHECK({field} >= 0)')
            cursor = db.execute('SELECT device_id,seq,digest,payload FROM records')
            while batch := cursor.fetchmany(100):
                for device_id, seq, digest, payload in batch:
                    record = json.loads(payload)
                    validate_sync({'version': 1, 'device_id': device_id,
                                   'policy_version': 1, 'policy': {'history': False, 'resources': False},
                                   'records': [record]})
                    if record['seq'] != seq or _hash(_json(record)) != digest:
                        raise StoreError('Invalid immutable record during migration')
                    db.execute('UPDATE records SET awake_seconds=?,measured_seconds=?,imported_seconds=? WHERE device_id=? AND seq=?',
                               (*[record[field] for field in DELTA_FIELDS], device_id, seq))
            db.execute('CREATE INDEX records_usage ON records(device_id,awake_seconds,measured_seconds,imported_seconds)')
            db.execute("ALTER TABLE devices ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")
            db.execute("ALTER TABLE devices ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
            cursor = db.execute('SELECT device_id,identity FROM devices')
            while batch := cursor.fetchmany(100):
                for device_id, identity in batch:
                    columns = _identity_columns(device_id, validate_identity(json.loads(identity)))
                    db.execute('UPDATE devices SET search_text=?,group_name=? WHERE device_id=?', (*columns, device_id))
            db.execute('CREATE INDEX devices_order ON devices(created_at,device_id)')
            db.execute('CREATE INDEX devices_status ON devices(status,created_at,device_id)')
            db.execute('CREATE INDEX devices_group ON devices(group_name,created_at,device_id)')
            db.execute('PRAGMA user_version=3')
        if version < 4:
            db.execute('ALTER TABLE settings ADD COLUMN reporting_interval INTEGER NOT NULL DEFAULT 30 CHECK(reporting_interval BETWEEN 5 AND 3600)')
            db.execute('ALTER TABLE devices ADD COLUMN refresh_requested INTEGER NOT NULL DEFAULT 0 CHECK(refresh_requested IN (0,1))')
            db.execute('PRAGMA user_version=4')
        db.commit()
    except BaseException:
        db.rollback()
        raise


class Store:
    """Thread-safe Store(path); separate instances/processes serialize via SQLite.

    create_code -> {code, expires_at, uses}; enroll/approve/deny ->
    {request_id, device_id, status}; claim -> same plus credential when approved.
    ingest -> {device_id, accepted, duplicates, retired, policy_version, received_at}.
    revoke/update_identity/get_device -> public device detail; list_devices omits
    records. Device results include pending_requests for admin approval, status
    (pending/approved/denied/revoked) and reporting_status (unavailable/reporting/
    report-overdue), derived from last_received_at with a 600-second interval.
    list_devices(limit=100,offset=0,search='',group='',status='') returns a page;
    group matches department and search matches identity/ID, case-insensitively.
    status accepts enrollment or reporting status. get_device(device_id,limit=100,
    after_seq=0) returns ascending records after an exclusive sequence cursor.
    Both limits are 1..500. record_count/retained_usage always cover all retained
    records; records_next_seq is the last emitted sequence when another page
    exists, otherwise None; records_truncated indicates that additional page.
    Device list results omit history/readings bodies and expose *_available and
    *_received_at instead; detail includes bodies. With long identity fields,
    dashboard callers should request limit=25 to fit the admin response budget.
    retain -> {deleted}; backup/restore -> {path, schema_version}.
    Errors: ProtocolError, StoreError/ConflictError, AuthenticationError/
    ThrottledError; sqlite3/OSError failures propagate after rollback.
    """

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute('PRAGMA foreign_keys=ON')
            self._db.execute('PRAGMA trusted_schema=OFF')
            _migrate(self._db)
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('PRAGMA synchronous=FULL')
        except BaseException:
            self._db.close()
            raise

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def create_code(self, ttl=600, uses=1):
        """Issue a hashed-at-rest code; ttl 1..86400 seconds, uses 1..100."""
        if (type(ttl) is not int or not 1 <= ttl <= MAX_CODE_TTL
                or type(uses) is not int or not 1 <= uses <= MAX_CODE_USES):
            raise StoreError('Invalid code lifetime or use limit')
        now = _now()
        code = secrets.token_hex(6)
        expires = _stamp(now + dt.timedelta(seconds=ttl))
        with self._transaction() as db:
            db.execute('DELETE FROM codes WHERE expires_at <= ? OR remaining=0', (_stamp(now),))
            if db.execute('SELECT count(*) FROM codes').fetchone()[0] >= MAX_CODES:
                raise StoreError('Too many live enrollment codes')
            db.execute('INSERT INTO codes VALUES (?,?,?)', (_hash(code), expires, uses))
        return {'code': code, 'expires_at': expires, 'uses': uses}

    def enroll(self, request):
        """Consume one use for a new request; exact retries consume no uses.

        The guessing budget is global (no client network identifiers), durable,
        and shared between Store instances. Re-enrollment requires approval and
        preserves server metadata, usage and the immutable sequence namespace.
        """
        keys = {'version', 'request_id', 'device_id', 'code', 'poll_token', 'identity', 'policy'}
        if not isinstance(request, dict) or set(request) != keys or type(request['version']) is not int or request['version'] != 1:
            raise ProtocolError('Invalid enrollment request')
        encoded = _json(request).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ProtocolError('Enrollment request too large')
        request = json.loads(encoded)
        request_id = _identifier(request['request_id'])
        device_id = _identifier(request['device_id'])
        token = _secret(request['poll_token'])
        code = _secret(request['code'], 1)
        identity = validate_identity(request['identity'])
        policy = _policy(request['policy'])
        canonical = dict(request, identity=identity, code=_hash(code), poll_token=_hash(token))
        digest = _hash(_json(canonical))
        failure = None
        result = None
        with self._transaction() as db:
            previous = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if not hmac.compare_digest(previous['request_digest'], digest):
                    raise ConflictError('Enrollment request conflicts with an earlier request')
                return self._request_result(previous)
            now = _now()
            throttle = db.execute('SELECT * FROM throttle WHERE singleton=1').fetchone()
            failures = 0
            start = _stamp(now)
            if throttle and (now - _time(throttle['window_start'])).total_seconds() < GUESS_WINDOW:
                failures, start = throttle['failures'], throttle['window_start']
            if failures >= GUESS_LIMIT:
                failure = ThrottledError('Enrollment temporarily throttled')
            else:
                found = db.execute('SELECT * FROM codes WHERE code_hash=?', (_hash(code),)).fetchone()
                if not found or found['remaining'] < 1 or _time(found['expires_at']) <= now:
                    db.execute('INSERT OR REPLACE INTO throttle VALUES (1,?,?)', (start, failures + 1))
                    failure = AuthenticationError('Invalid or expired enrollment code')
                else:
                    if db.execute("SELECT count(*) FROM requests WHERE status='pending'").fetchone()[0] >= MAX_PENDING:
                        raise StoreError('Too many pending enrollments')
                    db.execute('UPDATE codes SET remaining=remaining-1 WHERE code_hash=?', (_hash(code),))
                    db.execute('''INSERT OR IGNORE INTO devices
                        (device_id,identity,status,policy_version,policy,created_at,search_text,group_name)
                        VALUES (?,?,'pending',1,?,?,?,?)''',
                               (device_id, _json(identity), _json(policy), _stamp(now), *_identity_columns(device_id, identity)))
                    db.execute('UPDATE devices SET retained_before=(SELECT retained_before FROM settings WHERE singleton=1) WHERE device_id=?', (device_id,))
                    db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)',
                               (request_id, device_id, digest, _hash(token), _json(policy), 'pending', _stamp(now)))
                    result = {'request_id': request_id, 'device_id': device_id, 'status': 'pending'}
        if failure:
            raise failure
        return result

    @staticmethod
    def _request_result(row):
        return {key: row[key] for key in ('request_id', 'device_id', 'status')}

    @staticmethod
    def _request(db, request_id):
        row = db.execute('SELECT * FROM requests WHERE request_id=?', (_identifier(request_id),)).fetchone()
        if not row:
            raise StoreError('Unknown enrollment request')
        return row

    def approve(self, request_id):
        with self._transaction() as db:
            row = self._request(db, request_id)
            if row['status'] in ('approved', 'completed'):
                return self._request_result(row)
            if row['status'] != 'pending':
                raise ConflictError('Enrollment is no longer pending')
            db.execute("UPDATE requests SET status='revoked',poll_hash=NULL WHERE device_id=? AND request_id!=? AND status IN ('pending','approved','completed')",
                       (row['device_id'], request_id))
            # Approval owns the new consent; pairing never rewinds policy or usage.
            db.execute("UPDATE devices SET status='approved',credential_hash=NULL,policy=?,history=NULL,history_received_at=NULL,readings=NULL,readings_received_at=NULL WHERE device_id=?",
                       (row['policy'], row['device_id']))
            db.execute("UPDATE requests SET status='approved' WHERE request_id=?", (request_id,))
            return dict(self._request_result(row), status='approved')

    def deny(self, request_id):
        with self._transaction() as db:
            row = self._request(db, request_id)
            if row['status'] == 'denied':
                return self._request_result(row)
            if row['status'] != 'pending':
                raise ConflictError('Enrollment is no longer pending')
            db.execute("UPDATE requests SET status='denied' WHERE request_id=?", (request_id,))
            db.execute("UPDATE devices SET status='denied' WHERE device_id=? AND status='pending' AND NOT EXISTS (SELECT 1 FROM requests WHERE device_id=? AND status='pending')",
                       (row['device_id'], row['device_id']))
            return dict(self._request_result(row), status='denied')

    def claim(self, request_id, poll_token):
        token_hash = _hash(_secret(poll_token))
        with self._transaction() as db:
            row = self._request(db, request_id)
            if not row['poll_hash'] or not hmac.compare_digest(row['poll_hash'], token_hash):
                raise AuthenticationError('Invalid enrollment poll token')
            if row['status'] not in ('pending', 'denied', 'approved'):
                raise AuthenticationError('Enrollment no longer claimable')
            result = self._request_result(row)
            if row['status'] == 'approved':
                credential = secrets.token_urlsafe(32)
                db.execute("UPDATE devices SET credential_hash=? WHERE device_id=? AND status='approved'",
                           (_hash(credential), row['device_id']))
                result['credential'] = credential
            return result

    def ingest(self, device_id, credential, message):
        """Atomically authenticate, apply policy and insert an immutable batch.

        A conflict rolls back the entire batch, including policy/receipt changes.
        Retired sequences are acknowledged separately and never resurrected.
        Exact duplicate records keep their original receipt timestamp.
        """
        device_id = _identifier(device_id)
        credential_hash = _hash(_secret(credential))
        encoded = _json(message).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ProtocolError('Message too large')
        message = validate_sync(json.loads(encoded))
        if len(message['records']) > 100:
            raise ProtocolError('Batch exceeds record limit')
        if message['device_id'] != device_id:
            raise AuthenticationError('Device identity mismatch')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
            if (not row or row['status'] != 'approved' or not row['credential_hash']
                    or not hmac.compare_digest(row['credential_hash'], credential_hash)):
                raise AuthenticationError('Invalid device credential')
            current_policy = json.loads(row['policy'])
            version = row['policy_version']
            incoming_version = message['policy_version']
            if incoming_version == version and message['policy'] != current_policy:
                raise ConflictError('Policy changed without a new version')
            if incoming_version > version:
                current_policy, version = message['policy'], incoming_version
                db.execute('UPDATE devices SET policy_version=?,policy=? WHERE device_id=?',
                           (version, _json(current_policy), device_id))
            # Revoked consent removes the previously stored optional category.
            for category, permission in (('history', 'history'), ('readings', 'resources')):
                if not current_policy[permission]:
                    db.execute(f'UPDATE devices SET {category}=NULL,{category}_received_at=NULL WHERE device_id=?', (device_id,))
            received = _stamp(_now())
            accepted, duplicates, retired = [], [], []
            latest_seq = row['latest_seq']
            retired_seq = row['retired_seq']
            for record in message['records']:
                seq = record['seq']
                payload = _json(record)
                digest = _hash(payload)
                old = db.execute('SELECT digest FROM records WHERE device_id=? AND seq=?', (device_id, seq)).fetchone()
                if old:
                    if not hmac.compare_digest(old['digest'], digest):
                        raise ConflictError('Conflicting immutable usage record')
                    duplicates.append(seq)
                    continue
                observed = _stamp(_time(record['observed_at']))
                if seq <= retired_seq or (row['retained_before'] and observed < row['retained_before']):
                    retired.append(seq)
                    retired_seq = max(retired_seq, seq)
                    continue
                db.execute('''INSERT INTO records (device_id,seq,digest,observed_at,received_at,payload,
                    awake_seconds,measured_seconds,imported_seconds) VALUES (?,?,?,?,?,?,?,?,?)''',
                           (device_id, seq, digest, observed, received, payload,
                            *[record[field] for field in DELTA_FIELDS]))
                accepted.append(seq)
                if seq > latest_seq:
                    latest_seq = seq
                    db.execute('UPDATE devices SET latest_seq=?,latest_record=? WHERE device_id=?',
                               (seq, payload, device_id))
            batch_latest = max((record['seq'] for record in message['records']), default=latest_seq)
            if incoming_version >= row['policy_version'] and batch_latest >= latest_seq:
                for category, permission in (('history', 'history'), ('readings', 'resources')):
                    if category in message and current_policy[permission] and message['policy'][permission]:
                        value = message[category]
                        if category == 'history':
                            value = self._retained_history(value, row['retained_before'])
                        elif row['retained_before'] and _stamp(_time(value['observed_at'])) < row['retained_before']:
                            continue
                        elif row['readings'] and _time(value['observed_at']) < _time(json.loads(row['readings'])['observed_at']):
                            continue
                        db.execute(f'UPDATE devices SET {category}=?,{category}_received_at=? WHERE device_id=?',
                                   (_json(value), received, device_id))
            db.execute('UPDATE devices SET retired_seq=?,last_received_at=? WHERE device_id=?', (retired_seq, received, device_id))
            db.execute("UPDATE requests SET poll_hash=NULL,status='completed' WHERE device_id=? AND status='approved'", (device_id,))
            interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            refresh = bool(row['refresh_requested'])
            db.execute('UPDATE devices SET refresh_requested=0 WHERE device_id=?', (device_id,))
            return {'device_id': device_id, 'accepted': accepted, 'duplicates': duplicates,
                    'retired': retired, 'policy_version': version, 'received_at': received,
                    'reporting_interval': interval, 'refresh': refresh}

    @staticmethod
    def _device_row(db, device_id):
        row = db.execute('SELECT * FROM devices WHERE device_id=?', (_identifier(device_id),)).fetchone()
        if not row:
            raise StoreError('Unknown device')
        return row

    @staticmethod
    def _public_device(db, row, detail=False, limit=100, after_seq=0):
        received = row['last_received_at']
        reporting_status = 'unavailable'
        if row['status'] == 'approved' and received:
            interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            reporting_status = ('report-overdue' if (_now() - _time(received)).total_seconds() > max(OVERDUE_SECONDS, interval * 3) else 'reporting')
        latest = json.loads(row['latest_record']) if row['latest_record'] else None
        # The covering index aggregates deltas without reading any JSON payload.
        aggregate = db.execute('''SELECT COUNT(*),SUM(awake_seconds),SUM(measured_seconds),
            SUM(imported_seconds) FROM records WHERE device_id=?''', (row['device_id'],)).fetchone()
        count = aggregate[0]
        usage = dict(zip(DELTA_FIELDS, aggregate[1:])) if count else None
        policy = json.loads(row['policy'])
        result = {'device_id': row['device_id'], 'identity': json.loads(row['identity']),
                  'status': row['status'], 'reporting_status': reporting_status,
                  'created_at': row['created_at'], 'last_received_at': received,
                  'last_observed_at': latest['observed_at'] if latest else None,
                  'policy_version': row['policy_version'], 'policy': policy,
                  'totals': latest['totals'] if latest else None, 'latest_seq': row['latest_seq'],
                  'retired_seq': row['retired_seq'], 'retained_before': row['retained_before'],
                  'record_count': count, 'coverage': 'unknown',
                  'retained_usage': usage}
        result['pending_requests'] = [
            {key: request[key] for key in ('request_id', 'status', 'created_at')}
            for request in db.execute("SELECT request_id,status,created_at FROM requests WHERE device_id=? AND status='pending' ORDER BY created_at,request_id", (row['device_id'],))]
        for category, permission in (('history', 'history'), ('readings', 'resources')):
            allowed = policy[permission]
            available = row[category] is not None if detail else row[category + '_available']
            result[category + '_available'] = bool(allowed and available)
            if detail:
                result[category] = json.loads(row[category]) if allowed and row[category] else None
            result[category + '_received_at'] = row[category + '_received_at'] if allowed else None
        if detail:
            records = db.execute('''SELECT payload,received_at FROM records
                WHERE device_id=? AND seq>? ORDER BY seq LIMIT ?''',
                                 (row['device_id'], after_seq, limit + 1)).fetchall()
            result['records'] = [dict(json.loads(record['payload']), received_at=record['received_at'])
                                 for record in records[:limit]]
            result['records_truncated'] = len(records) > limit
            result['records_next_seq'] = result['records'][-1]['seq'] if result['records_truncated'] else None
        return result

    def list_devices(self, limit=100, offset=0, search='', group='', status=''):
        """Return a stable bounded page, filtering before applying its offset."""
        _page(limit, offset)
        if any(not isinstance(value, str) or len(value) > 4096 for value in (search, group, status)):
            raise StoreError('Invalid device filter')
        predicates, parameters = [], []
        if search:
            predicates.append('instr(search_text,?)>0')
            parameters.append(search.casefold())
        if group:
            predicates.append('group_name=?')
            parameters.append(group.casefold())
        if status in ('pending', 'approved', 'denied', 'revoked'):
            predicates.append('status=?')
            parameters.append(status)
        elif status in ('reporting', 'report-overdue'):
            predicates.append("status='approved' AND last_received_at IS NOT NULL AND last_received_at "
                              + ('<' if status == 'report-overdue' else '>=') + ' ?')
            with self._lock:
                interval = self._db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            parameters.append(_stamp(_now() - dt.timedelta(seconds=max(OVERDUE_SECONDS, interval * 3))))
        elif status == 'unavailable':
            predicates.append("(status!='approved' OR last_received_at IS NULL)")
        elif status:
            raise StoreError('Invalid device status filter')
        where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
        with self._transaction() as db:
            rows = db.execute('''SELECT device_id,identity,status,policy_version,policy,created_at,
                last_received_at,latest_seq,latest_record,history_received_at,readings_received_at,
                retired_seq,retained_before,history IS NOT NULL AS history_available,
                readings IS NOT NULL AS readings_available FROM devices'''
                              + where + ' ORDER BY created_at,device_id LIMIT ? OFFSET ?',
                              (*parameters, limit, offset)).fetchall()
            return [self._public_device(db, row) for row in rows]

    def get_device(self, device_id, limit=100, after_seq=0):
        _page(limit, after_seq, maximum=2**53 - 1)
        with self._transaction() as db:
            return self._public_device(db, self._device_row(db, device_id), detail=True,
                                       limit=limit, after_seq=after_seq)

    def update_identity(self, device_id, identity):
        """Replace optional server-owned identity fields after protocol validation."""
        identity = validate_identity(identity)
        with self._transaction() as db:
            self._device_row(db, device_id)
            db.execute('UPDATE devices SET identity=?,search_text=?,group_name=? WHERE device_id=?',
                       (_json(identity), *_identity_columns(device_id, identity), device_id))
            return self._public_device(db, self._device_row(db, device_id), detail=True)

    def revoke(self, device_id):
        with self._transaction() as db:
            self._device_row(db, device_id)
            db.execute("UPDATE devices SET status='revoked',credential_hash=NULL WHERE device_id=?", (device_id,))
            db.execute("UPDATE requests SET status='revoked',poll_hash=NULL WHERE device_id=?", (device_id,))
            return self._public_device(db, self._device_row(db, device_id), detail=True)

    @staticmethod
    def _retained_history(value, before):
        result = dict(value)
        if before:
            for field in ('daily', 'imported_daily'):
                if field in result:
                    result[field] = {day: seconds for day, seconds in result[field].items() if day >= before[:10]}
            for field in ('first_seen', 'recording_started'):
                if result.get(field) and _time(result[field]) < _time(before):
                    result.pop(field)
        return result

    def retain(self, before):
        """Delete event times strictly before the boundary; keep a sequence floor.

        Boundaries move only forward. Still-retained out-of-order records below
        the floor remain immutable; unseen sequences at/below it are retired.
        The latest stock snapshot remains available with its original timestamp.
        """
        before = _stamp(_time(before))
        with self._transaction() as db:
            old = db.execute('SELECT retained_before FROM settings WHERE singleton=1').fetchone()[0]
            if old and before < old:
                raise StoreError('Retention boundary cannot move backwards')
            db.execute('UPDATE settings SET retained_before=? WHERE singleton=1', (before,))
            deleted = 0
            for row in db.execute('SELECT * FROM devices').fetchall():
                maximum = db.execute('SELECT MAX(seq) FROM records WHERE device_id=? AND observed_at<?', (row['device_id'], before)).fetchone()[0]
                deleted += db.execute('DELETE FROM records WHERE device_id=? AND observed_at<?', (row['device_id'], before)).rowcount
                db.execute('UPDATE devices SET retired_seq=?,retained_before=? WHERE device_id=?',
                           (max(row['retired_seq'], maximum or 0), before, row['device_id']))
                if row['history']:
                    db.execute('UPDATE devices SET history=? WHERE device_id=?',
                               (_json(self._retained_history(json.loads(row['history']), before)), row['device_id']))
                if row['readings'] and _time(json.loads(row['readings'])['observed_at']) < _time(before):
                    db.execute('UPDATE devices SET readings=NULL,readings_received_at=NULL WHERE device_id=?', (row['device_id'],))
            return {'deleted': deleted}

    def _different_path(self, path):
        path = Path(path).resolve()
        if (path == self.path or str(path) in (str(self.path) + '-wal', str(self.path) + '-shm')
                or (path.exists() and os.path.samefile(path, self.path))):
            raise StoreError('Backup path must differ from the live database')
        return path

    def backup(self, path):
        """Write a consistent SQLite snapshot with atomic destination replacement."""
        path = self._different_path(path)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        os.close(descriptor)
        try:
            destination = sqlite3.connect(temporary, isolation_level=None)
            try:
                with self._lock:
                    self._db.backup(destination)
                self._validate_backup(destination)
            finally:
                destination.close()
            with open(temporary, 'r+b') as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'path': str(path), 'schema_version': SCHEMA_VERSION}

    @staticmethod
    def _schema(db):
        return sorted(tuple(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))

    @classmethod
    def _validate_backup(cls, db):
        """Reject foreign schemas, corruption and invalid protocol data before writes."""
        if (db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION
                or db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or db.execute('PRAGMA foreign_key_check').fetchone()):
            raise StoreError('Invalid or unsupported backup')
        expected = sqlite3.connect(':memory:', isolation_level=None)
        try:
            _migrate(expected)
            if cls._schema(db) != cls._schema(expected):
                raise StoreError('Unexpected backup schema')
        finally:
            expected.close()
        db.row_factory = sqlite3.Row
        options = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()
        if not options or type(options[0]) is not int or not 5 <= options[0] <= 3600:
            raise StoreError('Invalid reporting interval')
        if db.execute('SELECT count(*) FROM settings WHERE singleton=1').fetchone()[0] != 1:
            raise StoreError('Missing retention metadata')
        boundary = db.execute('SELECT retained_before FROM settings').fetchone()[0]
        if boundary:
            _time(boundary)
        for row in db.execute('SELECT * FROM devices'):
            if row['refresh_requested'] not in (0, 1):
                raise StoreError('Invalid refresh hint')
            _identifier(row['device_id'])
            identity = validate_identity(json.loads(row['identity']))
            if (row['search_text'], row['group_name']) != _identity_columns(row['device_id'], identity):
                raise StoreError('Invalid identity search metadata')
            policy = _policy(json.loads(row['policy']))
            if (type(row['policy_version']) is not int or not 1 <= row['policy_version'] <= 2**53-1
                    or type(row['retired_seq']) is not int or row['retired_seq'] < 0
                    or type(row['latest_seq']) is not int or row['latest_seq'] < 0
                    or row['retained_before'] != boundary):
                raise StoreError('Invalid device metadata')
            _time(row['created_at'])
            if row['last_received_at']:
                _time(row['last_received_at'])
            base = {'version': 1, 'device_id': row['device_id'],
                    'policy_version': row['policy_version'], 'policy': policy, 'records': []}
            if row['latest_record']:
                latest = json.loads(row['latest_record'])
                validate_sync(dict(base, records=[latest]))
                if latest['seq'] != row['latest_seq']:
                    raise StoreError('Invalid latest snapshot')
                stored = db.execute('SELECT payload FROM records WHERE device_id=? AND seq=?', (row['device_id'], row['latest_seq'])).fetchone()
                if stored and _json(json.loads(stored['payload'])) != _json(latest):
                    raise StoreError('Latest snapshot conflicts with immutable record')
            elif row['latest_seq']:
                raise StoreError('Missing latest snapshot')
            maximum = db.execute('SELECT MAX(seq) FROM records WHERE device_id=?', (row['device_id'],)).fetchone()[0]
            if maximum and maximum > row['latest_seq']:
                raise StoreError('Latest snapshot precedes stored records')
            for category, permission in (('history', 'history'), ('readings', 'resources')):
                if row[category]:
                    if not policy[permission] or not row[category + '_received_at']:
                        raise StoreError('Invalid optional data policy')
                    _time(row[category + '_received_at'])
                    validate_sync(dict(base, **{category: json.loads(row[category])}))
        for row in db.execute('SELECT * FROM records'):
            payload = json.loads(row['payload'])
            validate_sync({'version': 1, 'device_id': row['device_id'], 'policy_version': 1,
                           'policy': {'history': False, 'resources': False}, 'records': [payload]})
            if (payload['seq'] != row['seq'] or not hmac.compare_digest(row['digest'], _hash(_json(payload)))
                    or row['observed_at'] != _stamp(_time(payload['observed_at']))
                    or any(row[field] != payload[field] for field in DELTA_FIELDS)):
                raise StoreError('Invalid immutable record digest')
            _time(row['received_at'])
        for row in db.execute('SELECT * FROM requests'):
            _identifier(row['request_id'])
            _policy(json.loads(row['policy']))
            _time(row['created_at'])
        for row in db.execute('SELECT * FROM codes'):
            _time(row['expires_at'])
            if not 0 <= row['remaining'] <= MAX_CODE_USES:
                raise StoreError('Invalid enrollment code uses')
        for row in db.execute('SELECT * FROM throttle'):
            _time(row['window_start'])
        for table, columns in (('devices', ('credential_hash',)), ('codes', ('code_hash',)),
                               ('requests', ('poll_hash', 'request_digest')), ('records', ('digest',))):
            for row in db.execute('SELECT * FROM ' + table):
                for column in columns:
                    value = row[column]
                    if value is not None and (not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)):
                        raise StoreError('Invalid stored secret or digest')

    def restore(self, path):
        """Validate a read-only source, then replace rows in one live transaction.

        Every device credential, code and poll token is invalidated. Device IDs,
        server metadata, history and sequence floors survive; re-pairing is
        required. Other open Store instances see the new state on their next
        transaction, with no file replacement or disconnected WAL handles.
        """
        path = self._different_path(path)
        snapshot = sqlite3.connect(':memory:', isolation_level=None)
        try:
            try:
                source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, isolation_level=None)
                try:
                    source.execute('PRAGMA trusted_schema=OFF')
                    source.backup(snapshot)
                finally:
                    source.close()
                snapshot.execute('PRAGMA trusted_schema=OFF')
                self._validate_backup(snapshot)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
                raise StoreError('Invalid or unsupported backup') from error
            snapshot.execute("UPDATE devices SET credential_hash=NULL,status='revoked'")
            snapshot.execute("UPDATE requests SET poll_hash=NULL,status='revoked'")
            snapshot.execute('DELETE FROM codes')
            snapshot.execute('DELETE FROM throttle')
            tables = ('devices', 'codes', 'requests', 'records', 'throttle', 'settings')
            with self._transaction() as db:
                for table in reversed(tables):
                    db.execute('DELETE FROM ' + table)
                for table in tables:
                    rows = snapshot.execute('SELECT * FROM ' + table).fetchall()
                    if rows:
                        placeholders = ','.join('?' for _ in rows[0])
                        db.executemany('INSERT INTO ' + table + ' VALUES (' + placeholders + ')', [tuple(row) for row in rows])
            return {'path': str(path), 'schema_version': SCHEMA_VERSION}
        finally:
            snapshot.close()

    def close(self):
        with self._lock:
            self._db.close()

    def get_configuration(self):
        with self._transaction() as db:
            row = db.execute('SELECT reporting_interval,retained_before FROM settings WHERE singleton=1').fetchone()
            return dict(reporting_interval=row[0], retained_before=row[1])

    def get_overview(self):
        """Global counters, independent of a paged/filterable device table."""
        with self._transaction() as db:
            interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            counts = dict.fromkeys(('total', 'reporting', 'report-overdue', 'pending', 'approved', 'revoked', 'denied', 'unavailable'), 0)
            totals = dict.fromkeys(('measured_seconds', 'imported_seconds', 'baseline_seconds'), 0)
            known = 0
            for row in db.execute('SELECT status,last_received_at,latest_record FROM devices'):
                counts['total'] += 1
                counts[row['status']] += 1
                if row['status'] == 'approved' and row['last_received_at']:
                    elapsed = (_now() - _time(row['last_received_at'])).total_seconds()
                    counts['report-overdue' if elapsed > max(OVERDUE_SECONDS, interval * 3) else 'reporting'] += 1
                else:
                    counts['unavailable'] += 1
                if row['latest_record']:
                    known += 1
                    snapshot = json.loads(row['latest_record'])['totals']
                    for key in totals:
                        totals[key] += snapshot[key]
            events = [dict(device_id=row[0], kind='report-received', time=row[1]) for row in db.execute(
                'SELECT device_id,last_received_at FROM devices WHERE last_received_at IS NOT NULL ORDER BY last_received_at DESC LIMIT 20')]
            events += [dict(device_id=row[0], kind='enrollment-pending', time=row[1]) for row in db.execute(
                "SELECT device_id,created_at FROM requests WHERE status='pending' ORDER BY created_at DESC LIMIT 20")]
            events.sort(key=lambda event: event['time'], reverse=True)
            return dict(counts=counts, totals=totals if known else None,
                        known_devices=known, unknown_devices=counts['total'] - known,
                        recent_events=events[:20], server_time=_stamp(_now()),
                        schema_version=SCHEMA_VERSION, health='available')

    def update_configuration(self, reporting_interval):
        if type(reporting_interval) is not int or not 5 <= reporting_interval <= 3600:
            raise StoreError('Reporting interval must be 5 to 3600 seconds')
        with self._transaction() as db:
            db.execute('UPDATE settings SET reporting_interval=? WHERE singleton=1', (reporting_interval,))
            boundary = db.execute('SELECT retained_before FROM settings WHERE singleton=1').fetchone()[0]
            return dict(reporting_interval=reporting_interval, retained_before=boundary)

    def request_refresh(self, device_id):
        with self._transaction() as db:
            row = self._device_row(db, device_id)
            if row['status'] != 'approved':
                raise StoreError('Only approved devices can receive a refresh hint')
            db.execute('UPDATE devices SET refresh_requested=1 WHERE device_id=?', (device_id,))
            return dict(device_id=device_id, refresh_requested=True)
