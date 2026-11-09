"""Read-only chart aggregation, local civil dates, and bounded telemetry."""
import datetime as dt
import io
import struct
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch

import lifetime_charts as c


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.today = dt.date(2026, 11, 6)
        self.daily = {self.today-dt.timedelta(days=i): (i+1)*3600 for i in range(10)}

    def test_range_inclusive_and_future_excluded(self):
        data = {dt.date(2026, 10, 7): 1, dt.date(2026, 10, 8): 2,
                self.today: 3, self.today+dt.timedelta(days=1): 4}
        start, end = c.range_bounds(data, '30 days', self.today)
        self.assertEqual(c.filter_range(data, start, end), {dt.date(2026, 10, 8): 2, self.today: 3})
        self.assertEqual(c.range_bounds(data, '90 days', self.today)[0], dt.date(2026, 8, 9))
        self.assertEqual(c.range_bounds(data, 'All', self.today), (dt.date(2026, 10, 7), self.today))
        self.assertEqual(c.range_bounds({}, 'All', self.today), (self.today, self.today))

    def test_calendar_year_includes_leap_day(self):
        self.assertEqual(c.range_bounds({}, '1 year', dt.date(2024, 2, 29))[0], dt.date(2023, 3, 1))
        self.assertEqual(c.range_bounds({}, '1 year', dt.date(2025, 2, 28))[0], dt.date(2024, 2, 29))

    def test_weekly_monday_and_year_boundary(self):
        data = {dt.date(2025, 12, 28): 1, dt.date(2025, 12, 29): 2,
                dt.date(2026, 1, 4): 3, dt.date(2026, 1, 5): 0}
        self.assertEqual(c.weekly(data), {dt.date(2025, 12, 22): 1, dt.date(2025, 12, 29): 5, dt.date(2026, 1, 5): 0})

    def test_moving_average_requires_complete_trailing_calendar_window(self):
        points = c.date_series(self.daily, self.today-dt.timedelta(days=9), self.today)
        avg = dict(c.moving_average(points))
        self.assertIsNone(avg[self.today-dt.timedelta(days=4)])
        self.assertEqual(avg[self.today], 4*3600)
        self.assertEqual(avg[self.today-dt.timedelta(days=3)], 7*3600)
        self.daily.pop(self.today-dt.timedelta(days=1))
        self.assertIsNone(dict(c.moving_average(c.date_series(self.daily, points[0][0], self.today)))[self.today])
        with self.assertRaises(ValueError):
            c.moving_average(points, 0)

    def test_weekday_averages_recorded_zero_and_missing(self):
        data = {dt.date(2026, 11, 2): 0, dt.date(2026, 11, 9): 7200,
                dt.date(2026, 11, 3): 3600}
        self.assertEqual(c.weekday_averages(data), [3600, 3600, None, None, None, None, None])

    def test_histogram_exact_boundaries_and_invalid(self):
        seconds = [0, 899, 900, 3599, 3600, 10799, 10800, 21599, 21600, 43199, 43200, 90000, -1, float('nan')]
        self.assertEqual(c.histogram([{'seconds': v} for v in seconds]), [2]*6)

    def test_session_filter_iso_start_in_local_timezone(self):
        tz = dt.timezone(dt.timedelta(hours=7))
        sessions = [dict(start='2026-11-05T18:00:00+00:00', seconds=3600),
                    dict(start='2026-11-05T16:00:00+00:00', seconds=7200),
                    dict(start='2026-11-06T09:00:00', seconds=900), dict(start='bad', seconds=1)]
        self.assertEqual(c.filter_sessions(sessions, self.today, self.today, tz), [sessions[0], sessions[2]])

    def test_dst_dates_and_repeated_hour(self):
        # Tiny real TZif fixture for the 2026 US transitions. Windows Python
        # ships no IANA database; from_file tests real ZoneInfo without deps.
        transitions = [int(dt.datetime(2026, 3, 8, 7, tzinfo=dt.timezone.utc).timestamp()),
                       int(dt.datetime(2026, 11, 1, 6, tzinfo=dt.timezone.utc).timestamp())]
        tzif = (b'TZif\0'+bytes(15)+struct.pack('>6I', 0, 0, 0, 2, 2, 8)
                +struct.pack('>2i', *transitions)+bytes([1, 0])
                +struct.pack('>iBBiBB', -18000, 0, 0, -14400, 1, 4)+b'EST\0EDT\0')
        tz = ZoneInfo.from_file(io.BytesIO(tzif), key='Test/New_York_2026')
        self.assertEqual(c.local_date('2026-03-08T04:30:00+00:00', tz), dt.date(2026, 3, 7))
        self.assertEqual(c.local_date('2026-03-08T07:30:00+00:00', tz), dt.date(2026, 3, 8))
        self.assertEqual(c.local_date('2026-11-01T05:30:00+00:00', tz), c.local_date('2026-11-01T06:30:00+00:00', tz))
        self.assertEqual(c.local_date('2026-11-01'), dt.date(2026, 11, 1))
        ledger = dict(sessions=[dict(start='2026-11-01T01:00:00-04:00', seconds=7200, imported=True)])
        day = dt.date(2026, 11, 1)
        values, coverage = c.hour_profile(ledger, day, day, {day: 7200}, tz)
        self.assertEqual(values[1], 7200)  # repeated 01:00, two real hours
        self.assertEqual(sum(values), 7200)
        day = dt.date(2026, 3, 8)
        ledger['sessions'][0].update(start='2026-03-08T01:00:00-05:00')
        values, _ = c.hour_profile(ledger, day, day, {day: 7200}, tz)
        self.assertEqual(values[2], 0)  # nonexistent local hour
        self.assertEqual(values[1], values[3])

    def test_cumulative_anchors_retained_history_and_baseline_when_zoomed(self):
        start = self.today-dt.timedelta(days=1)
        result = c.cumulative(self.daily, start, self.today, 100*3600, 5*3600)
        self.assertEqual(result, [(start, 104*3600), (self.today, 105*3600)])
        empty = c.cumulative({}, self.today, self.today, 7200, 3600)
        self.assertEqual(empty, [(self.today, 10800)])

    def test_cumulative_missing_date_is_no_new_credit(self):
        first = self.today-dt.timedelta(days=2)
        data = {first: 3600, self.today: 7200}
        self.assertEqual([v for _, v in c.cumulative(data, first, self.today)], [3600, 3600, 10800])

    def test_actual_live_boundary_uses_import_original(self):
        ledger = dict(tracking_started='2026-06-27', imported=dict(
            until='2026-11-06T01:00:00+07:00', original=dict(tracking_started='2026-11-05')))
        self.assertEqual(c.live_started(ledger), dt.date(2026, 11, 5))

    def test_hour_profile_all_time_or_clipped_import_intervals(self):
        day = self.today
        tz = dt.timezone(dt.timedelta(hours=7))
        data = {day-dt.timedelta(days=1): 3600, day: 3600}
        ledger = dict(hourly=[7200]*24, sessions=[
            dict(start='2026-11-05T23:30:00+07:00', seconds=3600, imported=True),
            dict(start='2026-11-06T01:00:00+07:00', seconds=86400)])
        profile, text = c.hour_profile(ledger, day, day, data, tz)
        self.assertEqual(profile[0], 1800)
        self.assertEqual(sum(profile), 1800)  # live session isn't a continuous span
        self.assertIn('partial', text)
        profile, text = c.hour_profile(ledger, day-dt.timedelta(days=1), day, data, tz)
        self.assertEqual(profile, [3600]*24)
        self.assertIn('boot credit excluded', text)

    def test_missing_hour_data_is_unavailable_not_faked(self):
        profile, _ = c.hour_profile(dict(hourly=[1]*24), self.today, self.today,
                                    {self.today: 10, self.today-dt.timedelta(days=1): 10})
        self.assertEqual(profile, [None]*24)

    def test_hourly_totals_older_than_daily_retention_are_not_averaged(self):
        ledger = dict(history_started='2020-01-01', hourly=[1000000]*24)
        profile, _ = c.hour_profile(ledger, self.today, self.today, {self.today: 3600})
        self.assertEqual(profile, [None]*24)

    def test_clean_daily_preserves_unknown_and_rejects_invalid(self):
        self.assertEqual(c.clean_daily({'2026-11-06': 0, 'bad': 5, '2026-11-05': -1,
                                       '2026-11-04': float('inf')}), {self.today: 0})

    def test_ring_buffer_five_minutes_zero_missing_and_capacity(self):
        b = c.SampleBuffer()
        for i in range(700):
            b.append(0 if i%2 else None, now=i)
        self.assertEqual(len(b.samples), 301)
        self.assertEqual(b.samples[0][0], 399)
        self.assertEqual(b.samples[-1][2], 0)
        b.prune(1001)
        self.assertFalse(b.samples)

    def test_three_year_daily_models_keep_every_date(self):
        start = self.today-dt.timedelta(days=1095)
        data = {d: 3600 for d, _ in c.date_series({}, start, self.today)}
        self.assertEqual(len(c.cumulative(data, start, self.today)), 1096)
        self.assertEqual(sum(c.weekly(data).values()), 1096*3600)
        self.assertEqual(dict(c.moving_average(list(sorted(data.items()))))[self.today], 3600)

    def test_heat_ramp_monotonic_luminance(self):
        def luminance(color):
            channels = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
            return sum(a*b for a, b in zip(linear, (.2126, .7152, .0722)))
        import lifetime_theme as theme
        # Busier days read as "more": brighter on the dark theme, darker ink on the light theme.
        dark = [luminance(color) for color in theme.THEMES['dark']['heat']]
        self.assertTrue(all(a < b for a, b in zip(dark, dark[1:])))
        light = [luminance(color) for color in theme.THEMES['light']['heat']]
        self.assertTrue(all(a > b for a, b in zip(light, light[1:])))
        self.assertEqual(len(dark), len(light))


class LiveChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_test_support import qt_app
        cls.app = qt_app()

    def setUp(self):
        self.panel = c.LiveSparklines()
        self.panel.timer.stop()
        self.panel.set_settings(dict(resources=True, battery=True, graphics=True, network=True))
        self.addCleanup(self.panel.deleteLater)

    def test_live_samples_use_real_units_keep_zero_and_mark_missing(self):
        self.panel.update_values(dict(cpu=0, ram=dict(percent=25), battery=dict(watts=0),
                                      gpu=[dict(watts=12)], counters=dict(down=1024, up=0)))
        for now in (100, 102):
            with patch.object(c.time, 'monotonic', return_value=now):
                self.panel.sample()
        self.assertEqual(self.panel.charts['cpu'].values, [0, 0])
        self.assertEqual(self.panel.charts['down'].values, [1, 1])
        self.assertEqual(self.panel.charts['up'].values, [0, 0])
        self.assertEqual(self.panel.charts['watts'].values, [0, 0])
        self.panel.update_values({})
        with patch.object(c.time, 'monotonic', return_value=104):
            self.panel.sample()
        self.assertEqual(self.panel.charts['cpu'].values, [0, 0, None])
        self.assertIn('Not available', self.panel.cards['cpu'].subtitle.text())

    def test_revoking_category_clears_ring_chart_and_table(self):
        self.panel.update_values(dict(cpu=77))
        with patch.object(c.time, 'monotonic', return_value=100):
            self.panel.sample()
        card = self.panel.cards['cpu']
        card.show_table(True)
        self.assertEqual(card.table.rowCount(), 1)
        self.panel.set_settings(dict(resources=False))
        self.assertEqual(card.table.rowCount(), 0)
        self.assertFalse(card.rows)
        self.assertFalse(self.panel.charts['cpu'].values)
        self.assertFalse(self.panel.charts['cpu'].buffer.samples)
        self.assertNotIn('cpu', self.panel.pending)


class ActivityLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_test_support import qt_app
        cls.app = qt_app()

    def test_one_toggle_six_tables_and_range_buttons_update_every_chart(self):
        from PyQt6.QtWidgets import QPushButton
        panel = c.Insights()
        self.addCleanup(panel.deleteLater)
        today = dt.date.today()
        panel.set_ledger(dict(daily={(today-dt.timedelta(days=i)).isoformat(): 3600 for i in range(400)}))
        self.assertEqual(len(panel.findChildren(QPushButton)), 6)  # Four ranges, disclosure, data.
        panel.counting_toggle.click()
        self.assertTrue(panel.counting_toggle.isChecked())
        self.assertFalse(panel.source_panel.isHidden())
        panel.data_toggle.click()
        self.assertTrue(all(not card.table.isHidden() for card in panel.cards))
        panel.ranges.buttons[1].click()
        self.assertEqual(panel.selection, '90 days')
        self.assertEqual(len(panel.daily.values), 90)
        self.assertEqual(len(panel.running.values), 90)
        self.assertEqual(panel.cards[0].table.rowCount(), 90)
        self.assertEqual(panel.heatmap.start, panel.daily.dates[0])
        panel.data_toggle.click()
        self.assertTrue(all(card.table.isHidden() for card in panel.cards))

    def test_range_indicator_interrupts_and_reduced_motion_is_instant(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import QRectF
        ranges = c.RangeSegments()
        self.addCleanup(ranges.deleteLater)
        ranges.show()
        self.app.processEvents()
        with patch.object(c, 'motion_reduced', return_value=False):
            ranges.setCurrentIndex(1)
            QTest.qWait(50)
            current = QRectF(ranges.indicator)
            ranges.setCurrentIndex(3)
            self.assertEqual(ranges.animation.startValue(), current)
            QTest.qWait(280)
            self.assertEqual(ranges.indicator, QRectF(ranges.buttons[3].geometry()))
        with patch.object(c, 'motion_reduced', return_value=True):
            ranges.setCurrentIndex(0)
            self.assertEqual(ranges.indicator, QRectF(ranges.buttons[0].geometry()))
            self.assertEqual(ranges.animation.state(), ranges.animation.State.Stopped)


if __name__ == '__main__':
    unittest.main()
