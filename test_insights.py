"""Golden cases for lifetime_insights, every expected number worked out by hand in the comments."""
import datetime as dt
import unittest

import test_compat  # noqa: F401
import lifetime_insights as insights

TODAY = dt.date(2026, 11, 16)


def day(offset):
    return (TODAY - dt.timedelta(days=offset)).isoformat()


class EnergyTests(unittest.TestCase):
    def test_kwh_is_hours_times_watts_over_a_thousand(self):
        self.assertEqual(insights.kwh(36000, 20), 0.2)      # 10 h x 20 W = 200 Wh
        self.assertEqual(insights.kwh(5400, 80), 0.12)      # 1.5 h x 80 W = 120 Wh

    def test_measured_needs_three_discharge_readings_and_wins_over_settings(self):
        config = insights.settings(dict(group_watts={'Lab': 120}, device_watts={'pc-1': 45}))
        power = insights.power('laptop', [12, 15, 18, None, -3, float('nan')], 'pc-1', 'lab', config)
        self.assertEqual(power, dict(watts=15.0, method='measured', source='battery', readings=3))   # (12+15+18)/3
        two = insights.power('laptop', [12, 15], 'pc-1', 'lab', config)
        self.assertEqual((two['watts'], two['method'], two['source']), (45.0, 'estimated', 'computer'))
        self.assertEqual(insights.power('desktop', [], 'pc-2', 'LAB', config)['source'], 'group')     # case-insensitive
        self.assertEqual(insights.power('laptop', [], 'pc-2', 'Office', config)['watts'], 20.0)
        unknown = insights.power(None, [], 'pc-2', '', config)
        self.assertEqual((unknown['watts'], unknown['source']), (80.0, 'typical-unknown'))     # the higher figure, said so

    def test_today_week_month_and_cost(self):
        # 8 h every day for 30 days, plus a day 40 days ago that is outside every window.
        daily = {day(i): 8 * 3600 for i in range(30)}
        daily[day(40)] = 24 * 3600
        result = insights.energy(daily, 25, TODAY, 2500)
        self.assertEqual(result['today'], dict(kwh=0.2, cost=500.0, awake_hours=8.0))        # 8 h x 25 W = 0.2 kWh
        self.assertEqual(result['week']['kwh'], 1.4)                                          # 7 x 0.2
        self.assertEqual(result['month'], dict(kwh=6.0, cost=15000.0, awake_hours=240.0))    # 30 x 0.2 x 2500 VND
        self.assertEqual(len(result['daily']), 30)
        self.assertEqual(result['days_known'], 30)

    def test_missing_days_are_counted_as_known_days_only(self):
        result = insights.energy({day(0): 3600}, 80, TODAY, 3000)
        self.assertEqual(result['days_known'], 1)
        self.assertEqual(result['month']['kwh'], 0.08)
        self.assertEqual(result['daily'][day(5)], 0.0)

    def test_fleet_sums_groups_and_leaves_unshared_history_out(self):
        items = [dict(device_id='a', name='A', group='Lab', kind='laptop', discharge=[10, 10, 10],
                      history=dict(daily={day(0): 36000})),                         # 10 h x 10 W = 0.1 kWh
                 dict(device_id='b', name='B', group='Lab', kind='desktop', discharge=[],
                      history=dict(daily={day(0): 36000})),                         # 10 h x 80 W = 0.8 kWh
                 dict(device_id='c', name='C', group='Office', kind='desktop', history=None)]
        result = insights.fleet(items, insights.settings(dict(price=2000)), TODAY)
        self.assertEqual(result['fleet']['today'], dict(kwh=0.9, cost=1800.0))
        self.assertEqual((result['fleet']['known'], result['fleet']['unknown'], result['fleet']['measured']), (2, 1, 1))
        self.assertEqual(result['groups']['Office']['today']['kwh'], 0)
        self.assertEqual(result['top'], ['b', 'a'])
        self.assertIsNone(result['computers'][2]['energy'])

    def test_settings_are_validated(self):
        for bad in (dict(price=-1), dict(currency='dong'), dict(laptop_watts=0), dict(desktop_watts=5000),
                    dict(group_watts={'x': 'many'}), dict(price=float('inf'))):
            with self.assertRaises(ValueError, msg=bad):
                insights.settings(bad)
        self.assertEqual(insights.settings(dict(currency='USD', unknown=1))['currency'], 'USD')

    def test_money(self):
        self.assertEqual(insights.money(1234567.4, 'VND'), '1,234,567 VND')
        self.assertEqual(insights.money(12.5, 'USD'), '12.50 USD')


