"""Who may do what on Lifetime Server, and the tamper-evident record of what was done.

Roles: admin (everything), operator (view; acknowledge and suppress incidents; refresh; remote power for one computer
at a time where its owner allowed it), viewer (read only) and auditor (read, plus the audit trail). Every admin
operation must appear in exactly one class below; an operation that is in none is refused to everyone, Admin included
(default deny), and the tests fail until it is classified.

Identity comes from Windows (lifetime_pipe): SYSTEM and accounts whose Administrators group is enabled (elevated) are
always Admin, so nobody can lock the server out of its own administrators. Other accounts and groups get a role only by
assignment. The loopback HTTP channel with ``admin-token`` cannot tell who is calling: whoever can read the token is
treated as Admin and recorded as ``admin-token``.

The audit trail is an append-only table in ``access.sqlite``: every state-changing action and every remote command,
with the actor's SID and name, role, action, target, result, UTC time and a correlation id. Each row carries the hash of
the previous row, so an edited, removed or reordered row breaks the chain and ``verify`` names the first broken link.
This makes tampering detectable, not impossible: a local administrator can still rewrite the file and recompute every
hash. Compare against exported copies and archives kept elsewhere for stronger evidence.
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading

import lifetime_permissions as permissions

ROLES = ('admin', 'operator', 'auditor', 'viewer')
SYSTEM, ADMINISTRATORS = 'S-1-5-18', 'S-1-5-32-544'
TOKEN_ACTOR = dict(sid=None, name='admin-token', groups=[], channel='token')

READ = frozenset({
    'whoami', 'live', 'overview', 'devices', 'device', 'configuration', 'invitations', 'fleet', 'server_info',
    'pending_enrollments', 'server_log', 'presence', 'alert_rules', 'incidents', 'incident', 'schedules',
    'maintenance', 'alert_summary', 'notifications', 'deliveries', 'time_zones', 'availability', 'coverage_floor',
    'checks', 'check_scopes', 'updates', 'insights'})
AUDIT_READ = frozenset({'audit', 'verify_audit'})
OPERATE = frozenset({'acknowledge', 'add_maintenance', 'delete_maintenance', 'refresh', 'send_command',
                     'cancel_command', 'wake'})
ADMINISTER = frozenset({
    'code', 'approve', 'deny', 'revoke', 'delete', 'identity', 'retain', 'backup', 'restore', 'set_configuration',
    'invitation', 'revoke_invitation', 'organisation', 'names', 'set_alert_rule', 'set_schedule', 'delete_schedule',
    'set_notifications', 'test_notification', 'set_coverage_floor', 'save_check', 'delete_check', 'set_check_scopes',
    'roles', 'set_role', 'delete_role', 'backup_archive', 'verify_backup', 'stage_restore',
    'upload_update', 'set_update', 'rollback_update', 'set_insight_settings'})
PERMISSIONS = {'viewer': READ, 'auditor': READ | AUDIT_READ, 'operator': READ | OPERATE,
               'admin': READ | AUDIT_READ | OPERATE | ADMINISTER}
POWER = frozenset({'send_command', 'wake'})        # 'refresh' as a send_command action is not power
TARGET_FIELDS = ('device_id', 'request_id', 'incident_id', 'command_id', 'invitation_id', 'window_id', 'check_id',
                 'rule', 'scope', 'channel', 'principal', 'sid', 'path')
GENESIS = '0' * 64
SCHEMA_VERSION, APPLICATION_ID = 1, 0x4C544143      # 'LTAC'
MAX_ROWS, SEGMENT = 200_000, 20_000
FIELDS = ('seq', 'at', 'actor_sid', 'actor_name', 'role', 'action', 'target', 'result', 'correlation_id', 'prev_hash')


class AccessError(ValueError):
    """Invalid role assignment or access database."""


def state_changing(operation):
    return operation not in READ and operation not in AUDIT_READ


def builtin_admin(identity):
    return identity.get('sid') == SYSTEM or ADMINISTRATORS in identity.get('groups', ())


def operations(roles):
    allowed = set()
    for role in roles:
        allowed |= PERMISSIONS.get(role, frozenset())
    return allowed


def role_label(roles):
    return next((role for role in ROLES if role in roles), 'none') if len(roles) < 2 else ','.join(r for r in ROLES if r in roles)


def target_of(arguments):
    """What an action was about, from a fixed list of identifier fields (never secrets or free text)."""
    for field in TARGET_FIELDS:
        value = arguments.get(field) if isinstance(arguments, dict) else None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return f'{field}={str(value)[:200]}'
    return ''


def _hash(row):
    return hashlib.sha256(json.dumps([row[name] for name in FIELDS], ensure_ascii=False, separators=(',', ':'))
                          .encode('utf-8')).hexdigest()


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


def _migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise AccessError('Access database is newer than this server')
    db.execute('''CREATE TABLE IF NOT EXISTS roles (sid TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL
                  CHECK(kind IN ('user','group')), role TEXT NOT NULL CHECK(role IN ('admin','operator','auditor','viewer')),
                  granted_by TEXT NOT NULL, granted_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_sid TEXT,
                  actor_name TEXT NOT NULL, role TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
                  result TEXT NOT NULL, correlation_id TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL)''')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute(f'PRAGMA application_id={APPLICATION_ID}')
    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')


def verify_rows(rows, anchor_seq=0, anchor_hash=GENESIS, head=None):
    """Walk the chain in order. Returns (checked, first broken seq or None, reason)."""
    expected_seq, previous, checked = anchor_seq + 1, anchor_hash, 0
    for row in rows:
        if row['seq'] != expected_seq:
            return checked, expected_seq, 'missing'
        if row['prev_hash'] != previous:
            return checked, row['seq'], 'link'
        if _hash(row) != row['hash']:
            return checked, row['seq'], 'edited'
        previous, expected_seq, checked = row['hash'], expected_seq + 1, checked + 1
    if head is not None and (expected_seq - 1, previous) != head:
        return checked, expected_seq, 'truncated'
    return checked, None, None


class Access:
    """Role assignments and the audit trail in ``access.sqlite``; one writer (the server runtime)."""

    def __init__(self, path, archive=None, max_rows=MAX_ROWS, segment=SEGMENT):
        self.path = Path(path)
        self.archive = Path(archive) if archive else self.path.parent / 'audit-archive'
        self.max_rows, self.segment = max_rows, segment
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        with self._transaction() as db:
            _migrate(db)

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield self._db
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            self._db.execute('COMMIT')

    def close(self):
        with self._lock:
            self._db.close()

    # ---- roles ----

    def roles(self):
        with self._lock:
            return [dict(row) for row in self._db.execute('SELECT * FROM roles ORDER BY kind DESC, name')]

    def roles_for(self, identity):
        """Roles of a Windows identity: built-in Admin, or every role assigned to its user SID or enabled groups."""
        if identity.get('channel') == 'token' or builtin_admin(identity):
            return {'admin'}
        sids = {identity.get('sid'), *identity.get('groups', ())}
        return {row['role'] for row in self.roles() if row['sid'] in sids}

    def set_role(self, sid, name, kind, role, granted_by):
        if role not in ROLES or kind not in ('user', 'group') or not isinstance(sid, str) or not sid.startswith('S-1-'):
            raise AccessError('Invalid role assignment')
        if sid in (SYSTEM, ADMINISTRATORS):
            raise AccessError('SYSTEM and Administrators are always Admin')
        with self._transaction() as db:
            previous = db.execute('SELECT role FROM roles WHERE sid=?', (sid,)).fetchone()
            db.execute('INSERT OR REPLACE INTO roles VALUES (?,?,?,?,?,?)', (sid, str(name)[:256], kind, role,
                                                                           str(granted_by)[:256], _now()))
            return previous['role'] if previous else None

    def delete_role(self, sid):
        with self._transaction() as db:
            previous = db.execute('SELECT role FROM roles WHERE sid=?', (sid,)).fetchone()
            if not previous:
                raise AccessError('No such role assignment')
            db.execute('DELETE FROM roles WHERE sid=?', (sid,))
            return previous['role']

    def pipe_descriptor(self, server_sid):
        """SDDL for the admin pipe: no network logons; full control for SYSTEM, Administrators and the server's own
        account; read/write (never create-instance) for each assigned account or group."""
        grants = ''.join(f'(A;;0x12019b;;;{row["sid"]})' for row in self.roles())
        own = f'(A;;FA;;;{server_sid})' if server_sid not in (SYSTEM, ADMINISTRATORS) else ''
        return f'D:P(D;;FA;;;NU)(A;;FA;;;SY)(A;;FA;;;BA){own}{grants}'

    # ---- audit ----

    def record(self, actor, roles, action, target='', result='ok', correlation_id=''):
        with self._transaction() as db:
            last = db.execute('SELECT seq,hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            if last:
                seq, previous = last['seq'] + 1, last['hash']
            else:
                anchor = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('anchor_seq','anchor_hash')").fetchall())
                seq, previous = int(anchor.get('anchor_seq', 0)) + 1, anchor.get('anchor_hash', GENESIS)
            row = dict(seq=seq, at=_now(), actor_sid=actor.get('sid'), actor_name=str(actor.get('name') or '')[:256],
                       role=role_label(roles), action=str(action)[:64], target=str(target)[:256],
                       result=str(result)[:64], correlation_id=str(correlation_id)[:64], prev_hash=previous)
            row['hash'] = _hash(row)
            db.execute('INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?)', tuple(row[name] for name in (*FIELDS, 'hash')))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('head', ?)", (json.dumps([seq, row['hash']]),))
        self._prune()
        return row

    def audit(self, limit=100, before=None):
        if type(limit) is not int or not 1 <= limit <= 1000 or (before is not None and (type(before) is not int or before < 1)):
            raise AccessError('Invalid audit page')
        with self._lock:
            query, values = 'SELECT * FROM audit', []
            if before is not None:
                query, values = query + ' WHERE seq<?', [before]
            return [dict(row) for row in self._db.execute(query + ' ORDER BY seq DESC LIMIT ?', (*values, limit))]

    def recent(self, action, since):
        with self._lock:
            return [dict(row) for row in self._db.execute(
                "SELECT * FROM audit WHERE action=? AND result='ok' AND at>=? ORDER BY seq", (action, since))]

    def verify(self):
        with self._lock:
            return self._verify(self._db)

    @staticmethod
    def _verify(db):
        meta = dict(db.execute('SELECT key,value FROM meta').fetchall())
        head = tuple(json.loads(meta['head'])) if 'head' in meta else (int(meta.get('anchor_seq', 0)), meta.get('anchor_hash', GENESIS))
        rows = (dict(row) for row in db.execute('SELECT * FROM audit ORDER BY seq'))
        checked, broken, reason = verify_rows(rows, int(meta.get('anchor_seq', 0)), meta.get('anchor_hash', GENESIS), head)
        return dict(ok=broken is None, checked=checked, first_broken=broken, reason=reason,
                    archived_through=int(meta.get('anchor_seq', 0)))

    def _prune(self):
        """Keep at most ``max_rows``: the oldest ``segment`` goes to a protected archive file first, then is removed.
        The last archived row becomes the chain's anchor, so verification still covers the first remaining row."""
        with self._lock:
            if (self._db.execute('SELECT coalesce(max(seq) - min(seq) + 1, 0) FROM audit').fetchone()[0]
                    <= self.max_rows):        # rows are contiguous; a gap only makes this prune early
                return
            rows = [dict(row) for row in self._db.execute('SELECT * FROM audit ORDER BY seq LIMIT ?', (self.segment,))]
            self.archive.mkdir(exist_ok=True)
            target = self.archive / f'audit-{rows[0]["seq"]:012d}-{rows[-1]["seq"]:012d}.jsonl'
            descriptor, temporary = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=self.archive)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
                    permissions.protect_private(temporary)
                    for row in rows:
                        output.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            with self._transaction() as db:
                db.execute('DELETE FROM audit WHERE seq<=?', (rows[-1]['seq'],))
                db.execute("INSERT OR REPLACE INTO meta VALUES ('anchor_seq', ?)", (str(rows[-1]['seq']),))
                db.execute("INSERT OR REPLACE INTO meta VALUES ('anchor_hash', ?)", (rows[-1]['hash'],))

    # ---- backup ----

    def backup(self, path):
        with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as destination:
            with self._lock:
                self._db.backup(destination)

    @staticmethod
    def validate(path):
        """An intact access database of this schema whose audit chain verifies (read-only)."""
        try:
            with contextlib.closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.execute('PRAGMA trusted_schema=OFF')
                db.row_factory = sqlite3.Row
                if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                        or db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION
                        or db.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok'):
                    raise AccessError('Invalid access database')
                for row in db.execute('SELECT * FROM roles'):
                    if row['role'] not in ROLES or not row['sid'].startswith('S-1-'):
                        raise AccessError('Invalid role assignment')
                return Access._verify(db)
        except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as error:
            raise AccessError('Invalid access database') from error


CSV_COLUMNS = ('seq', 'at', 'actor_name', 'actor_sid', 'role', 'action', 'target', 'result', 'correlation_id',
               'prev_hash', 'hash')


def write_csv(rows, path):
    """Audit rows to a UTF-8 CSV (formula-safe, atomic), with the hashes so a copy can be checked later."""
    import csv
    from lifetime_reports import _atomic_output, _csv_safe

    def write(temporary):
        with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
            output = csv.writer(stream)
            output.writerow(CSV_COLUMNS)
            for row in sorted(rows, key=lambda item: item['seq']):
                output.writerow([_csv_safe('' if row.get(name) is None else row[name]) for name in CSV_COLUMNS])
    _atomic_output(Path(path), write)


def verify_directory(directory):
    """Offline check of a data directory's audit trail (the server may be running: SQLite reads are safe)."""
    path = Path(directory) / 'access.sqlite'
    if not path.is_file():
        raise AccessError('No audit trail in this directory')
    return Access.validate(path)
