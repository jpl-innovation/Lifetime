"""Uptime and downtime on the Lifetime Server dashboard (1.3): the shared date range, a computer's uptime and
downtime, the fleet uptime bars and "Keep detailed records for".

Built into a ServerWindow (like InsightsUI), so lifetime_server_gui.py only places these pieces. All I/O runs in the
window's Tasks; panels, buttons and translations are the window's, so Glass/Classic, light/dark and English/Vietnamese
follow it. Missing data is shown as unknown (grey), never as zero.
"""
import datetime as dt
import time

from PyQt6.QtCore import QDate, QLocale, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QSizePolicy, QSpinBox, QWidget
from lifetime_i18n_qt import PlainLabel as QLabel

from lifetime_charts import bounded_tooltip, hide_tooltip
from lifetime_theme import tokens
import lifetime_uptime as uptime

RETENTION_NOTE = ('Online and offline history, incident timelines and sent alerts older than this are removed '
                  'automatically. Daily awake and off-time totals and incident summaries are kept until you delete '
                  'records before a date.')


PART_COLOR = {'awake_s': 'accent', 'asleep_s': 'bar', 'off_s': 'muted', 'unknown_s': 'track'}
PART_TEXT = {'awake_s': 'Awake', 'asleep_s': 'Asleep', 'off_s': 'Off', 'unknown_s': 'Unknown'}
SOURCE_TEXT = {'os-install-date': 'Windows installation date', 'system-file-date': 'system file dates',
               'tracking-start': 'when Lifetime started recording', 'imported-log': 'the Windows event log',
               'unknown': 'unknown source'}
AVAILABILITY_CACHE_SECONDS = 60


class StackedDays(QWidget):
    """One bar per day, week or month, stacked from the bottom: awake, asleep, off and unknown as shares of that
    time. A legend sits above; hovering a bar shows its numbers. Colours come from the active theme when painted."""
    LEGEND = 22

    def __init__(self, translate):
        super().__init__()
        self.t = translate
        self.items, self.message = [], ''
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_items(self, items, message=''):
        """items: [dict(parts={awake_s: seconds, ...}, tip=text)], oldest first."""
        self.items, self.message = list(items), message
        self.setAccessibleDescription(message or self.t('{count} bars; hover a bar for its numbers', count=len(self.items)))
        self.update()

    def plot(self):
        return QRectF(0, self.LEGEND + 4, max(1, self.width()), max(1, self.height() - self.LEGEND - 6))

    def paintEvent(self, event):
        palette = tokens()
        p = QPainter(self)
        x = 0.0
        metrics = QFontMetrics(p.font())
        for key in uptime.PARTS:
            p.fillRect(QRectF(x, 6, 10, 10), QColor(palette[PART_COLOR[key]]))
            p.setPen(QColor(palette['muted']))
            words = self.t(PART_TEXT[key])
            p.drawText(QRectF(x + 14, 0, metrics.horizontalAdvance(words) + 4, self.LEGEND),
                       Qt.AlignmentFlag.AlignVCenter, words)
            x += 14 + metrics.horizontalAdvance(words) + 16
        rect = self.plot()
        if self.message or not self.items:
            p.setPen(QColor(palette['muted']))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        pitch = rect.width() / len(self.items)
        width = max(1.0, pitch - (2 if pitch > 6 else 0))
        for index, item in enumerate(self.items):
            total = sum(item['parts'].values())
            if not total:
                continue
            left, bottom = rect.left() + index * pitch, rect.bottom()
            for key in uptime.PARTS:
                height = rect.height() * item['parts'][key] / total
                if height > 0:
                    p.fillRect(QRectF(left, bottom - height, width, height), QColor(palette[PART_COLOR[key]]))
                    bottom -= height

    def mouseMoveEvent(self, event):
        rect = self.plot()
        if self.items and not self.message and rect.contains(event.position()):
            index = min(len(self.items) - 1, int((event.position().x() - rect.left()) / rect.width() * len(self.items)))
            bounded_tooltip(self, event.globalPosition().toPoint(), self.items[index]['tip'])
        else:
            hide_tooltip(self)

    def leaveEvent(self, event):
        hide_tooltip(self)
        super().leaveEvent(event)


def _qdate(day):
    return QDate(day.year, day.month, day.day)


def _date(value):
    return dt.date(value.year(), value.month(), value.day())


