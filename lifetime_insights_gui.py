"""Energy and cost for this computer on the desktop app's Reports page (lifetime_insights).

Power is measured only from this computer's own on-battery discharge readings, taken every ten minutes while the app
runs and only with the Battery permission; they are kept in insights.json beside the settings (never in the ledger).
Without enough readings the panel uses the wattage the owner set, else a typical one, and says estimated.
"""
import datetime as dt
import json

from PyQt6.QtCore import QLocale, QTimer
from PyQt6.QtWidgets import QDoubleSpinBox, QGridLayout, QHBoxLayout, QLineEdit, QSizePolicy, QVBoxLayout

import lifetime_insights as insights
from lifetime_charts import Plot
from lifetime_i18n import on_language_change, tr
from lifetime_i18n_qt import QFrame, QLabel, QPushButton
from lifetime_reveal import later
from lifetime_settings import allowed_probes, load_settings, update_settings
from lifetime_tasks import Tasks
from lifetime_tracker import atomic_json, data_dir
from lifetime_widgets_dashboard import InfoCard

SAMPLE_MS = 600_000         # one on-battery reading every ten minutes
KEEP_READINGS = 200


def read_samples(directory=None):
    try:
        value = json.loads((data_dir() if directory is None else directory).joinpath('insights.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def add_samples(found, directory=None, now=None):
    """Keep the latest KEEP_READINGS discharge readings ([[ISO time, watts]]); returns the saved value."""
    value = read_samples(directory)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec='seconds')
    if found.get('watts'):
        value['discharge'] = [*[row for row in value.get('discharge', []) if isinstance(row, list) and len(row) == 2],
                              [stamp, found['watts']]][-KEEP_READINGS:]
    if found.get('kind'):
        value['kind'] = found['kind']
    if found.get('disk') is not None:      # one system-disk reading a day, for the replacement forecast
        day = (now or dt.datetime.now(dt.timezone.utc)).astimezone().date().isoformat()
        disk = value.get('disk') if isinstance(value.get('disk'), dict) else {}
        disk[day] = found['disk']
        value['disk'] = dict(sorted(disk.items())[-730:])
    atomic_json((data_dir() if directory is None else directory) / 'insights.json', value)
    return value


def probe(allowed):
    """(this computer's on-battery draw in watts or None, 'laptop'/'desktop'/None) from the local probes allowed."""
    import lifetime_signals as signals
    disk = None
    if 'disks' in allowed:
        import os
        import sys
        system = os.environ.get('SystemDrive', 'C:') + '\\' if sys.platform == 'win32' else '/'
        drive = next((d for d in signals.safe(signals.probe_disks)() or [] if d.get('path') == system), {})
        if drive.get('total'):
            disk = round(100 * (drive['total'] - drive['used']) / drive['total'], 1)
    if 'power' not in allowed:
        return dict(disk=disk) if disk is not None else {}
    power = signals.safe(signals.probe_power)() or {}
    kind = 'desktop' if power.get('state') == 'No battery' else 'laptop' if power.get('percent') is not None else None
    watts = None
    if power.get('state') == 'On battery' and 'battery' in allowed:
        battery = signals.safe(signals.probe_battery)() or {}
        watts = battery.get('watts') or None
    return dict(watts=watts, kind=kind, disk=disk)


def energy_config():
    """The owner's energy settings (price, currency, this computer's wattage), defaults where unset or damaged."""
    saved = load_settings().get('energy')
    try:
        return insights.settings(saved if isinstance(saved, dict) else None)
    except ValueError:
        return insights.settings()


class EnergyPanel(QFrame):
    """kWh and cost today, this week and this month from the ledger's daily awake time, with the method stated."""

    def __init__(self):
        super().__init__()
        self.setObjectName('insetPanel')
        self.daily, self.hourly, self.samples = {}, {}, read_samples()
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self._sampled)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName('groupTitle')
        box.addWidget(self.title)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = {}
        for key in ('today', 'week', 'month'):
            card, value = InfoCard('event', ''), QLabel()
            card.add_row('', value)
            self.cards[key] = (card, value)
            cards.addWidget(card, 1)
        box.addLayout(cards)
        self.method = QLabel()
        self.method.setObjectName('caption')
        self.method.setWordWrap(True)
        box.addWidget(self.method)
        self.chart_title = QLabel()
        self.chart_title.setObjectName('caption')
        box.addWidget(self.chart_title)
        self.chart = Plot('bars', 'kWh')
        self.chart.bar_color = None
        self.chart.setFixedHeight(150)
        self.chart.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        box.addWidget(self.chart)
        self.after = QLabel()        # the after-hours note (lifetime_insights.after_hours)
        self.after.setObjectName('caption')
        self.after.setWordWrap(True)
        self.after.hide()
        box.addWidget(self.after)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1e9)
        self.price.setDecimals(2)
        self.price.setLocale(QLocale.c())       # "2500.00" like every number the app prints, whatever Windows is set to
        self.currency = QLineEdit()
        self.currency.setMaxLength(3)
        self.currency.setMaximumWidth(90)
        self.watts = QDoubleSpinBox()
        self.watts.setRange(0, insights.WATTS_RANGE[1])
        self.watts.setDecimals(0)
        self.watts.setSuffix(' W')
        self.labels = []
        for index, widget in enumerate((self.price, self.currency, self.watts)):
            label = QLabel()
            label.setObjectName('caption')
            label.setWordWrap(True)
            self.labels.append(label)
            form.addWidget(label, 0, index * 2)
            form.addWidget(widget, 0, index * 2 + 1)
        self.save = QPushButton()
        self.save.clicked.connect(self._save)
        form.addWidget(self.save, 0, 6)
        box.addLayout(form)
        self.note = QLabel()
        self.note.setObjectName('caption')
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.timer = QTimer(self)
        self.timer.setInterval(SAMPLE_MS)
        self.timer.timeout.connect(self.sample)
        self.timer.start()
        later(0, self.sample)
        self._fill_form()
        on_language_change(self.render)
        self.render()

    def config(self):
        return energy_config()

    def _fill_form(self):
        config = self.config()
        self.price.setValue(config['price'])
        self.currency.setText(config['currency'])
        self.watts.setValue(config['device_watts'].get('this', 0))

    def _save(self):
        own = self.watts.value()
        try:
            value = insights.settings(dict(price=self.price.value(), currency=self.currency.text().strip().upper(),
                                           device_watts={'this': own} if own else {}))
        except ValueError as error:
            self.note.setText(tr(str(error)))
            return
        update_settings({'energy': None})        # replace, never merge, the per-computer wattage
        update_settings({'energy': {key: value[key] for key in ('price', 'currency', 'device_watts')}})
        self.render()

    def sample(self):
        allowed = allowed_probes(load_settings())
        self.tasks.submit(lambda: probe(allowed))

    def _sampled(self, number, found):
        if found and (found.get('watts') or found.get('kind') != self.samples.get('kind') or found.get('disk') is not None):
            try:
                self.samples = add_samples(found)
            except OSError:
                pass
            self.render()

    def set_ledger(self, ledger):
        daily, hourly = dict(ledger.get('daily') or {}), dict(ledger.get('dated_hourly') or {})
        if (daily, hourly) != (self.daily, self.hourly):
            self.daily, self.hourly = daily, hourly
            self.render()

    def item(self):
        """This computer in lifetime_insights.computer's input shape."""
        return dict(device_id='this', kind=self.samples.get('kind'), group='',
                    discharge=[row[1] for row in self.samples.get('discharge', [])],
                    history=dict(daily=self.daily, dated_hourly=self.hourly) if self.daily else None)

    def render(self, *unused):
        config = self.config()
        result = insights.computer(self.item(), config, dt.date.today())
        self.title.setText(tr('Energy and cost'))
        for key, title in (('today', 'Today'), ('week', 'Last 7 days'), ('month', 'Last 30 days')):
            card, value = self.cards[key]
            card.title.setText(tr(title))
            card.pairs[0][0].setText(tr('Cost'))
            energy = result['energy']
            card.set_headline(f"{energy[key]['kwh']:,.2f} kWh" if energy else tr('Not available'))
            value.setText(insights.money(energy[key]['cost'], config['currency']) if energy else tr('Not available'))
        power = result['power']
        how = tr(insights.SOURCE_TEXT[power['source']])
        if power['method'] == 'measured':
            how += ' ' + tr('({count} readings)', count=power['readings'])
        self.method.setText(tr('Power: {watts} W · {how}', watts=f"{power['watts']:g}", how=how) if result['energy']
                            else tr('Energy needs the awake history. Turn on History in Settings.'))
        self.chart_title.setText(tr('Energy per day, last 30 days (kWh)'))
        self.chart.setAccessibleName(tr('Energy per day, last 30 days (kWh)'))
        days = list(result['energy']['daily']) if result['energy'] else []
        self.chart.set_data([day[5:] for day in days], [result['energy']['daily'][day] for day in days])
        self.chart.setVisible(bool(days))
        for label, key in zip(self.labels, ('Price per kWh', 'Currency', 'Power of this computer (0 = automatic)')):
            label.setText(tr(key))
        for widget, key in ((self.price, 'Price per kWh'), (self.currency, 'Currency'),
                            (self.watts, 'Power of this computer (0 = automatic)')):
            widget.setAccessibleName(tr(key))
        self.save.setText(tr('Save'))
        after = result['after']
        if after:
            hours = after['hours']
            days = ', '.join(tr(f'weekday.{day}') for day in hours['weekdays'])
            span = tr('office hours {start}–{end}, {days}', start=hours['start'], end=hours['end'], days=days)
            self.after.setText(tr('This computer was on {hours} h after hours in the last 7 days ({span}): about {kwh} kWh, {cost}.',
                                  hours=f"{after['week']['hours']:,.1f}", span=span, kwh=f"{after['week']['kwh']:,.2f}",
                                  cost=insights.money(after['week']['cost'], config['currency']))
                               if after['week']['hours'] else tr('Not on after hours in the last 7 days ({span}).', span=span))
        self.after.setVisible(bool(after))
        self.note.setText(tr(insights.ENERGY_NOTE))


