"""Desktop date range picker and the Uptime & downtime page."""
import datetime as dt
import time

from PyQt6.QtCore import QDate, QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QComboBox, QDateEdit, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout

from lifetime_charts import TOKENS, bounded_tooltip, font, hide_tooltip
from lifetime_i18n import tr, date_text, datetime_text, duration, on_language_change, language
from lifetime_i18n_qt import QFrame, QLabel, QPushButton, QWidget
from lifetime_tasks import Tasks
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


# --- Uptime & downtime page ---

PARTS = (('awake', 'Awake', 'accent'), ('asleep', 'Asleep', 'info'), ('off', 'Off', 'muted'), ('unknown', 'Unknown', 'track'))
ROWS_LIMIT = 31          # up to a month: one row per day; longer: one stacked bar per day
EVENT_PAGE = 50
EVENT_WORDS = {'start': 'Started', 'shutdown': 'Shut down', 'restart': 'Restarted', 'sleep': 'Slept', 'wake': 'Woke',
               'unexpected': 'Unexpected stop'}


def read_events():
    """Power events from this computer's log (desktop only; never saved or sent). Runs in a worker thread."""
    from lifetime_tracker import read_power
    found = read_power(None)
    if isinstance(found, Exception):
        raise found
    boots, extras, _info, now = found
    return downtime.from_boots(boots, extras, now)['events']


def hours(seconds):
    return duration(seconds, days=seconds >= 86400) if seconds is not None else tr('Not available')


