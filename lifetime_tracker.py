#!/usr/bin/env python3
"""Persistent awake-time accounting. The tracker itself uses only the stdlib."""
import argparse
import contextlib
import ctypes
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import sys
import tempfile
import threading
import time

INTERVAL = 30
MAX_DELTA = 300
MAX_BOOT_CREDIT = 366 * 24 * 3600  # Reject implausibly large initial uptimes.


def data_dir():
    if os.environ.get('LIFETIME_DATA_DIR'):
        return Path(os.environ['LIFETIME_DATA_DIR'])
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'Lifetime'
    return Path.home() / '.local/share/lifetime'


def awake_clock():
    if sys.platform == 'win32':
        value = ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(value)):
            raise ctypes.WinError()
        return value.value / 10_000_000
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def boot_id():
    if sys.platform == 'win32':
        # SystemBootEnvironmentInformation: GUID, firmware type, boot flags.
        # Fail explicitly if unavailable rather than silently missing a reboot.
        buffer = ctypes.create_string_buffer(32)
        status = ctypes.windll.ntdll.NtQuerySystemInformation(90, buffer, len(buffer), None)
        if status < 0:
            raise OSError(f'Cannot read Windows boot identity: NTSTATUS {status:#x}')
        identity = buffer.raw[:16].hex()
        # Some Windows VMs retain this GUID across actual OS starts. The local
        # Windows boot sequence distinguishes those starts without wall time.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                sequence, kind = winreg.QueryValueEx(key, 'BootId')
            if kind == winreg.REG_DWORD and type(sequence) is int and sequence >= 0:
                return f'{identity}:windows-boot:{sequence}'
        except OSError:
            pass
        return identity
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def same_boot_identity(left, right):
    """Recognize a legacy Windows GUID without treating upgrade as a reboot.

    Two explicit Windows sequences must match. A missing sequence is unknown,
    not evidence of a reboot; sample() separately guards counter rollback.
    """
    if left == right:
        return True
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    pattern = r'([0-9a-f]{32})(?::windows-boot:([0-9]+))?'
    a, b = re.fullmatch(pattern, left), re.fullmatch(pattern, right)
    return bool(a and b and a[1] == b[1] and (a[2] is None or b[2] is None))


def estimate_first_seen(with_source=False):
    candidates = []
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as key:
                candidates.append(winreg.QueryValueEx(key, 'InstallDate')[0])
        except (OSError, ValueError):
            pass
    else:
        for name in ('/var/log/pacman.log', '/etc/machine-id'):
            try:
                candidates.append(Path(name).stat().st_mtime)
            except OSError:
                pass
        # st_birthtime is available on some filesystems/platforms; do not shell out.
        birth = getattr(Path('/').stat(), 'st_birthtime', None)
        if birth:
            candidates.append(birth)
    now = time.time()
    candidates = [stamp for stamp in candidates if 0 < stamp <= now]
    day = dt.date.fromtimestamp(min(candidates, default=now)).isoformat()
    source = ('os-install-date' if candidates and sys.platform == 'win32' else
              'system-file-date' if candidates else 'tracking-start')
    return (day, source) if with_source else day


def new_ledger():
    estimate = estimate_first_seen(with_source=True)
    # Older integrations/tests may still supply the original string API.
    first_seen, source = estimate if isinstance(estimate, tuple) and len(estimate) == 2 else (estimate, 'unknown')
    return dict(total_seconds=0.0, baseline_seconds=0.0,
                first_seen=first_seen, first_seen_source=source, boots=0,
                current_session_seconds=0.0, longest_session_seconds=0.0,
                last_update=None, boot_id=None, last_clock=None,
                tracking_started=None, boot_credited_for=None, boot_accounted_seconds=0.0,
                daily={}, hourly=[0.0] * 24, sessions=[], history_started=None,
                unattributed_seconds=0.0, recording_started=None, coverage_gaps=[], clock_note_count=0)


