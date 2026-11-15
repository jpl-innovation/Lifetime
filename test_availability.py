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


if __name__ == '__main__':
    unittest.main()
