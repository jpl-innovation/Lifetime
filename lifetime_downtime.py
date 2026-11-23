"""Uptime and downtime per local day, from power evidence. Stdlib only; no ledger or log I/O here.

Ledger key ``downtime_daily`` (the 1.3.0 contract shared with the server):
``{"2026-11-20": {"asleep_s": 25200, "off_s": 3600, "unknown_s": 0, "restarts": 1, "shutdowns": 1, "unexpected_stops": 0}}``.
Keys are local ISO dates; all six fields are integers >= 0; asleep + off + unknown plus that day's awake seconds never
exceed the day's length. A missing day means unknown, never zero. Awake time itself stays in ``daily`` and is never
changed here: this module only reads it.
"""
import datetime as dt
import math

FIELDS = ('asleep_s', 'off_s', 'unknown_s', 'restarts', 'shutdowns', 'unexpected_stops')
COUNTS = ('restarts', 'shutdowns', 'unexpected_stops')
MAX_DAY = 90000          # seconds; a DST day can be 25 hours
MAX_DAYS = 3660          # days kept and sent, the same limit as daily awake totals
RESTART_GAP = 600        # a clean stop followed by a start this soon is a restart (locale-free rule)
FAST_STARTUP_GAP = 120   # a shutdown request followed this soon by hibernation is Windows Fast Startup's "shut down"
STOP_WINDOW = 900        # Kernel-Power 41 / EventLog 6008 are written within minutes of the next start
EVENT_KINDS = ('start', 'shutdown', 'restart', 'sleep', 'wake', 'unexpected')


# --- validation (ledger and the server contract) ---

def is_count(value):
    return type(value) is int and value >= 0


def valid_day(value):
    return (isinstance(value, dict) and set(value) == set(FIELDS) and all(is_count(value[k]) for k in FIELDS)
            and value['asleep_s'] + value['off_s'] + value['unknown_s'] <= MAX_DAY)


def valid_date(value):
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def problems(value):
    """Reasons ``value`` is not a valid ``downtime_daily`` object (empty list when it is)."""
    if not isinstance(value, dict):
        return ['must be an object keyed by dates']
    return [f'invalid entry {day!r}' for day, entry in value.items() if not valid_date(day) or not valid_day(entry)]


# --- local days ---

def day_start(day, tz=None):
    """Epoch seconds of local midnight (tz None = this computer's zone, with its historical rules)."""
    moment = dt.datetime.combine(day, dt.time())
    return moment.replace(tzinfo=tz).timestamp() if tz is not None else moment.timestamp()


def day_length(day, tz=None, now=None):
    """Seconds in a local day (23-25 h around DST); for the current day, only the part already passed."""
    start = day_start(day, tz)
    end = day_start(day + dt.timedelta(days=1), tz)
    if now is not None:
        end = min(end, now)
    return max(0.0, end - start)


def local_day(stamp, tz=None):
    return dt.datetime.fromtimestamp(stamp, tz).date()


def split(start, end, tz=None):
    """(local date, seconds) pieces of an interval, cut at local midnights."""
    while start < end:
        day = local_day(start, tz)
        stop = min(end, day_start(day + dt.timedelta(days=1), tz))
        if stop <= start:       # a clock that cannot advance: never loop forever
            break
        yield day, stop - start
        start = stop


# --- evidence -> per-day totals and an event list ---

def _sleep_intervals(boot):
    """Asleep spans inside one boot: the boot minus its awake spans (same rule as the history import)."""
    start, end = boot['start'], boot['end']
    cursor, sleeping, result = start, False, []
    asleep_from = None
    for stamp, kind in sorted(boot.get('events', [])):
        if stamp > end:
            break
        stamp = max(start, stamp)
        if kind == 'sleep' and not sleeping:
            sleeping, asleep_from = True, stamp
        elif kind == 'wake' and sleeping:
            if stamp > asleep_from:
                result.append((asleep_from, stamp))
            sleeping = False
    if sleeping and end > asleep_from:
        result.append((asleep_from, end))
    return result


def _near(items, stamp, before, after):
    """The item in ``items`` (sorted (time, value) pairs) closest to ``stamp`` within [stamp-before, stamp+after]."""
    found = [(abs(t - stamp), value) for t, value in items if stamp - before <= t <= stamp + after]
    return min(found, key=lambda pair: pair[0])[1] if found else None