def load_ledger(path):
    """Read one existing file strictly; malformed bytes and fields stay visible."""
    return validate_ledger(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def read_ledger(directory=None):
    path = Path(directory or data_dir()) / 'ledger.json'
    try:
        return load_ledger(path)
    except FileNotFoundError:
        return new_ledger()


def validate_ledger(value):
    """Validate before legacy defaults/migrations; retain unknown extensions."""
    from lifetime_ledger import validate
    validate(value)
    result = new_ledger()
    result.update(value)
    result['first_seen'] = value.get('first_seen')
    result['first_seen_source'] = value.get('first_seen_source', 'unknown')
    if 'boot_accounted_seconds' not in value:
        result['boot_accounted_seconds'] = min(result['total_seconds'], result['last_clock'] or 0.0)
    if not result.get('tracking_started'):
        # last_update is the latest sample, not evidence of the first sample.
        first_sample = value.get('first_sample')
        result['tracking_started'] = (first_sample or dt.date.today().isoformat())[:10]
        result['tracking_started_estimated'] = not bool(first_sample)
    return validate(result)


@contextlib.contextmanager
def file_lock(directory, name, blocking=True):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open('a+b') as handle:
        if sys.platform == 'win32':
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            if sys.platform == 'win32':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(directory, ledger, running=None):
    atomic_json(Path(directory) / 'ledger.json', ledger)
    status = dict(ledger)
    status.update(pid=os.getpid(), heartbeat=time.time(), running=running)
    atomic_json(Path(directory) / 'status.json', status)


def sample(ledger, tick, identity, timestamp=None, credit_current=False, history=True, boot_evidence=None):
    """Count awake deltas and credit initial uptime once for each observed boot."""
    if not math.isfinite(tick) or tick < 0:
        raise ValueError('Invalid awake clock')
    stamp = timestamp or dt.datetime.now(dt.timezone.utc).isoformat()
    if not ledger.get('recording_started') and ledger.get('last_update') is None and not ledger.get('total_seconds'):
        ledger['recording_started'] = dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).isoformat()
    if not ledger.get('tracking_started'):
        ledger['tracking_started'] = stamp[:10]
    local = dt.datetime.fromisoformat(stamp).astimezone()
    day = local.date().isoformat()
    ledger.setdefault('daily', {})
    ledger.setdefault('hourly', [0.0] * 24)
    ledger.setdefault('sessions', [])
    ledger.setdefault('unattributed_seconds', 0.0)
    if history and not ledger.get('history_started'):
        ledger['history_started'] = day
    previous = ledger.get('last_clock')
    delta = tick - previous if previous is not None else None
    changed_boot = (previous is None or
                    (identity is not None and ledger.get('boot_id') is not None and not same_boot_identity(identity, ledger.get('boot_id'))) or
                    (identity is None and delta is not None and delta < 0))
    # Never use cumulative boot_accounted_seconds as a clock watermark: legacy
    # ledgers may have accumulated several counter epochs under a reused GUID.
    high_water = max(ledger.get('awake_clock_high_water', 0.0), previous if previous is not None else 0.0)
    if not changed_boot and same_boot_identity(identity, ledger.get('boot_id')):
        if ':windows-boot:' in (ledger.get('boot_id') or '') and ':windows-boot:' not in (identity or ''):
            identity = ledger['boot_id']  # retain the stronger identity on read failure
        if same_boot_identity(ledger.get('boot_credited_for'), identity):
            ledger['boot_credited_for'] = identity
    if changed_boot:
        high_water = 0.0
        ledger['boots'] += 1
        ledger['current_session_seconds'] = 0.0
        ledger['boot_accounted_seconds'] = 0.0
        ledger['boot_credited_for'] = None
    # Legacy callers without an OS identity use an observed-session token.
    token = identity if identity is not None else f"session:{ledger['boots']}"
    if history and (changed_boot or not ledger['sessions']):
        ledger['sessions'].append(dict(start=local.isoformat(), seconds=0.0))
    added = 0.0
    accounted = ledger.get('boot_accounted_seconds', ledger['current_session_seconds'])
    rollback = not changed_boot and delta is not None and delta < 0
    if rollback:
        # A decreasing counter with no proven new boot is ambiguous. Preserve
        # totals and the prior watermark, including when history is disabled.
        ledger['awake_clock_rollbacks'] = ledger.get('awake_clock_rollbacks', 0) + 1
        ledger['current_session_seconds'] = 0.0
        if history:
            ledger.setdefault('coverage_gaps', []).append(dict(start=ledger.get('last_update'), end=stamp,
                                                              reason='awake_clock_rollback'))
            ledger['coverage_gaps'] = ledger['coverage_gaps'][-100:]
            ledger['sessions'].append(dict(start=local.isoformat(), seconds=0.0))
    elif changed_boot or (credit_current and ledger.get('boot_credited_for') != token and tick >= high_water):
        credit = max(0.0, min(tick, MAX_BOOT_CREDIT) - accounted)
        ledger['total_seconds'] += credit
        ledger['current_session_seconds'] += credit
        accounted += credit
        added = credit
        if history:
            from lifetime_daily import distribute
            evidence = boot_evidence
            provenance = distribute(credit, local.timestamp(), ledger['daily'], ledger['hourly'], evidence,
                                    dated=ledger.setdefault('dated_hourly', {}))
            ledger['daily_allocation'] = provenance
            ledger['history_started'] = min(ledger.get('history_started') or day, min(ledger['daily'], default=day))
        else:
            ledger['unattributed_seconds'] += credit
        ledger['boot_credited_for'] = token
    elif delta is not None and 0 <= delta <= MAX_DELTA:
        # Only the tail above the highest observed counter is new evidence.
        added = max(0.0, tick - max(previous, high_water))
        ledger['total_seconds'] += added
        ledger['current_session_seconds'] += added
        accounted += added
        if history and added:
            from lifetime_daily import distribute
            end = local.timestamp()
            distribute(added, end, ledger['daily'], ledger['hourly'],
                       (end-added, [(end-added, end)], False), dated=ledger.setdefault('dated_hourly', {}))
    elif delta is not None:
        if history:
            ledger.setdefault('coverage_gaps', []).append(dict(start=ledger.get('last_update'), end=stamp, reason='awake_clock_discontinuity'))
            ledger['coverage_gaps'] = ledger['coverage_gaps'][-100:]
        ledger['current_session_seconds'] = 0.0
        if history:
            ledger['sessions'].append(dict(start=local.isoformat(), seconds=0.0))
    if history:
        ledger['sessions'][-1]['seconds'] += added
    ledger['sessions'] = ledger['sessions'][-100:]
    cutoff = (local.date() - dt.timedelta(days=399)).isoformat()
    ledger['daily'] = {key: value for key, value in ledger['daily'].items() if key >= cutoff}
    if 'dated_hourly' in ledger:
        ledger['dated_hourly'] = {key: value for key, value in ledger['dated_hourly'].items() if key >= cutoff}
    if ledger.get('last_update'):
        previous_stamp = dt.datetime.fromisoformat(ledger['last_update']).astimezone(dt.timezone.utc)
        elapsed_wall = (local.astimezone(dt.timezone.utc)-previous_stamp).total_seconds()
        if elapsed_wall < 0 or (not changed_boot and delta is not None and elapsed_wall+2 < delta):
            ledger['clock_note_count'] = ledger.get('clock_note_count', 0)+1
    ledger['boot_accounted_seconds'] = accounted
    ledger['awake_clock_high_water'] = max(high_water, tick)
    ledger['longest_session_seconds'] = max(ledger['longest_session_seconds'], ledger['current_session_seconds'])
    ledger.update(last_clock=tick, boot_id=identity, last_update=stamp)
    return ledger


