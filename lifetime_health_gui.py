"""Native Health page; background reads require explicit Health consent."""
import datetime as dt
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy
from lifetime_i18n_qt import QWidget, QFrame, QLabel, QPushButton
from lifetime_i18n import tr, date_text, datetime_text, on_language_change
from lifetime_charts import Plot, RangeSegments, range_bounds, weekly
from lifetime_tasks import Tasks
import lifetime_health as health
from lifetime_theme import install, tokens
from PyQt6.QtGui import QColor, QPainter


class HealthPlot(Plot):
    def display(self, value):
        if value is not None and self.unit in ('events', 'cycles'):
            return tr('{count} events' if self.unit == 'events' else '{count} cycles', count=f'{value:g}')
        return super().display(value)

    def scale(self):
        if self.unit in ('events', 'cycles'):
            import math
            return max(3, math.ceil(max((v for v in self.values if v is not None), default=0)/3)*3)
        return super().scale()

    def positions(self, rect):
        if self.kind == 'line' and len(self.dates) > 1:
            span = (self.dates[-1]-self.dates[0]).days
            return [rect.left()+(day-self.dates[0]).days*rect.width()/max(1, span) for day in self.dates]
        return super().positions(rect)


class GoodDot(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(10, 10)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens()['good']))
        painter.drawEllipse(1, 1, 8, 8)


