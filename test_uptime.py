"""Date ranges, buckets and the uptime/downtime day breakdown (lifetime_uptime): pure functions, no I/O."""
import datetime as dt
import unittest

import lifetime_uptime as uptime

D = dt.date


class RangeTests(unittest.TestCase):
    def test_quick_choices(self):
        today = D(2026, 11, 23)
        self.assertEqual(uptime.preset_range('7d', today), (D(2026, 11, 17), today))
        self.assertEqual(uptime.preset_range('30d', today), (D(2026, 10, 25), today))
        self.assertEqual(uptime.preset_range('this-month', today), (D(2026, 11, 1), today))
        self.assertEqual(uptime.preset_range('last-month', today), (D(2026, 10, 1), D(2026, 10, 31)))
        self.assertEqual(uptime.preset_range('last-month', D(2026, 1, 5)), (D(2025, 12, 1), D(2025, 12, 31)))
        self.assertEqual(uptime.preset_range('this-year', today), (D(2026, 1, 1), today))
        self.assertEqual(uptime.preset_range('12m', today), (D(2025, 11, 24), today))
        self.assertEqual(uptime.preset_range('5y', today), (D(2021, 11, 24), today))
        self.assertIsNone(uptime.preset_range('all', today))
        self.assertIsNone(uptime.preset_range('custom', today))

    def test_leap_day_never_breaks_a_year_back(self):
        self.assertEqual(uptime.preset_range('12m', D(2028, 2, 29)), (D(2027, 3, 1), D(2028, 2, 29)))
        self.assertEqual(uptime.preset_range('5y', D(2028, 2, 29)), (D(2023, 3, 1), D(2028, 2, 29)))
        self.assertEqual(uptime.years_back(D(2028, 2, 29), 4), D(2024, 2, 29))

    def test_previous_period_has_the_same_length(self):
        self.assertEqual(uptime.previous_period(D(2026, 11, 1), D(2026, 11, 30)), (D(2026, 10, 2), D(2026, 10, 31)))
        self.assertEqual(uptime.previous_period(D(2026, 11, 23), D(2026, 11, 23)), (D(2026, 11, 22), D(2026, 11, 22)))

    def test_buckets_days_weeks_months(self):
        self.assertEqual(uptime.bucket_kind(D(2026, 1, 1), D(2026, 4, 2)), 'day')         # 92 days
        self.assertEqual(uptime.bucket_kind(D(2026, 1, 1), D(2026, 4, 3)), 'week')
        self.assertEqual(uptime.bucket_kind(D(2021, 11, 24), D(2026, 11, 23)), 'month')
        weeks = uptime.buckets(D(2026, 11, 4), D(2026, 11, 23), 'week')           # a Wednesday to a Monday
        self.assertEqual(weeks, [(D(2026, 11, 4), D(2026, 11, 8)), (D(2026, 11, 9), D(2026, 11, 15)),
                                 (D(2026, 11, 16), D(2026, 11, 22)), (D(2026, 11, 23), D(2026, 11, 23))])
        months = uptime.buckets(D(2024, 1, 31), D(2024, 3, 2), 'month')
        self.assertEqual(months, [(D(2024, 1, 31), D(2024, 1, 31)), (D(2024, 2, 1), D(2024, 2, 29)), (D(2024, 3, 1), D(2024, 3, 2))])
        five = uptime.buckets(D(2021, 11, 24), D(2026, 11, 23))
        self.assertEqual(len(five), 61)
        self.assertEqual(sum((end - start).days + 1 for start, end in five), (D(2026, 11, 23) - D(2021, 11, 24)).days + 1)

    def test_missing_days_are_unknown_never_zero(self):
        daily = {'2026-11-02': 3600, '2026-11-04': 7200, '2026-11-20': 0}
        rows = uptime.daily_buckets(daily, D(2026, 11, 2), D(2026, 11, 22), 'week')
        self.assertEqual([(row['seconds'], row['known'], row['days']) for row in rows],
                         [(10800, 2, 7), (None, 0, 7), (0, 1, 7)])


