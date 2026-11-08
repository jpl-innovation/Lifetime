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

from PyQt6.QtCore import QEvent, Qt, QTimer, QRectF, QVariantAnimation, QEasingCurve, QPointF, QProcess
from PyQt6.QtGui import QColor, QIcon, QPalette, QPainter, QPen, QFont
from PyQt6.QtWidgets import (QApplication, QAbstractSpinBox, QDoubleSpinBox,
                            QGraphicsOpacityEffect, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                            QProgressBar, QPushButton, QButtonGroup, QToolTip, QScrollArea, QVBoxLayout, QWidget)
from PyQt6.QtWidgets import QSizePolicy
from lifetime_i18n_qt import QLabel, QPushButton, QWidget, QFrame, QDoubleSpinBox
from lifetime_tracker import average_awake, data_dir, read_ledger, set_baseline, repair_daily, awake_clock, MAX_DELTA
from lifetime_i18n import duration
from lifetime_dashboard import ProbeWorker, SystemDashboard, RefreshGlyph
from lifetime_charts import Insights
from lifetime_settings import load_settings, save_settings, update_settings
from lifetime_setup import SetupDialog, configure_login
from lifetime_reports import coverage, SOURCES, FIRST_SEEN_SOURCES, source_totals
from lifetime_technician import IdentityDialog, CoverageDialog, ExportDialog
from lifetime_company import CompanyDialog
from lifetime_tasks import Tasks

from lifetime_theme import THEMES
REDUCE_MOTION = os.environ.get('LIFETIME_REDUCE_MOTION') == '1'


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
    if REDUCE_MOTION or start == end:
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
        return rect.adjusted(0, -2, 0, 5)

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
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        shown = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        self.setToolTip(self.text() if shown != self.text() else '')
        painter.drawText(self.contentsRect(), self.alignment() | Qt.AlignmentFlag.AlignVCenter, shown)


class LiveDot(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(10, 10)
        self.active = False
        self.phase = False

    def tick(self, active):
        changed = self.active != active
        self.active = active
        if active and not REDUCE_MOTION:
            self.phase = not self.phase
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = THEMES[self.window().theme]
        color = QColor(t['accent'] if self.active else t['muted'])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(2, 2, 6, 6))
        if self.active and self.phase:
            p.setPen(QPen(color, 1))
            p.drawLine(4, 0, 6, 0)


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
        self.setFixedHeight(148)
        self.setAccessibleName(tr('Total time awake'))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = THEMES[self.window().theme]
        w, h = self.width(), self.height()
        def text(y, height, value, size, color, weight=QFont.Weight.Normal, x=0, width=None):
            f = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
            f.setPixelSize(size)
            f.setWeight(weight)
            p.setFont(f)
            p.setPen(QColor(color))
            p.drawText(QRectF(x, y, w if width is None else width, height), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
        whole_minutes, secs = divmod(int(self.seconds), 60)
        days, rest = divmod(whole_minutes, 1440)
        hours, minutes = divmod(rest, 60)
        compact = h < 140
        text(2, 25, tr('Total time awake'), 15, t['muted'])
        value = f'{self.seconds / 3600:,.1f}'
        size = 52 if compact else 60
        f = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
        f.setPixelSize(size)
        from PyQt6.QtGui import QFontMetrics
        while QFontMetrics(f).horizontalAdvance(value) > max(120, w-12) and size > 26:
            size -= 2
            f.setPixelSize(size)
        text(23 if compact else 29, 62 if compact else 78, value, size, t['text'], QFont.Weight.Light)
        text(82 if compact else 111, 28, tr('hero.duration', days=days, hours=hours, minutes=minutes, seconds=secs), 15, t['muted'])

    def set_total(self, seconds):
        self.target = seconds
        self.setAccessibleDescription(duration(seconds))
        def update_value(value):
            self.seconds = value
            self.update()
        def update_ring(value):
            self.fraction = value
            self.update()
        animate(self, 'number_animation', self.seconds, seconds, update_value)
        animate(self, 'ring_animation', self.fraction, (seconds / 3600 % 100) / 100, update_ring)


class StatCard(QFrame):
    def __init__(self, title, kind, progress=False):
        super().__init__()
        self.setObjectName('card')
        self.hover = 0.0
        self.number = 0.0
        self.target = None
        self.effect = RiseEffect(self)
        self.setGraphicsEffect(self.effect)
        self.setFixedHeight(96 if not progress else 72)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(2)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(Icon(kind))
        heading_label = label(title, 'muted')
        heading_label.setWordWrap(True)
        heading.addWidget(heading_label, 1)
        self.live = LiveDot() if kind == 'clock' else None
        if self.live:
            heading.addWidget(self.live)
        self.value = ValueLabel('—')
        self.value.setMinimumWidth(0)
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value.setObjectName('value')
        self.caption = label('', 'caption')
        self.caption.setWordWrap(True)
        self.progress = None
        if progress:
            self.value.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self.value.setFixedWidth(130)
            heading.addWidget(self.value)
            layout.addLayout(heading)
            self.progress = QProgressBar()
            self.progress.setRange(0, 2400)
            self.progress.setTextVisible(False)
            layout.addWidget(self.progress)
            layout.addWidget(self.caption)
        else:
            layout.addLayout(heading)
            layout.addWidget(self.value)
            layout.addWidget(self.caption)

    def set_number(self, value, formatter):
        if value == self.target:
            return
        if self.target is not None and formatter(value) != formatter(self.target):
            animate(self, 'value_fade', .65, 1, self.value.set_opacity, 150)
        self.target = value
        self.value.setAccessibleName(formatter(value))
        def update(value):
            self.number = value
            self.value.setText(formatter(value))
            if self.progress:
                self.progress.setValue(min(2400, max(0, round(value * 100))))
        animate(self, 'number_animation', self.number, value, update)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = THEMES[self.window().theme]
        border = QColor(t['border'])
        bright = QColor(t['muted'])
        border.setRedF(border.redF() + (bright.redF() - border.redF()) * self.hover * .5)
        border.setGreenF(border.greenF() + (bright.greenF() - border.greenF()) * self.hover * .5)
        border.setBlueF(border.blueF() + (bright.blueF() - border.blueF()) * self.hover * .5)
        p.setPen(QPen(border, 1))
        p.setBrush(QColor(t['card']))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 10, 10)

    def set_hover(self, value):
        self.hover = value
        self.effect.offset = 0 if REDUCE_MOTION else -2 * value
        self.effect.update()
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        animate(self, 'hover_animation', self.hover, 1, self.set_hover, 120)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        animate(self, 'hover_animation', self.hover, 0, self.set_hover, 120)


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
            animate(self, 'entrance', 0, 1, frame, 350)

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
            height = rect.height() * value/3600/maximum * self.progress
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


