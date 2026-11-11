"""Shared native surfaces, controls and popup treatment for both editions."""
import sys
import weakref
THEMES = {
    'dark': dict(bg='#0B1426', card='#12203A', text='#FFFFFF',
                 muted='#9FB0CC', border='#22345A', accent='#FFC93C', accent_hover='#FFD761', track='#1B2A46',
                 bar='#3A5688', thumb='#2A3E66', line='#34445E', sidebar='#08101F', danger='#FF8A8A', bg2='#0F1A30', raise_='#243C63', skel_a='#263A60', skel_b='#34507F', tile='#182944', tile_hover='#1E3254', tile_border='#2A3E66', border2='#223959', good='#4CC38A', tab_on='#FFF0BF', sky='#2353A0', sky_mid='#15306A', ember_mid='#3A3220', ember='#8A6516', heat=('#3B3A32', '#6C5E35', '#9D8237', '#CEAA3A', '#FFC93C')),
    # Light mode: the same layout in a calm paper palette. The amber is darkened so it stays readable as text and lines.
    'light': dict(bg='#F3F4F1', card='#FCFCFA', text='#252D2D',
                  muted='#5F6B69', border='#D3D9D5', accent='#9A5B00', accent_hover='#7F4A00', track='#E1E6E1',
                  bar='#B8C4D6', thumb='#FDFDFC', line='#C5CDC8', sidebar='#E8EBE6', danger='#B3261E', bg2='#ECEEEA', raise_='#DDE3EB', skel_a='#E4E7E2', skel_b='#D5DAD3', tile='#F7F8F5', tile_hover='#EEF0EB', tile_border='#CBD2CC', border2='#D3D9D5', good='#2E8B57', tab_on='#6B3F00', sky='#BFD3F2', sky_mid='#DDE6F3', ember_mid='#F2EBDA', ember='#F1D58C', heat=('#E9E5D6', '#E0CE98', '#D2B25A', '#B98A1F', '#9A5B00')),
}

_active = 'dark'


def set_theme(name):
    """Choose the palette every shared surface and control uses ('dark' or 'light')."""
    global _active
    _active = name if name in THEMES else 'dark'
    return _active


def active_theme():
    return _active


def tokens():
    return THEMES[_active]


class Tokens:
    """Dict-like view of the ACTIVE palette, for modules that used to read THEMES['dark'] once at import time."""
    def __getitem__(self, key):
        return THEMES[_active][key]

    def get(self, key, default=None):
        return THEMES[_active].get(key, default)

    def __iter__(self):
        return iter(THEMES[_active])

    def keys(self):
        return THEMES[_active].keys()

    def items(self):
        return THEMES[_active].items()


# The dark palette's literal colours, by token name. Anything still written as one of these hex values in a
# stylesheet, HTML snippet or QColor call is re-tinted to the ACTIVE palette (identity in dark mode).
_DARK_LITERALS = {
    '#0B1426': 'bg', '#0B1220': 'bg', '#12203A': 'card', '#FFFFFF': 'text', '#9FB0CC': 'muted', '#22345A': 'border',
    '#FFC93C': 'accent', '#FFD761': 'accent_hover', '#1B2A46': 'track', '#3A5688': 'bar', '#34445E': 'line',
    '#08101F': 'sidebar', '#4CC38A': 'good', '#65D782': 'good', '#FF8A8A': 'danger', '#0F1A30': 'bg2',
    '#243C63': 'raise_', '#263A60': 'skel_a', '#34507F': 'skel_b', '#182944': 'tile', '#1E3254': 'tile_hover',
    '#2A3E66': 'tile_border', '#223959': 'border2', '#FFF0BF': 'tab_on', '#2353A0': 'sky', '#15306A': 'sky_mid', '#3A3220': 'ember_mid', '#8A6516': 'ember',
}


def T(text):
    """Re-tint dark-palette hex colours inside `text` (a colour or a stylesheet/HTML string) to the active theme."""
    if _active == 'dark' or not isinstance(text, str):
        return text
    import re
    palette = THEMES[_active]
    return re.sub(r'#[0-9A-Fa-f]{6}', lambda m: palette.get(_DARK_LITERALS.get(m.group(0).upper()), m.group(0)), text)


