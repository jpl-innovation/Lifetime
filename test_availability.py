"""Availability golden cases with hand-calculated numbers. No real clock, no I/O beyond a temporary database."""
import datetime as dt
import io
import struct
import unittest
from zoneinfo import ZoneInfo

import lifetime_availability as availability
from test_monitor import ID, MonitorCase, T0, stamp

UTC = dt.timezone.utc
HOUR = 3600
ALWAYS = dict(kind='always', timezone='UTC', start='08:00', end='18:00', weekdays=[0, 1, 2, 3, 4], holidays=[])
AUTO = dict(ALWAYS, kind='auto')


def at(day, hour, minute=0):
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def new_york():
    """A real TZif for the 2026 US transitions (8 March, 1 November): Windows Python ships no IANA database."""
    transitions = [int(dt.datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp()),
                   int(dt.datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp())]
    tzif = (b'TZif\0' + bytes(15) + struct.pack('>6I', 0, 0, 0, 2, 2, 8) + struct.pack('>2i', *transitions)
            + bytes([1, 0]) + struct.pack('>iBBiBB', -18000, 0, 0, -14400, 1, 4) + b'EST\0EDT\0')
    return ZoneInfo.from_file(io.BytesIO(tzif), key='Test/New_York_2026')


def run(presence, schedule=ALWAYS, laptop=False, maintenance=(), first=dt.date(2026, 11, 16), last=None, zone=UTC,
        now=at(30, 0), floor=80):
    return availability.device_availability(presence, schedule, laptop, list(maintenance), first, last or first, zone,
                                            now, floor)


