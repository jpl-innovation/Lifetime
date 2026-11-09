"""Optional identity, coverage evidence, and report dialogs. Never starts tracking."""
import datetime as dt
import json
import copy
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                            QFormLayout, QGridLayout, QLabel, QLineEdit,
                            QMessageBox, QPushButton, QScrollArea, QTextBrowser,
                            QSizePolicy, QVBoxLayout, QWidget)

from lifetime_i18n import TABLES
from lifetime_settings import read_settings, update_settings
from lifetime_tracker import read_ledger
from lifetime_identity import LABELS as IDENTITY_FIELDS

SYSTEM_FIELDS = {
    'system': 'Operating system', 'release': 'OS version',
    'machine': 'Architecture', 'python_version': 'Python version',
}


def reports():
    # Lazy import keeps the core desktop usable if reporting is unavailable.
    import lifetime_reports
    return lifetime_reports


def iso_date(text):
    if not text:
        return None
    try:
        value = dt.date.fromisoformat(text)
    except ValueError as error:
        raise ValueError('Use an ISO date in YYYY-MM-DD format.') from error
    if value.isoformat() != text:
        raise ValueError('Use an ISO date in YYYY-MM-DD format.')
    return text


class ScrollDialog(QDialog):
    """Keep actions reachable on a 390 × 440 desktop."""
    def __init__(self, parent=None, directory=None, language=None):
        super().__init__(parent)
        self.directory = directory
        self.language = language or read_settings(directory).get('language', 'en')
        self.resize(590, 620)
        self.setMinimumSize(300, 300)
        # Reuse the release palette; report/evidence panes remain readable paper.
        from lifetime_theme import THEMES, stylesheet, install, active_theme
        tokens = THEMES[active_theme()]
        install()
        self.setStyleSheet(stylesheet() + """
            QTextBrowser { background: #FFFFFF; color: #252D2D; }
        """)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName('technicianContent')
        self.form = QVBoxLayout(self.content)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.actions = QGridLayout()
        self.action_count = 0
        layout.addLayout(self.actions)

    def t(self, text, **values):
        translated = TABLES.get(self.language, TABLES['en']).get(text, text)
        if values:
            translated = translated.format(**values)
        if translated == text and self.language == 'vi':
            translated = reports().tr(text, self.language)
        return translated

    def note(self, text):
        value = QLabel(self.t(text))
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addWidget(value)
        return value

    def button(self, text, callback):
        button = QPushButton(self.t(text))
        button.setAccessibleName(self.t(text))
        button.clicked.connect(callback)
        self.actions.addWidget(button, self.action_count // 2, self.action_count % 2)
        self.action_count += 1
        return button

    def error(self, title, error):
        QMessageBox.warning(self, self.t(title), self.t(str(error)))


class IdentityDialog(ScrollDialog):
    def __init__(self, parent=None, directory=None, language=None):
        super().__init__(parent, directory, language)
        self.setWindowTitle(self.t('Device identity'))
        settings = read_settings(directory)
        self.note('Optional. Leave blank to omit.')
        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.editors = {}
        for key, title in IDENTITY_FIELDS.items():
            editor = QLineEdit(str(settings.get('identity', {}).get(key, '') or ''))
            editor.setAccessibleName(self.t(title))
            if key == 'deployment_date':
                editor.setPlaceholderText(self.t('Optional date: YYYY-MM-DD'))
            caption = QLabel(self.t(title))
            caption.setBuddy(editor)
            caption.setWordWrap(True)
            fields.addRow(caption, editor)
            self.editors[key] = editor
        self.form.addLayout(fields)
        self.form.addStretch()
        self.button('Cancel', self.reject)
        self.button('Save', self.save)

    def save(self):
        identity = {key: editor.text().strip() for key, editor in self.editors.items()}
        try:
            iso_date(identity['deployment_date'])
            # Only edit these preferences; preserve fresh consent/other settings.
            update_settings({'identity': identity},
                            directory=self.directory)
        except (OSError, ValueError, TypeError) as error:
            self.error('Could not save settings', error)
            return
        self.accept()


class CoverageDialog(ScrollDialog):
    def __init__(self, parent=None, directory=None, language=None):
        super().__init__(parent, directory, language)
        self.setWindowTitle(self.t('Coverage details'))
        self.note('Read-only evidence from saved records. No import or system scan is run.')
        self.evidence = QTextBrowser()
        self.evidence.setAccessibleName(self.t('Coverage details'))
        self.form.addWidget(self.evidence, 1)
        self.button('Close', self.accept)
        try:
            evidence = reports().coverage(read_ledger(self.directory), read_settings(self.directory))
            titles = {'source': 'Source', 'status': 'Status', 'earliest_known_date': 'Earliest known date',
                      'first_seen_source': 'Date source', 'recording_started': 'Recording started',
                      'earliest_record': 'Earliest record', 'first_record': 'First record',
                      'last_record': 'Last record', 'notes': 'Notes', 'gaps': 'Gap observations'}
            lines = []
            for key, title in titles.items():
                value = evidence.get(key)
                if key == 'source':
                    value = self.t({'tracked': 'Directly tracked', 'reconstructed': 'Reconstructed from OS records',
                                    'combined': 'Tracked and reconstructed'}.get(value, 'Unknown'))
                elif key == 'status':
                    value = self.t('Partial' if value == 'partial' else 'Unknown')
                elif key == 'first_seen_source':
                    value = self.t(reports().FIRST_SEEN_SOURCES.get(value, 'Unknown source'))
                elif key == 'notes':
                    value = '\n'.join(reports().tr(note, self.language) for note in value or [])
                elif key == 'gaps':
                    value = '\n'.join(f"{gap.get('start') or self.t('Unknown')} / {gap.get('end') or self.t('Unknown')}" for gap in value or []) or self.t('None recorded')
                elif isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False, indent=2)
                lines.append(f'{self.t(title)}: {value or self.t("Unknown")}')
            latest = getattr(parent, 'last_verification', None)
            if latest:
                if latest.get('difference') is not None:
                    lines.append(self.t('Latest Verify (current boot only)')+': '+str(latest['difference'])+' s')
                elif latest.get('message'):
                    lines.append(self.t('Latest Verify (current boot only)')+': '+self.t(latest['message']))
                lines.append(self.t('Verify compares the current boot only. It cannot prove complete historical coverage or recover missing OS logs.'))
            self.evidence.setPlainText('\n\n'.join(lines))
        except (ImportError, OSError, ValueError, TypeError) as error:
            self.evidence.setPlainText(self.t('Could not read coverage') + '\n' + str(error))