def from_boots(boots, extras=None, now=None, tz=None):
    """Turn boots (``start``, ``end``, ``clean``, ``events`` of (time, 'sleep'|'wake')) into per-day evidence.

    ``extras``: ``requests`` [(time, {'kind': 'restart'|'shutdown'|None, 'by': str, 'reason': str})] from Windows
    event 1074; ``stops`` [time] of Kernel-Power 41 / EventLog 6008 (or a Linux crash record), one per incident;
    ``wakes`` [(time, wake source text)]. Returns dict(start, end, days, events), where ``days`` hold float seconds
    and counts, and ``start`` is the earliest instant the evidence speaks for (the first boot's start).
    A gap after an end that was not clean is left unknown: no evidence says when the computer stopped.
    """
    extras = extras or {}
    requests = sorted(extras.get('requests', []), key=lambda item: item[0])
    stops = sorted(extras.get('stops', []))
    wakes = sorted(extras.get('wakes', []), key=lambda item: item[0])
    boots = sorted(boots, key=lambda b: b['start'])
    days, events = {}, []

    def bucket(day):
        return days.setdefault(day.isoformat(), dict(asleep_s=0.0, off_s=0.0, restarts=0, shutdowns=0, unexpected_stops=0))

    def add(start, end, field):
        for day, seconds in split(start, end, tz):
            bucket(day)[field] += seconds

    def event(stamp, kind, **detail):
        events.append(dict(time=stamp, kind=kind, by=detail.get('by', ''), reason=detail.get('reason', ''),
                           source=detail.get('source', '')))

    for index, boot in enumerate(boots):
        event(boot['start'], 'start')
        for start, end in _sleep_intervals(boot):
            request = _near(requests, start, FAST_STARTUP_GAP, 5)
            if request is not None:        # "Shut down" with Fast Startup hibernates the kernel: it is a shutdown
                add(start, end, 'off_s')
                bucket(local_day(start, tz))['shutdowns'] += 1
                event(start, 'shutdown', by=request.get('by', ''), reason=request.get('reason', ''), source='fast-startup')
                event(end, 'start', source='fast-startup')
            else:
                add(start, end, 'asleep_s')
                event(start, 'sleep')
                event(end, 'wake', source=_near(wakes, end, 120, 120) or '')
        if index + 1 >= len(boots):
            continue
        following = boots[index + 1]
        if following['start'] <= boot['end']:
            continue
        stopped = any(following['start'] - 60 <= t <= following['start'] + STOP_WINDOW for t in stops)
        if boot.get('clean'):
            request = _near(requests, boot['end'], 900, 5) or {}
            kind = request.get('kind') or ('restart' if following['start'] - boot['end'] <= RESTART_GAP else 'shutdown')
            add(boot['end'], following['start'], 'off_s')
            bucket(local_day(boot['end'], tz))['restarts' if kind == 'restart' else 'shutdowns'] += 1
            event(boot['end'], kind, by=request.get('by', ''), reason=request.get('reason', ''))
        elif stopped:
            bucket(local_day(boot['end'], tz))['unexpected_stops'] += 1
            event(boot['end'], 'unexpected')
    events.sort(key=lambda e: (e['time'], EVENT_KINDS.index(e['kind'])))
    return dict(start=boots[0]['start'] if boots else None, end=now, days=days, events=events)


# --- writing into the ledger ---

def settle(downtime, daily, now=None, tz=None):
    """Recompute ``unknown_s`` from today's awake totals, keeping the contract after awake time moved.

    Awake time wins: if awake + asleep + off would exceed the day, off and then asleep are reduced. Returns a new
    dict of valid entries only; ``daily`` is only read.
    """
    now = now if now is not None else dt.datetime.now().timestamp()
    today = local_day(now, tz)
    result = {}
    for key, entry in (downtime or {}).items():
        if not valid_date(key) or not isinstance(entry, dict):
            continue
        day = dt.date.fromisoformat(key)
        if day > today:
            continue
        length = int(day_length(day, tz, now if day == today else None))
        awake = daily.get(key, 0)
        awake = min(length, int(math.ceil(awake))) if isinstance(awake, (int, float)) and math.isfinite(awake) and awake > 0 else 0
        asleep = min(max(0, int(round(_number(entry.get('asleep_s'))))), length - awake)
        off = min(max(0, int(round(_number(entry.get('off_s'))))), length - awake - asleep)
        value = dict(asleep_s=asleep, off_s=off, unknown_s=length - awake - asleep - off,
                     **{k: max(0, int(_number(entry.get(k)))) for k in COUNTS})
        if valid_day(value):
            result[key] = value
    cutoff = (today - dt.timedelta(days=MAX_DAYS - 1)).isoformat()
    return {key: result[key] for key in sorted(result) if key >= cutoff}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0


