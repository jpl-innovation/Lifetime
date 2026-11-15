"""Alert monitoring for Lifetime Server: presence history, rules, calendars and incidents.

Monitoring has its own SQLite file (``monitoring.sqlite``), separate from ``server.sqlite`` and from every awake-time
ledger, which it never opens. One writer owns it: the Monitor that the server runtime creates; the admin API reaches
it only through that object. It reads the server store through ``Store.monitor_snapshot`` (presence, report times and
only the readings each computer's policy allows) and never writes there.

Server downtime is UNKNOWN, never DOWN and never zero: at start the open presence intervals are closed at the last
heartbeat and an "unknown (server-down)" interval covers the gap. History is bounded by age (RETENTION_DAYS) and by
size (QUOTA_BYTES), checked every hour.
"""
import contextlib
import datetime as dt
import json
import sqlite3
import threading

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4C544D4E     # "LTMN"
RETENTION_DAYS = 180
QUOTA_BYTES = 64 * 1024 * 1024
MAX_DELIVERIES = 5000
HOUSEKEEPING_SECONDS = 3600
PRESENCE = ('online', 'offline', 'unknown')

MIGRATIONS = [
    (   # 1: the first monitoring schema
        'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        '''CREATE TABLE presence (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('online','offline','unknown')), reason TEXT NOT NULL,
            start_at TEXT NOT NULL, end_at TEXT)''',
        'CREATE INDEX presence_device ON presence(device_id, start_at)',
        'CREATE UNIQUE INDEX presence_open ON presence(device_id) WHERE end_at IS NULL',
        '''CREATE TABLE incidents (id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, rule TEXT NOT NULL,
            cause TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','open','acknowledged','resolved','suppressed')),
            started_at TEXT NOT NULL, opened_at TEXT, acknowledged_by TEXT, acknowledged_at TEXT, clear_since TEXT,
            resolved_at TEXT, resolution TEXT, notified INTEGER NOT NULL DEFAULT 0 CHECK(notified IN (0,1)),
            value REAL, device_name TEXT NOT NULL DEFAULT '', group_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)''',
        "CREATE UNIQUE INDEX incidents_active ON incidents(device_id, rule, cause) WHERE state IN ('pending','open','acknowledged','suppressed')",
        'CREATE INDEX incidents_device ON incidents(device_id, started_at)',
        '''CREATE TABLE timeline (id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')''',
        'CREATE INDEX timeline_incident ON timeline(incident_id, id)',
        'CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        '''CREATE TABLE schedules (scope TEXT NOT NULL CHECK(scope IN ('default','group','device')), key TEXT NOT NULL,
            value TEXT NOT NULL, PRIMARY KEY(scope, key))''',
        '''CREATE TABLE maintenance (id INTEGER PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('all','group','device')),
            key TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')''',
        '''CREATE TABLE deliveries (id INTEGER PRIMARY KEY, incident_id INTEGER,
            kind TEXT NOT NULL CHECK(kind IN ('open','recovery','test','summary')),
            channel TEXT NOT NULL CHECK(channel IN ('email','webhook')), ref TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed')), attempts INTEGER NOT NULL DEFAULT 0,
            next_at TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(kind, channel, ref))''',
        'CREATE INDEX deliveries_due ON deliveries(status, next_at)',
    ),
]


