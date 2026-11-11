"""Reusable server presentation widgets. No network or filesystem access."""
import math

from PyQt6.QtCore import Qt, QEvent, QObject, QPointF, QPropertyAnimation, QEasingCurve, QRectF, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import (QWidget, QStackedWidget, QTabBar, QLabel, QVBoxLayout,
                            QHBoxLayout, QPushButton, QProgressBar, QStyle, QStyledItemDelegate,
                            QStyleOptionViewItem, QGraphicsOpacityEffect)

from lifetime_server_widgets import reduced_motion, status_dot, more_icon
from lifetime_theme import CONTROL_RADIUS, PANEL_RADIUS, SOFT_RADIUS, T, active_theme
from lifetime_reveal import Intro, paint_roll, phase


STYLES = ('glass', 'classic')
_style = 'glass'


def set_style(name):
    """The one switch between the two surface renderings; unknown names fall back to glass."""
    global _style
    _style = name if name in STYLES else 'glass'


def active_style():
    return _style


def paint_classic(painter, rect, radius, pill=False, base=None, lift=0):
    """The classic surface: a solid rounded card with a hairline border; the menu pill is a plain solid pill."""
    body = QPainterPath()
    body.addRoundedRect(rect, radius, radius)
    painter.fillPath(body, QColor(T('#223959') if pill else (base or T('#12203A'))))
    if lift:
        painter.fillPath(body, QColor(255, 255, 255, int((14 if active_theme() == 'dark' else 40) * lift)))
    if not pill:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(T('#22345A')), 1))
        outer = QPainterPath()
        outer.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.drawPath(outer)


def paint_glass(painter, rect, radius, pill=False, base=None, lift=0, strong=False):
    """The server's one material: translucent body, lit upper cap, faint amber along the lower edge, a hairline
    plus diagonal specular rim ending in amber, and a soft shadow under a raised (pill) piece. `base` tints a
    panel body; `lift` (0..1) brightens it a touch for hover. Two strengths: ordinary panels stay quiet, `strong`
    (selected or primary pieces) gets the brighter cap and rim."""
    if _style == 'classic':
        return paint_classic(painter, rect, radius, pill, base, lift)
    quiet = not (pill or strong)
    dark = active_theme() == 'dark'
    body = QPainterPath()
    body.addRoundedRect(rect, radius, radius)
    top, bottom = rect.topLeft(), rect.bottomLeft()
    if pill:
        for grow, alpha in ((6.0, 9), (3.5, 13), (1.5, 18)):      # a soft shadow under the lens
            shadow = QPainterPath()
            lifted = rect.adjusted(-grow, -grow / 3, grow, grow).translated(0, 2.5)
            shadow.addRoundedRect(lifted, lifted.height() / 2, lifted.height() / 2)
            painter.fillPath(shadow, QColor(0, 0, 0, alpha) if dark else QColor(20, 33, 61, alpha))
        base = QColor(T('#3A5688')) if dark else QColor(255, 255, 255)
        base.setAlpha(125 if dark else 215)
        painter.fillPath(body, base)
        wash = QLinearGradient(top, bottom)
        wash.setColorAt(0.0, QColor(255, 255, 255, 62) if dark else QColor(255, 255, 255, 255))
        wash.setColorAt(1.0, QColor(160, 190, 240, 22) if dark else QColor(228, 235, 246, 210))
        painter.fillPath(body, wash)
    else:
        tint = QColor(base or T('#0F1A30'))
        tint.setAlpha((100 if dark else 80) if base is None else (185 if dark else 210))
        painter.fillPath(body, tint)
        if lift:
            painter.fillPath(body, QColor(255, 255, 255, int((14 if dark else 40) * lift)))
        wash = QLinearGradient(top, bottom)
        wash.setColorAt(0.0, QColor(255, 255, 255, 30) if dark else QColor(255, 255, 255, 170))
        wash.setColorAt(0.5, QColor(255, 255, 255, 7) if dark else QColor(255, 255, 255, 55))
        wash.setColorAt(1.0, QColor(255, 255, 255, 16) if dark else QColor(255, 255, 255, 115))
        painter.fillPath(body, wash)
    # The lit upper half: a curved highlight that fades out before the middle.
    cap = QPainterPath()
    cap_rect = QRectF(rect.left() + 2, rect.top() + 1.5, rect.width() - 4, min(rect.height() * 0.48, 34))
    cap.addRoundedRect(cap_rect, min(radius, cap_rect.height() / 2), min(radius, cap_rect.height() / 2))
    lit = QLinearGradient(cap_rect.topLeft(), cap_rect.bottomLeft())
    lit.setColorAt(0.0, QColor(255, 255, 255, (86 if pill else 22 if quiet else 40) if dark else (255 if pill else 110 if quiet else 170)))
    lit.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(cap.intersected(body), lit)
    # The software's amber glows faintly along the lower edge, so the glass is blue with a warm light under it.
    warm = QColor(T('#FFC93C'))
    warm.setAlpha((56 if pill else 14 if quiet else 24) if dark else (48 if pill else 10 if quiet else 18))
    glow_height = min(rect.height() * 0.42, 26)
    glow_rect = QRectF(rect.left() + 2, rect.bottom() - glow_height, rect.width() - 4, glow_height - 1.5)
    glow_area = QPainterPath()
    glow_area.addRect(glow_rect)
    glow = QLinearGradient(glow_rect.bottomLeft(), glow_rect.topLeft())
    faded = QColor(warm)
    faded.setAlpha(0)
    glow.setColorAt(0.0, warm)
    glow.setColorAt(1.0, faded)
    painter.fillPath(body.intersected(glow_area), glow)
    # Rim: an outer hairline in the border colour, then a diagonal specular edge (bright top-left and bottom-right).
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outer = QPainterPath()
    outer.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setPen(QPen(QColor(20, 33, 61, 58) if (not dark and pill) else QColor(T('#22345A')), 1))
    painter.drawPath(outer)
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bright, weak = (170, 26) if (dark and pill) else (60, 14) if (dark and quiet) else (110, 20) if dark else (255, 120)
    rim.setColorAt(0.0, QColor(255, 255, 255, bright))
    rim.setColorAt(0.4, QColor(255, 255, 255, weak))
    rim.setColorAt(0.6, QColor(255, 255, 255, weak))
    glint = QColor(T('#FFC93C'))                            # amber catches the lower-right edge
    glint.setAlpha(200 if pill else 50 if quiet else 95)
    rim.setColorAt(0.85, QColor(255, 255, 255, weak))
    rim.setColorAt(1.0, glint)
    inner = QPainterPath()
    inner.addRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1, radius - 1)
    painter.setPen(QPen(QBrush(rim), 1))
    painter.drawPath(inner)