def resolve_theme(preference):
    """'light' / 'dark' as chosen; 'auto' follows the operating system (dark when it cannot be detected)."""
    if preference in ('light', 'dark'):
        return preference
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        if hints is not None and hasattr(hints, 'colorScheme'):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return 'light'
            if scheme == Qt.ColorScheme.Dark:
                return 'dark'
        from PyQt6.QtGui import QPalette
        window = QGuiApplication.palette().color(QPalette.ColorRole.Window)
        return 'light' if window.lightness() > 140 else 'dark'
    except Exception:
        return 'dark'


CONTROL_RADIUS = 8
PANEL_RADIUS = 10
SOFT_RADIUS = 16    # Lifetime Server's larger surfaces
# Circular radio indicators and thin meter caps are geometric marks, not boxes.
RADIO_RADIUS = 11
METER_RADIUS = 2
# Every QCheckBox is drawn as a sliding switch. The cell leaves 2 px around the 44x26 track for the focus ring.
SWITCH_W, SWITCH_H = 48, 30
SWITCH_MS = 180


def motion_off():
    """The application override, or Windows' own "Animation effects" setting, turns the slide into a jump."""
    import os
    gui = sys.modules.get('lifetime_gui')
    if (os.environ.get('LIFETIME_REDUCE_MOTION') == '1' or os.environ.get('REDUCE_MOTION') == '1'
            or bool(getattr(gui, 'REDUCE_MOTION', False))):
        return True
    if sys.platform == 'win32':
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return not enabled.value
        except Exception:
            pass
    return False


