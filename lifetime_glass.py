"""One glass material for both editions: the style switch (Glass or Classic), the painters, and the Glass
painter filter. Shared by Lifetime Server and the Lifetime desktop app; no network or filesystem access."""
from PyQt6.QtCore import Qt, QEvent, QObject, QRectF
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

from lifetime_theme import SOFT_RADIUS, T, active_theme


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

    def __init__(self, widget, radius=SOFT_RADIUS, pill=False, strong=False, accent=None, glass_only=False, owner=None):
        super().__init__(owner or widget)       # `owner` keeps this Python object alive for widgets Qt created itself
        self.radius, self.pill, self.strong, self.accent, self.glass_only = radius, pill, strong, accent, glass_only
        widget.setProperty('glass', True)
        widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        widget.installEventFilter(self)

    def eventFilter(self, widget, event):
        if self.glass_only and _style != 'glass':       # the desktop's Classic paints nothing extra
            return False
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


def glass(widget, radius=SOFT_RADIUS, pill=False, strong=False, accent=None, glass_only=False):
    Glass(widget, radius, pill, strong, accent, glass_only)
    return widget


def solid(color):
    """A surface fill for a widget that sits on a panel: the colour in Classic, see-through in Glass."""
    return 'transparent' if _style == 'glass' else color


def paint_track(painter, rect, radius):
    """A recessed channel (segmented controls) that the glass lens slides in: darker than what is around it."""
    track = QPainterPath()
    track.addRoundedRect(rect, radius, radius)
    dark = active_theme() == 'dark'
    painter.fillPath(track, QColor(0, 0, 0, 58) if dark else QColor(20, 33, 61, 24))
    painter.setPen(QPen(QColor(255, 255, 255, 16) if dark else QColor(255, 255, 255, 150), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def window_gradient(t):
    """The blue-to-gold depth behind everything (dark), or the pale sky to cream (light); one tint per theme token."""
    return (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t['sky']}, stop:0.28 {t['sky_mid']}, "
            f"stop:0.55 {t['bg']}, stop:0.8 {t['ember_mid']}, stop:1 {t['ember']})")


def desktop_sheet(t):
    """What the desktop's Glass adds to its window stylesheet. Empty in Classic, so Classic is exactly what it was."""
    if _style != 'glass':
        return ''
    return f"""
        Window, QDialog {{ background: {window_gradient(t)}; }}
        QScrollArea, QStackedWidget, QWidget#page, QWidget#connectPage {{ background: transparent; border: none; }}
        QFrame#signalCard, QFrame#insetPanel, QLabel#summary {{ background: transparent; border: none; }}
        QPushButton[glass="true"] {{ background: transparent; border: 1px solid transparent; }}
        QPushButton[glass="true"]:hover {{ border-color: transparent; }}
        QPushButton[glass="true"]:focus {{ border-color: {t['accent']}; }}
        QPushButton[glass="true"]:checked, QPushButton[glass="true"]:pressed, QPushButton[glass="true"]:disabled {{ background: transparent; }}
        QPushButton[glass="true"]:disabled {{ border-color: transparent; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t['bg2']}, stop:1 {t['card']}); border-color: {t['tile_border']}; }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover {{ border-color: {t['bar']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{ border-color: {t['accent']}; }}
    """
