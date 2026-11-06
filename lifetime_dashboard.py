"""Qt presentation and nonblocking scheduling for the local system probes."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from lifetime_i18n import tr, language, set_language, on_language_change, date_text, datetime_text, month, weekdays
from html import escape
import sys
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPen, QColor
from PyQt6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QProgressBar, QSizePolicy
from lifetime_i18n_qt import QLabel, QWidget, QFrame
from lifetime_i18n import duration, ui_text

import lifetime_signals as signals
from lifetime_settings import allowed_probes, defaults
from lifetime_charts import LiveSparklines


class ProbeWorker(QWidget):
    """Only poll completed futures on Qt's thread; never wait there.

    Each probe owns at most one future, even after its deadline, so a stalled OS
    call cannot accumulate threads. Late results are discarded. No Qt object is
    accessed from an executor thread.
    """
    changed = pyqtSignal(dict)
    verified = pyqtSignal(object)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.hide()
        self.executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix='lifetime-probe')
        self.jobs = {}
        self.due = {}
        self.values = {}
        self.previous_cpu = None
        self.previous_net = None
        self.previous_stamp = None
        self.closed = False
        self.probes = {name: (getattr(signals, 'probe_'+name), interval) for name, interval in
                       [('power', 2), ('clocks', 2), ('cpu', 2), ('ram', 2), ('disks', 2), ('counters', 2),
                        ('battery', 10), ('network', 10), ('wifi', 10), ('gpu', 10), ('identity', 10)]}
        self.catalog = dict(self.probes)
        self.set_settings(settings or defaults())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def set_settings(self, settings):
        self.allowed = allowed_probes(settings)
        self.probes = {key: value for key, value in self.catalog.items() if key in self.allowed}
        self.values = {key: value for key, value in self.values.items() if key in self.allowed}
        self.previous_cpu = self.previous_net = self.previous_stamp = None
        for key, (future, started, expired) in list(self.jobs.items()):
            if key not in self.allowed and key != 'verify':
                future.cancel()
                self.jobs[key] = (future, started, True)

    def poll(self):
        if self.closed:
            return
        now = time.monotonic()
        dirty = False
        for key, (future, started, expired) in list(self.jobs.items()):
            if not future.done() and now-started <= 5:
                continue
            result = None
            if future.done() and not expired and now-started <= 5:
                try:
                    result = future.result()
                except Exception:
                    pass
            if not expired:
                if key == 'verify':
                    self.verified.emit(result)
                else:
                    if key == 'cpu':
                        raw = result
                        result = signals.cpu_percent(self.previous_cpu, raw)
                        self.previous_cpu = raw
                    elif key == 'counters':
                        raw = result
                        result = signals.counter_rates(self.previous_net, raw or {},
                                                       now-self.previous_stamp if self.previous_stamp else 0)
                        self.previous_net, self.previous_stamp = raw, now
                    self.values[key] = result
                    dirty = True
            if future.done():
                del self.jobs[key]
            else:
                self.jobs[key] = (future, started, True)
        for key, (probe, interval) in self.probes.items():
            if key not in self.jobs and now >= self.due.get(key, 0):
                self.jobs[key] = (self.executor.submit(probe), now, False)
                self.due[key] = now+interval
        if dirty:
            self.changed.emit(dict(self.values))

    def verify(self, reader):
        if 'verify' in self.jobs or self.closed:
            return False
        def work():
            try:
                ledger = reader()
                error = None
            except Exception as exc:
                ledger, error = None, str(exc)
            return dict(ledger=ledger, ledger_error=error, verification=signals.verify_awake())
        self.jobs['verify'] = (self.executor.submit(work), time.monotonic(), False)
        return True

    def stop(self):
        self.closed = True
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)


class RefreshGlyph(QWidget):
    """A small refresh affordance that turns while verification runs."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(18, 18)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def tick(self):
        self.angle = (self.angle+24) % 360
        self.update()

    def set_busy(self, busy):
        self.timer.start(60) if busy else self.timer.stop()
        self.angle = 0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(9, 9)
        p.rotate(self.angle)
        p.setPen(QPen(QColor(self.window().palette_tokens['muted']), 1.5))
        p.drawArc(-6, -6, 12, 12, 40*16, 285*16)
        p.drawLine(6, -6, 6, -1)
        p.drawLine(2, -1, 6, -1)


def fmt(value, unit='', digits=1):
    if value is None:
        return None
    return (f'{value:,.{digits}f}', unit)


def span(seconds):
    if seconds is None:
        return None
    return duration(seconds, days=False)


def storage(used, total, unit='GiB', divisor=1024**3):
    if used is None or total is None:
        return None
    return (f'{used/divisor:,.1f} / {total/divisor:,.1f}', unit)


class SignalCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('signalCard')
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 12, 20, 14)
        self.box.setSpacing(10)
        heading = QLabel(tr(title))
        heading.setWordWrap(True)
        heading.setObjectName('groupTitle')
        self.box.addWidget(heading)
        self.rows = QWidget()
        self.grid = QGridLayout(self.rows)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(0, 2)
        self.grid.setColumnStretch(1, 3)
        self.box.addWidget(self.rows)
        self.box.addStretch()
        self.fields = {}

    def set_rows(self, rows, tokens):
        keys = [r[0] for r in rows]
        if list(self.fields) != keys:
            while self.grid.count():
                self.grid.takeAt(0).widget().deleteLater()
            self.fields = {}
            position = 0
            for key, _, percentage in rows:
                title = QLabel(ui_text(key))
                title.setObjectName('signalLabel')
                title.setWordWrap(True)
                value = QLabel()
                value.setObjectName('signalValue')
                value.setWordWrap(True)
                value.setTextFormat(Qt.TextFormat.RichText)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                font = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
                font.setPixelSize(15)
                if hasattr(font, 'setFeature'):
                    font.setFeature(QFont.Tag('tnum'), 1)
                value.setFont(font)
                self.grid.addWidget(title, position, 0)
                self.grid.addWidget(value, position, 1)
                meter = QProgressBar()
                meter.setTextVisible(False)
                meter.setRange(0, 1000)
                meter.setFixedHeight(3)
                self.grid.addWidget(meter, position+1, 0, 1, 2)
                self.fields[key] = (value, meter)
                position += 2
        for key, data, percentage in rows:
            value, meter = self.fields[key]
            if data is None:
                text = f'<span style="color:{tokens["muted"]}">{tr("Not available")}</span>'
            elif isinstance(data, tuple):
                text = escape(str(data[0])) + f' <span style="color:{tokens["muted"]}">{escape(data[1])}</span>'
            else:
                text = escape(str(ui_text(data)))
            value.setText(text)
            meter.setVisible(percentage is not None)
            if percentage is not None:
                meter.setValue(round(max(0, min(100, percentage))*10))
                meter.setAccessibleName(f'{key}: {percentage:.1f}%')