def controls():
    """The complete control sheet. No platform-native subcontrol may paint an edge."""
    c = tokens()
    sheet = f"""
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit,
        QTextEdit, QPlainTextEdit, QAbstractItemView {{
            background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']};
            border-radius: {CONTROL_RADIUS}px; padding: 8px; selection-background-color: {c['track']};
            selection-color: {c['text']};
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{ min-height: 22px; }}
        QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{ padding-right: 32px; }}
        QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{ background: transparent; border: none; border-radius: 0; padding: 0; min-height: 0; }}
        QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {c['muted']}; }}
        QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QAbstractItemView:focus {{ border-color: {c['accent']}; }}
        QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: {c['muted']}; background: {c['track']}; }}
        QPushButton, QToolButton {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']};
            border-radius: {CONTROL_RADIUS}px; padding: 8px 12px; min-height: 22px; }}
        QPushButton:hover, QPushButton:focus, QToolButton:hover, QToolButton:focus {{ border-color: {c['accent']}; }}
        QPushButton:pressed, QToolButton:pressed, QPushButton:checked {{ background: {c['track']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['track']}; border-color: {c['border']}; }}
        QPushButton#primary, QPushButton#primaryAction {{ background: {c['accent']}; color: {c['bg']}; border-color: {c['accent']}; font-weight: 600; }}
        QPushButton#primary:hover, QPushButton#primaryAction:hover {{ background: {c['accent_hover']}; }}
        QPushButton#primary:disabled, QPushButton#primaryAction:disabled {{ background: {c['track']}; color: {c['muted']}; border-color: {c['border']}; }}
        QCheckBox {{ spacing: 10px; min-height: 32px; }}
        QCheckBox:disabled {{ color: {c['muted']}; }}
        QCheckBox::indicator {{ width: {SWITCH_W}px; height: {SWITCH_H}px; border: none; background: transparent; }}
        QAbstractScrollArea::corner {{ background: transparent; border: none; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; border: none; width: 16px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; border: none; height: 16px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: {CONTROL_RADIUS}px; min-height: 32px; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: {CONTROL_RADIUS}px; min-width: 32px; }}
        QScrollBar::handle:hover, QScrollBar::handle:pressed {{ background: {c['muted']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ border: none; background: transparent; height: 0; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ border: none; background: transparent; width: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; border: none; }}
        QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{
            image: none; border: none; background: transparent; width: 0; height: 0; }}
        QHeaderView {{ background: transparent; border: none; border-radius: 0; padding: 0; }}
        QHeaderView::section {{ background: {c['card']}; color: {c['muted']}; padding: 8px 18px 8px 8px; border: none; }}
        QAbstractItemView {{ alternate-background-color: {c['card']}; gridline-color: {c['border']}; outline: none; }}
        QAbstractItemView::item {{ padding: 6px; }}
        QAbstractItemView::item:selected {{ background: {c['track']}; color: {c['text']}; }}
        QTableWidget[lifetimeBorderless="true"] {{ background: transparent; border: none; border-radius: 0; padding: 8px; }}
        QTableWidget[lifetimeCards="true"] {{ background: transparent; border: 1px solid {c['border']}; border-radius: {CONTROL_RADIUS}px; padding: 8px; }}
        QComboBox QAbstractItemView {{ background: transparent; border: none; padding: 8px; outline: none; }}
        QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 6px 12px; border-radius: {CONTROL_RADIUS}px; }}
        QWidget[lifetimePopup="true"], QMenu {{ background: {c['card']}; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: {PANEL_RADIUS}px; padding: 8px; }}
        QMenu::item {{ padding: 8px 24px; border: none; border-radius: {CONTROL_RADIUS}px; background: transparent; }}
        QMenu::item:selected {{ background: {c['track']}; }}
        QMenu::item:disabled {{ color: {c['muted']}; }}
        QMenu::separator {{ height: 1px; background: {c['border']}; margin: 6px 12px; }}
        QToolTip {{ color: {c['text']}; background: {c['card']}; border: 1px solid {c['border']}; border-radius: 0; padding: 5px; }}
        QCalendarWidget {{ background: {c['card']}; color: {c['text']}; border: none; }}
        QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: transparent; }}
        QCalendarWidget QAbstractItemView {{ background: {c['card']}; border: none; border-radius: {CONTROL_RADIUS}px;
            padding: 8px; color: {c['text']}; selection-background-color: {c['track']}; selection-color: {c['accent']}; }}
        QCalendarWidget QToolButton {{ background: transparent; padding: 4px 8px; min-height: 24px; }}
        QCalendarWidget QToolButton#qt_calendar_monthbutton {{ padding-right: 24px; }}
        QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; height: 0; }}
        QCalendarWidget QSpinBox {{ padding: 4px 32px 4px 8px; min-height: 24px; }}
        QCalendarWidget QAbstractItemView:disabled {{ color: {c['muted']}; }}
        QProgressBar {{ background: {c['track']}; border: none; border-radius: {METER_RADIUS}px; min-height: 4px; max-height: 4px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: {METER_RADIUS}px; }}
    """
    for kind in ('QComboBox', 'QDateEdit', 'QDateTimeEdit'):
        sheet += f"""
            {kind}::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
                position: absolute; top: 0; right: 0; bottom: 0; width: 28px; border: none; background: transparent; }}
            {kind}::drop-down:hover, {kind}::drop-down:focus {{ background: transparent; border: none; }}
        """
    for kind in ('QSpinBox', 'QDoubleSpinBox', 'QDateEdit', 'QDateTimeEdit'):
        for direction, position in (('up', 'top'), ('down', 'bottom')):
            sheet += f"""
                {kind}::{direction}-button {{ subcontrol-origin: padding; subcontrol-position: {position} right;
                    position: absolute; {position}: 1px; right: 2px; width: 26px; height: 17px;
                    border: none; background: transparent; }}
                {kind}::{direction}-button:hover, {kind}::{direction}-button:focus {{ border: none; background: transparent; }}
            """
    arrows = [(kind, direction, 'bottom' if direction == 'up' else 'top')
              for kind in ('QSpinBox', 'QDoubleSpinBox', 'QDateEdit', 'QDateTimeEdit', 'QHeaderView') for direction in ('up', 'down')]
    arrows.append(('QComboBox', 'down', 'top'))
    for kind, direction, edge in arrows:
        sheet += f"""
            {kind}::{direction}-arrow {{ image: none; width: 0; height: 0; border: none; background: transparent;
                border-left: 4px solid transparent; border-right: 4px solid transparent; border-{edge}: 5px solid {c['muted']}; }}
            {kind}::{direction}-arrow:hover, {kind}::{direction}-arrow:focus {{ border-{edge}-color: {c['accent']}; }}
            {kind}::{direction}-arrow:disabled {{ border-{edge}-color: {c['border']}; }}
        """
        # Qt QSS cannot join zero-size borders diagonally. The shared overlay
        # paints the specified triangle with the same dimensions and ink.
        sheet += f"""
            {kind}[lifetimePaintedArrows="true"]::{direction}-arrow {{ border-{edge}-color: transparent; }}
        """
    return sheet


