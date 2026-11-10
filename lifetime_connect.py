"""One-code connection page. Certificate search never sends application data."""
import datetime as dt
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import time
from PyQt6.QtCore import Qt, QTimer, QPointF, QPropertyAnimation, QRectF, QParallelAnimationGroup, QVariantAnimation
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (QWidget, QFrame, QLabel, QLineEdit, QPushButton,
                            QCheckBox, QVBoxLayout, QHBoxLayout, QMessageBox,
                            QGraphicsOpacityEffect)
from lifetime_tasks import Tasks
from lifetime_shell import PageStack, PulseDot
from lifetime_i18n import tr, TABLES, datetime_text
from lifetime_i18n_qt import QLabel, QPushButton
from lifetime_company import read_status
from lifetime_settings import read_settings
from lifetime_theme import tokens

NO_SERVER = 'No server found with this code. Check that the server is on and that this computer is on the same network.'
PERMISSION = 'Windows permission is needed to finish.'


def locate_server(value):
    from lifetime_connection import PREFIX, parse, normalize, bootstrap, find_server
    from lifetime_discovery import discover
    if value.strip().startswith(PREFIX):
        origin, code = parse(value)
        return bootstrap(origin, code)
    code = normalize(value)
    try:
        candidates = discover()
    except OSError:
        candidates = []
    for server in candidates:
        try:
            return bootstrap(server['address'], code)
        except (OSError, ValueError):
            continue
    return find_server(code)


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_agent(action, policy, bundle=None, directory=None, display_name=None):
    """One waited elevation, including the packaged installer when present."""
    from lifetime_agent import reporting_directory
    from lifetime_permissions import protect_directory
    root = Path(directory) if directory is not None else reporting_directory()
    with tempfile.TemporaryDirectory(prefix='Lifetime-connect-') as temporary:
        private = Path(temporary)
        if sys.platform == 'win32':
            import csv
            identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                                      text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            sid = next(csv.reader([identity.stdout.strip()]))[1]
            subprocess.run(['icacls', str(private), '/inheritance:r', '/grant:r',
                            '*' + sid + ':(OI)(CI)(F)', '*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            protect_directory(private)
        args = ['--directory', str(root)]
        if action == 'finish':
            path = private / 'connection.txt'
            path.write_text(bundle, encoding='utf-8')
            args += ['--enroll-bundle-file', str(path), '--background', '--display-name', display_name or socket.gethostname()]
        elif action == 'disconnect':
            args += ['--disable-reporting']
        else:
            args += ['--set-policy']
        if action != 'disconnect':
            args += ['--policy-' + key for key, enabled in policy.items() if enabled]
        if getattr(sys, 'frozen', False):
            executable = Path(sys.executable).with_name('LifetimeAgent.exe')
            if not executable.is_file():
                raise FileNotFoundError('LifetimeAgent.exe is missing beside Lifetime.exe')
            command = [str(executable)] + args
            installer = Path(sys.executable).with_name('install_agent_windows.ps1')
            if action == 'finish' and installer.is_file() and sys.platform == 'win32':
                command = ['powershell.exe', '-NoProfile', '-File', str(installer), '-Mode', 'Packaged',
                           '-EnableReporting', '-BundleFile', str(path), '-Start', '-DisplayName', display_name or socket.gethostname()]
                for key, flag in dict(history='TrackHistory', resources='ResourceReadings', health='HealthReadings', control='PowerControl').items():
                    if policy.get(key):
                        command.append('-' + flag)
        else:
            command = [sys.executable, str(Path(__file__).with_name('lifetime_agent.py'))] + args
        if sys.platform == 'win32':
            # Start-Process receives one correctly quoted native argument string.
            arguments = subprocess.list2cmdline(command[1:])
            script = ("$ErrorActionPreference='Stop'; try { $p = Start-Process -FilePath " +
                      _ps_quote(command[0]) + ' -ArgumentList ' + _ps_quote(arguments) +
                      # Not -Wait: it also waits for the detached agent, which never exits.
                      ' -Verb RunAs -PassThru -WindowStyle Hidden; $null = $p.Handle; $p.WaitForExit(); exit $p.ExitCode } catch { if ($_.Exception.NativeErrorCode -eq 1223 -or $_.Exception.InnerException.NativeErrorCode -eq 1223) { exit 1223 }; exit 1 }')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                    creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            if result.returncode == 1223:
                raise PermissionError(PERMISSION)
            if result.returncode:
                raise RuntimeError('Agent operation failed')
        else:
            subprocess.run(command, check=True)
            if action == 'finish':
                try:
                    subprocess.run(['systemctl', '--user', 'enable', '--now', 'lifetime-company-agent.service'],
                                   check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    return read_status(root)


class ConnectButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class SharingSwitch(QCheckBox):
    """Native checkbox keyboard/accessibility behavior, painted as a switch."""
    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tokens()['accent'] if self.isChecked() else tokens()['line']))
        p.drawRoundedRect(QRectF(2, 10, 40, 24), 12, 12)
        p.setBrush(QColor(tokens()['bg'] if self.isChecked() else '#FFFFFF'))
        p.drawEllipse(QRectF(24 if self.isChecked() else 6, 14, 16, 16))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(tokens()['muted']), 1))
            p.drawRoundedRect(QRectF(0.5, 7.5, 43, 29), 14, 14)