def apply(ledger, result, sources=None, now=None, tz=None):
    """Merge ``from_boots`` evidence into ``ledger['downtime_daily']``; never touches awake accounting.

    Every day from the evidence's first day to today is written (a day the evidence covers with no sleep or stop
    is still evidence: its remainder is awake or unknown). Days inside the evidence replace what was saved. The
    first day is only partly covered, so it replaces a saved day only when that leaves less unknown. Days the
    evidence no longer reaches (rotated logs) are kept.
    """
    now = now if now is not None else dt.datetime.now().timestamp()
    stored = dict(ledger.get('downtime_daily') or {})
    if result.get('start') is not None:
        first = local_day(result['start'], tz)
        today = local_day(now, tz)
        fresh = {}
        day = first
        while day <= today:
            key = day.isoformat()
            fresh[key] = {**dict(asleep_s=0.0, off_s=0.0, restarts=0, shutdowns=0, unexpected_stops=0),
                          **result['days'].get(key, {})}
            day += dt.timedelta(days=1)
        fresh = settle(fresh, ledger.get('daily', {}), now, tz)
        first_key = first.isoformat()
        for key, entry in fresh.items():
            old = stored.get(key)
            if key == first_key and isinstance(old, dict) and _number(old.get('unknown_s')) < entry['unknown_s']:
                continue
            stored[key] = entry
    ledger['downtime_daily'] = settle(stored, ledger.get('daily', {}), now, tz)
    if sources is not None:
        previous = ledger.get('downtime_sources') if isinstance(ledger.get('downtime_sources'), dict) else {}
        merged = dict(previous, **sources)
        dates = [d for d in (previous.get('log_from'), sources.get('log_from')) if valid_date(d)]
        if dates:
            merged['log_from'] = min(dates)
        ledger['downtime_sources'] = merged
    return ledger


def sources_for(result, log, archives=0, archive_note='none', now=None, tz=None, **more):
    """The coverage facts saved beside ``downtime_daily`` (no paths, names or accounts)."""
    now = now if now is not None else dt.datetime.now().timestamp()
    value = dict(log=log, archives=archives, archive_note=archive_note, note=None,
                 checked=dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec='seconds'), **more)
    if result.get('start') is not None:
        value['log_from'] = local_day(result['start'], tz).isoformat()
        boots = [e['time'] for e in result['events'] if e['kind'] == 'start' and e.get('source') != 'fast-startup']
        if boots:
            value['last_boot'] = boots[-1]
    return value


def for_server(ledger, now=None, tz=None):
    """What a connected computer may send: valid entries only, unknown re-settled, at most MAX_DAYS days."""
    return settle(ledger.get('downtime_daily') or {}, ledger.get('daily', {}), now, tz)


# --- reading: ranges, totals and counts ---

PERIODS = ('today', '7d', '30d', 'this_month', 'last_month', 'this_year', '12m', '5y', 'all', 'custom')