def off(**values):
    return dict(dict(asleep_s=0, off_s=0, unknown_s=0, restarts=0, shutdowns=0, unexpected_stops=0), **values)


class BreakdownTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 23, 6, 0, 0)

    def test_computer_with_downtime_old_computer_and_unknown_days(self):
        history = dict(daily={'2026-11-20': 36000, '2026-11-21': 7200},
                       downtime_daily={'2026-11-20': off(asleep_s=28800, off_s=18000, restarts=1, unexpected_stops=1)})
        days = uptime.day_breakdown(history, D(2026, 11, 20), D(2026, 11, 24), self.NOW)
        self.assertEqual([(d['awake_s'], d['asleep_s'], d['off_s'], d['unknown_s']) for d in days], [
            (36000, 28800, 18000, 3600),        # described: the hour it did not account for is unknown
            (7200, None, None, 79200),          # awake only: the rest of the day is unknown
            (None, None, None, 86400),          # nothing reported: the whole day is unknown, never zero
            (None, None, None, 21600),          # today: only the six hours so far
            (None, None, None, 0)])             # tomorrow has no length yet
        total = uptime.total(days)
        self.assertEqual((total['awake_s'], total['asleep_s'], total['off_s'], total['restarts'], total['unexpected_stops'],
                          total['reported'], total['days']), (43200, 28800, 18000, 1, 1, 1, 5))
        self.assertEqual(total['awake_share'], 43.5)      # 36000 / (36000 + 28800 + 18000)
        self.assertEqual(total['awake_per_stop'], 43200)
        old = uptime.total(uptime.day_breakdown(dict(daily={'2026-11-20': 3600}), D(2026, 11, 20), D(2026, 11, 20), self.NOW))
        self.assertEqual((old['asleep_s'], old['off_s'], old['restarts'], old['awake_share']), (None, None, None, None))
        self.assertEqual(old['unknown_s'], 82800)

    def test_a_daylight_saving_day_is_23_or_25_hours_long(self):
        from test_availability import new_york
        zone = new_york()
        history = dict(daily={'2026-03-08': 3600, '2026-11-01': 3600})
        now = dt.datetime(2026, 11, 23, 9, 30, tzinfo=dt.timezone.utc)        # 04:30 in New York
        days = uptime.day_breakdown(history, D(2026, 3, 7), D(2026, 3, 8), now, zone)
        self.assertEqual([day['unknown_s'] for day in days], [86400, 82800 - 3600])
        days = uptime.day_breakdown(history, D(2026, 11, 1), D(2026, 11, 1), now, zone)
        self.assertEqual(days[0]['unknown_s'], 90000 - 3600)
        today, = uptime.day_breakdown({}, D(2026, 11, 23), D(2026, 11, 23), now, zone)
        self.assertEqual(today['unknown_s'], 4.5 * 3600)                        # only the time so far

    def test_buckets_of_the_breakdown(self):
        history = dict(daily={'2026-11-16': 3600}, downtime_daily={'2026-11-17': off(asleep_s=86400)})
        days = uptime.day_breakdown(history, D(2026, 11, 16), D(2026, 11, 22), self.NOW)
        week, = uptime.breakdown_buckets(days, D(2026, 11, 16), D(2026, 11, 22), 'week')
        self.assertEqual((week['awake_s'], week['asleep_s'], week['unknown_s'], week['start']),
                         (3600, 86400, 82800 + 5 * 86400, D(2026, 11, 16)))


class BarTests(unittest.TestCase):
    def test_bar_status(self):
        self.assertEqual(uptime.bar_status(0, 0, 0), 'none')
        self.assertEqual(uptime.bar_status(100, 0, 900), 'unknown')
        self.assertEqual(uptime.bar_status(0, 0, 60), 'unknown')
        self.assertEqual(uptime.bar_status(990, 10, 0), 'good')
        self.assertEqual(uptime.bar_status(980, 20, 0), 'partial')
        self.assertEqual(uptime.bar_status(740, 260, 0), 'down')
        self.assertEqual(uptime.bar_status(100, 0, 20, floor=90), 'unknown')


if __name__ == '__main__':
    unittest.main()