def credit_current_boot(directory=None):
    directory = Path(directory or data_dir())
    from lifetime_settings import load_settings
    with file_lock(directory, 'ledger.lock'):
        ledger = read_ledger(directory)
        settings = load_settings(directory)
        sample(ledger, awake_clock(), boot_id(), credit_current=True,
               history=settings['completed'] and settings['history'])
        # A manual correction must not advertise a live tracker heartbeat.
        atomic_json(directory / 'ledger.json', ledger)
    return ledger


def repair_daily(directory=None, evidence=None):
    """Migrate legacy initial boot credits once, with a byte-for-byte backup.

    Only history buckets, attribution metadata and the migration marker change.
    Lifetime totals, baselines, sessions, boot counts and clocks are preserved.
    Evidence can be injected for deterministic tests. No status heartbeat is written.
    """
    from lifetime_daily import distribute, boot_history
    import copy
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        path = directory / 'ledger.json'
        if not path.exists():
            return read_ledger(directory)
        ledger = read_ledger(directory)
        if ledger.get('daily_repair_version') == 1:
            return ledger
        repaired = copy.deepcopy(ledger)
        remaining = ledger.get('unattributed_seconds', 0.0)
        # Privacy-disabled history credits have no daily bucket to repair.
        # Existing hourly allocations must never be credited a second time.
        pending = min(remaining, max(0.0, math.fsum(ledger['daily'].values())-math.fsum(ledger['hourly'])))
        allocations = []
        # Legacy sample() attached initial credit to its session's start date.
        # Imported spans already have correct buckets and are never reallocated.
        for session in ledger.get('sessions', []):
            if session.get('imported') or pending <= 1e-8:
                continue
            end = dt.datetime.fromisoformat(session['start']).timestamp()
            day = dt.datetime.fromtimestamp(end).date().isoformat()
            amount = min(pending, session['seconds'], repaired['daily'].get(day, 0))
            if amount <= 0:
                continue
            repaired['daily'][day] -= amount
            source = evidence if evidence is not None else boot_history(end)
            allocations.append(distribute(amount, end, repaired['daily'], repaired['hourly'], source,
                                          dated=repaired.setdefault('dated_hourly', {})))
            remaining -= amount
            pending -= amount
        # A retained ledger may have dropped its oldest session records. Repair
        # impossible buckets conservatively without inventing log evidence.
        for day in list(repaired['daily']):
            value = repaired['daily'][day]
            if value <= 86400+1e-7:
                continue
            end = dt.datetime.combine(dt.date.fromisoformat(day)+dt.timedelta(days=1), dt.time()).timestamp()
            repaired['daily'][day] = 0
            # Use enough prior wall days; reserve existing days' occupied capacity.
            span = max(value, 86400)
            while sum(max(0, 86400-repaired['daily'].get((dt.date.fromisoformat(day)-dt.timedelta(days=i)).isoformat(), 0))
                      for i in range(math.ceil(span/86400))) < value:
                span += 86400
            scratch = [0.0]*24
            allocations.append(distribute(value, end, repaired['daily'], scratch,
                                          (end-span, [(end-span, end)], True)))
        if any(v > 86400+1e-6 for v in repaired['daily'].values()):
            raise ValueError('Daily repair could not satisfy the 24-hour limit')
        repaired['unattributed_seconds'] = 0.0 if remaining < 1e-6 else remaining
        if repaired['daily']:
            repaired['history_started'] = min(repaired['daily'])
        repaired['daily_repair_version'] = 1
        repaired['daily_repair'] = dict(allocations=allocations,
                                       estimated=any(a['estimated'] for a in allocations))
        backup = directory / 'ledger.json.bak-before-daily-repair'
        if not backup.exists():
            with backup.open('xb') as target:
                with path.open('rb') as source:
                    shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
        atomic_json(path, repaired)
        return repaired


