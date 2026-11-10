"""Wipe reveal for dashboard cards: a moving clip uncovers each card left to right.

The clip is a widget mask, so nothing is laid out again while it runs, and it is removed the moment the
animation ends (the equivalent of clearing the property), so shadows, focus rings and tooltips are never left
clipped. Starting a reveal on a card first stops any reveal already running on it, so quick tab changes never
stack animations. Reduced motion shows the card at once.
"""
import os

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPauseAnimation, QSequentialAnimationGroup, QTimer,
                          QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QRegion

DURATION = 800      # ms, per card
STAGGER = 60        # ms between cards
NUMBER_DELAY = 150  # ms: numbers start just after the wipe does, so they land as the card is uncovered


def _stop(widget):
    group = getattr(widget, '_wipe', None)
    if group is not None:
        widget._wipe = None
        group.stop()
        group.deleteLater()
        widget.clearMask()


def reveal(widgets, motion_reduced=False, delay=0, stagger=STAGGER, duration=DURATION):
    """Uncover each visible widget in turn. Widgets are hidden behind an empty mask from this call on, so
    there is no flash of the finished card before its animation begins."""
    cards = [w for w in widgets if w is not None and w.isVisibleTo(w.window())]
    for card in cards:
        _stop(card)
    if motion_reduced:
        return
    for index, card in enumerate(cards):
        card.setMask(QRegion(0, 0, 0, 0))
        group = QSequentialAnimationGroup(card)
        group.addAnimation(QPauseAnimation(delay + index * stagger))
        sweep = QVariantAnimation(group)
        sweep.setStartValue(0.0)
        sweep.setEndValue(1.0)
        sweep.setDuration(duration)
        sweep.setEasingCurve(QEasingCurve.Type.OutCubic)      # the same curve as GSAP's power3.out

        def frame(value, card=card):
            width = max(1, round(card.width() * value))
            card.setMask(QRegion(0, 0, width, card.height()))
        sweep.valueChanged.connect(frame)
        group.addAnimation(sweep)
        group.finished.connect(lambda card=card, group=group: _finished(card, group))
        card._wipe = group
        group.start()


def _finished(card, group):
    if getattr(card, '_wipe', None) is group:
        card._wipe = None
        card.clearMask()
    group.deleteLater()


def count_up(label, motion_reduced=False, delay=NUMBER_DELAY, duration=700):
    """Count a whole-number label up from zero, starting just after the wipe. Anything that is not a plain
    integer is left alone. If the label is changed by anyone else meanwhile the count gives way to it."""
    stale = getattr(label, '_count', None)
    if stale is not None:
        label._count = None
        stale.stop()
        stale.deleteLater()
    text = label.text()
    if motion_reduced or not text.isdigit() or int(text) == 0:
        return
    target = int(text)
    animation = QVariantAnimation(label)
    animation.setStartValue(0.0)
    animation.setEndValue(float(target))
    animation.setDuration(duration)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    state = {'written': None}

    def frame(value):
        shown = str(round(value))
        if state['written'] is not None and label.text() != state['written']:
            animation.stop()        # fresh data arrived; do not overwrite it with an older count
            return
        label.setText(shown)
        state['written'] = shown

    def finished():
        if label.text() == state['written']:
            label.setText(str(target))
        label._count = None
        animation.deleteLater()

    animation.valueChanged.connect(frame)
    animation.finished.connect(finished)
    label._count = animation
    label.setText('0')              # no flash of the final number before the count begins
    state['written'] = '0'
    QTimer.singleShot(int(delay), lambda: animation.start() if label._count is animation else None)


def phase(value, start, end):
    """Progress of one beat inside a longer entrance: 0 before `start`, 1 after `end`, eased out between.
    Widgets paint their own choreography from one shared 0..1 value, so a single entrance can have many beats."""
    if end <= start:
        return 1.0 if value >= end else 0.0
    t = min(1.0, max(0.0, (value - start) / (end - start)))
    return 1 - (1 - t) ** 3


class Intro(QObject):
    """One authored entrance for one widget. It plays once, the first time the widget is on screen with real data,
    never again on tab changes or refreshes. The widget paints from `value` (0..1), so it needs no layout change,
    and a refresh that arrives mid-entrance simply continues from where it is (nothing restarts or snaps)."""
    changed = pyqtSignal()

    def __init__(self, widget, duration=900, delay=0, reduced=None):
        super().__init__(widget)
        self.widget, self.delay = widget, delay
        self.reduced = reduced or (lambda: os.environ.get('LIFETIME_REDUCE_MOTION') == '1')
        self.value, self.done, self._armed, self._started = 0.0, False, False, False
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(duration)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._finish)
        widget.installEventFilter(self)
        widget.destroyed.connect(self._animation.stop)

    def _step(self, value):
        self.value = float(value)
        self.widget.update()
        self.changed.emit()

    def _finish(self):
        self.value, self.done = 1.0, True
        self.widget.update()

    def ready(self):
        """Real data has arrived. Start now if the widget is visible, otherwise when it first is."""
        self._armed = True
        if self.reduced():
            self._started = True
            self._finish()
            return
        self._maybe_start()

    def _maybe_start(self):
        if self._armed and not self._started and self.widget.isVisible():
            self._started = True
            QTimer.singleShot(int(self.delay), self._animation.start)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._maybe_start)
        return False