class Glass(QObject):
    """Paints the glass under a widget whose own background is transparent, before the widget paints itself, so
    panels, buttons, notices and dialogs share the menu's material without subclassing each one. `strong` is the
    second strength for the selected or primary piece; `accent` adds a short coloured edge on the left (notices)."""
    SKIP = ('primary', 'primaryAction', 'link', 'deviceMenu')

    def __init__(self, widget, radius=SOFT_RADIUS, pill=False, strong=False, accent=None):
        super().__init__(widget)
        self.radius, self.pill, self.strong, self.accent = radius, pill, strong, accent
        widget.setProperty('glass', True)
        widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        widget.installEventFilter(self)

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Paint and widget.objectName() not in self.SKIP:
            painter = QPainter(widget)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            radius = min(self.radius, rect.height() / 2)
            if self.pill:
                lift = 1.0 if widget.underMouse() and widget.isEnabled() else 0.0
                checked = getattr(widget, 'isChecked', lambda: False)()
                if not widget.isEnabled():
                    painter.setOpacity(0.45)
                paint_glass(painter, rect, radius, False, base=T('#243C63' if checked else '#22345A'), lift=lift, strong=checked)
            else:
                paint_glass(painter, rect, radius, False, base=T('#12203A'), strong=self.strong)
            if self.accent:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(T(self.accent)))
                painter.drawRoundedRect(QRectF(rect.left() + 6, rect.top() + 8, 3, rect.height() - 16), 1.5, 1.5)
            painter.end()
        elif event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            widget.update()
        return False


def glass(widget, radius=SOFT_RADIUS, pill=False, strong=False, accent=None):
    Glass(widget, radius, pill, strong, accent)
    return widget


