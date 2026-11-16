"""Native presentation primitives for Lifetime Server; no store or network I/O."""
import math
import os
import sys

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
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
    override = os.environ.get('LIFETIME_REDUCE_MOTION')
    if override in ('0', '1'):
        return override == '1'
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


class GroupField(QComboBox):
    """A text field with a dropdown of the groups already in use, like a spreadsheet list: pick one or type a new one.

    Speaks the same small API as the QLineEdit identity fields (text, setText, clear, textEdited).
    """
    textEdited = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().textEdited.connect(self.textEdited)
        self.activated.connect(lambda _index: self.textEdited.emit(self.currentText()))

    def text(self):
        return self.currentText()

    def setText(self, value):
        self.setEditText(value or '')

    def clear(self):
        self.setEditText('')            # keep the list; only empty the text

    def setMaxLength(self, length):
        self.lineEdit().setMaxLength(length)

    def setPlaceholderText(self, text):
        self.lineEdit().setPlaceholderText(text)

    def set_groups(self, groups):
        text = self.currentText()
        self.blockSignals(True)
        QComboBox.clear(self)
        self.addItems([group for group in groups if group])
        self.setEditText(text)
        self.blockSignals(False)


class DeviceDetailsDialog(QDialog):
    """Edit a computer's name, asset tag, group, location and deployment date in one place (the ⋯ menu's Edit details)."""

    FIELDS = (('display_name', 'Display name'), ('asset_tag', 'Asset tag'), ('department', 'Group'),
              ('location', 'Location'), ('deployment_date', 'Deployment date'))

    def __init__(self, identity, groups, translate, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QDialogButtonBox, QFormLayout
        self.t, self._original = translate, dict(identity or {})
        self.setWindowTitle(translate('Edit details'))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(10)
        self.fields = {}
        for field, key in self.FIELDS:
            edit = GroupField() if field == 'department' else QLineEdit()
            if field == 'department':
                edit.set_groups(groups)
            edit.setMaxLength(4096)
            edit.setText(self._original.get(field, ''))
            edit.setAccessibleName(translate(key))
            label = QLabel(translate(key))
            label.setBuddy(edit)
            form.addRow(label, edit)
            self.fields[field] = edit
        self.fields['deployment_date'].setPlaceholderText(translate('YYYY-MM-DD (optional)'))
        self.fields['department'].setPlaceholderText(translate('Choose or type a group'))
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(T('color: #FF8A8A;'))
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.save = buttons.addButton(translate('Save'), QDialogButtonBox.ButtonRole.AcceptRole)
        self.save.setObjectName('primary')
        buttons.addButton(translate('Cancel'), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def identity(self):
        """The full identity to save: the edited fields, plus any other field the computer already had."""
        return dict(self._original, **{field: edit.text().strip() for field, edit in self.fields.items()})

    def _save(self):
        from lifetime_protocol import validate_identity
        try:
            validate_identity(self.identity())
        except ValueError:
            self.error.setText(self.t('Check the details. The deployment date must look like 2026-11-16, or be left empty.'))
            self.error.show()
            return
        self.accept()


class GroupsDialog(QDialog):
    """Create, rename and remove groups, including ones no computer is in yet.

    `run(operation, arguments, done)` sends one admin call in the background and calls `done(result, success)`.
    """

    def __init__(self, translate, run, parent=None, can_edit=True):
        super().__init__(parent)
        from PyQt6.QtWidgets import QListWidget
        self.t, self.run, self.groups, self.changed = translate, run, [], False
        self.setWindowTitle(translate('Manage groups'))
        self.resize(480, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        note = QLabel(translate('Create groups here first, then pick them for any computer. A computer is in a group when its Group field matches the name.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        add_row = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setMaxLength(120)
        self.new_name.setPlaceholderText(translate('New group name'))
        self.new_name.setAccessibleName(translate('New group name'))
        self.add_button = QPushButton(translate('Add group'))
        self.add_button.setObjectName('primary')
        self.add_button.clicked.connect(self._add)
        self.new_name.returnPressed.connect(self._add)
        add_row.addWidget(self.new_name, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)
        self.list = QListWidget()
        self.list.setAccessibleName(translate('Groups'))
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())
        layout.addWidget(self.list, 1)
        from lifetime_widgets_dashboard import EmptyState
        self.empty = EmptyState('setup', translate('No groups yet'),
                                translate('Add a group above, then pick it for any computer.'))
        self.empty.hide()
        layout.addWidget(self.empty, 1, Qt.AlignmentFlag.AlignTop)
        actions = QHBoxLayout()
        self.rename_button = QPushButton(translate('Rename…'))
        self.rename_button.clicked.connect(self._rename)
        self.remove_button = QPushButton(translate('Remove'))
        self.remove_button.clicked.connect(self._remove)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        close = QPushButton(translate('Close'))
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(T('color: #FF8A8A;'))
        layout.addWidget(self.message)
        self.can_edit = can_edit
        for widget in (self.new_name, self.add_button):
            widget.setEnabled(can_edit)
        self._update_buttons()
        self._call('groups', {})

    def _call(self, operation, arguments):
        self.run(operation, arguments, lambda result, ok: self._done(operation, result, ok))

    def _done(self, operation, result, success):
        from PyQt6 import sip
        if sip.isdeleted(self):         # the answer came after the dialog was closed
            return
        if not success or not isinstance(result, list):
            self.message.setText(self.t("Couldn't update groups. Check that the Lifetime server is running."))
            return
        self.changed = self.changed or operation != 'groups'
        self.message.clear()
        self.show_groups(result)

    def show_groups(self, groups):
        current = self.selected()
        self.groups = groups
        self.list.clear()
        for group in groups:
            count = group.get('computers', 0)
            self.list.addItem(self.t('{name}  ·  {count} computers', name=group['name'], count=count) if count != 1
                              else self.t('{name}  ·  1 computer', name=group['name']))
        names = [group['name'] for group in groups]
        self.list.setVisible(bool(groups))
        self.empty.setVisible(not groups)
        if current and current['name'] in names:
            self.list.setCurrentRow(names.index(current['name']))
        self._update_buttons()

    def selected(self):
        row = self.list.currentRow()
        return self.groups[row] if 0 <= row < len(self.groups) else None

    def _update_buttons(self):
        chosen = self.selected()
        self.rename_button.setEnabled(bool(self.can_edit and chosen))
        empty = bool(chosen and chosen.get('created') and not chosen.get('computers'))
        self.remove_button.setEnabled(bool(self.can_edit and empty))
        self.remove_button.setToolTip('' if not chosen or empty else
                                      self.t('Computers are still in this group. Move them to another group first.'))

    def _add(self):
        name = self.new_name.text().strip()
        if name:
            self.new_name.clear()
            self._call('add_group', dict(group=name))

    def _rename(self):
        from PyQt6.QtWidgets import QInputDialog
        chosen = self.selected()
        if not chosen:
            return
        name, ok = QInputDialog.getText(self, self.t('Rename group'),
                                        self.t('New name for {name}. Its computers and settings move with it.', name=chosen['name']),
                                        text=chosen['name'])
        if ok and name.strip() and name.strip() != chosen['name']:
            self._call('rename_group', dict(group=chosen['name'], new_name=name.strip()))

    def _remove(self):
        chosen = self.selected()
        if chosen and chosen.get('created') and not chosen.get('computers'):
            self._call('remove_group', dict(group=chosen['name']))


class AddDeviceSheet(QDialog):
    def __init__(self, issued, origin, translate, parent=None, client=None, groups=()):
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
        decision_layout = QVBoxLayout(self.decisions)
        decision_layout.setContentsMargins(0, 0, 0, 0)
        # Ask for the group while the computer is being approved, instead of leaving it to the device's detail panel.
        group_row = QHBoxLayout()
        group_label = QLabel(translate('Group (optional)'))
        self.group_choice = QComboBox()
        self.group_choice.setEditable(True)
        self.group_choice.setMinimumWidth(260)
        self.group_choice.setAccessibleName(translate('Group'))
        self.group_choice.addItem('')
        for group in groups or ():
            self.group_choice.addItem(group)
        self.group_choice.lineEdit().setPlaceholderText(translate('Choose or type a group'))
        self.group_choice.lineEdit().setMaxLength(4096)
        group_label.setBuddy(self.group_choice)
        group_row.addWidget(group_label)
        group_row.addWidget(self.group_choice, 1)
        decision_layout.addLayout(group_row)
        row = QHBoxLayout()
        decision_layout.addLayout(row)
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
        if operation != 'approve':
            self.tasks.submit(lambda: self.client.call(operation, request_id=request_id))
            return
        group = self.group_choice.currentText().strip()
        client = self.client

        def approve():
            result = client.call('approve', request_id=request_id)
            if not group:
                return result
            # Identity is replaced as a whole, so keep whatever the computer already has and only set its group.
            try:
                identity = dict(client.call('device', device_id=result['device_id']).get('identity') or {})
                identity['department'] = group
                client.call('identity', device_id=result['device_id'], identity=identity)
            except Exception:
                return dict(result, group=group, group_saved=False)
            return dict(result, group=group, group_saved=True)
        self.tasks.submit(approve)

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
            text = self.t('{name} connected', name=self.request['name'])
            if isinstance(result, dict) and result.get('group'):
                if result.get('group_saved'):
                    text += '\n' + self.t('Group: {group}', group=result['group'])
                else:
                    text += '\n' + self.t("The group was not saved. Set it on the Devices page under Group.")
            self.message.setText(text)
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


class CycleButton(QPushButton):
    """One compact header button that shows the current choice; each click moves to the next (wrapping round).

    The old label slides up and fades while the new one rises in (skipped under reduced motion). The surface under it is
    the shared glass/classic painter, attached by the window; this paints only the content and the keyboard focus ring.
    `draw(painter, rect, index)` paints choice `index` centred in `rect`.
    """
    cycled = pyqtSignal(int)

    def __init__(self, count, draw, index=0, size=(48, 40), radius=8):
        super().__init__()
        self.radius = radius
        self.count, self.draw, self.index, self.previous, self.phase = count, draw, index, index, 1.0
        self.keyboard = False
        self.setFixedSize(*size)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._frame)
        self.clicked.connect(lambda: self.set_index((self.index + 1) % self.count, True))

    def _frame(self, value):
        self.phase = float(value)
        self.update()

    def set_index(self, index, notify=False):
        if index != self.index:
            self.previous, self.index = self.index, index
            self.animation.stop()
            if reduced_motion() or not self.isVisible():
                self.phase = 1.0
            else:
                self.phase = 0.0
                self.animation.start()
            self.update()
        if notify:
            self.cycled.emit(self.index)

    def focusInEvent(self, event):
        self.keyboard = event.reason() not in (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.PopupFocusReason)
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self.keyboard = False
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.keyboard = True
            self.click()
            event.accept()
        else:
            self.keyboard = True
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        base = 1.0 if self.isEnabled() else 0.45
        if self.phase < 1.0:
            steps = ((self.previous, 1 - self.phase, -7 * self.phase), (self.index, self.phase, 7 * (1 - self.phase)))
        else:
            steps = ((self.index, 1.0, 0.0),)
        for index, alpha, shift in steps:
            p.setOpacity(base * alpha)
            p.save()
            p.translate(0, shift)
            self.draw(p, rect, index)
            p.restore()
        if self.hasFocus() and self.keyboard:
            p.setOpacity(1.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(C['accent']), 2))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), self.radius - 1, self.radius - 1)