def stylesheet():
    c = tokens()
    family = 'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'
    return f"""
        QWidget {{ background: transparent; color: {c['text']}; font-family: '{family}'; font-size: 15px; }}
        QMainWindow, QDialog, Window, QWidget#Window {{ background: {c['bg']}; }}
        QFrame, QStackedWidget, QWidget#qt_scrollarea_viewport {{ background: transparent; }}
        QWidget#panel, QFrame#signalCard, QFrame#insetPanel {{ background: {c['card']};
            border: 1px solid {c['border']}; border-radius: {PANEL_RADIUS}px; }}
        QScrollArea#detailScroll {{ background: {c['card']}; border: 1px solid {c['border']};
            border-radius: {PANEL_RADIUS}px; padding: 10px; }}
        QWidget#panel[lifetimeScrollContent="true"] {{ background: transparent; border: none; }}
        QLabel#summary {{ background: {c['card']}; padding: 20px; border-radius: {PANEL_RADIUS}px; }}
        QLabel#groupBadge {{ background: {c['border']}; color: {c['muted']}; padding: 3px 8px; border-radius: {CONTROL_RADIUS}px; }}
        QTabBar {{ background: transparent; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; padding: 8px 18px; }}
        QTabBar::tab:selected {{ color: {c['text']}; }}
        QTabBar::tab:focus {{ color: {c['accent']}; }}
        QGroupBox {{ border: 1px solid {c['border']}; border-radius: {PANEL_RADIUS}px; margin-top: 16px; padding: 16px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; }}
    """ + controls()


def server_surfaces():
    return f"QWidget#panel, QScrollArea#detailScroll {{ border-radius: {SOFT_RADIUS}px; }}"


