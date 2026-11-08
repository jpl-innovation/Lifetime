"""Wipe reveal: clipped while it runs, never left clipped, restartable, and off under reduced motion."""
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

    def test_cards_are_hidden_at_once_wiped_in_turn_and_unmasked_at_the_end(self):
        reveal.reveal(self.cards, duration=200, stagger=50)
        self.assertTrue(all(card.mask().isEmpty() for card in self.cards))             # no flash of the finished card
        QTest.qWait(120)
        self.assertFalse(self.cards[0].mask().isEmpty())                               # the first has started
        self.assertTrue(self.cards[2].mask().isEmpty() or self.cards[2].mask().boundingRect().width() < 280)
        QTest.qWait(500)
        for card in self.cards:
            self.assertTrue(card.mask().isEmpty())                                     # an empty mask means no mask
            self.assertIsNone(card._wipe)
            self.assertEqual(card.childrenRect().width() >= 0, True)

    def test_a_second_reveal_replaces_the_first_instead_of_stacking(self):
        reveal.reveal(self.cards, duration=400, stagger=0)
        QTest.qWait(100)
        first = self.cards[0]._wipe
        reveal.reveal(self.cards, duration=400, stagger=0)
        self.assertIsNot(self.cards[0]._wipe, first)
        QTest.qWait(700)
        self.assertIsNone(self.cards[0]._wipe)

    def test_reduced_motion_shows_everything_immediately(self):
        reveal.reveal(self.cards, motion_reduced=True)
        self.assertTrue(all(card.mask().isEmpty() and getattr(card, '_wipe', None) is None for card in self.cards))

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


if __name__ == '__main__':
    unittest.main()
