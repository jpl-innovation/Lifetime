"""Authored entrances: Intro plays one choreography once, widgets paint their beat from it with phase() and
progress(); paint_roll rolls digits into place; RollLabel and fade_in use it. Nothing here changes geometry or masks.
Reduced motion shows everything at once."""
import os

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QFontMetricsF, QPainter, QPalette
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

ROLL_DELAY = 160    # ms: numbers start rolling just after the entrance does


def later(delay, callback):
    """Run `callback` after `delay` ms on a coarse timer. Qt makes short single shots precise, and on Windows every
    precise timer holds one of the process's few multimedia timers. An entrance cascade can hold them all; the
    animation clock then falls back to the 15.6 ms system tick and every animation in the window runs at ~40 fps
    for as long as any animation keeps the clock alive (the status pulse loops forever)."""
    QTimer.singleShot(max(0, int(delay)), Qt.TimerType.CoarseTimer, callback)


def warm_pages(pages, busy, start=3000, gap=300):
    """Draw each page once, off screen, a few seconds after the window opens. A page's first drawing works out
    its style rules, fonts and layouts and costs several times a later one (up to ~90 ms for Settings or a page
    of charts); done here, in idle time one page at a time, the first visit costs what every later one does.
    `busy()` says a page change is playing; the next page then waits, so warming never stalls a transition."""
    queue = list(pages)

    def step():
        try:
            if busy():
                return later(500, step)
            if queue:
                queue.pop(0).grab()
                later(gap, step)
        except RuntimeError:        # the window was closed and deleted meanwhile
            pass
    later(start, step)


class PageEntrance(QWidget):
    """A page arrives as one picture. The page is drawn once into a pixmap and only that picture fades in and slides
    the last few pixels into place; then the live page takes over. Meanwhile the page sits behind a zero-opacity
    effect, which Qt skips without painting anything, so a frame costs one blit however much the page holds.
    (An opacity effect on the page itself re-renders every widget on the page on every frame.)"""
    finished = pyqtSignal()

    def __init__(self, page, offset, duration, start=0.15):
        super().__init__(page.parentWidget())
        self.page, self.offset, self.start = page, QPointF(offset), start
        self.picture, self.origin, self.value, self._done = None, QPointF(), 0.0, False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hider = QGraphicsOpacityEffect(page)
        self.hider.setOpacity(0.0)
        page.setGraphicsEffect(self.hider)
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._step)
        self.animation.finished.connect(self.finish)
        self.setGeometry(page.geometry())
        self.raise_()
        self.show()
        page.installEventFilter(self)
        # The click is answered at once: the first frame shows the menu moving and the old page gone. The picture
        # is taken just after that frame (and after the new page's pending layout, so it matches the live page).
        # Qt posts repaints at low priority, so a plain zero timer here would run first and delay that frame.
        self._scheduled = False
        later(100, self._capture)       # in case the first frame never comes (window covered)

    def _capture(self):
        if self._done or self.picture is not None:
            return
        area = self.page.visibleRegion().boundingRect()
        if area.isEmpty():
            return self.finish()
        self.hider.setEnabled(False)
        self.picture = self.page.grab(area)
        self.hider.setEnabled(True)
        self.origin = QPointF(area.topLeft())
        self.animation.start()
        self.update()

    def _step(self, value):
        self.value = float(value)
        self.update()

    def paintEvent(self, event):
        if self.picture is None:
            if not self._scheduled:
                self._scheduled = True
                QTimer.singleShot(0, self._capture)
            return
        painter = QPainter(self)
        painter.setOpacity(self.start + (1 - self.start) * self.value)
        painter.drawPixmap(self.origin + self.offset * (1 - self.value), self.picture)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Hide):
            self.finish()       # the picture no longer matches the page
        return False

    def finish(self):
        """Hand over to the live page. Safe to call more than once."""
        if self._done:
            return
        self._done = True
        self.animation.stop()
        self.page.removeEventFilter(self)
        if self.page.graphicsEffect() is self.hider:
            self.page.setGraphicsEffect(None)
        self.hide()
        self.deleteLater()
        self.finished.emit()


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
        later(delay, lambda: animation.start() if self._rolling is animation else None)

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
    later(delay, animation.start)


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
            later(self.delay, self._begin)

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