def text(value='', name=''):
    label = QLabel(value)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class HealthPage(QWidget):
    def __init__(self, settings, open_settings):
        super().__init__()
        install()
        self.settings, self.enabled = {}, False
        self.generation, self.jobs, self.data = 0, {}, {}
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.finished)
        self.tasks.failed.connect(lambda number, error: self.finished(number, None))
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        box.addWidget(text(tr('Health'), 'pageTitle'))
        self.machine = text('', 'caption')
        box.addWidget(self.machine)
        self.disabled = QFrame()
        self.disabled.setObjectName('insetPanel')
        disabled = QVBoxLayout(self.disabled)
        disabled.setContentsMargins(20, 20, 20, 20)
        disabled.addWidget(text(tr('Health readings are off. Enable Health and stability in Settings to read system events, battery condition and machine dates.')))
        button = QPushButton(tr('Open Settings'))
        button.clicked.connect(open_settings)
        disabled.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.disabled)
        self.content = QWidget()
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        self.range = RangeSegments()
        self.range.buttons[3].hide()
        self.range.setAccessibleName(tr('Health date range'))
        self.range.currentIndexChanged.connect(self.render)
        content.addWidget(self.range, alignment=Qt.AlignmentFlag.AlignLeft)
        self.panels, self.states = {}, {}
        for key, title in (('stability', 'Stability'), ('battery', 'Battery'), ('sleep', 'Sleep')):
            panel = QFrame()
            panel.setObjectName('insetPanel')
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(20, 16, 20, 16)
            layout.setSpacing(10)
            layout.addWidget(text(tr(title), 'groupTitle'))
            state = text('', 'caption')
            layout.addWidget(state)
            self.panels[key], self.states[key] = layout, state
            content.addWidget(panel)
        self.stability_count = text('', 'statValue')
        count_row = QHBoxLayout()
        self.good_dot = GoodDot()
        count_row.addWidget(self.good_dot)
        count_row.addWidget(self.stability_count, 1)
        self.panels['stability'].addLayout(count_row)
        self.stability_chart = self.chart(self.panels['stability'], 'Unexpected shutdowns and crashes per week', 'events')
        self.events = text('', 'caption')
        self.panels['stability'].addWidget(self.events)
        self.battery_body = QWidget()
        battery_layout = QVBoxLayout(self.battery_body)
        battery_layout.setContentsMargins(0, 0, 0, 0)
        battery_layout.setSpacing(10)
        self.panels['battery'].addWidget(self.battery_body)
        self.panels['battery'] = battery_layout
        self.battery_value = text('', 'healthValue')
        self.battery_value.setStyleSheet('font-size:40px; font-weight:300;')
        self.battery_value.setToolTip(tr('New batteries can exceed their design capacity, so health can be above 100%.'))
        self.panels['battery'].addWidget(self.battery_value)
        self.battery_details = text('', 'caption')
        self.panels['battery'].addWidget(self.battery_details)
        self.capacity = self.chart(self.panels['battery'], 'Full charge capacity over time', 'Wh', kind='line')
        self.power_grid = QGridLayout()
        self.power_grid.setContentsMargins(0, 0, 0, 0)
        self.power_grid.setSpacing(16)
        self.power_widgets = []
        for title in ('On battery · hours per day', 'Plugged in · hours per day'):
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            plot = self.chart(layout, title, 'h')
            self.power_widgets.append((widget, plot))
        self.panels['battery'].addLayout(self.power_grid)
        self.sleep_average = text('', 'statValue')
        self.sleep_average.setToolTip(tr('Sleep is grouped into local noon-to-noon nights. Only completed sleep intervals are included.'))
        self.panels['sleep'].addWidget(self.sleep_average)
        self.sleep_cycles = self.chart(self.panels['sleep'], 'Sleep/wake cycles per day', 'cycles')
        self.sleep_time = self.chart(self.panels['sleep'], 'Time asleep per day', 'h')
        self.coverage = text(tr('Retained system logs may be incomplete. Missing readings stay unavailable; sleep averages include completed intervals only.'), 'caption')
        content.addWidget(self.coverage)
        box.addWidget(self.content)
        self.timer = QTimer(self)
        self.timer.setInterval(3600000)
        self.timer.timeout.connect(self.refresh)
        self.set_settings(settings)
        on_language_change(self.render)

    def chart(self, layout, title, unit, kind='bars'):
        layout.addWidget(text(tr(title), 'caption'))
        plot = HealthPlot(kind, unit)
        plot.bar_color = None      # the active theme's accent
        plot.setMinimumWidth(0)
        plot.setFixedHeight(150)
        plot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        plot.setAccessibleName(tr(title))
        layout.addWidget(plot)
        return plot

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for index, (widget, _) in enumerate(self.power_widgets):
            self.power_grid.removeWidget(widget)
            self.power_grid.addWidget(widget, index if self.width() < 660 else 0, 0 if self.width() < 660 else index)

    def set_settings(self, settings):
        enabled = settings.get('completed') is True and settings.get('health') is True
        self.settings = dict(settings)
        if enabled != self.enabled:
            self.generation += 1
            self.enabled = enabled
            self.data = {}
            if enabled:
                self.timer.start()
                self.refresh()
            else:
                self.timer.stop()
        self.disabled.setVisible(not enabled)
        self.content.setVisible(enabled)
        self.machine.setVisible(enabled)
        self.render()

    def refresh(self):
        if not self.enabled or any(g == self.generation for _, g in self.jobs.values()):
            return
        for key, probe in (('machine', health.probe_machine), ('stability', health.probe_stability),
                           ('battery', health.probe_battery), ('sleep', health.probe_sleep)):
            self.data[key] = 'loading'
            self.jobs[self.tasks.submit(probe)] = (key, self.generation)
        self.render()

    def finished(self, number, result):
        job = self.jobs.pop(number, None)
        if job and self.enabled and job[1] == self.generation:
            self.data[job[0]] = result
            self.render()

    def series(self, plot, values, message=''):
        if plot.kind != 'line' and plot.unit != 'events':
            start, end = range_bounds({}, ('30 days', '90 days', '1 year')[min(self.range.index, 2)])
            values = {start+dt.timedelta(days=i): values.get(start+dt.timedelta(days=i)) for i in range((end-start).days+1)}
        points = sorted(values.items())
        plot.set_data([date_text(day) for day, _ in points], [value for _, value in points],
                      dates=[day for day, _ in points], message=message)
        # The same dates and numbers remain accessible without pointer hover.
        plot.setAccessibleDescription(message or '\n'.join(date_text(day)+': '+plot.display(value) for day, value in points))

    def render(self, *_):
        if not self.enabled:
            return
        start, end = range_bounds({}, ('30 days', '90 days', '1 year')[min(self.range.index, 2)])
        def selected(date):
            return start <= health.stamp(date).date() <= end
        unavailable = tr('Health reading unavailable. Check system log access and try again later.')
        machine = self.data.get('machine')
        if machine == 'loading':
            self.machine.setText(tr('Loading…'))
        elif isinstance(machine, dict):
            def date(value):
                return date_text(dt.date.fromisoformat(value)) if value else tr('Not available')
            self.machine.setText(tr('Model {model} · BIOS {bios} · OS installed {installed}',
                model=machine.get('model') or tr('Not available'), bios=date(machine.get('bios')), installed=date(machine.get('installed'))))
            self.machine.setToolTip(tr('Linux OS install dates are estimates from system file dates.') if machine.get('estimated') else '')
        else:
            self.machine.setText(tr('Machine dates unavailable'))
        for key in self.panels:
            data = self.data.get(key)
            message = tr('Loading…') if data == 'loading' else unavailable if data is None else ''
            self.states[key].setText(message)
            self.states[key].setVisible(bool(message))
        data = self.data.get('stability')
        events = [r for r in data if selected(r['date'])] if isinstance(data, list) else []
        self.stability_count.setVisible(isinstance(data, list))
        self.good_dot.setVisible(isinstance(data, list) and not events)
        crashes = sum(r['kind'] == 'Crash' for r in events)
        self.stability_count.setText(tr('{shutdowns} unexpected shutdowns · {crashes} crashes', shutdowns=len(events)-crashes, crashes=crashes))
        self.events.setText('\n'.join(datetime_text(health.stamp(r['date']))+' · '+tr(r['kind'])
                                     for r in sorted(events, key=lambda r: r['date'], reverse=True)[:5]))
        counts = {}
        for row in events:
            day = health.stamp(row['date']).date()
            counts[day] = counts.get(day, 0)+1
        weeks = weekly({start+dt.timedelta(days=i): counts.get(start+dt.timedelta(days=i), 0) for i in range((end-start).days+1)}) if isinstance(data, list) else {}
        self.series(self.stability_chart, weeks, self.states['stability'].text() or (tr('No unexpected shutdowns or crashes in retained logs for this range') if not events else ''))
        data = self.data.get('battery')
        packs = data.get('packs') if isinstance(data, dict) else []
        pack = packs[0] if packs else {}
        available = bool(packs)
        self.battery_body.setVisible(available)
        self.battery_value.setVisible(available)
        self.battery_details.setVisible(available)
        for plot in (self.capacity, *(p for _, p in self.power_widgets)):
            plot.setVisible(available)
        if isinstance(data, dict) and not packs:
            self.states['battery'].setText(tr('No battery in this computer'))
            self.states['battery'].show()
        if available:
            value = pack.get('health')
            self.battery_value.setText(f'{value:.1f}%' if value is not None else tr('Not available'))
            def number(key):
                value = pack.get(key)
                return (f'{value:,.0f}' if key == 'cycles' else f'{value:,.1f}') if value is not None else tr('Not available')
            self.battery_details.setText(tr('Cycles {cycles} · Design {design} {unit} · Full {full} {unit} · {chemistry}',
                cycles=number('cycles'), design=number('design'), full=number('full'), unit=pack.get('unit', 'Wh'), chemistry=pack.get('chemistry') or tr('Chemistry unavailable')) +
                ('\n'+tr('Multiple batteries: showing the first pack') if len(packs) > 1 else ''))
            history = {dt.date.fromisoformat(r['date']): r['full'] for r in data.get('history', []) if selected(r['date'])}
            try:
                from lifetime_tracker import read_ledger
                for day, samples in read_ledger().get('health_capacity', {}).items():
                    if samples and selected(day) and samples[0].get('unit') == pack.get('unit'):
                        history[dt.date.fromisoformat(day)] = samples[0].get('full')
            except (OSError, ValueError, TypeError):
                pass
            self.capacity.unit = pack.get('unit', 'Wh')
            self.series(self.capacity, history)
            for field, (_, plot) in zip(('battery', 'ac'), self.power_widgets):
                self.series(plot, {dt.date.fromisoformat(day): values[field]/3600 for day, values in (data.get('usage') or {}).items() if selected(day)})
        data = self.data.get('sleep')
        daily = data.get('daily', {}) if isinstance(data, dict) else {}
        daily = {dt.date.fromisoformat(day): values for day, values in daily.items() if selected(day)}
        nights = [r['seconds'] for r in data.get('nights', []) if selected(r['date'])] if isinstance(data, dict) else []
        self.sleep_average.setVisible(bool(nights))
        self.sleep_average.setText(tr('Average sleep per night · {hours} h', hours=f'{sum(nights)/len(nights)/3600:.1f}') if nights else '')
        for plot, field, divisor in ((self.sleep_cycles, 'cycles', 1), (self.sleep_time, 'seconds', 3600)):
            self.series(plot, {day: values[field]/divisor for day, values in daily.items()}, self.states['sleep'].text())
