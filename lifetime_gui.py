#!/usr/bin/env python3
"""Palette-aware, compact desktop view of the lifetime ledger."""
import datetime as dt
from lifetime_i18n import tr, language, set_language, on_language_change, date_text, datetime_text, month, weekdays
import json
import os
from pathlib import Path
import subprocess
import sys
import statistics
import time
import platform

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QRectF, QVariantAnimation, QEasingCurve, QPointF, QProcess
from PyQt6.QtGui import QColor, QIcon, QPalette, QPainter, QPen, QFont, QShortcut, QKeySequence, QFontMetrics
from PyQt6.QtWidgets import (QApplication, QAbstractSpinBox, QDoubleSpinBox,
                            QGraphicsOpacityEffect, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                            QProgressBar, QPushButton, QButtonGroup, QToolTip, QScrollArea, QVBoxLayout, QWidget)
from PyQt6.QtWidgets import QSizePolicy
from lifetime_i18n_qt import QLabel, QPushButton, QWidget, QFrame, QDoubleSpinBox
from lifetime_tracker import average_awake, data_dir, read_ledger, set_baseline, repair_daily, awake_clock, MAX_DELTA
from lifetime_i18n import duration
from lifetime_dashboard import ProbeWorker, SystemDashboard, RefreshGlyph
from lifetime_charts import Insights, Plot, clean_daily, date_series
from lifetime_health_gui import HealthPage
from lifetime_glass import STYLES, set_style, active_style, desktop_sheet
from lifetime_shell import install_glass_surfaces, attach_glass_all, ModeSwitch, theme_reveal, Sidebar, PageStack, DisclosureButton, PulseDot, install_tray, shut_down, tray_available, tell_running_instance, configure_tray_login
from lifetime_settings import load_settings, save_settings, update_settings
from lifetime_setup import SetupDialog, SettingsPage, configure_login
from lifetime_reports import coverage, SOURCES, FIRST_SEEN_SOURCES, source_totals
from lifetime_technician import IdentityDialog, CoverageDialog, ExportDialog
from lifetime_connect import ConnectPage
from lifetime_reveal import Intro, paint_roll, phase, progress
from lifetime_tasks import Tasks

from lifetime_theme import THEMES, stylesheet, install, configure_high_dpi, PANEL_RADIUS, tokens, active_theme, set_theme, resolve_theme, retint_all
ENTRANCE_MS = 1700
REDUCE_MOTION = os.environ.get('LIFETIME_REDUCE_MOTION') == '1'


def motion_reduced():
    return REDUCE_MOTION or os.environ.get('LIFETIME_REDUCE_MOTION') == '1'


def clock_problem(detail):
    """The importer refuses logs whose times go backwards (a changed clock). Say that in words, not the internal check."""
    return any(text in detail for text in ('Boot ends before it starts', 'Overlapping boot timestamps', 'SleepTime is later than WakeTime'))


VERIFY_TOLERANCE = 60   # seconds; a larger gap between the log and the awake clock is shown, not hidden as "verified"


