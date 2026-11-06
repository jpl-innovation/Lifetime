#!/usr/bin/env python3
"""Reconstruct Windows awake history from the retained System event log."""
from bisect import bisect_right
from collections import Counter
import ctypes
import datetime as dt
import io
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

from lifetime_import import main


def _timestamp(value):
    # Event XML is invariant UTC, regardless of the Windows display language.
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
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


def _query_system():
    # /uni:true avoids the active code page and localized message decoding.
    # /e:Events supplies a root for iterparse. No message rendering, elevation,
    # event count cap, or query of a restricted log such as Security is needed.
    args = ['wevtutil', 'qe', 'System', '/f:xml', '/e:Events', '/rd:false', '/uni:true']
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
            result = _parse_events(_xml_events(process.stdout))
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


def windows_boots():
    try:
        boots, stats = _query_system()
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
    main(windows_boots)
