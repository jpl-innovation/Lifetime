"""Native presentation primitives for Lifetime Server; no store or network I/O."""
import math
import os
import sys

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QTabBar, QVBoxLayout, QWidget)

from lifetime_theme import THEMES, install, T
from lifetime_reveal import Intro, phase

from lifetime_theme import Tokens
C = Tokens()   # follows the active palette


def more_icon():
    """Optically centered action dots, independent of the font's period baseline."""
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(C['muted']))
    for x in (4, 9, 14):
        painter.drawEllipse(QPointF(x, 9), 1.2, 1.2)
    painter.end()
    return QIcon(pixmap)


def status_dot(color, size=10):
    """A small filled circle icon; status colour sits on the dot, never on the words."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(size * 0.5, size * 0.5, size, size))
    p.end()
    return QIcon(pixmap)


def local_expiry(iso, translate):
    """'9 Nov 2026 at 11:46' in the window's language, from an ISO timestamp."""
    import datetime as dt
    moment = dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone()
    day = translate('date.full', day=moment.day, month=translate(f'month.{moment.month}'), year=moment.year)
    return translate('Expires {date} at {time}', date=day, time=moment.strftime('%H:%M'))


def reduced_motion():
    if os.environ.get('LIFETIME_REDUCE_MOTION') == '1':
        return True
    if sys.platform == 'win32':
        import ctypes
        enabled = ctypes.c_int(1)
        # Respect Windows "Animation effects" as well as the application override.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return not enabled.value
    return False