def configure_high_dpi():
    """Keep physical screen scale intact, identically in the two entry points."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def install():
    """Install once, including parentless tray menus and message boxes.

    Style the actual popup window before native creation; leave tooltips square.
    Insets keep child viewports and selected rows inside the rounded surface.
    """
    from PyQt6.QtCore import QObject, QEvent, Qt, QPointF, QRectF, QVariantAnimation, QEasingCurve
    from PyQt6.QtGui import QPainter, QColor, QPolygonF, QPen
    from PyQt6.QtWidgets import (QApplication, QWidget, QMenu, QAbstractScrollArea, QCheckBox,
                                QComboBox, QAbstractSpinBox, QDateTimeEdit, QHeaderView, QToolButton,
                                QStyle, QStyleOptionComboBox, QStyleOptionSpinBox, QStyleOptionButton)
    app = QApplication.instance()
    if app is None or hasattr(app, '_lifetime_surfaces'):
        return

    class Arrows(QWidget):
        """Qt QSS draws zero-size CSS border triangles as flat bars on some
        styles. Paint the same geometry over the transparent subcontrol, without
        an image dependency or a native edge. Input still goes to Qt's control.
        """
        def __init__(self, control):
            super().__init__(control)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        def paintEvent(self, event):
            control = self.parentWidget()
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            color = tokens()['accent' if control.hasFocus() or control.underMouse() else 'muted']
            if not control.isEnabled():
                color = tokens()['border']
            painter.setBrush(QColor(color))
            if isinstance(control, QHeaderView):
                if not control.isSortIndicatorShown():
                    return
                section = control.sortIndicatorSection()
                x = control.sectionViewportPosition(section) + control.sectionSize(section) - 12
                y = control.height() / 2
                sign = -1 if control.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else 1
                painter.drawPolygon(QPolygonF([QPointF(x-4, y-sign*2), QPointF(x+4, y-sign*2), QPointF(x, y+sign*3)]))
                return
            if control.objectName() == 'qt_calendar_monthbutton':
                x, y = control.width()-12, control.height()/2
                painter.drawPolygon(QPolygonF([QPointF(x-4,y-2),QPointF(x+4,y-2),QPointF(x,y+3)]))
                return
            if control.objectName() in ('qt_calendar_prevmonth', 'qt_calendar_nextmonth'):
                x, y = control.width()/2, control.height()/2
                sign = -1 if control.objectName() == 'qt_calendar_prevmonth' else 1
                painter.drawPolygon(QPolygonF([QPointF(x-sign*2, y-4), QPointF(x-sign*2, y+4), QPointF(x+sign*3, y)]))
                return
            if isinstance(control, QComboBox):
                option = QStyleOptionComboBox()
                control.initStyleOption(option)
                rects = [(control.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                          QStyle.SubControl.SC_ComboBoxArrow, control), 'down')]
            else:
                if control.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons:
                    return
                option = QStyleOptionSpinBox()
                control.initStyleOption(option)
                if isinstance(control, QDateTimeEdit) and control.calendarPopup():
                    rects = [(control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                              QStyle.SubControl.SC_SpinBoxUp, control).united(
                              control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                              QStyle.SubControl.SC_SpinBoxDown, control)), 'down')]
                else:
                    rects = [(control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, part, control), direction)
                             for part, direction in ((QStyle.SubControl.SC_SpinBoxUp, 'up'), (QStyle.SubControl.SC_SpinBoxDown, 'down'))]
            for rect, direction in rects:
                x, y = rect.center().x(), rect.center().y()
                sign = 1 if direction == 'down' else -1
                painter.drawPolygon(QPolygonF([QPointF(x-4, y-sign*2), QPointF(x+4, y-sign*2), QPointF(x, y+sign*3)]))

    class SwitchOverlay(QWidget):
        """Draws a QCheckBox as an Apple-style sliding switch over its (transparent) indicator.

        The checkbox keeps all of its own behaviour (click, Space, signals, accessibility); this only paints.
        It follows `isChecked()` itself, so blockSignals()/setChecked() from code also slide or jump correctly.
        """
        def __init__(self, box):
            super().__init__(box)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            self.position = 1.0 if box.isChecked() else 0.0
            self.animation = QVariantAnimation(self)
            self.animation.setDuration(SWITCH_MS)
            self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.animation.valueChanged.connect(self._step)
            box.toggled.connect(self.sync)

        def _step(self, value):
            self.position = float(value)
            self.update()

        def sync(self, *_):
            target = 1.0 if self.parentWidget().isChecked() else 0.0
            if target == self.position and self.animation.state() != QVariantAnimation.State.Running:
                return
            if self.animation.state() == QVariantAnimation.State.Running and self.animation.endValue() == target:
                return
            self.animation.stop()
            if motion_off() or not self.isVisible():
                self._step(target)
                return
            self.animation.setStartValue(self.position)
            self.animation.setEndValue(target)
            self.animation.start()

        def place(self):
            box = self.parentWidget()
            option = QStyleOptionButton()
            option.initFrom(box)
            option.rect = box.rect()
            option.text = box.text()
            rect = box.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, box)
            self.setGeometry(rect)
            self.raise_()

        def paintEvent(self, event):
            box, c = self.parentWidget(), tokens()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not box.isEnabled():
                p.setOpacity(0.45)
            track = QRectF(0, 0, 44, 26)
            track.moveCenter(QRectF(self.rect()).center())
            glass = False
            try:
                from lifetime_glass import active_style
                glass = active_style() == 'glass'
            except ImportError:
                pass
            if glass:
                off = QColor(255, 255, 255, 52) if _active == 'dark' else QColor(20, 40, 80, 62)
            else:
                off = QColor(c['bar'])
            if box.underMouse() and box.isEnabled() and not self.position:
                off = off.lighter(112)
            on = QColor(c['good'])
            a = self.position
            colour = QColor.fromRgbF(*(off.getRgbF()[i] + (on.getRgbF()[i] - off.getRgbF()[i]) * a for i in range(3)),
                                     off.alphaF() + (1 - off.alphaF()) * a)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(track, 13, 13)
            if glass and a < 1:        # a hairline keeps the empty glass track readable
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, int(46 * (1 - a))) if _active == 'dark' else QColor(20, 40, 80, int(60 * (1 - a))), 1))
                p.drawRoundedRect(track.adjusted(.5, .5, -.5, -.5), 12.5, 12.5)
            knob = QRectF(track.left() + 2 + 18 * a, track.top() + 2, 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            for grow, alpha in ((1.5, 18), (.75, 30)):                      # a soft shadow under the knob
                p.setBrush(QColor(0, 0, 0, alpha))
                p.drawEllipse(knob.adjusted(-grow, -grow + 1.5, grow, grow + 1.5))
            p.setBrush(QColor('#FFFFFF'))
            p.drawEllipse(knob)
            if getattr(box, '_lifetime_keyboard', False) and box.hasFocus():
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(c['accent']), 2))
                p.drawRoundedRect(track.adjusted(-1.5, -1.5, 1.5, 1.5), 14.5, 14.5)

    class Surfaces(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            # Qt-created calendar/header controls have temporary Python wrappers.
            # Retain them so their overlay and resize handling survive Polish.
            self.controls = {}

        def eventFilter(self, widget, event):
            if not isinstance(widget, QWidget):
                return False
            if event.type() == QEvent.Type.Paint and widget.property('lifetimePopup') and not isinstance(widget, QMenu):
                painter = QPainter(widget)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor(tokens()['card']))
                painter.setPen(QPen(QColor(tokens()['border']), 1))
                painter.drawRoundedRect(QRectF(widget.rect()).adjusted(.5, .5, -.5, -.5), PANEL_RADIUS, PANEL_RADIUS)
                painter.end()
            if isinstance(widget, QCheckBox):
                kind = event.type()
                overlay = getattr(widget, '_lifetime_switch', None)
                if kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and widget.isEnabled():
                    widget._lifetime_keyboard = True
                    widget.click()                       # Space toggles natively; Enter does too
                    return True
                if kind == QEvent.Type.KeyPress:
                    widget._lifetime_keyboard = True
                if kind == QEvent.Type.FocusIn:
                    widget._lifetime_keyboard = event.reason() not in (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.PopupFocusReason)
                if kind == QEvent.Type.MouseButtonPress:
                    widget._lifetime_keyboard = False
                if overlay is not None:
                    if kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.StyleChange, QEvent.Type.LayoutDirectionChange,
                                QEvent.Type.FontChange, QEvent.Type.LayoutRequest):
                        overlay.place()
                    if kind == QEvent.Type.Show:
                        overlay.position = 1.0 if widget.isChecked() else 0.0
                    if kind in (QEvent.Type.Paint, QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.Enter,
                                QEvent.Type.Leave, QEvent.Type.EnabledChange, QEvent.Type.MouseButtonPress,
                                QEvent.Type.MouseButtonRelease, QEvent.Type.KeyPress):
                        overlay.sync()
                        overlay.update()
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FocusIn,
                                QEvent.Type.FocusOut, QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.EnabledChange):
                arrows = getattr(widget, '_lifetime_arrows', None)
                if arrows is not None:
                    arrows.setGeometry(widget.rect())
                    arrows.raise_()
                    arrows.update()
            if event.type() == QEvent.Type.Polish:
                if isinstance(widget, QCheckBox) and not hasattr(widget, '_lifetime_switch'):
                    widget.setAttribute(Qt.WidgetAttribute.WA_Hover)
                    widget._lifetime_switch = SwitchOverlay(widget)
                    widget._lifetime_switch.place()
                    widget._lifetime_switch.show()
                if isinstance(widget, QAbstractScrollArea):
                    widget.viewport().setAutoFillBackground(False)
                arrow_control = isinstance(widget, (QComboBox, QAbstractSpinBox, QHeaderView)) or (
                    widget.objectName() in ('qt_calendar_prevmonth', 'qt_calendar_nextmonth', 'qt_calendar_monthbutton'))
                if arrow_control and not hasattr(widget, '_lifetime_arrows'):
                    widget.setProperty('lifetimePaintedArrows', True)
                    widget._lifetime_arrows = Arrows(widget)
                    widget._lifetime_arrows.setGeometry(widget.rect())
                    widget._lifetime_arrows.show()
                    key = id(widget)
                    self.controls[key] = widget
                    widget.destroyed.connect(lambda _=None, key=key: self.controls.pop(key, None))
                    if widget.objectName() in ('qt_calendar_prevmonth', 'qt_calendar_nextmonth'):
                        from PyQt6.QtGui import QIcon
                        widget.setIcon(QIcon())
                    if isinstance(widget, QHeaderView):
                        widget.sortIndicatorChanged.connect(lambda *_, w=widget: w._lifetime_arrows.update())
                        widget.sectionResized.connect(lambda *_, w=widget: w._lifetime_arrows.update())
                        widget.sectionMoved.connect(lambda *_, w=widget: w._lifetime_arrows.update())
                if isinstance(widget, QMenu) or widget.windowType() == Qt.WindowType.Popup:
                    if not widget.property('lifetimePopup'):
                        widget.setProperty('lifetimePopup', True)
                        widget.setWindowFlags(widget.windowFlags() | Qt.WindowType.FramelessWindowHint |
                                              Qt.WindowType.NoDropShadowWindowHint)
                        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
                        widget.setAutoFillBackground(False)
                        if not isinstance(widget, QMenu):
                            widget.setContentsMargins(8, 8, 8, 8)
            return False

    app._lifetime_surfaces = Surfaces(app)
    app.installEventFilter(app._lifetime_surfaces)
    app.setStyleSheet(stylesheet())

    def release():
        # The filter keeps Python wrappers of Qt-owned controls; let go of them while Qt can still tell
        # they are alive, or Python's exit tears them down after the C++ objects are gone (a crash on close).
        app.removeEventFilter(app._lifetime_surfaces)
        for widget in list(app._lifetime_surfaces.controls.values()):
            try:
                del widget._lifetime_arrows
            except (AttributeError, RuntimeError):
                pass
        app._lifetime_surfaces.controls.clear()
    app.aboutToQuit.connect(release)


def _canonical(text):
    """Express a stylesheet in dark-palette literals, whichever theme it was written under, so it can be re-tinted later."""
    if _active == 'dark' or not isinstance(text, str):
        return text
    import re
    reverse = {value.upper(): THEMES['dark'][key] for key, value in THEMES[_active].items() if isinstance(value, str)}
    return re.sub(r'#[0-9A-Fa-f]{6}', lambda m: reverse.get(m.group(0).upper(), m.group(0)), text)


_styled = weakref.WeakSet()


def _retint_style_sheets():
    """Every widget stylesheet goes through T() and is remembered, so an open window can change theme in place."""
    try:
        from PyQt6.QtWidgets import QWidget
    except ImportError:
        return
    if getattr(QWidget.setStyleSheet, '_retinted', False):
        return
    original = QWidget.setStyleSheet

    def setStyleSheet(self, text):
        self._lifetime_style = _canonical(text)
        _styled.add(self)
        original(self, T(self._lifetime_style))
    setStyleSheet._retinted = True
    setStyleSheet._original = original
    QWidget.setStyleSheet = setStyleSheet


def retint_all():
    """Re-apply every remembered stylesheet in the active theme and repaint all open windows."""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication, QWidget
    application = QApplication.instance()
    if application is not None:
        application.setStyleSheet(stylesheet())
    original = QWidget.setStyleSheet._original
    for widget in list(_styled):
        if not sip.isdeleted(widget):
            original(widget, T(widget._lifetime_style))
    if application is not None:
        for widget in application.allWidgets():
            widget.update()


_retint_style_sheets()
