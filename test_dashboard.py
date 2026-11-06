"""Worker deadlines and verification states; fixtures never touch a real ledger."""
from concurrent.futures import Future
import importlib.util
from pathlib import Path
import time
import unittest
from unittest.mock import patch

if importlib.util.find_spec('PyQt6') is None:
    raise unittest.SkipTest('PyQt6 is required for dashboard checks')

from PyQt6.QtWidgets import QApplication
import lifetime_dashboard as dash
import lifetime_gui as gui
import lifetime_tracker as tracker
from qt_test_support import qt_app
from lifetime_settings import CATEGORIES


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.worker = dash.ProbeWorker()
        self.worker.timer.stop()
        self.worker.probes = {}

    def tearDown(self):
        self.worker.stop()

    def test_stalled_probe_expires_without_waiting_or_resubmission(self):
        pending = Future()
        self.worker.jobs['ram'] = (pending, time.monotonic()-6, False)
        values = []
        self.worker.changed.connect(values.append)
        start = time.monotonic()
        self.worker.poll()
        self.assertLess(time.monotonic()-start, .2)
        self.assertIsNone(values[-1]['ram'])
        self.assertIn('ram', self.worker.jobs)
        pending.set_result({'total': 999})
        self.worker.poll()
        self.assertIsNone(self.worker.values['ram'])  # never publish late data
        self.assertNotIn('ram', self.worker.jobs)

    def test_completed_probe_is_published(self):
        done = Future()
        done.set_result({'total': 1024, 'used': 256, 'percent': 25})
        self.worker.jobs['ram'] = (done, time.monotonic(), False)
        self.worker.poll()
        self.assertEqual(self.worker.values['ram']['percent'], 25)

    def test_verification_deadline_emits_unavailable(self):
        results = []
        self.worker.verified.connect(results.append)
        self.worker.jobs['verify'] = (Future(), time.monotonic()-6, False)
        self.worker.poll()
        self.assertEqual(results, [None])


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ledger = tracker.new_ledger()
        self.patches = [patch.object(gui, 'read_ledger', return_value=self.ledger),
                        patch.object(gui, 'data_dir', return_value=Path('shots/nonexistent-fixture'))]
        for p in self.patches:
            p.start()
        self.window = gui.Window(dict(completed=True, **{key: True for key in CATEGORIES}))
        self.window.timer.stop()
        self.window.worker.timer.stop()

    def tearDown(self):
        self.window.close()
        for p in reversed(self.patches):
            p.stop()

    def test_verify_busy_and_difference_states(self):
        with patch.object(self.window.worker, 'verify', return_value=True):
            self.window.verify()
        self.assertFalse(self.window.verify_button.isEnabled())
        self.assertTrue(self.window.verify_glyph.timer.isActive())
        for difference, phrase in [(0, 'Verified against'), (3600, 'Compared with')]:
            self.window.verification_finished(dict(ledger=self.ledger, verification=dict(
                event_seconds=152.8*3600, awake_seconds=152.8*3600+difference, difference=difference)))
            self.assertIn(phrase, self.window.verification.text())
            self.assertTrue(self.window.verify_button.isEnabled())
            self.assertFalse(self.window.verify_glyph.timer.isActive())
        self.window.verification_finished(None)
        self.assertIn('Not available', self.window.verification.text())

    def test_power_ac_zero_and_missing_gpu(self):
        self.window.system.update_values({'power': {'ac': True, 'state': 'On AC power', 'percent': 80},
                                          'battery': {'watts': 0}, 'gpu': []}, gui.THEMES['dark'])
        power = self.window.system.cards[0]
        self.assertEqual(power.fields['Power'][0].text(), '0 W · battery idle')
        self.assertIn('No graphics adapter detected', self.window.system.cards[2].fields['Graphics card'][0].text())
        self.window.system.update_values({'battery': {'watts': None}, 'gpu': None}, gui.THEMES['dark'])
        self.assertIn('Not available', power.fields['Power'][0].text())
        self.assertIn('Not available', self.window.system.cards[2].fields['Graphics card'][0].text())

    def test_responsive_tabs(self):
        self.window.resize(760, 560)
        self.window.show()
        self.app.processEvents()
        self.window.navigation.group.button(1).click()
        self.assertTrue(self.window.system.isVisible())
        self.assertFalse(self.window.history.isVisible())
        self.window.navigation.group.button(2).click()
        self.assertTrue(self.window.history.isVisible())
        self.window.resize(500, 560)
        self.app.processEvents()
        self.assertEqual(self.window.system.columns, 1)

    def test_reduced_motion_cards_keep_full_contrast(self):
        with patch.object(gui, 'REDUCE_MOTION', True):
            self.window.show()
            self.app.processEvents()
            for card in self.window.cards.values():
                self.assertEqual(card.effect.opacity(), 1)


if __name__ == '__main__':
    unittest.main()
