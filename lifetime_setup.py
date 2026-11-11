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

from lifetime_theme import stylesheet, install
from lifetime_glass import STYLES, set_style, active_style, desktop_sheet
from lifetime_settings import defaults, delete_history, normalize, save_settings, load_settings, update_settings

CHOICES = (
    ('awake', 'Awake time', 'Required. Counts time while this computer is awake.'),
    ('history', 'Daily and hourly history', 'Shows when and how long you use this computer.'),
    ('health', 'Health and stability', 'Reads system shutdown and sleep events, battery capacity and machine dates.'),
    ('battery', 'Battery and power', 'Shows charge, power use and battery condition.'),
    ('network', 'Network', 'Shows connection type, link speed and transfer speed.'),
    ('wifi_name', 'Wi-Fi network name', 'Optional. Network names can reveal your location.'),
    ('graphics', 'Graphics card', 'Shows graphics models and available readings.'),
    ('resources', 'Computer resources', 'Shows CPU, memory and disk use.'),
)


def setup_style(tokens):
    t = tokens
    family = 'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans'
    return stylesheet() + f"""
        QDialog, QScrollArea, QStackedWidget, QWidget#page {{ background: {t['bg']}; }}
        QWidget {{ color: {t['text']}; font-family: '{family}'; font-size: 15px; }}
        QLabel {{ background: transparent; }}
        QLabel#title {{ font-size: 26px; font-weight: 600; }}
        QLabel#sectionTitle {{ font-size: 18px; font-weight: 600; }}
        QLabel#description, QLabel#progress {{ color: {t['muted']}; font-size: 13px; }}
        QLabel#summary {{ background: {t['card']}; padding: 20px; }}
        QCheckBox, QRadioButton {{ spacing: 12px; min-height: 28px; }}
        QCheckBox::indicator {{ width: 20px; height: 20px;
            background: {t['card']}; border: 1px solid {t['muted']}; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background: {t['accent']}; border: 1px solid {t['accent']}; }}
        QCheckBox:focus, QRadioButton:focus {{ color: {t['accent']}; }}
        QScrollArea {{ border: none; }}
    """ + desktop_sheet(t)


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
        self.tokens = tokens
        self.initial = normalize(settings or defaults())
        set_style(self.initial['style'])
        set_language(self.initial['language'])
        self.result_settings = None
        self.setWindowTitle(tr('Lifetime settings') if self.initial['completed'] else tr('Set up Lifetime'))
        install()
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
        language_page.addSpacing(16)
        language_page.addWidget(self.text(tr('Style'), 'sectionTitle'))
        style_group = QButtonGroup(self)
        self.style_buttons = {}
        for key, title in (('glass', 'Glass'), ('classic', 'Classic')):
            button = QRadioButton(tr(title))
            button.setMinimumHeight(44)
            button.setChecked(active_style() == key)
            button.toggled.connect(lambda checked, value=key: self.change_style(value) if checked else None)
            style_group.addButton(button)
            language_page.addWidget(button)
            self.style_buttons[key] = button
        language_page.addStretch()
        first = self.page(tr('What would you like to track?'),
            tr('Lifetime records how long this computer is awake. Everything stays on this computer. Nothing is uploaded.'))
        self.select_all = ConsentCheckBox(tr('Select all'), tokens['bg'])
        self.select_all.setMinimumHeight(32)
        first.addWidget(self.select_all)
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
            box.toggled.connect(self.sync_select_all)
        self.select_all.toggled.connect(self.toggle_all_choices)
        self.sync_select_all()
        first.addStretch()
        self.answers = {}
        for key, title, explanation in (
            ('rebuild', tr('Include earlier use of this computer?'),
             tr('Lifetime has only just been installed, so it can only count from now on. Say Yes and it reads your computer\'s own activity log to work out how long this computer has already been used. Nothing leaves this computer. Older records may be missing, so the earlier total can be approximate.')),
            ('autostart', tr('Start tracking at login?'),
             tr('Starts the awake-time tracker when you sign in. You can also start it yourself from Lifetime.'))):
            page = self.page(tr(title), tr(explanation))
            group = QButtonGroup(self)
            yes = QRadioButton(tr('Yes, add my earlier usage') if key == 'rebuild' else tr('Yes, start tracking at login'))
            no = QRadioButton(tr('No, count from now on') if key == 'rebuild' else tr('No, I will start it myself'))
            group.addButton(yes)
            group.addButton(no)
            # First run: pre-select "include earlier use" so a new user gets their real total without asking anyone.
            # The explanation above says exactly what is read; picking No is one click.
            wanted = self.initial[key] or (key == 'rebuild' and not self.initial['completed'])
            yes.setChecked(wanted)
            no.setChecked(not wanted)
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

    def optional_choices(self):
        return [box for box in self.checks.values() if box.isEnabled()]   # 'Awake time' is required and always on

    def toggle_all_choices(self, checked):
        for box in self.optional_choices():
            box.setChecked(checked)

    def sync_select_all(self, *_):
        boxes = self.optional_choices()
        self.select_all.blockSignals(True)
        self.select_all.setChecked(bool(boxes) and all(box.isChecked() for box in boxes))
        self.select_all.blockSignals(False)

    def change_language(self, value):
        set_language(value)
        # Language is a preference, not consent to collect data.
        try:
            update_settings({'language': value}, self.directory)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
        if self.parent() and hasattr(self.parent(), 'switch_language'):
            self.parent().switch_language(value)

    def change_style(self, value):
        """Glass or Classic is a look, not consent: saved at once, like the language. The window changes with it."""
        if value == active_style():
            return
        if self.parent() and hasattr(self.parent(), 'switch_style'):
            saved = self.parent().switch_style(value)          # the window saves it and changes in place
        else:                                                  # first run: there is no window behind the dialog yet
            from lifetime_shell import attach_glass_all, install_glass_surfaces
            try:
                update_settings({'style': value}, self.directory)
                saved = True
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
                saved = False
            if saved:
                set_style(value)
                install_glass_surfaces()
                attach_glass_all()
        if saved:
            self.setStyleSheet(setup_style(self.tokens))
        else:
            self.style_buttons[active_style()].setChecked(True)

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
            self.summary.setText(tr('Tracking\n') + '\n'.join(names) + tr('\n\nEarlier usage: ') +
                (tr('Add from the computer\'s own log') if value['rebuild'] else tr('Do not add')) + tr('\nStart at login: ') +
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
