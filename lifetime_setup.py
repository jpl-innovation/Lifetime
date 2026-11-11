"""Consent interview and settings, using the application's existing Qt palette."""
import os
from pathlib import Path
import subprocess
import sys
from lifetime_i18n import tr, language, set_language, on_language_change

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                            QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                            QStackedWidget, QVBoxLayout, QWidget, QFrame)
from lifetime_i18n_qt import QLabel, QPushButton, QWidget, QDialog, QCheckBox, QFrame

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
        QScrollArea {{ border: none; }}
    """ + desktop_sheet(t)


class ConsentCheckBox(QCheckBox):
    """A consent choice. Every QCheckBox is drawn as a sliding switch by lifetime_theme; `ink` is kept for callers."""
    def __init__(self, text, ink=None):
        super().__init__(text)
        self.ink = ink


def segmented(labels, current, handler, width=None):
    """The sidebar's sliding pill for choose-one settings. `handler(index)` runs when a segment becomes the chosen one."""
    from lifetime_shell import ModeSwitch
    control = ModeSwitch([tr(label) for label in labels], lambda index: None)
    control.setFixedWidth(width or 130 * len(labels) + 6)
    control.group.button(current).setChecked(True)
    for index in range(len(labels)):
        control.group.button(index).toggled.connect(lambda checked, index=index: handler(index) if checked else None)
    return control


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
        codes = ('en', 'vi')
        language_control = segmented(('English', 'Tiếng Việt'), codes.index(language()), lambda index: self.change_language(codes[index]))
        language_page.addWidget(language_control, alignment=Qt.AlignmentFlag.AlignLeft)
        self.language_buttons = {code: language_control.group.button(index) for index, code in enumerate(codes)}
        language_page.addSpacing(16)
        language_page.addWidget(self.text(tr('Style'), 'sectionTitle'))
        kinds = ('glass', 'classic')
        style_control = segmented(('Glass', 'Classic'), kinds.index(active_style()), lambda index: self.change_style(kinds[index]))
        language_page.addWidget(style_control, alignment=Qt.AlignmentFlag.AlignLeft)
        self.style_buttons = {key: style_control.group.button(index) for index, key in enumerate(kinds)}
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
            description.setContentsMargins(60, 0, 0, 8)
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
            # First run: pre-select "include earlier use" so a new user gets their real total without asking anyone.
            # The explanation above says exactly what is read; picking No is one click.
            wanted = self.initial[key] or (key == 'rebuild' and not self.initial['completed'])
            control = segmented(('Yes', 'No'), 0 if wanted else 1, lambda index: None, 236)
            page.addWidget(control, alignment=Qt.AlignmentFlag.AlignLeft)
            page.addStretch()
            yes = control.group.button(0)
            self.answers[key] = yes
        last = self.page(tr('Ready to start'), tr('You can change these choices at any time in Settings.'))
        self.summary = self.text('', 'summary')
        last.addWidget(self.summary)
        last.addStretch()
        self.notice = self.text(tr('Only the readings you choose will be collected.'), 'description')
        last.addWidget(self.notice)
        # Setup never said this computer can report to a company server; the page for it is easy to miss.
        self.connect_hint = self.text(tr('To report to a Lifetime Server on your network, open Connect after setup.'), 'description')
        last.addWidget(self.connect_hint)
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


class SwitchRow(QWidget):
    """A title and caption on the left and a sliding switch on the right; a click anywhere on the row flips it."""
    def __init__(self, title, caption='', checked=False, enabled=True):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 12, 0, 12)
        row.setSpacing(16)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        self.title = self._label(title, 'rowTitle')
        copy.addWidget(self.title)
        if caption:
            copy.addWidget(self._label(caption, 'caption'))
        row.addLayout(copy, 1)
        self.switch = QCheckBox()
        self.switch.setAccessibleName(title)
        self.switch.setAccessibleDescription(caption)
        self.switch.setChecked(checked)
        self.switch.setEnabled(enabled)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    @staticmethod
    def _label(value, name):
        label = QLabel(value)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.switch.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.switch.setFocus(Qt.FocusReason.MouseFocusReason)
            self.switch.click()
        super().mouseReleaseEvent(event)