class GoldenCases(unittest.TestCase):
    def assertTotals(self, result, good, bad, unknown, excluded):
        self.assertEqual([result[key] for key in availability.FIELDS], [good, bad, unknown, excluded])

    def test_24x7_server_with_an_outage_of_its_own(self):
        # Automatic schedule, a desktop: expected all day. Offline 10:00-11:30 = 1.5 h of 24.
        result = run([('online', at(16, 0), at(16, 10)), ('offline', at(16, 10), at(16, 11, 30)),
                      ('online', at(16, 11, 30), None)], schedule=AUTO)
        self.assertTotals(result, 22.5 * HOUR, 1.5 * HOUR, 0, 0)
        self.assertEqual((result['availability'], result['coverage'], result['status']), (93.75, 100.0, 'ok'))

    def test_business_hours_pc_counts_only_its_hours(self):
        # Mon-Fri 08:00-17:00 at +07:00 (01:00-10:00 UTC). On every weekday from 07:30 to 17:30 except Wednesday,
        # when it is off all day; weekends and nights are not counted. 4 x 9 h good, 9 h bad: 80 %.
        plus7 = dt.timezone(dt.timedelta(hours=7))
        schedule = dict(ALWAYS, kind='business', timezone='+07:00', start='08:00', end='17:00')
        presence = []
        for day in range(16, 21):
            if day == 18:
                presence.append(('offline', at(day, 0), at(day, 23)))
            else:
                presence += [('online', at(day, 0, 30), at(day, 10, 30)), ('offline', at(day, 10, 30), at(day, 23))]
        result = run(presence, schedule, first=dt.date(2026, 11, 16), last=dt.date(2026, 11, 22), zone=plus7)
        self.assertTotals(result, 36 * HOUR, 9 * HOUR, 0, 0)
        self.assertEqual((result['availability'], result['coverage'], result['scheduled_s']), (80.0, 100.0, 45 * HOUR))
        self.assertEqual([day['date'] for day in result['daily']][:2], ['2026-11-16', '2026-11-17'])
        self.assertEqual(result['daily'][5]['good_s'] + result['daily'][5]['bad_s'], 0)     # Saturday

    def test_laptop_with_no_schedule_is_not_scheduled_rather_than_down(self):
        result = run([('offline', at(16, 0), None)], schedule=AUTO, laptop=True)
        self.assertTotals(result, 0, 0, 0, 0)
        self.assertEqual((result['availability'], result['coverage'], result['status']), (None, None, 'not-scheduled'))

    def test_server_outage_is_unknown_never_up_or_down(self):
        # Online 00-08, the Lifetime server down 08-12 (unknown), online 12-20, then no record at all (a gap).
        presence = [('online', at(16, 0), at(16, 8)), ('unknown', at(16, 8), at(16, 12)), ('online', at(16, 12), at(16, 20))]
        result = run(presence)
        self.assertTotals(result, 16 * HOUR, 0, 8 * HOUR, 0)
        self.assertEqual((result['availability'], round(result['coverage'], 3), result['status']), (100.0, 66.667, 'insufficient'))
        self.assertEqual(run(presence, floor=60)['status'], 'ok')

    def test_maintenance_is_excluded_from_both_sides(self):
        # Offline 02:00-04:00 inside a 01:00-05:00 window: 4 h excluded, the other 20 h online.
        presence = [('online', at(16, 0), at(16, 2)), ('offline', at(16, 2), at(16, 4)), ('online', at(16, 4), None)]
        result = run(presence, maintenance=[(at(16, 1), at(16, 5))])
        self.assertTotals(result, 20 * HOUR, 0, 0, 4 * HOUR)
        self.assertEqual((result['availability'], result['coverage'], result['scheduled_s']), (100.0, 100.0, 24 * HOUR))

    def test_dst_changes_make_23_and_25_hour_days(self):
        zone = new_york()
        march = run([('online', dt.datetime(2026, 3, 1, tzinfo=UTC), None)], first=dt.date(2026, 3, 7),
                    last=dt.date(2026, 3, 9), zone=zone)
        self.assertEqual([day['good_s'] for day in march['daily']], [24 * HOUR, 23 * HOUR, 24 * HOUR])
        self.assertEqual(march['good_s'], 71 * HOUR)
        november = run([('online', dt.datetime(2026, 10, 30, tzinfo=UTC), None)], first=dt.date(2026, 10, 31),
                       last=dt.date(2026, 11, 2), zone=zone)
        self.assertEqual([day['good_s'] for day in november['daily']], [24 * HOUR, 25 * HOUR, 24 * HOUR])
        # Business hours stay ten local hours on the change day: 08:00 EDT = 12:00 UTC, 08:00 EST = 13:00 UTC.
        business = dict(ALWAYS, kind='business', start='08:00', end='18:00', weekdays=list(range(7)))
        result = run([('online', dt.datetime(2026, 3, 1, tzinfo=UTC), None)], business, first=dt.date(2026, 3, 7),
                     last=dt.date(2026, 3, 9), zone=zone)
        self.assertEqual([day['good_s'] for day in result['daily']], [10 * HOUR] * 3)
        windows = availability.schedule_windows(business, None, dt.datetime(2026, 3, 8, 5, tzinfo=UTC),
                                                dt.datetime(2026, 3, 9, 4, tzinfo=UTC), zone)
        self.assertEqual(windows, [(dt.datetime(2026, 3, 8, 12, tzinfo=UTC), dt.datetime(2026, 3, 8, 22, tzinfo=UTC))])

    def test_two_time_zones_label_their_own_days_and_add_up_in_seconds(self):
        # The same UTC history: online 01:00-10:00 UTC, then offline. 08:00-17:00 at +07:00 is 01:00-10:00 UTC (all
        # good); at -05:00 it is 13:00-22:00 UTC (all bad). Together 9 h good and 9 h bad: 50 %.
        presence = [('online', at(16, 1), at(16, 10)), ('offline', at(16, 10), None)]
        business = dict(ALWAYS, kind='business', start='08:00', end='17:00')
        east = run(presence, dict(business, timezone='+07:00'), zone=dt.timezone(dt.timedelta(hours=7)))
        west = run(presence, dict(business, timezone='-05:00'), zone=dt.timezone(dt.timedelta(hours=-5)))
        self.assertEqual((east['good_s'], east['bad_s'], west['good_s'], west['bad_s']), (9 * HOUR, 0, 0, 9 * HOUR))
        self.assertEqual((east['daily'][0]['date'], west['daily'][0]['date']), ('2026-11-16', '2026-11-16'))
        fleet = availability.combine([east, west])
        self.assertEqual((fleet['availability'], fleet['coverage'], fleet['status']), (50.0, 100.0, 'ok'))

    def test_future_time_is_not_counted_and_overlapping_rows_are_not_double_counted(self):
        presence = [('online', at(16, 0), None), ('offline', at(16, 6), at(16, 9))]      # overlaps the open row
        result = run(presence, now=at(16, 12))
        self.assertEqual(result['good_s'] + result['bad_s'] + result['unknown_s'], 12 * HOUR)
        self.assertEqual(result['scheduled_s'], 12 * HOUR)


