"""Entrance helpers: numbers count up and land exactly; Intro plays once and progress() reads its slice."""
import unittest

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QWidget

import lifetime_reveal as reveal
from qt_test_support import qt_app


class RevealTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.window = QWidget()
        self.window.resize(300, 200)
        self.cards = [QWidget(self.window) for _ in range(3)]
        for index, card in enumerate(self.cards):
            card.setGeometry(10, 10 + index * 60, 280, 50)
        self.window.show()
        self.addCleanup(self.window.close)

    def test_numbers_count_up_after_a_short_delay_and_land_exactly(self):
        label = QLabel('16', self.window)
        reveal.count_up(label, delay=100, duration=300)
        self.assertEqual(label.text(), '0')
        QTest.qWait(60)
        self.assertEqual(label.text(), '0')                                            # not before the delay
        QTest.qWait(600)
        self.assertEqual(label.text(), '16')

    def test_fresh_data_wins_over_a_running_count(self):
        label = QLabel('40', self.window)
        reveal.count_up(label, delay=0, duration=600)
        QTest.qWait(150)
        label.setText('7')
        QTest.qWait(800)
        self.assertEqual(label.text(), '7')

    def test_words_and_zero_are_left_alone(self):
        for text in ('Not available', '113 h 33 m', '0'):
            label = QLabel(text, self.window)
            reveal.count_up(label)
            self.assertEqual(label.text(), text)

    def test_intro_plays_once_waits_for_data_and_reports_slices(self):
        intro = reveal.Intro(self.window, 200)
        slice_ = (intro, 0.5, 1.0)
        QTest.qWait(50)
        self.assertEqual(intro.value, 0.0)                                             # no data yet: nothing plays
        self.assertEqual(reveal.progress(slice_), 0.0)
        intro.ready()
        QTest.qWait(500)
        self.assertTrue(intro.done)
        self.assertEqual(reveal.progress(slice_), 1.0)
        self.assertEqual(reveal.progress(None), 1.0)
        intro.ready()                                                                  # a second ready() never replays
        self.assertTrue(intro.done)

    def test_reduced_motion_finishes_the_intro_at_once(self):
        intro = reveal.Intro(self.window, 200, reduced=lambda: True)
        intro.ready()
        self.assertTrue(intro.done)

    def test_system_meters_fill_once_and_then_follow_the_data(self):
        import lifetime_dashboard as dashboard
        from lifetime_theme import tokens
        page = dashboard.SystemDashboard()
        page.resize(900, 700)
        page.show()
        self.addCleanup(page.close)
        page.update_values({'cpu': 60, 'ram': {'percent': 80, 'used': 1, 'total': 2}}, tokens())
        meters = lambda: [m.value() for c in page.cards for _, m in c.fields.values() if m.isVisibleTo(c) and m.value()]
        QTest.qWait(150)
        self.assertTrue(all(v < 600 for v in meters()))                                # still filling
        QTest.qWait(1300)
        self.assertEqual(sorted(meters()), [600, 800])
        page.update_values({'cpu': 61, 'ram': {'percent': 80, 'used': 1, 'total': 2}}, tokens())
        self.assertEqual(sorted(meters()), [610, 800])                                 # later readings land at once


if __name__ == '__main__':
    unittest.main()
