#!/usr/bin/env python3
"""Reconstruct Windows awake history from the retained System event log."""
from bisect import bisect_right
from collections import Counter
import ctypes
import datetime as dt
import io
from itertools import chain
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

from lifetime_import import main


def _timestamp(value):
    # Event XML is invariant UTC, regardless of the Windows display language.
    import re
    text = value.strip().replace('Z', '+00:00')
    # Event logs write 7 fractional digits; Python 3.10 only parses 3 or 6, so normalise to microseconds.
    text = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], text, count=1)
    value = dt.datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError('Event timestamp has no UTC offset')
    return value.timestamp()


def _xml_events(source):
    """Read /e:Events XML without retaining a tree for the entire System log."""
    root = None
    for action, element in ET.iterparse(source, events=('start', 'end')):
        if root is None:
            root = element
        if action == 'end' and element.tag.rsplit('}', 1)[-1] == 'Event':
            yield element
            root.remove(element)
            element.clear()


def _parse_events(events):
    """General 12 defines a full kernel boot; hybrid/hibernate resumes do not.

    Consume record order, not TimeCreated order: hibernation buffers events with
    pre-sleep timestamps. Keep only transition data and each boot's last observed
    timestamp, so ordinary events do not consume memory proportional to log size.
    Missing starts are not invented; missing ends stop at the last observation.
    """
    boots, active = [], None
    counts = Counter()
    first = last = None
    for event in events:
        system = event.find('{*}System')
        if system is None:
            raise ValueError('System log event has no System metadata')
        try:
            provider = system.find('{*}Provider').get('Name', '')
            identity = int(system.findtext('{*}EventID'))
            stamp = _timestamp(system.find('{*}TimeCreated').get('SystemTime'))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f'Invalid System log event metadata: {error}') from error
        counts['events'] += 1
        first = stamp if first is None else min(first, stamp)
        last = stamp if last is None else max(last, stamp)
        data = {item.get('Name'): item.text for item in event.findall('{*}EventData/{*}Data')}
        kind = None
        if provider == 'Microsoft-Windows-Kernel-General':
            kind = {12: 'start', 13: 'shutdown'}.get(identity)
            if kind:
                counts[kind] += 1
        elif provider == 'Microsoft-Windows-Kernel-Power':
            if identity == 42:
                kind = 'sleep'
                counts['sleep'] += 1
            elif identity == 107:
                counts['power_resume'] += 1
                # On real hibernate/hybrid boots, 107 is timestamped seconds
                # after sleep entry, BEFORE the overnight gap. It is not a wake
                # boundary. Kernel-Boot 27 / Troubleshooter supply the wake.
                if data.get('TargetState') not in ('5', '6'):
                    kind = 'wake'
        elif provider == 'Microsoft-Windows-Kernel-Boot' and identity == 27:
            boot_type = data.get('BootType')
            counts[f'boot_type_{boot_type}'] += 1
            if boot_type in ('1', '2'):
                kind = 'wake'
        elif provider == 'Microsoft-Windows-Power-Troubleshooter' and identity == 1:
            counts['troubleshooter_resume'] += 1
            kind = 'wake'
            if data.get('WakeTime'):
                wake = _timestamp(data['WakeTime'])
                if data.get('SleepTime'):
                    sleep = _timestamp(data['SleepTime'])
                    if sleep > wake:
                        raise ValueError('SleepTime is later than WakeTime; refusing ambiguous history')
                    if active is not None:
                        active['sleep_intervals'].append((sleep, wake))
                stamp = wake

        if kind == 'start':
            active = dict(id=f'windows:{stamp}', start=stamp, end=stamp,
                          events=[], sleep_intervals=[], clean=False)
            boots.append(active)
        elif active is not None:
            # EventLog startup records can precede the previous shutdown in
            # record order while carrying the NEXT boot's timestamp.
            if not (provider == 'EventLog' and identity in (6005, 6009, 6013)):
                active['end'] = max(active['end'], stamp)
            if kind == 'shutdown':
                active['end'] = stamp  # The explicit end overrides buffered observations.
                active['clean'] = True
                active = None
            elif kind in ('sleep', 'wake'):
                active['events'].append((stamp, kind))

    for boot in boots:
        # Troubleshooter carries the actual sleep/wake times even if event 42
        # was lost. Suppress buffered/duplicate wake observations inside those
        # intervals; a wake created late must never extend sleep until creation.
        intervals = []
        for start, end in sorted(boot.pop('sleep_intervals')):
            if intervals and start <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(end, intervals[-1][1]))
            else:
                intervals.append((start, end))
        starts = [start for start, _ in intervals]
        filtered = []
        for stamp, kind in boot['events']:
            index = bisect_right(starts, stamp) - 1
            if kind == 'wake' and index >= 0 and stamp < intervals[index][1]:
                continue
            filtered.append((stamp, kind))
        for start, end in intervals:
            filtered.extend(((start, 'sleep'), (end, 'wake')))
        boot['events'] = sorted(filtered)
    return boots, dict(counts=counts, first=first, last=last)