class SegmentedBar(QTabBar):
    """The server's page menu: a pane of glass with a lens-like pill for the current page.

    The pill stretches as it moves. The edge it moves towards goes first and the edge it leaves catches up, so the
    motion has a direction and a little weight. The bar is painted, not styled, so one pass draws both themes:
    a translucent body that lets the surface through, a bright diagonal rim, and a highlight along the top edge."""
    LEAD_MS = 320       # the leading edge arrives first
    TRAIL_MS = 520      # the trailing edge catches up
    SHEEN_MS = 1250     # one light sweep when the window first shows

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setMinimumHeight(56)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.compact = None
        self.set_compact(False)
        self.indicator = QLabel(self)
        self.indicator.setStyleSheet('background:#223959; ')
        self.indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animation = QPropertyAnimation(self.indicator, b'geometry', self)
        self.animation.setDuration(self.LEAD_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda *unused: self.update())
        self.trail = QVariantAnimation(self)
        self.trail.setStartValue(0.0)
        self.trail.setEndValue(1.0)
        self.trail.setDuration(self.TRAIL_MS)
        self.trail.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.trail.valueChanged.connect(lambda *unused: self.update())
        self._trail_side, self._trail_from, self._trail_to = 'l', 0.0, 0.0
        self.sheen = 1.0
        self._sheen_played = False
        self.sheen_animation = QVariantAnimation(self)
        self.sheen_animation.setStartValue(0.0)
        self.sheen_animation.setEndValue(1.0)
        self.sheen_animation.setDuration(self.SHEEN_MS)
        self.sheen_animation.valueChanged.connect(self._set_sheen)
        self.sheen_animation.finished.connect(lambda: self._set_sheen(1.0))
        self.currentChanged.connect(self.move_indicator)

    def tabSizeHint(self, index):
        # Tabs fill the bar's full height, so the labels and the pill sit on its centre line.
        size = super().tabSizeHint(index)
        size.setHeight(max(size.height(), self.minimumHeight()))
        return size

    def set_compact(self, compact):
        """Narrow windows keep the labels and give up padding, so all four pages stay reachable."""
        if compact == self.compact:
            return
        self.compact = compact
        self.setStyleSheet(f'''QTabBar {{background:transparent; }}
            QTabBar::tab {{background:transparent; color:#9FB0CC; border:0; padding:13px {7 if compact else 30}px; font-size:{14 if compact else 15}px; font-weight:600;}}
            QTabBar::tab:hover {{color:#FFFFFF;}}
            QTabBar::tab:selected {{color:#FFF0BF;}}
            QTabBar::tab:focus {{color:#FFC93C;}}''')

    def _set_sheen(self, value):
        self.sheen = float(value)
        self.update()

    def pill_rect(self):
        """The pill as drawn: the leading edge is the animated geometry, the trailing edge lags behind it."""
        rect = QRectF(self.indicator.geometry())
        if self.trail.state() == self.trail.State.Running:
            edge = self._trail_from + (self._trail_to - self._trail_from) * float(self.trail.currentValue())
            if self._trail_side == 'l':
                rect.setLeft(min(edge, rect.right() - 1))
            else:
                rect.setRight(max(edge, rect.left() + 1))
        return rect

    def move_indicator(self, index, animate=True):
        target = self.tabRect(index).adjusted(5, 5, -5, -5)
        drawn = self.pill_rect()
        self.animation.stop()
        self.trail.stop()
        if reduced_motion() or not animate or not self.isVisible():
            self.indicator.setGeometry(target)
        else:
            start = self.indicator.geometry()
            self.animation.setStartValue(start)
            self.animation.setEndValue(target)
            self.animation.start()
            forward = target.left() >= start.left()
            self._trail_side = 'l' if forward else 'r'
            self._trail_from = drawn.left() if forward else drawn.right()
            self._trail_to = float(target.left() if forward else target.right())
            self.trail.start()
        # Paint the animated geometry beneath the native labels in paintEvent.
        self.indicator.hide()
        self.update()

    def _glass(self, painter, rect, pill):
        paint_glass(painter, rect, rect.height() / 2, pill)

    def paintEvent(self, event):
        from PyQt6.QtWidgets import QStyle, QStyleOptionTab, QStylePainter
        painter = QStylePainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        self._glass(painter, bar, pill=False)
        # On load the pill fades up as the light passes, then it is an ordinary pill.
        painter.setOpacity(min(1.0, self.sheen * 2.4))
        self._glass(painter, self.pill_rect(), pill=True)
        painter.setOpacity(1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, option)
        if self.sheen < 1.0 and _style == 'glass':               # one soft sweep of light, left to right
            t = self.sheen
            t = t * t * (3 - 2 * t)
            clip = QPainterPath()
            clip.addRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            width = bar.width() * 0.22
            centre = bar.left() - width + (bar.width() + 2 * width) * t
            band = QLinearGradient(centre - width, bar.top(), centre + width, bar.top() + bar.height() * 0.7)
            light = QColor(255, 232, 170, 84) if active_theme() == 'dark' else QColor(T('#FFC93C'))   # a warm sweep
            if active_theme() != 'dark':
                light.setAlpha(120)
            clear = QColor(light)
            clear.setAlpha(0)
            band.setColorAt(0.0, clear)
            band.setColorAt(0.5, light)
            band.setColorAt(1.0, clear)
            painter.save()
            painter.setClipPath(clip)
            painter.fillRect(bar, band)
            painter.restore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.move_indicator(self.currentIndex(), False)

    def showEvent(self, event):
        super().showEvent(event)
        self.move_indicator(self.currentIndex(), False)
        if not self._sheen_played:
            self._sheen_played = True
            if not reduced_motion():
                self.sheen = 0.0
                self.sheen_animation.start()