class RangeControl(QWidget):
    """Quick choices (Last 7 days ... Last 5 years, All) and Custom From/To calendars with no lower limit; To is never
    in the future. ``period()`` is (first, last), or None for All (the caller decides where its data starts)."""
    changed = pyqtSignal()

    def __init__(self, translate, default='30d', parent=None):
        super().__init__(parent)
        self.t = translate
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.choice = QComboBox()
        for key in uptime.PRESETS:
            self.choice.addItem('', key)
        self.choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        layout.addWidget(self.choice)
        today = dt.date.today()
        first, last = uptime.preset_range(default, today) or (today - 29 * uptime.DAY, today)
        self.from_label, self.to_label = QLabel(), QLabel()
        self.from_date, self.to_date = QDateEdit(_qdate(first)), QDateEdit(_qdate(last))
        for label, editor in ((self.from_label, self.from_date), (self.to_label, self.to_date)):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat('d MMM yyyy')
            editor.setMinimumWidth(130)
            label.setBuddy(editor)
            layout.addWidget(label)
            layout.addWidget(editor)
            editor.dateChanged.connect(self._dates_changed)
        self.to_date.setMaximumDate(QDate.currentDate())
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.choice.setCurrentIndex(uptime.PRESETS.index(default))
        self.choice.currentIndexChanged.connect(self._choice_changed)
        self._previous = self.period()
        self._show_dates()
        self.retranslate()

    def key(self):
        return self.choice.currentData()

    def set_key(self, key):
        self.choice.setCurrentIndex(uptime.PRESETS.index(key))

    def set_custom(self, first, last):
        for editor, day in ((self.from_date, first), (self.to_date, last)):
            editor.blockSignals(True)
            editor.setDate(_qdate(day))
            editor.blockSignals(False)
        self.choice.blockSignals(True)
        self.choice.setCurrentIndex(uptime.PRESETS.index('custom'))
        self.choice.blockSignals(False)
        self._show_dates()
        self.changed.emit()

    def period(self, today=None):
        today = today or dt.date.today()
        self.to_date.setMaximumDate(_qdate(today))      # a window left open past midnight follows the calendar
        key = self.key()
        if key == 'all':
            return None
        if key != 'custom':
            return uptime.preset_range(key, today)
        first, last = _date(self.from_date.date()), min(_date(self.to_date.date()), today)
        first = min(first, last)
        return max(first, last - (uptime.MAX_DAYS - 1) * uptime.DAY), last      # at most ten years at once

    def label(self):
        """The chosen period in words, for titles and reports."""
        if self.key() not in ('custom',):
            return self.t(uptime.PRESET_TEXT[self.key()])
        first, last = self.period()
        return self.t('{first} to {last}', first=first.strftime('%Y-%m-%d'), last=last.strftime('%Y-%m-%d'))

    def _choice_changed(self):
        if self.key() == 'custom':     # start from what was shown
            today = dt.date.today()
            first, last = self._previous or (today - 29 * uptime.DAY, today)
            for editor, day in ((self.from_date, first), (self.to_date, last)):
                editor.blockSignals(True)
                editor.setDate(_qdate(day))
                editor.blockSignals(False)
        else:
            self._previous = self.period()
        self._show_dates()
        self.changed.emit()

    def _dates_changed(self):
        if self.from_date.date() > self.to_date.date():
            sender = self.sender()
            other = self.to_date if sender is self.from_date else self.from_date
            other.blockSignals(True)
            other.setDate(sender.date())
            other.blockSignals(False)
        self.changed.emit()

    def _show_dates(self):
        custom = self.key() == 'custom'
        for widget in (self.from_label, self.from_date, self.to_label, self.to_date):
            widget.setVisible(custom)

    def retranslate(self, locale=None):
        for index, key in enumerate(uptime.PRESETS):
            self.choice.setItemText(index, self.t(uptime.PRESET_TEXT[key]))
        self.choice.setAccessibleName(self.t('Period'))
        self.from_label.setText(self.t('From'))
        self.to_label.setText(self.t('To'))
        self.from_date.setAccessibleName(self.t('From'))
        self.to_date.setAccessibleName(self.t('To'))
        if locale is not None:
            for editor in (self.from_date, self.to_date):
                editor.setLocale(locale)