def parse_windows_xml(raw):
    """Parse concatenated event XML or an Events document (also used by tests)."""
    if isinstance(raw, bytes):
        raw = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    raw = raw.lstrip('\ufeff').strip()
    if raw.startswith('<?xml'):
        raw = raw[raw.index('?>')+2:].strip()
    if not raw.startswith('<Events'):
        raw = '<Events>' + raw + '</Events>'
    return _parse_events(_xml_events(io.StringIO(raw)))[0]


POWER_IDS = (1, 12, 13, 27, 41, 42, 107, 1074, 6005, 6006, 6008, 6009, 6013)
STOPS = (('Microsoft-Windows-Kernel-Power', 41), ('EventLog', 6008))


def _note(event, extras):
    """Collect what downtime needs and the awake parser ignores: unexpected stops and wake sources.

    41 and 6008 normally describe the same stop, both written at the next start: keep one per incident."""
    system = event.find('{*}System')
    provider = system.find('{*}Provider').get('Name', '')
    identity = int(system.findtext('{*}EventID'))
    stamp = _timestamp(system.find('{*}TimeCreated').get('SystemTime'))
    data = {item.get('Name'): item.text or '' for item in event.findall('{*}EventData/{*}Data')}
    if provider == 'User32' and identity == 1074:
        # Who or what asked for the shutdown or restart. param5 (the type) is localized on some Windows languages:
        # only the English words are trusted, otherwise the restart rule falls back to the time until the next start.
        process = re.split(r'[\\/]', re.sub(r'\s*\([^)]*\)\s*$', '', data.get('param1', '')))[-1]
        user = data.get('param7', '')
        kind = {'restart': 'restart', 'power off': 'shutdown', 'shutdown': 'shutdown'}.get(data.get('param5', '').strip().lower())
        extras['requests'].append((stamp, dict(kind=kind, by=(f'{process} ({user})' if user else process)[:200],
                                               reason=data.get('param3', '')[:200])))
    elif (provider, identity) in STOPS:
        if not any(abs(stamp - other) < 120 for other in extras['stops'][-3:]):
            extras['stops'].append(stamp)
    elif provider == 'Microsoft-Windows-Power-Troubleshooter' and identity == 1 and data.get('WakeTime'):
        extras['wakes'].append((_timestamp(data['WakeTime']), (data.get('WakeSourceText') or data.get('WakeTimerOwner') or '')[:200]))


def _tap(events, extras):
    """Pass events through unchanged while noting downtime details; a detail that cannot be read is skipped."""
    for event in events:
        try:
            _note(event, extras)
        except (AttributeError, TypeError, ValueError):
            pass
        yield event


def new_extras():
    return dict(requests=[], stops=[], wakes=[])


def power_query(since=None):
    """XPath for the power events only (fast: a fraction of a second), optionally from a UTC instant on."""
    when = ''
    if since is not None:
        moment = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        when = f" and TimeCreated[@SystemTime>='{moment}']"
    return '*[System[(' + ' or '.join(f'EventID={i}' for i in POWER_IDS) + ')' + when + ']]'