class SystemDashboard(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel(tr('System'))
        title.setObjectName('pageTitle')
        header.addWidget(title)
        header.addStretch()
        self.freshness = QLabel(tr('Reading local system…'))
        self.freshness.setObjectName('caption')
        header.addWidget(self.freshness)
        layout.addLayout(header)
        self.empty = QLabel(tr('System readings are off. Choose what to collect in Settings.'))
        self.empty.setObjectName('caption')
        self.empty.setWordWrap(True)
        self.empty.hide()
        layout.addWidget(self.empty)
        self.sparklines = LiveSparklines()
        layout.addWidget(self.sparklines)
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        self.cards = [SignalCard(title) for title in (tr('Power'), tr('Network'), tr('Graphics'), tr('Computer'))]
        self.columns = 0
        layout.addLayout(self.grid)
        self.set_columns(2)
        self.settings = dict(battery=True, network=True, wifi_name=True, graphics=True, resources=True)

    def set_settings(self, settings):
        self.settings = settings
        self.sparklines.set_settings(settings)
        any_enabled = any(settings.get(key) for key in ('battery', 'network', 'wifi_name', 'graphics', 'resources'))
        self.empty.setVisible(not any_enabled)
        self.freshness.setVisible(any_enabled)
        for card, visible in zip(self.cards, (settings.get('battery'), settings.get('network') or settings.get('wifi_name'),
                                              settings.get('graphics'), settings.get('resources'))):
            card.setVisible(bool(visible))

    def set_columns(self, count):
        if count == self.columns:
            return
        for card in self.cards:
            self.grid.removeWidget(card)
        for i, card in enumerate(self.cards):
            self.grid.addWidget(card, i//count, i % count)
        for i in range(2):
            self.grid.setColumnStretch(i, 1 if i < count else 0)
        self.columns = count

    def update_values(self, data, tokens):
        self.sparklines.update_values(data)
        p, b = data.get('power') or {}, data.get('battery') or {}
        rate = '0 W · battery idle' if p.get('ac') and b.get('watts') == 0 else fmt(b.get('watts'), 'W')
        power = [('Battery', fmt(p.get('percent'), '%', 0), p.get('percent')),
                 ('State', p.get('state'), None), ('Power', rate, None),
                 ('Time remaining', span(p.get('remaining')), None),
                 ('Capacity', storage(b.get('capacity_wh'), b.get('full_wh'), 'Wh', 1), None),
                 ('Voltage', fmt(b.get('voltage'), 'V', 2), None)]
        if b.get('design_wh'):
            power += [('Design capacity', fmt(b['design_wh'], 'Wh'), None), ('Battery wear', fmt(b.get('wear'), '%'), None)]
        if p.get('state') == 'No battery':
            power = [('Battery', 'No battery', None)]
        if len(b.get('packs', [])) > 1:
            for index, pack in enumerate(b['packs'], 1):
                power += [(tr('battery.capacity', index=index), storage(pack.get('capacity_wh'), pack.get('full_wh'), 'Wh', 1), None),
                          (tr('battery.power', index=index), fmt(pack.get('watts'), 'W'), None)]
        wifi = next((row for row in data.get('wifi') or [] if str(row.get('state', '')).lower() == 'connected'), {})
        network = []
        for adapter in data.get('network') or []:
            network.append((str(adapter.get('Name') or 'Adapter'),
                            (adapter.get('Type') or adapter.get('InterfaceDescription') or 'Network') +
                            (' · ' + adapter['LinkSpeed'] if adapter.get('LinkSpeed') else ''), None))
        if not network:
            network.append(('Active adapters', 'No active adapters' if data.get('network') == [] else None, None))
        counters = data.get('counters') or {}
        network += [('Download', fmt(counters.get('down')/1024 if counters.get('down') is not None else None, 'KiB/s'), None),
                    ('Upload', fmt(counters.get('up')/1024 if counters.get('up') is not None else None, 'KiB/s'), None),
                    ('Wi-Fi SSID', wifi.get('ssid') if wifi else 'Disconnected' if data.get('wifi') else None, None),
                    ('Wi-Fi signal', fmt(wifi.get('signal'), '%', 0), wifi.get('signal')),
                    ('Band / channel', f'{wifi.get("band") or tr("Not available")} / {int(wifi["channel"]) if wifi.get("channel") is not None else tr("Not available")}' if wifi else None, None),
                    ('Wi-Fi receive', fmt(wifi.get('rx_mbps'), 'Mbps'), None),
                    ('Wi-Fi transmit', fmt(wifi.get('tx_mbps'), 'Mbps'), None)]
        if not self.settings.get('wifi_name'):
            network = [row for row in network if not row[0].startswith(('Wi-Fi', 'Band /'))]
        elif not self.settings.get('network'):
            network = [row for row in network if row[0].startswith(('Wi-Fi', 'Band /'))]
        if self.settings.get('wifi_name') and data.get('wifi') == []:
            network = [row for row in network if not row[0].startswith(('Wi-Fi', 'Band /'))]
            network.append(('Wi-Fi', 'No Wi-Fi adapter', None))
        graphics = []
        if data.get('gpu') == []:
            graphics = [('Graphics card', 'No graphics adapter detected', None)]
        for index, gpu in enumerate(data.get('gpu') or []):
            prefix = f'GPU {index+1} ' if len(data['gpu']) > 1 else ''
            graphics += [(tr('gpu.field', prefix=prefix, field=tr('Model')), gpu.get('name'), None), (tr('gpu.field', prefix=prefix, field=tr('Power')), fmt(gpu.get('watts'), 'W'), None),
                         (tr('gpu.field', prefix=prefix, field=tr('Temperature')), fmt(gpu.get('temperature'), '°C', 0), None),
                         (tr('gpu.field', prefix=prefix, field=tr('Utilization')), fmt(gpu.get('utilization'), '%', 0), gpu.get('utilization')),
                         (tr('gpu.field', prefix=prefix, field=tr('Memory')), storage(gpu.get('memory_used'), gpu.get('memory_total'), 'MiB', 1), None)]
        if not graphics:
            graphics = [('Graphics card', None, None)]
        ram, clocks, identity = data.get('ram') or {}, data.get('clocks') or {}, data.get('identity') or {}
        boot = identity.get('boot') or clocks.get('boot')
        computer = [('Processor', identity.get('cpu'), None),
                    ('CPU', fmt(data.get('cpu'), '%', 0), data.get('cpu')),
                    ('RAM', storage(ram.get('used'), ram.get('total')), ram.get('percent'))]
        for disk in data.get('disks') or []:
            computer.append((tr('disk', path=disk['path']), storage(disk.get('used'), disk.get('total')), disk.get('percent')))
        if not data.get('disks'):
            computer.append(('Disks', None, None))
        computer += [('Uptime · incl. sleep', span(clocks.get('uptime')), None),
                     ('Awake since boot', span(clocks.get('awake')), None),
                     ('Last boot', datetime_text(dt.datetime.fromtimestamp(boot)) if boot else None, None),
                     ('Fast Startup', 'On' if clocks.get('fast_startup') else 'Off' if clocks.get('fast_startup') is False else None, None)]
        for card, rows in zip(self.cards, (power, network, graphics, computer)):
            card.set_rows(rows, tokens)
        self.freshness.setText(tr('Local readings · 2 s / 10 s'))