class UptimeUI:
    def __init__(self, window):
        self.w = window
        self._retention_loaded = False
        self.controls = []

    def t(self, key, **values):
        return self.w.t(key, **values)

    def range_control(self, default='30d'):
        control = RangeControl(self.t, default)
        self.controls.append(control)
        return control

    def day_text(self, day, year=False):
        return self.w._date(day.isoformat(), year=year)

    def bucket_text(self, start, kind):
        """'2 Nov' for a day, 'Week of 2 Nov' for a week, 'Nov 2026' for a month."""
        if kind == 'day':
            return self.day_text(start)
        if kind == 'week':
            return self.t('Week of {day}', day=self.day_text(start))
        return f"{self.t(f'month.{start.month}')} {start.year}"

    @staticmethod
    def resolve(period, earliest, today=None):
        """A control's period, with All running from ``earliest`` (an ISO date, or None) up to today, at most ten years."""
        today = today or dt.date.today()
        if period is not None:
            return period
        first = dt.date.fromisoformat(earliest) if earliest else today
        return max(min(first, today), today - (uptime.MAX_DAYS - 1) * uptime.DAY), today

    # ---- one computer: awake time per day ----

    def render_awake(self, device):
        """Awake hours per local day from the history the computer reports, for the chosen period. Long periods show
        the average per recorded day of each week or month; days without a record stay empty, never zero."""
        w = self.w
        chart, summary = w.device_daily, w.daily_summary
        allowed = (device.get('policy') or {}).get('history') is True
        daily = ((device.get('history') or {}).get('daily') or {}) if allowed else {}
        daily = {day: value for day, value in daily.items() if isinstance(value, (int, float))}
        first, last = self.resolve(w.detail_range.period(), min(daily, default=None))
        kind = uptime.bucket_kind(first, last)
        rows = uptime.daily_buckets(daily, first, last, kind)
        values = [None if row['seconds'] is None else row['seconds'] / row['known'] / 3600 for row in rows]
        message = '' if any(v is not None for v in values) else self.t(
            'No awake time recorded in this period' if daily else
            'No awake time recorded yet' if allowed else 'History is off on this computer')
        if kind == 'day':
            chart.details = []
            chart.set_data([row['start'].isoformat() for row in rows], values, dates=[row['start'] for row in rows],
                           message=message)
        else:
            chart.details = [self.t('Average per recorded day · {known} of {days} days recorded', known=row['known'],
                                    days=row['days']) for row in rows]
            chart.set_data([self.bucket_text(row['start'], kind) for row in rows], values, message=message)
        days = [first + i * uptime.DAY for i in range((last - first).days + 1)]
        recorded = [(daily[d.isoformat()] / 3600, d) for d in days if d.isoformat() in daily]
        if recorded:
            longest, day = max(recorded, key=lambda pair: pair[0])
            average = sum(v for v, _ in recorded) / len(recorded)
            text = self.t('Average {average} a day · longest day {longest} on {day}',
                          average=self.t('{hours} h', hours=f'{average:.1f}'),
                          longest=self.t('{hours} h', hours=f'{longest:.1f}'), day=w._date(day.isoformat(), year=False))
            text += ' · ' + self.t('{known} of {days} days recorded', known=len(recorded), days=len(days))
            if kind != 'day':
                text += ' · ' + self.t('Bars show the average per recorded day of each week' if kind == 'week' else
                                       'Bars show the average per recorded day of each month')
            summary.setText(text)
        summary.setVisible(bool(recorded))
        self.render_downtime(device)

    # ---- one computer: uptime and downtime ----

    def device_section(self, layout):
        w = self.w
        title = w.label('Uptime & downtime')
        title.setStyleSheet('font-size:18px; font-weight:600;')
        layout.addWidget(title)
        self.downtime_chart = StackedDays(self.t)
        self.downtime_chart.setFixedHeight(170)
        layout.addWidget(self.downtime_chart)
        self.downtime_summary, self.downtime_coverage, self.availability_text = QLabel(), QLabel(), QLabel()
        for label in (self.downtime_summary, self.availability_text, self.downtime_coverage):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        self.downtime_coverage.setObjectName('muted')
        self._availability = {}         # (device id, first, last) -> (monotonic time, result or None)
        self._availability_wanted = None

    def hours(self, seconds):
        return '—' if seconds is None else self.w._duration(seconds)

    def render_downtime(self, device):
        """Awake, asleep, off and unknown per day for the chosen period (asleep and off only from a computer that
        reports them, 1.3 or later), its restarts and stops, and availability in expected hours from monitoring."""
        if not hasattr(self, 'downtime_chart'):
            return
        w = self.w
        allowed = (device.get('policy') or {}).get('history') is True
        history = (device.get('history') or {}) if allowed else {}
        daily = history.get('daily') or {}
        earliest = min([*daily, *(history.get('downtime_daily') or {})], default=None)
        period = w.detail_range.period()
        first, last = self.resolve(period, earliest)
        days = uptime.day_breakdown(history, first, last)
        kind = uptime.bucket_kind(first, last)
        items = []
        for row in uptime.breakdown_buckets(days, first, last, kind):
            parts = {key: row[key] or 0 for key in uptime.PARTS}
            lines = [self.bucket_text(row['start'], kind)]
            lines += [f"{self.t(PART_TEXT[key])}: {self.hours(row[key])}" for key in uptime.PARTS]
            if row['reported']:
                lines.append(self.t('Restarts {restarts} · unexpected stops {stops}', restarts=row['restarts'],
                                    stops=row['unexpected_stops']))
            items.append(dict(parts=parts, tip='\n'.join(lines)))
        message = '' if allowed else self.t('History is off on this computer')
        self.downtime_chart.set_items(items, message)
        total = uptime.total(days)
        parts = ' · '.join(f"{self.t(PART_TEXT[key])} {self.hours(total[key])}" for key in uptime.PARTS)
        if total['reported']:
            parts += ' · ' + self.t('Restarts {restarts} · clean shutdowns {shutdowns} · unexpected stops {stops}',
                                    restarts=total['restarts'], shutdowns=total['shutdowns'], stops=total['unexpected_stops'])
            if total['awake_share'] is not None:
                parts += ' · ' + self.t('Awake {share} % of the known time', share=f"{total['awake_share']:g}")
            if total['awake_per_stop'] is not None:
                parts += ' · ' + self.t('{hours} awake per unexpected stop', hours=self.hours(total['awake_per_stop']))
        self.downtime_summary.setText(parts if allowed else '')
        coverage = []
        if allowed and not history.get('downtime_daily'):
            coverage.append(self.t('This computer does not report sleep and off time (Lifetime before 1.3): only awake '
                                   'time is known and the rest is Unknown.'))
        elif allowed:
            coverage.append(self.t('Sleep and off time reported for {known} of {days} days.', known=total['reported'],
                                   days=total['days']))
        if history.get('first_seen'):
            coverage.append(self.t('Data from {date} ({source})', date=w._date(history['first_seen']),
                                   source=self.t(SOURCE_TEXT.get(history.get('first_seen_source', 'unknown'), 'unknown source'))))
        if history.get('recording_started'):
            coverage.append(self.t('recorded by Lifetime since {date}', date=w._date(history['recording_started'])))
        self.downtime_coverage.setText(' · '.join(coverage))
        self.downtime_coverage.setVisible(bool(coverage))
        self._load_availability(device['device_id'] if device.get('device_id') else None,
                                None if period is None else (first, last))

    def _load_availability(self, device_id, period):
        """Availability in expected hours over the period (All: everything monitoring kept), asked again at most once
        a minute while the detail refreshes."""
        key = (device_id, period)
        self._availability_wanted = key
        cached = self._availability.get(key)
        if cached and time.monotonic() - cached[0] < AVAILABILITY_CACHE_SECONDS:
            self.render_availability(cached[1])
            return
        if device_id is None:
            self.render_availability(None)
            return
        if cached is None:
            self.availability_text.setText(self.t('Availability in expected hours: loading…'))
        arguments = dict(device_ids=[device_id])
        if period:
            arguments.update(start=period[0].isoformat(), end=period[1].isoformat())
        client = self.w.client

        def done(result, success):
            devices = (result.get('devices') or ()) if success and isinstance(result, dict) else ()
            found = next((item for item in devices if item.get('device_id') == device_id), None)
            self._availability[key] = (time.monotonic(), found)
            while len(self._availability) > 16:
                del self._availability[next(iter(self._availability))]
            if self._availability_wanted == key:
                self.render_availability(found)
        self.w._submit(lambda: client.call('availability', **arguments), done, quiet=True)

    def render_availability(self, result):
        """The existing availability model (lifetime_availability): observed online share of the expected hours."""
        if result is None:
            text = self.t('Availability in expected hours: not available')
        elif result['status'] == 'not-scheduled':
            text = self.t('Not expected online in this period')
        else:
            if result['status'] == 'insufficient':
                head = self.t('Availability in expected hours: insufficient evidence (observed {value}, coverage {coverage})',
                              value=self._percent(result['availability']), coverage=self._percent(result['coverage']))
            else:
                head = self.t('Availability in expected hours: {value} (coverage {coverage})',
                              value=self._percent(result['availability']), coverage=self._percent(result['coverage']))
            outages = result.get('outages')
            text = head if outages is None else head + ' · ' + (
                self.t('no outages') if not outages else
                self.t('{count} outages, longest {longest}', count=outages, longest=self.hours(result['longest_outage_s'])))
        self.availability_text.setText(text)

    def _percent(self, value):
        return self.t('Not available') if value is None else f'{value:g} %'

    # ---- Overview: fleet totals for the period ----

    def fleet_arguments(self):
        """start/end for the store's fleet call: the chosen period, All as the last ten years (trimmed on display)."""
        return dict(zip(('start', 'end'), (day.isoformat() for day in self.resolve(
            self.w.fleet_range.period(), (dt.date.today() - (uptime.MAX_DAYS - 1) * uptime.DAY).isoformat()))))

    def render_fleet_chart(self, result):
        w = self.w
        samples = list(result.get('daily') or [])
        if w.fleet_range.key() == 'all':        # from the first day any computer reported
            while samples and samples[0]['seconds'] is None:
                samples.pop(0)
        kind = 'day'
        if samples:
            first, last = dt.date.fromisoformat(samples[0]['day']), dt.date.fromisoformat(samples[-1]['day'])
            kind = uptime.bucket_kind(first, last)
            if kind != 'day':
                by_day = {sample['day']: sample for sample in samples}
                grouped = []
                for start, end in uptime.buckets(first, last, kind):
                    part = [by_day.get((start + i * uptime.DAY).isoformat()) for i in range((end - start).days + 1)]
                    known = [sample for sample in part if sample and sample['seconds'] is not None]
                    grouped.append(dict(day=start.isoformat(), seconds=sum(x['seconds'] for x in known) if known else None,
                                        devices=max((x['devices'] for x in known), default=0)))
                samples = grouped
        w.daily_chart.day_label = lambda day, kind=kind: self.bucket_text(dt.date.fromisoformat(day), kind)
        w.daily_chart.set_samples(samples, self.t('No dated reports yet'), self.t('h'))
        total = result.get('range')
        if total and total.get('seconds') is not None:
            w.fleet_range_total.setText(self.t('{period}: {hours} awake on {count} computers', period=w.fleet_range.label(),
                                               hours=w._duration(total['seconds']), count=total['devices']))
        else:
            w.fleet_range_total.setText(self.t('{period}: no awake time reported', period=w.fleet_range.label()))

    # ---- Administration: "Keep detailed records for" ----

    def retention_rows(self, form):
        w = self.w
        self.detail_days = QSpinBox()
        self.detail_days.setRange(30, 3660)
        self.detail_days.setValue(400)
        self.detail_days.setMaximumWidth(150)
        self.detail_days.setEnabled(False)
        self.detail_save = w.button('Save', self.save_retention)
        self.detail_save.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.detail_days)
        row.addWidget(self.detail_save)
        row.addStretch()
        title = w.label('Keep detailed records for')
        title.setBuddy(self.detail_days)
        form.addRow(title, row)
        note = w.label(RETENTION_NOTE)
        note.setObjectName('muted')
        form.addRow(note)
        self.retranslate()

    def load_retention(self):
        client = self.w.client
        self.w._submit(lambda: client.call('detail_retention'), self._retention_finished, quiet=True)

    def _retention_finished(self, result, success):
        if success and isinstance(result, dict) and type(result.get('days')) is int:
            self.detail_days.setValue(result['days'])
            self._retention_loaded = True
        self.detail_days.setEnabled(self._retention_loaded)
        self.detail_save.setEnabled(self._retention_loaded)

    def save_retention(self):
        client, days = self.w.client, self.detail_days.value()

        def done(result, success):
            self.detail_save.setEnabled(True)
            self.w.status_label.setText(self.t('Changes saved.' if success else
                                               'The server did not accept this. Check the values and try again.'))
        self.w._submit(lambda: client.call('set_detail_retention', days=days), done, self.detail_save, quiet=True)

    def retranslate(self):
        locale = QLocale('vi_VN' if self.w.language == 'vi' else 'en_GB')
        for control in self.controls:
            control.retranslate(locale)
        if hasattr(self, 'detail_days'):
            self.detail_days.setSuffix(' ' + self.t('days'))
            self.detail_days.setAccessibleName(self.t('Keep detailed records for'))