class SettingsPage(QWidget):
    """Settings as a page of the window: every option of the old dialog, applied and saved as soon as it changes."""
    def __init__(self, window, directory=None):
        super().__init__()
        self.window_, self.directory = window, directory
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        box.addWidget(self.heading(tr('Settings'), 'pageTitle'))
        box.addWidget(self.heading(tr('Choose what Lifetime tracks and how it looks. Changes apply and save straight away.'), 'caption'))
        codes, kinds = ('en', 'vi'), ('glass', 'classic')
        self.language_control = segmented(('English', 'Tiếng Việt'), 0, lambda index: self.change_language(codes[index]))
        self.language_buttons = {code: self.language_control.group.button(index) for index, code in enumerate(codes)}
        self.style_control = segmented(('Glass', 'Classic'), 1, lambda index: self.change_style(kinds[index]))
        self.style_buttons = {key: self.style_control.group.button(index) for index, key in enumerate(kinds)}
        for title, control, name in ((tr('Language / Ngôn ngữ'), self.language_control, 'Language'), (tr('Style'), self.style_control, 'Style')):
            panel, inner = self.panel()
            inner.addWidget(self.heading(title, 'groupTitle'))
            control.setAccessibleName(tr(name))
            inner.addWidget(control, alignment=Qt.AlignmentFlag.AlignLeft)
            box.addWidget(panel)
        panel, inner = self.panel()
        inner.addWidget(self.heading(tr('What would you like to track?'), 'groupTitle'))
        inner.addWidget(self.heading(tr('Lifetime records how long this computer is awake. Everything stays on this computer. Nothing is uploaded.'), 'caption'))
        self.select_all = SwitchRow(tr('Select all'))
        inner.addWidget(self.select_all)
        self.checks, self.rows = {}, {}
        for key, title, explanation in CHOICES:
            inner.addWidget(self.divider())
            row = SwitchRow(tr(title), tr(explanation), False, key != 'awake')
            inner.addWidget(row)
            self.checks[key], self.rows[key] = row.switch, row
            row.switch.toggled.connect(self.changed)
        self.select_all.switch.toggled.connect(self.toggle_all)
        box.addWidget(panel)
        self.answers = {}
        for key, title, explanation in (
            ('rebuild', 'Include earlier use of this computer?',
             "Lifetime has only just been installed, so it can only count from now on. Say Yes and it reads your computer's own activity log to work out how long this computer has already been used. Nothing leaves this computer. Older records may be missing, so the earlier total can be approximate."),
            ('autostart', 'Start tracking at login?',
             'Starts the awake-time tracker when you sign in. You can also start it yourself from Lifetime.')):
            panel, inner = self.panel()
            row = SwitchRow(tr(title), tr(explanation))
            inner.addWidget(row)
            row.switch.toggled.connect(self.changed)
            self.answers[key] = row.switch
            box.addWidget(panel)
        self.delete_button = QPushButton(tr('Delete collected history'))
        self.delete_button.clicked.connect(self.confirm_delete)
        box.addWidget(self.delete_button, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addStretch(1)
        self.load(window.settings)

    @staticmethod
    def heading(value, name):
        return SwitchRow._label(value, name)

    @staticmethod
    def divider():
        line = QFrame()
        line.setObjectName('divider')
        line.setFixedHeight(1)
        return line

    @staticmethod
    def panel():
        frame = QFrame()
        frame.setObjectName('insetPanel')
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(20, 14, 20, 14)
        inner.setSpacing(4)
        return frame, inner

    def load(self, settings):
        """Show what is saved, without treating it as a change."""
        controls = (*self.checks.values(), *self.answers.values(), self.select_all.switch)
        for control in controls:
            control.blockSignals(True)
        for key, check in self.checks.items():
            check.setChecked(bool(settings.get(key)) or key == 'awake')
        for key, check in self.answers.items():
            check.setChecked(bool(settings.get(key)))
        self.sync_select_all()
        for control in controls:
            control.blockSignals(False)
            control.update()
        for buttons, current in ((self.language_buttons, language()), (self.style_buttons, active_style())):
            if current in buttons and not buttons[current].isChecked():
                buttons[current].blockSignals(True)
                buttons[current].setChecked(True)
                buttons[current].blockSignals(False)
                buttons[current].parentWidget().move_to(list(buttons).index(current))

    def optional_choices(self):
        return [check for check in self.checks.values() if check.isEnabled()]      # 'Awake time' is required and always on

    def sync_select_all(self, *_):
        boxes = self.optional_choices()
        self.select_all.switch.setChecked(bool(boxes) and all(check.isChecked() for check in boxes))

    def toggle_all(self, checked):
        for check in self.optional_choices():
            check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(False)
            check.update()
        self.changed()

    def changed(self, *_):
        """Save and apply the choices, exactly as the old dialog's Save did."""
        window = self.window_
        if self.sender() is not self.select_all.switch:
            self.select_all.switch.blockSignals(True)
            self.sync_select_all()
            self.select_all.switch.blockSignals(False)
        values = {key: check.isChecked() for key, check in self.checks.items()}
        values.update({key: check.isChecked() for key, check in self.answers.items()})
        previous = dict(window.settings)
        try:
            value = normalize(dict(load_settings(self.directory), **values, completed=True, language=language()))
            save_settings(value, self.directory)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Settings could not be saved'), str(error))
            self.load(previous)
            return
        window.apply_preferences(value, previous)
        window.refresh()

    def change_language(self, value):
        self.window_.switch_language(value)
        self.load(self.window_.settings)

    def change_style(self, value):
        window, control = self.window_, self.style_control
        if not window.switch_style(value, control.mapTo(window, control.rect().center())):
            self.load(window.settings)

    def confirm_delete(self):
        answer = QMessageBox.question(self, tr('Delete collected history?'),
            tr('This resets recorded totals, starting hours and history. A backup will be kept on this computer. Tracking will continue from now.'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.window_.cancel_rebuild()
            backup = delete_history(self.directory)
            QMessageBox.information(self, tr('History deleted'), tr('backup.saved', path=backup))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('History could not be deleted'), str(error))
        self.window_.refresh()


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
