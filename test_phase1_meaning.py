"""Honest totals/source labels and awake-only UI interpolation."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qt_test_support import qt_app
import lifetime_gui as gui
import lifetime_i18n as i18n
import lifetime_reports as reports
import lifetime_settings as settings
import lifetime_tracker as tracker
from PyQt6.QtCore import QVariantAnimation
from PyQt6.QtTest import QTest


class PhaseOneMeaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def tearDown(self):
        i18n.set_language('en')

    def test_measured_imported_baseline_are_separate_all_data_and_period(self):
        ledger = tracker.new_ledger()
        ledger.update(total_seconds=1000, baseline_seconds=100, last_update='2026-11-07T12:00:00+00:00',
                      daily={'2026-11-07': 250}, imported={'seconds': 400, 'boots': 1, 'daily': {'2026-11-07': 100}})
        self.assertEqual(reports.source_totals(ledger), dict(measured_seconds=600, imported_seconds=400, baseline_seconds=100))
        period = reports.source_totals(ledger, '2026-11-07', '2026-11-07')
        self.assertEqual(period, dict(measured_seconds=150, imported_seconds=100, baseline_seconds=None))
        report = reports.build_report(ledger, dict(completed=True, history=True))
        self.assertEqual(report['stats']['awake_seconds'], 1000)
        self.assertEqual(report['stats']['baseline_seconds'], 100)
        html = reports.report_html(report)
        self.assertIn('Directly measured awake time', html)
        self.assertIn('Reconstructed OS history', html)

    def test_zero_verify_difference_does_not_prove_coverage(self):
        ledger = tracker.new_ledger()
        ledger.update(total_seconds=100, last_update='2026-11-07T12:00:00+00:00')
        self.assertEqual(reports.coverage(ledger)['status'], 'unknown')
        ledger['coverage_gaps'] = [dict(start='2026-11-07T11:00:00+00:00', end='2026-11-07T12:00:00+00:00', reason='awake_clock_discontinuity')]
        self.assertEqual(reports.coverage(ledger)['status'], 'partial')

    def test_wall_clock_does_not_interpolate_sleep_into_live_total(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'LIFETIME_DATA_DIR': directory}):
            prefs = settings.update_settings(dict(completed=True, history=False, resources=False), directory)
            ledger = tracker.new_ledger()
            ledger.update(total_seconds=100, last_clock=100, last_update=dt.datetime.now().astimezone().isoformat())
            tracker.atomic_json(Path(directory)/'ledger.json', ledger)
            tracker.atomic_json(Path(directory)/'status.json', dict(running=True, heartbeat=1000))
            with patch.object(gui.time, 'time', return_value=1060), patch.object(gui, 'awake_clock', return_value=100):
                window = gui.Window(prefs)
                window.worker.timer.stop()
                window.timer.stop()
                self.assertEqual(window.hero.target, 100)
                window.close()

    def test_hiding_window_finishes_and_stops_owned_animations(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'LIFETIME_DATA_DIR': directory}):
            prefs = settings.update_settings(dict(completed=True, history=False, resources=False), directory)
            window = gui.Window(prefs)
            window.worker.timer.stop()
            window.show()
            self.app.processEvents()
            window.hide()
            QTest.qWait(300)  # delayed entrance callbacks must not restart motion
            self.assertTrue(all(a.state() != a.State.Running for a in window.findChildren(QVariantAnimation)))
            window.close()


if __name__ == '__main__':
    unittest.main()
