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
        self.worker.jobs['verify'] = (Future(), time.monotonic()-(dash.VERIFY_LIMIT+1), False)
        self.worker.poll()
        self.assertEqual(results, [None])

    def test_verification_gets_longer_deadline_than_ordinary_probes(self):
        """Bug 5: a large journal needs up to 20 s, so Verify must not be cut off at the 5 s probe limit."""
        results = []
        self.worker.verified.connect(results.append)
        pending = Future()
        self.worker.jobs['verify'] = (pending, time.monotonic()-10, False)     # 10 s old: still within its limit
        self.worker.poll()
        self.assertEqual(results, [])
        self.assertIn('verify', self.worker.jobs)
        pending.set_result({'verification': {'message': 'done'}})
        self.worker.poll()
        self.assertEqual(results, [{'verification': {'message': 'done'}}])
        self.assertGreater(dash.VERIFY_LIMIT, 20)                               # longer than the journal query timeout


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

    def test_verify_reports_small_gaps_instead_of_hiding_them(self):
        """A 116 s gap used to read 'difference 0.0 h' under 'Verified'. Gaps over a minute are now stated."""
        def show(difference):
            self.window.verification_finished(dict(ledger=self.ledger, verification=dict(
                event_seconds=5*3600, awake_seconds=5*3600+difference, difference=difference)))
            return self.window.verification.text()
        self.assertIn('Verified against', show(30))
        self.assertNotIn('Gap of', show(30))
        gap = show(116)
        self.assertIn('Gap of 1 m 56 s', gap)
        self.assertNotIn('Verified against', gap)
        self.assertIn('Compared with', gap)
        self.assertIn('Review the difference', show(2*3600))                    # large gaps keep the stronger wording
        self.assertEqual([gui.format_gap(s) for s in (0, 45, 60, 116, 3605, 11100)],
                         ['0 s', '45 s', '1 m 00 s', '1 m 56 s', '1 h 00 m', '3 h 05 m'])

    def test_tests_use_the_offscreen_platform(self):
        """The responsive-tabs test failed on tiling window managers because the real compositor ignored resize()."""
        self.assertEqual(self.app.platformName(), 'offscreen')

    def test_power_ac_zero_and_missing_gpu(self):
        self.window.system.update_values({'power': {'ac': True, 'state': 'On AC power', 'percent': 80},
                                          'battery': {'watts': 0}, 'gpu': []}, gui.THEMES['dark'])
        power = self.window.system.cards[0]
        self.assertEqual(power.fields['Power'][0].text(), '0 W · battery idle')
        self.assertIn('No graphics adapter detected', self.window.system.cards[2].fields['Graphics card'][0].text())
        self.window.system.update_values({'battery': {'watts': None}, 'gpu': None}, gui.THEMES['dark'])
        self.assertIn('Not available', power.fields['Power'][0].text())
        self.assertIn('Not available', self.window.system.cards[2].fields['Graphics card'][0].text())

    def test_grouped_rows_inline_sparklines_and_single_data_toggle(self):
        from PyQt6.QtWidgets import QPushButton
        panel = self.window.system
        panel.update_values(dict(cpu=25, ram=dict(percent=40), power=dict(percent=80),
                                 battery=dict(watts=8), gpu=[dict(name='GPU', watts=12)],
                                 counters=dict(down=1024, up=512)), gui.THEMES['dark'])
        # Narrow content: one column in reading order. Wide content: Power | Network, Graphics | Computer,
        # so labels stay close to their values instead of ~1400 px apart.
        panel.set_columns(1)
        self.assertEqual([panel.grid.getItemPosition(i)[:2] for i in range(4)], [(0, 0), (1, 0), (2, 0), (3, 0)])
        panel.set_columns(2)
        self.assertEqual([panel.grid.getItemPosition(i)[:2] for i in range(4)], [(0, 0), (0, 1), (1, 0), (1, 1)])
        for key, index in [('watts', 0), ('down', 1), ('up', 1), ('gpu', 2), ('cpu', 3), ('ram', 3)]:
            chart = panel.sparklines.charts[key]
            self.assertTrue(panel.cards[index].isAncestorOf(chart))
            self.assertEqual((chart.width(), chart.height()), (120, 28))
            self.assertFalse(chart.message)
        self.assertEqual(panel.findChildren(QPushButton), [panel.data_toggle])
        panel.sparklines.sample()
        panel.data_toggle.click()
        self.assertFalse(panel.sparklines.isHidden())
        for card in panel.sparklines.cards.values():
            self.assertFalse(card.table.isHidden())
            self.assertEqual(card.table.rowCount(), 1)
        panel.data_toggle.click()
        self.assertTrue(panel.sparklines.isHidden())

    def test_consent_hides_groups_and_clears_live_data(self):
        panel = self.window.system
        panel.update_values(dict(cpu=45), gui.THEMES['dark'])
        panel.sparklines.sample()
        panel.set_settings(dict(network=True))
        self.assertEqual([card.isHidden() for card in panel.cards], [True, False, True, True])
        self.assertFalse(panel.sparklines.charts['cpu'].buffer.samples)
        self.assertEqual(panel.sparklines.cards['cpu'].table.rowCount(), 0)
        panel.set_settings({})
        self.assertTrue(all(card.isHidden() for card in panel.cards))
        self.assertTrue(panel.data_toggle.isHidden())

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