def _years_back(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:          # 29 February
        return day.replace(year=day.year - years, day=28)


def period_bounds(choice, today, earliest=None, start=None, end=None):
    """Inclusive local dates for a quick choice. ``custom`` uses start/end, clamped so the end is never in the future."""
    if choice == 'today':
        return today, today
    if choice in ('7d', '30d'):
        return today - dt.timedelta(days=int(choice[:-1]) - 1), today
    if choice == 'this_month':
        return today.replace(day=1), today
    if choice == 'last_month':
        last = today.replace(day=1) - dt.timedelta(days=1)
        return last.replace(day=1), last
    if choice == 'this_year':
        return today.replace(month=1, day=1), today
    if choice == '12m':
        return _years_back(today, 1) + dt.timedelta(days=1), today
    if choice == '5y':
        return _years_back(today, 5) + dt.timedelta(days=1), today
    if choice == 'all':
        return min(earliest or today, today), today
    if choice == 'custom' and start is not None and end is not None:
        end = min(end, today)
        return min(start, end), end
    return today - dt.timedelta(days=29), today


def earliest_day(ledger):
    """The first local date any saved evidence speaks for."""
    keys = [k for k in (*ledger.get('daily', {}), *(ledger.get('downtime_daily') or {})) if valid_date(k)]
    return dt.date.fromisoformat(min(keys)) if keys else None


def summarize(ledger, start, end, events=None, now=None, tz=None):
    """Totals, counts and per-day rows for [start, end]. Days with no evidence are unknown, never zero."""
    now = now if now is not None else dt.datetime.now().timestamp()
    today = local_day(now, tz)
    end = min(end, today)
    daily = ledger.get('daily', {})
    downtime = settle({k: v for k, v in (ledger.get('downtime_daily') or {}).items()
                       if valid_date(k) and start.isoformat() <= k <= end.isoformat()}, daily, now, tz)
    rows = []
    totals = dict(awake=0, asleep=0, off=0, unknown=0, restarts=0, shutdowns=0, unexpected_stops=0)
    day = start
    while day <= end:
        key = day.isoformat()
        length = int(day_length(day, tz, now if day == today else None))
        entry = downtime.get(key)
        if entry:
            asleep, off, unknown = entry['asleep_s'], entry['off_s'], entry['unknown_s']
            awake = length - asleep - off - unknown
            counts = {k: entry[k] for k in COUNTS}
        else:
            raw = daily.get(key)
            awake = min(length, int(math.ceil(raw))) if isinstance(raw, (int, float)) and math.isfinite(raw) and raw > 0 else 0
            asleep = off = 0
            unknown = length - awake
            counts = dict.fromkeys(COUNTS, 0)
        rows.append(dict(day=day, awake=awake, asleep=asleep, off=off, unknown=unknown,
                         evidence=bool(entry) or key in daily, **counts))
        for name, value in (('awake', awake), ('asleep', asleep), ('off', off), ('unknown', unknown), *counts.items()):
            totals[name] += value
        day += dt.timedelta(days=1)
    known = totals['awake'] + totals['asleep'] + totals['off']
    totals['share'] = totals['awake'] / known if known else None
    totals['between_stops'] = totals['awake'] / totals['unexpected_stops'] if totals['unexpected_stops'] else None
    totals['days'] = len(rows)
    totals['evidence_days'] = sum(row['evidence'] for row in rows)
    longest = longest_spans(events, day_start(start, tz), min(now, day_start(end + dt.timedelta(days=1), tz))) if events is not None else {}
    totals['longest_awake'] = longest.get('awake')
    totals['longest_off'] = longest.get('off')
    return dict(totals=totals, rows=rows)


def longest_spans(events, start, end):
    """Longest continuous awake and off time inside [start, end] from an ordered event list."""
    awake_from = off_from = None
    best = dict(awake=None, off=None)

    def close(kind, begin, stop):
        begin, stop = max(begin, start), min(stop, end)
        if stop > begin and (best[kind] is None or stop - begin > best[kind]):
            best[kind] = stop - begin

    for item in sorted(events or [], key=lambda e: e['time']):
        stamp, kind = item['time'], item['kind']
        if kind in ('start', 'wake'):
            if kind == 'start' and off_from is not None:
                close('off', off_from, stamp)
            off_from = None
            awake_from = stamp
        elif kind in ('sleep', 'shutdown', 'restart', 'unexpected'):
            if awake_from is not None:
                close('awake', awake_from, stamp)
            awake_from = None
            off_from = stamp if kind in ('shutdown', 'restart') else None
    if awake_from is not None:
        close('awake', awake_from, end)
    return best


def coverage(ledger):
    """Facts for the coverage line: where the data starts, from which source, and when Lifetime began recording."""
    sources = ledger.get('downtime_sources') if isinstance(ledger.get('downtime_sources'), dict) else {}
    candidates = []
    if valid_date(sources.get('log_from')):
        candidates.append((sources['log_from'], sources.get('log') or 'log'))
    imported = ledger.get('imported') or {}
    if valid_date(imported.get('first_day')):
        candidates.append((imported['first_day'], 'imported'))
    daily = [k for k in ledger.get('daily', {}) if valid_date(k)]
    if daily:
        candidates.append((min(daily), 'lifetime'))
    first, source = min(candidates, key=lambda item: item[0]) if candidates else (None, None)   # a log wins a tie
    recorded = ledger.get('recording_started') or ((imported.get('original') or {}).get('tracking_started')) or ledger.get('tracking_started')
    try:
        recorded = dt.datetime.fromisoformat(str(recorded).replace('Z', '+00:00')).astimezone().date().isoformat() if recorded and len(str(recorded)) > 10 else (recorded if valid_date(recorded) else None)
    except (ValueError, OverflowError, OSError):
        recorded = None
    return dict(first=first, source=source, recorded=recorded, log=sources.get('log'),
                archives=sources.get('archives', 0) if is_count(sources.get('archives', 0)) else 0,
                archive_note=sources.get('archive_note'), wtmp=sources.get('wtmp', 0), note=sources.get('note'))