def reference(presence, schedule, laptop, maintenance, first, last, zone, now):
    """The pre-1.3 day-by-day computation with the O(n*m) interval helpers, to check the sweep against."""
    def intersect(a, b):
        return [(max(x, z), min(y, w)) for x, y in a for z, w in b if max(x, z) < min(y, w)]

    def subtract(first_list, second):
        result = []
        for start, end in first_list:
            for cut_start, cut_end in second:
                if cut_end <= start or cut_start >= end:
                    continue
                if cut_start > start:
                    result.append((start, cut_start))
                start = max(start, cut_end)
                if start >= end:
                    break
            if start < end:
                result.append((start, end))
        return result

    def seconds(items):
        return sum((b - a).total_seconds() for a, b in items)
    start, end = availability.local_midnight(first, zone), min(availability.local_midnight(last + dt.timedelta(days=1), zone), now)
    windows = availability.schedule_windows(schedule, laptop, start, end, zone) if start < end else []
    states, cuts = availability.normalize(presence, now), availability._merge(maintenance)
    days, day = [], first
    while day <= last:
        low, high = max(start, availability.local_midnight(day, zone)), min(end, availability.local_midnight(day + dt.timedelta(days=1), zone))
        scheduled = intersect(windows, [(low, high)]) if low < high else []
        counted = subtract(scheduled, cuts)
        good, bad = seconds(intersect(counted, states['online'])), seconds(intersect(counted, states['offline']))
        days.append(dict(date=day.isoformat(), good_s=round(good), bad_s=round(bad),
                         unknown_s=round(max(0.0, seconds(counted) - good - bad)), excluded_s=round(seconds(intersect(scheduled, cuts)))))
        day += dt.timedelta(days=1)
    return days


class SweepTests(unittest.TestCase):
    def test_the_sweep_gives_the_same_days_as_the_old_computation(self):
        import random
        chance = random.Random(13)
        business = dict(ALWAYS, kind='business', start='22:00', end='06:00', weekdays=[0, 2, 3, 6], holidays=['2026-11-19'])
        for case in range(60):
            moment, presence = at(10, 0), []
            while moment < at(28, 0):
                length = dt.timedelta(minutes=chance.randint(1, 3000))
                presence.append((chance.choice(['online', 'offline', 'unknown']), moment,
                                 None if chance.random() < .05 else moment + length + dt.timedelta(minutes=chance.randint(-30, 30))))
                moment += length
            maintenance = [(at(chance.randint(11, 26), chance.randint(0, 23)),) for _ in range(chance.randint(0, 4))]
            maintenance = [(start, start + dt.timedelta(hours=chance.randint(1, 50))) for (start,) in maintenance]
            schedule = chance.choice([ALWAYS, business, dict(business, start='08:00', end='18:00')])
            zone = chance.choice([UTC, new_york(), None])
            first = dt.date(2026, 11, chance.randint(9, 14))
            last = first + dt.timedelta(days=chance.randint(0, 12))
            now = at(chance.randint(15, 29), chance.randint(0, 23))
            expected = reference(presence, schedule, False, maintenance, first, last, zone, now)
            result = availability.device_availability(presence, schedule, False, maintenance, first, last, zone, now)
            import lifetime_uptime
            spans = lifetime_uptime.buckets(first, last, 'week')
            weekly = availability.device_availability(presence, schedule, False, maintenance, first, last, zone, now, spans=spans)
            by_day = {day['date']: day for day in expected}
            with self.subTest(case=case, spans=True):
                self.assertEqual(len(weekly['spans_s']), len(spans))
                for (low, high), values in zip(spans, weekly['spans_s']):
                    days = [by_day[(low + dt.timedelta(days=i)).isoformat()] for i in range((high - low).days + 1)]
                    for key, value in zip(availability.FIELDS, values):
                        self.assertAlmostEqual(value, sum(day[key] for day in days), delta=len(days))
                self.assertEqual(weekly['availability'], result['availability'])
            with self.subTest(case=case):
                for got, want in zip(result['daily'], expected):
                    self.assertEqual(got['date'], want['date'])
                    for key in availability.FIELDS:
                        self.assertAlmostEqual(got[key], want[key], delta=1)

    def test_outages_count_offline_stretches_inside_expected_hours(self):
        business = dict(ALWAYS, kind='business', start='08:00', end='18:00', weekdays=[0, 1, 2, 3, 4], holidays=[])
        presence = [('online', at(16, 0), at(16, 10)), ('offline', at(16, 10), at(16, 11)),      # 1 h in hours
                    ('online', at(16, 11), at(16, 17)), ('offline', at(16, 17), at(17, 9)),       # overnight: 1 h + 1 h
                    ('online', at(17, 9), at(17, 20)), ('offline', at(17, 20), at(18, 7)),        # only outside: none
                    ('unknown', at(18, 7), at(18, 12)), ('online', at(18, 12), None)]             # unknown is never down
        result = run(presence, schedule=business, last=dt.date(2026, 11, 18), now=at(18, 18))
        self.assertEqual((result['outages'], result['longest_outage_s']), (2, 2 * HOUR))
        self.assertEqual(result['bad_s'], 3 * HOUR)
        quiet = run([('online', at(16, 0), None)], now=at(16, 12))
        self.assertEqual((quiet['outages'], quiet['longest_outage_s']), (0, 0))