class MonitorError(ValueError):
    """Invalid monitoring setting or request."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _stamp(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec='seconds')


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(dt.timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise MonitorError('Expected a timezone-aware ISO timestamp') from None


def _migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    application = db.execute('PRAGMA application_id').fetchone()[0]
    if not 0 <= version <= SCHEMA_VERSION:
        raise MonitorError('Unsupported monitoring database version')
    if application not in (0, APPLICATION_ID) or (version and application != APPLICATION_ID):
        raise MonitorError('Not a Lifetime monitoring database')
    if not version:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise MonitorError('Refusing to initialize a nonempty database')
        db.execute('PRAGMA auto_vacuum=INCREMENTAL')    # only possible before the first table exists
    db.execute('BEGIN IMMEDIATE')
    try:
        for number in range(version, SCHEMA_VERSION):
            for statement in MIGRATIONS[number]:
                db.execute(statement)
            db.execute(f'PRAGMA user_version={number + 1}')
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
        db.commit()
    except BaseException:
        db.rollback()
        raise


class Monitor:
    """The single writer of monitoring.sqlite. ``store`` provides ``monitor_snapshot()``; ``clock`` returns UTC now."""

    def __init__(self, path, store=None, clock=None, quota=QUOTA_BYTES, retention_days=RETENTION_DAYS):
        self.store, self.clock = store, clock or _now
        self.quota, self.retention = quota, dt.timedelta(days=retention_days)
        self.started_at = None
        self._housekeeping_at = None
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=15, isolation_level=None, check_same_thread=False)
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

    def close(self):
        with self._lock:
            with contextlib.suppress(sqlite3.Error):
                with self._transaction() as db:
                    self._meta(db, 'heartbeat', _stamp(self.clock()))
            self._db.close()

    @staticmethod
    def _meta(db, key, value=None):
        if value is not None:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, value))
            return value
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    # ---- start and reconcile ----

    def start(self):
        """Reconcile after a restart: the time the server was down is UNKNOWN for every computer it knew."""
        now = self.clock()
        with self._transaction() as db:
            last = self._meta(db, 'heartbeat')
            last = min(_time(last), now) if last else now
            for row in db.execute('SELECT id,device_id,start_at FROM presence WHERE end_at IS NULL').fetchall():
                end = max(last, _time(row['start_at']))
                db.execute('UPDATE presence SET end_at=? WHERE id=?', (_stamp(end), row['id']))
                if end < now:
                    db.execute("INSERT INTO presence(device_id,state,reason,start_at,end_at) VALUES (?,'unknown','server-down',?,?)",
                               (row['device_id'], _stamp(end), _stamp(now)))
            self._meta(db, 'heartbeat', _stamp(now))
        self.started_at = now
        return self

    # ---- presence ----

    def _presence(self, device, now):
        """(state, reason, since) for one computer, from what the server store saw.

        Right after a start the server cannot know whether a computer that last reported before the downtime is
        still off: it stays UNKNOWN until it has had a full reporting window to come back."""
        if device.get('online') is None:
            return 'unknown', 'never-reported', now
        if device['online']:
            return 'online', 'reporting', now
        since = _time(device['offline_since'])
        started = self.started_at or now
        if since < started:
            if (now - started).total_seconds() < device.get('threshold', 600):
                return 'unknown', 'awaiting-report', started
            return 'offline', device.get('reason') or 'report-overdue', started
        return 'offline', device.get('reason') or 'report-overdue', since

    @staticmethod
    def _record_presence(db, device_id, state, reason, since, now):
        current = db.execute('SELECT id,state,reason,start_at FROM presence WHERE device_id=? AND end_at IS NULL',
                             (device_id,)).fetchone()
        if current and current['state'] == state:
            return
        start = min(now, since)
        if current:
            start = max(start, _time(current['start_at']))
            db.execute('UPDATE presence SET end_at=? WHERE id=?', (_stamp(start), current['id']))
        db.execute('INSERT INTO presence(device_id,state,reason,start_at) VALUES (?,?,?,?)',
                   (device_id, state, reason, _stamp(start)))

    def tick(self, now=None):
        """One evaluation pass. Returns the per-computer monitoring status."""
        now = now or self.clock()
        if self.started_at is None:
            self.start()
        devices = self.store.monitor_snapshot() if self.store is not None else []
        status = {}
        with self._transaction() as db:
            self._meta(db, 'heartbeat', _stamp(now))
            for device in devices:
                state, reason, since = self._presence(device, now)
                self._record_presence(db, device['device_id'], state, reason, since, now)
                status[device['device_id']] = dict(presence=state, reason=reason)
            for row in db.execute('SELECT id,device_id FROM presence WHERE end_at IS NULL').fetchall():
                if row['device_id'] not in status:      # no longer approved or removed: its history simply ends
                    db.execute('UPDATE presence SET end_at=? WHERE id=?', (_stamp(now), row['id']))
            if self._housekeeping_at is None or (now - self._housekeeping_at).total_seconds() >= HOUSEKEEPING_SECONDS:
                self._housekeeping(db, now)
                self._housekeeping_at = now
        return status

    def presence(self, device_id, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise MonitorError('Invalid limit')
        with self._lock:
            return [dict(row) for row in self._db.execute(
                'SELECT state,reason,start_at,end_at FROM presence WHERE device_id=? ORDER BY start_at DESC,id DESC LIMIT ?',
                (str(device_id), limit))]

    def forget(self, device_id):
        """A removed computer: delete everything monitoring kept about it."""
        with self._transaction() as db:
            db.execute('DELETE FROM presence WHERE device_id=?', (device_id,))
            db.execute('DELETE FROM incidents WHERE device_id=?', (device_id,))
            db.execute("DELETE FROM schedules WHERE scope='device' AND key=?", (device_id,))
            db.execute("DELETE FROM maintenance WHERE scope='device' AND key=?", (device_id,))

    # ---- retention and size ----

    def size(self):
        with self._lock:
            pages = self._db.execute('PRAGMA page_count').fetchone()[0] - self._db.execute('PRAGMA freelist_count').fetchone()[0]
            return pages * self._db.execute('PRAGMA page_size').fetchone()[0]

    def _housekeeping(self, db, now):
        """Age limit first, then the size quota: oldest closed history goes first, active incidents never."""
        cutoff = _stamp(now - self.retention)
        db.execute('DELETE FROM presence WHERE end_at IS NOT NULL AND end_at<?', (cutoff,))
        db.execute("DELETE FROM incidents WHERE state='resolved' AND resolved_at<?", (cutoff,))
        db.execute("DELETE FROM deliveries WHERE status IN ('sent','failed') AND updated_at<?", (cutoff,))
        db.execute('DELETE FROM deliveries WHERE id <= (SELECT MAX(id)-? FROM deliveries)', (MAX_DELIVERIES,))
        db.execute('DELETE FROM maintenance WHERE end_at<?', (cutoff,))
        for _ in range(20):
            used = (db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]) \
                * db.execute('PRAGMA page_size').fetchone()[0]
            if used <= self.quota:
                break
            removed = 0
            for table, closed, order in (('presence', 'end_at IS NOT NULL', 'end_at'),
                                         ('incidents', "state='resolved'", 'resolved_at'),
                                         ('deliveries', "status IN ('sent','failed')", 'updated_at')):
                count = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {closed}').fetchone()[0]     # nosec B608: fixed tuple
                removed += db.execute(f'DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE {closed} '   # nosec B608
                                      f'ORDER BY {order} LIMIT ?)', (max(1, count // 4),)).rowcount
            if not removed:
                break
        db.execute('PRAGMA incremental_vacuum').fetchall()     # each step frees a page: run them all
