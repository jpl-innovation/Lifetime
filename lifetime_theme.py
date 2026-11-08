"""Shared native surfaces, controls and popup treatment for both editions."""
import sys
THEMES = {
    'dark': dict(bg='#0B1426', card='#12203A', text='#FFFFFF',
                 muted='#9FB0CC', border='#22345A', accent='#FFC93C', accent_hover='#FFD761', track='#1B2A46'),
    # Compatibility for existing callers; this is not a selectable edition theme.
    'light': dict(bg='#F3F4F1', card='#FCFCFA', text='#252D2D',
                  muted='#626E6C', border='#D8DEDA', accent='#52796D', track='#E1E6E1'),
}


CONTROL_RADIUS = 8
PANEL_RADIUS = 10
SOFT_RADIUS = 16    # Lifetime Server's larger surfaces
# Circular radio indicators and thin meter caps are geometric marks, not boxes.
RADIO_RADIUS = 11
METER_RADIUS = 2


def controls():
    """The complete control sheet. No platform-native subcontrol may paint an edge."""
    c = THEMES['dark']
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
        QCheckBox, QRadioButton {{ spacing: 12px; min-height: 28px; }}
        QCheckBox:focus, QRadioButton:focus {{ color: {c['accent']}; }}
        QCheckBox:disabled, QRadioButton:disabled {{ color: {c['muted']}; }}
        QRadioButton::indicator {{ width: 20px; height: 20px; border: 1px solid {c['muted']};
            border-radius: {RADIO_RADIUS}px; background: {c['card']}; }}
        QRadioButton::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
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
    c = THEMES['dark']
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
        QCheckBox::indicator {{ width: 22px; height: 22px; border: 1px solid {c['muted']}; background: {c['card']}; border-radius: {CONTROL_RADIUS}px; }}
        QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
        QCheckBox::indicator:disabled {{ background: {c['track']}; border-color: {c['border']}; }}
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
    from PyQt6.QtCore import QObject, QEvent, Qt, QPointF, QRectF
    from PyQt6.QtGui import QPainter, QColor, QPolygonF, QPen
    from PyQt6.QtWidgets import (QApplication, QWidget, QMenu, QAbstractScrollArea,
                                QComboBox, QAbstractSpinBox, QDateTimeEdit, QHeaderView, QToolButton,
                                QStyle, QStyleOptionComboBox, QStyleOptionSpinBox)
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
            color = THEMES['dark']['accent' if control.hasFocus() or control.underMouse() else 'muted']
            if not control.isEnabled():
                color = THEMES['dark']['border']
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
                painter.setBrush(QColor(THEMES['dark']['card']))
                painter.setPen(QPen(QColor(THEMES['dark']['border']), 1))
                painter.drawRoundedRect(QRectF(widget.rect()).adjusted(.5, .5, -.5, -.5), PANEL_RADIUS, PANEL_RADIUS)
                painter.end()
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FocusIn,
                                QEvent.Type.FocusOut, QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.EnabledChange):
                arrows = getattr(widget, '_lifetime_arrows', None)
                if arrows is not None:
                    arrows.setGeometry(widget.rect())
                    arrows.raise_()
                    arrows.update()
            if event.type() == QEvent.Type.Polish:
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