class ExportDialog(ScrollDialog):
    def __init__(self, parent=None, directory=None, language=None, permissions=None):
        super().__init__(parent, directory, language)
        self.setWindowTitle(self.t('Export report'))
        self.note('Choose fields, then preview or save. All fields are excluded by default.')
        self.range_select = QComboBox()
        self.range_select.addItem(self.t('All available history'), False)
        self.range_select.addItem(self.t('Custom date range (inclusive)'), True)
        self.range_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.range_select.setMinimumContentsLength(12)
        self.range_select.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.range_select.setAccessibleName(self.t('All available history'))
        self.form.addWidget(self.range_select)
        self.dates = QWidget()
        dates_form = QFormLayout(self.dates)
        dates_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.start_date = QLineEdit(dt.date.today().isoformat())
        self.end_date = QLineEdit(dt.date.today().isoformat())
        for title, editor in [('Start date', self.start_date), ('End date', self.end_date)]:
            editor.setAccessibleName(self.t(title))
            editor.setPlaceholderText('YYYY-MM-DD')
            caption = QLabel(self.t(title))
            caption.setBuddy(editor)
            caption.setWordWrap(True)
            dates_form.addRow(caption, editor)
        self.form.addWidget(self.dates)
        self.dates.hide()
        self.range_select.currentIndexChanged.connect(lambda: self.dates.setVisible(bool(self.range_select.currentData())))
        self.note('Include identity fields')
        self.identity_checks = self.checks(IDENTITY_FIELDS)
        self.note('Include system fields')
        self.system_checks = self.checks(SYSTEM_FIELDS)
        permissions = read_settings(self.directory) if permissions is None else permissions
        for check in self.system_checks.values():
            check.setEnabled(bool(permissions.get('completed') and permissions.get('resources')))
        if not permissions.get('resources'):
            self.note('System information is disabled in Settings. Export does not change that choice.')
        if not permissions.get('history'):
            self.note('History collection is disabled in Settings. Dated history is not included in reports.')
        self.preview = QTextBrowser()
        self.preview.setMinimumHeight(180)
        self.preview.setAccessibleName(self.t('Preview'))
        self.form.addWidget(self.preview, 1)
        self.result = self.note('')
        self.button('Close', self.reject)
        self.button('Preview', self.refresh_preview)
        self.save_pdf = self.button('Save PDF', lambda: self.save_report('pdf'))
        self.save_csv = self.button('Save CSV', lambda: self.save_report('csv'))
        self.reviewed_report = None
        for check in (*self.identity_checks.values(), *self.system_checks.values()):
            check.toggled.connect(self.invalidate_preview)
        self.range_select.currentIndexChanged.connect(self.invalidate_preview)
        self.start_date.textChanged.connect(self.invalidate_preview)
        self.end_date.textChanged.connect(self.invalidate_preview)
        self.invalidate_preview()

    def checks(self, fields):
        result = {}
        for key, title in fields.items():
            check = QCheckBox(self.t(title))
            check.setAccessibleName(self.t(title))
            self.form.addWidget(check)
            result[key] = check
        return result

    def build_fresh(self):
        settings = read_settings(self.directory)
        ledger = read_ledger(self.directory)
        start = end = None
        if self.range_select.currentData():
            start = iso_date(self.start_date.text().strip())
            end = iso_date(self.end_date.text().strip())
            if not start or not end:
                raise ValueError('Use an ISO date in YYYY-MM-DD format.')
            if start > end:
                raise ValueError('Start date must be on or before end date.')
        # The engine checks fresh consent before calling any platform getters.
        fields = [key for key, check in self.system_checks.items() if check.isChecked()]
        report = reports().build_report(ledger, settings, start=start, end=end,
                                       system_fields=fields, language=settings.get('language', 'en'),
                                       selected_identity=[key for key, check in self.identity_checks.items() if check.isChecked()],
                                       verification=getattr(self.parent(), 'last_verification', None))
        return report

    def invalidate_preview(self, *unused):
        self.reviewed_report = None
        self.preview.clear()
        self.result.setText(self.t('Preview the current selection before saving.'))
        self.save_pdf.setEnabled(False)
        self.save_csv.setEnabled(False)

    def show_report(self, report):
        self.preview.setHtml(reports().report_html(report))
        self.reviewed_report = copy.deepcopy(report)
        self.save_pdf.setEnabled(True)
        self.save_csv.setEnabled(True)
        self.scroll.ensureWidgetVisible(self.preview)
        self.preview.setFocus()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(max(0, self.preview.y()-8)))

    @staticmethod
    def report_content(report):
        result = dict(report)
        result.pop('created', None)
        return result

    def refresh_preview(self):
        try:
            self.show_report(self.build_fresh())
            self.result.setText(self.t('Preview ready. Choose PDF or CSV to save.'))
        except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
            self.invalidate_preview()
            self.error('Could not build report', error)

    def save_report(self, kind):
        if self.reviewed_report is None:
            self.refresh_preview()
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save PDF' if kind == 'pdf' else 'Save CSV'),
                                             'lifetime-report.' + kind,
                                             f'{kind.upper()} (*.{kind})',
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        destination = Path(path)
        # A misleading extension must never turn an export into an overwrite of
        # a live ledger/settings file. Keep the chosen name and append the format.
        if destination.suffix.lower() != '.'+kind:
            destination = destination.with_name(destination.name+'.'+kind)
        if destination.exists():
            choice = QMessageBox.question(self, self.t('Replace file?'),
                                          self.t('This file already exists. Replace it?'),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if choice != QMessageBox.StandardButton.Yes:
                return
        try:
            report = self.build_fresh()
            if self.report_content(report) != self.report_content(self.reviewed_report):
                self.show_report(report)
                self.result.setText(self.t('Saved data or permissions changed. Review the updated preview, then save again.'))
                return
            # Keep the reviewed creation timestamp so preview and output match.
            report['created'] = self.reviewed_report['created']
            self.show_report(report)
            writer = reports().write_pdf if kind == 'pdf' else reports().write_csv
            writer(report, destination)
            self.result.setText(self.t('Report saved.'))
        except (ImportError, OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            self.invalidate_preview()
            self.error('Could not save report', error)
