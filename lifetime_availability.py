"""Availability from monitoring presence history: what was observed, never what was assumed.

For each computer and period every second of its expected-online time (its schedule) is exactly one of:
  good      observed online
  bad       observed offline
  unknown   no observation (server down, never reported, waiting for a report, before monitoring began)
  excluded  inside a maintenance window
Time outside the schedule is not counted at all. Then

  observed availability = 100 * good / (good + bad)      only when good + bad > 0
  coverage              = (good + bad) / (good + bad + unknown)

and a result whose coverage is below the floor is "insufficient evidence". UNKNOWN is never counted as up or down.

Periods and day labels use the computer's calendar: the time zone of its expected-online schedule (the computer does
not report its own zone), so a day is 23 or 25 hours long across a DST change. Pure functions, no I/O.
"""
import datetime as dt

UTC = dt.timezone.utc
FIELDS = ('good_s', 'bad_s', 'unknown_s', 'excluded_s')
DEFAULT_FLOOR = 80      # percent coverage below which a result is "insufficient evidence"


def _merge(intervals):
    result = []
    for start, end in sorted(item for item in intervals if item[0] < item[1]):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _intersect(first, second):
    """Both merged and sorted: one sweep, O(n+m) (a fleet's years of presence, not just one computer's month)."""
    result, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        start, end = max(first[i][0], second[j][0]), min(first[i][1], second[j][1])
        if start < end:
            result.append((start, end))
        if first[i][1] < second[j][1]:
            i += 1
        else:
            j += 1
    return result


def _subtract(first, second):
    """``first`` minus ``second``, both merged and sorted: one sweep."""
    result, j = [], 0
    for start, end in first:
        while j < len(second) and second[j][1] <= start:
            j += 1
        k = j
        while k < len(second) and second[k][0] < end:
            if second[k][0] > start:
                result.append((start, second[k][0]))
            start = max(start, second[k][1])
            if start >= end:
                break
            k += 1
        if start < end:
            result.append((start, end))
    return result


def _spread(intervals, edges):
    """Seconds of sorted, non-overlapping ``intervals`` inside each [edges[i], edges[i+1]) (edges ascending)."""
    totals, k = [0.0] * (len(edges) - 1), 0
    for start, end in intervals:
        while k < len(totals) and edges[k + 1] <= start:
            k += 1
        m = k
        while m < len(totals) and edges[m] < end:
            low, high = max(start, edges[m]), min(end, edges[m + 1])
            if low < high:
                totals[m] += (high - low).total_seconds()
            m += 1
    return totals


def _seconds(intervals):
    return sum((end - start).total_seconds() for start, end in intervals)


def local_midnight(day, zone):
    """The start of a calendar day in ``zone`` (None: the server's own zone), in UTC."""
    moment = dt.datetime.combine(day, dt.time(), tzinfo=zone) if zone else dt.datetime.combine(day, dt.time()).astimezone()
    return moment.astimezone(UTC)


def schedule_windows(schedule, laptop, start, end, zone):
    """UTC intervals inside [start, end) in which the computer is expected online (see lifetime_monitor.expected)."""
    kind = schedule['kind']
    if kind == 'auto':
        kind = 'always' if laptop is False else 'never'
    if kind != 'business':
        return [(start, end)] if kind == 'always' and start < end else []
    opens, closes = dt.time.fromisoformat(schedule['start']), dt.time.fromisoformat(schedule['end'])
    local = (lambda moment: moment.astimezone(zone)) if zone else (lambda moment: moment.astimezone())
    day, last = local(start).date() - dt.timedelta(days=1), local(end).date()
    windows = []
    while day <= last:
        if day.weekday() in schedule['weekdays'] and day.isoformat() not in schedule['holidays']:
            closing_day = day if opens < closes else day + dt.timedelta(days=1)    # hours over midnight
            windows.append((_at(day, opens, zone), _at(closing_day, closes, zone)))
        day += dt.timedelta(days=1)
    return _intersect(_merge(windows), [(start, end)])


def _at(day, time, zone):
    moment = dt.datetime.combine(day, time, tzinfo=zone) if zone else dt.datetime.combine(day, time).astimezone()
    return moment.astimezone(UTC)


def normalize(presence, now):
    """Presence rows (state, start, end or None while open) as non-overlapping UTC intervals per state."""
    states = dict(online=[], offline=[])
    previous = None
    for state, start, end in sorted(presence, key=lambda row: row[1]):
        end = min(end or now, now)
        start = max(start, previous) if previous else start
        if start >= end:
            continue
        previous = end
        if state in states:
            states[state].append((start, end))
    return {state: _merge(items) for state, items in states.items()}