class ForecastPanel(QFrame):
    """The Health page's replacement forecast for this computer: battery health from the battery report's history,
    system disk free space from the app's daily readings, and the computer's age."""

    def __init__(self):
        super().__init__()
        self.setObjectName('insetPanel')
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName('groupTitle')
        box.addWidget(self.title)
        self.lines = []
        for _ in range(3):
            line = QLabel()
            line.setObjectName('caption')
            line.setWordWrap(True)
            box.addWidget(line)
            self.lines.append(line)
        self.note = QLabel()
        self.note.setObjectName('caption')
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.battery, self.machine = None, None
        on_language_change(self.render)

    def set_data(self, battery, machine):
        self.battery, self.machine = battery, machine
        self.render()

    def render(self, *unused):
        config = energy_config()
        today = dt.date.today()
        samples = read_samples()
        battery = self.battery if isinstance(self.battery, dict) else None
        item = dict(kind='laptop' if battery and battery.get('packs') else 'desktop' if battery else None,
                    health=dict(detail=dict(battery=battery)) if battery else None,
                    readings=dict(specs=dict(hardware=dict(os=dict(installed=(self.machine or {}).get('installed'))))) if isinstance(self.machine, dict) else None,
                    trends=[(day, None, value) for day, value in sorted((samples.get('disk') or {}).items())])
        parts = {part['part']: part for part in insights.replacements(item, config, today)}
        self.title.setText(tr('Replacement forecast'))
        def say(part, title):
            key, values = insights.when_text(part) if part else ('Not available', {})
            return tr(title) + ': ' + tr(key, **values)
        self.lines[0].setText(say(parts.get('battery'), 'Battery') if battery is None or battery.get('packs')
                              else tr('Battery') + ': ' + tr('No battery in this computer'))
        self.lines[1].setText(say(parts.get('disk'), 'System disk space'))
        years = insights.age(item, today)['years']
        self.lines[2].setText(tr('Age') + ': ' + (tr('{years} years', years=years) if years is not None else tr('Not available')))
        self.note.setText(tr('A date is given only with at least 4 readings over 30 days. Battery: replace below {battery} %. '
                             'Disk: act below {disk} % free.', battery=config['battery_replace'], disk=config['disk_replace']))
