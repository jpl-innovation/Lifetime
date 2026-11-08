"""Explicitly initialized machine accounting; never reads a user's ledger implicitly.

MachineAgent.initialize(directory) creates a restricted SQLite WAL database.
MachineAgent(directory) only opens an existing one. sample(), import_ledger(),
set_policy(), ledger(), status(), batch() and run() are the public local API.
Credentials and enrollment recovery material stay in SQLite, never status.json.
"""
import argparse
import contextlib
import copy
import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import uuid

import lifetime_ledger as schema
import lifetime_protocol as protocol
import lifetime_tracker as tracker
from lifetime_permissions import windows_acl as _windows_acl, protect_directory

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4C544147
DEFAULT_MAX_RECORDS = 10000
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
MAX_IMPORT_BYTES = 16 * 1024 * 1024
DATABASE = 'machine.sqlite'
DEFAULT_POLICY = {'history': False, 'resources': False}


def machine_directory():
    """Machine scope only; ignore APPDATA, home and LIFETIME_DATA_DIR."""
    if sys.platform == 'win32':
        return Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'Lifetime' / 'Agent'
    return Path('/var/lib/lifetime-agent')


def status_path(directory=None):
    """Readable without opening private SQLite; this function performs no I/O."""
    root = machine_directory()
    if directory is None or Path(directory).absolute() == root.absolute():
        return root.parent / ('Agent-status.json' if sys.platform == 'win32' else 'lifetime-agent-status.json')
    return Path(directory) / 'status.json'


def protect_root(directory):
    """No bypass: restrict this dedicated directory before creating any data."""
    protect_directory(directory)


def validate_policy(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY)
            or any(type(v) is not bool for v in value.values())):
        raise ValueError('Policy must contain explicit history/resources booleans')
    return dict(value)


def _empty_ledger():
    # Unlike tracker.new_ledger(), this does not inspect OS dates without consent.
    return dict(total_seconds=0.0, baseline_seconds=0.0, first_seen=None,
                first_seen_source='unknown', boots=0, current_session_seconds=0.0,
                longest_session_seconds=0.0, last_update=None, boot_id=None,
                last_clock=None, tracking_started=None, boot_credited_for=None,
                boot_accounted_seconds=0.0, daily={}, hourly=[0.0]*24, sessions=[],
                history_started=None, unattributed_seconds=0.0, recording_started=None,
                coverage_gaps=[], clock_note_count=0)


def _totals(ledger):
    imported = ledger.get('imported', {}).get('seconds', 0.0)
    return dict(measured_seconds=ledger['total_seconds']-imported,
                imported_seconds=imported, baseline_seconds=ledger['baseline_seconds'])


def _scrub_ledger(ledger):
    for key in ('first_seen', 'tracking_started', 'history_started', 'recording_started', 'first_sample'):
        ledger.pop(key, None)
    ledger.update(first_seen=None, tracking_started=None, history_started=None,
                  recording_started=None, first_seen_source='unknown', daily={},
                  dated_hourly={}, hourly=[0.0]*24, sessions=[], coverage_gaps=[])
    for key in ('daily_allocation', 'daily_repair', 'daily_repair_version'):
        ledger.pop(key, None)
    if 'imported' in ledger:
        ledger['imported'] = {'seconds': ledger['imported'].get('seconds', 0.0)}


def _history(ledger):
    def date_stamp(value):
        return dt.datetime.fromisoformat(value).astimezone().isoformat() if value else None
    result = dict(daily=dict(sorted(ledger['daily'].items())[-3660:]),
                  imported_daily=dict(sorted(ledger.get('imported', {}).get('daily', {}).items())[-3660:]),
                  first_seen=date_stamp(ledger.get('first_seen')),
                  first_seen_source=ledger.get('first_seen_source', 'unknown'),
                  recording_started=ledger.get('recording_started'),
                  daily_estimated=bool(ledger.get('daily_allocation', {}).get('estimated', False)))
    return result


