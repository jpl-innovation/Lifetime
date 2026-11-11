"""Authored entrances: Intro plays one choreography once, widgets paint their beat from it with phase() and
progress(); paint_roll rolls digits into place; RollLabel and fade_in use it. Nothing here changes geometry or masks.
Reduced motion shows everything at once."""
import os

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QFontMetricsF, QPainter, QPalette
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel

ROLL_DELAY = 160    # ms: numbers start rolling just after the entrance does


def paint_roll(painter, rect, text, progress, align=Qt.AlignmentFlag.AlignLeft):
    """Draw `text` with every digit rolling into place like an odometer; everything else stays put.

    `text` is always the FINAL text and `progress` runs 0..1, so a value that changes mid-roll just keeps rolling
    toward the new text. Each digit keeps the width of its final digit, so nothing shifts when the roll ends. The
    last two digits turn one extra time, higher digits start a moment later: a little life, not a show."""
    rect = QRectF(rect)
    flags = int(align | Qt.AlignmentFlag.AlignVCenter)
    if progress >= 1.0 or not any(c.isdigit() for c in text):
        painter.drawText(rect, flags, text)
        return
    fm = QFontMetricsF(painter.font())
    widths = [fm.horizontalAdvance(c) for c in text]
    total = sum(widths)
    if align & Qt.AlignmentFlag.AlignHCenter:
        x = rect.left() + (rect.width() - total) / 2
    elif align & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - total
    else:
        x = rect.left()
    line = fm.height()
    top = rect.top() + (rect.height() - line) / 2
    base = top + fm.ascent()
    digits = [i for i, c in enumerate(text) if c.isdigit()]
    for index, ch in enumerate(text):
        width = widths[index]
        if not ch.isdigit():
            painter.drawText(QPointF(x, base), ch)
        else:
            k = digits.index(index)
            from_right = len(digits) - 1 - k
            t = min(1.0, max(0.0, (progress - 0.05 * k) / (1 - 0.05 * k)))
            value = (int(ch) + (10 if from_right < 2 else 0)) * (1 - (1 - t) ** 3)
            whole = int(value)
            fraction = value - whole
            painter.save()
            painter.setClipRect(QRectF(x, top, width, line))
            for glyph, offset in ((whole % 10, -fraction * line), ((whole + 1) % 10, (1 - fraction) * line)):
                glyph_width = fm.horizontalAdvance(str(glyph))
                painter.drawText(QPointF(x + (width - glyph_width) / 2, base + offset), str(glyph))
            painter.restore()
        x += width


class RollLabel(QLabel):
    """A label whose digits roll into place once on request. Its text is always the real text, so accessibility,
    tests and live updates see the true value; only the painting is animated."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self._roll = 1.0
        self._rolling = None

    def roll_in(self, delay=ROLL_DELAY, duration=850, reduced=False):
        if self._rolling is not None:
            self._rolling.stop()
            self._rolling.deleteLater()
            self._rolling = None
        if reduced or not any(c.isdigit() for c in self.text()):
            self._roll = 1.0
            return
        self._roll = 0.0
        self.update()
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(duration)
        animation.valueChanged.connect(self._set_roll)
        animation.finished.connect(lambda: self._set_roll(1.0))
        self._rolling = animation
        QTimer.singleShot(int(delay), lambda: animation.start() if self._rolling is animation else None)

    def _set_roll(self, value):
        self._roll = float(value)
        self.update()

    def paintEvent(self, event):
        if self._roll >= 1.0:
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        paint_roll(painter, self.contentsRect(), self.text(), self._roll,
                   self.alignment() & (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignHCenter)
                   or Qt.AlignmentFlag.AlignLeft)


def fade_in(widget, delay=0, duration=260, reduced=False):
    """Settle a widget in with a short fade. The effect is removed when it ends so nothing stays rasterised."""
    if reduced or widget is None:
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QVariantAnimation(effect)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(duration)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.valueChanged.connect(lambda v: effect.setOpacity(float(v)))
    animation.finished.connect(lambda: widget.graphicsEffect() is effect and widget.setGraphicsEffect(None))
    QTimer.singleShot(int(delay), animation.start)


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
