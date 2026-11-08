"""Consent interview and settings, using the application's existing Qt palette."""
import os
from pathlib import Path
import subprocess
import sys
from lifetime_i18n import tr, language, set_language, on_language_change

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel,
                            QMessageBox, QPushButton, QRadioButton, QScrollArea,
                            QStackedWidget, QVBoxLayout, QWidget, QStyle, QStyleOptionButton)
from lifetime_i18n_qt import QLabel, QPushButton, QWidget, QDialog, QCheckBox, QRadioButton

from lifetime_settings import defaults, delete_history, normalize, save_settings, load_settings, update_settings

CHOICES = (
    ('awake', 'Awake time', 'Required. Counts time while this computer is awake.'),
    ('history', 'Daily and hourly history', 'Shows when and how long you use this computer.'),
    ('battery', 'Battery and power', 'Shows charge, power use and battery condition.'),
    ('network', 'Network', 'Shows connection type, link speed and transfer speed.'),
    ('wifi_name', 'Wi-Fi network name', 'Optional. Network names can reveal your location.'),
    ('graphics', 'Graphics card', 'Shows graphics models and available readings.'),
    ('resources', 'Computer resources', 'Shows CPU, memory and disk use.'),
)


def setup_style(tokens):
    t = tokens
    family = 'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'
    return f"""
        QDialog, QScrollArea, QStackedWidget, QWidget#page {{ background: {t['bg']}; }}
        QWidget {{ color: {t['text']}; font-family: '{family}'; font-size: 15px; }}
        QLabel {{ background: transparent; }}
        QLabel#title {{ font-size: 26px; font-weight: 600; }}
        QLabel#description, QLabel#progress {{ color: {t['muted']}; font-size: 13px; }}
        QLabel#summary {{ background: {t['card']}; padding: 20px; border-radius: 6px; }}
        QPushButton {{ background: {t['card']}; border: 1px solid {t['border']};
            border-radius: 8px; padding: 10px 18px; min-height: 22px; }}
        QPushButton:focus, QPushButton:hover {{ border-color: {t['accent']}; }}
        QPushButton:pressed {{ background: {t['track']}; }}
        QPushButton:disabled {{ color: {t['muted']}; }}
        QPushButton#primary {{ background: {t['accent']}; color: {t['bg']}; font-weight: 600; }}
        QPushButton#primary:hover {{ background: {t['accent_hover']}; }}
        QCheckBox, QRadioButton {{ spacing: 12px; min-height: 28px; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 20px; height: 20px;
            background: {t['card']}; border: 1px solid {t['muted']}; border-radius: 4px; }}
        QRadioButton::indicator {{ border-radius: 11px; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background: {t['accent']}; border: 1px solid {t['accent']}; }}
        QCheckBox:focus, QRadioButton:focus {{ color: {t['accent']}; }}
        QScrollArea {{ border: none; }}
        QScrollBar:vertical {{ width: 8px; background: {t['bg']}; }}
        QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


class ConsentCheckBox(QCheckBox):
    """Keep the familiar check mark at every DPI without a bitmap dependency."""
    def __init__(self, text, ink):
        super().__init__(text)
        self.ink = ink

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.ink), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        def point(x, y):
            return QPointF(rect.x()+rect.width()*x, rect.y()+rect.height()*y)
        painter.drawLine(point(.24, .5), point(.43, .7))
        painter.drawLine(point(.43, .7), point(.77, .3))


class SetupDialog(QDialog):
    def __init__(self, tokens, settings=None, directory=None, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.initial = normalize(settings or defaults())
        set_language(self.initial['language'])
        self.result_settings = None
        self.setWindowTitle(tr('Lifetime settings') if self.initial['completed'] else tr('Set up Lifetime'))
        self.setStyleSheet(setup_style(tokens))
        self.setMinimumSize(380, 400)
        self.resize(680, 850)
        screen = self.screen().availableGeometry()
        self.resize(min(self.width(), screen.width()-32), min(self.height(), screen.height()-64))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        self.progress = QLabel()
        self.progress.setObjectName('progress')
        layout.addWidget(self.progress)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.checks = {}
        language_page = self.page(tr('Language / Ngôn ngữ'),
                                  tr('Choose your language. You can change it at any time.'))
        language_group = QButtonGroup(self)
        self.language_buttons = {}
        for code, title in (('en', 'English'), ('vi', 'Tiếng Việt')):
            button = QRadioButton(tr(title))
            button.setMinimumHeight(44)
            button.setChecked(language() == code)
            button.toggled.connect(lambda checked, value=code: self.change_language(value) if checked else None)
            language_group.addButton(button)
            language_page.addWidget(button)
            self.language_buttons[code] = button
        language_page.addStretch()
        first = self.page(tr('What would you like to track?'),
            tr('Lifetime records how long this computer is awake. Everything stays on this computer. Nothing is uploaded.'))
        for key, title, explanation in CHOICES:
            box = ConsentCheckBox(tr(title), tokens['bg'])
            box.setChecked(self.initial[key])
            box.setEnabled(key != 'awake')
            box.setAccessibleDescription(tr(explanation))
            first.addWidget(box)
            description = self.text(tr(explanation), 'description')
            description.setContentsMargins(33, 0, 0, 8)
            first.addWidget(description)
            self.checks[key] = box
        first.addStretch()
        self.answers = {}
        for key, title, explanation in (
            ('rebuild', tr('Rebuild past history?'),
             tr('Reads retained System event logs on Windows or the system journal on Linux. Past totals may be incomplete if older logs are missing.')),
            ('autostart', tr('Start tracking at login?'),
             tr('Starts the awake-time tracker when you sign in. You can also start it yourself from Lifetime.'))):
            page = self.page(tr(title), tr(explanation))
            group = QButtonGroup(self)
            yes = QRadioButton(tr('Yes, rebuild from system logs') if key == 'rebuild' else tr('Yes, start tracking at login'))
            no = QRadioButton(tr('No, start with current tracking') if key == 'rebuild' else tr('No, I will start it myself'))
            group.addButton(yes)
            group.addButton(no)
            yes.setChecked(self.initial[key])
            no.setChecked(not self.initial[key])
            page.addWidget(yes)
            page.addWidget(no)
            page.addStretch()
            self.answers[key] = yes
        last = self.page(tr('Ready to start'), tr('You can change these choices at any time in Settings.'))
        self.summary = self.text('', 'summary')
        last.addWidget(self.summary)
        last.addStretch()
        self.notice = self.text(tr('Only the readings you choose will be collected.'), 'description')
        last.addWidget(self.notice)
        if self.initial['completed']:
            self.delete_button = QPushButton(tr('Delete collected history'))
            self.delete_button.clicked.connect(self.confirm_delete)
            layout.addWidget(self.delete_button, alignment=Qt.AlignmentFlag.AlignLeft)
        controls = QHBoxLayout()
        self.back = QPushButton(tr('Back'))
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex()-1))
        controls.addWidget(self.back)
        controls.addStretch()
        self.next = QPushButton(tr('Next'))
        self.next.setObjectName('primary')
        self.next.setDefault(True)
        self.next.clicked.connect(self.advance)
        controls.addWidget(self.next)
        layout.addLayout(controls)
        self.go(0)
        on_language_change(self.retranslate_content)

    def change_language(self, value):
        set_language(value)
        # Language is a preference, not consent to collect data.
        try:
            update_settings({'language': value}, self.directory)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
        if self.parent() and hasattr(self.parent(), 'switch_language'):
            self.parent().switch_language(value)

    def retranslate_content(self):
        if hasattr(self, 'next'):
            self.go(self.pages.currentIndex())
            self.language_buttons[language()].blockSignals(True)
            self.language_buttons[language()].setChecked(True)
            self.language_buttons[language()].blockSignals(False)

    @staticmethod
    def text(value, name=''):
        label = QLabel(value)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def page(self, title, explanation):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('page')
        page = QVBoxLayout(body)
        page.setContentsMargins(0, 0, 12, 0)
        page.setSpacing(8)
        page.addWidget(self.text(title, 'title'))
        intro = self.text(explanation)
        intro.setContentsMargins(0, 8, 0, 24)
        page.addWidget(intro)
        scroll.setWidget(body)
        self.pages.addWidget(scroll)
        return page

    def choices(self):
        value = {key: box.isChecked() for key, box in self.checks.items()}
        value.update({key: yes.isChecked() for key, yes in self.answers.items()})
        return normalize(dict(load_settings(self.directory), **value, completed=True, language=language()))

    def go(self, index):
        self.pages.setCurrentIndex(index)
        self.progress.setText(tr('step', step=index+1))
        self.back.setEnabled(index > 0)
        self.next.setText((tr('Save settings') if self.initial['completed'] else tr('Start')) if index == 4 else tr('Next'))
        if index == 4:
            value = self.choices()
            names = [tr(title) for key, title, _ in CHOICES if value[key]]
            self.summary.setText(tr('Tracking\n') + '\n'.join(names) + tr('\n\nPast history: ') +
                (tr('Rebuild from system logs') if value['rebuild'] else tr('Do not rebuild')) + tr('\nStart at login: ') +
                (tr('Yes') if value['autostart'] else tr('No')))
        self.pages.currentWidget().verticalScrollBar().setValue(0)

    def advance(self):
        if self.pages.currentIndex() < 4:
            self.go(self.pages.currentIndex()+1)
            return
        try:
            value = self.choices()
            save_settings(value, self.directory)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
            return
        self.result_settings = value
        self.accept()

    def confirm_delete(self):
        answer = QMessageBox.question(self, tr('Delete collected history?'),
            tr('This resets recorded totals, starting hours and history. A backup will be kept on this computer. Tracking will continue from now.'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.parent() and hasattr(self.parent(), 'cancel_rebuild'):
                self.parent().cancel_rebuild()
            backup = delete_history(self.directory)
            QMessageBox.information(self, tr('History deleted'), tr('backup.saved', path=backup))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('History could not be deleted'), str(error))


def configure_login(enabled):
    """Run only after consent. No elevation; tasks always use the current user."""
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        args = [str(executable.with_name('LifetimeTracker.exe')), '--run']
    else:
        if sys.platform == 'win32' and executable.with_name('pythonw.exe').exists():
            executable = executable.with_name('pythonw.exe')
        args = [str(executable), str(Path(__file__).with_name('lifetime_tracker.py')), '--run']
    if sys.platform == 'win32':
        import base64
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        if enabled:
            script = ("$ErrorActionPreference='Stop';$u=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
                "$a=New-ScheduledTaskAction -Execute " + quote(args[0]) + ' -Argument ' + quote(subprocess.list2cmdline(args[1:])) + ';'
                "$t=New-ScheduledTaskTrigger -AtLogOn -User $u;"
                "$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;"
                "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([timespan]::Zero);"
                "Register-ScheduledTask -TaskName 'Lifetime Tracker' -Action $a -Trigger $t -Principal $p -Settings $s -Force|Out-Null")
        else:
            script = ("$ErrorActionPreference='Stop'; if(Get-ScheduledTask -TaskName 'Lifetime Tracker' -ErrorAction SilentlyContinue) {"
                      "Disable-ScheduledTask -TaskName 'Lifetime Tracker'|Out-Null}")
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                   base64.b64encode(script.encode('utf-16le')).decode('ascii')]
    else:
        # The installer prepares a disabled user unit. Source runs can prepare it too.
        if enabled:
            unit = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'systemd/user/lifetime-tracker.service'
            unit.parent.mkdir(parents=True, exist_ok=True)
            def quote(value):
                return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
            unit.write_text('[Unit]\nDescription=Lifetime awake-time tracker\n[Service]\nExecStart=' +
                ' '.join(quote(a) for a in args) + '\nRestart=on-failure\n[Install]\nWantedBy=default.target\n', encoding='utf-8')
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, capture_output=True, timeout=10)
        elif not (Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'systemd/user/lifetime-tracker.service').exists():
            return
        command = ['systemctl', '--user', 'enable' if enabled else 'disable', 'lifetime-tracker.service']
    result = subprocess.run(command, capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError(tr('Login tracking could not be updated. Check your user task or service permissions.'))
