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

    def test_a_rolling_number_keeps_its_real_text_rolls_then_lands_exactly(self):
        label = reveal.RollLabel('16', self.window)
        label.setStyleSheet('font-size: 28px; color: white;')
        label.resize(120, 40)
        label.roll_in(delay=60, duration=300)
        self.assertEqual(label.text(), '16')                  # the true value is always there for readers and tests
        self.assertEqual(label._roll, 0.0)                     # rolled back to zero until the delay passes
        QTest.qWait(60 + 120)
        self.assertTrue(0.0 < label._roll < 1.0)
        QTest.qWait(600)
        self.assertEqual(label._roll, 1.0)
        self.assertEqual(label.text(), '16')

    def test_rolling_digits_land_on_exactly_the_plain_rendering(self):
        from PyQt6.QtGui import QImage, QPainter, QPalette
        from PyQt6.QtCore import Qt
        def render(label):
            image = QImage(120, 40, QImage.Format.Format_ARGB32)
            image.fill(0)
            label.render(image)
            return image
        plain = QLabel('1,234 h 05 m', self.window)
        rolled = reveal.RollLabel('1,234 h 05 m', self.window)
        for label in (plain, rolled):
            label.setStyleSheet('font-size: 20px; color: white;')
            label.resize(120, 40)
        rolled._roll = 1.0
        self.assertEqual(render(plain), render(rolled))        # nothing shifts or changes when the roll ends
        rolled._roll = 0.5
        self.assertNotEqual(render(plain), render(rolled))     # and midway it really is different

    def test_words_and_reduced_motion_do_not_roll(self):
        for text, reduced in (('Not available', False), ('16', True)):
            label = reveal.RollLabel(text, self.window)
            label.roll_in(reduced=reduced)
            self.assertEqual(label._roll, 1.0)
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