def recover(directory=None):
    """Explicit recovery; return (ledger, how, notes), preserving original bytes.

    Salvage only derived damage. Otherwise try backups newest first, validating
    each strictly. Without a valid replacement the original is never changed.
    """
    from lifetime_ledger import salvage
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        path = directory / 'ledger.json'
        raw = path.read_bytes()
        how = 'salvaged'
        try:
            value = json.loads(raw.decode('utf-8-sig'))
            # Retain missing optional fields until the normal legacy migrations.
            ledger, notes = salvage(value, {})
            ledger = validate_ledger(ledger)
            if not notes:
                return ledger, 'unchanged', []
        except (ValueError, UnicodeError) as error:
            candidates = []
            for candidate in (*directory.glob('ledger.json.bak*'),
                              *directory.glob('backup-*/ledger.json')):
                try:
                    if candidate.is_file():
                        candidates.append((candidate.stat().st_mtime_ns, candidate))
                except OSError:
                    continue
            ledger = None
            for _, candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
                try:
                    ledger = load_ledger(candidate)
                except (OSError, ValueError, UnicodeError):
                    continue
                how, notes = f'restored from {candidate.relative_to(directory)}', [str(error)]
                break
            if ledger is None:
                raise ValueError(f'Ledger left unchanged; no valid backup found. {error}') from error
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        fd, damaged = tempfile.mkstemp(prefix=f'ledger.json.corrupt-{stamp}-', dir=directory)
        # Exclusive creation plus fsync: replacement cannot precede preservation.
        with os.fdopen(fd, 'wb') as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        atomic_json(path, ledger)
        return ledger, how, notes


