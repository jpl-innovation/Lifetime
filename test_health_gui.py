"""Offscreen Health states, consent revocation and translated native navigation."""
import datetime as dt
import itertools
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import Qt, QAbstractAnimation
from PyQt6.QtTest import QTest
from qt_test_support import qt_app
from lifetime_health_gui import HealthPage
from lifetime_i18n import set_language
from test_health import REPORT, summary
import lifetime_health as health


class HealthPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        for window in self.app.topLevelWidgets():
            if not window.isVisible():
                for animation in window.findChildren(QAbstractAnimation):
                    animation.stop()
        # Earlier GUI fixtures can retain closed windows through animation
        # closures. Scope translation listeners to the page under test.
        listeners = patch('lifetime_i18n._listeners', [])
        listeners.start()
        self.addCleanup(listeners.stop)
        motion = patch.dict(os.environ, LIFETIME_REDUCE_MOTION='1')
        motion.start()
        self.addCleanup(motion.stop)
        set_language('en')
        self.submit = patch('lifetime_health_gui.Tasks.submit', side_effect=itertools.count(1))
        self.jobs = self.submit.start()
        self.addCleanup(self.submit.stop)
        self.page = HealthPage(dict(completed=True),lambda: None)
        self.page.resize(900,1200)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        self.page.close(); self.page.deleteLater(); self.app.processEvents()
        set_language('en')

    def test_off_does_not_submit_and_has_settings_button(self):
        from PyQt6.QtWidgets import QPushButton
        self.jobs.assert_not_called()
        self.assertTrue(self.page.disabled.isVisible())
        self.assertTrue(any(button.text() == 'Open Settings' for button in self.page.findChildren(QPushButton)))

    def test_loading_failure_empty_and_revocation_discards_late_results(self):
        self.page.set_settings(dict(completed=True,health=True))
        self.assertEqual(len(self.page.jobs),4)
        self.assertEqual(self.page.states['stability'].text(),'Loading…')
        generation=self.page.generation
        for number,(key,_) in list(self.page.jobs.items()):
            self.page.finished(number, dict(packs=[],history=[],usage={}) if key == 'battery' else [] if key == 'stability' else None)
        self.assertIn('No battery',self.page.states['battery'].text())
        self.assertIn('No unexpected',self.page.stability_chart.message)
        self.assertIn('unavailable',self.page.states['sleep'].text())
        self.assertTrue(self.page.battery_body.isHidden())
        self.page.jobs[99]=('battery',generation)
        self.page.set_settings(dict(completed=True,health=False))
        self.page.finished(99,health.parse_battery_report(REPORT))
        self.assertEqual(self.page.data,{})
        self.assertFalse(self.page.timer.isActive())

    def test_above_100_range_translation_and_single_series(self):
        self.page.set_settings(dict(completed=True,health=True))
        self.page.data.update(battery=health.parse_battery_report(REPORT),stability=[],sleep=dict(daily={},nights=[]))
        self.page.render()
        self.assertEqual(self.page.battery_value.text(),'107.4%')
        self.assertIn('100%',self.page.battery_value.toolTip())
        self.page.range.setCurrentIndex(1)
        self.assertEqual(self.page.range.index,1)
        self.assertTrue(self.page.range.buttons[3].isHidden())
        set_language('vi'); self.app.processEvents()
        self.assertIn('chu kỳ',self.page.battery_details.text().lower())
        for _, plot in self.page.power_widgets:
            self.assertIsNone(plot.bar_color)   # None = the active theme's accent
            self.assertEqual(plot.overlay,[])

    def test_server_health_rows_and_text_hint(self):
        from test_server_gui import FakeAdmin, device
        import lifetime_server_gui as gui
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            fake = FakeAdmin(folder)
            with patch.object(gui, 'AdminClient',return_value=fake):
                window = gui.ServerWindow(folder,1)
                try:
                    window.timer.stop()
                    row = device(1)
                    row.update(health=summary(),unstable=True)
                    window._render_rows([row])
                    self.assertIn('Unstable',window.table.item(0,2).text())
                    self.assertFalse(window.table.item(0,2).icon().isNull())
                    window._render_detail(row)
                    contents=window.detail_text.toPlainText()
                    self.assertIn('Health',contents)
                    self.assertIn('Crashes in 30 days',contents)
                    self.assertIn('107.4%',contents)
                finally:
                    window.close()
                    for _ in range(30):
                        self.app.processEvents(); QTest.qWait(5)


if __name__ == '__main__': unittest.main()