class Skeleton(QWidget):
    """Placeholder shapes laid over a widget until its real data arrives."""
    GAP = 10

    def __init__(self, target, shape='rows', count=6, fill='#12203A', inset=0):
        super().__init__(target)
        self.shape, self.count, self.fill, self.inset = shape, count, fill, inset
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName('Loading')
        self.opacity, self.level = 1.0, 0.0
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(1.0)
        self.pulse.setDuration(1400)
        self.pulse.setLoopCount(-1)
        self.pulse.valueChanged.connect(self._pulse)
        self.fade = QVariantAnimation(self)
        self.fade.setStartValue(1.0)
        self.fade.setEndValue(0.0)
        self.fade.setDuration(260)          # long enough to read as the real content arriving beneath the placeholders
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.valueChanged.connect(self._fade)
        self.fade.finished.connect(self.hide)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self._reveal)
        self.hide()
        target.installEventFilter(self)

    def start(self, delay=0):
        """A delay keeps a fast answer from flashing placeholders."""
        if delay:
            self.delay.start(delay)
        else:
            self._reveal()

    def _reveal(self):
        self.fade.stop()
        self.opacity = 1.0
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.update()

    def finish(self):
        self.delay.stop()
        if self.isHidden():
            return
        if reduced_motion() or not self.isVisible():
            self.hide()
        elif self.fade.state() != QVariantAnimation.State.Running:
            self.fade.start()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget():
            self.setGeometry(watched.rect())
        return False

    def showEvent(self, event):
        super().showEvent(event)
        if not reduced_motion():
            self.pulse.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.pulse.stop()

    def _pulse(self, value):
        self.level = 1 - abs(2 * value - 1)
        self.update()

    def _fade(self, value):
        self.opacity = value
        self.update()

    def _bar(self, painter, x, y, width, height):
        painter.drawRoundedRect(QRectF(x, y, max(0, width), height), min(6, height / 2), min(6, height / 2))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        area = QRectF(self.rect()).adjusted(self.inset, self.inset, -self.inset, -self.inset)
        painter.setBrush(QColor(T(self.fill)))
        painter.drawRoundedRect(area, CONTROL_RADIUS, CONTROL_RADIUS)
        low, high = QColor(T('#263A60')), QColor(T('#34507F'))
        tone = QColor(*(round(a + (b - a) * self.level) for a, b in zip(low.getRgb()[:3], high.getRgb()[:3])))
        left, top, width = area.left(), area.top(), area.width()
        if self.shape == 'value':
            height = min(area.height() * .62, 44)
            painter.setBrush(tone)
            self._bar(painter, left, top + (area.height() - height) / 2, min(width, height * 3), height)
        elif self.shape == 'chart':
            painter.setBrush(tone)
            pitch = (width - 48) / 7
            for index, share in enumerate((.5, .72, .4, .85, .6, .35, .68)):
                height = (area.height() - 40) * share
                self._bar(painter, left + 48 + pitch * (index + .25), area.bottom() - 28 - height, pitch * .5, height)
        elif self.shape == 'cards':
            for index in range(self.count):
                y = top + index * (168 + self.GAP)
                if y > area.bottom():
                    break
                painter.setBrush(QColor(T('#182944')))
                painter.drawRoundedRect(QRectF(left, y, width, 168), SOFT_RADIUS, SOFT_RADIUS)
                painter.setBrush(tone)
                self._bar(painter, left + 18, y + 20, min(200, width * .4), 16)
                self._bar(painter, left + width - 108, y + 20, 90, 16)
                self._bar(painter, left + 18, y + 56, min(340, width * .7), 10)
                self._bar(painter, left + 18, y + 92, min(130, width * .3), 10)
                self._bar(painter, left + 170, y + 92, min(130, width * .3), 10)
                self._bar(painter, left + 18, y + 128, min(280, width * .6), 10)
        else:
            step = 44 if self.shape == 'rows' else 38
            painter.setBrush(tone)
            for index in range(self.count):
                y = top + 16 + index * step
                if y + 12 > area.bottom():
                    break
                if self.shape == 'rows':
                    for start, share in ((.02, .24), (.34, .14), (.54, .18), (.78, .16)):
                        self._bar(painter, left + width * start, y, width * share, 12)
                else:
                    self._bar(painter, left + 16, y, width * .3, 12)
                    self._bar(painter, left + width * .62, y, width * .33, 12)


