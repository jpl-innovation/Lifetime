#!/usr/bin/env python3
"""Reconstruct historical awake time from retained system logs (stdlib only)."""
import argparse
import copy
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import lifetime_tracker as tracker


def command(args, no_matches=False):
    result = subprocess.run(args, capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'})
    if result.returncode and not (no_matches and result.returncode == 1 and not result.stdout.strip()):
        raise ValueError(f'{args[0]} failed: {result.stderr.strip()}')
    if re.search(r'permission|not seeing messages', result.stderr, re.I):
        raise ValueError(f'Incomplete journal access: {result.stderr.strip()}')
    return result.stdout


def parse_boots(raw):
    """JSON microseconds, or journalctl --utc text with weekday/timezone columns."""
    if raw.lstrip().startswith('['):
        return [dict(id=b['boot_id'], start=int(b['first_entry']) / 1e6,
                     end=int(b['last_entry']) / 1e6, index=int(b['index'])) for b in json.loads(raw)]
    boots = []
    for line in raw.splitlines():
        match = re.match(r'\s*(-?\d+)\s+([0-9a-f]{32})\s+', line)
        if not match:
            continue
        stamps = re.findall(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', line)
        if len(stamps) != 2:
            raise ValueError(f'Cannot parse boot: {line}')
        start, end = [dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() for s in stamps]
        boots.append(dict(id=match[2], start=start, end=end, index=int(match[1])))
    if not boots:
        raise ValueError('No accessible boots in journal')
    return boots


def parse_events(raw, fallback=False):
    events = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        message = row.get('MESSAGE', '')
        kind = None
        if re.search(r'PM: (?:suspend|hibernation) entry', message):
            kind = 'sleep'
        elif re.search(r'PM: (?:suspend|hibernation) exit', message):
            kind = 'wake'
        elif fallback:
            if re.search(r'^(Suspending|Hibernating|Suspending then hibernating)\.\.\.', message):
                kind = 'sleep'
            elif re.search(r'Operation .* finished|System returned from sleep', message):
                kind = 'wake'
        if kind:
            events.append((int(row['__REALTIME_TIMESTAMP']) / 1e6, kind))
    return sorted(events)


def parse_shutdowns(raw):
    """Parse C-locale `last -x -F` shutdown timestamps, requested in UTC."""
    stamps = []
    for line in raw.splitlines():
        if not line.startswith('shutdown '):
            continue
        match = re.search(r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w{3}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+\d{4}', line)
        if match:
            stamps.append(dt.datetime.strptime(match[0], '%a %b %d %H:%M:%S %Y').replace(tzinfo=dt.timezone.utc).timestamp())
    return stamps


def _journal_boots():
    if not shutil.which('journalctl'):
        raise ValueError('Earlier history is not available: this system has no systemd journal')
    try:
        raw = command(['journalctl', '--list-boots', '--output=json', '--no-pager', '--utc'])
        boots = parse_boots(raw)
    except ValueError:
        boots = parse_boots(command(['journalctl', '--list-boots', '--no-pager', '--utc']))
    identity = tracker.boot_id().replace('-', '')
    current = next((b for b in boots if b['id'] == identity), None)
    if current is None:
        raise ValueError('Current boot missing; refusing to guess the import boundary')
    return boots, current


STAMP = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w{3}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+\d{4}'


def parse_wtmp(raw):
    """Boots from C-locale `last -x -F` reboot lines, requested in UTC: start, end (None while running), crash."""
    boots = []
    for line in raw.splitlines():
        if not line.startswith('reboot '):
            continue
        stamps = [dt.datetime.strptime(' '.join(s.split()), '%a %b %d %H:%M:%S %Y').replace(tzinfo=dt.timezone.utc).timestamp()
                  for s in re.findall(STAMP, line)]
        if stamps:
            boots.append(dict(start=stamps[0], end=stamps[1] if len(stamps) > 1 else None, crash=' crash' in line))
    return boots


def read_wtmp(folder=Path('/var/log')):
    """(shutdown times, reboot records, rotated files read) from wtmp and its rotated copies (wtmp.1, wtmp.*.gz).

    Optional corroboration: a missing or unreadable file is skipped, never an error, and never invents an end."""
    outputs, rotated = [], 0
    try:
        outputs.append(command(['last', '-x', '-F', 'reboot', 'shutdown']))
    except (OSError, ValueError):
        pass
    try:
        names = sorted(path for path in Path(folder).glob('wtmp.*') if path.is_file())
    except OSError:
        names = []
    for path in names:
        temporary = None
        try:
            target = path
            if path.suffix == '.gz':
                with gzip.open(path) as source, tempfile.NamedTemporaryFile(delete=False, suffix='.wtmp') as copy_:
                    shutil.copyfileobj(source, copy_)
                    temporary = target = Path(copy_.name)
            outputs.append(command(['last', '-x', '-F', '-f', str(target), 'reboot', 'shutdown']))
            rotated += 1
        except (OSError, ValueError, EOFError):
            continue
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    shutdowns = sorted(stamp for raw in outputs for stamp in parse_shutdowns(raw))
    boots = sorted((boot for raw in outputs for boot in parse_wtmp(raw)), key=lambda b: b['start'])
    return shutdowns, boots, rotated


def _wtmp_shutdowns():
    return read_wtmp()[0]


def _power_details(boot, shutdowns):
    """Sleep/wake events and whether the boot ended cleanly (journal, then the wtmp shutdown records)."""
    raw = command(['journalctl', '-k', '-b', boot['id'], '-g',
                   'PM: (suspend|hibernation) (entry|exit)', '-o', 'json', '--no-pager'], True)
    boot['events'] = parse_events(raw)
    if not boot['events']:
        raw = command(['journalctl', '-b', boot['id'], '-u', 'systemd-logind',
                       '-o', 'json', '--no-pager'])
        boot['events'] = parse_events(raw, fallback=True)
    tail = command(['journalctl', '-b', boot['id'], '-n', '100', '-o', 'json', '--no-pager'])
    records = [json.loads(line) for line in tail.splitlines() if line.strip()]
    boot['clean'] = any(boot['start'] <= stamp and abs(stamp - boot['end']) <= 120 for stamp in shutdowns)
    boot['clean'] |= any(row.get('_PID') == '1' and re.search(
        r'Shutting down|Reached target .*System (Reboot|Power Off)|Stopped Record System Boot/Shutdown in UTMP',
        row.get('MESSAGE', '')) for row in records)


def linux_boots():
    boots, current = _journal_boots()
    shutdowns = _wtmp_shutdowns()
    for boot in boots:
        if boot['id'] == current['id'] or boot['end'] >= current['start']:
            continue
        _power_details(boot, shutdowns)
        boot['activity'] = parse_activity(command(['journalctl', '-b', boot['id'], '-o', 'short-unix', '--no-pager']))
    return boots, current['start']


def linux_power(since=None):
    """Boots and downtime details for ``lifetime_downtime``: (boots, extras, info). On Linux an end that neither
    the journal nor wtmp shows as a clean shutdown counts as an unexpected stop (there is no Kernel-Power 41)."""
    boots, current = _journal_boots()
    shutdowns, records, rotated = read_wtmp()
    ordered = sorted(boots, key=lambda b: b['start'])
    first_index = next((i for i, boot in enumerate(ordered) if since is None or boot['end'] >= since), len(ordered))
    chosen = ordered[max(0, first_index - 1):]      # and the boot before: its end and the gap after it are read too
    for boot in chosen:
        _power_details(boot, shutdowns)
        if boot['id'] == current['id']:
            boot['clean'] = False
    # Boots older than the journal, known only from wtmp: starts, ends and crashes, but no sleep. They add off
    # time and counts; their running time stays unknown.
    first = min((b['start'] for b in boots), default=None)
    older = []
    for record in records:
        end = record['end'] if record['end'] is not None else record['start'] if record['crash'] else None
        if end is None or end < record['start'] or first is None or end > first or (since is not None and end < since):
            continue        # still running (the journal has it), damaged, or outside the range read
        older.append(dict(id='wtmp', start=record['start'], end=end, events=[],
                          clean=not record['crash'] and any(abs(t - end) <= 120 for t in shutdowns)))
    chosen = sorted(older, key=lambda b: b['start']) + chosen
    stops = [after['start'] for before, after in zip(chosen, chosen[1:]) if not before['clean']]
    return chosen, dict(requests=[], stops=stops, wakes=[]), dict(log='journal', wtmp=rotated)


GAP = 600  # seconds. A running desktop writes to the journal constantly; a longer silence means the
# computer was asleep, hung or off even if the sleep itself was never logged (suspend messages can be lost).


def parse_activity(raw):
    stamps = []
    for line in raw.splitlines():
        head = line.split(' ', 1)[0]
        try:
            stamps.append(float(head))
        except ValueError:
            continue  # '-- Boot ...' separators and the like
    return sorted(stamps)


def active_spans(stamps, gap=GAP):
    spans = []
    for a, b in zip(stamps, stamps[1:]):
        if b - a <= gap:
            if spans and spans[-1][1] == a:
                spans[-1] = (spans[-1][0], b)
            else:
                spans.append((a, b))
    return spans


def intersect(spans, others):
    out, j = [], 0
    for a, b in spans:
        while j < len(others) and others[j][1] <= a:
            j += 1
        k = j
        while k < len(others) and others[k][0] < b:
            lo, hi = max(a, others[k][0]), min(b, others[k][1])
            if hi > lo:
                out.append((lo, hi))
            k += 1
    return out


def awake_spans(boot):
    spans = _boot_spans(boot)
    if boot.get('activity'):
        spans = intersect(spans, active_spans(boot['activity']))
    return spans


def _boot_spans(boot):
    """Unmatched entry stays asleep to last entry; unmatched exit adds no sleep.

    Journal timestamps are observations, not proof of power-off. Never extend an
    unclean boot to the next boot, nor infer a missing wake from its last message.
    """
    start, end = boot['start'], boot['end']
    if end < start:
        raise ValueError('Boot ends before it starts')
    cursor, sleeping, spans = start, False, []
    for stamp, kind in sorted(boot.get('events', [])):
        if stamp > end:
            break
        stamp = max(start, stamp)
        if kind == 'sleep' and not sleeping:
            if stamp > cursor:
                spans.append((cursor, stamp))
            sleeping = True
        elif kind == 'wake' and sleeping:
            cursor, sleeping = stamp, False
    if not sleeping and cursor < end:
        spans.append((cursor, end))
    return spans


def allocate(start, end, daily, hourly, tz=None):
    # Epoch-hour stepping handles offset changes; local labels use the machine TZ.
    while start < end:
        local = dt.datetime.fromtimestamp(start, tz)
        boundary = start + 3600 - local.minute * 60 - local.second - local.microsecond / 1e6
        stop = min(end, boundary)
        seconds = stop - start
        key = local.date().isoformat()
        daily[key] = daily.get(key, 0.0) + seconds
        hourly[local.hour] += seconds
        start = stop


def rebuild(boots, current_start, ledger, tz=None):
    _check_saved_import(ledger)
    old = ledger.get('imported') or {}
    original = old.get('original', {})
    tracking = original.get('tracking_started', ledger.get('tracking_started'))
    # Date-only ledgers cannot prove which earlier boots were already credited.
    # Conservatively exclude every boot touching the original tracking day onward.
    cutoff = min(current_start, dt.datetime.fromisoformat(tracking[:10]).replace(tzinfo=tz).timestamp()) if tracking else current_start
    eligible = sorted((b for b in boots if not b.get('is_current') and b['end'] < current_start and b['end'] < cutoff), key=lambda b: b['start'])
    daily, hourly, sessions = {}, [0.0] * 24, []
    seconds = sleep = 0.0
    previous_end = None
    for boot in eligible:
        if previous_end is not None and boot['start'] < previous_end:
            raise ValueError('Overlapping boot timestamps; refusing ambiguous history')
        previous_end = boot['end']
        spans = awake_spans(boot)
        awake = sum(end - start for start, end in spans)
        seconds += awake
        sleep += boot['end'] - boot['start'] - awake
        for start, end in spans:
            allocate(start, end, daily, hourly, tz)
            sessions.append(dict(start=dt.datetime.fromtimestamp(start, tz).astimezone(tz).isoformat(), seconds=end-start, imported=True))
    return dict(until=dt.datetime.fromtimestamp(current_start, tz).astimezone(tz).isoformat(), seconds=seconds,
                boots=len(eligible), daily=daily, hourly=hourly, sessions=sessions[-100:],
                longest_session_seconds=max((s['seconds'] for s in sessions), default=0),
                first_day=dt.datetime.fromtimestamp(eligible[0]['start'], tz).date().isoformat() if eligible else None,
                last_day=dt.datetime.fromtimestamp(eligible[-1]['end'], tz).date().isoformat() if eligible else None,
                sleep_seconds=sleep, unclean_boots=sum(not b.get('clean', False) for b in eligible),
                boots_found=len(boots), skipped_boots=len(boots)-len(eligible))


def _check_saved_import(ledger):
    """An incomplete legacy marker is readable, but cannot authorize reimport."""
    tracker.validate_ledger(ledger)
    if 'imported' in ledger:
        marker = ledger['imported']
        required = {'seconds', 'boots', 'daily', 'hourly', 'sessions',
                    'longest_session_seconds', 'original'}
        original = marker.get('original', {})
        if (not required.issubset(marker) or
                not {'first_seen', 'tracking_started', 'history_started',
                     'longest_session_seconds'}.issubset(original)):
            raise ValueError('Incomplete imported accounting marker; refusing reimport to avoid double counting')


def merge(ledger, imported):
    _check_saved_import(ledger)
    from lifetime_ledger import validate
    required = {'seconds', 'boots', 'daily', 'hourly', 'sessions',
                'longest_session_seconds', 'first_day'}
    if not isinstance(imported, dict) or not required.issubset(imported):
        raise ValueError('Incomplete imported contribution')
    validate(dict(total_seconds=imported['seconds'], boots=imported['boots'], imported=imported))
    result = copy.deepcopy(ledger)
    old = result.get('imported') or {}
    original = old.get('original') or {k: result.get(k) for k in
                ('first_seen', 'first_seen_source', 'tracking_started', 'history_started', 'longest_session_seconds')}
    # Legacy markers predate provenance; do not attribute their date to the OS.
    original['first_seen_source'] = original.get('first_seen_source') or 'unknown'
    result['total_seconds'] = result.get('total_seconds', 0) - old.get('seconds', 0) + imported['seconds']
    result['boots'] = result.get('boots', 0) - old.get('boots', 0) + imported['boots']
    daily = result.setdefault('daily', {})
    for key, value in old.get('daily', {}).items():
        if key in daily:
            daily[key] = max(0, daily[key] - value)
            if daily[key] < 1e-6:
                del daily[key]
    for key, value in imported['daily'].items():
        daily[key] = daily.get(key, 0) + value
    result['hourly'] = [max(0, value - old.get('hourly', [0]*24)[i]) + imported['hourly'][i]
                        for i, value in enumerate(result.get('hourly', [0]*24))]
    live = [s for s in result.get('sessions', []) if not s.get('imported')]
    result['sessions'] = sorted(imported['sessions'] + live, key=lambda s: s['start'])[-100:]
    for key in ('first_seen', 'tracking_started', 'history_started'):
        dates = [v for v in (original.get(key), imported['first_day']) if v]
        result[key] = min(dates) if dates else None
    first = original.get('first_seen')
    if imported['first_day'] and (not first or imported['first_day'] < first):
        result['first_seen_source'] = 'imported-log'
    else:
        result['first_seen_source'] = original.get('first_seen_source') or 'unknown'
    live_longest = result.get('longest_session_seconds', 0)
    if old and live_longest <= old.get('longest_session_seconds', 0):
        live_longest = max(original.get('longest_session_seconds') or 0,
                           result.get('current_session_seconds', 0), *(s['seconds'] for s in live), 0)
    result['longest_session_seconds'] = max(live_longest, imported['longest_session_seconds'])
    imported = copy.deepcopy(imported)
    imported['original'] = original
    result['imported'] = imported
    return result


def read_path(path):
    # Custom filenames use precisely the same schema defaults as the tracker.
    return tracker.load_ledger(path)


def report(result):
    print(f"Boots found: {result['boots_found']}; imported: {result['boots']}; excluded (current/live overlap): {result['skipped_boots']}")
    print(f"Awake: {result['seconds']:.6f} seconds ({tracker.duration(result['seconds'])})")
    print(f"Sleep excluded: {result['sleep_seconds']:.6f} seconds ({tracker.duration(result['sleep_seconds'])})")
    print(f"Dates: {result['first_day']} through {result['last_day']}; unclean/unconfirmed ends: {result['unclean_boots']}")
    print('Top 5 local days:')
    for day, seconds in sorted(result['daily'].items(), key=lambda item: (-item[1], item[0]))[:5]:
        print(f'  {day}: {seconds:.3f} seconds ({tracker.duration(seconds)})')
    print(f"Daily sum: {sum(result['daily'].values()):.6f}; hourly sum: {sum(result['hourly']):.6f}")


def add_downtime(ledger, power, since=None):
    """Write ``downtime_daily`` from the power history ``power(since)`` returns. Awake accounting is not touched;
    a log that cannot be read only leaves a note for the coverage line."""
    import time
    import lifetime_downtime as downtime
    try:
        boots, extras, info = power(since)
    except (OSError, ValueError, KeyError, TypeError) as error:
        ledger['downtime_sources'] = dict(ledger.get('downtime_sources') or {}, note='unavailable')
        return str(error)
    now = time.time()
    result = downtime.from_boots(boots, extras, now)
    downtime.apply(ledger, result, downtime.sources_for(result, now=now, **info), now)
    return None


def main(provider=linux_boots, power=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    paths = parser.add_mutually_exclusive_group()
    paths.add_argument('--ledger', type=Path)
    paths.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    path = args.ledger or (args.data_dir or tracker.data_dir()) / 'ledger.json'
    try:
        boots, current = provider()
        if args.apply:
            with tracker.file_lock(path.parent, 'ledger.lock'):
                ledger = read_path(path) if path.exists() else tracker.new_ledger()
                imported = rebuild(boots, current, ledger)
                from lifetime_settings import load_settings
                settings = load_settings(path.parent)
                history = not (settings['completed'] and not settings['history'])
                if not history:
                    imported.update(daily={}, hourly=[0.0]*24, sessions=[])
                result = merge(ledger, imported)
                if power is not None and history:
                    problem = add_downtime(result, power)
                    if problem:
                        print(f'Downtime history not added: {problem}', file=sys.stderr)
                if path.exists():
                    shutil.copy2(path, path.with_name(path.name + '.bak-before-import'))
                tracker.atomic_json(path, result)
            print(f'Applied to {path}; backup: {path.name}.bak-before-import')
        else:
            ledger = read_path(path) if path.exists() else tracker.new_ledger()
            if not path.exists():
                print(f'No ledger at {path}; previewing history with no live tracking cutoff.')
            imported = rebuild(boots, current, ledger)
            print('Dry run — no files changed.')
        report(imported)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Lifetime import: {error}\n')


if __name__ == '__main__':
    main(linux_boots, linux_power)
