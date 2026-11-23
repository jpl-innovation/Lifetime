"""How long the server's availability computation takes for a big fleet, with Python and with the Rust accelerator.

    python tools/availability_speed.py [COMPUTERS]

Synthetic data, no database: COMPUTERS (default 500) computers with an online/offline change every 1 to 15 hours for
400 days, and the 365 daily bars of Last 12 months (lifetime_availability.device_availability, as uptime_bars calls
it). Prints the time per engine and checks that both give identical results. Reading the presence from the database
and parsing its times (about 60% of a real uptime_bars request) are not included.
"""
import datetime as dt
import os
import random
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lifetime_availability as availability    # noqa: E402
import lifetime_uptime                           # noqa: E402

UTC = dt.timezone.utc


def fleet(computers):
    now = dt.datetime(2026, 11, 16, 9, 0, 12, 345678, tzinfo=UTC)
    chance, result = random.Random(1), []
    for _ in range(computers):
        moment, state, rows = now - dt.timedelta(days=400), 'online', []
        while moment < now - dt.timedelta(hours=1):
            end = min(moment + dt.timedelta(minutes=chance.randint(60, 900)), now - dt.timedelta(hours=1))
            rows.append((state, moment, end))
            moment, state = end, 'offline' if state == 'online' else 'online'
        rows.append((state, moment, None))
        result.append(rows)
    return result, now


def main():
    computers = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    presence, now = fleet(computers)
    first, last = dt.date(2025, 11, 17), dt.date(2026, 11, 16)
    spans = lifetime_uptime.buckets(first, last, 'day')
    schedule = dict(kind='always', timezone='UTC', start='08:00', end='18:00', weekdays=[0, 1, 2, 3, 4], holidays=[])
    maintenance = [(now - dt.timedelta(days=day, hours=3), now - dt.timedelta(days=day)) for day in range(5, 300, 30)]
    engines = [('Python', None)] + ([('Rust accelerator', availability.lifetime_fast)] if availability.lifetime_fast else [])
    results = {}
    print(f'{computers} computers, {sum(map(len, presence))} presence intervals, {len(spans)} daily bars')
    for name, module in engines:
        with patch.object(availability, 'lifetime_fast', module):
            began = time.perf_counter()
            results[name] = [availability.device_availability(rows, schedule, False, maintenance, first, last, UTC, now,
                                                              spans=spans) for rows in presence]
            print(f'{name}: {time.perf_counter() - began:.2f} s')
    if len(results) == 2:
        same = repr(results['Python']) == repr(results['Rust accelerator'])
        print('identical results' if same else 'DIFFERENT RESULTS')
        return 0 if same else 1
    print('Rust accelerator not installed (lifetime_fast): only Python was measured')
    return 0


if __name__ == '__main__':
    sys.exit(main())
