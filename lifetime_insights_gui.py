"""Energy and cost for this computer on the desktop app's Reports page (lifetime_insights).

Power is measured only from this computer's own on-battery discharge readings, taken every ten minutes while the app
runs and only with the Battery permission; they are kept in insights.json beside the settings (never in the ledger).
Without enough readings the panel uses the wattage the owner set, else a typical one, and says estimated.
"""
import datetime as dt
import json

from PyQt6.QtCore import QTimer, Qt
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
    atomic_json((data_dir() if directory is None else directory) / 'insights.json', value)
    return value


def probe(allowed):
    """(this computer's on-battery draw in watts or None, 'laptop'/'desktop'/None) from the local probes allowed."""
    if 'power' not in allowed:
        return {}
    import lifetime_signals as signals
    power = signals.safe(signals.probe_power)() or {}
    kind = 'desktop' if power.get('state') == 'No battery' else 'laptop' if power.get('percent') is not None else None
    watts = None
    if power.get('state') == 'On battery' and 'battery' in allowed:
        battery = signals.safe(signals.probe_battery)() or {}
        watts = battery.get('watts') or None
    return dict(watts=watts, kind=kind)


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
        saved = load_settings().get('energy')
        try:
            return insights.settings(saved if isinstance(saved, dict) else None)
        except ValueError:
            return insights.settings()

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
        if found and (found.get('watts') or found.get('kind') != self.samples.get('kind')):
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