def _resources():
    # No host, user, network, serial number or hardware identifier is inspected.
    import platform
    return {key: getattr(platform, key)() for key in ('system', 'release', 'machine', 'python_version')}


def _stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class MachineAgent:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else machine_directory()
        path = self.directory / DATABASE
        try:
            protect_root(self.directory)
        except FileNotFoundError:
            raise FileNotFoundError('Explicitly initialize or enroll the machine agent first') from None
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            raise FileNotFoundError('Explicitly initialize or enroll the machine agent first')
        if sys.platform != 'win32':
            path.chmod(0o600)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        try:
            if (self._db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or self._db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
                raise ValueError('Unsupported machine database')
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('PRAGMA secure_delete=ON')
            self._db.execute('PRAGMA wal_autocheckpoint=64')
            self._db.execute('PRAGMA journal_size_limit=1048576')
            state = self._load()
            protocol.identifier(state['device_id'])
            validate_policy(state['policy'])
            schema.validate(state['ledger'])
        except BaseException:
            self._db.close()
            raise

    @classmethod
    def initialize(cls, directory=None, *, max_records=DEFAULT_MAX_RECORDS, max_bytes=DEFAULT_MAX_BYTES):
        if (type(max_records) is not int or not 1 <= max_records <= 100000
                or type(max_bytes) is not int or not 1 <= max_bytes <= 1024*1024*1024):
            raise ValueError('Invalid outbox limits')
        root = Path(directory) if directory is not None else machine_directory()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        protect_root(root)
        path = root / DATABASE
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value TEXT NOT NULL)')
            db.execute('''CREATE TABLE outbox (seq INTEGER PRIMARY KEY, record TEXT NOT NULL,
                          history TEXT, readings TEXT, size INTEGER NOT NULL)''')
            state = dict(device_id=str(uuid.uuid4()), next_seq=1, ledger=_empty_ledger(),
                         policy=dict(DEFAULT_POLICY), policy_version=1, sampled=False,
                         import_marker=None, enabled=False, phase='disabled', bundle=None,
                         enrollment=None, enrollment_sent=False, credential=None,
                         readings=None, last_sync=None, status='disabled', interval=30,
                         failures=0, next_attempt=0, dropped=0,
                         max_records=max_records, max_bytes=max_bytes)
            db.execute('INSERT INTO state VALUES (1,?)', (protocol.canonical(state).decode(),))
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.commit()
        except BaseException:
            db.rollback()
            db.close()
            path.unlink()
            raise
        else:
            db.close()
        agent = cls(root)
        agent.write_status()
        return agent

    def _load(self):
        return json.loads(self._db.execute('SELECT value FROM state WHERE singleton=1').fetchone()[0])

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                state = self._load()
                yield state
                self._db.execute('UPDATE state SET value=? WHERE singleton=1', (protocol.canonical(state).decode(),))
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @contextlib.contextmanager
    def reporting_lock(self):
        """Serialize network and consent changes across UI/agent processes too."""
        with self._lock:
            while True:
                try:
                    lock = tracker.file_lock(self.directory, 'reporting.lock')
                    lock.__enter__()
                except OSError as error:
                    winerror = getattr(error, 'winerror', None)
                    busy = (winerror in (33, 36) if winerror is not None else
                            error.errno in (errno.EAGAIN, errno.EDEADLK))
                    if sys.platform == 'win32' and error.errno == errno.EACCES and winerror is None:
                        busy = (self.directory / 'reporting.lock').is_file()
                    if not busy:
                        raise
                    time.sleep(0.05)
                    continue
                try:
                    yield
                finally:
                    lock.__exit__(None, None, None)
                break

    @property
    def device_id(self):
        with self._lock:
            return self._load()['device_id']

    def ledger(self):
        with self._lock:
            return self._load()['ledger']

    def status(self):
        with self._lock:
            state = self._load()
            return dict(enabled=state['enabled'], server=state['bundle']['address'] if state['bundle'] else None,
                        status=state['status'], last_sync=state['last_sync'],
                        pending=self._db.execute('SELECT count(*) FROM outbox').fetchone()[0],
                        dropped=state['dropped'])

    def write_status(self):
        with self._lock:
            path = status_path(self.directory)
            if path.parent == self.directory:
                tracker.atomic_json(path, self.status())
                return
            # The public directory allows traversal/read, never Users write.
            if path.parent.is_symlink() or not path.parent.is_dir():
                raise ValueError('Unsafe public status directory')
            if sys.platform == 'win32':
                _windows_acl(path.parent, 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;;FRFX;;;BU)',
                             ['*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)', '*S-1-5-32-545:(RX)'])
            descriptor, temporary = tempfile.mkstemp(prefix='Agent-status.', dir=path.parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    if sys.platform == 'win32':
                        _windows_acl(temporary, 'D:P(A;;FA;;;BA)(A;;FA;;;SY)(A;;FR;;;BU)',
                                     ['*S-1-5-32-544:(F)', '*S-1-5-18:(F)', '*S-1-5-32-545:(R)'])
                    else:
                        os.fchmod(output.fileno(), 0o644)
                    output.write(protocol.canonical(self.status()))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def _set_policy(self, state, policy):
        policy = validate_policy(policy)
        if state['policy'] != policy:
            state['policy_version'] = protocol.number(state['policy_version']+1, True)
            state['policy'] = policy
        if not policy['history']:
            _scrub_ledger(state['ledger'])
            self._db.execute('UPDATE outbox SET history=NULL')
        if not policy['resources']:
            state['readings'] = None
            self._db.execute('UPDATE outbox SET readings=NULL')
        # Update the byte accounting after removing optional snapshots.
        self._db.execute('UPDATE outbox SET size=length(CAST(record AS BLOB))+'
                         'coalesce(length(CAST(history AS BLOB)),0)+coalesce(length(CAST(readings AS BLOB)),0)')

    def set_policy(self, policy):
        with self.reporting_lock():
            with self._transaction() as state:
                self._set_policy(state, policy)
            # Remove old optional bytes from the WAL after committing a revocation.
            self._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.write_status()

    def disable_reporting(self):
        """Stop requests locally and scrub optional data, retaining core accounting.

        Previously transmitted server data requires server administration/retention;
        disabling while offline cannot remove it from the remote server.
        """
        with self.reporting_lock():
            with self._transaction() as state:
                self._set_policy(state, dict(DEFAULT_POLICY))
                state.update(enabled=False, phase='disabled', status='disabled', credential=None,
                             enrollment=None, enrollment_sent=False, next_attempt=0, failures=0)
                if state['bundle'] is not None:
                    # Keep only public trust/address material, not the old pairing code.
                    state['bundle']['code'] = 'disabled'
            self._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.write_status()
        return self.status()

    def _append(self, state, measured, imported, observed, history=None, readings=None):
        seq = state['next_seq']
        record = dict(seq=seq, observed_at=observed, awake_seconds=measured+imported,
                      measured_seconds=measured, imported_seconds=imported, totals=_totals(state['ledger']))
        message = dict(version=1, device_id=state['device_id'], policy_version=state['policy_version'],
                       policy=state['policy'], records=[record])
        if history is not None:
            message['history'] = history
        if readings is not None:
            message['readings'] = readings
        protocol.validate_sync(message)
        if len(json.dumps(message, ensure_ascii=True, separators=(',', ':')).encode()) > protocol.MAX_BYTES:
            raise ValueError('Optional snapshot exceeds transport limit')
        columns = [protocol.canonical(v).decode() if v is not None else None
                   for v in (record, history, readings)]
        size = sum(len(v.encode()) for v in columns if v is not None)
        self._db.execute('INSERT INTO outbox VALUES (?,?,?,?,?)', (seq, *columns, size))
        state['next_seq'] = protocol.number(seq+1, True)
        count, total = self._db.execute('SELECT count(*),coalesce(sum(size),0) FROM outbox').fetchone()
        while count > state['max_records'] or total > state['max_bytes']:
            oldest, size = self._db.execute('SELECT seq,size FROM outbox ORDER BY seq LIMIT 1').fetchone()
            self._db.execute('DELETE FROM outbox WHERE seq=?', (oldest,))
            count -= 1
            total -= size
            state['dropped'] += 1
        return record

    def sample(self, tick=None, boot=None, timestamp=None, *, history_sampler=None, resources_sampler=None,
               boot_evidence=None):
        tick = tracker.awake_clock() if tick is None else tick
        boot = tracker.boot_id() if boot is None else boot
        if not isinstance(boot, str) or not boot or boot == 'unknown' or boot.startswith('session:'):
            raise ValueError('A known boot identity is required')
        protocol.number(tick)
        timestamp = timestamp or _stamp()
        protocol.timestamp(timestamp)
        with self._transaction() as state:
            ledger = state['ledger']
            before = ledger['total_seconds']
            tracker.sample(ledger, tick, boot, timestamp, history=state['policy']['history'],
                           boot_evidence=boot_evidence)
            history = (history_sampler or _history)(copy.deepcopy(ledger)) if state['policy']['history'] else None
            readings = None
            if state['policy']['resources']:
                readings = dict((resources_sampler or _resources)(), observed_at=timestamp)
            else:
                state['readings'] = None
            state['readings'] = readings
            if not state['policy']['history']:
                _scrub_ledger(ledger)
            schema.validate(ledger)
            record = self._append(state, ledger['total_seconds']-before, 0, timestamp, history, readings)
            state['sampled'] = True
        self.write_status()
        return record

    def import_ledger(self, path, *, tick=None, boot=None, timestamp=None):
        """Explicit one-time transfer, before sampling, preserving the boot boundary.

        Exact repeats of the same path and bytes are no-ops, even after sampling.
        Different files, changed bytes and transfers after sampling are refused.
        The original file is read-only; its unknown extensions are never retained.
        """
        path = Path(path)
        with path.open('rb') as source:
            raw = source.read(MAX_IMPORT_BYTES+1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise ValueError('Import ledger is too large')
        marker = dict(content=hashlib.sha256(raw).hexdigest(),
                      path=hashlib.sha256(str(path.resolve()).encode()).hexdigest())
        with self._transaction() as state:
            if state['import_marker'] == marker:
                return False
            if state['import_marker'] is not None or state['sampled']:
                raise ValueError('Machine history already started; another import is refused')
            value = protocol.decode(raw.decode('utf-8-sig').encode('utf-8'))
            schema.validate(value)
            identity, clock = value.get('boot_id'), value.get('last_clock')
            if (not isinstance(identity, str) or not identity or identity == 'unknown'
                    or identity.startswith('session:') or not schema.is_number(clock)):
                raise ValueError('Import requires a known boot identity and last awake clock')
            current_boot = tracker.boot_id() if boot is None else boot
            current_tick = tracker.awake_clock() if tick is None else tick
            protocol.number(current_tick)
            if not isinstance(current_boot, str) or not current_boot or current_boot == 'unknown':
                raise ValueError('Current boot identity is unavailable')
            if identity == current_boot and clock > current_tick:
                raise ValueError('Imported same-boot clock is ahead of this machine')
            ledger = _empty_ledger()
            allowed = set(ledger) | {'imported', 'dated_hourly', 'daily_allocation', 'first_sample'}
            ledger.update({k: v for k, v in value.items() if k in allowed})
            ledger['boot_accounted_seconds'] = value.get('boot_accounted_seconds', min(clock, ledger['total_seconds']))
            if ledger['boot_accounted_seconds'] > clock or ledger.get('boot_credited_for') not in (None, identity):
                raise ValueError('Imported boot accounting is inconsistent')
            if not ledger.get('last_update'):
                raise ValueError('Imported boot boundary requires its last sample time')
            ledger['last_update'] = dt.datetime.fromisoformat(ledger['last_update'].replace('Z', '+00:00')).astimezone().isoformat()
            if 'imported' in ledger:
                ledger['imported'] = {k: v for k, v in ledger['imported'].items() if k in ('seconds', 'daily')}
            if not state['policy']['history']:
                _scrub_ledger(ledger)
            schema.validate(ledger)
            state['ledger'] = ledger
            stock = _totals(ledger)
            observed = timestamp or _stamp()
            self._append(state, stock['measured_seconds'], stock['imported_seconds'], observed,
                         _history(ledger) if state['policy']['history'] else None)
            state['import_marker'] = marker
        self.write_status()
        return True

    def batch(self):
        """Return at most 100 immutable records with currently permitted snapshots."""
        with self._lock:
            state = self._load()
            message = dict(version=1, device_id=state['device_id'], policy_version=state['policy_version'],
                           policy=state['policy'], records=[])
            rows = self._db.execute('SELECT record,history,readings FROM outbox ORDER BY seq LIMIT ?',
                                    (protocol.MAX_RECORDS,)).fetchall()
            for record, history, readings in rows:
                candidate = copy.deepcopy(message)
                candidate['records'].append(json.loads(record))
                for category, permission, raw in (('history', 'history', history), ('readings', 'resources', readings)):
                    candidate.pop(category, None)
                    if state['policy'][permission] and raw is not None:
                        candidate[category] = json.loads(raw)
                if len(json.dumps(candidate, ensure_ascii=True, separators=(',', ':')).encode()) > protocol.MAX_BYTES:
                    break
                message = candidate
            if not rows:
                if state['policy']['history']:
                    message['history'] = _history(state['ledger'])
                if state['policy']['resources'] and state['readings'] is not None:
                    message['readings'] = state['readings']
            return protocol.validate_sync(message)

    def run(self, stop_event=None, client=None):
        """Hold a nonblocking process lock for the entire capture/sync loop."""
        from lifetime_client import SyncClient
        stop = stop_event or threading.Event()
        with tracker.file_lock(self.directory, 'agent.lock', blocking=False):
            client = client or SyncClient(self)
            while not stop.is_set():
                self.sample()
                client.step()
                with self._lock:
                    state = self._load()
                wait = tracker.INTERVAL
                if state['enabled']:
                    wait = min(wait, max(1, state['next_attempt']-time.time()))
                stop.wait(wait)

    def close(self):
        with self._lock:
            self._db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit machine capture and company reporting; installs nothing')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--enroll-bundle-file', type=Path)
    parser.add_argument('--policy-history', action='store_true')
    parser.add_argument('--policy-resources', action='store_true')
    parser.add_argument('--import-ledger', type=Path)
    args = parser.parse_args(argv)
    if (args.policy_history or args.policy_resources) and not args.enroll_bundle_file:
        parser.error('Optional reporting permissions require explicit enrollment')
    directory = args.directory if args.directory is not None else machine_directory()
    bundle = None
    if args.enroll_bundle_file:
        import lifetime_tls as tls
        with args.enroll_bundle_file.open(encoding='utf-8-sig') as source:
            bundle = source.read(tls.MAX_BUNDLE_BYTES*2+1).strip()
        tls.decode_bundle(bundle)
    agent = (MachineAgent.initialize(directory) if args.initialize or
             (bundle is not None and not (directory / DATABASE).exists()) else MachineAgent(directory))
    try:
        policy = dict(history=args.policy_history, resources=args.policy_resources)
        if bundle is not None:
            agent.set_policy(policy)
        if args.import_ledger:
            agent.import_ledger(args.import_ledger)
        if bundle is not None:
            from lifetime_client import SyncClient
            SyncClient(agent).start_enrollment(bundle, {}, policy)
        if args.run:
            agent.run()
        return agent.status()
    finally:
        agent.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
