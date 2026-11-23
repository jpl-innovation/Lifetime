"""Desktop date range picker and the Uptime & downtime page."""
import datetime as dt

from PyQt6.QtCore import QDate, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QSizePolicy

from lifetime_i18n import tr, date_text, on_language_change
from lifetime_i18n_qt import QLabel, QWidget
import lifetime_downtime as downtime

CHOICES = (('today', 'Today'), ('7d', 'Last 7 days'), ('30d', 'Last 30 days'), ('this_month', 'This month'),
           ('last_month', 'Last month'), ('this_year', 'This year'), ('12m', 'Last 12 months'),
           ('5y', 'Last 5 years'), ('all', 'All'), ('custom', 'Custom…'))
DEFAULT = '30d'


def _date(value):
    try:
        day = dt.date.fromisoformat(value)
        return day if day.isoformat() == value else None
    except (TypeError, ValueError):
        return None


class PeriodModel(QObject):
    """The chosen range, shared by every page that has a picker. Dates are worked out when used, so a quick choice
    such as "Last 30 days" moves on at midnight; only Custom keeps fixed dates. ``save`` stores it in settings."""
    changed = pyqtSignal()

    def __init__(self, saved=None, save=None, today=None):
        super().__init__()
        saved = saved if isinstance(saved, dict) else {}
        self.choice = saved.get('choice') if saved.get('choice') in downtime.PERIODS else DEFAULT
        self.start, self.end = _date(saved.get('start')), _date(saved.get('end'))
        if self.choice == 'custom' and (self.start is None or self.end is None):
            self.choice = DEFAULT
        self.save = save
        self.today = today          # tests pin the date; None = the computer's date

    def current_day(self):
        return self.today or dt.date.today()

    def bounds(self, earliest=None):
        return downtime.period_bounds(self.choice, self.current_day(), earliest, self.start, self.end)

    def set(self, choice, start=None, end=None):
        if choice not in downtime.PERIODS:
            return
        if choice == 'custom':
            if start is None or end is None:
                start, end = self.bounds()
            end = min(end, self.current_day())
            start = min(start, end)
            self.start, self.end = start, end
        if choice == self.choice and choice != 'custom':
            return
        self.choice = choice
        if self.save is not None:
            value = dict(choice=choice)
            if self.start and self.end:
                value.update(start=self.start.isoformat(), end=self.end.isoformat())
            try:
                self.save(value)
            except (OSError, ValueError):
                pass        # the range still applies; it is just not remembered
        self.changed.emit()


class PeriodPicker(QWidget):
    """Quick choices in a list, and From / To calendars for Custom (no lower limit; To never in the future)."""
    def __init__(self, model):
        super().__init__()
        self.model = model
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.choices = QComboBox()
        self.choices.setMinimumContentsLength(12)
        self.choices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.choices.setMinimumHeight(36)
        for key, _ in CHOICES:
            self.choices.addItem('', key)
        row.addWidget(self.choices)
        self.from_label = QLabel(tr('From'))
        self.from_edit = QDateEdit()
        self.to_label = QLabel(tr('To'))
        self.to_edit = QDateEdit()
        for caption, edit in ((self.from_label, self.from_edit), (self.to_label, self.to_edit)):
            caption.setObjectName('caption')
            caption.setBuddy(edit)
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            edit.setMinimumHeight(36)
            edit.setMinimumDate(QDate(1900, 1, 1))     # no practical lower limit
            row.addWidget(caption)
            row.addWidget(edit)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.choices.currentIndexChanged.connect(self._chosen)
        self.from_edit.dateChanged.connect(self._dates)
        self.to_edit.dateChanged.connect(self._dates)
        model.changed.connect(self.sync)
        self.retranslate()
        self.sync()
        on_language_change(self.retranslate)

    def retranslate(self):
        for index, (_, text) in enumerate(CHOICES):
            self.choices.setItemText(index, str(tr(text)))
        self.choices.setAccessibleName(str(tr('Date range')))
        self.from_edit.setAccessibleName(str(tr('From date')))
        self.to_edit.setAccessibleName(str(tr('To date')))

    def sync(self):
        """Show the model's state without echoing it back."""
        today = self.model.current_day()
        start, end = self.model.bounds()
        for edit, value in ((self.from_edit, start), (self.to_edit, end)):
            edit.blockSignals(True)
            edit.setMaximumDate(QDate(today.year, today.month, today.day))
            edit.setDate(QDate(value.year, value.month, value.day))
            edit.blockSignals(False)
        self.choices.blockSignals(True)
        self.choices.setCurrentIndex(self.choices.findData(self.model.choice))
        self.choices.blockSignals(False)
        custom = self.model.choice == 'custom'
        for widget in (self.from_label, self.from_edit, self.to_label, self.to_edit):
            widget.setVisible(custom)

    def _chosen(self, index):
        self.model.set(self.choices.itemData(index))

    def _dates(self, *_):
        start, end = (edit.date().toPyDate() for edit in (self.from_edit, self.to_edit))
        if self.sender() is self.from_edit and start > end:
            end = start
        self.model.set('custom', start, end)


def _source_name(key):
    return {'windows-log': tr('Windows log'), 'journal': tr('system journal'), 'lifetime': tr('Lifetime'),
            'imported': tr('OS logs')}.get(key, tr('OS logs'))


def coverage_line(ledger):
    """"Data from 3 Jul 2026 (Windows log) · recorded by Lifetime since 6 Nov 2026", from what the ledger knows."""
    facts = downtime.coverage(ledger)
    parts = []
    if facts['first']:
        source = str(_source_name(facts['source']))
        if facts['source'] != 'lifetime' and facts['archives']:
            source = str(tr('{source} incl. {count} archived logs', source=source, count=facts['archives']))
        parts.append(str(tr('Data from {date} ({source})', date=date_text(facts['first']), source=source)))
    else:
        parts.append(str(tr('No data yet')))
    if facts['recorded']:
        parts.append(str(tr('recorded by Lifetime since {date}', date=date_text(facts['recorded']))))
    note = {'denied': 'archived logs could not be read (access denied)', 'none': 'no archived logs found',
            'error': 'archived logs could not be read'}.get(facts['archive_note'])
    if note and facts['log'] == 'windows-log':
        parts.append(str(tr(note)))
    if facts['log'] == 'journal' and facts['wtmp']:
        parts.append(str(tr('{count} rotated login record files read', count=facts['wtmp'])))
    if facts['note'] == 'unavailable':
        parts.append(str(tr('the system log could not be read')))
    return ' · '.join(parts)