def hours(*pairs):
    """24 hourly buckets with the given (hour, seconds)."""
    buckets = [0.0] * 24
    for hour, seconds in pairs:
        buckets[hour] = float(seconds)
    return buckets


class AfterHoursTests(unittest.TestCase):
    OFFICE = insights.office_hours(dict(start='08:00', end='18:00', weekdays=[0, 1, 2, 3, 4]))

    def test_a_night_across_midnight_is_split_between_its_two_dates(self):
        # Monday 16 Nov 2026: at work 09-17 (inside), then on from 22:00 to 02:00 Tuesday.
        dated = {'2026-11-16': hours(*[(h, 3600) for h in range(9, 17)], (22, 3600), (23, 3600)),
                 '2026-11-17': hours((0, 3600), (1, 3600))}
        self.assertEqual(insights.after_hours(dated, self.OFFICE), {'2026-11-16': 7200.0, '2026-11-17': 7200.0})

    def test_daylight_saving_days_follow_the_wall_clock(self):
        every_day = insights.office_hours(dict(start='08:00', end='18:00', weekdays=list(range(7))))
        # Clocks go back on Sunday 1 Nov 2026 (US): 01:00-02:00 happens twice, so that bucket holds 7200 s, all of it
        # after hours; 09:00 is inside. Clocks go forward on 8 Mar 2026: there is no 02:00 bucket at all.
        dated = {'2026-11-01': hours((1, 7200), (9, 3600)), '2026-03-08': hours((1, 3600), (3, 3600), (8, 1800))}
        self.assertEqual(insights.after_hours(dated, every_day), {'2026-11-01': 7200.0, '2026-03-08': 7200.0})

    def test_hours_cut_by_the_opening_and_closing_time_are_shared_by_minutes(self):
        office = insights.office_hours(dict(start='08:30', end='17:15', weekdays=[0, 1, 2, 3, 4]))
        dated = {'2026-11-16': hours((8, 3600), (12, 3600), (17, 3600))}
        self.assertEqual(insights.after_hours(dated, office), {'2026-11-16': 1800.0 + 2700.0})   # 30 + 45 minutes

    def test_hours_over_midnight_weekends_and_holidays(self):
        night = insights.office_hours(dict(start='22:00', end='06:00', weekdays=[0, 1, 2, 3, 4], holidays=['2026-11-18']))
        dated = {'2026-11-16': hours((5, 3600), (22, 3600)),        # Mon 05:00 belongs to Sunday night: closed
                 '2026-11-17': hours((5, 3600), (6, 3600)),         # Tue 05:00 is Monday's shift; 06:00 is after
                 '2026-11-18': hours((23, 3600)),                    # a holiday night: after hours
                 '2026-11-21': hours((12, 3600))}                    # Saturday
        self.assertEqual(insights.after_hours(dated, night),
                         {'2026-11-16': 3600.0, '2026-11-17': 3600.0, '2026-11-18': 3600.0, '2026-11-21': 3600.0})
        self.assertTrue(insights.open_at(night, dt.datetime(2026, 11, 17, 5, 59)))
        self.assertFalse(insights.open_at(night, dt.datetime(2026, 11, 17, 6, 0)))
        self.assertFalse(insights.open_at(night, dt.datetime(2026, 11, 19, 3, 0)))   # the holiday's shift never began

    def test_week_energy_cost_trend_and_schedule_source(self):
        # One night of 4 h after hours this week (Mon 16 - Sun 22 Nov) and 2 h the week before, 50 W, 2000 VND.
        history = dict(daily={}, dated_hourly={'2026-11-16': hours((20, 3600), (21, 3600), (22, 3600), (23, 3600)),
                                               '2026-11-12': hours((19, 3600), (20, 3600))})
        config = insights.settings(dict(price=2000))
        result = insights.after_week(history, None, 50, config, dt.date(2026, 11, 18))
        self.assertEqual(result['source'], 'office-hours')
        self.assertEqual(result['week'], dict(hours=6.0, kwh=0.3, cost=600.0))   # last 7 days 12-18 Nov: 6 h x 50 W
        self.assertEqual(result['weeks'][-2:], [('2026-11-09', 2.0), ('2026-11-16', 4.0)])
        schedule = dict(kind='business', start='07:00', end='22:00', weekdays=[0, 1, 2, 3, 4], holidays=[], timezone='local')
        own = insights.after_week(history, schedule, 50, config, dt.date(2026, 11, 18))
        self.assertEqual((own['source'], own['week']['hours']), ('schedule', 2.0))   # only Mon 22-24 is after 22:00
        self.assertIsNone(insights.after_week(dict(daily={}), None, 50, config, TODAY))

    def test_group_saving_counts_only_computers_whose_owner_allows_remote_power(self):
        night = {day(1): hours((20, 3600), (21, 3600))}      # 2 h after hours, within the last 7 days
        items = [dict(device_id=name, name=name, group='Lab', kind='desktop', control=control,
                      history=dict(daily={}, dated_hourly=night)) for name, control in (('a', True), ('b', False))]
        result = insights.fleet(items, insights.settings(dict(price=3000)), TODAY)
        after = result['groups']['Lab']['after']
        self.assertEqual((after['hours'], after['kwh'], after['allowed']), (4.0, 0.32, 1))     # 2 x 2 h x 80 W
        self.assertEqual(after['saving_month'], dict(kwh=0.69, cost=2057.14))       # 0.16 x 30 / 7 = 0.6857 kWh
        self.assertEqual(result['after_top'], ['a', 'b'])

    def test_office_hours_are_validated(self):
        for bad in (dict(start='8:00'), dict(start='09:00', end='09:00'), dict(weekdays=[7]), dict(weekdays=[1, 1]),
                    dict(holidays=['2026-13-01'])):
            with self.assertRaises(ValueError, msg=bad):
                insights.settings(dict(hours=bad))


