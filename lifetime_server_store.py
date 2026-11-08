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

from lifetime_protocol import ProtocolError, number, validate_identity, validate_sync, validate_performance

SCHEMA_VERSION = 7
APPLICATION_ID = 0x4C544D45
MAX_BYTES = 1024 * 1024
MAX_CODES = 1000
MAX_PENDING = 1000
MAX_CODE_TTL = 86400
MAX_CODE_USES = 100
GUESS_LIMIT = 10
GUESS_WINDOW = 60
OVERDUE_SECONDS = 600
LIVE_SILENCE_SECONDS = 30   # a computer that has been reporting live and goes quiet is shown offline this fast
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
    if (not isinstance(value, dict) or not {'history', 'resources'} <= set(value) or set(value) - {'history', 'resources', 'health', 'control'}
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


def _disk_percent(specs, performance):
    disks = specs.get('disks', [])
    # Collector orders the system disk first; never infer it from another drive.
    total = disks[0].get('total_bytes') if disks else None
    free = performance.get('disk_free_bytes')
    return min(100, 100*free/total) if total and free is not None else None


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
        if version < 5:
            db.execute("ALTER TABLE settings ADD COLUMN organisation TEXT NOT NULL DEFAULT ''")
            db.execute('''CREATE TABLE invitations (invitation_id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL, secret_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
                remaining INTEGER NOT NULL CHECK(remaining >= 0), failures INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)), created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE guess_ips (ip_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL,
                next_attempt TEXT NOT NULL, last_seen TEXT NOT NULL)''')
            db.execute('''CREATE TABLE events (event_id INTEGER PRIMARY KEY, device_id TEXT,
                kind TEXT NOT NULL, time TEXT NOT NULL)''')
            db.execute('ALTER TABLE devices ADD COLUMN client_version TEXT')
            db.execute('PRAGMA user_version=5')
        if version < 6:
            db.execute('ALTER TABLE devices ADD COLUMN health TEXT')
            db.execute('ALTER TABLE devices ADD COLUMN health_received_at TEXT')
            db.execute('PRAGMA user_version=6')
        if version < 7:
            db.execute('''CREATE TABLE IF NOT EXISTS performance_samples (
                device_id TEXT NOT NULL REFERENCES devices(device_id), observed_at TEXT NOT NULL,
                cpu_percent REAL, ram_percent REAL, disk_free_percent REAL, battery_percent REAL,
                power_watts REAL, temperature_c REAL, PRIMARY KEY(device_id,observed_at))''')
            db.execute('CREATE INDEX IF NOT EXISTS performance_device_time ON performance_samples(device_id,observed_at)')
            db.execute('''CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
                action TEXT NOT NULL CHECK(action IN ('sleep','restart','shutdown','refresh')),
                status TEXT NOT NULL CHECK(status IN ('pending','delivered','done','failed','cancelled','expired')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL)''')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS commands_active ON commands(device_id) WHERE status IN ('pending','delivered')")
            db.execute('PRAGMA user_version=7')
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
        from collections import defaultdict, deque
        self._live_samples = defaultdict(lambda: deque(maxlen=300))
        self._live_credentials = {}
        self._live_demand = {}
        self._ws_down = {}      # device_id -> True while its live socket is closed; cleared when the computer speaks again
        self.hooks = {}         # clear / nudge / command, set by the live hub
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

    def _clear_live(self, device_id):
        self._live_credentials.pop(device_id, None)
        self._live_samples.pop(device_id, None)
        self._live_demand.pop(device_id, None)
        self._ws_down.pop(device_id, None)
        self._hook('clear', device_id)

    def _hook(self, name, *arguments):
        callback = self.hooks.get(name)
        if callback:
            try:
                callback(*arguments)
            except Exception:
                pass

    def _forced_offline(self, device_id):
        return device_id in self._ws_down

    def note_live_link(self, device_id, up):
        with self._lock:
            if up:
                self._ws_down.pop(device_id, None)
            else:
                self._ws_down[device_id] = True

    def live_rate_ms(self, device_id):
        """How often this computer should send: fast for the one being watched, quiet when nobody looks."""
        import time
        with self._lock:
            now = time.monotonic()
            self._live_demand = {k: expiry for k, expiry in self._live_demand.items() if expiry > now}
            return 500 if device_id in self._live_demand else 2000 if self._live_demand else 10000

    def last_live_time(self, device_id):
        with self._lock:
            samples = self._live_samples.get(device_id)
            return samples[-1]['observed_at'] if samples else _stamp(_now())

    def watch(self, device_id=None):
        """A viewer is looking at the fleet (and one computer): raise the send rate at once."""
        import time
        with self._lock:
            now = time.monotonic()
            changed = device_id not in self._live_demand or None not in self._live_demand
            self._live_demand[None] = now+20
            if device_id is not None:
                self._live_demand[device_id] = now+20
        if changed:
            self._hook('nudge')

    def authenticate_live(self, device_id, credential):
        """The same checks as a live POST, without a sample: used to accept or refuse a socket."""
        import time
        digest = _hash(_secret(credential))
        with self._lock:
            now = time.monotonic()
            cached = self._live_credentials.get(device_id)
            if not cached or now >= cached[1]:
                row = self._db.execute('SELECT status,credential_hash,policy FROM devices WHERE device_id=?', (device_id,)).fetchone()
                if not row or row['status'] != 'approved' or not json.loads(row['policy']).get('resources'):
                    self._clear_live(device_id)
                    raise AuthenticationError('Live reporting is not permitted')
                cached = (row['credential_hash'], now+5)
            if not cached[0] or not hmac.compare_digest(cached[0], digest):
                raise AuthenticationError('Invalid device credential')
            self._live_credentials[device_id] = cached

    def _last_seen(self, row):
        samples = self._live_samples.get(row['device_id'])
        if row['status'] == 'approved' and samples and (_now()-_time(samples[-1]['observed_at'])).total_seconds() < 20:
            return samples[-1]['observed_at']
        return row['last_received_at']

    def ingest_live(self, device_id, credential, message, min_gap=.5):
        """Only SELECT on cache miss. Never enters a SQLite transaction or outbox."""
        import time
        from lifetime_live import validate_live
        from lifetime_protocol import decode, canonical
        message = validate_live(decode(canonical(message)))
        if message['device_id'] != device_id:
            raise AuthenticationError('Device identity mismatch')
        with self._lock:
            now = time.monotonic()
            self.authenticate_live(device_id, credential)
            samples = self._live_samples[device_id]
            if samples and (_now()-_time(samples[-1]['observed_at'])).total_seconds() < min_gap:
                raise ThrottledError('Live rate exceeded')
            self._ws_down.pop(device_id, None)
            samples.append(dict(message['sample'], observed_at=_stamp(_now())))
            self._live_demand = {k: expiry for k, expiry in self._live_demand.items() if expiry > now}
            interval = 1 if device_id in self._live_demand else 5 if self._live_demand else 10
            pending = self._db.execute("SELECT 1 FROM commands WHERE device_id=? AND status IN ('pending','delivered') AND created_at>?", (device_id, _stamp(_now()-dt.timedelta(minutes=10)))).fetchone()
            return dict(interval=interval, sync_now=bool(pending))

    def live(self, device_id=None):
        import time
        if device_id is not None:
            _identifier(device_id)
        with self._lock:
            now = time.monotonic()
            self._live_demand = {k: expiry for k, expiry in self._live_demand.items() if expiry > now}
            rows = self._db.execute("SELECT device_id,policy FROM devices WHERE status='approved'").fetchall()
            approved = {r['device_id']: json.loads(r['policy']).get('resources', False) for r in rows}
            if device_id is not None and device_id not in approved:
                raise StoreError('Unknown approved device')
            changed = device_id not in self._live_demand or None not in self._live_demand
            self._live_demand[None] = now+20
            if device_id is not None:
                self._live_demand[device_id] = now+20
            if changed:
                self._hook('nudge')
            latest = []
            for identifier, permitted in approved.items():
                samples = self._live_samples.get(identifier) if permitted else None
                sample = dict(samples[-1]) if samples else None
                age = max(0, (_now()-_time(sample['observed_at'])).total_seconds()) if sample else None
                latest.append(dict(device_id=identifier, sample=sample, age_seconds=age))
            ring = [dict(s) for s in self._live_samples.get(device_id, ())] if approved.get(device_id) else []
            return dict(devices=latest, ring=ring, device_id=device_id)

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

    @staticmethod
    def _event(db, device_id, kind):
        db.execute('INSERT INTO events(device_id,kind,time) VALUES (?,?,?)', (device_id, kind, _stamp(_now())))
        db.execute('DELETE FROM events WHERE event_id <= (SELECT MAX(event_id)-1000 FROM events)')

    def create_invitation(self, fingerprint, ttl=86400, uses=1):
        from lifetime_connection import issue, parts
        if (type(ttl) is not int or not 1 <= ttl <= MAX_CODE_TTL
                or type(uses) is not int or not 1 <= uses <= MAX_CODE_USES):
            raise StoreError('Invalid invitation lifetime or uses')
        expires = _stamp(_now() + dt.timedelta(seconds=ttl))
        with self._transaction() as db:
            db.execute('DELETE FROM invitations WHERE expires_at<=?', (_stamp(_now()),))
            if db.execute('SELECT COUNT(*) FROM invitations').fetchone()[0] >= MAX_CODES:
                raise StoreError('Too many invitations')
            for _ in range(10):
                code = issue(fingerprint)
                invitation_id, secret, _ = parts(code)
                if not db.execute('SELECT 1 FROM invitations WHERE invitation_id=?', (invitation_id,)).fetchone():
                    break
            else:
                raise StoreError('Invitation could not be created')
            db.execute('INSERT INTO invitations VALUES (?,?,?,?,?,0,0,?)',
                       (invitation_id, _hash(code), _hash(secret), expires, uses, _stamp(_now())))
        return dict(invitation_id=invitation_id, code=code, expires_at=expires, uses=uses)

    def pending_enrollments(self, limit=100, offset=0):
        _page(limit, offset)
        with self._transaction() as db:
            return [dict(request_id=row['request_id'], device_id=row['device_id'], created_at=row['created_at'],
                         name=json.loads(row['identity']).get('display_name') or row['device_id'])
                    for row in db.execute('''SELECT requests.request_id,requests.device_id,requests.created_at,devices.identity
                        FROM requests JOIN devices USING(device_id) WHERE requests.status='pending'
                        ORDER BY requests.created_at,requests.request_id LIMIT ? OFFSET ?''', (limit, offset))]

    def organisation(self, name=None):
        if name is not None and (not isinstance(name, str) or len(name) > 120 or any(ord(c) < 32 for c in name)):
            raise StoreError('Invalid organisation name')
        with self._transaction() as db:
            if name is not None:
                db.execute('UPDATE settings SET organisation=? WHERE singleton=1', (name.strip(),))
            return dict(organisation=db.execute('SELECT organisation FROM settings WHERE singleton=1').fetchone()[0])

    def invitations(self, limit=100, offset=0):
        _page(limit, offset)
        with self._transaction() as db:
            return [dict(row) for row in db.execute('''SELECT invitation_id,expires_at,remaining,
                failures,revoked,created_at FROM invitations ORDER BY created_at DESC LIMIT ? OFFSET ?''', (limit, offset))]

    def revoke_invitation(self, invitation_id):
        if not isinstance(invitation_id, str) or len(invitation_id) != 5:
            raise StoreError('Invalid invitation ID')
        with self._transaction() as db:
            db.execute('UPDATE invitations SET revoked=1 WHERE invitation_id=?', (invitation_id,))
        return dict(invitation_id=invitation_id, revoked=True)

    def enroll(self, request, peer_ip=None):
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
        invitation = None
        if code.upper().startswith('LTS'):
            from lifetime_connection import normalize, parts
            try:
                code = normalize(code)
                invitation = parts(code)
            except ValueError:
                # Bad checksums are still guesses and consume the global budget.
                pass
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
                # Legacy bundle codes retain their existing global budget.
                ip_hash = _hash(peer_ip) if peer_ip is not None and code.upper().startswith('LTS') else None
                ip_row = db.execute('SELECT * FROM guess_ips WHERE ip_hash=?', (ip_hash,)).fetchone()
                if ip_row and _time(ip_row['next_attempt']) > now:
                    failure = ThrottledError('Enrollment temporarily throttled')
                invited = db.execute('SELECT * FROM invitations WHERE invitation_id=?',
                                     (invitation[0],)).fetchone() if invitation else None
                found = db.execute('SELECT * FROM codes WHERE code_hash=?', (_hash(code),)).fetchone()
                if invited:
                    matches = (hmac.compare_digest(invited['secret_hash'], _hash(invitation[1]))
                               & hmac.compare_digest(invited['code_hash'], _hash(code)))
                    found = invited if matches and not invited['revoked'] and invited['failures'] < 5 else None
                if failure:
                    pass
                elif not found or found['remaining'] < 1 or _time(found['expires_at']) <= now:
                    db.execute('INSERT OR REPLACE INTO throttle VALUES (1,?,?)', (start, failures + 1))
                    if invited:
                        db.execute('UPDATE invitations SET failures=MIN(failures+1,5) WHERE invitation_id=?', (invitation[0],))
                    if ip_hash:
                        count = min(16, (ip_row['failures'] if ip_row else 0) + 1)
                        if ip_row and (now - _time(ip_row['last_seen'])).total_seconds() > 3600:
                            count = 1
                        delay = 0 if count < 3 else min(300, 2**(count - 3))
                        db.execute('INSERT OR REPLACE INTO guess_ips VALUES (?,?,?,?)',
                                   (ip_hash, count, _stamp(now + dt.timedelta(seconds=delay)), _stamp(now)))
                        db.execute('DELETE FROM guess_ips WHERE ip_hash IN (SELECT ip_hash FROM guess_ips ORDER BY last_seen DESC LIMIT -1 OFFSET 4096)')
                    failure = AuthenticationError('Invalid or expired enrollment code')
                else:
                    if db.execute("SELECT count(*) FROM requests WHERE status='pending'").fetchone()[0] >= MAX_PENDING:
                        raise StoreError('Too many pending enrollments')
                    if invited:
                        db.execute('UPDATE invitations SET remaining=remaining-1 WHERE invitation_id=?', (invitation[0],))
                        self._event(db, device_id, 'invitation-used')
                    else:
                        db.execute('UPDATE codes SET remaining=remaining-1 WHERE code_hash=?', (_hash(code),))
                    db.execute('''INSERT OR IGNORE INTO devices
                        (device_id,identity,status,policy_version,policy,created_at,search_text,group_name)
                        VALUES (?,?,'pending',1,?,?,?,?)''',
                               (device_id, _json(identity), _json(policy), _stamp(now), *_identity_columns(device_id, identity)))
                    db.execute('UPDATE devices SET retained_before=(SELECT retained_before FROM settings WHERE singleton=1) WHERE device_id=?', (device_id,))
                    db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)',
                               (request_id, device_id, digest, _hash(token), _json(policy), 'pending', _stamp(now)))
                    self._event(db, device_id, 'enrollment-pending')
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
            db.execute("UPDATE devices SET status='approved',credential_hash=NULL,policy=?,history=NULL,history_received_at=NULL,readings=NULL,readings_received_at=NULL,health=NULL,health_received_at=NULL WHERE device_id=?",
                       (row['policy'], row['device_id']))
            db.execute('DELETE FROM performance_samples WHERE device_id=?', (row['device_id'],))
            self._cancel_device_commands(db, row['device_id'])
            self._clear_live(row['device_id'])
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
            for category, permission in (('history', 'history'), ('readings', 'resources'), ('health', 'health')):
                if not current_policy.get(permission, False):
                    db.execute(f'UPDATE devices SET {category}=NULL,{category}_received_at=NULL WHERE device_id=?', (device_id,))
            if not current_policy.get('resources'):
                self._clear_live(device_id)
                db.execute('DELETE FROM performance_samples WHERE device_id=?', (device_id,))
            self._live_credentials.pop(device_id, None)
            self._ws_down.pop(device_id, None)
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
                for category, permission in (('history', 'history'), ('readings', 'resources'), ('health', 'health')):
                    if category in message and current_policy.get(permission, False) and message['policy'].get(permission, False):
                        value = message[category]
                        if category == 'history':
                            value = self._retained_history(value, row['retained_before'])
                        elif row['retained_before'] and _stamp(_time(value['observed_at'])) < row['retained_before']:
                            continue
                        elif row[category] and _time(value['observed_at']) < _time(json.loads(row[category])['observed_at']):
                            continue
                        if category == 'readings':
                            if 'specs' not in value and row['readings']:
                                old_specs = json.loads(row['readings']).get('specs')
                                if old_specs is not None:
                                    value = dict(value, specs=old_specs)
                            self._performance_insert(db, device_id, value)
                        db.execute(f'UPDATE devices SET {category}=?,{category}_received_at=? WHERE device_id=?',
                                   (_json(value), received, device_id))
            db.execute('UPDATE devices SET retired_seq=?,last_received_at=? WHERE device_id=?', (retired_seq, received, device_id))
            if 'client_version' in message:
                db.execute('UPDATE devices SET client_version=? WHERE device_id=?', (message['client_version'], device_id))
            db.execute("UPDATE requests SET poll_hash=NULL,status='completed' WHERE device_id=? AND status='approved'", (device_id,))
            interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            refresh = bool(row['refresh_requested'])
            db.execute('UPDATE devices SET refresh_requested=0 WHERE device_id=?', (device_id,))
            command = self._sync_command(db, device_id, current_policy, message.get('command_result'))
            command_ack = None
            if message.get('command_result'):
                ack = db.execute('SELECT command_id,status FROM commands WHERE command_id=? AND device_id=?', (message['command_result']['id'], device_id)).fetchone()
                if ack:
                    command_ack = dict(id=ack['command_id'], status=ack['status'])
            return {**({'command_ack': command_ack} if command_ack else {}), **({'command': command} if command else {}), 'device_id': device_id, 'accepted': accepted, 'duplicates': duplicates,
                    'retired': retired, 'policy_version': version, 'received_at': received,
                    'reporting_interval': interval, 'refresh': refresh}

    @staticmethod
    def _device_row(db, device_id):
        row = db.execute('SELECT * FROM devices WHERE device_id=?', (_identifier(device_id),)).fetchone()
        if not row:
            raise StoreError('Unknown device')
        return row

    def _public_device(self, db, row, detail=False, limit=100, after_seq=0, hours=24):
        received = self._last_seen(row)
        samples = self._live_samples.get(row['device_id'])
        interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
        threshold = max(OVERDUE_SECONDS, interval * 3)
        if samples:     # this computer reports live, so silence is noticed in seconds, not minutes
            threshold = LIVE_SILENCE_SECONDS
        offline = (_now()-_time(received or row['created_at'])).total_seconds() > threshold or self._forced_offline(row['device_id'])
        reporting_status = 'unavailable'
        if row['status'] == 'approved' and received:
            reporting_status = 'report-overdue' if offline else 'reporting'
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
        result['client_version'] = row['client_version']
        result['health'] = json.loads(row['health']) if row['health'] and policy.get('health') else None
        result['unstable'] = bool(result['health'] and (result['health'].get('crashes_30d') or 0) >= 3)
        readings = json.loads(row['readings']) if row['readings'] and policy.get('resources') else {}
        specs, perf = readings.get('specs', {}), readings.get('performance', {})
        if samples and policy.get('resources'):
            perf = dict(perf, **{k: v for k, v in samples[-1].items() if k != 'observed_at'})
        disk_percent = _disk_percent(specs, perf)
        result['summary'] = dict(cpu_name=specs.get('cpu_name'), ram_bytes=specs.get('ram_bytes'),
                                 disk_free_percent=disk_percent, battery_health_percent=perf.get('battery_health_percent'))
        result['attention'] = []
        for code, active in (('offline', row['status'] == 'approved' and offline),
                             ('disk_low', disk_percent is not None and disk_percent < 10),
                             ('battery_worn', perf.get('battery_health_percent') is not None and perf['battery_health_percent'] < 70),
                             ('hot', perf.get('temperature_c') is not None and perf['temperature_c'] >= 90),
                             ('unstable', result['unstable'])):
            if active:
                result['attention'].append(code)
        Store._expire_commands(db)
        if db.execute("SELECT 1 FROM commands WHERE device_id=? AND status IN ('pending','delivered')", (row['device_id'],)).fetchone():
            result['attention'].append('pending_command')
        if detail:
            result['specs'], result['performance'] = specs or None, perf or None
            result['performance_history'] = [dict(r) for r in db.execute(
                'SELECT observed_at,cpu_percent,ram_percent,disk_free_percent,battery_percent,power_watts,temperature_c FROM performance_samples WHERE device_id=? AND observed_at>=? ORDER BY observed_at',
                (row['device_id'], _stamp(_now()-dt.timedelta(hours=hours))))] if policy.get('resources') else []
            result['commands'] = [dict(r) for r in db.execute('SELECT * FROM commands WHERE device_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20', (row['device_id'],))]
            history = json.loads(row['history']) if row['history'] and policy['history'] else {}
            result['today'] = history.get('daily', {}).get(_now().astimezone().date().isoformat())
        else:
            result['today'] = row['today'] if policy['history'] else None
        result['pending_requests'] = [
            {key: request[key] for key in ('request_id', 'status', 'created_at')}
            for request in db.execute("SELECT request_id,status,created_at FROM requests WHERE device_id=? AND status='pending' ORDER BY created_at,request_id", (row['device_id'],))]
        for category, permission in (('history', 'history'), ('readings', 'resources'), ('health', 'health')):
            allowed = policy.get(permission, False)
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

    def list_devices(self, limit=100, offset=0, search='', group='', status='', sort=None, descending=False):
        """Return a stable bounded page, filtering before applying its offset."""
        _page(limit, offset)
        if any(not isinstance(value, str) or len(value) > 4096 for value in (search, group, status)):
            raise StoreError('Invalid device filter')
        predicates, parameters = [], []
        with self._lock:
            live_ids = _json([key for key, samples in self._live_samples.items()
                              if samples and (_now()-_time(samples[-1]['observed_at'])).total_seconds() < 20])
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
            if status == 'reporting':
                predicates.append("status='approved' AND ((last_received_at IS NOT NULL AND last_received_at>=?) OR device_id IN (SELECT value FROM json_each(?)))")
            else:
                predicates.append("status='approved' AND last_received_at IS NOT NULL AND last_received_at<? AND device_id NOT IN (SELECT value FROM json_each(?))")
            with self._lock:
                interval = self._db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            parameters.append(_stamp(_now() - dt.timedelta(seconds=max(OVERDUE_SECONDS, interval * 3))))
            parameters.append(live_ids)
        elif status == 'unavailable':
            predicates.append("(status!='approved' OR (last_received_at IS NULL AND device_id NOT IN (SELECT value FROM json_each(?))))")
            parameters.append(live_ids)
        elif status:
            raise StoreError('Invalid device status filter')
        where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
        orders = dict(name="json_extract(identity,'$.display_name') COLLATE NOCASE",
                      group="group_name", status="status", today="today", last="last_received_at")
        if sort is not None and sort not in orders or type(descending) is not bool:
            raise StoreError('Invalid sort')
        order = orders[sort] + (' DESC' if descending else ' ASC') + ',device_id' if sort else 'created_at,device_id'
        day = _now().astimezone().date().isoformat()
        with self._transaction() as db:
            rows = db.execute('''SELECT device_id,identity,status,policy_version,policy,created_at,
                last_received_at,latest_seq,latest_record,history_received_at,readings_received_at,
                retired_seq,retained_before,history IS NOT NULL AS history_available,
                readings,readings IS NOT NULL AS readings_available,health,health_received_at,health IS NOT NULL AS health_available,client_version,
                json_extract(history,?) AS today FROM devices'''
                              + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                              (f'$.daily."{day}"', *parameters, limit, offset)).fetchall()
            return [self._public_device(db, row) for row in rows]

    def get_device(self, device_id, limit=100, after_seq=0, hours=24):
        if type(hours) is not int or not 1 <= hours <= 168:
            raise StoreError('Invalid performance history hours')
        _page(limit, after_seq, maximum=2**53 - 1)
        with self._transaction() as db:
            return self._public_device(db, self._device_row(db, device_id), detail=True,
                                       limit=limit, after_seq=after_seq, hours=hours)

    def update_identity(self, device_id, identity):
        """Replace optional server-owned identity fields after protocol validation."""
        identity = validate_identity(identity)
        with self._transaction() as db:
            self._device_row(db, device_id)
            db.execute('UPDATE devices SET identity=?,search_text=?,group_name=? WHERE device_id=?',
                       (_json(identity), *_identity_columns(device_id, identity), device_id))
            return self._public_device(db, self._device_row(db, device_id), detail=True)

    def leave(self, device_id, credential):
        """The computer's own Disconnect: same effect as an administrator revoking it, proven by its credential."""
        device_id = _identifier(device_id)
        credential_hash = _hash(_secret(credential))
        row = self._db.execute('SELECT status,credential_hash FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if (not row or row['status'] != 'approved' or not row['credential_hash']
                or not hmac.compare_digest(row['credential_hash'], credential_hash)):
            raise AuthenticationError('Invalid device credential')
        self.revoke(device_id)
        return dict(device_id=device_id, status='revoked')

    def delete_device(self, device_id):
        """Remove a computer and everything stored about it. Its credential stops working at once."""
        device_id = _identifier(device_id)
        with self._transaction() as db:
            self._clear_live(device_id)
            self._device_row(db, device_id)
            for table in ('performance_samples', 'commands', 'records', 'requests', 'events'):
                db.execute(f'DELETE FROM {table} WHERE device_id=?', (device_id,))  # nosec B608: table is from the fixed tuple above
            db.execute('DELETE FROM devices WHERE device_id=?', (device_id,))
        return dict(device_id=device_id, deleted=True)

    def revoke(self, device_id):
        with self._transaction() as db:
            self._clear_live(device_id)
            self._device_row(db, device_id)
            self._cancel_device_commands(db, device_id)
            db.execute("UPDATE devices SET status='revoked',credential_hash=NULL WHERE device_id=?", (device_id,))
            db.execute("UPDATE requests SET status='revoked',poll_hash=NULL WHERE device_id=?", (device_id,))
            self._event(db, device_id, 'revoked')
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
            db.execute('DELETE FROM performance_samples WHERE observed_at<?', (before,))
            deleted = 0
            for row in db.execute('SELECT * FROM devices').fetchall():
                maximum = db.execute('SELECT MAX(seq) FROM records WHERE device_id=? AND observed_at<?', (row['device_id'], before)).fetchone()[0]
                deleted += db.execute('DELETE FROM records WHERE device_id=? AND observed_at<?', (row['device_id'], before)).rowcount
                db.execute('UPDATE devices SET retired_seq=?,retained_before=? WHERE device_id=?',
                           (max(row['retired_seq'], maximum or 0), before, row['device_id']))
                if row['history']:
                    db.execute('UPDATE devices SET history=? WHERE device_id=?',
                               (_json(self._retained_history(json.loads(row['history']), before)), row['device_id']))
                for category in ('readings', 'health'):
                    if row[category] and _time(json.loads(row[category])['observed_at']) < _time(before):
                        db.execute(f'UPDATE devices SET {category}=NULL,{category}_received_at=NULL WHERE device_id=?', (row['device_id'],))
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
        organisation = db.execute('SELECT organisation FROM settings WHERE singleton=1').fetchone()[0]
        if not isinstance(organisation, str) or len(organisation) > 120 or any(ord(c) < 32 for c in organisation):
            raise StoreError('Invalid organisation')
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
            for category, permission in (('history', 'history'), ('readings', 'resources'), ('health', 'health')):
                if row[category]:
                    if not policy.get(permission, False) or not row[category + '_received_at']:
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
        for row in db.execute('SELECT * FROM invitations'):
            from lifetime_connection import ALPHABET
            if (len(row['invitation_id']) != 5 or any(c not in ALPHABET for c in row['invitation_id'])
                    or type(row['remaining']) is not int or not 0 <= row['remaining'] <= MAX_CODE_USES
                    or type(row['failures']) is not int or not 0 <= row['failures'] <= 5
                    or row['revoked'] not in (0, 1)):
                raise StoreError('Invalid invitation')
            _time(row['expires_at'])
            _time(row['created_at'])
        for row in db.execute('SELECT * FROM guess_ips'):
            _time(row['next_attempt'])
            _time(row['last_seen'])
            if type(row['failures']) is not int or not 1 <= row['failures'] <= 16:
                raise StoreError('Invalid guess budget')
        for row in db.execute('SELECT * FROM events'):
            if row['device_id'] is not None:
                _identifier(row['device_id'])
            if row['kind'] not in ('enrollment-pending', 'invitation-used', 'revoked', 'command-pending', 'command-delivered', 'command-done', 'command-failed', 'command-cancelled', 'command-expired', 'wake'):
                raise StoreError('Invalid event')
            _time(row['time'])
        for row in db.execute('SELECT * FROM commands'):
            _identifier(row['command_id'])
            _identifier(row['device_id'])
            _time(row['created_at'])
            if _time(row['updated_at']) < _time(row['created_at']):
                raise StoreError('Invalid command timestamps')
            if not isinstance(row['detail'], str) or len(row['detail']) > 200:
                raise StoreError('Invalid command detail')
        for row in db.execute('SELECT * FROM performance_samples'):
            _identifier(row['device_id'])
            _time(row['observed_at'])
            for key in ('ram_percent', 'disk_free_percent'):
                if row[key] is not None and number(row[key]) > 100:
                    raise StoreError('Invalid performance percentage')
            validate_performance({key: row[key] for key in ('cpu_percent', 'battery_percent', 'power_watts', 'temperature_c')})
        for table, columns in (('devices', ('credential_hash',)), ('codes', ('code_hash',)),
                               ('requests', ('poll_hash', 'request_digest')), ('records', ('digest',)),
                               ('invitations', ('code_hash', 'secret_hash')), ('guess_ips', ('ip_hash',))):
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
            snapshot.execute('DELETE FROM invitations')
            snapshot.execute('DELETE FROM guess_ips')
            tables = ('devices', 'codes', 'requests', 'records', 'throttle', 'settings', 'invitations', 'guess_ips', 'events', 'performance_samples', 'commands')
            snapshot.execute("UPDATE commands SET status='cancelled' WHERE status IN ('pending','delivered')")
            with self._transaction() as db:
                for table in reversed(tables):
                    db.execute('DELETE FROM ' + table)
                for table in tables:
                    rows = snapshot.execute('SELECT * FROM ' + table).fetchall()
                    if rows:
                        placeholders = ','.join('?' for _ in rows[0])
                        db.executemany('INSERT INTO ' + table + ' VALUES (' + placeholders + ')', [tuple(row) for row in rows])
                self._live_credentials.clear()
                self._live_samples.clear()
                self._live_demand.clear()
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
            for row in db.execute('SELECT device_id,status,last_received_at,latest_record FROM devices'):
                counts['total'] += 1
                counts[row['status']] += 1
                received = self._last_seen(row)
                if row['status'] == 'approved' and received:
                    elapsed = (_now() - _time(received)).total_seconds()
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

    def fleet(self, day=None, days=7, limit=6, offset=0, search='', group='', status='',
              sort='name', descending=False):
        """Small dashboard projection. Dated amounts use permitted history only.

        Sorting and filtering precede pagination. Missing dates remain unknown,
        including in the aggregate, whose contributor count is explicit.
        """
        _page(limit, offset)
        if (type(days) is not int or days not in (7, 30) or type(descending) is not bool
                or sort not in ('name', 'group', 'status', 'today', 'last')
                or any(not isinstance(v, str) or len(v) > 4096 for v in (search, group, status))):
            raise StoreError('Invalid fleet filter')
        try:
            selected = dt.date.fromisoformat(day) if day is not None else _now().astimezone().date()
        except (ValueError, TypeError):
            raise StoreError('Invalid fleet date') from None
        dates = [(selected - dt.timedelta(days=i)).isoformat() for i in range(days-1, -1, -1)]
        daily = {date: dict(day=date, seconds=None, devices=0) for date in dates}
        counts = dict(total=0, enrolled=0, connected=0, overdue=0, pending=0)
        rows, groups, names, events = [], set(), {}, []
        with self._transaction() as db:
            interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
            threshold = max(OVERDUE_SECONDS, interval * 3)
            counts['pending'] = db.execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0]
            for row in db.execute('SELECT * FROM devices'):
                received = self._last_seen(row)
                identity, policy = json.loads(row['identity']), json.loads(row['policy'])
                name = identity.get('display_name') or row['device_id']
                names[row['device_id']] = name
                department = identity.get('department', '')
                if department:
                    groups.add(department)
                counts['total'] += 1
                current = row['status']
                if current == 'approved':
                    counts['enrolled'] += 1
                    if received:
                        overdue = ((_now() - _time(received)).total_seconds() > (LIVE_SILENCE_SECONDS if self._live_samples.get(row['device_id']) else threshold)
                                   or self._forced_offline(row['device_id']))
                        current = 'report-overdue' if overdue else 'reporting'
                        counts['overdue' if overdue else 'connected'] += 1
                        if overdue:
                            events.append(dict(device_id=row['device_id'], kind='report-overdue',
                                               time=_stamp(_time(row['last_received_at']) + dt.timedelta(seconds=threshold))))
                    else:
                        current = 'unavailable'
                history = json.loads(row['history']) if row['history'] and policy['history'] else {}
                dated = history.get('daily', {})
                if row['retained_before']:
                    dated = {date: seconds for date, seconds in dated.items() if date >= row['retained_before'][:10]}
                for date in dates:
                    if date in dated:
                        daily[date]['seconds'] = (daily[date]['seconds'] or 0) + dated[date]
                        daily[date]['devices'] += 1
                snapshot = json.loads(row['latest_record']) if row['latest_record'] else None
                item = dict(device_id=row['device_id'], name=name, group=department,
                            status=current, today=dated.get(selected.isoformat()), last=received,
                            totals=snapshot['totals'] if snapshot else None, identity=identity,
                            client_version=row['client_version'], tracking_since=history.get('recording_started'),
                            coverage='partial' if dated else 'unknown')
                item['health'] = json.loads(row['health']) if row['health'] and policy.get('health') else None
                item['unstable'] = bool(item['health'] and (item['health'].get('crashes_30d') or 0) >= 3)
                if search.casefold() not in row['search_text']:
                    continue
                if group and group.casefold() != department.casefold():
                    continue
                if status and current != status and row['status'] != status:
                    continue
                rows.append(item)
            for row in db.execute('SELECT device_id,last_received_at FROM devices WHERE last_received_at IS NOT NULL'):
                events.append(dict(device_id=row[0], kind='report-received', time=row[1]))
            events += [dict(row) for row in db.execute('SELECT device_id,kind,time FROM events ORDER BY event_id DESC LIMIT 30')]
        # Keep missing values last in either direction, and resolve ties by UUID.
        known = [row for row in rows if row[sort] is not None]
        unknown = [row for row in rows if row[sort] is None]
        known.sort(key=lambda row: ((row[sort].casefold() if isinstance(row[sort], str) else row[sort]), row['device_id']), reverse=descending)
        ordered = known + sorted(unknown, key=lambda row: row['device_id'])
        events.sort(key=lambda event: event['time'], reverse=True)
        for event in events[:20]:
            event['name'] = names.get(event['device_id'], '')
        return dict(counts=counts, daily=list(daily.values()), today=daily[selected.isoformat()],
                    rows=ordered[offset:offset+limit], matching=len(rows), groups=sorted(groups, key=str.casefold),
                    recent_events=events[:20], updated_at=_stamp(_now()), date=selected.isoformat())

    def request_refresh(self, device_id):
        with self._transaction() as db:
            row = self._device_row(db, device_id)
            if row['status'] != 'approved':
                raise StoreError('Only approved devices can receive a refresh hint')
            db.execute('UPDATE devices SET refresh_requested=1 WHERE device_id=?', (device_id,))
            return dict(device_id=device_id, refresh_requested=True)

    @staticmethod
    def _performance_insert(db, device_id, readings):
        now = _now()
        cutoff = _stamp(now-dt.timedelta(days=7))
        db.execute('DELETE FROM performance_samples WHERE observed_at<?', (cutoff,))
        perf, specs = readings.get('performance'), readings.get('specs', {})
        if not perf:
            return
        observed = _time(readings['observed_at'])
        if not now-dt.timedelta(days=7) <= observed <= now+dt.timedelta(minutes=5):
            return
        if db.execute('SELECT 1 FROM performance_samples WHERE device_id=? AND observed_at>? AND observed_at<?',
                      (device_id, _stamp(observed-dt.timedelta(minutes=5)), _stamp(observed+dt.timedelta(minutes=5)))).fetchone():
            return
        ram = specs.get('ram_bytes')
        used = perf.get('ram_used_bytes')
        db.execute('INSERT INTO performance_samples VALUES (?,?,?,?,?,?,?,?)',
                   (device_id, _stamp(observed), perf.get('cpu_percent'),
                    min(100, 100*used/ram) if ram and used is not None else None,
                    _disk_percent(specs, perf), perf.get('battery_percent'), perf.get('power_watts'), perf.get('temperature_c')))

    @staticmethod
    def _expire_commands(db):
        rows = db.execute("SELECT command_id,device_id FROM commands WHERE status IN ('pending','delivered') AND created_at<=?",
                          (_stamp(_now()-dt.timedelta(minutes=10)),)).fetchall()
        for row in rows:
            db.execute("UPDATE commands SET status='expired',updated_at=? WHERE command_id=?", (_stamp(_now()), row['command_id']))
            Store._event(db, row['device_id'], 'command-expired')

    @staticmethod
    def _cancel_device_commands(db, device_id):
        rows = db.execute("SELECT command_id FROM commands WHERE device_id=? AND status IN ('pending','delivered')", (device_id,)).fetchall()
        for row in rows:
            db.execute("UPDATE commands SET status='cancelled',updated_at=? WHERE command_id=?", (_stamp(_now()), row['command_id']))
            Store._event(db, device_id, 'command-cancelled')

    def _online(self, db, row):
        if self._forced_offline(row['device_id']):
            return False
        samples = self._live_samples.get(row['device_id'])
        if samples and (_now()-_time(samples[-1]['observed_at'])).total_seconds() < 20:
            return True
        interval = db.execute('SELECT reporting_interval FROM settings WHERE singleton=1').fetchone()[0]
        return bool(row['last_received_at'] and (_now()-_time(row['last_received_at'])).total_seconds() <= max(600, 3*interval))

    def send_command(self, device_id, action):
        command = self._send_command(device_id, action)
        self._hook('command', device_id)        # a connected computer gets it now, not on its next poll
        return command

    def take_command(self, device_id):
        """The pending command for a computer with an open socket, marked delivered. None when nothing waits."""
        with self._transaction() as db:
            row = db.execute('SELECT status,policy FROM devices WHERE device_id=?', (device_id,)).fetchone()
            if not row or row['status'] != 'approved':
                return None
            self._expire_commands(db)
            if not json.loads(row['policy']).get('control'):
                for waiting in db.execute("SELECT command_id FROM commands WHERE device_id=? AND action!='refresh' AND status='pending'", (device_id,)).fetchall():
                    db.execute("UPDATE commands SET status='cancelled',updated_at=? WHERE command_id=?", (_stamp(_now()), waiting['command_id']))
                    self._event(db, device_id, 'command-cancelled')
            command = db.execute("SELECT * FROM commands WHERE device_id=? AND status='pending'", (device_id,)).fetchone()
            if not command:
                return None
            db.execute("UPDATE commands SET status='delivered',updated_at=? WHERE command_id=?", (_stamp(_now()), command['command_id']))
            self._event(db, device_id, 'command-delivered')
            return dict(id=command['command_id'], action=command['action'], issued_at=command['created_at'])

    def requeue_command(self, command_id):
        with self._transaction() as db:
            db.execute("UPDATE commands SET status='pending',updated_at=? WHERE command_id=? AND status='delivered'", (_stamp(_now()), command_id))

    def command_ack(self, device_id, command_id, status, detail=''):
        """The computer's own answer to a pushed command: executed, failed, unsupported or declined."""
        outcome = 'done' if status == 'executed' else 'failed'
        with self._transaction() as db:
            row = db.execute('SELECT * FROM commands WHERE command_id=? AND device_id=?', (command_id, device_id)).fetchone()
            if row and (row['status'] == 'delivered' or row['status'] == 'done' and outcome == 'failed'):
                db.execute('UPDATE commands SET status=?,updated_at=?,detail=? WHERE command_id=?',
                           (outcome, _stamp(_now()), detail or status, command_id))
                self._event(db, device_id, 'command-' + outcome)
                return outcome
        return None

    def _send_command(self, device_id, action):
        if action not in ('sleep', 'restart', 'shutdown', 'refresh'):
            raise StoreError('Invalid command action')
        with self._transaction() as db:
            self._expire_commands(db)
            row = self._device_row(db, device_id)
            if row['status'] != 'approved' or not self._online(db, row):
                raise StoreError('Device must be approved and online')
            if action != 'refresh' and not json.loads(row['policy']).get('control'):
                raise StoreError('Remote power consent is required')
            if db.execute("SELECT 1 FROM commands WHERE device_id=? AND status IN ('pending','delivered')", (device_id,)).fetchone():
                raise ConflictError('Device already has a pending command')
            command_id, now = str(uuid.uuid4()), _stamp(_now())
            db.execute("INSERT INTO commands VALUES (?,?,?,'pending',?,?,'')", (command_id, device_id, action, now, now))
            self._event(db, device_id, 'command-pending')
            return dict(db.execute('SELECT * FROM commands WHERE command_id=?', (command_id,)).fetchone())

    def cancel_command(self, command_id):
        _identifier(command_id)
        with self._transaction() as db:
            self._expire_commands(db)
            row = db.execute('SELECT * FROM commands WHERE command_id=?', (command_id,)).fetchone()
            if not row:
                raise StoreError('Unknown command')
            if row['status'] not in ('pending', 'delivered'):
                raise StoreError('Command is no longer pending')
            db.execute("UPDATE commands SET status='cancelled',updated_at=? WHERE command_id=?", (_stamp(_now()), command_id))
            self._event(db, row['device_id'], 'command-cancelled')
            return dict(db.execute('SELECT * FROM commands WHERE command_id=?', (command_id,)).fetchone())

    def _sync_command(self, db, device_id, policy, result):
        self._expire_commands(db)
        if result:
            row = db.execute('SELECT * FROM commands WHERE command_id=? AND device_id=?', (result['id'], device_id)).fetchone()
            if row and (row['status'] == 'delivered' or row['status'] == 'done' and result['status'] == 'failed'):
                status = 'failed' if result['status'] == 'declined' else result['status']
                db.execute('UPDATE commands SET status=?,updated_at=?,detail=? WHERE command_id=?',
                           (status, _stamp(_now()), result['detail'], result['id']))
                self._event(db, device_id, 'command-'+status)
        if not policy.get('control'):
            rows = db.execute("SELECT command_id FROM commands WHERE device_id=? AND action!='refresh' AND status IN ('pending','delivered')", (device_id,)).fetchall()
            for row in rows:
                db.execute("UPDATE commands SET status='cancelled',updated_at=? WHERE command_id=?", (_stamp(_now()), row['command_id']))
                self._event(db, device_id, 'command-cancelled')
        row = db.execute("SELECT * FROM commands WHERE device_id=? AND status IN ('pending','delivered')", (device_id,)).fetchone()
        if not row:
            return None
        if row['status'] == 'pending':
            db.execute("UPDATE commands SET status='delivered',updated_at=? WHERE command_id=?", (_stamp(_now()), row['command_id']))
            self._event(db, device_id, 'command-delivered')
        return dict(id=row['command_id'], action=row['action'], issued_at=row['created_at'])

    def wake(self, device_id):
        from lifetime_wol import send_magic_packet
        with self._transaction() as db:
            row = self._device_row(db, device_id)
            if row['status'] != 'approved' or not json.loads(row['policy']).get('control') or self._online(db, row):
                raise StoreError('Wake requires an approved offline device with remote power consent')
            readings = json.loads(row['readings']) if row['readings'] else {}
            macs = readings.get('specs', {}).get('mac_addresses', [])
            if not macs:
                raise StoreError('No MAC addresses are available')
            for mac in macs:
                send_magic_packet(mac)
            self._event(db, device_id, 'wake')
            return dict(device_id=device_id, sent=len(macs))