class Window(QWidget):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else load_settings(data_dir())
        self.last_verification = None
        self.daily_repair_complete = True
        set_language(self.settings.get('language', language()))
        self.theme = None
        self.initialized = False
        self.setWindowTitle(tr('Lifetime · JPL Innovation'))
        self.resize(1280, 800)
        self.setMinimumSize(390, 440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        footer = label(tr('Lifetime · Made by JPL Innovation'), 'footer')
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setContentsMargins(16, 8, 16, 12)
        outer.addWidget(footer)
        canvas = Backdrop()
        self.scroll.setWidget(canvas)
        centering = QHBoxLayout(canvas)
        self.centering = centering
        centering.setContentsMargins(24, 24, 24, 24)
        self.body = QWidget()
        self.body.setMaximumWidth(1400)
        centering.addWidget(self.body, 1, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(label(tr('Lifetime'), 'brand'))
        header.addStretch()
        self.status = label('', 'status')
        self.status_dot = LiveDot()
        header.addWidget(self.status_dot)
        header.addWidget(self.status)
        settings_button = QPushButton(tr('Settings'))
        settings_button.clicked.connect(self.open_settings)
        header.addWidget(settings_button)
        self.language_control = segments([tr('EN'), tr('VI')],
                                         lambda index: self.switch_language(('en', 'vi')[index]))
        self.language_control.group.button(('en', 'vi').index(language())).setChecked(True)
        for button in self.language_control.group.buttons():
            button.setMinimumSize(44, 36)
        self.language_control.setAccessibleName(tr('Language'))
        header.addWidget(self.language_control)
        header.setContentsMargins(24, 12, 24, 4)
        outer.insertLayout(0, header)
        self.navigation = segments([tr('Overview'), tr('System'), tr('Insights')], self.select_page)
        layout.addWidget(self.navigation, alignment=Qt.AlignmentFlag.AlignLeft)
        self.overview = QWidget()
        overview_layout = QVBoxLayout(self.overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(8)
        page_layout = layout
        page_layout.addWidget(self.overview)
        layout = overview_layout
        self.hero = Hero()
        self.source_breakdown = label('', 'caption')
        self.source_breakdown.setWordWrap(True)
        self.source_breakdown.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        total_row = QHBoxLayout()
        total_row.addWidget(self.hero, 1)
        self.verify_glyph = RefreshGlyph()
        self.verify_button = QPushButton(tr('Verify'))
        self.log_name = tr('Windows event log') if sys.platform == 'win32' else tr('system journal')
        self.verify_button.setAccessibleName(tr('verify.accessible', log=self.log_name))
        self.verify_button.clicked.connect(self.verify)
        total_row.addWidget(self.verify_glyph)
        total_row.addWidget(self.verify_button)
        layout.addLayout(total_row)
        layout.addWidget(self.source_breakdown)
        self.verification = label(tr('verify.intro', log=self.log_name), 'caption')
        self.verification.setWordWrap(True)
        layout.addWidget(self.verification)
        grid = QGridLayout()
        self.grid = grid
        self.columns = 2
        grid.setSpacing(16)
        self.cards = {}
        for i, (key, title, kind) in enumerate((
            ('first', tr('Earliest known date'), 'calendar'), ('boots', tr('Boots observed'), 'restart'),
            ('current', tr('Current session'), 'clock'), ('longest', tr('Longest session'), 'longest'),
            ('average', tr('Average awake / day'), 'average'))):
            card = StatCard(tr(title), kind, i == 4)
            self.cards[key] = card
            grid.addWidget(card, i // 2, i % 2, 1, 2 if i == 4 else 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        note = label(tr('Sleep and hibernation are not counted.'), 'caption')
        note.setWordWrap(True)
        self.awake_note = label(tr('Awake time is the time the computer was running and awake. It does not measure human activity, component health or remaining lifespan.'), 'caption')
        self.awake_note.setWordWrap(True)
        layout.addWidget(self.awake_note)
        actions = QHBoxLayout()
        actions.addWidget(note, 1)
        self.import_toggle = QPushButton(tr('+  Import starting hours'))
        self.import_toggle.setObjectName('importToggle')
        self.import_toggle.setCheckable(True)
        self.import_toggle.toggled.connect(self.toggle_import)
        actions.addWidget(self.import_toggle)
        layout.addLayout(actions)
        self.import_panel = QWidget()
        form = QVBoxLayout(self.import_panel)
        form.setContentsMargins(0, 0, 0, 8)
        explanation = label(tr('Known hours before tracking. Replaces the existing baseline.'), 'caption')
        explanation.setWordWrap(True)
        form.addWidget(explanation)
        row = QHBoxLayout()
        self.baseline = HoursInput()
        self.baseline.setFixedHeight(40)
        self.baseline.lineEdit().setTextMargins(12, 0, 12, 0)
        self.baseline.setRange(0, 100_000_000)
        self.baseline.setDecimals(2)
        self.baseline.setSuffix(tr('unit.hour.suffix'))
        self.baseline.setAccessibleName(tr('Starting hours'))
        self.baseline.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        row.addWidget(self.baseline, 1)
        apply = QPushButton(tr('Set baseline'))
        apply.clicked.connect(self.apply_baseline)
        row.addWidget(apply)
        form.addLayout(row)
        layout.addWidget(self.import_panel)
        self.import_panel.setMaximumHeight(0)
        self.import_panel.hide()
        self.system = SystemDashboard()
        page_layout.addWidget(self.system)
        self.system.hide()
        self.history = History()
        page_layout.addWidget(self.history)
        self.history.hide()
        layout = page_layout
        tracker = QHBoxLayout()
        self.hint = label('', 'caption')
        self.hint.setWordWrap(True)
        tracker.addWidget(self.hint, 1)
        self.start = QPushButton(tr('Start tracker'))
        self.start.setObjectName('startTracker')
        self.start.clicked.connect(self.start_tracker)
        tracker.addWidget(self.start)
        layout.addLayout(tracker)
        self.device_name = label('', 'caption')
        self.device_name.setWordWrap(True)
        self.device_name.setTextFormat(Qt.TextFormat.PlainText)
        self.device_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.device_name)
        self.coverage_summary = label('', 'caption')
        self.coverage_summary.setWordWrap(True)
        layout.addWidget(self.coverage_summary)
        self.technician_actions = {}
        technician = QGridLayout()
        for index, (text, dialog) in enumerate((('Device identity', IdentityDialog),
                                               ('Export report', ExportDialog), ('Coverage details', CoverageDialog),
                                               ('Company server', CompanyDialog))):
            button = QPushButton(tr(text))
            button.setAccessibleName(tr(text))
            button.clicked.connect(lambda checked=False, cls=dialog: self.open_technician(cls))
            technician.addWidget(button, index//2, index%2)
            self.technician_actions[text] = button
        layout.addLayout(technician)
        self.repair_notice = label('', 'caption')
        self.repair_notice.setWordWrap(True)
        self.repair_notice.hide()
        layout.addWidget(self.repair_notice)
        self.apply_theme()
        QApplication.instance().paletteChanged.connect(self.apply_theme)
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

    def retranslate_content(self):
        self.log_name = tr('Windows event log') if sys.platform == 'win32' else tr('system journal')
        self.verify_button.setAccessibleName(tr('verify.accessible', log=self.log_name))
        self.verification.setText(tr('verify.intro', log=self.log_name))
        self.language_control.group.button(('en', 'vi').index(language())).setChecked(True)
        for card in self.cards.values():
            card.target = None
        self.toggle_import(self.import_toggle.isChecked())
        self.system.update_values(self.worker.values, THEMES[self.theme])
        self.system.sparklines.sample()
        self.refresh()

    def apply_visibility(self):
        enabled = self.settings.get('completed', False)
        for button in self.technician_actions.values():
            button.setEnabled(enabled)
        self.technician_actions['Export report'].setEnabled(enabled and self.daily_repair_complete)
        self.navigation.group.button(2).setVisible(enabled and self.settings.get('history', False))
        self.system.set_settings(self.settings if enabled else {})
        self.verify_button.setEnabled(enabled)
        self.start.setEnabled(enabled)
        if not self.settings.get('history') and self.navigation.group.checkedId() == 2:
            self.navigation.group.button(0).click()

    def open_settings(self):
        dialog = SetupDialog(THEMES[self.theme], self.settings, data_dir(), self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.apply_preferences(dialog.result_settings, self.settings)
            self.refresh()

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
        set_language(settings['language'])
        self.worker.set_settings(settings)
        self.apply_visibility()
        self.system.update_values(self.worker.values, THEMES[self.theme])
        if settings['autostart'] != previous.get('autostart') or not previous.get('completed'):
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
            tr('History rebuild could not start. Check that the history importer is installed.')))
        self.import_process.start(command[0], command[1:] + ['--apply', '--data-dir', str(data_dir())])

    def import_finished(self, code, status):
        if code == 0:
            self.verification.setText(tr('rebuild.done', log=self.log_name))
            self.refresh()
        else:
            detail = bytes(self.import_process.readAllStandardError()).decode('utf-8', errors='replace').strip()
            self.verification.setText(tr('rebuild.error', error=detail[-1000:]))

    def cancel_rebuild(self):
        if hasattr(self, 'import_process') and self.import_process.state() != QProcess.ProcessState.NotRunning:
            self.import_process.kill()
            self.import_process.waitForFinished(1000)

    def select_page(self, index):
        self.overview.setVisible(index == 0)
        self.system.setVisible(index == 1)
        self.history.setVisible(index == 2 and self.settings.get('history', False))
        self.scroll.verticalScrollBar().setValue(0)

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

    def closeEvent(self, event):
        self.cancel_rebuild()
        self.timer.stop()
        self.system.sparklines.timer.stop()
        self.worker.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'timer'):
            self.timer.start(1000)
        if getattr(self, 'entered', False) or REDUCE_MOTION:
            return
        self.entered = True
        self.hero.effect = RiseEffect(self.hero)
        self.hero.setGraphicsEffect(self.hero.effect)
        for index, widget in enumerate([self.hero, *self.cards.values()]):
            effect = widget.effect
            effect.setOpacity(0)
            def begin(w=widget, e=effect):
                if not self.isVisible():
                    return
                def frame(value):
                    try:
                        e.setOpacity(value)
                        e.offset = 5 * (1 - value)
                        e.update()
                    except RuntimeError:
                        pass  # effect already deleted
                try:
                    animate(w, 'entrance_animation', 0, 1, frame, 150)
                except RuntimeError:
                    pass  # the card was deleted (window closed or rebuilt) before its turn; PyQt would abort on this
            QTimer.singleShot(index * 50, begin)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'timer'):
            self.timer.stop()
        for animation in self.findChildren(QVariantAnimation):
            animation.setCurrentTime(animation.duration())
            animation.stop()

    def apply_theme(self):
        self.theme = 'dark'
        t = THEMES[self.theme]
        surface = t['card']
        self.setStyleSheet(f"""
            QWidget {{ color: {t['text']}; font-family: '{'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'}'; font-size: 15px; }}
            Window {{ background: {t['bg']}; }}
            QScrollArea {{ border: none; background: {t['bg']}; }}
            QLabel {{ background: transparent; }}
            QLabel#brand {{ font-size: 23px; font-weight: 600; }}
            QLabel#muted, QLabel#signalLabel {{ color: {t['muted']}; font-size: 13px; }}
            QLabel#caption, QLabel#footer {{ color: {t['muted']}; font-size: 13px; }}
            QLabel#value {{ font-size: 25px; font-weight: 500; }}
            QLabel#status {{ color: {t['muted']}; padding: 5px 0; font-size: 13px; }}
            QFrame#signalCard {{ background: {surface}; border: 1px solid {t['border']}; border-radius: 6px; }}
            QLabel#groupTitle {{ font-size: 18px; font-weight: 600; }}
            QLabel#pageTitle {{ font-size: 23px; font-weight: 600; }}
            QLabel#signalValue {{ font-size: 15px; }}
            QPushButton {{ color: {t['text']}; background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 8px 12px; }}
            QPushButton:hover, QPushButton:focus {{ border-color: {t['accent']}; }}
            QPushButton#startTracker {{ color: {t['bg']}; background: {t['accent']}; border-color: {t['accent']}; font-weight: 600; }}
            QPushButton#startTracker:hover, QPushButton#startTracker:focus {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
            QPushButton#segment {{ padding: 5px 12px; border-radius: 5px; color: {t['muted']}; background: transparent; }}
            QPushButton#segment:checked {{ background: {t['track']}; color: {t['text']}; }}
            QLabel#historyTitle {{ font-size: 17px; font-weight: 500; }}
            QLabel#historyValue {{ font-size: 15px; }}
            QToolTip {{ color: {t['text']}; background: {t['card']}; border: 1px solid {t['border']}; padding: 5px; }}
            QPushButton:pressed {{ background: {t['track']}; }}
            QPushButton#importToggle {{ background: transparent; border: none; padding: 6px 0; text-align: left; }}
            QPushButton#importToggle:focus {{ text-decoration: underline; }}
            QDoubleSpinBox {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 0px; selection-background-color: {t['accent']}; }}
            QProgressBar {{ background: {t['track']}; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; }}
            QProgressBar::chunk {{ border-radius: 2px; background: {t['accent']}; }}
            QScrollBar:vertical {{ background: {t['bg']}; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 24px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
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
        if not hasattr(self, 'cards'):
            return
        self.status.setVisible(self.width() >= 600)
        short = self.height() < 620 and self.width() >= 700
        columns = 3 if self.width() >= 700 else 2
        if columns != self.columns:
            for card in self.cards.values():
                self.grid.removeWidget(card)
            for i, card in enumerate(self.cards.values()):
                self.grid.addWidget(card, i // columns, i % columns,
                                    1, 2 if i == 4 else 1)
            for column in range(3):
                self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.columns = columns
        compact = self.height() <= 640
        self.hero.setFixedHeight(112 if compact else 148)
        self.system.set_columns(2 if self.width() >= 700 else 1)
        for name, card in self.cards.items():
            card.setFixedHeight(104 if compact else 108)
        margin = 16 if compact else 24
        self.centering.setContentsMargins(margin, margin, margin, margin)

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
            self.hint.setText(tr('ledger.error', error=error))
            self.hint.show()
            self.start.show()

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
            self.cards['average'].set_number(-1 if average is None else average, lambda v: '—' if v < 0 else tr('hours.value', value=v))
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
            self.status_dot.tick(running)
            self.cards['current'].live.tick(running)
            self.status.setText(tr('Tracking active') if running else tr('Tracker offline'))
            self.hint.setText(tr('Recording awake time\nTracker saves every 30 s · view refreshes every 1 s') if running
                              else tr('Saved totals are available.\nStart the tracker to resume recording.'))
            self.start.setVisible(not running)
            self.hint.setVisible(not running)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.status_dot.tick(False)
            self.cards['current'].live.tick(False)
            self.status.setText(tr('Ledger unavailable'))
            self.hint.setText(tr('ledger.error', error=error))
            self.hint.show()
            self.start.show()

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
    app = QApplication(sys.argv)
    here = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    for candidate in (here / 'assets' / 'lifetime.png', here / 'lifetime.png'):
        if candidate.exists():
            app.setWindowIcon(QIcon(str(candidate)))
            break
    settings = load_settings()
    set_language(settings['language'])
    previous = dict(settings)
    if not settings['completed']:
        setup = SetupDialog(THEMES['dark'], settings)
        if setup.exec() != setup.DialogCode.Accepted:
            sys.exit(0)
        settings = setup.result_settings
    window = Window(settings)
    window.show()
    window.status.setText(tr('Updating history'))
    window._set_export_ready(False)
    window.start_daily_repair()
    if not previous['completed']:
        window.apply_preferences(settings, previous)
        window.start_tracker()
    sys.exit(app.exec())