class ForecastTests(unittest.TestCase):
    START = dt.date(2026, 1, 1)

    def points(self, *pairs):
        return [((self.START + dt.timedelta(days=d)).isoformat(), v) for d, v in pairs]

    def test_a_straight_decline_gives_the_day_it_crosses(self):
        # 100, 97, 94, 91 % every 30 days: -0.1 % a day from 100, so 80 % on day 200 (20 July 2026).
        result = insights.forecast(self.points((0, 100), (30, 97), (60, 94), (90, 91)), 80, self.START + dt.timedelta(days=90))
        self.assertEqual((result['status'], result['date'], result['per_year']), ('expected', '2026-07-20', -36.5))
        self.assertEqual((result['points'], result['first'], result['last'], result['span_days']), (4, '2026-01-01', '2026-04-01', 90))

    def test_too_little_data_never_gives_a_date(self):
        three = insights.forecast(self.points((0, 100), (60, 90), (90, 85)), 80, self.START)
        short = insights.forecast(self.points((0, 100), (5, 95), (10, 90), (20, 85)), 80, self.START)   # 20 days
        for result in (three, short, insights.forecast([], 80, self.START)):
            self.assertEqual((result['status'], result['date']), ('not-enough-data', None))
        self.assertEqual((three['points'], three['span_days']), (3, 90))
        self.assertEqual((short['points'], short['span_days']), (4, 20))

    def test_reached_flat_and_far_away(self):
        today = self.START + dt.timedelta(days=90)
        self.assertEqual(insights.forecast(self.points((0, 85), (30, 82), (60, 80), (90, 79)), 80, today)['status'], 'reached')
        self.assertEqual(insights.forecast(self.points((0, 90), (30, 91), (60, 90), (90, 91)), 80, today)['status'], 'not-declining')
        # -0.001 % a day from 95 % reaches 80 % in 15,000 days: more than ten years away.
        slow = insights.forecast(self.points((0, 95), (30, 94.97), (60, 94.94), (90, 94.91)), 80, today)
        self.assertEqual((slow['status'], slow['date']), ('beyond', None))

    def test_battery_history_disk_trend_health_warning_and_plan(self):
        today = dt.date(2026, 11, 16)
        history = [dict(date=(today - dt.timedelta(days=30 * i)).isoformat(), full=50 - (3 - i) * 1.5) for i in range(4)]
        # full 45.5, 47, 48.5, 50 Wh of a 50 Wh design: 91 % now, falling 3 % a month: 80 % in 110 days (6 Mar 2027).
        item = dict(device_id='a', name='A', group='Lab', kind='laptop',
                    health=dict(detail=dict(battery=dict(packs=[dict(design=50.0)], history=history))),
                    readings=dict(specs=dict(hardware=dict(disks=[dict(model='Old HDD', health='Warning')]))),
                    trends=[((today - dt.timedelta(days=d)).isoformat(), None, 30 - (40 - d) * 0.5) for d in (40, 20, 10, 0)])
        config = insights.settings(dict(unit_prices=dict(battery=1_500_000)))
        result = insights.fleet([item], config, today)
        parts = {part['part']: part for part in result['computers'][0]['replace']}
        self.assertEqual((parts['battery']['status'], parts['battery']['date']), ('expected', '2027-03-06'))
        # disk free 30, 20, 15, 10 %... at day 0 it is 10 %: the threshold is reached.
        self.assertEqual(parts['disk']['status'], 'reached')
        self.assertEqual(parts['disk-health']['health'], 'Warning')
        self.assertEqual([row['part'] for row in result['plan']['quarter']], ['disk', 'disk-health'])
        self.assertEqual([row['part'] for row in result['plan']['year']], ['battery'])
        self.assertEqual(result['plan']['year_budget'], dict(total=1_500_000.0, priced=1, unpriced=0))
        self.assertEqual(result['plan']['quarter_budget'], dict(total=0, priced=0, unpriced=2))

    def test_age_and_awake_hours(self):
        item = dict(readings=dict(specs=dict(hardware=dict(os=dict(installed='2024-11-16')))), awake_seconds=7_200_000)
        self.assertEqual(insights.age(item, dt.date(2026, 11, 16)), dict(years=2.0, since='2024-11-16', awake_hours=2000))
        self.assertEqual(insights.age({}, dt.date(2026, 11, 16)), dict(years=None, since=None, awake_hours=None))


