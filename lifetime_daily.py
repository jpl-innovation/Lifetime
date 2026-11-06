"""Local civil-time allocation of awake seconds; no ledger I/O or Qt dependency."""
import datetime as dt
import json
import math
import sys
import time


def awake_intervals(start, end, events):
    """Exclude sleep -> resume pairs, including an unfinished sleep at the end."""
    cursor, sleeping, spans = start, False, []
    for stamp, kind in sorted(events):
        if stamp < start or stamp > end:
            continue
        if kind == 'sleep' and not sleeping:
            if stamp > cursor:
                spans.append((cursor, stamp))
            sleeping = True
        elif kind == 'wake' and sleeping:
            cursor, sleeping = stamp, False
    if not sleeping and end > cursor:
        spans.append((cursor, end))
    return spans


def boot_history(end=None):
    """Return (boot epoch, awake intervals, estimated) for the current OS boot.

    Windows uses the same 42 -> 27 boundaries as verify_awake. Linux uses
    realtime/monotonic journal pairs, so translated journal messages are irrelevant.
    Retention gaps and failed probes explicitly select a wall-time estimate.
    """
    from lifetime_signals import powershell, parse_json, command
    end = time.time() if end is None else end
    start = None
    try:
        if sys.platform == 'win32':
            from lifetime_import_windows import _native_boot_time
            start = _native_boot_time()
            data = parse_json(powershell(r"""
$ErrorActionPreference='Stop'
$boot=[DateTimeOffset]::FromUnixTimeMilliseconds(__BOOT_MS__).LocalDateTime
$old=Get-WinEvent -LogName System -Oldest -MaxEvents 1
$found=@()
try {$found=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName=@('Microsoft-Windows-Kernel-Power','Microsoft-Windows-Kernel-Boot');Id=@(42,27);StartTime=$boot} -ErrorAction Stop)}
catch {if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {throw}}
@{boot=([DateTimeOffset]$boot).ToUnixTimeMilliseconds()/1000;covered=($old.TimeCreated -le $boot);
events=@($found|ForEach-Object {@{time=([DateTimeOffset]$_.TimeCreated).ToUnixTimeMilliseconds()/1000;id=$_.Id;provider=$_.ProviderName}})}|ConvertTo-Json -Depth 5 -Compress
""".replace('__BOOT_MS__', str(round(start*1000)))))
            if not isinstance(data, dict) or not data.get('covered'):
                raise ValueError('Incomplete System log')
            start = data['boot']
            events = [(e['time'], 'sleep' if e['id'] == 42 else 'wake')
                      for e in data.get('events', [])
                      if (e['id'], e['provider']) in (
                          (42, 'Microsoft-Windows-Kernel-Power'),
                          (27, 'Microsoft-Windows-Kernel-Boot'))]
        else:
            from pathlib import Path
            start = time.time()-float(Path('/proc/uptime').read_text().split()[0])
            raw = command(['journalctl', '-k', '-b', '0', '-o', 'json', '--no-pager'])
            pairs = sorted((int(row['__REALTIME_TIMESTAMP'])/1e6,
                            int(row['__MONOTONIC_TIMESTAMP'])/1e6)
                           for row in (json.loads(line) for line in (raw or '').splitlines())
                           if '__REALTIME_TIMESTAMP' in row and '__MONOTONIC_TIMESTAMP' in row)
            if not pairs or pairs[0][1] > 60:
                raise ValueError('Incomplete journal')
            events = []
            for (wall_a, mono_a), (wall_b, mono_b) in zip(pairs, pairs[1:]):
                gap = (wall_b-wall_a)-(mono_b-mono_a)
                if gap > 1:
                    events.extend(((wall_b-gap, 'sleep'), (wall_b, 'wake')))
        if start >= end:
            raise ValueError('Snapshot predates this boot')
        return start, awake_intervals(start, end, events), False
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return start, [(start, end)] if start is not None and start < end else [], True


def cells(spans, tz=None):
    """Split epoch intervals at local hour boundaries, preserving historical TZ rules."""
    result = {}
    for start, end in spans:
        while start < end:
            local = dt.datetime.fromtimestamp(start, tz)
            stop = min(end, start+3600-local.minute*60-local.second-local.microsecond/1e6)
            key = (local.date().isoformat(), local.hour)
            result[key] = result.get(key, 0.0)+stop-start
            start = stop
    return result


def distribute(seconds, end, daily, hourly, evidence=None, tz=None, dated=None):
    """Scale evidence to the exact credit, bounded by 24 h per local date.

    Cap saturated dates and rescale the other logged intervals. Use wall-time
    weights only when evidence is missing or has insufficient date capacity.
    Return provenance; never silently discard seconds or cap the lifetime total.
    """
    if seconds <= 0:
        return dict(estimated=False, seconds=0)
    start, spans, estimated = boot_history(end) if evidence is None else evidence
    if start is None or start >= end:
        start, spans, estimated = end-seconds, [(end-seconds, end)], True
    weights = cells(spans, tz)
    weight_sum = math.fsum(weights.values())
    by_day = {}
    for (day, _), value in weights.items():
        by_day[day] = by_day.get(day, 0)+value
    if not weight_sum or sum(max(0, 86400-daily.get(day, 0)) for day in by_day) < seconds-1e-7:
        weights, estimated = cells([(start, end)], tz), True
    source = 'wall-time-estimate' if estimated else 'logged-intervals'
    # Capped proportional allocation. Each day's remaining capacity is shared
    # across its local hour cells. This also enforces the explicit 24 h DST cap.
    remaining = seconds
    allocation = {}
    pending = dict(weights)
    while remaining > 1e-8 and pending:
        scale = remaining/math.fsum(pending.values())
        day_weights = {}
        for (day, hour), value in pending.items():
            day_weights[day] = day_weights.get(day, 0)+value
        saturated = {day for day, value in day_weights.items()
                     if value*scale > max(0, 86400-daily.get(day, 0))+1e-8}
        if not saturated:
            for key, value in pending.items():
                allocation[key] = value*scale
            remaining = 0
            break
        for day in saturated:
            capacity = max(0, 86400-daily.get(day, 0))
            for key in [key for key in pending if key[0] == day]:
                allocation[key] = capacity*pending.pop(key)/day_weights[day]
            remaining -= capacity
        estimated = True
    if remaining > 1e-6:
        raise ValueError('Awake credit exceeds available local-day capacity')
    if allocation:
        key = max(allocation, key=allocation.get)
        allocation[key] += seconds-math.fsum(allocation.values())
    for (day, hour), value in allocation.items():
        daily[day] = min(86400.0, daily.get(day, 0)+value)
        hourly[hour] += value
        if dated is not None:
            dated.setdefault(day, [0.0]*24)[hour] += value
    return dict(estimated=estimated, source=source, seconds=seconds, start=start, end=end)
