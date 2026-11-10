"""Authored entrances: Intro plays one choreography once, widgets paint their beat from it with phase() and
progress(); count_up counts a plain whole-number label up. Nothing here changes geometry or masks.
Reduced motion shows everything at once."""
import os

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QTimer, QVariantAnimation, pyqtSignal

NUMBER_DELAY = 150  # ms: default pause before count_up starts


def count_up(label, motion_reduced=False, delay=NUMBER_DELAY, duration=700):
    """Count a whole-number label up from zero, after a short delay. Anything that is not a plain
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
        self.changed.emit()

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
            QTimer.singleShot(int(self.delay), self._begin)

    def _begin(self):
        if self.widget.isVisible():
            self._animation.start()
        else:
            self._started = False       # hidden again before it began: wait for the next time it is shown

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._maybe_start)
        return False


def progress(entrance, eased=True):
    """0..1 for a widget's slice of an Intro. `entrance` is (intro, start, end) or None (meaning finished)."""
    if entrance is None or entrance[0].done:
        return 1.0
    intro, start, end = entrance
    if eased:
        return phase(intro.value, start, end)
    return min(1.0, max(0.0, (intro.value - start) / (end - start)))