def format_gap(seconds):
    """'1 m 56 s', '3 h 05 m' or '40 s' for a short difference."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f'{hours} h {minutes:02d} m'
    return f'{minutes} m {secs:02d} s' if minutes else f'{secs} s'


def animate(owner, name, start, end, callback, milliseconds=150):
    previous = getattr(owner, name, None)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
    if motion_reduced() or start == end:
        callback(end)
        setattr(owner, name, None)
        return
    animation = QVariantAnimation(owner)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setDuration(milliseconds)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.valueChanged.connect(callback)
    setattr(owner, name, animation)
    animation.start()


class RiseEffect(QGraphicsOpacityEffect):
    """Move the rendered card without fighting its layout geometry."""
    offset = 0.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpacity(1.0)

    def boundingRectFor(self, rect):
        return rect.adjusted(0, -2, 0, 10)

    def draw(self, painter):
        painter.save()
        painter.translate(0, self.offset)
        super().draw(painter)
        painter.restore()


class ValueLabel(QLabel):
    opacity = 1.0

    def set_opacity(self, value):
        self.opacity = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.opacity)
        font = QFont(self.font())
        # Keep translated duration units readable before resorting to elision.
        while font.pixelSize() > 15 and QFontMetrics(font).horizontalAdvance(self.text()) > self.contentsRect().width():
            font.setPixelSize(font.pixelSize()-1)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        shown = QFontMetrics(font).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        self.setToolTip(self.text() if shown != self.text() else '')
        roll = progress(getattr(self.parentWidget(), 'entrance', None), eased=False)    # the card's slice of the entrance
        if roll < 1.0:
            paint_roll(painter, QRectF(self.contentsRect()), shown, roll,
                       self.alignment() & (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignRight) or Qt.AlignmentFlag.AlignLeft)
            return
        painter.drawText(self.contentsRect(), self.alignment() | Qt.AlignmentFlag.AlignVCenter, shown)


STATUS_GOOD = '#4CC38A'   # dark-mode green; light mode uses tokens()['good']


class LiveDot(PulseDot):
    """Tracking: green dot with a ripple wave. Paused: grey dot. Label beside it names the state."""

    def __init__(self):
        super().__init__(size=24, reduce_motion=REDUCE_MOTION)

    def tick(self, active):
        self.set_active(active)


def label(text='', name=''):
    w = QLabel(text)
    w.setObjectName(name)
    return w


class HoursInput(QDoubleSpinBox):
    """Inset the editor explicitly for buttonless platform spin-box styles."""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_editor_geometry()

    def update_editor_geometry(self):
        self.lineEdit().setGeometry(self.rect().adjusted(1, 1, -1, -1))


class Backdrop(QWidget):
    def paintEvent(self, event):
        if active_style() == 'glass':
            return      # the window's own depth shows through
        p = QPainter(self)
        t = THEMES[self.window().theme or 'dark']
        p.fillRect(self.rect(), QColor(t['bg']))


class Icon(QWidget):
    """Small, resolution-independent line symbols; no font glyph dependencies."""
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setFixedSize(20, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(THEMES[self.window().theme]['muted']), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self.kind == 'calendar':
            p.drawRoundedRect(QRectF(3, 5, 14, 12), 2, 2)
            p.drawLine(3, 9, 17, 9)
            p.drawLine(7, 3, 7, 6)
            p.drawLine(13, 3, 13, 6)
        elif self.kind == 'power':
            p.drawArc(QRectF(4, 4, 12, 13), 130*16, 280*16)
            p.drawLine(10, 2, 10, 10)
        elif self.kind == 'restart':
            p.drawArc(QRectF(3, 3, 14, 14), 40*16, 290*16)
            p.drawLine(16, 2, 16, 7)
            p.drawLine(12, 7, 16, 7)
        elif self.kind == 'average':
            p.drawArc(QRectF(3, 4, 14, 14), 0, 180*16)
            p.drawLine(10, 11, 14, 7)
            p.drawLine(3, 14, 17, 14)
        elif self.kind == 'longest':
            p.drawLine(5, 3, 5, 18)
            p.drawLine(5, 3, 16, 3)
            p.drawLine(16, 3, 13, 7)
            p.drawLine(13, 7, 16, 11)
            p.drawLine(16, 11, 5, 11)
        else:
            p.drawEllipse(QRectF(3, 3, 14, 14))
            p.drawLine(10, 6, 10, 10)
            p.drawLine(10, 10, 14, 12)


class Hero(QWidget):
    def __init__(self):
        super().__init__()
        self.seconds = 0
        self.target = 0
        self.fraction = 0
        self.entrance = None        # (intro, start, end): the total rolls inside the first Overview entrance
        self.replay = 1.0           # later rolls: the total is the number that matters, so it rolls again each time Overview opens
        self._replay_animation = None
        self.setFixedHeight(144)     # content ends at 140 px; the old 156 left dead space and made Overview scroll by 4 px
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(tr('Total time awake'))

    def roll_again(self):
        if self._replay_animation is not None:
            self._replay_animation.stop()
            self._replay_animation.deleteLater()
            self._replay_animation = None
        if motion_reduced():
            self.replay = 1.0
            self.update()
            return
        self.replay = 0.0
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(1000)
        animation.valueChanged.connect(self._replay_step)
        animation.finished.connect(lambda: self._replay_step(1.0))
        self._replay_animation = animation
        animation.start()

    def _replay_step(self, value):
        self.replay = float(value)
        self.update()

    def position_controls(self):
        if not hasattr(self, 'controls'):
            return
        f = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
        f.setPixelSize(64)
        f.setWeight(QFont.Weight.Light)
        number_width = QFontMetrics(f).horizontalAdvance(f'{self.seconds / 3600:,.1f}')
        controls = self.controls
        controls.adjustSize()
        inline = self.width() >= 400
        controls.move(min(self.width()-controls.width(), number_width+24) if inline else 0, 62 if inline else 148)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_controls()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = THEMES[self.window().theme]
        w, h = self.width(), self.height()
        def text(y, height, value, size, color, weight=QFont.Weight.Normal, x=0, width=None, roll=1.0):
            f = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
            f.setPixelSize(size)
            f.setWeight(weight)
            p.setFont(f)
            p.setPen(QColor(color))
            paint_roll(p, QRectF(x, y, w if width is None else width, height), value, roll, Qt.AlignmentFlag.AlignLeft)
        shown = self.seconds * progress(self.entrance)
        whole_minutes, secs = divmod(int(shown), 60)
        days, rest = divmod(whole_minutes, 1440)
        hours, minutes = divmod(rest, 60)
        # The number is the focal point and aligns with the page title; the old 100-hour ring read as a
        # loading spinner and pushed the number out of line with everything else.
        x = 0
        text(3, 25, tr('Total time awake'), 15, t['muted'], x=x, width=w-x)
        value = f'{self.seconds / 3600:,.1f}'        # the real total: its digits roll into place during the entrance
        size = 64
        f = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
        f.setPixelSize(size)
        available = w-x-8
        if hasattr(self, 'controls') and w >= 400:
            available -= self.controls.width()+24
        while QFontMetrics(f).horizontalAdvance(value) > max(60, available) and size > 18:
            size -= 2
            f.setPixelSize(size)
        text(28, 80, value, size, t['text'], QFont.Weight.Light, x=x, width=w-x, roll=min(progress(self.entrance, eased=False), self.replay))
        text(112, 28, tr('hero.duration', days=days, hours=hours, minutes=minutes, seconds=secs),
             13 if w < 400 else 15, t['muted'], x=0 if w < 400 else x, width=w if w < 400 else w-x)

    def set_total(self, seconds):
        self.target = seconds
        self.setAccessibleDescription(duration(seconds))
        def update_value(value):
            self.seconds = value
            self.position_controls()
            self.update()
        def update_ring(value):
            self.fraction = value
            self.update()
        animate(self, 'number_animation', self.seconds, seconds, update_value)
        animate(self, 'ring_animation', self.fraction, (seconds / 3600 % 100) / 100, update_ring)


class StatCard(QFrame):
    def __init__(self, title, kind, progress=False):
        super().__init__()
        self.setObjectName('statCell')
        self.hover = 0.0
        self.number = 0.0
        self.target = None
        self.formatter = None
        self.entrance = None        # (intro, start, end): the number counts up inside the Overview entrance
        self.effect = RiseEffect(self)
        self.setGraphicsEffect(self.effect)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 12, 16, 12)
        self.box.setSpacing(8)
        self.heading_label = label(title, 'muted')
        self.heading_label.setWordWrap(True)
        self.heading_label.setMinimumWidth(0)
        self.heading_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.live = LiveDot() if kind == 'clock' else None
        if self.live:
            self.live.setParent(self)
            self.live.hide()
        self.value = ValueLabel('')
        self.value.setMinimumWidth(0)
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value.setObjectName('statValue')
        self.caption = label('', 'caption')
        self.caption.setWordWrap(True)
        self.caption.hide()
        self.progress = None
        self.box.addWidget(self.heading_label)
        self.box.addWidget(self.value)
        self.setFixedHeight(104)
        self.divider = False
        self.list_mode = False

    def set_list_mode(self, enabled, divider):
        self.list_mode, self.divider = enabled, divider
        self.box.setDirection(QVBoxLayout.Direction.LeftToRight if enabled else QVBoxLayout.Direction.TopToBottom)
        self.box.setStretch(0, 1)
        self.box.setStretch(1, 1 if enabled else 0)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight if enabled else Qt.AlignmentFlag.AlignLeft)
        self.setFixedHeight(56 if enabled else 104)
        self.update()

    def set_number(self, value, formatter):
        if value == self.target:
            return
        if self.target is not None and formatter(value) != formatter(self.target):
            animate(self, 'value_fade', .65, 1, self.value.set_opacity, 150)
        self.target = value
        self.formatter = formatter
        self.value.setAccessibleName(formatter(value))
        animate(self, 'number_animation', self.number, value, self._show_number)

    def _show_number(self, value):
        self.number = value
        shown = value * progress(self.entrance) if value > 0 else value
        entering = self.entrance is not None and not self.entrance[0].done and self.target is not None
        self.value.setText(self.formatter(self.target if entering else shown))     # the digits roll; the text is the real one
        if self.progress:
            self.progress.setValue(min(2400, max(0, round(shown * 100))))

    def refresh_entrance(self):
        if self.formatter is not None:
            self._show_number(self.number)
            self.value.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = THEMES[self.window().theme]
        p.setPen(QPen(QColor(t['border']), 1))
        if self.divider:
            if self.list_mode:
                p.drawLine(16, self.height()-1, self.width()-16, self.height()-1)
            else:
                p.drawLine(self.width()-1, 16, self.width()-1, self.height()-16)

    def set_hover(self, value):
        self.hover = value
        self.effect.offset = 0 if REDUCE_MOTION else -2 * value
        self.effect.update()
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)


def hours_minutes(seconds):
    return duration(seconds, days=False)


def history_summary(daily, sessions, today):
    active = sorted((dt.date.fromisoformat(k), v) for k, v in daily.items() if v > 0)
    streak = longest = 0
    previous = None
    for day, _ in active:
        streak = streak + 1 if previous and day == previous + dt.timedelta(days=1) else 1
        longest = max(longest, streak)
        previous = day
    def total(days):
        return sum(v for day, v in active if today - dt.timedelta(days=days-1) <= day <= today)
    return dict(week=total(7), month=total(30), busiest=max(active, key=lambda item: item[1], default=None),
                average=sum(v for _, v in active) / len(active) if active else 0,
                streak=longest, sessions=len(sessions),
                median=statistics.median(s['seconds'] for s in sessions) if sessions else 0)


class BarChart(QWidget):
    def __init__(self, hourly=False):
        super().__init__()
        self.hourly = hourly
        self.values = []
        self.keys = []
        self.progress = 1.0
        self.entered = False
        self.setFixedHeight(122 if hourly else 166)
        self.setMouseTracking(True)
        self.setAccessibleName(tr('Awake time by hour of day') if hourly else tr('Daily awake time'))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.entered:
            self.entered = True
            def frame(value):
                self.progress = value
                self.update()
            animate(self, 'entrance', 0, 1, frame, 750)

    def set_data(self, keys, values):
        self.keys, self.values = keys, values
        self.setAccessibleDescription('; '.join(f'{key}: {hours_minutes(value)}' for key, value in zip(keys, values)))
        self.update()

    def plot(self):
        return QRectF(62, 12, max(1, self.width()-70), self.height()-40)

    def paintEvent(self, event):
        if not self.values:
            return
        p = QPainter(self)
        t = THEMES[self.window().theme]
        font = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
        font.setPixelSize(13)
        p.setFont(font)
        rect = self.plot()
        maximum = max(24, max(self.values)/3600) if not self.hourly else max(1, max(self.values)/3600)
        levels = [maximum/4, maximum/2, maximum*3/4, maximum] if not self.hourly else [maximum/2, maximum]
        for level in levels:
            y = rect.bottom() - rect.height()*level/maximum
            p.setPen(QPen(QColor(t['border']), 0.5))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor(t['muted']))
            p.drawText(QRectF(0, y-9, 54, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tr('axis.hours', value=level))
        step = rect.width()/len(self.values)
        width = min(12, step*.55)
        for i, (key, value) in enumerate(zip(self.keys, self.values)):
            x = rect.left() + (i+.5)*step
            begin = 0.5 * i / max(1, len(self.values) - 1)      # bars rise left to right
            height = rect.height() * value/3600/maximum * phase(self.progress, begin, begin + 0.5)
            color = QColor(t['accent'])
            if not self.hourly and key != dt.date.today().isoformat():
                color.setAlphaF(.58)
            p.fillRect(QRectF(x-width/2, rect.bottom()-max(1, height), width, max(1, height)), color if value else QColor(t['track']))
            interval = 6 if self.hourly else 1 if len(self.values) == 7 else 7 if len(self.values) == 30 else 21
            if i % interval == 0:
                text = tr('time.hour', hour=i) if self.hourly else date_text(key, year=False)
                p.setPen(QColor(t['muted']))
                p.drawText(QRectF(x-30, rect.bottom()+5, 60, 22), Qt.AlignmentFlag.AlignCenter, text)

    def mouseMoveEvent(self, event):
        rect = self.plot()
        if self.values and rect.contains(event.position()):
            index = min(len(self.values)-1, int((event.position().x()-rect.left())/rect.width()*len(self.values)))
            key = self.keys[index] if self.hourly else date_text(self.keys[index])
            QToolTip.showText(event.globalPosition().toPoint(), f'{key} · {hours_minutes(self.values[index])}', self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)


def segments(items, callback):
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(3)
    group = QButtonGroup(widget)
    for index, text in enumerate(items):
        button = QPushButton(text)
        button.setObjectName('segment')
        button.setCheckable(True)
        group.addButton(button, index)
        row.addWidget(button)
    group.button(0).setChecked(True)
    group.idClicked.connect(callback)
    widget.group = group
    return widget


# Retain the public History name for existing integrations.
History = Insights


class OverviewTrend(Plot):
    def __init__(self, activate):
        super().__init__('daily')
        self.activate = activate
        self.bar_color = None      # brand yellow, like every other chart
        self.setFixedHeight(160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tr('Last 14 days; open Activity'))
        self.setToolTip(tr('Last 14 days; open Activity'))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activate()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus():
            p = QPainter(self)
            p.setPen(QPen(QColor(tokens()['muted']), 1))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), PANEL_RADIUS, PANEL_RADIUS)


class Window(QWidget):
    tray = None

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else load_settings(data_dir())
        set_style(self.settings.get('style') if self.settings.get('style') in STYLES else 'classic')
        install_glass_surfaces()
        self.last_verification = None
        self.daily_repair_complete = True
        set_language(self.settings.get('language', language()))
        self.theme = None
        self.initialized = False
        self.setWindowTitle(tr('Lifetime · JPL Innovation'))
        self.resize(1280, 800)
        self.setMinimumSize(390, 440)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.status = label('', 'status')
        self.status_dot = LiveDot()
        self.language_control = ModeSwitch([tr('EN'), tr('VI')],
                                           lambda index: self.switch_language(('en', 'vi')[index]), globe=True)
        self.language_control.group.button(('en', 'vi').index(language())).setChecked(True)
        self.language_control.setAccessibleName(tr('Language'))
        self.theme_control = ModeSwitch([tr('Auto'), tr('Light'), tr('Dark')],
                                        lambda index: self.switch_theme(('auto', 'light', 'dark')[index]),
                                        icons=('auto', 'light', 'dark'))
        saved_theme = self.settings.get('theme', 'auto')
        self.theme_control.group.button(('auto', 'light', 'dark').index(saved_theme if saved_theme in ('auto', 'light', 'dark') else 'auto')).setChecked(True)
        self.theme_control.setAccessibleName(tr('Theme'))
        self.sidebar = Sidebar(self.select_page, motion_reduced,
                               self.language_control, self.status, self.status_dot, self.theme_control)
        self.navigation = self.sidebar
        outer.addWidget(self.sidebar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        canvas = Backdrop()
        self.scroll.setWidget(canvas)
        self.centering = QHBoxLayout(canvas)
        self.centering.setContentsMargins(32, 32, 32, 32)
        self.body = QWidget()
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.body.setMaximumWidth(1200)
        self.centering.addWidget(self.body, 1, Qt.AlignmentFlag.AlignTop)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        self.pages = PageStack()
        self.pages.setMinimumWidth(0)
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_layout.addWidget(self.pages)
        self.overview = QWidget()
        layout = QVBoxLayout(self.overview)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.overview_title = label(tr('Overview'), 'pageTitle')
        layout.addWidget(self.overview_title)
        self.hero = Hero()
        self.total_row = QHBoxLayout()
        self.total_row.setSpacing(16)
        self.total_row.addWidget(self.hero)
        self.verify_glyph = RefreshGlyph()
        self.verify_button = QPushButton(tr('Verify'))
        self.verify_button.setMinimumHeight(36)
        self.log_name = tr('Windows event log') if sys.platform == 'win32' else tr('system journal')
        self.verify_button.setAccessibleName(tr('verify.accessible', log=self.log_name))
        self.verify_button.clicked.connect(self.verify)
        self.verify_controls = QWidget()
        verify_row = QHBoxLayout(self.verify_controls)
        verify_row.setContentsMargins(0, 0, 0, 0)
        verify_row.setSpacing(8)
        verify_row.addWidget(self.verify_glyph)
        verify_row.addWidget(self.verify_button)
        self.verify_controls.setParent(self.hero)
        self.hero.controls = self.verify_controls
        self.total_row.addStretch(1)
        layout.addLayout(self.total_row)
        self.verification = label(tr('verify.intro', log=self.log_name), 'caption')
        self.verification.setWordWrap(True)
        self.verification.setMinimumWidth(0)
        self.verification.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.verification)
        self.stats_panel = QFrame()
        self.stats_panel.setObjectName('insetPanel')
        self.grid = QGridLayout(self.stats_panel)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(0)
        self.columns = 0
        self.cards = {}
        # Preserve the public stat keys and provenance mapping.
        for key, title, kind in (
            ('first', 'Earliest known date', 'calendar'), ('boots', 'Boots observed', 'restart'),
            ('current', 'Current session', 'clock'), ('longest', 'Longest session', 'longest'),
            ('average', 'Average awake / day', 'average')):
            self.cards[key] = StatCard(tr(title), kind)
        self.stat_order = ('current', 'longest', 'average', 'boots', 'first')
        layout.addWidget(self.stats_panel)
        self.trend_panel = QFrame()
        self.trend_panel.setObjectName('insetPanel')
        trend_box = QVBoxLayout(self.trend_panel)
        trend_box.setContentsMargins(16, 12, 16, 8)
        trend_box.setSpacing(8)
        trend_header = QHBoxLayout()
        trend_header.addWidget(label(tr('Last 14 days'), 'groupTitle'))
        trend_header.addStretch()
        self.activity_link = QPushButton(tr('View Activity'))
        self.activity_link.setObjectName('quietAction')
        self.activity_link.clicked.connect(lambda: self.select_page(2))
        trend_header.addWidget(self.activity_link)
        trend_box.addLayout(trend_header)
        self.trend = OverviewTrend(lambda: self.select_page(2))
        trend_box.addWidget(self.trend)
        layout.addWidget(self.trend_panel)
        # One entrance for the page, the first time real data is on screen: the total rolls into place, then the
        # 14-day bars rise oldest to newest. Only the number that matters moves; the other figures just appear.
        self.intro = Intro(self.overview, ENTRANCE_MS, 0, motion_reduced)
        self.hero.entrance = (self.intro, 0.0, 0.38)
        self.trend.entrance = (self.intro, 0.36, 1.0)
        self.intro.changed.connect(self.hero.update)
        self.intro.changed.connect(self.trend.update)
        self.counting_toggle = DisclosureButton(tr('How this is counted'))
        self.counting_toggle.setObjectName('disclosure')
        self.counting_toggle.setCheckable(True)
        self.counting_toggle.toggled.connect(self.toggle_counting)
        layout.addWidget(self.counting_toggle)
        self.counting_panel = QWidget()
        counting = QVBoxLayout(self.counting_panel)
        counting.setContentsMargins(16, 0, 16, 8)
        counting.setSpacing(8)
        self.source_breakdown = label('', 'caption')
        self.source_breakdown.setWordWrap(True)
        self.source_breakdown.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.coverage_summary = label('', 'caption')
        self.coverage_summary.setWordWrap(True)
        self.device_name = label('', 'caption')
        self.device_name.setWordWrap(True)
        self.device_name.setTextFormat(Qt.TextFormat.PlainText)
        self.device_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.awake_note = label(tr('Awake time is the time the computer was running and awake. It does not measure human activity, component health or remaining lifespan.'), 'caption')
        self.awake_note.setWordWrap(True)
        note = label(tr('Sleep and hibernation are not counted.'), 'caption')
        note.setWordWrap(True)
        for item in (self.source_breakdown, self.coverage_summary, self.device_name, self.awake_note, note):
            counting.addWidget(item)
        self.import_toggle = QPushButton(tr('+  Import starting hours'))
        self.import_toggle.setObjectName('importToggle')
        self.import_toggle.setCheckable(True)
        self.import_toggle.toggled.connect(self.toggle_import)
        counting.addWidget(self.import_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.import_panel = QWidget()
        form = QVBoxLayout(self.import_panel)
        form.setContentsMargins(0, 0, 0, 8)
        explanation = label(tr('Known hours before tracking. Replaces the existing baseline.'), 'caption')
        explanation.setWordWrap(True)
        form.addWidget(explanation)
        self.baseline_row = QHBoxLayout()
        self.baseline = HoursInput()
        self.baseline.setMinimumWidth(0)
        self.baseline.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.baseline.setFixedHeight(40)
        self.baseline.lineEdit().setTextMargins(12, 0, 12, 0)
        self.baseline.setRange(0, 100_000_000)
        self.baseline.setDecimals(2)
        self.baseline.setSuffix(tr('unit.hour.suffix'))
        self.baseline.setAccessibleName(tr('Starting hours'))
        self.baseline.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.baseline_row.addWidget(self.baseline, 1)
        apply = QPushButton(tr('Set baseline'))
        apply.clicked.connect(self.apply_baseline)
        self.baseline_row.addWidget(apply)
        form.addLayout(self.baseline_row)
        counting.addWidget(self.import_panel)
        self.import_panel.setMaximumHeight(0)
        self.import_panel.hide()
        layout.addWidget(self.counting_panel)
        self.counting_panel.setMaximumHeight(0)
        self.counting_panel.hide()
        self.system = SystemDashboard(self.open_settings)
        self.history = History()
        self.system.layout().setContentsMargins(0, 0, 0, 0)
        self.history.layout().setContentsMargins(0, 0, 0, 0)
        self.history.layout().itemAt(0).layout().itemAt(0).widget().setText(tr('Activity'))
        self.reports = QWidget()
        reports_layout = QVBoxLayout(self.reports)
        reports_layout.setContentsMargins(0, 0, 0, 0)
        reports_layout.setSpacing(16)
        reports_layout.addWidget(label(tr('Reports'), 'pageTitle'))
        intro = label(tr('Reports and details for this computer.'), 'caption')
        intro.setWordWrap(True)
        reports_layout.addWidget(intro)
        self.report_list = QFrame()
        self.report_list.setObjectName('insetPanel')
        reports_list = QVBoxLayout(self.report_list)
        reports_list.setContentsMargins(16, 0, 16, 0)
        reports_list.setSpacing(0)
        self.technician_actions = {}
        self.report_rows = []
        for index, (title, description, dialog) in enumerate((
            ('Export report', 'Save a report of recorded awake time and coverage.', ExportDialog),
            ('Device identity', 'Manage the device name, asset tag and ownership.', IdentityDialog),
            ('Coverage details', 'Review sources, dates and gaps in the record.', CoverageDialog))):
            row = QWidget()
            row.setObjectName('reportRow')
            row_box = QHBoxLayout(row)
            row_box.setContentsMargins(0, 20, 0, 20)
            row_box.setSpacing(16)
            copy = QVBoxLayout()
            copy.setSpacing(4)
            title_label = label(tr(title), 'rowTitle')
            title_label.setWordWrap(True)
            copy.addWidget(title_label)
            description_label = label(tr(description), 'caption')
            description_label.setWordWrap(True)
            description_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            copy.addWidget(description_label)
            row_box.addLayout(copy, 1)
            # The row title already says what it is; the button names the action only.
            button = QPushButton(tr('Export') if index == 0 else tr('Open'))
            button.setMinimumHeight(36)
            button.setObjectName('primaryAction' if index == 0 else 'reportAction')
            button.setAccessibleName(tr(title))
            button.clicked.connect(lambda checked=False, cls=dialog: self.open_technician(cls))
            row_box.addWidget(button)
            reports_list.addWidget(row)
            if index < 2:
                divider = QFrame()
                divider.setObjectName('divider')
                divider.setFixedHeight(1)
                reports_list.addWidget(divider)
            self.technician_actions[title] = button
            self.report_rows.append(row)
        reports_layout.addWidget(self.report_list)
        self.repair_notice = label('', 'caption')
        self.repair_notice.setWordWrap(True)
        self.repair_notice.hide()
        reports_layout.addWidget(self.repair_notice)
        self.health = HealthPage(self.settings, self.open_settings)
        self.connect_page = ConnectPage(directory=data_dir())
        self.settings_page = SettingsPage(self, data_dir())
        for page in (self.overview, self.system, self.history, self.reports, self.health, self.connect_page, self.settings_page):
            page.setMinimumWidth(0)
            page.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            self.pages.addWidget(page)
        self.pages.setCurrentIndex(0)
        # Paused-tracking notice: a calm banner at the top of every page, shown only while the tracker is
        # offline, so the call to action sits where people look first instead of trailing under the content.
        self.tracker_banner = QFrame()
        self.tracker_banner.setObjectName('insetPanel')
        tracker = QHBoxLayout(self.tracker_banner)
        tracker.setContentsMargins(16, 12, 12, 12)
        tracker.setSpacing(16)
        self.hint = label('', 'caption')
        self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tracker.addWidget(self.hint, 1)
        self.start = QPushButton(tr('Start tracker'))
        self.start.setObjectName('startTracker')
        self.start.clicked.connect(self.start_tracker)
        tracker.addWidget(self.start)
        body_layout.insertWidget(0, self.tracker_banner)
        self.tracker_banner.hide()
        # History banner: without it a fresh install only counts time since the last boot (often about an hour), which
        # looks wrong on a computer that has been used for months. One click reads the system log and fills it in.
        self.history_banner = QFrame()
        self.history_banner.setObjectName('insetPanel')
        history_row = QHBoxLayout(self.history_banner)
        history_row.setContentsMargins(16, 12, 12, 12)
        history_row.setSpacing(16)
        self.history_hint = label(tr('history.banner'), 'caption')
        self.history_hint.setWordWrap(True)
        self.history_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        history_row.addWidget(self.history_hint, 1)
        self.rebuild_button = QPushButton(tr('Add earlier usage'))
        self.rebuild_button.clicked.connect(lambda: self.rebuild_history())
        history_row.addWidget(self.rebuild_button)
        body_layout.insertWidget(1, self.history_banner)
        self.history_banner.hide()
        self.shortcuts = []
        for key, page in (*((f'Ctrl+{number}', page) for number, page in enumerate((0, 2, 1, 4, 5, 3), 1)), ('Ctrl+,', 6)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda index=page: self.select_page(index))
            self.shortcuts.append(shortcut)
        self.page_animation = None
        self.active_page = 0
        self.reflow()
        self.apply_theme()
        QApplication.instance().paletteChanged.connect(self.apply_theme)
        hints = QApplication.instance().styleHints()
        if hasattr(hints, 'colorSchemeChanged'):
            hints.colorSchemeChanged.connect(self.system_theme_changed)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.worker = ProbeWorker(self, self.settings)
        self.worker.changed.connect(lambda data: self.system.update_values(data, THEMES[self.theme]))
        self.worker.verified.connect(self.verification_finished)
        self.system.update_values({}, THEMES[self.theme])
        self.apply_visibility()
        on_language_change(self.retranslate_content)

    def start_daily_repair(self, repair=repair_daily):
        self._set_export_ready(False)
        self.repair_notice.setText(tr('Updating history'))
        self.repair_notice.show()
        self.daily_repair_tasks = Tasks(self)
        self.daily_repair_tasks.completed.connect(self._daily_repair_done)
        self.daily_repair_tasks.failed.connect(self._daily_repair_failed)
        self.daily_repair_tasks.submit(repair)

    def _set_export_ready(self, ready):
        self.daily_repair_complete = ready
        self.technician_actions['Export report'].setEnabled(
            ready and self.settings.get('completed', False))

    def _daily_repair_done(self, _number, _result):
        self._set_export_ready(True)
        self.status.setText(tr('History updated'))
        self.repair_notice.hide()
        self.refresh()

    def _daily_repair_failed(self, _number, error):
        self._set_export_ready(False)
        self.status.setText(tr('History update failed'))
        self.repair_notice.setText(tr('Calendar history could not be updated. Restart Lifetime to retry. Report export remains unavailable.'))
        self.repair_notice.show()

    def switch_language(self, value):
        previous = self.settings.get('language')
        self.settings['language'] = value
        try:
            saved = update_settings({'language': value}, data_dir())
            self.settings['identity'] = saved['identity']
        except (OSError, ValueError) as error:
            self.settings['language'] = previous
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
            return
        set_language(value)
        self.worker.set_settings(self.settings)
        self.apply_visibility()
        self.language_control.group.button(('en', 'vi').index(value)).setChecked(True)

    def switch_theme(self, value):
        """Light / dark / follow the system. Changes in place: the new theme spreads out from the control."""
        previous = self.settings.get('theme', 'auto')
        if value == previous:
            return
        try:
            update_settings({'theme': value}, data_dir())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
            self.theme_control.group.button(('auto', 'light', 'dark').index(previous)).setChecked(True)
            return
        self.settings['theme'] = value
        button = self.theme_control.group.button(('auto', 'light', 'dark').index(value))
        self.reveal_theme(resolve_theme(value), button.mapTo(self, button.rect().center()))

    def reveal_theme(self, name, origin):
        if name != active_theme():
            theme_reveal(self, origin, lambda: self.apply_live_theme(name))

    def apply_live_theme(self, name):
        set_theme(name)
        retint_all()
        self.apply_theme()
        self.sidebar.refresh_theme()
        self.system.update_values(self.worker.values, THEMES[self.theme])
        self.refresh()

    def switch_style(self, value, origin=None):
        """Glass or Classic. Saved with the theme and applied in place, spreading out like a theme change does."""
        previous = self.settings.get('style', 'classic')
        if value not in STYLES or value == previous:
            return False
        try:
            update_settings({'style': value}, data_dir())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
            return False
        self.settings['style'] = value
        theme_reveal(self, origin or self.rect().center(), lambda: self.apply_live_style(value))
        return True

    def apply_live_style(self, name):
        set_style(name)
        attach_glass_all()
        for widget in self.findChildren(QObject):
            if hasattr(widget, 'restyle_glass'):
                widget.restyle_glass()
        self.apply_theme()
        self.sidebar.update()
        self.refresh()

    def system_theme_changed(self, *_):
        if self.settings.get('theme', 'auto') == 'auto':
            self.reveal_theme(resolve_theme('auto'), self.rect().center())

    def retranslate_content(self):
        self.log_name = tr('Windows event log') if sys.platform == 'win32' else tr('system journal')
        self.verify_button.setAccessibleName(tr('verify.accessible', log=self.log_name))
        self.verification.setText(tr('verify.intro', log=self.log_name))
        self.language_control.group.button(('en', 'vi').index(language())).setChecked(True)
        self.settings_page.load(self.settings)
        for card in self.cards.values():
            card.target = None
        self.toggle_import(self.import_toggle.isChecked())
        self.verify_controls.layout().invalidate()
        self.hero.position_controls()
        QTimer.singleShot(0, self.hero.position_controls)
        self.system.update_values(self.worker.values, THEMES[self.theme])
        self.system.sparklines.sample()
        self.refresh()

    def apply_visibility(self):
        enabled = self.settings.get('completed', False)
        for button in self.technician_actions.values():
            button.setEnabled(enabled)
        self.technician_actions['Export report'].setEnabled(enabled and self.daily_repair_complete)
        self.navigation.group.button(2).setVisible(enabled and self.settings.get('history', False))
        self.trend_panel.setVisible(enabled and self.settings.get('history', False))
        self.system.set_settings(self.settings if enabled else {})
        self.health.set_settings(self.settings if enabled else {})
        self.verify_button.setEnabled(enabled)
        self.start.setEnabled(enabled)
        if not self.settings.get('history') and self.navigation.group.checkedId() == 2:
            self.navigation.group.button(0).click()

    def open_settings(self):
        self.select_page(6)

    def open_technician(self, cls):
        try:
            dialog = cls(self, directory=data_dir(), language=language())
            dialog.exec()
            # Identity is a preference edit, not a request to configure tracking.
            # Refresh choices without invoking apply_preferences/autostart/import.
            self.settings = load_settings(data_dir())
            self.worker.set_settings(self.settings)
            self.apply_visibility()
            self.refresh()
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            QMessageBox.warning(self, tr('Could not open details'), str(error))

    def apply_preferences(self, settings, previous):
        if not settings['rebuild']:
            self.cancel_rebuild()
        self.settings = settings
        self.connect_page.update_health(settings)
        set_language(settings['language'])
        self.worker.set_settings(settings)
        self.apply_visibility()
        self.system.update_values(self.worker.values, THEMES[self.theme])
        if bool(settings.get('health')) != bool(previous.get('health')):
            from lifetime_company import update_health_consent
            future_health = self.worker.executor.submit(update_health_consent, settings.get('health', False))
            def health_finished():
                if not future_health.done():
                    if not self.worker.closed:
                        QTimer.singleShot(100, health_finished)
                    return
                try:
                    future_health.result()
                except Exception:
                    QMessageBox.warning(self, tr('Connect'), tr('Could not change Health sharing. Open Connect to change what is shared.'))
            QTimer.singleShot(100, health_finished)
        if settings['autostart'] != previous.get('autostart') or not previous.get('completed'):
            try:
                configure_tray_login(settings['autostart'])
            except OSError:
                pass        # the tracker's own login task below is what matters; the icon is a convenience
            future = self.worker.executor.submit(configure_login, settings['autostart'])
            def finished():
                if not future.done():
                    if not self.worker.closed:
                        QTimer.singleShot(100, finished)
                    return
                try:
                    future.result()
                except Exception as error:
                    if self.settings is settings:
                        settings['autostart'] = previous.get('autostart', False)
                        try:
                            save_settings(settings, data_dir())
                        except OSError:
                            pass
                    QMessageBox.warning(self, tr('Login tracking could not be updated'), str(error))
            QTimer.singleShot(100, finished)
        if settings['rebuild'] and (not previous.get('completed') or not previous.get('rebuild')):
            self.rebuild_history()

    def rebuild_history(self):
        if hasattr(self, 'import_process') and self.import_process.state() != QProcess.ProcessState.NotRunning:
            return
        self.import_process = QProcess(self)
        if getattr(sys, 'frozen', False):
            command = [str(Path(sys.executable).with_name('LifetimeImport.exe'))]
        else:
            script = 'lifetime_import_windows.py' if sys.platform == 'win32' else 'lifetime_import.py'
            command = [sys.executable, str(Path(__file__).with_name(script))]
        self.verification.setText(tr('rebuild.busy', log=self.log_name))
        self.import_process.finished.connect(self.import_finished)
        self.import_process.errorOccurred.connect(lambda error: self.verification.setText(
            tr('Earlier usage could not be added. Check that the history importer is installed.')))
        self.import_process.start(command[0], command[1:] + ['--apply', '--data-dir', str(data_dir())])

    def import_finished(self, code, status):
        if code == 0:
            self.verification.setText(tr('rebuild.done', log=self.log_name))
            self.refresh()
        else:
            detail = bytes(self.import_process.readAllStandardError()).decode('utf-8', errors='replace').strip()
            self.verification.setText(tr('rebuild.clock') if clock_problem(detail) else tr('rebuild.error', error=detail[-1000:]))

    def cancel_rebuild(self):
        if hasattr(self, 'import_process') and self.import_process.state() != QProcess.ProcessState.NotRunning:
            self.import_process.kill()
            self.import_process.waitForFinished(1000)

    def _show_history_banner(self):
        """Not on Connect: that form needs the height, and the banner is about a different task."""
        self.history_banner.setVisible(getattr(self, '_history_due', False) and getattr(self, 'active_page', 0) != 5)

    def select_page(self, index):
        if index not in (0, 1, 2, 3, 4, 5, 6):
            return
        if index == 2 and not (self.settings.get('completed') and self.settings.get('history')):
            return
        self.navigation.group.button(index).setChecked(True)
        if index == 6:
            self.settings_page.load(self.settings)
        if getattr(self, 'active_page', None) == index:
            return
        if self.page_animation:
            self.page_animation.stop()
            self.page_animation.deleteLater()
            self.page_animation = None
        previous = self.pages.currentWidget()
        if previous.graphicsEffect():
            previous.setGraphicsEffect(None)
        self.active_page = index
        self._show_history_banner()
        self.pages.setCurrentIndex(index)
        self.pages.updateGeometry()
        self.body.layout().activate()
        self.scroll.verticalScrollBar().setValue(0)
        self.sidebar.move_indicator()
        page = self.pages.currentWidget()
        if index == 0 and self.intro.done:
            self.hero.roll_again()
        # System can be thousands of pixels tall. A page-wide opacity effect
        # rasterizes its hardware sheet on every animation frame, including rows
        # outside the viewport. Its meters and sidebar already animate separately.
        if index != 1 and not motion_reduced():
            effect = RiseEffect(page)
            page.setGraphicsEffect(effect)
            def frame(value):
                effect.setOpacity(.15 + .85*value)
                effect.offset = 8*(1-value)
                effect.update()
            animate(self, 'page_animation', 0, 1, frame, 220)
            frame(0)
            self.page_animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self.reflow()

    @property
    def palette_tokens(self):
        return THEMES[self.theme or 'dark']

    def verify(self):
        if self.worker.verify(read_ledger):
            self.verify_button.setEnabled(False)
            self.verify_button.setText(tr('Verifying…'))
            self.verify_glyph.set_busy(True)
            self.verification.setText(tr('verify.reading', log=self.log_name))

    def verification_finished(self, result):
        self.verify_button.setEnabled(True)
        self.verify_button.setText(tr('Verify'))
        self.verify_glyph.set_busy(False)
        if not result:
            self.verification.setText(tr('Not available · verification timed out or could not complete'))
            return
        if result.get('ledger') is not None:
            self.display_ledger(result['ledger'])
        verification = result.get('verification') or {}
        self.last_verification = {**verification, 'captured': dt.datetime.now().astimezone().isoformat(timespec='seconds')}
        if verification.get('event_seconds') is not None:
            seconds = verification['difference']
            difference = seconds/3600
            numbers = dict(log=self.log_name, events=verification['event_seconds']/3600,
                           awake=verification['awake_seconds']/3600, difference=difference)
            if difference > 0.5:
                text = tr('verify.compared', **numbers)
            elif seconds > VERIFY_TOLERANCE:      # small but real: say so, in minutes and seconds
                text = tr('verify.gap', gap=format_gap(seconds), **numbers)
            else:
                text = tr('verify.result', **numbers)
        else:
            text = tr(verification.get('message', 'Not available · Windows event log could not be read'))
        if result.get('ledger_error'):
            text = tr('verify.ledger_error', message=text)
        self.verification.setText(text)

    def tray_status(self):
        if not self.isVisible():    # the window timer is stopped while hidden
            self.refresh()
        return self.status.text()

    def closeEvent(self, event):
        if self.tray and self.tray.keep(event):
            return
        self.cancel_rebuild()
        self.timer.stop()
        self.health.timer.stop()
        self.system.sparklines.timer.stop()
        self.worker.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'timer'):
            self.timer.start(1000)
        if getattr(self, '_shown_once', False) and getattr(self, 'active_page', None) == 0 and self.intro.done:
            self.hero.roll_again()      # brought back from the tray or restored: the total rolls in again
        self._shown_once = True
        self.reflow()
        QTimer.singleShot(0, lambda: self.sidebar.move_indicator(False))

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'timer'):
            self.timer.stop()
        for animation in self.findChildren(QVariantAnimation):
            if animation.state() == QVariantAnimation.State.Running:
                animation.setCurrentTime(animation.duration())
                animation.stop()

    def apply_theme(self):
        self.theme = active_theme()
        t = THEMES[self.theme]
        surface = t['card']
        install()
        self.setStyleSheet(stylesheet() + f"""
            QWidget {{ color: {t['text']}; font-family: '{'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'}'; font-size: 15px; }}
            Window {{ background: {t['bg']}; }}
            QScrollArea {{ border: none; background: {t['bg']}; }}
            QLabel {{ background: transparent; }}
            QLabel#brand {{ font-size: 23px; font-weight: 600; }}
            QLabel#muted, QLabel#signalLabel {{ color: {t['muted']}; font-size: 13px; }}
            QLabel#caption, QLabel#footer {{ color: {t['muted']}; font-size: 13px; }}
            QLabel#value {{ font-size: 25px; font-weight: 500; }}
            QLabel#statValue {{ font-size: 20px; font-weight: 500; }}
            QLabel#status {{ color: {t['muted']}; padding: 5px 0; font-size: 13px; }}
            QFrame#signalCard {{ background: {surface}; border: 1px solid {t['border']}; }}
            QLabel#groupTitle {{ font-size: 18px; font-weight: 600; }}
            QLabel#pageTitle {{ font-size: 28px; font-weight: 600; letter-spacing: -0.5px; }}
            QLabel#rowTitle {{ font-size: 16px; font-weight: 600; }}
            QFrame#insetPanel {{ background: {t['card']}; border: none; }}
            QFrame#divider {{ background: {t['border']}; border: none; }}
            QLabel#signalValue {{ font-size: 15px; }}
            QPushButton#primaryAction {{ color: {t['bg']}; background: {t['accent']}; border-color: {t['accent']}; font-weight: 600; }}
            QPushButton#primaryAction:hover, QPushButton#primaryAction:focus {{ background: {t['accent_hover']}; border-color: {t['text']}; }}
            QPushButton#primaryAction:disabled {{ color: {t['muted']}; background: {t['track']}; border-color: {t['border']}; }}
            QPushButton#quietAction {{ background: transparent; border-color: transparent; color: {t['muted']}; padding: 6px 0; }}
            QPushButton#quietAction:hover, QPushButton#quietAction:focus {{ color: {t['text']}; border-color: {t['muted']}; }}
            QPushButton#disclosure {{ text-align: left; background: transparent; border-color: transparent; color: {t['muted']}; padding: 12px 0; }}
            QPushButton#disclosure:hover, QPushButton#disclosure:focus {{ border-color: {t['muted']}; color: {t['text']}; }}
            QPushButton#startTracker {{ font-weight: 600; }}
            QPushButton#segment {{ padding: 5px 12px; color: {t['muted']}; background: transparent; }}
            QPushButton#segment:checked {{ background: {t['track']}; color: {t['text']}; }}
            QLabel#historyTitle {{ font-size: 17px; font-weight: 500; }}
            QLabel#historyValue {{ font-size: 15px; }}
            QToolTip {{ color: {t['text']}; background: {t['card']}; border: 1px solid {t['border']}; padding: 5px; }}
            QPushButton:pressed {{ background: {t['track']}; }}
            QPushButton#importToggle {{ background: transparent; border: none; padding: 6px 0; text-align: left; }}
            QPushButton#importToggle:focus {{ text-decoration: underline; }}
        """ + desktop_sheet(t))
        self.update()
        self.hero.update()
        QTimer.singleShot(0, self.baseline.update_editor_geometry)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, 'timer'):
            if self.isMinimized():
                self.timer.stop()
            elif self.isVisible():
                self.timer.start(1000)
        if event.type() == QEvent.Type.ApplicationPaletteChange and hasattr(self, 'hero'):
            self.apply_theme()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'report_rows'):
            return
        self.reflow()

    def reflow(self):
        self.sidebar.set_collapsed(self.width() < 900)
        self.sidebar.fit_height(self.height())
        available = self.width() - self.sidebar.width()
        margin = 16 if available < 850 else 32
        self.centering.setContentsMargins(margin, margin, margin, margin)
        content = available - margin*2
        columns = 5 if content >= 660 else 1
        if columns != self.columns:
            for card in self.cards.values():
                self.grid.removeWidget(card)
            for i, key in enumerate(self.stat_order):
                card = self.cards[key]
                card.set_list_mode(columns == 1, i < 4)
                self.grid.addWidget(card, i // columns, i % columns)
            for column in range(5):
                self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.columns = columns
        narrow = content < 480
        self.hero.setFixedWidth(max(0, min(560, content)))
        self.hero.setFixedHeight(196 if content < 400 else 144)
        self.hero.position_controls()
        self.verification.setContentsMargins(0, 0, 0, 0)     # aligned with the number now that the ring is gone
        self.system.set_columns(2 if content >= 900 else 1)
        self.baseline_row.setDirection(QHBoxLayout.Direction.TopToBottom if narrow else QHBoxLayout.Direction.LeftToRight)
        for row in self.report_rows:
            row.layout().setDirection(QHBoxLayout.Direction.TopToBottom if narrow else QHBoxLayout.Direction.LeftToRight)
        self.pages.updateGeometry()
        if self.counting_toggle.isChecked() and not getattr(self, 'counting_animation', None):
            self.counting_panel.setMaximumHeight(16777215)

    def toggle_counting(self, expanded):
        self.counting_panel.show()
        self.counting_panel.layout().activate()
        target = self.counting_panel.layout().totalHeightForWidth(self.counting_panel.width()) if expanded else 0
        if expanded:
            target = max(target, self.counting_panel.sizeHint().height())
        def frame(value):
            self.counting_panel.setMaximumHeight(round(value))
        def finish():
            self.counting_panel.setVisible(self.counting_toggle.isChecked())
            if self.counting_toggle.isChecked():
                self.counting_panel.setMaximumHeight(16777215)
            self.counting_animation = None
        animate(self, 'counting_animation', self.counting_panel.height(), target, frame, 200)
        if self.counting_animation:
            self.counting_animation.finished.connect(finish)
        else:
            finish()
        self.counting_toggle.setText(tr('Hide counting details') if expanded else tr('How this is counted'))

    def toggle_import(self, expanded):
        self.import_panel.show()
        target = self.import_panel.layout().sizeHint().height() if expanded else 0
        def update_height(value):
            self.import_panel.setMaximumHeight(round(value))
        animate(self, 'import_animation', self.import_panel.height() if self.import_panel.maximumHeight() else 0,
                target, update_height, 150)
        if self.import_animation:
            self.import_animation.finished.connect(lambda: self.import_panel.setVisible(self.import_toggle.isChecked()))
        else:
            self.import_panel.setVisible(expanded)
        self.import_toggle.setText(tr('+  Import starting hours').replace('+', '−', 1) if expanded else tr('+  Import starting hours'))

    def refresh(self):
        try:
            ledger = read_ledger()
            self.display_ledger(ledger)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.status_dot.tick(False)
            self.cards['current'].live.tick(False)
            self.status.setText(tr('Ledger unavailable'))
            self.status_dot.setToolTip(tr('Ledger unavailable'))
            self.hint.setText(tr('ledger.error', error=error))
            self.hint.show()
            self.start.show()
            self.tracker_banner.show()

    def display_ledger(self, ledger):
        try:
            evidence = coverage(ledger, self.settings)
            self.coverage_summary.setText(tr('Coverage: {source} · {status}', source=tr(SOURCES[evidence['source']]),
                status=tr('Partial' if evidence['status'] == 'partial' else 'Unknown')))
            name = self.settings.get('identity', {}).get('display_name', '')
            if not name and self.settings.get('completed') and self.settings.get('resources'):
                name = str(tr('System name'))+': '+(platform.node() or str(tr('Not available')))
            self.device_name.setText('\u200b'.join(name[i:i+32] for i in range(0, len(name), 32)))
            self.device_name.setAccessibleName(name)
            self.device_name.setVisible(bool(name))
            self.history.set_ledger(ledger)
            today = dt.date.today()
            points = date_series(clean_daily(ledger.get('daily', {})), today-dt.timedelta(days=13), today)
            dates = [day for day, value in points]
            values = [None if value is None else value/3600 for day, value in points]
            self.trend.set_data(dates, values, dates=dates)
            try:
                status = json.loads((data_dir() / 'status.json').read_text(encoding='utf-8-sig'))
                running = isinstance(status, dict) and status.get('running') is True and 0 <= time.time() - status['heartbeat'] < 90
            except (OSError, ValueError, KeyError, TypeError):
                running = False
            total = ledger['total_seconds'] + ledger['baseline_seconds']
            # Interpolate with the same awake-only clock, never wall time (sleep
            # and clock adjustments must not inflate the displayed live total).
            if running and ledger.get('last_clock') is not None:
                try:
                    delta = awake_clock()-ledger['last_clock']
                    if 0 <= delta <= MAX_DELTA:
                        total += min(delta, 60)
                except OSError:
                    pass
            parts = source_totals(ledger)
            def component(value):
                return duration(value) if value is not None else tr('Not available')
            self.source_breakdown.setText(tr('Saved sources · measured: {measured} · OS logs: {imported} · manual baseline: {baseline}',
                measured=component(parts['measured_seconds']), imported=component(parts['imported_seconds']), baseline=component(parts['baseline_seconds'])))
            if not self.initialized or abs(total - self.hero.target) > 5:
                self.hero.set_total(total)
            else:
                self.hero.target = self.hero.seconds = total
                self.hero.update()
            first = dt.date.fromisoformat(ledger['first_seen']) if ledger.get('first_seen') else None
            pretty_date = date_text(first) if first else tr('Not available')
            average = average_awake(ledger)
            self.cards['first'].value.setText(pretty_date)
            self.cards['boots'].set_number(ledger['boots'], lambda v: f'{round(v):,}')
            for key, field in (('current', 'current_session_seconds'), ('longest', 'longest_session_seconds')):
                self.cards[key].set_number(ledger[field], lambda v: duration(v) if v >= 86400 else duration(v, days=False))
            self.cards['average'].set_number(-1 if average is None else average, lambda v: tr('Not available') if v < 0 else tr('hours.value', value=v))
            source = tr(FIRST_SEEN_SOURCES.get(ledger.get('first_seen_source', 'unknown'), 'Unknown source'))
            since = tr('Days since this date: {days}', days=(dt.date.today()-first).days) if first and first <= dt.date.today() else tr('Not available')
            self.cards['first'].setToolTip(str(source)+' · '+str(since))
            captions = (source, tr('Since tracking began'),
                        tr('Awake this session'), tr('Longest recorded session'), tr('Needs 1 day of data') if average is None else tr('since tracking began'))
            for card, caption in zip(self.cards.values(), captions):
                card.caption.setText(caption)
            if not self.initialized:
                self.baseline.setValue(ledger['baseline_seconds'] / 3600)
                self.initialized = True
            self.intro.ready()
            self.status_dot.tick(running)
            self.cards['current'].live.tick(running)
            self.status.setText(tr('Tracking active') if running else tr('Tracker offline'))
            self.status_dot.setToolTip(tr('Tracking active') if running else tr('Tracker offline'))
            self.hint.setText(tr('Recording awake time\nTracker saves every 30 s · view refreshes every 1 s') if running
                              else tr('Saved totals are available.\nStart the tracker to resume recording.'))
            self.start.setVisible(not running)
            self.tracker_banner.setVisible(not running)
            self.hint.setVisible(not running)
            importing = hasattr(self, 'import_process') and self.import_process.state() != QProcess.ProcessState.NotRunning
            self._history_due = bool(self.settings.get('completed')) and not ledger.get('imported') and not importing
            self._show_history_banner()
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.status_dot.tick(False)
            self.cards['current'].live.tick(False)
            self.status.setText(tr('Ledger unavailable'))
            self.status_dot.setToolTip(tr('Ledger unavailable'))
            self.hint.setText(tr('ledger.error', error=error))
            self.hint.show()
            self.start.show()
            self.tracker_banner.show()

    def apply_baseline(self):
        try:
            set_baseline(self.baseline.value())
            self.refresh()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Baseline could not be saved'), str(error))

    def start_tracker(self):
        executable = Path(sys.executable)
        if getattr(sys, 'frozen', False):          # packaged Windows build: tracker exe sits next to this one
            command = [str(executable.with_name('LifetimeTracker.exe')), '--run']
        else:
            if sys.platform == 'win32' and executable.with_name('pythonw.exe').exists():
                executable = executable.with_name('pythonw.exe')
            command = [str(executable), str(Path(__file__).with_name('lifetime_tracker.py')), '--run']
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.hint.setText(tr('Starting tracker…'))
        except OSError as error:
            QMessageBox.warning(self, tr('Could not start tracker'), str(error))


if __name__ == '__main__':
    if sys.platform == 'win32':  # own taskbar identity so the icon is not grouped under python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Lifetime.App')
        except Exception:
            pass
    configure_high_dpi()
    app = QApplication(sys.argv)
    here = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    for candidate in (here / 'assets' / 'lifetime.png', here / 'lifetime.png'):
        if candidate.exists():
            app.setWindowIcon(QIcon(str(candidate)))
            break
    if tray_available() and tell_running_instance('lifetime-gui'):
        sys.exit(0)
    settings = load_settings()
    set_language(settings['language'])
    set_theme(resolve_theme(settings.get('theme', 'auto')))
    set_style(settings.get('style', 'classic'))
    install_glass_surfaces()
    previous = dict(settings)
    if not settings['completed']:
        setup = SetupDialog(THEMES[active_theme()], settings)
        if setup.exec() != setup.DialogCode.Accepted:
            sys.exit(0)
        settings = setup.result_settings
    window = Window(settings)
    window.tray = install_tray(window, 'Lifetime', window.tray_status, key='lifetime-gui')
    if settings.get('autostart'):
        try:
            configure_tray_login(True)      # existing installs get the sign-in icon too
        except OSError:
            pass
    if not (window.tray and '--tray' in sys.argv and previous['completed']):
        window.show()
    window.status.setText(tr('Updating history'))
    window._set_export_ready(False)
    window.start_daily_repair()
    if not previous['completed']:
        window.apply_preferences(settings, previous)
        window.start_tracker()
    code = app.exec()
    shut_down(app, window)
    sys.exit(code)