class StageMark(QWidget):
    """Drawn, not typed: a green tick when done, a yellow dot for the current step, nothing for later steps."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 22)
        self.state = 0      # 0 later, 1 current, 2 done

    def set_state(self, state):
        self.state = state
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state == 1:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tokens()['accent']))
            p.drawEllipse(QPointF(11, 11), 4, 4)
        elif self.state == 2:
            pen = QPen(QColor(tokens()['good']), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(5, 11.5), QPointF(9.5, 16), QPointF(17, 6.5)])


class PacketFlight(QWidget):
    """Parcels leave this computer, cross to the server, and a green reply comes back."""
    PARCELS = 4

    def __init__(self, reduce_motion=False):
        super().__init__()
        self.setFixedHeight(120)
        self.reduce_motion = reduce_motion
        self.phase = 0.35
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(2400)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._step)

    def _step(self, value):
        self.phase = value
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.reduce_motion:
            self.anim.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.anim.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle, left, right = self.height() / 2, 74.0, self.width() - 74.0
        line = QPen(QColor(tokens()['line']), 2, Qt.PenStyle.DashLine)
        p.setPen(line)
        p.drawLine(QPointF(left, middle), QPointF(right, middle))
        outline = QPen(QColor(tokens()['text']), 2)
        outline.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(outline)
        p.setBrush(QColor(tokens()['bg']))
        # This computer
        p.drawRoundedRect(QRectF(24, middle - 24, 46, 32), 4, 4)
        p.drawLine(QPointF(16, middle + 16), QPointF(78, middle + 16))
        # The server: three stacked units with a live light on each
        x = self.width() - 70.0
        for row in range(3):
            y = middle - 28 + row * 19
            p.setPen(outline)
            p.setBrush(QColor(tokens()['bg']))
            p.drawRoundedRect(QRectF(x, y, 46, 16), 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            lit = int(self.phase * 6) % 3 == row
            p.setBrush(QColor(tokens()['good'] if lit else tokens()['line']))
            p.drawEllipse(QPointF(x + 37, y + 8), 2.5, 2.5)
        span = right - left
        p.setPen(Qt.PenStyle.NoPen)
        for index in range(self.PARCELS):
            t = (self.phase + index / self.PARCELS) % 1.0
            eased = t * t * (3 - 2 * t)
            p.setOpacity(min(1.0, min(t, 1 - t) * 8))
            p.setBrush(QColor(tokens()['accent']))
            cx = left + span * eased
            hop = 10 * (4 * eased * (1 - eased))
            p.drawRoundedRect(QRectF(cx - 7, middle - 7 - hop, 14, 14), 3, 3)
            p.setBrush(QColor(tokens()['bg']))
            p.drawRect(QRectF(cx - 1, middle - 7 - hop, 2, 14))     # the parcel's tape
        reply = (self.phase * 2) % 1.0
        p.setOpacity(min(1.0, min(reply, 1 - reply) * 8))
        p.setBrush(QColor(tokens()['good']))
        p.drawEllipse(QPointF(right - span * reply, middle + 13), 4, 4)
        p.setOpacity(1.0)


class ConnectPage(QWidget):
    def __init__(self, parent=None, directory=None, language=None, machine_root=None):
        super().__init__(parent)
        from lifetime_theme import install
        install()
        self.setObjectName('connectPage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#connectPage {background: ' + tokens()['bg'] + ';}')
        self.directory, self.machine_root, self.language = directory, machine_root, language
        self._busy = False
        self._operation = None
        self.bundle = None
        self._origin = None
        self._line_code = None
        self.server_name = ''
        self.server_address = ''
        self.status = {}
        self.editing = False
        self.reduce_motion = os.environ.get('LIFETIME_REDUCE_MOTION') == '1' or bool(os.environ.get('REDUCE_MOTION'))
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 24, 0, 24)
        outer.addStretch()
        self.column = QWidget()
        self.column.setMaximumWidth(560)
        self.column.setMinimumWidth(0)
        box = QVBoxLayout(self.column)
        box.setContentsMargins(0, 0, 0, 0)
        self.steps = PageStack()
        box.addWidget(self.steps)
        box.addStretch()
        outer.addWidget(self.column, 1)
        outer.addStretch()
        self.forms = []
        for _ in range(5):
            panel = QFrame()
            panel.setObjectName('insetPanel')
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(16)
            self.forms.append(layout)
            self.steps.addWidget(panel)
        f = self.forms[0]
        self.note(f, 'Connect to a server', 'pageTitle')
        self.note(f, 'Link this computer to a Lifetime Server on your network so it can see how this computer is doing.')
        f.addWidget(PacketFlight(self.reduce_motion))
        # The tutorial is captioned onto the drawing: each end says what to do at that computer.
        ends = QHBoxLayout()
        for heading, detail, side in (('On this computer', 'Type that code below.', Qt.AlignmentFlag.AlignLeft),
                                      ('On the server', 'Press Add device to get a code.', Qt.AlignmentFlag.AlignRight)):
            end = QVBoxLayout()
            end.setSpacing(0)
            self.note(end, heading).setAlignment(side)
            self.note(end, detail, 'caption').setAlignment(side)
            ends.addLayout(end, 1)
        f.addLayout(ends)
        self.code_error = self.note(f, '')      # above the field, where it is seen without scrolling
        self.code_error.hide()
        self.code = QLineEdit()
        self.code.setMaxLength(2300)
        self.code.setPlaceholderText('LTS-XXXX-XXXX-XXXX-XXXX-XXXX')
        self.code.setAccessibleName(self.t('Connection code'))
        self.code.setStyleSheet('QLineEdit {font-family: Consolas, monospace; font-size: 26px; color: ' + tokens()['accent'] + '; background: ' + tokens()['card'] + '; border: 1px solid ' + tokens()['line'] + '; padding: 14px 8px;}')
        f.addWidget(self.code)
        self.connect_button = self.button(f, 'Connect', self.connect_server, True)
        self.connect_button.setEnabled(False)
        self.code.textChanged.connect(self.format_input)
        self.code.returnPressed.connect(self.connect_server)
        self.note(f, 'Next you choose what to share. You can change it or disconnect any time.', 'caption')
        self.pulse(self.forms[1], 'Looking for your server...', True)
        self.forms[1].addWidget(PacketFlight(self.reduce_motion))
        f = self.forms[2]
        self.review_title = self.pulse(f, '', True)
        self.review_address = self.note(f, '', 'caption')
        self.note(f, 'Choose what this server can see.')
        self.awake = self.choice(f, 'Awake time', 'Required to connect.', True)
        self.awake.setEnabled(False)
        self.history = self.choice(f, 'Awake history by day', 'Daily totals of awake time.', True)
        self.resources = self.choice(f, 'Computer specs and live performance', 'Processor, memory, disk, battery and network.', True)
        settings = read_settings(directory)
        self.health_allowed = bool(settings.get('completed') and settings.get('health'))
        self.health = self.choice(f, 'Health and crashes', 'Health summaries and crash counts.', self.health_allowed)
        self.health.setEnabled(self.health_allowed)
        self.control = self.choice(f, 'Let the server sleep or restart this computer', 'You get a one minute warning first.', False)
        self.review_error = self.note(f, '')
        self.review_error.hide()
        self.finish_button = self.button(f, 'Finish', self.finish, True)
        self.cancel_button = self.button(f, 'Cancel', self.cancel)
        self.cancel_button.setObjectName('quietAction')
        f = self.forms[3]
        self.loading_title = self.note(f, 'Connecting securely', 'pageTitle')
        self.waiting_text = self.note(f, '')
        f.addWidget(PacketFlight(self.reduce_motion))
        self.stage_rows = []
        for key in ('Server found', 'Connecting securely', 'Waiting for approval', "Sending this computer's details"):
            row = QHBoxLayout()
            mark = StageMark()
            text = QLabel(self.t(key))
            row.addWidget(mark)
            row.addWidget(text, 1)
            f.addLayout(row)
            self.stage_rows.append((mark, text))
        self.stage = 1
        self._report_started = None
        f = self.forms[4]
        self.connected_title = self.pulse(f, 'Connected', True)
        self.summary = self.note(f, '')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.unreachable = self.note(f, "Can't reach the server right now. Lifetime will keep trying.")
        self.unreachable.hide()
        self.button(f, 'Change what is shared', self.change_policy)
        self.button(f, 'Disconnect', self.disconnect)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.show_step(1)
        self.poll()

    def update_health(self, settings):
        self.health_allowed = bool(settings.get('completed') and settings.get('health'))
        self.health.setEnabled(self.health_allowed)
        if not self.health_allowed:
            self.health.setChecked(False)

    def t(self, key, **fmt):
        if self.language:
            return TABLES[self.language][key].format(**fmt)
        return tr(key, **fmt)

    def note(self, layout, key, kind=''):
        label = QLabel(self.t(key) if key else '')
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setMinimumWidth(0)
        if kind:
            label.setObjectName(kind)
        layout.addWidget(label)
        return label

    def button(self, layout, key, action, primary=False):
        button = ConnectButton(self.t(key))
        button.setMinimumHeight(44)
        if primary:
            button.setObjectName('primaryAction')
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def pulse(self, layout, key, active):
        row = QHBoxLayout()
        dot = PulseDot(reduce_motion=self.reduce_motion)
        dot.set_active(active)
        row.addWidget(dot)
        label = QLabel(self.t(key) if key else '')
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName('pageTitle')
        label.setWordWrap(True)
        label.dot = dot
        row.addWidget(label, 1)
        layout.addLayout(row)
        return label

    def choice(self, layout, title, detail, checked):
        row = QHBoxLayout()
        copy = QVBoxLayout()
        self.note(copy, title)
        self.note(copy, detail, 'caption')
        row.addLayout(copy, 1)
        switch = SharingSwitch()
        switch.setAccessibleName(self.t(title))
        switch.setChecked(checked)
        switch.setFixedSize(44, 44)
        row.addWidget(switch)
        layout.addLayout(row)
        return switch

    def show_step(self, step):
        if getattr(self, "step", None) == step:
            return
        if hasattr(self, 'animation'):
            self.animation.stop()
            self.animation.deleteLater()
            del self.animation
        if hasattr(self, '_outgoing') and self._outgoing is not None:
            self._outgoing.deleteLater()
            self._outgoing = None
        previous = self.steps.currentWidget()
        snapshot = previous.grab() if previous and self.isVisible() and not self.reduce_motion else None
        old_geometry = previous.geometry() if previous else None
        if previous:
            previous.setGraphicsEffect(None)
        self.step = step
        self.steps.setCurrentIndex(step - 1)
        incoming = self.steps.currentWidget()
        if snapshot is not None:
            self.animation = QParallelAnimationGroup(self)
            effect = QGraphicsOpacityEffect(incoming)
            incoming.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b'opacity', self.animation)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setDuration(180)
            self.animation.addAnimation(fade)
            overlay = QLabel(self.steps)
            overlay.setPixmap(snapshot)
            overlay.setGeometry(old_geometry)
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            overlay.show()
            overlay.raise_()
            self._outgoing = overlay
            outgoing = QGraphicsOpacityEffect(overlay)
            overlay.setGraphicsEffect(outgoing)
            fade_out = QPropertyAnimation(outgoing, b'opacity', self.animation)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setDuration(180)
            self.animation.addAnimation(fade_out)
            def finished():
                incoming.setGraphicsEffect(None)
                overlay.deleteLater()
                if self._outgoing is overlay:
                    self._outgoing = None
            self.animation.finished.connect(finished)
            self.animation.start()
        if step == 1:
            self.code.setFocus()
        elif step == 3:
            self.finish_button.setFocus()

    def set_stage(self, stage):
        """1 connecting, 2 approval, 3 sending. Every row always names its own state in words."""
        self.stage = stage
        titles = {1: 'Connecting securely', 2: 'Waiting for approval', 3: "Sending this computer's details"}
        self.loading_title.setText(self.t(titles[stage]))
        for index, (mark, text) in enumerate(self.stage_rows):
            done, current = index < stage, index == stage
            mark.set_state(2 if done else 1 if current else 0)
            text.setStyleSheet('color: ' + (tokens()['text'] if current else tokens()['muted']) + ';')

    def format_input(self):
        from lifetime_connection import PREFIX, parse, normalize, format_code
        value = self.code.text()
        if value.strip().startswith(PREFIX):
            try:
                self._origin, self._line_code = parse(value)
                self.code.blockSignals(True)
                self.code.setText(self._line_code)
                self.code.blockSignals(False)
                valid = True
            except ValueError:
                valid = False
        else:
            cursor = self.code.cursorPosition()
            formatted = format_code(value)
            if formatted != value:
                self.code.blockSignals(True)
                self.code.setText(formatted)
                self.code.setCursorPosition(min(len(formatted), cursor + len(formatted) - len(value)))
                self.code.blockSignals(False)
            if formatted != self._line_code:
                self._origin = None
                self._line_code = None
            try:
                normalize(formatted)
                valid = True
            except ValueError:
                valid = False
        self.connect_button.setEnabled(valid and not self._busy)

    def submit(self, operation, function):
        if self._busy:
            return
        self._busy, self._operation = True, operation
        self.finish_button.setEnabled(False)
        self.cancel_button.setEnabled(False)    # a running step cannot be cancelled, so do not offer it
        self.tasks.submit(function)

    def connect_server(self):
        if not self.connect_button.isEnabled() or self._busy:
            return
        value = self.code.text()
        if self._origin:
            from lifetime_connection import connection_line
            value = connection_line(self._origin, value)
        self.code_error.hide()
        self.show_step(2)
        self.submit('find', lambda: locate_server(value))

    def policy(self):
        return dict(history=self.history.isChecked(), resources=self.resources.isChecked(),
                    health=self.health.isChecked() and self.health_allowed, control=self.control.isChecked())

    def finish(self):
        if self._busy:
            return
        self.review_error.hide()
        action = 'policy' if self.editing else 'finish'
        policy, bundle, root = self.policy(), self.bundle, self.machine_root
        if action == 'finish':
            self.waiting_text.setText('')
            self.set_stage(1)
            self.show_step(4)
        self.submit(action, lambda: run_agent(action, policy, bundle, root, socket.gethostname()))

    def cancel(self):
        if self._busy:
            return
        self.show_step(5 if self.editing else 1)
        self.editing = False
        self.bundle = None

    def change_policy(self):
        if self._busy:
            return
        policy = self.status.get('policy') or {}
        for key in ('history', 'resources', 'health', 'control'):
            getattr(self, key).setChecked(bool(policy.get(key)) and (key != 'health' or self.health_allowed))
        self.editing = True
        self.review_title.setText(self.t('Connected to {server}', server=self.server_name))
        self.review_address.setText(self.server_address)
        self.show_step(3)

    def disconnect(self):
        if self._busy:
            return
        if QMessageBox.question(self, self.t('Disconnect'), self.t('Disconnect from this server?')) == QMessageBox.StandardButton.Yes:
            self.submit('disconnect', lambda: run_agent('disconnect', {}, directory=self.machine_root))

    def poll(self):
        if not self._busy and self.step in (1, 4, 5):
            self.submit('status', lambda: read_status(self.machine_root))

    def completed(self, number, result):
        operation = self._operation
        self._busy = False
        self.finish_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.format_input()
        if operation == 'find':
            from lifetime_tls import decode_bundle
            from urllib.parse import urlsplit
            self.bundle = result
            trust = decode_bundle(result)
            self.server_address = trust['address']
            self.server_name = urlsplit(self.server_address).hostname
            self.review_title.setText(self.t('Found {server}', server=self.server_name))      # found, not connected yet
            self.review_address.setText(self.server_address)
            self.show_step(3)
        elif operation == 'disconnect':
            self.status = {}
            self.bundle = None
            self.show_step(1)
        elif operation in ('status', 'finish', 'policy'):
            self.status = result or {}
            if self.status.get('status') in ('denied', 'revoked', 'invitation-used', 'invalid-code'):
                self._code_rejected(self.status.get('status'))
            elif self.status.get('enabled'):
                self.server_address = self.status.get('server') or self.server_address
                from urllib.parse import urlsplit
                self.server_name = self.status.get('server_name') or urlsplit(self.server_address).hostname or ''
                state = self.status.get('status')
                if state in ('denied', 'revoked', 'invitation-used', 'invalid-code'):
                    self._code_rejected(state)
                elif self.status.get('phase') in ('pending', 'enrolling') or state in ('pending', 'enrolling'):
                    self.waiting_text.setText(self.t('{server} needs to approve this computer.', server=self.server_name))
                    self.set_stage(2)
                    self.show_step(4)
                elif self.step == 4 and state == 'connected' and not self.status.get('reported') and not self._report_timed_out():
                    # Approved is not finished: stay here until the server has this computer's details.
                    self.waiting_text.setText(self.t('Almost done'))
                    self.set_stage(3)
                else:
                    self._report_started = None
                    self.render_status()
                    self.show_step(5)
            elif self.step in (4, 5) or operation != 'status':
                self.show_step(1)
            self.editing = False

    def _report_timed_out(self):
        if self._report_started is None:
            self._report_started = time.monotonic()
        return time.monotonic() - self._report_started > 60     # never trap someone on this screen

    def render_status(self):
        names = ['Awake time']
        for key, title in (('history', 'Awake history by day'), ('resources', 'Computer specs and live performance'),
                           ('health', 'Health and crashes'), ('control', 'Let the server sleep or restart this computer')):
            if (self.status.get('policy') or {}).get(key):
                names.append(title)
        self.summary.setText(self.server_name + '\n' + self.server_address + '\n' +
                             self.t('Last report: {time}', time=self._local_time(self.status.get('last_sync')) or self.t('Not available')) +
                             '\n\n' + self.t('Shared: {items}', items='\n'.join(self.t(name) for name in names)))
        self._set_reachable(self.status.get('status') not in ('offline', 'unavailable', 'error', 'retrying', 'backoff', 'unreachable'))

    def _code_rejected(self, state):
        """Back to code entry, saying why. The old code is of no use, so it is cleared once (never while a new one is typed)."""
        if self.step != 1:
            self.code.clear()
        self.show_step(1)
        self.code_error.setText(self.t('The server removed this computer. Ask for a new code on the server to connect again.'
                                       if state == 'revoked' else 'This code cannot be used. Ask for a new code on the server.'))
        self.code_error.show()

    @staticmethod
    def _local_time(stamp):
        """'2026-11-10T17:03:17Z' means little to a person: show their own local date and time."""
        if not stamp:
            return ''
        try:
            moment = dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).astimezone()
        except ValueError:
            return str(stamp)
        return str(datetime_text(moment))

    def _set_reachable(self, reachable):
        """The title and dot must agree with the line below them: never 'Connected' beside 'Can't reach the server'."""
        self.unreachable.setVisible(not reachable)
        self.connected_title.setText(self.t('Connected' if reachable else 'Not connected right now'))
        self.connected_title.dot.set_active(reachable)

    def failed(self, number, error):
        operation = self._operation
        self._busy = False
        self.finish_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.format_input()
        if operation == 'find':
            self.show_step(1)
            self.code_error.setText(self.t(NO_SERVER))
            self.code_error.show()
        elif operation in ('finish', 'policy', 'disconnect'):
            if operation == 'disconnect':
                self.change_policy()
            self.show_step(3)
            message = PERMISSION if error == 'PermissionError' else 'Could not finish connecting. Check that the server is on and try again.'
            self.review_error.setText(self.t(message))
            self.review_error.show()
        elif self.step == 5:
            self._set_reachable(False)