class DesktopPanelTests(unittest.TestCase):
    """The desktop Reports page panel: its own on-battery readings (insights.json, never the ledger) and settings."""

    def setUp(self):
        import os
        import tempfile
        from unittest.mock import patch
        from qt_test_support import qt_app
        self.app = qt_app()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(os.environ, {'LIFETIME_DATA_DIR': directory.name}))
        import lifetime_insights_gui as gui
        self.gui, self.real_probe = gui, gui.probe
        self.enterContext(patch.object(gui, 'probe', return_value=dict(watts=15.0, kind='laptop')))

    def test_readings_make_the_power_measured_and_settings_are_kept(self):
        from lifetime_settings import load_settings
        from lifetime_shell import shut_down
        panel = self.gui.EnergyPanel()
        self.addCleanup(shut_down, self.app, panel)
        self.assertEqual(panel.method.text(), 'Energy needs the awake history. Turn on History in Settings.')
        panel.set_ledger(dict(daily={dt.date.today().isoformat(): 36000}))
        self.assertEqual(panel.method.text(), 'Power: 80 W · estimated: kind unknown, typical desktop')
        for _ in range(3):
            panel._sampled(0, dict(watts=15.0, kind='laptop'))
        self.assertEqual(len(self.gui.read_samples()['discharge']), 3)
        self.assertEqual(panel.method.text(), 'Power: 15 W · measured on battery (3 readings)')
        self.assertEqual(panel.cards['today'][0].headline.text(), '0.15 kWh')          # 10 h x 15 W
        panel.price.setValue(3000)
        panel.watts.setValue(40)
        panel._save()
        self.assertEqual(load_settings()['energy']['device_watts'], {'this': 40.0})
        self.assertEqual(panel.cards['today'][1].text(), '450 VND')                       # measured still wins
        night = [0.0] * 24
        night[22] = 3600.0
        panel.set_ledger(dict(daily={dt.date.today().isoformat(): 36000}, dated_hourly={dt.date.today().isoformat(): night}))
        self.assertTrue(panel.after.text().startswith('This computer was on 1.0 h after hours in the last 7 days'))
        panel.watts.setValue(0)
        panel._save()
        self.assertEqual(load_settings()['energy']['device_watts'], {})

    def test_forecast_panel_from_the_battery_report_and_daily_disk_readings(self):
        from lifetime_shell import shut_down
        today = dt.date.today()
        for d in (90, 60, 30, 0):
            self.gui.add_samples(dict(disk=40 - (90 - d) * 0.1), now=dt.datetime.combine(
                today - dt.timedelta(days=d), dt.time(12)).astimezone())
        panel = self.gui.ForecastPanel()
        self.addCleanup(shut_down, self.app, panel)
        history = [dict(date=(today - dt.timedelta(days=d)).isoformat(), full=50 - (90 - d) * 0.05) for d in (90, 60, 30, 0)]
        panel.set_data(dict(packs=[dict(design=50.0)], history=history), dict(installed=None))
        # Battery 100 % -> 91 % over 90 days (-0.1 %/day): 80 % in 110 days. Disk 40 % -> 31 %: 10 % in 210 days.
        self.assertEqual(panel.lines[0].text(), 'Battery: ' + (today + dt.timedelta(days=110)).isoformat()
                         + ' (from 4 readings over 90 days)')
        self.assertEqual(panel.lines[1].text(), 'System disk space: ' + (today + dt.timedelta(days=210)).isoformat()
                         + ' (from 4 readings over 90 days)')
        self.assertEqual(panel.lines[2].text(), 'Age: Not available')
        panel.set_data(dict(packs=[], history=[]), None)
        self.assertEqual(panel.lines[0].text(), 'Battery: No battery in this computer')

    def test_nothing_is_probed_without_the_battery_permission(self):
        self.assertEqual(self.real_probe(set()), {})


if __name__ == '__main__':
    unittest.main()
