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
import functools
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid
import zoneinfo

import lifetime_notify as notify

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4C544D4E     # "LTMN"
RETENTION_DAYS = 180
QUOTA_BYTES = 64 * 1024 * 1024
MAX_DELIVERIES = 5000
HOUSEKEEPING_SECONDS = 3600
PRESENCE = ('online', 'offline', 'unknown')
BACKOFF = (60, 300, 900, 3600)  # seconds before the 2nd to 5th attempt; after the 5th the delivery has failed
RECOVER_SECONDS = 300           # a problem must stay away this long before its incident resolves (no flapping storms)
ACTIVE = ('pending', 'open', 'acknowledged', 'suppressed')

# Built-in rules. ``threshold``/``minutes`` are the editable defaults with their allowed ranges; ``direction`` and
# ``hysteresis`` make a reading inside the band keep the current state instead of flipping it.
RULES = {
    'offline': dict(enabled=True, threshold=None, minutes=15, minutes_range=(1, 10080)),
    'no_report': dict(enabled=True, threshold=None, minutes=1440, minutes_range=(5, 43200)),
    'disk_low': dict(enabled=True, threshold=10, range=(1, 99), minutes=30, metric='disk_free_percent',
                     direction='below', hysteresis=2),
    'cpu_high': dict(enabled=True, threshold=95, range=(1, 100), minutes=15, minutes_range=(1, 10080),
                     metric='cpu_percent', direction='above', hysteresis=10),
    'memory_high': dict(enabled=True, threshold=95, range=(1, 100), minutes=15, metric='ram_percent',
                        direction='above', hysteresis=5),
    'battery_worn': dict(enabled=True, threshold=60, range=(1, 99), minutes=0, metric='battery_health_percent',
                         direction='below', hysteresis=2, optional=True),
    'temperature_high': dict(enabled=True, threshold=90, range=(30, 150), minutes=5, metric='temperature_c',
                             direction='above', hysteresis=5, optional=True),
    'crashes': dict(enabled=True, threshold=3, range=(1, 1000), minutes=0, direction='atleast', hysteresis=0),
    'agent_outdated': dict(enabled=False, threshold=None, minutes=0, version=''),
}
VERSION = re.compile(r'[0-9]{1,6}(\.[0-9]{1,6}){0,3}')

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


def _version(text):
    """'1.2.0' -> (1, 2, 0); the leading digits of each part, None when there are none."""
    parts = []
    for part in str(text).split('.')[:4]:
        match = re.match(r'[0-9]+', part)
        if not match:
            break
        parts.append(int(match.group()))
    return tuple(parts) or None


def _compare(rule, value, threshold, active):
    """bad / clear for a reading; inside the hysteresis band the current state holds."""
    direction, band = rule['direction'], rule['hysteresis']
    if direction == 'above':
        bad, clear = value > threshold, value <= threshold - band
    elif direction == 'below':
        bad, clear = value < threshold, value >= threshold + band
    else:
        bad, clear = value >= threshold, value < threshold
    return 'bad' if bad else 'clear' if clear else ('bad' if active else 'clear')


DEFAULT_SCHEDULE = dict(kind='auto', timezone='local', start='08:00', end='18:00', weekdays=[0, 1, 2, 3, 4], holidays=[])
SCHEDULE_KINDS = ('auto', 'always', 'business', 'never')   # auto: always for desktops, never for laptops


NO_TZDATA = ('Time zone names such as Asia/Ho_Chi_Minh need the tzdata package on this server (install '
             'requirements-server.txt). Use local, UTC or a fixed offset such as +07:00 meanwhile.')


@functools.lru_cache(maxsize=1)
def iana_available():
    """True when zoneinfo can load IANA names. Windows Python has no system database: it needs ``tzdata``."""
    try:
        zoneinfo.ZoneInfo('Etc/UTC')
        return True
    except (ValueError, OSError, KeyError):     # KeyError: ZoneInfoNotFoundError
        return False