class LoadingBar(QWidget):
    """A hairline under the top band while real work is pending. Fast answers never show it."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self.busy, self.position = False, 0.0
        self.setAccessibleName('Loading')
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(200)
        self.delay.timeout.connect(self._begin)
        self.sweep = QVariantAnimation(self)
        self.sweep.setStartValue(0.0)
        self.sweep.setEndValue(1.0)
        self.sweep.setDuration(1100)
        self.sweep.setLoopCount(-1)
        self.sweep.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.sweep.valueChanged.connect(self._move)

    def set_busy(self, busy):
        if busy and not self.busy and not self.delay.isActive():
            self.delay.start()
        elif not busy:
            self.delay.stop()
            self.sweep.stop()
            self.busy = False
            self.update()

    def _begin(self):
        self.busy = True
        if not reduced_motion():
            self.sweep.start()
        self.update()

    def _move(self, value):
        self.position = value
        self.update()

    def paintEvent(self, event):
        if not self.busy:
            return
        painter = QPainter(self)
        if reduced_motion():
            painter.fillRect(self.rect(), QColor(T('#FFC93C')))
            return
        span = self.width() * .24
        painter.fillRect(QRectF(-span + (self.width() + span) * self.position, 0, span, 2), QColor(T('#FFC93C')))


class StatusNotice(QLabel):
    """Shows its strip only for a message worth reading; the loading bar covers routine work."""
    def __init__(self, is_routine):
        super().__init__()
        self.is_routine = is_routine

    def setText(self, value):
        super().setText(value)
        if self.parentWidget() is not None:
            self.parentWidget().setVisible(not self.is_routine(value))


class TileIcon(QWidget):
    """A shell symbol at tile size, so both editions share one icon family."""
    def __init__(self, kind, delay=0):
        super().__init__()
        self.kind = kind
        self.setFixedSize(32, 32)
        self.intro = Intro(self, 700, delay, reduced_motion)    # the strokes draw themselves, tile by tile

    def paintEvent(self, event):
        from lifetime_shell import symbol
        drawn = phase(self.intro.value, 0.0, 1.0) if not self.intro.done else 1.0
        if drawn < 0.02:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(1.6, 1.6)
        pen = QPen(QColor(T('#FFFFFF')), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if drawn < 1.0:
            # Every stroke grows from its own start: a dash that lengthens while the gap stays longer than the shape.
            pen.setDashPattern([drawn * 18, 60])
        painter.setPen(pen)
        symbol(painter, self.kind)


class FleetGauge(QWidget):
    """Half ring of ticks: the share of enrolled computers online right now."""
    TICKS = 31

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(176)
        self.value, self.connected = 0.0, 0
        self.intro = Intro(self, 1200, 0, reduced_motion)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(520)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self._move)

    def set_counts(self, connected, total, description):
        self.connected = connected
        self.setAccessibleName(description)
        target = connected / total if total else 0.0
        self.motion.stop()
        first = not self.intro._armed
        if reduced_motion() or not self.isVisible() or first:
            self.value = target         # the first reading is revealed by the entrance, not by a second animation
            self.update()
            self.intro.ready()
        else:
            self.motion.setStartValue(self.value)
            self.motion.setEndValue(target)
            self.motion.start()

    def _move(self, value):
        self.value = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle, base = self.width() / 2, self.height() - 16.0
        outer = min(middle - 10, base - 8)
        inner = outer - 22
        entering = not self.intro.done
        v = self.intro.value if entering else 1.0
        sweep = phase(v, 0.18, 0.88) if entering else 1.0
        lit = round(self.value * sweep * self.TICKS)
        for index in range(self.TICKS):
            along = index / (self.TICKS - 1)
            angle = math.pi * (1 - along)
            # The empty track settles in from the left first; the lit ticks follow it round.
            arrive = phase(v, along * 0.30, along * 0.30 + 0.28) if entering else 1.0
            if index < lit:
                head = entering and index >= lit - 2 and sweep < 1.0     # the leading ticks are brighter while it travels
                color = QColor(T('#FFD761') if head else T('#FFC93C'))
            else:
                color = QColor(T(T('#22345A')))
                color.setAlphaF(arrive)
            pen = QPen(color, 6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(middle + inner * math.cos(angle), base - inner * math.sin(angle)),
                             QPointF(middle + outer * math.cos(angle), base - outer * math.sin(angle)))
        font = painter.font()
        font.setPixelSize(52)
        font.setWeight(QFont.Weight.Light)
        painter.setFont(font)
        painter.setPen(QColor(T('#FFFFFF')))
        roll = min(1.0, max(0.0, (v - 0.2) / 0.7)) if entering else 1.0          # the digits roll as the sweep travels
        paint_roll(painter, QRectF(0, base - 70, self.width(), 78), str(self.connected), roll, Qt.AlignmentFlag.AlignHCenter)


class RowDelegate(QStyledItemDelegate):
    """Selection and hover read as one rounded row instead of square cells."""
    def __init__(self, table):
        super().__init__(table)
        self.table, self.hover = table, -1
        table.setMouseTracking(True)
        table.entered.connect(self._entered)
        table.viewport().installEventFilter(self)

    def _entered(self, index):
        if index.row() != self.hover:
            self.hover = index.row()
            self.table.viewport().update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Leave:
            self.hover = -1
            watched.update()
        return False

    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        # A selected row already shows where the keyboard is; a focused but unselected row needs the outline.
        focused = not selected and self.table.hasFocus() and index.row() == self.table.currentRow()
        if selected or focused or index.row() == self.hover:
            header = self.table.horizontalHeader()
            visible = [c for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c)]
            order = sorted(visible, key=header.visualIndex)
            rect = QRectF(option.rect).adjusted(0, 2, 0, -2)
            # Square inner ends are pushed past the clip, so only the row's outer corners round.
            if index.column() != order[0]:
                rect.setLeft(rect.left() - CONTROL_RADIUS - 1)
            if index.column() != order[-1]:
                rect.setRight(rect.right() + CONTROL_RADIUS + 1)
            painter.save()
            painter.setClipRect(option.rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(T('#FFC93C')), 1) if focused else Qt.PenStyle.NoPen)
            # Rows are lit from the glass itself: a faint amber wash when selected, a faint white one on hover.
            dark = active_theme() == 'dark'
            wash = QColor(T('#FFC93C'))
            wash.setAlpha(30 if dark else 34)
            painter.setBrush(wash if selected else QColor(255, 255, 255, 12) if dark else QColor(20, 33, 61, 12))
            painter.drawRoundedRect(rect, CONTROL_RADIUS, CONTROL_RADIUS)
            painter.restore()
        plain = QStyleOptionViewItem(option)
        plain.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        super().paint(painter, plain, index)


class ServerPages(QStackedWidget):
    """Stack with a separate, centered keyboard-accessible navigation bar."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = SegmentedBar()
        self.bar.currentChanged.connect(self.setCurrentIndex)
        self.currentChanged.connect(self.bar.setCurrentIndex)
        self.currentChanged.connect(self._fade_in)
        self.page_fade = QVariantAnimation(self)
        self.page_fade.setStartValue(0.0)
        self.page_fade.setEndValue(1.0)
        self.page_fade.setDuration(240)
        self.page_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fading = None
        self._last_index = 0
        self._direction = 1
        self.page_fade.valueChanged.connect(self._page_opacity)
        self.page_fade.finished.connect(self._page_settled)
        self.bar.animation.valueChanged.connect(lambda _: self.bar.update())
        self.shortcuts = []

    def _fade_in(self, index):
        """The new page settles in from the side the menu pill moved towards: a short fade and a small slide."""
        self.page_fade.stop()
        self._page_settled()
        previous, self._last_index = self._last_index, index
        page = self.widget(index)
        if page is None or reduced_motion() or not self.isVisible():
            return
        self._direction = 1 if index >= previous else -1
        self._fading = page
        page.setGraphicsEffect(QGraphicsOpacityEffect(page))
        self._page_opacity(0.0)
        self.page_fade.start()

    def _page_opacity(self, value):
        page = self._fading
        if page is not None and page.graphicsEffect() is not None:
            page.graphicsEffect().setOpacity(0.15 + 0.85 * value)
            base = self.contentsRect().topLeft()
            page.move(base.x() + round(14 * self._direction * (1 - value)), base.y())

    def _page_settled(self):
        if self._fading is not None:
            self._fading.setGraphicsEffect(None)
            self._fading.move(self.contentsRect().topLeft())
            self._fading = None

    def addTab(self, widget, title):
        index = self.addWidget(widget)
        self.bar.addTab(title)
        shortcut = QShortcut(QKeySequence(f'Ctrl+{index + 1}'), self)
        shortcut.activated.connect(lambda i=index: self.setCurrentIndex(i))
        self.shortcuts.append(shortcut)
        return index

    def setTabText(self, index, title):
        self.bar.setTabText(index, title)
        self.bar.setAccessibleName(' / '.join(self.bar.tabText(i) for i in range(self.count())))

    def tabText(self, index):
        return self.bar.tabText(index)

    def tabBar(self):
        return self.bar