class FleetTabBar(QTabBar):
    """One interruptible underline; keyboard and reduced-motion changes are instant."""
    def __init__(self, parent=None):
        super().__init__(parent)
        # No native tab frame: the sliding underline is the only selection mark.
        self.setDrawBase(False)
        self.setDocumentMode(True)
        self.indicator = QLabel(self)
        self.indicator.setStyleSheet('background: #FFC93C; ')
        self.indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animation = QPropertyAnimation(self.indicator, b'geometry', self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.currentChanged.connect(self.move_indicator)

    def move_indicator(self, index, animate=True):
        target = self.tabRect(index)
        target = QRect(target.x() + 12, target.bottom() - 2, max(0, target.width() - 24), 3)
        self.animation.stop()
        if reduced_motion() or not animate or not self.isVisible():
            self.indicator.setGeometry(target)
        else:
            self.animation.setStartValue(self.indicator.geometry())
            self.animation.setEndValue(target)
            self.animation.start()
        self.indicator.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.move_indicator(self.currentIndex(), False)

    def showEvent(self, event):
        super().showEvent(event)
        self.move_indicator(self.currentIndex(), False)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self.move_indicator(self.currentIndex(), False)


class DailyChart(QLabel):
    """One series with explicit missing dates and contributor counts in tooltips."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = []
        self.missing = 'No dated reports yet'
        self.unit = 'h'
        self.day_label = lambda day: day[8:10] + '/' + day[5:7]   # replaced by the window with a localized '2 Nov'
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.intro = Intro(self, 1000, 120, reduced_motion)

    def set_samples(self, samples, missing, unit='h'):
        self.samples, self.missing, self.unit = samples, missing, unit
        self.setAccessibleName(missing if not any(s['seconds'] is not None for s in samples) else
                               '; '.join(s['day'] + ': ' + ('?' if s['seconds'] is None else f"{s['seconds']/3600:.2f} {unit}") for s in samples))
        self.update()
        if any(sample['seconds'] is not None for sample in samples):
            self.intro.ready()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        entering = not self.intro.done
        v = self.intro.value if entering else 1.0
        bounds = QRectF(48, 12, max(1, self.width()-64), max(1, self.height()-45))
        available = [s['seconds']/3600 for s in self.samples if s['seconds'] is not None]
        if not available:
            p.setPen(QColor(C['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.missing)
            return
        maximum = max(1, max(available))
        magnitude = 10**math.floor(math.log10(maximum))
        ceiling = math.ceil(maximum / magnitude) * magnitude
        p.setOpacity(phase(v, 0.0, 0.3))        # the axis settles first, so the bars have something to stand on
        for tick in range(5):
            y = bounds.bottom() - tick / 4 * bounds.height()
            p.setPen(QPen(QColor(C['border']), 1))
            p.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
            p.setPen(QColor(C['muted']))
            p.drawText(QRectF(0, y-9, 40, 18), Qt.AlignmentFlag.AlignRight, f'{ceiling*tick/4:g} {self.unit}')
        p.setOpacity(1.0)
        pitch = bounds.width() / max(1, len(self.samples))
        step = max(1, math.ceil(len(self.samples) / 7))
        for index, sample in enumerate(self.samples):
            x = bounds.left() + pitch * (index + 0.5)
            if sample['seconds'] is None:
                p.setPen(QPen(QColor(C['muted']), 2))
                p.drawLine(QPointF(x-3, bounds.bottom()-4), QPointF(x+3, bounds.bottom()-4))
            else:
                height = sample['seconds']/3600/ceiling*bounds.height()
                if entering:    # oldest day first, the newest (accent) bar last and a little slower
                    begin = 0.16 + 0.5 * index / max(1, len(self.samples) - 1)
                    height *= phase(v, begin, begin + (0.34 if index == len(self.samples) - 1 else 0.28))
                # The newest day carries the accent; earlier days stay quiet behind it.
                p.save()
                p.setClipRect(bounds.adjusted(0, -2, 0, 0))
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(C['accent'] if index == len(self.samples) - 1 else T(T('#3A5688'))))
                width = min(pitch*.5, 44)
                p.drawRoundedRect(QRectF(x-width/2, bounds.bottom()-max(1, height), width, max(1, height)+8), 6, 6)
                p.restore()
            if index % step == 0 or index == len(self.samples)-1:
                p.setPen(QColor(C['muted']))
                p.drawText(QRectF(x-40, bounds.bottom()+8, 80, 22), Qt.AlignmentFlag.AlignHCenter, self.day_label(sample['day']))

    def mouseMoveEvent(self, event):
        if self.samples:
            index = int((event.position().x()-48) / max(1, self.width()-64) * len(self.samples))
            if 0 <= index < len(self.samples):
                s = self.samples[index]
                value = self.missing if s['seconds'] is None else f"{s['seconds']/3600:.2f} {self.unit} · {s['devices']}"
                self.setToolTip(s['day'] + '\n' + value)
        super().mouseMoveEvent(event)


class AddDeviceSheet(QDialog):
    def __init__(self, issued, origin, translate, parent=None, client=None):
        super().__init__(parent)
        install()
        from lifetime_connection import connection_line
        from lifetime_tasks import Tasks
        from PyQt6.QtCore import QTimer
        import datetime as dt
        self.issued, self.t, self.client = issued, translate, client
        self._busy = False
        self._operation = None
        self.request = None
        self.finished_connection = False
        self.setWindowTitle(translate('Add a computer'))
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        title = QLabel(translate('Add a computer'))
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        layout.addWidget(title)
        note = QLabel(translate('On the computer, open Lifetime, choose Connect, and enter this code.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.code = QLineEdit(issued['code'])
        self.code.setReadOnly(True)
        self.code.setAccessibleName(translate('Connection code'))
        self.code.setStyleSheet('font-family: Consolas, monospace; font-size: 30px; font-weight: 600; color: #FFC93C; padding: 14px 8px;')
        code_row = QHBoxLayout()
        code_row.addWidget(self.code, 1)
        self.copy_code = QPushButton(translate('Copy'))
        self.copy_code.setMinimumHeight(44)
        self.copy_code.clicked.connect(lambda: QApplication.clipboard().setText(issued['code']))
        code_row.addWidget(self.copy_code)
        layout.addLayout(code_row)
        expiry = dt.datetime.fromisoformat(issued['expires_at'].replace('Z', '+00:00')).astimezone()
        # A code lasts 24 hours by default, so a bare "23:58" reads as a minute from now. Say the day unless it is today.
        if expiry.date() == dt.datetime.now().astimezone().date():
            self.expiry = QLabel(translate('Valid until {time}', time=expiry.strftime('%H:%M')))
        else:
            self.expiry = QLabel(translate('Valid until {date}, {time}', date=expiry.strftime('%Y-%m-%d'), time=expiry.strftime('%H:%M')))
        self.expiry.setStyleSheet('color: #9FB0CC;')
        layout.addWidget(self.expiry)
        self.other_network = QPushButton(translate('Other network?'))
        self.other_network.setFlat(True)
        self.other_network.setMinimumHeight(44)
        self.other_network.setCheckable(True)
        layout.addWidget(self.other_network, alignment=Qt.AlignmentFlag.AlignLeft)
        self.line_panel = QWidget()
        line_row = QHBoxLayout(self.line_panel)
        line_row.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit(connection_line(origin, issued['code']))
        self.line.setReadOnly(True)
        self.line.setAccessibleName(translate('Connection line'))
        line_row.addWidget(self.line, 1)
        self.copy_line = QPushButton(translate('Copy'))
        self.copy_line.clicked.connect(lambda: QApplication.clipboard().setText(self.line.text()))
        line_row.addWidget(self.copy_line)
        layout.addWidget(self.line_panel)
        self.line_panel.hide()
        self.other_network.toggled.connect(self.line_panel.setVisible)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.decisions = QWidget()
        row = QHBoxLayout(self.decisions)
        self.approve = QPushButton(translate('Approve'))
        self.approve.setObjectName('primary')
        self.deny = QPushButton(translate('Deny'))
        self.approve.clicked.connect(lambda: self.decide('approve'))
        self.deny.clicked.connect(lambda: self.decide('deny'))
        row.addWidget(self.approve)
        row.addWidget(self.deny)
        layout.addWidget(self.decisions)
        self.decisions.hide()
        self.done = QPushButton(translate('Done'))
        self.done.clicked.connect(self.accept)
        layout.addWidget(self.done)
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.finished.connect(lambda: self.timer.stop())

    def poll(self):
        if not self.client or self._busy or self.finished_connection:
            return
        self._busy, self._operation = True, 'poll'
        self.tasks.submit(lambda: self.client.call('pending_enrollments'))

    def decide(self, operation):
        if self._busy or not self.request:
            return
        self._busy, self._operation = True, operation
        self.approve.setEnabled(False)
        self.deny.setEnabled(False)
        request_id = self.request['request_id']
        self.tasks.submit(lambda: self.client.call(operation, request_id=request_id))

    def completed(self, number, result):
        self._busy = False
        self.approve.setEnabled(True)
        self.deny.setEnabled(True)
        self.error.clear()
        if self._operation == 'poll':
            self.request = result[0] if result else None
            self.decisions.setVisible(bool(self.request))
            self.message.setText(self.t('{name} wants to connect', name=self.request['name']) if self.request else '')
        elif self._operation == 'approve':
            self.finished_connection = True
            self.timer.stop()
            self.message.setText(self.t('{name} connected', name=self.request['name']))
            self.decisions.hide()
            self.done.setFocus()
        else:
            self.message.setText(self.t('Connection declined.'))
            self.decisions.hide()
            self.request = None

    def failed(self, number, error):
        self._busy = False
        self.approve.setEnabled(True)
        self.deny.setEnabled(True)
        self.error.setText(self.t("Can't reach the Lifetime server. Make sure it is running."))


def table_action(button):
    """Give native row actions breathing room without clipping their padded text."""
    wrapper = QWidget()
    wrapper.setObjectName('rowAction')
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(6, 4, 6, 4)
    layout.addWidget(button)
    wrapper.setMinimumWidth(max(112, button.sizeHint().width() + 24))
    return wrapper