class DayTimeline(QWidget):
    """Awake / asleep / off / unknown for each day of the range, with a red mark on days with an unexpected stop.

    Up to a month: one row per day, a 24-hour bar split by share. Longer ranges: one stacked column per day; when
    there are more days than pixels, neighbouring days share a column (their seconds added), so five years
    (~1,900 days) paint as a few hundred rectangles."""
    ROW = 22
    LEGEND = 26

    def __init__(self):
        super().__init__()
        self.rows = []
        self.setMouseTracking(True)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(str(tr('Uptime timeline')))

    def set_rows(self, rows):
        self.rows = rows
        self.setFixedHeight(self.LEGEND + 28 + (self.ROW * len(rows) if self.by_row() else 200))
        stops = sum(row['unexpected_stops'] for row in rows)
        self.setAccessibleName(str(tr('Uptime timeline')))
        self.setAccessibleDescription(str(tr('{days} days, {stops} unexpected stops', days=len(rows), stops=stops)))
        self.update()

    def by_row(self):
        return 0 < len(self.rows) <= ROWS_LIMIT

    def area(self):
        left = 96 if self.by_row() else 8
        return QRectF(left, self.LEGEND + 8, max(1, self.width() - left - 24), max(1, self.height() - self.LEGEND - 30))

    def columns(self):
        """[(first index, last index, summed row)] buckets, at most one per pixel."""
        rect = self.area()
        count = len(self.rows)
        slots = max(1, min(count, int(rect.width())))
        result = []
        for slot in range(slots):
            first, last = slot * count // slots, max(slot * count // slots, (slot + 1) * count // slots - 1)
            chunk = self.rows[first:last + 1]
            result.append((first, last, {key: sum(row[key] for row in chunk)
                                         for key in ('awake', 'asleep', 'off', 'unknown', 'unexpected_stops')}))
        return result

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(font(12))
        x = 0.0
        for key, word, token in PARTS:          # the legend says each colour in words
            p.setPen(QPen(QColor(TOKENS['border']), 1))
            p.setBrush(QColor(TOKENS[token]))
            p.drawRect(QRectF(x, 7, 11, 11))
            p.setPen(QColor(TOKENS['muted']))
            text = str(tr(word))
            p.drawText(QRectF(x + 16, 0, 200, 24), Qt.AlignmentFlag.AlignVCenter, text)
            x += 16 + p.fontMetrics().horizontalAdvance(text) + 14
        p.setPen(QPen(QColor(TOKENS['danger']), 3))
        p.drawLine(QPointF(x + 5, 6), QPointF(x + 5, 19))
        p.setPen(QColor(TOKENS['muted']))
        p.drawText(QRectF(x + 14, 0, 220, 24), Qt.AlignmentFlag.AlignVCenter, str(tr('Unexpected stop')))
        if not self.rows:
            return
        rect = self.area()
        if self.by_row():
            for index, row in enumerate(self.rows):
                top = rect.top() + index * self.ROW
                p.setPen(QColor(TOKENS['muted']))
                p.drawText(QRectF(0, top, rect.left() - 8, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           date_text(row['day'], year=False))
                self._stack(p, QRectF(rect.left(), top + 4, rect.width(), self.ROW - 8), row, horizontal=True)
                if row['unexpected_stops']:
                    p.setPen(QPen(QColor(TOKENS['danger']), 3))
                    p.drawLine(QPointF(rect.right() + 8, top + 4), QPointF(rect.right() + 8, top + self.ROW - 4))
            return
        buckets = self.columns()
        step = rect.width() / len(buckets)
        for slot, (_, _, total) in enumerate(buckets):
            left = rect.left() + slot * step
            self._stack(p, QRectF(left, rect.top() + 8, max(1.0, step - (1 if step >= 4 else 0)), rect.height() - 8), total)
            if total['unexpected_stops']:
                p.setPen(QPen(QColor(TOKENS['danger']), max(1.5, min(3.0, step))))
                p.drawLine(QPointF(left + step / 2, rect.top()), QPointF(left + step / 2, rect.top() + 6))
        p.setPen(QColor(TOKENS['muted']))
        count = min(len(buckets), max(2, int(rect.width() / 110)))
        for i in range(count):
            slot = round(i * (len(buckets) - 1) / max(1, count - 1))
            day = self.rows[buckets[slot][0]]['day']
            x = min(max(rect.left() + slot * step - 50, rect.left()), rect.right() - 100)
            p.drawText(QRectF(x, rect.bottom() + 4, 100, 18), Qt.AlignmentFlag.AlignCenter,
                       date_text(day, year=len(self.rows) > 366))

    @staticmethod
    def _stack(p, rect, values, horizontal=False):
        total = sum(values[key] for key, _, _ in PARTS) or 1
        p.setPen(Qt.PenStyle.NoPen)
        cursor = rect.left() if horizontal else rect.bottom()
        for key, _, token in PARTS:
            share = values[key] / total
            if share <= 0:
                continue
            p.setBrush(QColor(TOKENS[token]))
            if horizontal:
                width = rect.width() * share
                p.drawRect(QRectF(cursor, rect.top(), width, rect.height()))
                cursor += width
            else:
                height = rect.height() * share
                p.drawRect(QRectF(rect.left(), cursor - height, rect.width(), height))
                cursor -= height

    def row_at(self, position):
        rect = self.area()
        if not self.rows or not rect.adjusted(0, -8, 16, 0).contains(position):
            return None
        if self.by_row():
            index = int((position.y() - rect.top()) // self.ROW)
            return (index, index) if 0 <= index < len(self.rows) else None
        buckets = self.columns()
        slot = min(len(buckets) - 1, max(0, int((position.x() - rect.left()) / rect.width() * len(buckets))))
        return buckets[slot][:2]

    def mouseMoveEvent(self, event):
        found = self.row_at(event.position())
        if found is None:
            hide_tooltip(self)
            return
        chunk = self.rows[found[0]:found[1] + 1]
        title = date_text(chunk[0]['day']) if len(chunk) == 1 else str(tr('date.range', start=date_text(chunk[0]['day']), end=date_text(chunk[-1]['day'])))
        lines = [title] + [f"{tr(word)}: {hours(sum(row[key] for row in chunk))}" for key, word, _ in PARTS]
        stops = sum(row['unexpected_stops'] for row in chunk)
        if stops:
            lines.append(str(tr('Unexpected stops: {count}', count=stops)))
        bounded_tooltip(self, event.globalPosition().toPoint(), '\n'.join(str(line) for line in lines))

    def leaveEvent(self, event):
        hide_tooltip(self)
        super().leaveEvent(event)


def _figure(grid, index, columns, caption_text):
    box = QVBoxLayout()
    box.setSpacing(2)
    value = QLabel('')
    value.setObjectName('statValue')
    value.setWordWrap(True)
    caption = QLabel(tr(caption_text))
    caption.setObjectName('caption')
    caption.setWordWrap(True)
    box.addWidget(value)
    box.addWidget(caption)
    grid.addLayout(box, index // columns, index % columns)
    return value


class UptimePage(QWidget):
    """Uptime & downtime for the chosen range: totals, counts, a per-day timeline and the power events."""
    TOTALS = (('awake', 'Awake'), ('asleep', 'Asleep'), ('off', 'Off'), ('unknown', 'Unknown'),
              ('share', 'Awake share of known time'))
    COUNTS = (('restarts', 'Restarts'), ('shutdowns', 'Shutdowns'), ('unexpected_stops', 'Unexpected stops'),
              ('between_stops', 'Average awake time between unexpected stops'),
              ('longest_awake', 'Longest continuous awake'), ('longest_off', 'Longest off'))

    def __init__(self, model, events_source=read_events, earliest=downtime.earliest_day):
        super().__init__()
        self.model = model
        self.events_source = events_source
        self.earliest = earliest
        self.ledger = {}
        self.events = None          # None: not read yet; a list once read
        self.events_error = ''
        self.events_read_at = None
        self.shown_events = EVENT_PAGE
        self.fingerprint = None
        self.summary = None
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self._events_read)
        self.tasks.failed.connect(self._events_failed)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel(tr('Uptime & downtime'))
        title.setObjectName('pageTitle')
        header.addWidget(title)
        header.addStretch()
        self.picker = PeriodPicker(model)
        header.addWidget(self.picker)
        box.addLayout(header)
        self.coverage = QLabel('')
        self.coverage.setObjectName('caption')
        self.coverage.setWordWrap(True)
        self.coverage.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(self.coverage)
        self.values = {}
        for keys in (self.TOTALS, self.COUNTS):
            panel = QFrame()
            panel.setObjectName('insetPanel')
            grid = QGridLayout(panel)
            grid.setContentsMargins(16, 14, 16, 14)
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(12)
            for index, (key, caption_text) in enumerate(keys):
                self.values[key] = _figure(grid, index, 3, caption_text)
            box.addWidget(panel)
        timeline_panel = QFrame()
        timeline_panel.setObjectName('insetPanel')
        timeline_box = QVBoxLayout(timeline_panel)
        timeline_box.setContentsMargins(16, 12, 16, 12)
        timeline_box.setSpacing(8)
        heading = QLabel(tr('Each day'))
        heading.setObjectName('groupTitle')
        timeline_box.addWidget(heading)
        self.timeline = DayTimeline()
        timeline_box.addWidget(self.timeline)
        box.addWidget(timeline_panel)
        events_panel = QFrame()
        events_panel.setObjectName('insetPanel')
        self.events_box = QVBoxLayout(events_panel)
        self.events_box.setContentsMargins(16, 12, 16, 12)
        self.events_box.setSpacing(6)
        heading = QLabel(tr('Events'))
        heading.setObjectName('groupTitle')
        self.events_box.addWidget(heading)
        self.events_note = QLabel('')
        self.events_note.setObjectName('caption')
        self.events_note.setWordWrap(True)
        self.events_box.addWidget(self.events_note)
        self.event_rows = QVBoxLayout()
        self.event_rows.setSpacing(2)
        self.events_box.addLayout(self.event_rows)
        self.more = QPushButton(tr('Show more'))
        self.more.setObjectName('quietAction')
        self.more.setAccessibleName(str(tr('Show more events')))
        self.more.clicked.connect(self.show_more)
        self.events_box.addWidget(self.more, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addWidget(events_panel)
        self.note = QLabel(tr('Awake is time the computer was running and awake; asleep is sleep or hibernation; off is shut down or restarting. Unknown is time with no record: Windows and Linux keep only a limited log, so older days are unknown, never counted as zero.'))
        self.note.setObjectName('caption')
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(self.note)
        model.changed.connect(self.range_changed)
        on_language_change(self.retranslate)

    def retranslate(self):
        self.fingerprint = None
        if self.isVisible():
            self.refresh()

    def range_changed(self):
        self.shown_events = EVENT_PAGE
        self.fingerprint = None
        if self.isVisible():        # a hidden page catches up when it is shown
            self.refresh()

    def set_ledger(self, ledger):
        self.ledger = ledger
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        if self.events_read_at is None or time.monotonic() - self.events_read_at > 600:
            self.load_events()

    def load_events(self):
        if self.events_source is None or getattr(self, '_loading', False):
            return
        self._loading = True
        self.events_note.setText(str(tr('Reading the power events…')))
        self.tasks.submit(self.events_source)

    def _events_read(self, _number, events):
        self._loading = False
        self.events = list(events or [])
        self.events_error = ''
        self.events_read_at = time.monotonic()
        self.fingerprint = None
        self.refresh()

    def _events_failed(self, _number, error):
        self._loading = False
        self.events = []
        self.events_error = error
        self.events_read_at = time.monotonic()
        self.fingerprint = None
        self.refresh()

    def show_more(self):
        self.shown_events += EVENT_PAGE
        self._show_events()

    def bounds(self):
        return self.model.bounds(self.earliest(self.ledger) if self.ledger else None)

    def refresh(self):
        start, end = self.bounds()
        fingerprint = (start, end, language(), dt.date.today(), TOKENS['text'], len(self.events or ()), self.events_error,
                       repr(self.ledger.get('downtime_daily')), repr(self.ledger.get('daily')))
        if fingerprint == self.fingerprint:
            return
        self.fingerprint = fingerprint
        self.coverage.setText(coverage_line(self.ledger) if self.ledger else '')
        summary = downtime.summarize(self.ledger, start, end, self.events)
        self.summary = summary
        totals = summary['totals']
        for key, _ in self.TOTALS[:4]:
            self.values[key].setText(str(hours(totals[key])))
        self.values['share'].setText(f"{totals['share'] * 100:.0f} %" if totals['share'] is not None else str(tr('Not available')))
        for key in ('restarts', 'shutdowns', 'unexpected_stops'):
            self.values[key].setText(f'{totals[key]:,}')
        for key in ('between_stops', 'longest_awake', 'longest_off'):
            self.values[key].setText(str(hours(totals[key])) if totals[key] is not None else str(tr('Not available')))
        for key, value in self.values.items():
            value.setAccessibleName(value.text())
        self.timeline.set_rows(summary['rows'])
        self._show_events()

    def range_events(self):
        start, end = self.bounds()
        first = downtime.day_start(start)
        last = downtime.day_start(end + dt.timedelta(days=1))
        return [e for e in (self.events or []) if first <= e['time'] < last][::-1]

    def _show_events(self):
        while self.event_rows.count():
            item = self.event_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()           # gone from the screen now, deleted at the next event loop turn
                widget.deleteLater()
        if self.events is None:
            self.more.hide()
            return
        chosen = self.range_events()
        if self.events_error:
            self.events_note.setText(str(tr('The power events could not be read from the system log.')))
        elif not chosen:
            self.events_note.setText(str(tr('No power events in this range. The system log may not reach back this far.')))
        else:
            self.events_note.setText(str(tr('{shown} of {count} events, newest first. Read from the system log; not saved or sent.',
                                            shown=min(self.shown_events, len(chosen)), count=len(chosen))))
        for item in chosen[:self.shown_events]:
            words = [str(tr(EVENT_WORDS.get(item['kind'], item['kind'])))]
            if item.get('source') == 'fast-startup':
                words.append(str(tr('Fast Startup')))
            elif item['kind'] == 'wake' and item.get('source'):
                words.append(str(tr('wake source: {source}', source=item['source'])))
            if item.get('by'):
                words.append(str(tr('asked by {who}', who=item['by'])))
            if item.get('reason'):
                words.append(item['reason'])
            row = QLabel(datetime_text(dt.datetime.fromtimestamp(item['time'])) + '  ·  ' + ' · '.join(words))
            row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            row.setFixedHeight(row.fontMetrics().height() + 6)       # one line each: the list stays easy to scan
            row.setToolTip(row.text())
            if item['kind'] == 'unexpected':
                row.setStyleSheet(f"color: {TOKENS['danger']};")
            self.event_rows.addWidget(row)
        self.more.setVisible(len(chosen) > self.shown_events)
        self.updateGeometry()