def _zone(name):
    """'local' (the server's zone, None), 'UTC', a fixed offset '+07:00', or an IANA name where the system knows it.

    Without a time zone database an IANA name is refused with NO_TZDATA; offsets keep working."""
    if name == 'local':
        return None
    if name == 'UTC':
        return dt.timezone.utc
    match = re.fullmatch(r'([+-])([01][0-9]|2[0-3]):([0-5][0-9])', name) if isinstance(name, str) else None
    if match:
        offset = dt.timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        if offset > dt.timedelta(hours=14):
            raise MonitorError('Unknown time zone')
        return dt.timezone(-offset if match.group(1) == '-' else offset)
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_+\-/]{1,64}', name):
        raise MonitorError('Unknown time zone')
    try:
        return zoneinfo.ZoneInfo(name)
    except (ValueError, OSError, KeyError):     # KeyError: ZoneInfoNotFoundError (no tz data on this system)
        raise MonitorError('Unknown time zone' if iana_available() else NO_TZDATA) from None


def schedule_zone(schedule):
    """(tzinfo or None for server local, True when the saved zone loaded). A saved IANA name that this system can
    no longer load (tzdata removed) falls back to server time instead of stopping monitoring."""
    try:
        return _zone(schedule['timezone']), True
    except MonitorError:
        return None, False


def _clock_time(value):
    if not isinstance(value, str) or not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', value):
        raise MonitorError('Expected a time as HH:MM')
    return dt.time.fromisoformat(value)


def _schedule(value):
    if value.get('kind') not in SCHEDULE_KINDS:
        raise MonitorError('Invalid schedule kind')
    _zone(value['timezone'])
    if _clock_time(value['start']) == _clock_time(value['end']):
        raise MonitorError('Opening and closing times must differ')
    weekdays = value['weekdays']
    if (not isinstance(weekdays, list) or len(set(weekdays)) != len(weekdays)
            or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)):
        raise MonitorError('Invalid weekdays')
    holidays = value['holidays']
    if not isinstance(holidays, list) or len(holidays) > 366:
        raise MonitorError('Invalid holidays')
    for day in holidays:
        try:
            if not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except ValueError:
            raise MonitorError('Invalid holiday date') from None
    return dict(value, weekdays=sorted(weekdays), holidays=sorted(set(holidays)))


