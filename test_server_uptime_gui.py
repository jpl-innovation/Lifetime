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
        control.from_date.setDate(QDate(1990, 1, 1))                                   # no lower limit
        self.assertEqual(control.from_date.date(), QDate(1990, 1, 1))
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


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromNames([f'{__name__}.UptimeGuiTests.{name}' for name in dir(UptimeGuiTests)
                                      if name.startswith('test_') and name not in dir(_Base)])


if __name__ == '__main__':
    unittest.main()