def classify(windows, states, maintenance, start, end):
    """Seconds of good, bad, unknown and excluded time inside [start, end)."""
    scheduled = _intersect(windows, [(start, end)]) if start < end else []
    excluded = _intersect(scheduled, maintenance)
    counted = _subtract(scheduled, maintenance)
    good, bad = _seconds(_intersect(counted, states['online'])), _seconds(_intersect(counted, states['offline']))
    return dict(good_s=good, bad_s=bad, unknown_s=max(0.0, _seconds(counted) - good - bad), excluded_s=_seconds(excluded))


def summarise(totals, floor=DEFAULT_FLOOR):
    """Availability, coverage and a status from summed seconds (one computer, a group or the fleet)."""
    totals = {key: round(totals.get(key, 0)) for key in FIELDS}
    observed = totals['good_s'] + totals['bad_s']
    eligible = observed + totals['unknown_s']
    availability = round(100 * totals['good_s'] / observed, 3) if observed else None
    coverage = round(100 * observed / eligible, 3) if eligible else None
    if not eligible:
        status = 'not-scheduled'        # nothing expected (a laptop with no schedule), or all of it under maintenance
    elif coverage < floor or availability is None:
        status = 'insufficient'
    else:
        status = 'ok'
    return dict(totals, scheduled_s=eligible + totals['excluded_s'], availability=availability, coverage=coverage,
                status=status, coverage_floor=floor)


def outages(counted, offline):
    """(number, longest seconds) of outages: one merged offline interval that overlaps expected-online (counted) time
    is one outage, and its length is the expected-online time it covers. Unknown time is never an outage."""
    lengths, j = [], 0
    for start, end in offline:
        while j < len(counted) and counted[j][1] <= start:
            j += 1
        k, seconds = j, 0.0
        while k < len(counted) and counted[k][0] < end:
            seconds += (min(end, counted[k][1]) - max(start, counted[k][0])).total_seconds()
            k += 1
        if seconds > 0:
            lengths.append(seconds)
    return len(lengths), round(max(lengths, default=0))


def device_availability(presence, schedule, laptop, maintenance, first_day, last_day, zone, now, floor=DEFAULT_FLOOR,
                        daily=True, since=None):
    """One computer over local days first_day..last_day (inclusive) in ``zone``. ``presence``: (state, start, end)
    with UTC datetimes, ``maintenance``: UTC (start, end) pairs that apply to it. Time after ``now`` is not counted,
    nor time before ``since`` (for "everything kept": from the first record, not from that day's midnight).
    The same seconds as ``classify`` per day, in one sweep over the whole period."""
    start, end = local_midnight(first_day, zone), min(local_midnight(last_day + dt.timedelta(days=1), zone), now)
    start = max(start, since) if since else start
    windows = schedule_windows(schedule, laptop, start, end, zone) if start < end else []
    states, cuts = normalize(presence, now), _merge(maintenance)
    count = (last_day - first_day).days + 1
    edges = [min(max(local_midnight(first_day + dt.timedelta(days=offset), zone), start), max(start, end))
             for offset in range(count + 1)]
    scheduled = _intersect(windows, [(start, end)]) if start < end else []
    counted = _subtract(scheduled, cuts)
    good, bad = _spread(_intersect(counted, states['online']), edges), _spread(_intersect(counted, states['offline']), edges)
    expected, excluded = _spread(counted, edges), _spread(_intersect(scheduled, cuts), edges)
    days, totals = [], dict.fromkeys(FIELDS, 0.0)
    for index in range(count):
        part = dict(good_s=good[index], bad_s=bad[index], unknown_s=max(0.0, expected[index] - good[index] - bad[index]),
                    excluded_s=excluded[index])
        for key in FIELDS:
            totals[key] += part[key]
        if daily:
            days.append(dict(date=(first_day + dt.timedelta(days=index)).isoformat(),
                             **{key: round(value) for key, value in part.items()}))
    number, longest = outages(counted, states['offline'])
    return dict(summarise(totals, floor), daily=days, outages=number, longest_outage_s=longest)


def combine(results, floor=DEFAULT_FLOOR):
    """A group or the fleet: seconds add up across computers, then one availability from the sums."""
    return summarise({key: sum(item[key] for item in results) for key in FIELDS}, floor)