def expected(schedule, laptop, now):
    """(expected online at ``now``, start of the current expected window or None) for one schedule.

    'auto' expects desktops always and laptops (or computers whose kind is unknown) never, so a laptop asleep
    outside its hours never raises an alert. Business hours may run over midnight (end before start)."""
    kind = schedule['kind']
    if kind == 'auto':
        kind = 'always' if laptop is False else 'never'
    if kind != 'business':
        return kind == 'always', None
    zone, _ = schedule_zone(schedule)
    local = now.astimezone(zone) if zone else now.astimezone()
    start, end = _clock_time(schedule['start']), _clock_time(schedule['end'])

    def opened(day):
        if day.weekday() not in schedule['weekdays'] or day.isoformat() in schedule['holidays']:
            return None
        return dt.datetime.combine(day, start, tzinfo=zone or local.tzinfo)
    today, moment = local.date(), local.time()
    if start < end:
        window = opened(today) if start <= moment < end else None
    elif moment >= start:
        window = opened(today)
    else:
        window = opened(today - dt.timedelta(days=1)) if moment < end else None
    return window is not None, window and window.astimezone(dt.timezone.utc)


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

    def __init__(self, path, store=None, clock=None, quota=QUOTA_BYTES, retention_days=RETENTION_DAYS,
                 secrets_path=None, server_name='Lifetime Server', transport=None):
        self.store, self.clock = store, clock or _now
        self.secrets = notify.Secrets(secrets_path or Path(path).with_name(notify.SECRET_FILE))
        self.server_name, self.transport = server_name, transport or notify.Transport()
        self.status = {}
        self._delivering = threading.Lock()
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
            # A message being sent when the server stopped may or may not have left: never send it twice.
            db.execute("UPDATE deliveries SET status='failed',error='interrupted',updated_at=? WHERE status='sending'",
                       (_stamp(now),))
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
            rules = self._rules(db)
            for device in devices:
                state, reason, since = self._presence(device, now)
                self._record_presence(db, device['device_id'], state, reason, since, now)
                expected, window = self._expected(db, device, now)
                paused = self._paused(db, device, now)
                missing = []
                for name, rule in rules.items():
                    row = self._active(db, device['device_id'], name)
                    verdict, began, value = self._evaluate(name, rule, device, (state, since), expected, window, now,
                                                           bool(row and row['clear_since'] is None))
                    if verdict == 'missing':
                        missing.append(name)
                    self._step(db, device, name, rule, row, verdict, began, value, paused, now)
                status[device['device_id']] = dict(presence=state, reason=reason, expected=expected, paused=paused,
                                                   missing=missing)
            for row in db.execute('SELECT id,device_id FROM presence WHERE end_at IS NULL').fetchall():
                if row['device_id'] not in status:      # no longer approved or removed: its history simply ends
                    db.execute('UPDATE presence SET end_at=? WHERE id=?', (_stamp(now), row['id']))
            for row in db.execute(f'SELECT * FROM incidents WHERE state IN {ACTIVE}').fetchall():    # nosec B608: constant
                if row['device_id'] not in status or row['rule'] not in rules:
                    self._resolve(db, row, now, 'not-monitored')     # removed, disconnected, or its rule turned off
            self._summary(db, now)
            if self._housekeeping_at is None or (now - self._housekeeping_at).total_seconds() >= HOUSEKEEPING_SECONDS:
                self._housekeeping(db, now)
                self._housekeeping_at = now
        with self._lock:
            self.status = status
        return status

    # ---- rules ----

    @staticmethod
    def _setting(db, key, value=None):
        if value is not None:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))
            return value
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def _all_rules(self, db):
        saved = json.loads(self._setting(db, 'rules') or '{}')
        return {name: dict(default, **saved.get(name, {})) for name, default in RULES.items()}

    def _rules(self, db):
        """The enabled built-in rules with their saved settings applied."""
        return {name: rule for name, rule in self._all_rules(db).items() if rule['enabled']}

    def rules(self):
        with self._transaction() as db:
            rules = self._all_rules(db)
        return [dict(rule=name, **{key: rule[key] for key in ('enabled', 'threshold', 'minutes', 'version') if key in rule},
                     **({'range': list(rule['range'])} if 'range' in rule else {}),
                     minutes_range=list(rule.get('minutes_range', (0, 10080)))) for name, rule in rules.items()]

    def set_rule(self, rule, enabled=None, threshold=None, minutes=None, version=None):
        """Turn one built-in rule on or off and change its threshold, duration or (agent rule) minimum version."""
        if rule not in RULES:
            raise MonitorError('Unknown rule')
        default = RULES[rule]
        change = {}
        if enabled is not None:
            if type(enabled) is not bool:
                raise MonitorError('Invalid enabled flag')
            change['enabled'] = enabled
        if threshold is not None:
            low, high = default.get('range', (0, -1))
            if type(threshold) not in (int, float) or not low <= threshold <= high:
                raise MonitorError('Threshold out of range')
            change['threshold'] = threshold
        if minutes is not None:
            low, high = default.get('minutes_range', (0, 10080))
            if type(minutes) is not int or not low <= minutes <= high:
                raise MonitorError('Duration out of range')
            change['minutes'] = minutes
        if version is not None:
            if 'version' not in default or not isinstance(version, str) or (version and not VERSION.fullmatch(version)):
                raise MonitorError('Invalid minimum version')
            change['version'] = version
        with self._transaction() as db:
            saved = json.loads(self._setting(db, 'rules') or '{}')
            saved[rule] = dict(saved.get(rule, {}), **change)
            self._setting(db, 'rules', json.dumps(saved, sort_keys=True))
        return self.rules()

    # ---- calendars: expected-online schedules and maintenance windows ----

    def _schedule_for(self, db, device):
        for scope, key in (('device', device['device_id']), ('group', (device.get('group') or '').casefold()), ('default', '')):
            row = db.execute('SELECT value FROM schedules WHERE scope=? AND key=?', (scope, key)).fetchone()
            if row and (scope != 'group' or key):
                return dict(json.loads(row[0]), scope=scope)
        return dict(DEFAULT_SCHEDULE, scope='default')

    def _expected(self, db, device, now):
        """(expected online now, when the current expected window began or None)."""
        return expected(self._schedule_for(db, device), device.get('laptop'), now)

    @staticmethod
    def _paused(db, device, now):
        """True while a maintenance window covers this computer (PAUSED: no alert opens)."""
        moment = _stamp(now)
        return bool(db.execute('''SELECT 1 FROM maintenance WHERE start_at<=? AND end_at>? AND (scope='all'
            OR (scope='group' AND key=?) OR (scope='device' AND key=?))''',
                               (moment, moment, (device.get('group') or '').casefold(), device['device_id'])).fetchone())

    def schedules(self):
        with self._lock:
            rows = [dict(json.loads(row['value']), scope=row['scope'], key=row['key'])
                    for row in self._db.execute('SELECT * FROM schedules ORDER BY scope,key')]
        if not any(row['scope'] == 'default' for row in rows):
            rows.insert(0, dict(DEFAULT_SCHEDULE, scope='default', key=''))
        for row in rows:
            row['zone_ok'] = schedule_zone(row)[1]
        return rows

    @staticmethod
    def time_zones():
        """Whether IANA names work here; the dashboard shows the message when they do not."""
        return dict(iana=iana_available(), message='' if iana_available() else NO_TZDATA)

    def set_schedule(self, scope, kind, key='', timezone='local', start='08:00', end='18:00', weekdays=(0, 1, 2, 3, 4),
                     holidays=()):
        """Expected-online hours for everyone (default), a group (department) or one computer."""
        if scope not in ('default', 'group', 'device') or not isinstance(key, str) or len(key) > 128 \
                or (scope == 'default') != (key == '') or any(ord(c) < 32 for c in key):
            raise MonitorError('Invalid schedule scope')
        value = _schedule(dict(kind=kind, timezone=timezone, start=start, end=end, weekdays=list(weekdays),
                               holidays=list(holidays)))
        key = key.casefold() if scope == 'group' else key
        with self._transaction() as db:
            if scope != 'default' and db.execute('SELECT COUNT(*) FROM schedules').fetchone()[0] >= 1000:
                raise MonitorError('Too many schedules')
            db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?,?)', (scope, key, json.dumps(value, sort_keys=True)))
        return self.schedules()

    def delete_schedule(self, scope, key=''):
        with self._transaction() as db:
            db.execute('DELETE FROM schedules WHERE scope=? AND key=?', (scope, key.casefold() if scope == 'group' else key))
        return self.schedules()

    def maintenance(self, include_past=False):
        if type(include_past) is not bool:
            raise MonitorError('Invalid filter')
        with self._lock:
            return [dict(row) for row in self._db.execute(
                'SELECT * FROM maintenance WHERE end_at>? ORDER BY start_at,id',
                ('' if include_past else _stamp(self.clock()),))]

    def add_maintenance(self, scope, start, end, key='', note=''):
        """A window during which no alert opens for everyone, a group or one computer (problems stay suppressed)."""
        if scope not in ('all', 'group', 'device') or not isinstance(key, str) or len(key) > 128 \
                or (scope == 'all') != (key == '') or any(ord(c) < 32 for c in key):
            raise MonitorError('Invalid maintenance scope')
        if not isinstance(note, str) or len(note) > 200 or any(ord(c) < 32 for c in note):
            raise MonitorError('Invalid maintenance note')
        begin, finish = _time(start), _time(end)
        if not dt.timedelta(minutes=1) <= finish - begin <= dt.timedelta(days=31):
            raise MonitorError('A maintenance window lasts from one minute to 31 days')
        with self._transaction() as db:
            if db.execute('SELECT COUNT(*) FROM maintenance').fetchone()[0] >= 500:
                raise MonitorError('Too many maintenance windows')
            db.execute('INSERT INTO maintenance(scope,key,start_at,end_at,note) VALUES (?,?,?,?,?)',
                       (scope, key.casefold() if scope == 'group' else key, _stamp(begin), _stamp(finish), note.strip()))
        return self.maintenance()

    def delete_maintenance(self, window_id):
        if type(window_id) is not int:
            raise MonitorError('Invalid maintenance window')
        with self._transaction() as db:
            db.execute('DELETE FROM maintenance WHERE id=?', (window_id,))
        return self.maintenance()

    def _evaluate(self, name, rule, device, presence, expected, window, now, active=False):
        """(verdict, since, value). The verdict is bad, clear, missing (telemetry absent: a separate condition),
        idle (nothing to judge now: outside its hours, or offline for a reading rule) or na (not shared, no sensor,
        rule not configured). Missing or idle data never fires a rule and never resolves an open incident."""
        floor = max(filter(None, (window, self.started_at)), default=None)
        if name == 'offline':       # back online counts at any hour; being away counts only inside its hours
            state, since = presence
            if state == 'online':
                return 'clear', None, None
            if not expected or state == 'unknown':
                return 'idle', None, None
            return 'bad', max(filter(None, (since, floor))), None
        if name == 'no_report':
            last = max(filter(None, (_time(device.get('last_report') or device['created_at']), floor)))
            if (now - last).total_seconds() < rule['minutes'] * 60:
                return 'clear', None, None
            return ('bad' if expected else 'idle'), last, None
        if name == 'agent_outdated':
            minimum = _version(rule.get('version') or '')
            if not minimum:
                return 'na', None, None
            current = _version(device.get('client_version') or '')
            if current is None:
                return 'missing', None, None
            return ('bad' if current < minimum else 'clear'), None, None
        if name == 'crashes':
            if not device.get('health'):
                return 'na', None, None
            value = device.get('crashes_30d')
        elif not device.get('resources'):
            return 'na', None, None
        elif not device.get('online'):
            return 'idle', None, None
        elif not device.get('metrics'):
            return 'missing', None, None
        else:
            value = device['metrics'].get(rule['metric'])
            if value is None and rule.get('optional'):
                return 'na', None, None      # no battery or no temperature sensor: nothing to watch
            if not device.get('metrics_fresh'):
                return 'missing', None, None
        if value is None:
            return 'missing', None, None
        return _compare(rule, value, rule['threshold'], active), None, value

    # ---- incidents ----

    @staticmethod
    def _active(db, device_id, rule):
        return db.execute(f'SELECT * FROM incidents WHERE device_id=? AND rule=? AND cause=? AND state IN {ACTIVE}',  # nosec B608
                          (device_id, rule, rule)).fetchone()

    @staticmethod
    def _event(db, incident_id, kind, now, detail=''):
        db.execute('INSERT INTO timeline(incident_id,time,kind,detail) VALUES (?,?,?,?)', (incident_id, _stamp(now), kind, detail))

    def _resolve(self, db, row, now, resolution, at=None):
        if row['state'] == 'pending':
            db.execute('DELETE FROM incidents WHERE id=?', (row['id'],))     # never became an incident: nobody was told
            return
        db.execute("UPDATE incidents SET state='resolved',resolved_at=?,resolution=?,updated_at=? WHERE id=?",
                   (_stamp(at or now), resolution, _stamp(now), row['id']))
        self._event(db, row['id'], 'resolved', now, resolution)
        if resolution == 'recovered' and row['notified']:
            self._notify(db, row['id'], 'recovery', now)

    def _notify(self, db, incident_id, kind, now, ref=None):
        """Queue a message for every enabled channel. (kind, channel, ref) is unique, so a restart or a repeated
        pass can never queue the same message twice."""
        settings = self._notification_settings(db)
        for channel, enabled in (('email', settings['email_enabled']), ('webhook', settings['webhook_enabled'])):
            if enabled:
                db.execute('''INSERT OR IGNORE INTO deliveries(incident_id,kind,channel,ref,status,next_at,created_at,updated_at)
                    VALUES (?,?,?,?,'queued',?,?,?)''', (incident_id, kind, channel, ref or str(incident_id),
                                                         _stamp(now), _stamp(now), _stamp(now)))

    # ---- notifications ----

    def _notification_settings(self, db):
        return dict(notify.DEFAULTS, **json.loads(self._setting(db, 'notifications') or '{}'))

    def notifications(self):
        """Settings for the dashboard: never the password or the webhook URL, only whether they are set."""
        with self._transaction() as db:
            settings = self._notification_settings(db)
        secrets = self.secrets.load()
        return dict(settings, password_set=bool(secrets.get('smtp_password')),
                    webhook=notify.masked(secrets.get('webhook_url')), secrets_unreadable=bool(secrets.get('unreadable')))

    def set_notifications(self, password=None, webhook_url=None, **settings):
        """Save the settings. ``password`` and ``webhook_url``: None keeps the stored value, '' removes it."""
        with self._transaction() as db:
            try:
                value = notify.validate(dict(self._notification_settings(db), **settings))
                secrets = self.secrets.load()
                secrets.pop('unreadable', None)
                for key, given in (('smtp_password', password), ('webhook_url', webhook_url)):
                    if given is None:
                        continue
                    if not isinstance(given, str) or len(given) > 2048 or any(ord(c) < 32 for c in given):
                        raise MonitorError('Invalid secret')
                    if key == 'webhook_url' and given:
                        notify.check_webhook(given)
                    secrets[key] = given
            except notify.NotifyError as error:
                raise MonitorError(str(error)) from None
            if value['webhook_enabled'] and not secrets.get('webhook_url'):
                raise MonitorError('Enter the webhook address')
            if password is not None or webhook_url is not None:
                self.secrets.save(secrets)
            self._setting(db, 'notifications', json.dumps(value, sort_keys=True))
        return self.notifications()

    def deliveries(self, limit=50, offset=0):
        """The delivery log: queued, sent or failed with an error category; never message text or secrets."""
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise MonitorError('Invalid pagination')
        with self._lock:
            return [dict(row) for row in self._db.execute(
                '''SELECT deliveries.id,deliveries.incident_id,kind,channel,deliveries.status,attempts,error,created_at,
                   deliveries.updated_at,incidents.device_name,incidents.rule FROM deliveries
                   LEFT JOIN incidents ON incidents.id=deliveries.incident_id
                   ORDER BY deliveries.id DESC LIMIT ? OFFSET ?''', (limit, offset))]

    def _message(self, db, row, settings):
        language = settings['language']
        if row['kind'] == 'test':
            return notify.compose_test(self.server_name, language)
        if row['kind'] == 'summary':
            counts = dict.fromkeys(ACTIVE, 0)
            counts.update({state: number for state, number in db.execute(
                f'SELECT state,COUNT(*) FROM incidents WHERE state IN {ACTIVE} GROUP BY state')})    # nosec B608
            day = _stamp(self.clock() - dt.timedelta(days=1))
            counts['new'] = db.execute('SELECT COUNT(*) FROM incidents WHERE opened_at>=?', (day,)).fetchone()[0]
            counts['resolved'] = db.execute("SELECT COUNT(*) FROM incidents WHERE state='resolved' AND resolved_at>=?",
                                            (day,)).fetchone()[0]
            items = db.execute("SELECT * FROM incidents WHERE state IN ('open','acknowledged') ORDER BY started_at LIMIT 20")
            return notify.compose_summary(counts, [dict(item) for item in items], self.server_name, language)
        incident = db.execute('SELECT * FROM incidents WHERE id=?', (row['incident_id'],)).fetchone()
        return notify.compose(row['kind'], dict(incident), self.server_name, language) if incident else None

    def _send(self, channel, settings, secrets, message):
        """Deliver one message; returns None, or a secret-free error category."""
        subject, body, payload = message
        if secrets.get('unreadable'):
            return 'config'
        try:
            if channel == 'email':
                if not (settings['smtp_host'] and settings['sender'] and settings['recipients']):
                    return 'config'
                self.transport.send_email(settings, secrets.get('smtp_password', ''), subject, body)
            else:
                if not secrets.get('webhook_url'):
                    return 'config'
                self.transport.post_webhook(secrets['webhook_url'], payload)
        except Exception as error:      # every failure becomes a category; the worker never stops
            return notify.category(error)
        return None

    def deliver(self, now=None):
        """Send what is due, retrying after BACKOFF. Returns the number of attempts made."""
        now = now or self.clock()
        attempts = 0
        with self._delivering:
            while attempts < 50:
                with self._transaction() as db:
                    row = db.execute("SELECT * FROM deliveries WHERE status='queued' AND next_at<=? ORDER BY id LIMIT 1",
                                     (_stamp(now),)).fetchone()
                    if not row:
                        break
                    db.execute("UPDATE deliveries SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?",
                               (_stamp(now), row['id']))
                    settings = self._notification_settings(db)
                    message = self._message(db, row, settings)
                error = self._send(row['channel'], settings, self.secrets.load(), message) if message else 'gone'
                attempts += 1
                tried = row['attempts'] + 1
                with self._transaction() as db:
                    if error is None:
                        db.execute("UPDATE deliveries SET status='sent',error=NULL,updated_at=? WHERE id=?",
                                   (_stamp(now), row['id']))
                    elif tried > len(BACKOFF) or error in ('config', 'gone'):
                        db.execute("UPDATE deliveries SET status='failed',error=?,updated_at=? WHERE id=?",
                                   (error, _stamp(now), row['id']))
                    else:
                        db.execute("UPDATE deliveries SET status='queued',error=?,next_at=?,updated_at=? WHERE id=?",
                                   (error, _stamp(now + dt.timedelta(seconds=BACKOFF[tried - 1])), _stamp(now), row['id']))
        return attempts

    def test_notification(self, channel):
        """Send a test message now. It never creates an incident; the result is also in the delivery log."""
        if channel not in ('email', 'webhook'):
            raise MonitorError('Invalid channel')
        now = self.clock()
        with self._transaction() as db:
            settings = self._notification_settings(db)
            identifier = db.execute('''INSERT INTO deliveries(incident_id,kind,channel,ref,status,attempts,next_at,created_at,updated_at)
                VALUES (NULL,'test',?,?,'sending',1,?,?,?)''', (channel, str(uuid.uuid4()), _stamp(now), _stamp(now),
                                                              _stamp(now))).lastrowid
        error = self._send(channel, settings, self.secrets.load(), notify.compose_test(self.server_name, settings['language']))
        with self._transaction() as db:
            db.execute('UPDATE deliveries SET status=?,error=?,updated_at=? WHERE id=?',
                       ('failed' if error else 'sent', error, _stamp(self.clock()), identifier))
        return dict(channel=channel, status='failed' if error else 'sent', error=error)

    def _summary(self, db, now):
        """The optional daily summary, once per server-local day, after the chosen hour."""
        settings = self._notification_settings(db)
        local = now.astimezone()
        if settings['summary_enabled'] and local.hour >= settings['summary_hour']:
            self._notify(db, None, 'summary', now, ref=local.date().isoformat())

    def _step(self, db, device, name, rule, row, verdict, since, value, paused, now):
        """Move one (computer, rule) through pending -> open or suppressed -> resolved: one incident per problem."""
        if verdict in ('missing', 'idle'):
            if row and row['state'] == 'pending':
                db.execute('DELETE FROM incidents WHERE id=?', (row['id'],))
            return
        if verdict == 'na':
            if row:
                self._resolve(db, row, now, 'not-monitored')
            return
        if verdict == 'clear':
            if not row:
                return
            if row['state'] in ('pending', 'suppressed'):
                self._resolve(db, row, now, 'recovered')
            elif row['clear_since'] is None:
                db.execute('UPDATE incidents SET clear_since=?,updated_at=? WHERE id=?', (_stamp(now), _stamp(now), row['id']))
            elif (now - _time(row['clear_since'])).total_seconds() >= RECOVER_SECONDS:
                self._resolve(db, row, now, 'recovered', _time(row['clear_since']))
            return
        if not row:
            began = min(since or now, now)
            cursor = db.execute('''INSERT INTO incidents(device_id,rule,cause,state,started_at,value,device_name,group_name,updated_at)
                VALUES (?,?,?,'pending',?,?,?,?,?)''', (device['device_id'], name, name, _stamp(began), value,
                                                       device.get('name', ''), device.get('group', ''), _stamp(now)))
            self._event(db, cursor.lastrowid, 'pending', began)
            row = db.execute('SELECT * FROM incidents WHERE id=?', (cursor.lastrowid,)).fetchone()
        else:
            db.execute('UPDATE incidents SET value=?,clear_since=NULL,device_name=?,group_name=?,updated_at=? WHERE id=?',
                       (value, device.get('name', ''), device.get('group', ''), _stamp(now), row['id']))
            if row['clear_since'] is not None:
                self._event(db, row['id'], 'returned', now)
        sustain = 0 if name == 'no_report' else rule['minutes'] * 60
        if row['state'] in ('pending', 'suppressed') and (now - _time(row['started_at'])).total_seconds() >= sustain:
            if paused:
                if row['state'] == 'pending':
                    db.execute("UPDATE incidents SET state='suppressed',updated_at=? WHERE id=?", (_stamp(now), row['id']))
                    self._event(db, row['id'], 'suppressed', now)
            else:
                db.execute("UPDATE incidents SET state='open',opened_at=?,notified=1,updated_at=? WHERE id=?",
                           (_stamp(now), _stamp(now), row['id']))
                self._event(db, row['id'], 'opened', now)
                self._notify(db, row['id'], 'open', now)

    def acknowledge(self, incident_id, by):
        """Someone has seen an open incident: it stays until the problem clears, and says who and when."""
        if type(incident_id) is not int:
            raise MonitorError('Invalid incident')
        if not isinstance(by, str) or not 1 <= len(by.strip()) <= 64 or any(ord(c) < 32 for c in by):
            raise MonitorError('Say who acknowledges')
        now = self.clock()
        with self._transaction() as db:
            row = db.execute('SELECT state FROM incidents WHERE id=?', (incident_id,)).fetchone()
            if not row:
                raise MonitorError('Unknown incident')
            if row['state'] == 'open':
                db.execute("UPDATE incidents SET state='acknowledged',acknowledged_by=?,acknowledged_at=?,updated_at=? WHERE id=?",
                           (by.strip(), _stamp(now), _stamp(now), incident_id))
                self._event(db, incident_id, 'acknowledged', now, by.strip())
            elif row['state'] != 'acknowledged':
                raise MonitorError('Only an open incident can be acknowledged')
        return self.incident(incident_id)

    def incident(self, incident_id):
        if type(incident_id) is not int:
            raise MonitorError('Invalid incident')
        with self._lock:
            row = self._db.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()
            if not row:
                raise MonitorError('Unknown incident')
            result = dict(row)
            result['timeline'] = [dict(event) for event in self._db.execute(
                'SELECT time,kind,detail FROM timeline WHERE incident_id=? ORDER BY id LIMIT 200', (incident_id,))]
            result['deliveries'] = [dict(item) for item in self._db.execute(
                '''SELECT kind,channel,status,attempts,error,updated_at FROM deliveries WHERE incident_id=?
                   ORDER BY id LIMIT 50''', (incident_id,))]
            return result

    def summary(self):
        """Counts for the header and Overview badges."""
        with self._lock:
            counts = dict.fromkeys(ACTIVE, 0)
            counts.update({row[0]: row[1] for row in self._db.execute(
                f'SELECT state,COUNT(*) FROM incidents WHERE state IN {ACTIVE} GROUP BY state')})    # nosec B608
            paused = sum(1 for item in self.status.values() if item.get('paused'))
            missing = sum(1 for item in self.status.values() if item.get('missing'))
        return dict(counts, active=counts['open'] + counts['acknowledged'], paused=paused, telemetry_missing=missing)

    def device_alerts(self, device_id):
        """The device detail's incident history and current monitoring state."""
        with self._lock:
            status = dict(self.status.get(str(device_id)) or {})
        return dict(incidents=self.incidents(device_id=str(device_id), limit=20), status=status or None)

    def incidents(self, state='', device_id='', limit=100, offset=0):
        """Newest first. ``state`` is one state, 'active' (everything not resolved) or '' for all."""
        if state not in ('', 'active', *ACTIVE, 'resolved') or not isinstance(device_id, str) or len(device_id) > 64:
            raise MonitorError('Invalid incident filter')
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise MonitorError('Invalid pagination')
        where, parameters = [], []
        if state == 'active':
            where.append(f"state IN {ACTIVE} AND state!='pending'")
        elif state:
            where.append('state=?')
            parameters.append(state)
        if device_id:
            where.append('device_id=?')
            parameters.append(device_id)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self._lock:
            return [dict(row) for row in self._db.execute(
                f'SELECT * FROM incidents{clause} ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?',   # nosec B608
                (*parameters, limit, offset))]

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
