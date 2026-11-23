"""Uptime and downtime on the Lifetime Server dashboard (1.3): the shared date range, a computer's uptime and
downtime, the fleet uptime bars and "Keep detailed records for".

Built into a ServerWindow (like InsightsUI), so lifetime_server_gui.py only places these pieces. All I/O runs in the
window's Tasks; panels, buttons and translations are the window's, so Glass/Classic, light/dark and English/Vietnamese
follow it. Missing data is shown as unknown (grey), never as zero.
"""
import datetime as dt

from PyQt6.QtCore import QDate, QLocale, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QSizePolicy, QSpinBox, QWidget
from lifetime_i18n_qt import PlainLabel as QLabel

import lifetime_uptime as uptime

RETENTION_NOTE = ('Online and offline history, incident timelines and sent alerts older than this are removed '
                  'automatically. Daily awake and off-time totals and incident summaries are kept until you delete '
                  'records before a date.')


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