def _first_live_event():
    """Time of the oldest event in the live System log, or None."""
    try:
        result = subprocess.run(['wevtutil', 'qe', 'System', '/c:1', '/rd:false', '/f:xml', '/uni:true'],
                                capture_output=True, timeout=30)
        if result.returncode:
            return None
        event = ET.fromstring(result.stdout)
        return _timestamp(event.find('{*}System/{*}TimeCreated').get('SystemTime'))
    except (OSError, subprocess.SubprocessError, ET.ParseError, AttributeError, ValueError):
        return None


_ARCHIVES = {}     # path -> (size, modified, since, [(record, time, event)]): an archive never changes once written


def _read_archive(path, since):
    """[(record number, time, event)] of one archived log, read once while its size and date stay the same."""
    info = os.stat(path)
    key = (info.st_size, info.st_mtime_ns, since)
    cached = _ARCHIVES.get(str(path))
    if cached is not None and cached[0] == key:
        return cached[1]
    result = subprocess.run(['wevtutil', 'qe', str(path), '/lf:true', f'/q:{power_query(since)}',
                             '/f:xml', '/e:Events', '/uni:true'], capture_output=True, timeout=120)
    if result.returncode:
        message = result.stderr.decode('utf-16' if result.stderr.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8', errors='replace')
        raise PermissionError(message) if result.returncode == 5 or 'denied' in message.lower() else OSError(message)
    rows = []
    for event in ET.fromstring(result.stdout):
        system = event.find('{*}System')
        rows.append((int(system.findtext('{*}EventRecordID') or len(rows)),
                     _timestamp(system.find('{*}TimeCreated').get('SystemTime')), event))
    rows.sort(key=lambda row: row[0])
    _ARCHIVES[str(path)] = (key, rows)
    return rows


def archive_events(since=None, before=None, folder=None):
    """Power events from archived System logs (Archive-System-*.evtx, kept when IT sets the log to archive when full).

    Returns (events, info). Order: archive by archive (their names carry the date they were saved), then by record
    number inside each, because record numbers can start again after the log is cleared. Events at or after
    ``before`` (the live log's first event) are left to the live log, and an event seen in two files is read once,
    so copies never double a boot. Each archive is read once and kept while it is unchanged. Missing archives or
    denied access are reported in ``info['archive_note']``, never raised."""
    folder = Path(folder or Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'winevt' / 'Logs')
    try:
        names = sorted(name for name in os.listdir(folder) if name.lower().startswith('archive-system-') and name.lower().endswith('.evtx'))
    except PermissionError:
        return [], dict(archives=0, archive_note='denied')
    except OSError:
        return [], dict(archives=0, archive_note='none')
    events, seen, read, denied, failed = [], set(), 0, False, False
    for name in names:
        try:
            rows = _read_archive(folder / name, since)
        except PermissionError:
            denied = failed = True
            continue
        except (OSError, subprocess.SubprocessError, ET.ParseError, AttributeError, TypeError, ValueError):
            failed = True
            continue
        for record, stamp, event in rows:
            if (before is None or stamp < before) and (record, stamp) not in seen:
                seen.add((record, stamp))
                events.append(event)
        read += 1
    note = 'read' if read else 'denied' if denied else 'error' if failed else 'none'
    return events, dict(archives=read, archive_note=note)


def _query_system(extras=None, query=None, before=()):
    # /uni:true avoids the active code page and localized message decoding.
    # /e:Events supplies a root for iterparse. No message rendering, elevation,
    # event count cap, or query of a restricted log such as Security is needed.
    # ``extras`` (optional) also collects downtime details from the same stream; ``query`` filters by XPath;
    # ``before`` are older (archived) events read first, in record order.
    args = ['wevtutil', 'qe', 'System', '/f:xml', '/e:Events', '/rd:false', '/uni:true'] + ([f'/q:{query}'] if query else [])
    errors = bytearray()
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        def read_errors():
            while chunk := process.stderr.read(4096):
                if len(errors) < 65536:
                    errors.extend(chunk[:65536-len(errors)])

        reader = threading.Thread(target=read_errors, daemon=True)
        reader.start()
        parse_error = None
        try:
            events = chain(before, _xml_events(process.stdout))
            result = _parse_events(_tap(events, extras) if extras is not None else events)
        except (ET.ParseError, ValueError) as error:
            parse_error = error
            if process.poll() is None:
                process.kill()
        finally:
            process.wait()
            reader.join()
        if process.returncode:
            detail = bytes(errors).decode('utf-16' if errors.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8', errors='replace').strip()
            raise ValueError('Cannot read the System event log. This account may need Event Log Readers '
                             f'access; no history was imported. wevtutil: {detail or process.returncode}')
        if parse_error is not None:
            raise ValueError(f'Cannot parse the System event log; no history was imported. {parse_error}') from parse_error
    return result


def _native_boot_time():
    # SystemTimeOfDayInformation is a 48-byte structure. Unlike CIM/WMI this
    # query works for a standard user, and includes sleep/hibernation time.
    buffer = ctypes.create_string_buffer(48)
    status = ctypes.windll.ntdll.NtQuerySystemInformation(3, buffer, len(buffer), None)
    if status < 0:
        raise OSError(f'Cannot read Windows boot time: NTSTATUS {status & 0xffffffff:#x}')
    stamp = int.from_bytes(buffer.raw[:8], 'little', signed=True) / 10_000_000 - 11644473600
    if not 0 < stamp <= time.time():
        raise ValueError('Invalid native Windows boot time')
    return stamp


def _with_archives(read, since=None):
    """``read(archived events)``, archives first. Archives are only an addition: without a known start of the live
    log (needed to avoid reading a boot twice) they are skipped, and if anything in them breaks the read, the live
    log is read alone. Returns (result, info)."""
    first = _first_live_event()
    archived, info = archive_events(since, first) if first is not None else ([], dict(archives=0, archive_note='error'))
    try:
        return read(archived), info
    except ValueError:
        if not archived:
            raise
        return read(()), dict(archives=0, archive_note='error')


def windows_power(since=None):
    """Boots and downtime details from the power events in the System log, for ``lifetime_downtime``.

    Returns (boots, extras, info); raises ValueError when the log cannot be read."""
    def read(archived):
        extras = new_extras()
        return _query_system(extras, power_query(since), archived)[0], extras
    try:
        (boots, extras), info = _with_archives(read, since)
    except (OSError, ET.ParseError) as error:
        raise ValueError(f'Cannot read the System event log. {error}') from error
    return boots, extras, dict(log='windows-log', **info)


def windows_boots():
    try:
        # Archived System logs add older boots (power events only); the live log is read in full as before.
        (boots, stats), info = _with_archives(lambda archived: _query_system(before=archived))
        if info['archives']:
            print(f"Archived System logs read: {info['archives']}")
    except (OSError, ET.ParseError) as error:
        raise ValueError('Cannot read the System event log; no history was imported. '
                         f'Check event-log access for this account. {error}') from error
    if not boots:
        raise ValueError('No Kernel-General 12 boot starts in the accessible System log; '
                         'retention may be incomplete. No history was imported.')
    # The newest retained General 12 is always excluded. Native boot time is an
    # additional conservative boundary if current-boot events were lost/cleared.
    # Clock corrections mean it need not exactly equal the event timestamp.
    current = boots[-1]['start']
    boots[-1]['is_current'] = True
    try:
        current = min(current, _native_boot_time())
    except (OSError, ValueError, AttributeError) as error:
        print(f'Warning: {error}; conservatively excluding the newest logged boot.', file=sys.stderr)
    counts = stats['counts']
    print(f"System log: {counts['events']} events; Kernel-General 12/13: "
          f"{counts['start']}/{counts['shutdown']}; Kernel-Power 42/107: "
          f"{counts['sleep']}/{counts['power_resume']}; Power-Troubleshooter 1: {counts['troubleshooter_resume']}")
    print(f"Kernel-Boot 27: full={counts['boot_type_0']}, Fast Startup={counts['boot_type_1']}, "
          f"hibernate={counts['boot_type_2']} (resumes stay within their kernel boot).")
    print('Retained System log dates: ' + ' through '.join(
        dt.datetime.fromtimestamp(stats[key]).astimezone().isoformat() for key in ('first', 'last')))
    print('History is limited to retained complete boot starts; unconfirmed ends stop at the last '
          'observation, and unmatched sleep stays excluded to that end.')
    return boots, current


if __name__ == '__main__':
    main(windows_boots, windows_power)