class MonitorAvailabilityTests(MonitorCase):
    def test_from_recorded_presence_with_schedule_maintenance_incidents_and_a_fixed_as_of(self):
        self.fleet.add()        # a desktop: expected all day
        self.monitor.set_schedule('device', 'always', key=ID, timezone='UTC')
        self.monitor.add_maintenance('device', stamp(T0 + dt.timedelta(hours=3)), stamp(T0 + dt.timedelta(hours=4)), key=ID)
        self.monitor.tick()                                 # 09:00 online
        self.clock.advance(hours=1)
        self.fleet.offline(self.clock.now)                  # 10:00 offline
        self.run_for(60)
        self.fleet.online(self.clock.now)                   # 11:00 online again
        self.monitor.tick()
        self.run_for(120, step=10)                          # until 13:00
        as_of = stamp(self.clock.now)
        result = self.monitor.availability([ID], start='2026-11-16', end='2026-11-16', as_of=as_of, daily=True)
        device = result['devices'][0]
        # 00:00-09:00 before monitoring: unknown 9 h; 09-10 good; 10-11 bad; 11-12 good; 12-13 maintenance.
        self.assertEqual([device[key] for key in availability.FIELDS], [2 * HOUR, HOUR, 9 * HOUR, HOUR])
        self.assertEqual((device['status'], device['timezone'], device['zone_source']), ('insufficient', 'UTC', 'device'))
        self.assertEqual([item['rule'] for item in device['incidents']], ['offline'])
        self.assertEqual(device['daily'][0]['date'], '2026-11-16')
        self.clock.advance(hours=5)
        self.monitor.tick()
        self.assertEqual(self.monitor.availability([ID], start='2026-11-16', end='2026-11-16', as_of=as_of,
                                                   daily=True)['devices'], result['devices'])
        everything = self.monitor.availability([ID])['devices'][0]          # since monitoring began: no unknown prefix
        self.assertEqual((everything['start'], everything['unknown_s']), ('2026-11-16', 0))
        self.assertEqual(self.monitor.set_coverage_floor(10), dict(coverage_floor=10))
        self.assertEqual(self.monitor.availability([ID], start='2026-11-16', end='2026-11-16', as_of=as_of)['devices'][0]['status'], 'ok')
        for arguments in (dict(device_ids=[]), dict(device_ids=[ID], start='2026-11-16'),
                          dict(device_ids=[ID], start='2026-11-17', end='2026-11-16'),
                          dict(device_ids=[ID], start='16/11/2026', end='2026-11-16'), dict(device_ids=[ID], as_of='now')):
            with self.assertRaises(Exception, msg=arguments):
                self.monitor.availability(**arguments)
        with self.assertRaises(Exception):
            self.monitor.set_coverage_floor(0)

    def test_any_period_up_to_ten_years_with_outages(self):
        self.fleet.add()
        self.monitor.set_schedule('device', 'always', key=ID, timezone='UTC')
        self.monitor.tick()
        self.clock.advance(hours=1)
        self.fleet.offline(self.clock.now)
        self.run_for(90, step=10)
        self.fleet.online(self.clock.now)
        self.monitor.tick()
        self.run_for(30, step=10)
        ten = self.monitor.availability([ID], start='2016-11-19', end='2026-11-16')['devices'][0]     # 3,650 days
        self.assertEqual((ten['outages'], ten['longest_outage_s'], ten['bad_s']), (1, 5400, 5400))
        self.assertEqual(ten['start'], '2016-11-19')
        with self.assertRaises(Exception):
            self.monitor.availability([ID], start='2016-01-01', end='2026-11-16')          # over 3,660 days
        # Local times before 1970 cannot be computed on Windows: refused as a bad request, never a server error.
        import lifetime_monitor
        for start, end in (('1969-12-31', '1970-01-05'), ('1970-01-02', '1970-01-02')):
            with self.assertRaises(lifetime_monitor.MonitorError):
                self.monitor.availability([ID], start=start, end=end)
            with self.assertRaises(lifetime_monitor.MonitorError):
                self.monitor.uptime_bars(start, end)
        self.assertEqual(self.monitor.availability([ID], start='1971-01-01', end='1971-01-02')['devices'][0]['status'],
                         'insufficient')

    def test_fleet_uptime_bars_are_worst_first_bucketed_and_small(self):
        import uuid
        from lifetime_protocol import MAX_BYTES, canonical
        laptop, flaky, steady = (str(uuid.UUID(int=n)) for n in (7, 8, 9))
        self.fleet.add(laptop, name='Laptop', laptop=True)       # no schedule: never expected online
        self.fleet.add(flaky, name='Flaky')
        self.fleet.add(steady, name='Steady')
        for device_id in (flaky, steady):
            self.monitor.set_schedule('device', 'always', key=device_id, timezone='UTC')
        self.monitor.tick()                                       # 09:00 Monday 16 November, all online
        self.clock.advance(hours=1)
        self.fleet.offline(self.clock.now, flaky)
        self.monitor.tick()
        self.clock.advance(hours=2)
        self.fleet.online(self.clock.now, flaky)
        self.monitor.tick()
        self.clock.advance(hours=1)
        self.monitor.tick()                                       # 13:00
        result = self.monitor.uptime_bars('2026-11-15', '2026-11-16', kind='day')
        self.assertEqual([row['name'] for row in result['rows']], ['Flaky', 'Steady', 'Laptop'])
        self.assertEqual(result['buckets'], [['2026-11-15', '2026-11-15'], ['2026-11-16', '2026-11-16']])
        flaky_row = result['rows'][0]
        self.assertEqual(flaky_row['bars'], [[0, 0, 1440], [120, 120, 540]])     # minutes: good, bad, unknown
        self.assertEqual((flaky_row['outages'], flaky_row['longest_outage_s']), (1, 7200))
        self.assertEqual(result['rows'][2]['bars'], [[0, 0, 0], [0, 0, 0]])     # not expected: no bars to colour
        self.assertEqual(self.monitor.uptime_bars('2025-11-17', '2026-11-16')['buckets'][0], ['2025-11-17', '2025-11-23'])
        page = self.monitor.uptime_bars('2026-11-15', '2026-11-16', kind='day', offset=1, limit=1)
        self.assertEqual(([row['name'] for row in page['rows']], page['total']), (['Steady'], 3))
        for arguments in (dict(start='2026-11-16', end='2026-11-15'), dict(start=None, end=None),
                          dict(start='2026-11-15', end='2026-11-16', kind='hour'),
                          dict(start='2026-11-15', end='2026-11-16', limit=101)):
            with self.subTest(arguments=arguments), self.assertRaises(Exception):
                self.monitor.uptime_bars(**arguments)
        # The largest page the dashboard can ask for still fits the 1 MiB admin reply.
        row = dict(flaky_row, name='x' * 64, bars=[[1440, 1440, 1440]] * 366)
        self.assertLess(len(canonical(dict(result, rows=[row] * 100, buckets=[['2026-01-01', '2026-01-01']] * 366))),
                        MAX_BYTES * 0.8)


if __name__ == '__main__':
    unittest.main()
