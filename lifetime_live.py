"""Cheap native telemetry. No processes, persistence, or optional dependencies."""
import ctypes
import os
from pathlib import Path
import shutil
import sys
import time


FIELDS = ('cpu_percent', 'ram_percent', 'ram_used_bytes', 'disk_free_bytes',
          'disk_free_percent', 'battery_percent', 'charging', 'network_rx_bytes_sec',
          'network_tx_bytes_sec', 'temperature_c', 'power_watts', 'wifi_signal_percent',
          'battery_health_percent')


class Sampler:
    def __init__(self):
        self.cpu = self.network = None
        self.network_time = None
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True) if sys.platform == 'win32' else None
        self.iphlp = ctypes.WinDLL('iphlpapi') if self.kernel else None

    def _windows(self, sample):
        from ctypes import wintypes as w
        class Memory(ctypes.Structure):
            _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [(key, ctypes.c_ulonglong) for key in
                ('total', 'available', 'page', 'page_available', 'virtual', 'virtual_available', 'extended')]
        class Power(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ubyte) for key in ('ac', 'flag', 'percent', 'reserved')] + [
                ('life', w.DWORD), ('full', w.DWORD)]
        idle, kernel, user = (w.FILETIME() for _ in range(3))
        counter = None
        if self.kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            counter = ticks(idle), ticks(kernel) + ticks(user)
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if self.kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
            sample.update(ram_used_bytes=memory.total-memory.available,
                          ram_percent=100*(memory.total-memory.available)/memory.total if memory.total else None)
        power = Power()
        if self.kernel.GetSystemPowerStatus(ctypes.byref(power)) and power.flag != 255 and not power.flag & 128:
            sample.update(battery_percent=power.percent if power.percent <= 100 else None,
                          charging=bool(power.flag & 8))
        # MIB_IFROW (GetIfTable): fixed native structure, counters are 32 bit.
        class Row(ctypes.Structure):
            _fields_ = [('name', w.WCHAR * 256)] + [(key, w.DWORD) for key in
                ('index', 'type', 'mtu', 'speed', 'phys_len')] + [('phys', ctypes.c_ubyte * 8)] + [
                (key, w.DWORD) for key in ('admin', 'oper', 'changed', 'in_bytes', 'in_ucast', 'in_nucast',
                'in_discards', 'in_errors', 'in_unknown', 'out_bytes', 'out_ucast', 'out_nucast',
                'out_discards', 'out_errors', 'out_queue', 'descr_len')] + [('descr', ctypes.c_ubyte * 256)]
        size = w.ULONG(0)
        network = None
        if self.iphlp.GetIfTable(None, ctypes.byref(size), False) == 122 and size.value <= 1024*1024:
            buf = ctypes.create_string_buffer(size.value)
            if self.iphlp.GetIfTable(buf, ctypes.byref(size), False) == 0:
                count = w.DWORD.from_buffer(buf).value
                if 4 + count*ctypes.sizeof(Row) <= len(buf):
                    rows = (Row * count).from_buffer(buf, 4)
                    network = {r.index: (r.in_bytes, r.out_bytes) for r in rows if r.type != 24 and r.oper == 5}
        return counter, network

    def _linux(self, sample):
        def read(path):
            return Path(path).read_text(encoding='ascii')
        counter = network = None
        try:
            values = [int(v) for v in read('/proc/stat').splitlines()[0].split()[1:9]]
            counter = values[3] + values[4], sum(values)
        except (OSError, ValueError, IndexError):
            pass
        try:
            mem = {line.split(':')[0]: int(line.split()[1])*1024 for line in read('/proc/meminfo').splitlines()}
            total, available = mem['MemTotal'], mem.get('MemAvailable')
            if available is not None and total:
                sample.update(ram_used_bytes=total-available, ram_percent=100*(total-available)/total)
        except (OSError, ValueError, KeyError):
            pass
        try:
            for supply in Path('/sys/class/power_supply').iterdir():
                if read(supply / 'type').strip() == 'Battery':
                    try:
                        percent = int(read(supply / 'capacity'))
                        sample['battery_percent'] = percent if 0 <= percent <= 100 else None
                    except (OSError, ValueError):
                        pass
                    try:
                        status = read(supply / 'status').strip()
                        sample['charging'] = status == 'Charging' if status != 'Unknown' else None
                    except OSError:
                        pass
                    break
        except OSError:
            pass
        try:
            network = {}
            for line in read('/proc/net/dev').splitlines()[2:]:
                name, raw = line.split(':', 1)
                values = raw.split()
                if name.strip() != 'lo':
                    network[name.strip()] = int(values[0]), int(values[8])
        except (OSError, ValueError, IndexError):
            pass
        return counter, network

    def sample(self, slow=None):
        sample = dict.fromkeys(FIELDS)
        slow = slow or {}
        for field in ('temperature_c', 'power_watts', 'wifi_signal_percent', 'battery_health_percent'):
            sample[field] = slow.get(field)
        try:
            cpu, network = self._windows(sample) if self.kernel else self._linux(sample)
        except (OSError, ValueError, AttributeError):
            cpu = network = None
        if cpu and self.cpu:
            idle, total = cpu[0]-self.cpu[0], cpu[1]-self.cpu[1]
            if total > 0 and 0 <= idle <= total:
                sample['cpu_percent'] = 100*(total-idle)/total
        self.cpu = cpu
        now = time.monotonic()
        if network is not None and self.network is not None and self.network_time is not None:
            elapsed = now-self.network_time
            if elapsed > 0:
                deltas = [tuple(a-b for a, b in zip(counters, self.network[name]))
                          for name, counters in network.items() if name in self.network]
                if deltas and all(min(pair) >= 0 for pair in deltas):
                    sample.update(network_rx_bytes_sec=sum(p[0] for p in deltas)/elapsed,
                                  network_tx_bytes_sec=sum(p[1] for p in deltas)/elapsed)
        self.network, self.network_time = network, now
        try:
            disk = shutil.disk_usage(os.environ.get('SystemDrive', 'C:') + '\\' if self.kernel else '/')
            sample.update(disk_free_bytes=disk.free, disk_free_percent=100*disk.free/disk.total if disk.total else None)
        except OSError:
            pass
        return sample


