"""1.3 server uptime on the dashboard: offscreen Qt with the fake admin connection of test_server_gui."""
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import unittest

from PyQt6.QtCore import QDate

from qt_test_support import dispose
from test_server_gui import ServerGuiTests as _Base, device


class UptimeGuiTests(_Base):
    # Only the tests below run here; the inherited ones run in test_server_gui.
    def test_keep_detailed_records_for_loads_saves_and_translates(self):
        uptime = self.window.uptime
        self.window._load_configuration()
        self.wait_idle()
        self.assertTrue(uptime.detail_days.isEnabled())
        self.assertEqual(uptime.detail_days.value(), 400)
        uptime.detail_days.setValue(730)
        uptime.detail_save.click()
        self.wait_idle()
        self.assertEqual(self.arguments('set_detail_retention'), [{'days': 730}])
        self.assertEqual(self.window.status_label.text(), 'Changes saved.')
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertEqual(uptime.detail_days.suffix(), ' ngày')
        self.assertEqual(uptime.detail_days.accessibleName(), 'Giữ dữ liệu chi tiết trong')

    def test_delete_before_says_what_goes_and_stops_at_today(self):
        from unittest.mock import patch
        self.assertEqual(self.window.retention_date.maximumDate(), QDate.currentDate())
        with patch.object(self.window, '_confirm', return_value=False) as confirm:
            self.window.retain_button.click()
        words = confirm.call_args.args[0]
        for part in ('monitoring history', 'resolved incidents', 'maintenance windows', 'open incidents',
                     'latest totals', 'cannot be undone'):
            self.assertIn(part, words)
        self.assertEqual(self.arguments('retain'), [])

    def test_range_control_quick_choices_custom_dates_and_limits(self):
        control = self.window.detail_range
        today = dt.date.today()
        self.assertEqual(control.period(), (today - dt.timedelta(days=29), today))
        self.assertFalse(control.from_date.isVisibleTo(control))
        control.set_key('custom')
        self.assertTrue(control.from_date.isVisibleTo(control))
        self.assertTrue(control.from_date.calendarPopup())
        self.assertEqual(control.period(), (today - dt.timedelta(days=29), today))     # starts from what was shown
        self.assertEqual(control.to_date.maximumDate(), QDate.currentDate())           # never in the future
        control.from_date.setDate(QDate(1990, 1, 1))                                   # years back
        self.assertEqual(control.from_date.date(), QDate(1990, 1, 1))
        self.assertEqual(control.from_date.minimumDate(), QDate(1971, 1, 1))           # not before 1971
        self.assertEqual(control.period()[0], today - dt.timedelta(days=3659))        # at most ten years at once
        control.from_date.setDate(QDate(today.year, today.month, today.day))
        control.to_date.setDate(QDate(2020, 1, 1))                                     # To before From moves From
        self.assertEqual(control.period(), (dt.date(2020, 1, 1), dt.date(2020, 1, 1)))
        control.set_key('all')
        self.assertIsNone(control.period())
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertEqual(control.choice.itemText(6), '5 năm qua')
        self.assertEqual(control.to_label.text(), 'Đến')

    def test_awake_time_for_five_years_is_averaged_per_month_and_missing_months_stay_empty(self):
        row = device(1)
        row['policy']['history'] = True
        today = dt.date.today()
        long_ago = today.replace(day=1) - dt.timedelta(days=700)
        row['history'] = {'daily': {today.isoformat(): 7200, long_ago.isoformat(): 36000,
                                    (long_ago + dt.timedelta(days=1)).isoformat(): 18000}}
        self.load([row])
        self.select(row['device_id'])
        chart = self.window.device_daily
        self.window.detail_range.set_key('5y')
        self.assertEqual(len(chart.values), 61)
        self.assertEqual(chart.values[-1], 2.0)
        self.assertIn(7.5, chart.values)                        # (10 h + 5 h) / 2 recorded days
        self.assertEqual(sum(value is not None for value in chart.values), 2)
        self.assertIn('· 2 of ', chart.details[chart.values.index(7.5)])
        self.assertIn('each month', self.window.daily_summary.text())
        self.window.detail_range.set_key('all')                # from its first record
        self.assertIsNotNone(chart.values[0])
        self.window.detail_range.set_key('12m')
        self.assertEqual(len(chart.values), len(set(chart.keys)))
        self.assertTrue(chart.keys[0].startswith('Week of'))

    def test_overview_and_energy_ask_for_the_chosen_period(self):
        self.load([device(0)])
        today = dt.date.today()
        self.window.fleet_range.set_key('12m')
        self.assertTrue(self.window._fleet_timer.isActive())
        self.window._load_fleet()
        self.wait_idle()
        fleet = self.arguments('fleet')[-1]
        import lifetime_uptime
        self.assertEqual((fleet['start'], fleet['end']),
                         tuple(day.isoformat() for day in lifetime_uptime.preset_range('12m', today)))
        self.assertNotIn('days', fleet)
        samples = [dict(day=(today - dt.timedelta(days=offset)).isoformat(), seconds=None if offset % 3 else 3600, devices=1)
                   for offset in range(364, -1, -1)]
        self.window.uptime.render_fleet_chart(dict(daily=samples, range=dict(seconds=3600 * 122, devices=3)))
        chart = self.window.daily_chart
        self.assertLess(len(chart.samples), 60)                 # weeks, not 365 bars
        self.assertTrue(all(sample['seconds'] is not None for sample in chart.samples))
        self.assertEqual({sample['seconds'] for sample in chart.samples}, {3600})     # per recorded day, like one computer
        self.assertIn('122 h 00 m awake on 3 computers', self.window.fleet_range_total.text())
        ui = self.window.insights
        self.api.insight_items = [dict(device_id='a', name='Lab PC', group='Lab', kind='desktop',
                                       history=dict(daily={today.isoformat(): 36000,
                                                           (today - dt.timedelta(days=200)).isoformat(): 36000}))]
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        ui.period.set_key('12m')
        self.wait_idle()
        call = self.arguments('insights')[-1]
        self.assertEqual(call['end'], today.isoformat())
        card, cost = ui.cards['range']
        self.assertEqual(card.headline.text(), '1.60 kWh')       # 2 x 10 h x 80 W
        self.assertEqual(card.title.text(), 'Last 12 months')
        self.assertEqual(ui.top_title.text(), 'Top consumers, Last 12 months')

    def test_report_dialog_takes_any_period(self):
        from lifetime_server_export import ServerExportDialog
        client = SimpleNamespace(directory=Path(self.temporary.name), call=lambda *a, **k: None)
        dialog = ServerExportDialog(client, ['one'])
        self.addCleanup(dispose, dialog)
        self.assertFalse(dialog.range_select.isVisibleTo(dialog))
        self.assertEqual(dialog.options()['start'], None)       # All: all available history, as before
        dialog.period.set_key('last-month')
        first = dt.date.today().replace(day=1) - dt.timedelta(days=1)
        self.assertEqual((dialog.options()['start'], dialog.options()['end']),
                         (first.replace(day=1).isoformat(), first.isoformat()))
        dialog.period.set_custom(dt.date(2021, 3, 1), dt.date(2021, 3, 31))
        self.assertEqual((dialog.options()['start'], dialog.options()['end']), ('2021-03-01', '2021-03-31'))

    def test_uptime_and_downtime_of_one_computer(self):
        today = dt.date.today()
        days = [(today - dt.timedelta(days=offset)).isoformat() for offset in (1, 2, 3)]
        off = dict(asleep_s=28800, off_s=18000, unknown_s=0, restarts=2, shutdowns=1, unexpected_stops=1)
        new, old = device(1), device(2)
        for row in (new, old):
            row['policy']['history'] = True
        new['history'] = dict(daily={day: 36000 for day in days}, downtime_daily={days[0]: off, days[1]: off},
                              first_seen='2025-03-04T08:00:00+07:00', first_seen_source='os-install-date',
                              recording_started='2026-11-01T09:00:00+07:00')
        old['history'] = dict(daily={days[0]: 7200})
        import lifetime_availability as availability
        found = availability.summarise(dict(good_s=72000, bad_s=7200, unknown_s=7200, excluded_s=0))
        self.api.availability = dict(devices=[dict(found, device_id=new['device_id'], outages=3, longest_outage_s=5400)])
        self.load([new, old])
        self.select(new['device_id'])
        uptime = self.window.uptime
        self.window.detail_range.set_key('7d')
        self.wait_idle()
        chart = uptime.downtime_chart
        self.assertEqual(len(chart.items), 7)
        yesterday = chart.items[-2]
        self.assertEqual(yesterday['parts'], dict(awake_s=36000, asleep_s=28800, off_s=18000, unknown_s=3600))
        self.assertIn('Restarts 2 · unexpected stops 1', yesterday['tip'])
        self.assertEqual(chart.items[-4]['parts']['unknown_s'], 86400 - 36000)     # awake only that day: the rest unknown
        summary = uptime.downtime_summary.text()
        self.assertIn('Restarts 4 · clean shutdowns 2 · unexpected stops 2', summary)
        self.assertIn('Asleep 16 h 00 m', summary)
        self.assertIn('Sleep and off time reported for 2 of 7 days.', uptime.downtime_coverage.text())
        self.assertIn('Windows installation date', uptime.downtime_coverage.text())
        self.assertIn('recorded by Lifetime since', uptime.downtime_coverage.text())
        self.assertEqual(uptime.availability_text.text(),
                         'Availability in expected hours: 90.909 % (coverage 91.667 %) · 3 outages, longest 1 h 30 m')
        self.assertNotIn('downtime_daily', self.window._detail['history'])     # never in the detail reply
        days_call = self.arguments('uptime_days')[-1]
        self.assertEqual((days_call['device_id'], days_call['end']), (new['device_id'], today.isoformat()))
        call = self.arguments('availability')[-1]
        self.assertEqual((call['device_ids'], call['end']), ([new['device_id']], today.isoformat()))
        self.select(old['device_id'])
        self.assertIn('does not report sleep and off time', uptime.downtime_coverage.text())
        self.assertNotIn('Restarts', uptime.downtime_summary.text())
        self.assertEqual(uptime.downtime_chart.items[-2]['parts'], dict(awake_s=7200, asleep_s=0, off_s=0, unknown_s=79200))
        self.assertEqual(uptime.availability_text.text(), 'Availability in expected hours: not available')
        self.window.language = 'vi'
        self.window.retranslate()
        self.window._render_daily(self.window._detail)
        self.assertIn('Lifetime trước 1.3', uptime.downtime_coverage.text())

    def test_fleet_uptime_bars_on_the_overview(self):
        today = dt.date.today()
        days = [(today - dt.timedelta(days=offset)).isoformat() for offset in (1, 0)]
        rows = [dict(device_id='b', name='Flaky', group='', availability=50.0, coverage=100.0, status='ok', outages=1,
                     longest_outage_s=7200, bars=[[120, 120, 0], [600, 0, 0]]),
                dict(device_id='c', name='Laptop', group='', availability=None, coverage=None, status='not-scheduled',
                     outages=0, longest_outage_s=0, bars=[[0, 0, 0], [0, 0, 0]])]
        self.api.uptime_bars = dict(rows=rows, buckets=[[day, day] for day in days], total=3, coverage_floor=80)
        self.window.uptime._bars = None         # the window's first load (no computers) is otherwise kept five minutes
        self.load([device(0)])
        call = self.arguments('uptime_bars')[-1]
        self.assertEqual((call['end'], call['offset'], call['limit']), (today.isoformat(), 0, 50))
        bars = self.window.uptime.fleet_bars
        self.assertEqual(bars.kinds, [['down', 'good'], ['none', 'none']])
        tip = bars.tip(0, 0)
        self.assertIn('Flaky', tip)
        self.assertIn('Down (under 75 %)', tip)
        self.assertIn('Available 50.0 % of the observed expected hours', tip)
        self.assertIn('offline 2 h 00 m', tip)
        self.assertIn('Not expected online', bars.tip(1, 1))
        self.assertTrue(self.window.uptime.bars_more.isVisibleTo(self.window))
        self.assertEqual(self.window.uptime.bars_count.text(), 'Showing 2 of 3 computers')
        self.window.uptime.bars_more.click()
        self.wait_idle()
        self.assertEqual(self.arguments('uptime_bars')[-1]['offset'], 2)
        self.assertEqual(len(bars.rows), 4)                     # the fake answers the same page again
        bars.grab()                                              # paints in either theme without error
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertIn('kém nhất trước', self.window.uptime.bars_note.text())

    def test_five_hundred_computers_by_365_days_paint_quickly(self):
        import random
        import time
        from lifetime_server_uptime import FleetBars
        chance = random.Random(3)
        rows = [dict(device_id=str(n), name=f'PC-{n:03}', bars=[[chance.choice((1440, 1300, 700, 0)), chance.choice((0, 60, 700)),
                                                                   chance.choice((0, 0, 1440))] for _ in range(365)])
                for n in range(500)]
        bars = FleetBars(lambda key, **values: key.format(**values))
        bars.resize(1000, 100)
        began = time.perf_counter()
        bars.set_rows(rows, [str(n) for n in range(365)], 80)
        image = bars.grab()                                      # every row: the worst case, nothing scrolled away
        took = time.perf_counter() - began
        self.assertEqual(image.height(), FleetBars.HEADER + 500 * FleetBars.ROW)
        self.assertLess(took, 10)
        print(f'\n500 computers x 365 days: classified and painted in {took:.2f} s')


    def test_a_refused_period_is_shown_as_not_available_never_as_other_figures(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QEvent, Qt
        self.load([device(0)])
        self.window.fleet_range.set_key('12m')
        self.api.fail.add('fleet')
        self.window._load_fleet()
        self.wait_idle()
        self.assertEqual([op for op, arguments, _ in self.api.calls if op == 'fleet'][-1:], ['fleet'])
        self.assertEqual(self.window.daily_chart.samples, [])
        self.assertEqual(self.window.daily_chart.missing, 'Fleet totals for this period are not available.')
        self.assertEqual(self.window.fleet_range_total.text(), 'Last 12 months: not available')
        self.api.fail.discard('fleet')
        ui = self.window.insights
        today = dt.date.today().isoformat()
        self.api.insight_items = [dict(device_id='a', name='Lab PC', group='Lab', kind='desktop',
                                       history=dict(daily={today: 36000}))]
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        self.assertEqual(ui.cards['range'][0].headline.text(), '0.80 kWh')
        self.api.fail.add('insights')
        ui.period.set_key('7d')
        self.wait_idle()
        self.assertEqual(ui.cards['range'][0].headline.text(), '—')       # not the 30 days under "Last 7 days"
        self.assertIn('not available', ui.error.text())
        self.assertEqual(len([op for op, arguments, _ in self.api.calls if op == 'insights' and 'start' not in arguments]), 0)
        bars = self.window.uptime.fleet_bars
        bars.set_rows([dict(device_id='a', name='A', bars=[])], [], 80)
        bars.resize(600, bars.height())
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(400, 30), QPointF(400, 30), Qt.MouseButton.NoButton,
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        bars.mouseMoveEvent(event)
        bars.grab()


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromNames([f'{__name__}.UptimeGuiTests.{name}' for name in dir(UptimeGuiTests)
                                      if name.startswith('test_') and name not in dir(_Base)])


if __name__ == '__main__':
    unittest.main()