def byte_text(value, missing):
    return missing if value is None else f'{value / 1024**3:,.1f} GB'


ATTENTION = {'offline': 'Offline', 'disk_low': 'Low disk space', 'battery_worn': 'Battery worn',
             'hot': 'High temperature', 'unstable': 'Unstable', 'pending_command': 'Command pending'}


def problems(device):
    codes = list(device.get('attention') or [])
    if device.get('status') == 'pending':
        codes.append('approval')
    if device.get('reporting_status') == 'report-overdue' and 'offline' not in codes:
        codes.append('offline')
    return codes


class DeviceBox(QWidget):
    activated = pyqtSignal(str, bool)
    menu_requested = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device_id = ''
        # A card eases in once, the first time it is on screen with its reading; cards cascade by row.
        self.intro = Intro(self, 320, 0, reduced_motion)
        self._fade = None
        if not reduced_motion():
            self._fade = QGraphicsOpacityEffect(self)
            self._fade.setOpacity(0.0)
            self.setGraphicsEffect(self._fade)
            self.intro.changed.connect(self._intro_step)
        self.setObjectName('deviceBox')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('''QWidget#deviceBox {background:transparent; border:0;}
            QWidget#deviceBox QLabel {background:transparent; border:0;}
            QWidget#deviceBox QProgressBar {background:#22345A; border:0; max-height:6px;}
            QWidget#deviceBox QProgressBar::chunk {background:#FFC93C; }''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 22)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.name, self.status, self.dot = QLabel(), QLabel(), QLabel()
        self.name.setStyleSheet('font-size:18px; font-weight:600;')
        self.name.setWordWrap(True)
        top.addWidget(self.name, 1)
        top.addWidget(self.dot)
        top.addWidget(self.status)
        self.menu_button = QPushButton()
        self.menu_button.setIcon(more_icon())
        self.menu_button.setObjectName('deviceMenu')
        self.menu_button.setMinimumWidth(44)
        self.menu_button.clicked.connect(lambda: self.menu_requested.emit(self.device_id, self.menu_button))
        top.addWidget(self.menu_button)
        layout.addLayout(top)
        self.spec, self.attention, self.bottom = QLabel(), QLabel(), QLabel()
        self.user = QLabel()
        self.user.setStyleSheet('color:#9FB0CC; font-size:13px;')
        layout.addWidget(self.user)
        for label in (self.spec, self.attention, self.bottom):
            label.setWordWrap(True)
            label.setStyleSheet('color:#9FB0CC; font-size:13px;')
        layout.addWidget(self.spec)
        self.attention.setStyleSheet('color:#FFC93C; font-size:13px;')
        layout.addWidget(self.attention)
        meters = QHBoxLayout()
        self.meters = {}
        for key in ('CPU', 'RAM'):
            value, meter = QLabel(), QProgressBar()
            meter.setRange(0, 100)
            meter.setTextVisible(False)
            meter.setFixedWidth(120)
            meters.addWidget(value)
            meters.addWidget(meter)
            self.meters[key] = value, meter
        meters.addStretch()
        layout.addLayout(meters)
        layout.addWidget(self.bottom)
        # Labels must let the whole card receive mouse input.
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_device(self, device, window):
        self.device_id = device['device_id']
        t, missing = window.t, window.t('Not available')
        identity, summary = device.get('identity') or {}, device.get('summary') or {}
        detail = window._cached_device(device)
        paint_key = (repr(device), repr(detail), window.language, window._relative(device.get('last_received_at')))
        if getattr(self, '_paint_key', None) == paint_key:
            return
        self._paint_key = paint_key
        specs, perf = detail.get('specs') or {}, detail.get('performance') or {}
        name = identity.get('display_name') or self.device_id
        user = identity.get('user') or identity.get('username') or device.get('user')
        self.name.setText(name)
        self.user.setText(t('User: {user}', user=str(user) if user else missing))
        self.user.setVisible(bool(user))
        self.spec.setText(t('CPU: {cpu} · RAM: {ram}', cpu=summary.get('cpu_name') or specs.get('cpu_name') or missing,
                            ram=byte_text(summary.get('ram_bytes', specs.get('ram_bytes')), missing)))
        codes = problems(device)
        status = device.get('reporting_status') if device.get('status') == 'approved' else device.get('status')
        self.status.setText(window._fleet_status(status))
        color = T(T('#FF8A8A')) if any(c in codes for c in ('hot', 'disk_low', 'unstable')) else T(T('#FFC93C')) if codes else T(T('#4CC38A')) if status == 'reporting' else T(T('#9FB0CC'))
        status_color = T(T('#4CC38A')) if status == 'reporting' else T(T('#FFC93C')) if status in ('pending', 'report-overdue') else T(T('#9FB0CC'))
        self.dot.setPixmap(status_dot(status_color).pixmap(16, 16))
        self.attention.setText(' · '.join(t('Pending approval' if c == 'approval' else ATTENTION.get(c, 'Needs attention')) for c in codes if c != 'offline'))
        self.attention.setStyleSheet(f'color:{color}; font-size:13px;')
        self.attention.setVisible(any(c != 'offline' for c in codes))
        for key, field in (('CPU', 'cpu_percent'), ('RAM', 'ram_percent')):
            value = perf.get(field)
            if key == 'RAM' and perf.get('ram_used_bytes') is not None and specs.get('ram_bytes'):
                value = perf['ram_used_bytes'] / specs['ram_bytes'] * 100
            label, meter = self.meters[key]
            label.setText(t('{metric}: {value}', metric=t(key), value=missing if value is None else f'{value:.0f}%'))
            self._fill(meter, 0 if value is None else round(value), 0 if key == 'CPU' else 90)
            meter.setVisible(value is not None)
            label.setVisible(value is not None)     # the spec line above already says when nothing is known
            meter.setAccessibleName(label.text())
        disk = summary.get('disk_free_percent')
        disk_text = f'{disk:.0f}%' if disk is not None else byte_text(perf.get('disk_free_bytes'), missing)
        battery = perf.get('battery_percent')
        self.bottom.setText(t('Disk free: {disk} · Battery: {battery} · Last seen: {seen}', disk=disk_text,
                             battery=missing if battery is None else f'{battery:.0f}%', seen=window._relative(device.get('last_received_at'))))
        self.menu_button.setAccessibleName(t('Actions for {name}', name=name))
        self.intro.ready()
        self.setAccessibleName(' · '.join((self.name.text(), self.user.text(), self.status.text(), self.spec.text(), self.attention.text(), self.bottom.text())))
        self.setToolTip(self.accessibleName())

    def _intro_step(self):
        if self._fade is None:
            return
        if self.intro.done:
            if self.graphicsEffect() is self._fade:
                self.setGraphicsEffect(None)
            self._fade = None
        else:
            self._fade.setOpacity(phase(self.intro.value, 0.0, 1.0))

    def _fill(self, meter, value, delay):
        """The first time a meter shows a reading it fills from empty; later live updates just move it."""
        if getattr(meter, '_filled', False) or reduced_motion() or value <= 0 or not self.isVisible():
            meter._filled = meter._filled if getattr(meter, '_filled', False) else bool(value)
            meter.setValue(value)
            return
        meter._filled = True
        meter.setValue(0)
        fill = QVariantAnimation(meter)
        fill.setStartValue(0)
        fill.setEndValue(value)
        fill.setDuration(650)
        fill.setEasingCurve(QEasingCurve.Type.OutCubic)
        fill.valueChanged.connect(lambda v: meter.setValue(int(v)))
        fill.finished.connect(lambda: meter.setValue(value))
        QTimer.singleShot(delay, fill.start)

    def set_selected(self, selected):
        if self.property('selected') != selected:
            self.setProperty('selected', selected)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect().adjusted(1, 1, -1, -11))
        # Lighter than its panel, so each computer reads as its own piece of the same glass.
        under = QPainterPath()
        under.addRoundedRect(box, SOFT_RADIUS, SOFT_RADIUS)
        painter.fillPath(under, QColor(T('#12203A')))      # opaque underneath: the table's own cell text must not show through
        chosen = bool(self.property('selected'))
        paint_glass(painter, box, SOFT_RADIUS, base=T('#182944' if chosen else '#12203A'), strong=chosen,
                    lift=1.0 if self.underMouse() else 0.0)
        if self.hasFocus() or self.property('selected'):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(T('#FFC93C')), 1))
            painter.drawRoundedRect(box, SOFT_RADIUS, SOFT_RADIUS)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.activated.emit(self.device_id, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.device_id, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            event.accept()
        elif event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            boxes = [self.parentWidget().parentWidget().cellWidget(i, 0) for i in range(self.parentWidget().parentWidget().rowCount())]
            index = boxes.index(self)
            boxes[max(0, min(len(boxes) - 1, index + (1 if event.key() == Qt.Key.Key_Down else -1)))].setFocus()
        else:
            super().keyPressEvent(event)
