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
import subprocess
import shutil
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


def reporting_directory():
    """Desktop reporting: Windows machine agent or opt-in Linux user agent.

    MachineAgent's privileged default is retained for explicit system installs.
    The Linux desktop and systemd user unit share this separate private ledger.
    """
    if sys.platform == 'win32':
        return machine_directory()
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))) / 'lifetime-agent'


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
    if (not isinstance(value, dict) or not set(DEFAULT_POLICY) <= set(value) or set(value) - (set(DEFAULT_POLICY) | {'health', 'control'})
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


def _resources(state=None):
    """Bounded inventory and live readings; mutable state caches six-hour specs."""
    import platform
    import socket
    import lifetime_signals as signals
    state = state if state is not None else {}
    def probe(name):
        return signals.safe(getattr(signals, name))()
    def text(value):
        return str(value).replace('\n', ' ').replace('\r', ' ')[:128] if value is not None else None
    cpu = probe('probe_cpu')
    ram = probe('probe_ram') or {}
    disks = probe('probe_disks')
    gpus = probe('probe_gpu')
    battery = probe('probe_battery') or {}
    capacity = state.get('battery_capacity') or {}
    if capacity.get('health') is not None:
        battery['wear'] = 100-capacity['health']
    if capacity.get('design') is not None and capacity.get('unit') == 'Wh':
        battery['design_wh'] = capacity['design']
    power = probe('probe_power') or {}
    network = probe('probe_network')
    wifi = probe('probe_wifi')
    counters = probe('probe_counters')
    system_path = os.environ.get('SystemDrive', 'C:') + '\\' if sys.platform == 'win32' else '/'
    disk = next((d for d in disks or [] if d.get('path') == system_path), {})
    now = time.time()
    if not state.get('specs') or not 0 <= now-state.get('specs_collected_at', 0) < 21600:
        identity = probe('probe_identity') or {}
        hardware = probe('probe_hardware') or {}
        specs = {k: text(hardware.get(k)) for k in ('manufacturer', 'model')}
        specs.update(cpu_name=text(identity.get('cpu')),
                     cpu_cores=int(hardware['cpu_cores']) if hardware.get('cpu_cores') is not None else None,
                     cpu_threads=int(hardware['cpu_threads']) if hardware.get('cpu_threads') is not None else None,
                     ram_bytes=ram.get('total'), gpu_names=[text(g['name']) for g in gpus or [] if g.get('name')][:8],
                     disks=[dict(name=text(d['path']), total_bytes=d.get('total'),
                                 free_bytes=d['total']-d['used'] if d.get('total') is not None and d.get('used') is not None else None)
                            for d in sorted(disks or [], key=lambda d: d.get('path') != system_path)[:8]],
                     os_name=text(signals.safe(platform.system)()), os_version=text(signals.safe(platform.release)()),
                     battery_design_wh=battery.get('design_wh'),
                     mac_addresses=[m.upper().replace('-', ':') for m in hardware.get('mac_addresses', []) or [] if m][:8],
                     hostname=text(signals.safe(socket.gethostname)()))
        state.update(specs=specs, specs_collected_at=now)
    wear = battery.get('wear')
    if capacity.get('design') is not None and capacity.get('unit') == 'Wh':
        state['specs']['battery_design_wh'] = capacity['design']
    temperatures = [g['temperature'] for g in gpus or [] if g.get('temperature') is not None]
    kinds = ' '.join(str(n.get('Type') or n.get('InterfaceDescription', '')) for n in network or []).lower()
    performance = dict(cpu_percent=signals.safe(signals.cpu_percent)(state.get('cpu_counter'), cpu),
                       ram_used_bytes=ram.get('used'),
                       disk_free_bytes=disk['total']-disk['used'] if disk.get('total') is not None and disk.get('used') is not None else None,
                       battery_percent=power.get('percent'),
                       charging=any(s.strip().lower() == 'charging' for s in power['state'].split(',')) if power.get('percent') is not None and power.get('state') else None,
                       battery_health_percent=max(0, min(100, 100-wear)) if wear is not None else None,
                       power_watts=battery.get('watts') or None,
                       network_kind=None if network is None else 'wifi' if 'wi-fi' in kinds else 'ethernet' if 'ethernet' in kinds else 'none',
                       wifi_signal_percent=next((w.get('signal') for w in wifi or [] if w.get('signal') is not None), None),
                       temperature_c=max(temperatures) if temperatures else None)
    state.update(cpu_counter=cpu, network_counters=counters)
    result = {key: text(signals.safe(getattr(platform, key))()) or ''
              for key in ('system', 'release', 'machine', 'python_version')}
    result.update(specs=state['specs'], performance=performance)
    return result


def execute_power(action, runner=subprocess.run):
    """Only the agent command path calls this, after its result is acknowledged."""
    if action not in ('sleep', 'restart', 'shutdown'):
        raise ValueError('Invalid power action')
    if sys.platform == 'win32':
        if action == 'sleep':
            args = ['rundll32.exe', 'powrprof.dll,SetSuspendState', '0,1,0']
        else:
            message = ('Lifetime: your IT technician restarted this computer. Save your work.' if action == 'restart'
                       else 'Lifetime: your IT technician shut down this computer. Save your work.')
            args = ['shutdown', '/r' if action == 'restart' else '/s', '/t', '60', '/c', message]
    elif action == 'sleep':
        args = ['systemctl', 'suspend']
    elif shutil.which('shutdown'):
        args = ['shutdown', '-r' if action == 'restart' else '-h', '+1']
    else:
        args = ['systemctl', 'reboot' if action == 'restart' else 'poweroff']
    return runner(args, check=True, timeout=15, capture_output=True,
                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


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

    def _reported(self):
        with self._lock:
            return bool(self._load().get('reported'))

    def status(self):
        with self._lock:
            state = self._load()
            return dict(reported=bool(state.get('reported')), enabled=state['enabled'], server=state['bundle']['address'] if state['bundle'] else None,
                        status=state['status'], last_sync=state['last_sync'],
                        pending=self._db.execute('SELECT count(*) FROM outbox').fetchone()[0],
                        dropped=state['dropped'], policy=dict(state['policy']),
                        server_name=state.get('server_name'), phase=state['phase'])

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
        if not policy.get('control'):
            pending = state.pop('pending_power', None)
            if pending:
                state['command_result'] = dict(id=pending['id'], status='declined', detail='Remote power consent is required')
        if not policy.get('health'):
            state.pop('health', None)
            state.pop('battery_capacity', None)
            state['ledger'].pop('health_capacity', None)
        if not policy['resources']:
            state['readings'] = None
            for key in ('specs', 'specs_collected_at', 'cpu_counter', 'network_counters'):
                state.pop(key, None)
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

    def _say_goodbye(self):
        """Tell the server we are leaving so it stops showing this computer as connected. Best effort, 2 s."""
        with self._lock:
            state = self._load()
        if not (state['enabled'] and state['phase'] == 'approved' and state.get('credential') and state['bundle']):
            return
        try:
            from lifetime_tls import request_json
            request_json(state['bundle'], '/v1/leave', dict(device_id=state['device_id']),
                         credential=state['credential'], timeout=2)
        except Exception:
            pass    # offline: the server will mark this computer offline by itself within seconds

    def disable_reporting(self):
        """Stop requests locally and scrub optional data, retaining core accounting.

        Previously transmitted server data requires server administration/retention;
        disabling while offline cannot remove it from the remote server.
        """
        with self.reporting_lock():
            self._say_goodbye()
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
        # Slow OS probes must not hold the writer lock or a SQLite transaction.
        # This also lets the command worker synchronize during a hardware probe.
        with self._lock:
            snapshot = self._load()
        health_data = None
        if snapshot['policy'].get('health'):
            import lifetime_health as health
            previous = snapshot.get('health')
            if previous is None or (protocol.timestamp(timestamp)-protocol.timestamp(previous['observed_at'])).total_seconds() >= 3600:
                health_data = health.collect({'completed': True, 'health': True}, details=False)
                packs = ((health_data or {}).get('battery') or {}).get('packs') or []
                snapshot['battery_capacity'] = packs[0] if len(packs) == 1 else {}
        resource_data = None
        if snapshot['policy']['resources']:
            resource_data = dict(resources_sampler() if resources_sampler else _resources(snapshot), observed_at=timestamp)
        with self._transaction() as state:
            ledger = state['ledger']
            before = ledger['total_seconds']
            tracker.sample(ledger, tick, boot, timestamp, history=state['policy']['history'],
                           boot_evidence=boot_evidence)
            if state['policy'].get('health'):
                import lifetime_health as health
                if health_data is not None:
                    data = health_data
                    state['health'] = health.summary(data)
                    packs = ((data or {}).get('battery') or {}).get('packs') or []
                    state['battery_capacity'] = packs[0] if len(packs) == 1 else {}
                    health.capacity_sample(ledger, data.get('battery') if data else None)
            history = (history_sampler or _history)(copy.deepcopy(ledger)) if state['policy']['history'] else None
            readings = None
            if state['policy']['resources']:
                readings = resource_data
                for key in ('specs', 'specs_collected_at', 'cpu_counter', 'network_counters', 'battery_capacity'):
                    if key in snapshot:
                        state[key] = snapshot[key]
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

    def set_health_consent(self, enabled):
        """Update just Health without overwriting other reporting choices."""
        if type(enabled) is not bool:
            raise ValueError('Health consent must be boolean')
        with self.reporting_lock():
            with self._transaction() as state:
                if bool(state['policy'].get('health')) != enabled:
                    self._set_policy(state, dict(state['policy'], health=enabled))
            self._db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.write_status()

    def set_control_consent(self, enabled):
        """Owner consent for remote power, independent of all other categories."""
        if type(enabled) is not bool:
            raise ValueError('Control consent must be boolean')
        with self.reporting_lock():
            with self._transaction() as state:
                if bool(state['policy'].get('control')) != enabled:
                    self._set_policy(state, dict(state['policy'], control=enabled))
            self.write_status()

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
            if state['policy'].get('health') and state.get('health') is not None:
                message['health'] = state['health']
            if state.get('command_result'):
                message['command_result'] = state['command_result']
            return protocol.validate_sync(message)

    def run(self, stop_event=None, client=None):
        """Hold a nonblocking process lock for the entire capture/sync loop."""
        from lifetime_client import SyncClient
        stop = stop_event or threading.Event()
        with tracker.file_lock(self.directory, 'agent.lock', blocking=False):
            client = client or SyncClient(self)
            from lifetime_live import Reporter
            wake = threading.Event()
            sample_now = threading.Event()
            reporter = Reporter(self, wake)
            thread = threading.Thread(target=reporter.run, name='lifetime-live', daemon=True)
            thread.start()
            def synchronize():
                while not stop.is_set() and not reporter.stop.is_set():
                    force = wake.is_set()
                    wake.clear()
                    try:
                        client.step(force=force)
                        with self._lock:
                            state = self._load()
                        if state['phase'] == 'approved' and not state.get('reported') and not sample_now.is_set():
                            sample_now.set()    # do not leave a new computer blank until the next 30 s sample
                        if state.get('command_result'):
                            client.step(force=True)
                    except Exception:
                        pass
                    wake.wait(1)
            sync_thread = threading.Thread(target=synchronize, name='lifetime-sync', daemon=True)
            sync_thread.start()
            try:
                while not stop.is_set():
                    self.sample()
                    if sample_now.is_set():
                        wake.set()      # send the first full report straight away
                    for _ in range(int(tracker.INTERVAL)):
                        if (self.directory/STOP_MARKER).exists():
                            stop.set()      # a newer agent has asked this one to hand over
                        if stop.is_set() or (sample_now.is_set() and not self._reported()):
                            break
                        stop.wait(1)
                    sample_now.clear()
            finally:
                reporter.stop.set()
                wake.set()
                thread.join(timeout=4)
                sync_thread.join()

    def close(self):
        with self._lock:
            self._db.close()


STOP_MARKER = 'agent.stop'


def replace_running_agent(directory, timeout=20):
    """Connecting must start current code. A reporter left running from an older connect holds
    the process lock and keeps its old behaviour, so ask it to leave and wait for the lock."""
    directory = Path(directory)
    marker = directory/STOP_MARKER
    deadline = time.monotonic()+timeout
    try:
        marker.write_text('1', encoding='ascii')
        while time.monotonic() < deadline:
            try:
                with tracker.file_lock(directory, 'agent.lock', blocking=False):
                    return True
            except OSError:
                time.sleep(0.5)
        return False
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit machine capture and company reporting; installs nothing')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--enroll-bundle-file', type=Path)
    parser.add_argument('--policy-history', action='store_true')
    parser.add_argument('--policy-resources', action='store_true')
    parser.add_argument('--policy-health', action='store_true')
    parser.add_argument('--policy-control', action='store_true')
    parser.add_argument('--display-name', default=__import__('socket').gethostname())
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--disable-reporting', action='store_true')
    parser.add_argument('--set-policy', action='store_true')
    parser.add_argument('--import-ledger', type=Path)
    args = parser.parse_args(argv)
    if (args.policy_history or args.policy_resources or args.policy_health or args.policy_control) and not (args.enroll_bundle_file or args.set_policy):
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
        policy.update(health=args.policy_health, control=args.policy_control)
        if bundle is not None or args.set_policy:
            agent.set_policy(policy)
        if args.disable_reporting:
            agent.disable_reporting()
        if args.import_ledger:
            agent.import_ledger(args.import_ledger)
        if bundle is not None:
            from lifetime_client import SyncClient
            SyncClient(agent).start_enrollment(bundle, {'display_name': args.display_name}, policy)
        if args.background:
            replace_running_agent(directory)
            command = ([sys.executable] if getattr(sys, 'frozen', False) else
                       [sys.executable, str(Path(__file__).resolve())])
            options = dict(creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if sys.platform == 'win32' else dict(start_new_session=True)
            subprocess.Popen(command + ['--directory', str(directory), '--run'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
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