def validate_live(message):
    from lifetime_protocol import ProtocolError, identifier, number, canonical
    if not isinstance(message, dict) or set(message) != {'device_id', 'sample'} or len(canonical(message)) > 4096:
        raise ProtocolError('Invalid live message')
    identifier(message['device_id'])
    sample = message['sample']
    if not isinstance(sample, dict) or set(sample) != set(FIELDS):
        raise ProtocolError('Invalid live sample fields')
    for key, value in sample.items():
        if value is None:
            continue
        if key == 'charging':
            if type(value) is not bool:
                raise ProtocolError('Invalid charging flag')
        elif key == 'temperature_c':
            if type(value) not in (int, float) or not -100 <= value <= 250:
                raise ProtocolError('Invalid temperature')
        elif number(value, integer=key in ('ram_used_bytes', 'disk_free_bytes')) > (
                100 if key.endswith('percent') else 2**53):
            raise ProtocolError('Invalid live value')
    return message


class Reporter:
    """Independent, disposable telemetry. The normal writer owns all durable state."""
    def __init__(self, agent, wake):
        import threading
        self.agent, self.wake = agent, wake
        self.stop = threading.Event()
        self.sampler = Sampler()
        self.failures = 0
        self.link = None
        try:        # the persistent socket is optional: without its package the HTTPS path below does everything
            from lifetime_ws_client import LiveLink
            self.link = LiveLink(agent, wake, self.sampler, self.stop)
        except ImportError:
            pass

    @staticmethod
    def eligible(state):
        return bool(state.get('enabled') and state.get('phase') == 'approved' and state.get('credential')
                    and state.get('policy', {}).get('resources') is True)

    def tick(self, state, request=None):
        from lifetime_tls import request_json
        if not (state.get('enabled') and state.get('phase') == 'approved' and state.get('credential')
                and state.get('policy', {}).get('resources') is True):
            self.failures = 0
            return 1
        try:
            slow = (state.get('readings') or {}).get('performance') or {}
            message = dict(device_id=state['device_id'], sample=self.sampler.sample(slow))
            reply = (request or request_json)(state['bundle'], '/v1/live', message,
                                            credential=state['credential'], timeout=2)
            if (not isinstance(reply, dict) or set(reply) != {'interval', 'sync_now'}
                    or type(reply['interval']) is not int or reply['interval'] not in (1, 5, 10)
                    or type(reply['sync_now']) is not bool):
                raise ValueError('Invalid live response')
            if reply['sync_now']:
                self.wake.set()
            self.failures = 0
            # Keep command detection below five seconds, even without viewers.
            return min(4, reply['interval'])
        except Exception:
            self.failures = min(6, self.failures+1)
            return min(30, 2**(self.failures-1))

    def run(self):
        import json
        import sqlite3
        import lifetime_tracker as tracker
        from lifetime_agent import DATABASE
        db = sqlite3.connect((self.agent.directory / DATABASE).resolve().as_uri() + '?mode=ro', uri=True)
        try:
            while not self.stop.is_set():
                delay = 1
                try:
                    # Same consent gate as normal sync, without the normal writer's
                    # SQLite connection, transaction, or sampling lock.
                    def read_state():
                        with tracker.file_lock(self.agent.directory, 'reporting.lock'):
                            return json.loads(db.execute('SELECT value FROM state WHERE singleton=1').fetchone()[0])
                    state = read_state()
                    if self.link and self.eligible(state):
                        self.link.note_credential(state['credential'])
                    if self.link and self.link.due() and self.eligible(state):
                        # The socket holds no lock while open, so Disconnect is never kept waiting.
                        if self.link.session(state, read_state, self.eligible):
                            self.stop.wait(self.link.retry_at - time.monotonic() if self.link.retry_at > time.monotonic() else 0)
                            continue
                    with tracker.file_lock(self.agent.directory, 'reporting.lock'):
                        delay = self.tick(json.loads(db.execute('SELECT value FROM state WHERE singleton=1').fetchone()[0]))
                except Exception:
                    self.failures = min(6, self.failures+1)
                    delay = min(30, 2**(self.failures-1))
                self.stop.wait(delay)
        finally:
            db.close()
