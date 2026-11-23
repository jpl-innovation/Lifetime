"""Date ranges, chart buckets and the uptime/downtime breakdown per day for Lifetime Server (pure: no I/O, no Qt).

Definitions (docs/UPGRADE_PLAN.md section 4): uptime is awake time (history.daily). Off time per local day is split
into asleep, off (shut down or restarting) and unknown, with counts of restarts, clean shutdowns and unexpected stops
(history.downtime_daily, sent by 1.3 computers). A day the computer did not describe is unknown, never zero.
"""
import datetime as dt

DAY = dt.timedelta(days=1)
DAY_SECONDS = 86400
MAX_DAYS = 3660                 # ten years: as far back as computers send daily totals
EARLIEST = dt.date(1971, 1, 1)  # local times before 1970 cannot be computed on Windows; no Lifetime data is that old
PRESETS = ('7d', '30d', 'this-month', 'last-month', 'this-year', '12m', '5y', 'all', 'custom')
PRESET_TEXT = {'7d': 'Last 7 days', '30d': 'Last 30 days', 'this-month': 'This month', 'last-month': 'Last month',
               'this-year': 'This year', '12m': 'Last 12 months', '5y': 'Last 5 years', 'all': 'All',
               'custom': 'Custom'}
GOOD_PERCENT, PARTIAL_PERCENT = 99.0, 75.0      # fleet uptime bars: good / partial / down
PARTS = ('awake_s', 'asleep_s', 'off_s', 'unknown_s')
COUNTS = ('restarts', 'shutdowns', 'unexpected_stops')


def years_back(day, years):
    """The same date ``years`` earlier; 29 February becomes the 28th."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def preset_range(key, today):
    """(first, last), both included, for a quick choice; None for 'all' and 'custom'."""
    if key == '7d':
        return today - 6 * DAY, today
    if key == '30d':
        return today - 29 * DAY, today
    if key == 'this-month':
        return today.replace(day=1), today
    if key == 'last-month':
        end = today.replace(day=1) - DAY
        return end.replace(day=1), end
    if key == 'this-year':
        return today.replace(month=1, day=1), today
    if key == '12m':
        return years_back(today, 1) + DAY, today
    if key == '5y':
        return years_back(today, 5) + DAY, today
    return None


def previous_period(first, last):
    """The period of the same length just before first..last."""
    length = last - first + DAY
    return first - length, first - DAY


def bucket_kind(first, last):
    """Days up to three months, weeks up to two years, months beyond, so a chart stays readable."""
    days = (last - first).days + 1
    return 'day' if days <= 92 else 'week' if days <= 731 else 'month'


def buckets(first, last, kind=None):
    """[(start, end)] inclusive dates covering first..last: single days, weeks from Monday or calendar months,
    the first and last clipped to the range."""
    kind = kind or bucket_kind(first, last)
    result, start = [], first
    while start <= last:
        if kind == 'day':
            end = start
        elif kind == 'week':
            end = start + (6 - start.weekday()) * DAY
        else:
            end = (start.replace(day=28) + 4 * DAY).replace(day=1) - DAY
        end = min(end, last)
        result.append((start, end))
        start = end + DAY
    return result


def daily_buckets(daily, first, last, kind=None):
    """{ISO date: seconds} summed per bucket: dict(start, end, days, known, seconds). ``seconds`` is None when no day
    of the bucket has a value; days without one are left out, never counted as zero."""
    result = []
    for start, end in buckets(first, last, kind):
        values = [daily[day.isoformat()] for day in _days(start, end)
                  if isinstance(daily.get(day.isoformat()), (int, float))]
        result.append(dict(start=start, end=end, days=(end - start).days + 1, known=len(values),
                           seconds=sum(values) if values else None))
    return result


def series(summary):
    """A period summary with per-day values ({first, last, daily, ...}) as chart bars: the same summary with ``kind``
    and ``series`` [[bucket start, sum or None]] instead of ``daily``, so a ten-year reply stays small."""
    first, last = dt.date.fromisoformat(summary['first']), dt.date.fromisoformat(summary['last'])
    kind = bucket_kind(first, last)
    result = {key: value for key, value in summary.items() if key != 'daily'}
    result.update(kind=kind, series=[[row['start'].isoformat(), None if row['seconds'] is None else round(row['seconds'], 4)]
                                     for row in daily_buckets(summary['daily'], first, last, kind)])
    return result


def _days(first, last):
    return (first + offset * DAY for offset in range((last - first).days + 1))


def day_breakdown(history, first, last, now=None):
    """One dict per local day first..last: awake (history.daily), asleep, off and unknown seconds and the counts
    (history.downtime_daily). Without a downtime entry only awake is known (asleep, off and the counts are None) and
    the rest of the day is unknown. Today counts only the time so far; days after today have no length."""
    history = history or {}
    awake, downtime = history.get('daily') or {}, history.get('downtime_daily') or {}
    now = now or dt.datetime.now().astimezone()
    today, elapsed = now.date(), now.hour * 3600 + now.minute * 60 + now.second
    result = []
    for day in _days(first, last):
        key = day.isoformat()
        length = DAY_SECONDS if day < today else elapsed if day == today else 0
        seconds = awake.get(key) if isinstance(awake.get(key), (int, float)) else None
        entry = downtime.get(key)
        item = dict(date=key, reported=entry is not None, awake_s=seconds)
        if entry is not None:
            item.update({part: entry[part] for part in ('asleep_s', 'off_s', 'unknown_s')},
                        **{count: entry[count] for count in COUNTS})
            item['unknown_s'] += max(0, length - (seconds or 0) - entry['asleep_s'] - entry['off_s'] - entry['unknown_s'])
        else:
            item.update(asleep_s=None, off_s=None, unknown_s=max(0, length - (seconds or 0)),
                        **dict.fromkeys(COUNTS))
        result.append(item)
    return result


def total(days):
    """Sums over day_breakdown items. A part or count is None when no day knows it. ``awake_share`` is awake out of
    the known time (awake + asleep + off) on days the computer described; ``awake_per_stop`` is the awake time per
    unexpected stop (None without stops)."""
    result = dict(days=len(days), reported=sum(day['reported'] for day in days))
    for key in (*PARTS, *COUNTS):
        values = [day[key] for day in days if day[key] is not None]
        result[key] = sum(values) if values else None
    described = [day for day in days if day['reported']]
    known = sum((day['awake_s'] or 0) + day['asleep_s'] + day['off_s'] for day in described)
    result['awake_share'] = round(100 * sum(day['awake_s'] or 0 for day in described) / known, 1) if known else None
    stops = result['unexpected_stops']
    result['awake_per_stop'] = round(result['awake_s'] / stops) if stops and result['awake_s'] else None
    return result


def breakdown_buckets(days, first, last, kind=None):
    """day_breakdown items summed per bucket (see ``total``), each with its start and end."""
    index = {day['date']: day for day in days}
    return [dict(total([index[day.isoformat()] for day in _days(start, end)]), start=start, end=end)
            for start, end in buckets(first, last, kind)]


def bar_status(good, bad, unknown, floor=80):
    """One fleet uptime bar from seconds in expected hours: 'none' (not expected online), 'unknown' (coverage below
    the floor), 'good' (99 % or more), 'partial' (75 % or more) or 'down'."""
    observed = good + bad
    if not observed + unknown:
        return 'none'
    if not observed or 100 * observed / (observed + unknown) < floor:
        return 'unknown'
    share = 100 * good / observed
    return 'good' if share >= GOOD_PERCENT else 'partial' if share >= PARTIAL_PERCENT else 'down'