def average_awake(ledger, today=None):
    if not ledger.get('tracking_started'):
        return None
    days = ((today or dt.date.today()) - dt.date.fromisoformat(ledger['tracking_started'])).days
    return ledger['total_seconds'] / 3600 / days if days >= 1 else None


def set_baseline(hours, directory=None):
    if not math.isfinite(hours) or hours < 0:
        raise ValueError('Baseline must be a finite, nonnegative number of hours')
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        ledger = read_ledger(directory)
        ledger['baseline_seconds'] = hours * 3600
        # Preserve the tracker's heartbeat; baseline edits do not imply it is alive.
        atomic_json(directory / 'ledger.json', ledger)
    return ledger


def duration(seconds):
    minutes = int(seconds // 60)
    return f'{minutes // 1440} d {minutes // 60 % 24} h {minutes % 60} m'


def summary(ledger):
    return f"{duration(ledger['total_seconds'] + ledger['baseline_seconds'])} awake (earliest known date {ledger['first_seen']}, {ledger['boots']} boots)"


def run(directory=None):
    directory = Path(directory or data_dir())
    from lifetime_settings import load_settings
    if not load_settings(directory)['completed']:
        return
    stop = threading.Event()
    finished = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    # Keep the callback alive for the entire run on Windows.
    callback = None
    if sys.platform == 'win32':
        def console_close(event):
            stop.set()
            # Windows may terminate the process as soon as this handler returns.
            finished.wait(4)
            return True
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(console_close)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(callback, True)
    with file_lock(directory, 'tracker.lock', blocking=False):
        # Capacity collection never delays accounting or holds the ledger lock.
        import lifetime_health as health
        health_results = []
        health_pending = False
        health_attempt = -3600
        def collect_capacity():
            health_results.append(health.probe_battery())
        def flush(running):
            nonlocal health_pending, health_attempt
            with file_lock(directory, 'ledger.lock'):
                settings = load_settings(directory)
                if not settings['completed']:
                    return
                ledger = read_ledger(directory)
                if settings.get('health'):
                    if health_results:
                        health.capacity_sample(ledger, health_results.pop())
                        health_pending = False
                    today = dt.date.today().isoformat()
                    if running and not health_pending and time.monotonic()-health_attempt >= 3600 and today not in ledger.get('health_capacity', {}):
                        health_pending = True
                        health_attempt = time.monotonic()
                        threading.Thread(target=collect_capacity, daemon=True).start()
                else:
                    health_results.clear()
                    health_pending = False
                sample(ledger, awake_clock(), boot_id(), history=settings['history'])
                save(directory, ledger, running)
        try:
            flush(True)
            while not stop.wait(INTERVAL):
                flush(True)
        finally:
            try:
                flush(False)
            finally:
                finished.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--print', action='store_true', dest='print_summary')
    group.add_argument('--set-baseline', type=float, metavar='HOURS')
    group.add_argument('--run', action='store_true')
    group.add_argument('--repair-daily', action='store_true', help='Repair legacy daily boot credits once, preserving totals')
    group.add_argument('--recover', action='store_true',
                       help='Recover damaged data, preserving ledger.json.corrupt-*')
    group.add_argument('--credit-current-boot', action='store_true',
                       help='Credit unrecorded awake time in this boot once')
    args = parser.parse_args()
    try:
        if args.run:
            run()
        elif args.repair_daily:
            print(summary(repair_daily()))
        elif args.recover:
            ledger, how, notes = recover()
            print(f'Ledger {how}.' + ''.join(f'\n  - {note}' for note in notes))
            print(summary(ledger))
        elif args.credit_current_boot:
            print(summary(credit_current_boot()))
        else:
            ledger = set_baseline(args.set_baseline) if args.set_baseline is not None else read_ledger()
            print(summary(ledger))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Lifetime: {error}\n')


if __name__ == '__main__':
    main()
