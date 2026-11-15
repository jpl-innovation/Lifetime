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
        panel.watts.setValue(0)
        panel._save()
        self.assertEqual(load_settings()['energy']['device_watts'], {})

    def test_nothing_is_probed_without_the_battery_permission(self):
        self.assertEqual(self.real_probe(set()), {})


if __name__ == '__main__':
    unittest.main()
