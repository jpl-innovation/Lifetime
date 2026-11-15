"""Fleet load test for Lifetime Server. A developer tool: not shipped in any package.

It starts a TEST server in a temporary directory on 127.0.0.1 (a separate process, so its CPU and memory can be
measured on their own), enrolls N simulated computers through the real pairing path (code, enroll, approve, claim),
and runs them for a while the way real computers behave with a dashboard open:

- every computer syncs over pinned HTTPS at the server's reporting interval (30 s by default) with a usage record,
  30 days of history, hourly activity and readings, and resends records that were not acknowledged;
- every computer keeps a live socket and sends telemetry at the rate the server asks for (2 s while someone watches
  the fleet, 0.5 s for the computer being looked at);
- a viewer holds the dashboard's live stream and polls the overview and fleet pages every 5 s;
- computers drop off at random and come back (5 to 30 s), and a few go offline for good to open "offline" incidents;
- optionally M agentless TCP checks run against listeners on 127.0.0.1.

Measured: server CPU and memory (psutil when it is already installed, otherwise the operating system's own counters),
SQLite and WAL sizes, live update latency (computer to dashboard stream) P50/P95/P99, samples that never reached the
dashboard stream, sync latency and errors, dashboard call latency, and incident engine lag (how long after a computer
had been offline for the rule's duration its incident opened). Writes report.json and report.md.

    python tools/fleet_simulator.py                          # 50 computers for 2 minutes (fits a small PC)
    python tools/fleet_simulator.py --devices 200 --duration 600 --checks 20
    python tools/fleet_simulator.py --devices 500 --duration 900 --checks 50   # a machine with 8+ GB free

Safety: it only ever talks to the server it started itself on 127.0.0.1. Pointing it at another server (--attach-*)
enrolls fake computers there and needs --i-understand; the fake computers are removed again at the end.
"""
import argparse
import asyncio
import concurrent.futures
import contextlib
import datetime as dt
import functools
import json
import math
import os
import platform
import random
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lifetime_tls as tls  # noqa: E402
import lifetime_ws as ws  # noqa: E402
from lifetime_admin import AdminClient  # noqa: E402
from lifetime_live import FIELDS  # noqa: E402

POLICY = dict(history=True, resources=True, health=False, control=False)
SPECS = dict(manufacturer='Simulated', model='Load test', cpu_name='Simulated CPU', cpu_cores=4, cpu_threads=8,
             ram_bytes=16 * 2**30, gpu_names=['Simulated GPU'], disks=[dict(name='C:', total_bytes=512 * 2**30,
                                                                           free_bytes=200 * 2**30)],
             os_name='Windows 11', os_version='10.0.26100', battery_design_wh=None, mac_addresses=[], hostname='SIM')
OFFLINE_RULE_MINUTES = 1    # the shortest offline rule, so a short run can see incidents open
SAMPLE_EVERY = 2.0          # seconds between process and storage measurements

# The test server: a throw-away directory, run without elevation, so the Administrators-only ACL is skipped (exactly
# what the unit tests do). It stops when the simulator closes its standard input.
BOOT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import lifetime_permissions
if sys.platform == 'win32':
    lifetime_permissions.windows_acl = lambda *arguments: None
from lifetime_server import ServerRuntime
runtime = ServerRuntime(sys.argv[2], address='https://127.0.0.1:8443', host='127.0.0.1', port=0, admin_port=0).start()
print(json.dumps(dict(public=runtime.public_address, admin=runtime.admin_address)), flush=True)
sys.stdin.read()
runtime.stop()
'''


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def percentiles(values):
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda p: ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]     # nearest rank
    return dict(count=len(ordered), p50=round(pick(50), 1), p95=round(pick(95), 1), p99=round(pick(99), 1),
                max=round(ordered[-1], 1))


def _category(error):
    status = getattr(error, 'status', None)
    if isinstance(status, int):
        return f'http-{status}'
    for kind, name in ((TimeoutError, 'timeout'), (ssl.SSLError, 'tls'), (ConnectionError, 'dropped'), (OSError, 'network')):
        if isinstance(error, kind):
            return name
    return type(error).__name__


# ---- process and machine measurements (psutil only if it is already installed) ----

class ProcessMeter:
    """Total CPU seconds and resident memory of one process."""

    def __init__(self, pid):
        self.pid, self.method = pid, None
        try:
            import psutil
            self._process, self.method = psutil.Process(pid), 'psutil'
        except Exception:
            if sys.platform == 'win32':
                import ctypes
                self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                self._kernel.OpenProcess.restype = ctypes.c_void_p
                self._handle = self._kernel.OpenProcess(0x1000, False, pid)    # PROCESS_QUERY_LIMITED_INFORMATION
                self.method = 'GetProcessTimes/K32GetProcessMemoryInfo' if self._handle else None
            elif Path(f'/proc/{pid}/stat').exists():
                self.method = '/proc'

    def read(self):
        """(cpu seconds, resident bytes) or None."""
        try:
            if self.method == 'psutil':
                times = self._process.cpu_times()
                return times.user + times.system, self._process.memory_info().rss
            if self.method == '/proc':
                fields = Path(f'/proc/{self.pid}/stat').read_text().rsplit(')', 1)[1].split()
                cpu = (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
                status = Path(f'/proc/{self.pid}/status').read_text()
                rss = next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith('VmRSS:'))
                return cpu, rss
            if self.method:
                import ctypes
                from ctypes import wintypes
                times = [wintypes.FILETIME() for _ in range(4)]
                if not self._kernel.GetProcessTimes(ctypes.c_void_p(self._handle), *map(ctypes.byref, times)):
                    return None
                ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime

                class Counters(ctypes.Structure):
                    _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                        (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'a', 'b', 'c', 'd', 'pagefile', 'peak_pagefile')]
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                if not self._kernel.K32GetProcessMemoryInfo(ctypes.c_void_p(self._handle), ctypes.byref(counters), counters.cb):
                    return None
                return (ticks(times[2]) + ticks(times[3])) / 1e7, counters.ws
        except (OSError, ValueError, StopIteration):
            return None
        return None


def machine():
    info = dict(system=platform.system(), release=platform.release(), version=platform.version(),
                processor=platform.processor() or platform.machine(), logical_cpus=os.cpu_count(),
                python=platform.python_version(), total_ram_gb=None)
    try:
        if sys.platform == 'win32':
            import ctypes

            class Memory(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ('total', 'free', 'a', 'b', 'c', 'd', 'e')]
            memory = Memory()
            memory.length = ctypes.sizeof(memory)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                info.update(total_ram_gb=round(memory.total / 2**30, 1), free_ram_gb_at_start=round(memory.free / 2**30, 1))
        else:
            meminfo = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
            info.update(total_ram_gb=round(meminfo['MemTotal'] / 2**20, 1),
                        free_ram_gb_at_start=round(meminfo.get('MemAvailable', 0) / 2**20, 1))
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    return info


# ---- one simulated computer ----

class Device:
    def __init__(self, index, bundle_text):
        self.index, self.id = index, str(uuid.uuid4())
        self.bundle = tls.decode_bundle(bundle_text)
        self.code = self.bundle['code']
        self.request_id, self.poll_token = str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex
        self.credential = None
        self.seq, self.measured, self.unacked = 0, 0.0, []
        self.offline_until, self.gone_at = 0.0, None     # a short drop-out; gone for good (an outage)
        self.connection = None
        self.sent = 0

    @property
    def offline(self):
        return self.gone_at is not None or time.perf_counter() < self.offline_until

    def drop(self):
        if self.connection is not None:
            with contextlib.suppress(Exception):
                self.connection.transport.abort()    # abrupt, like a cable pulled or a laptop lid closed

    def enroll_message(self):
        return dict(version=1, request_id=self.request_id, device_id=self.id, code=self.code, poll_token=self.poll_token,
                    identity=dict(display_name=f'SIM-{self.index:04d}', department=f'Load group {self.index % 5}'),
                    policy=POLICY)

    def sync_message(self, interval):
        now = _now()
        self.seq += 1
        self.measured += interval
        self.unacked = (self.unacked + [dict(seq=self.seq, observed_at=_stamp(now), awake_seconds=float(interval),
                                             measured_seconds=float(interval), imported_seconds=0.0,
                                             totals=dict(measured_seconds=self.measured, imported_seconds=0.0,
                                                         baseline_seconds=0.0))])[-100:]
        today = now.date()
        history = dict(daily={(today - dt.timedelta(days=day)).isoformat(): float(random.randint(3600, 36000))
                              for day in range(30)},
                       first_seen=_stamp(now - dt.timedelta(days=400)), first_seen_source='tracking-start',
                       recording_started=_stamp(now - dt.timedelta(days=30)),
                       hourly=[float(random.randint(0, 3600)) for _ in range(24)],
                       sessions=[dict(start=_stamp(now - dt.timedelta(hours=8 * n)), seconds=float(random.randint(600, 20000)))
                                 for n in range(20)], boots=random.randint(10, 500))
        performance = dict(cpu_percent=random.uniform(1, 60), ram_used_bytes=random.randint(4, 12) * 2**30,
                           disk_free_bytes=random.randint(100, 300) * 2**30, battery_percent=None, charging=None,
                           battery_health_percent=None, power_watts=None, network_kind='ethernet',
                           wifi_signal_percent=None, temperature_c=None)
        readings = dict(observed_at=_stamp(now), system='Windows', release='11', machine='AMD64', python_version='3.12',
                        specs=dict(SPECS, hostname=f'SIM-{self.index:04d}'), performance=performance)
        return dict(version=1, device_id=self.id, policy_version=1, policy=POLICY, records=list(self.unacked),
                    history=history, readings=readings, client_version='1.2.0-sim')

    @staticmethod
    def sample():
        value = dict.fromkeys(FIELDS)
        used = random.randint(4, 12) * 2**30
        value.update(cpu_percent=random.uniform(1, 80), ram_used_bytes=used, ram_percent=100 * used / (16 * 2**30),
                     disk_free_bytes=200 * 2**30, disk_free_percent=39.0, network_rx_bytes_sec=random.uniform(0, 1e6),
                     network_tx_bytes_sec=random.uniform(0, 1e5))
        return value


# ---- the run ----

class Simulation:
    def __init__(self, args):
        self.args = args
        self.stop = asyncio.Event()
        self.pool = concurrent.futures.ThreadPoolExecutor(16, thread_name_prefix='sim-https')
        self.admin_pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix='sim-admin')
        self.devices = []
        self.result = dict(sync=dict(latency=[], errors={}, requests=0), dashboard=dict(latency=[], errors=0),
                           live=dict(latency=[], received={}, reconnects=0, refused=0), churn=0,
                           samples=[], incidents={})

    async def admin(self, operation, **arguments):
        return await asyncio.get_running_loop().run_in_executor(
            self.admin_pool, functools.partial(self.client.call, operation, **arguments))

    async def https(self, device, path, message, credential=None):
        return await asyncio.get_running_loop().run_in_executor(
            self.pool, functools.partial(tls.request_json, device.bundle, path, message, credential=credential, timeout=20))

    # -- enrollment through the real path: code, enroll, approve, claim
    async def enroll(self):
        started = time.perf_counter()
        certificate = (self.directory / 'server-cert.pem').read_text(encoding='ascii')
        remaining = self.args.devices
        while remaining:
            uses = min(100, remaining)
            issued = await self.admin('code', ttl=3600, uses=uses)
            bundle = tls.encode_bundle(self.address, issued['code'], certificate)
            self.devices += [Device(len(self.devices) + n, bundle) for n in range(uses)]
            remaining -= uses
        await asyncio.gather(*(self.https(device, '/v1/enroll', device.enroll_message()) for device in self.devices))
        mine = {device.request_id for device in self.devices}
        while True:
            pending = [item for item in await self.admin('pending_enrollments', limit=500) if item['request_id'] in mine]
            if not pending:
                break
            for item in pending:
                await self.admin('approve', request_id=item['request_id'])
        replies = await asyncio.gather(*(self.https(device, '/v1/claim', dict(request_id=device.request_id,
                                                                              poll_token=device.poll_token))
                                         for device in self.devices))
        for device, reply in zip(self.devices, replies):
            device.credential = reply.get('credential')
        missing = sum(device.credential is None for device in self.devices)
        if missing:
            raise RuntimeError(f'{missing} simulated computers were not approved')
        return round(time.perf_counter() - started, 2)

    # -- one computer's HTTPS sync
    async def sync_loop(self, device):
        interval = 30
        await asyncio.sleep(random.uniform(0, interval))
        while not self.stop.is_set():
            if not device.offline:
                message = device.sync_message(interval)
                started = time.perf_counter()
                self.result['sync']['requests'] += 1
                try:
                    reply = await self.https(device, '/v1/sync', message, device.credential)
                    self.result['sync']['latency'].append((time.perf_counter() - started) * 1000)
                    done = set(reply['accepted']) | set(reply['duplicates']) | set(reply['retired'])
                    device.unacked = [record for record in device.unacked if record['seq'] not in done]
                    interval = reply.get('reporting_interval', interval)
                except Exception as error:
                    errors = self.result['sync']['errors']
                    errors[_category(error)] = errors.get(_category(error), 0) + 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop.wait(), interval * random.uniform(.9, 1.1))

    # -- one computer's live socket
    async def live_loop(self, device):
        from websockets.asyncio.client import connect
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        context.load_verify_locations(cadata=device.bundle['certificate_pem'])
        _, host, port = tls._address(device.bundle['address'])
        url = f'wss://{host}:{port}{ws.PATH}?device={device.id}'
        first = True
        while not self.stop.is_set():
            if device.offline:
                await asyncio.sleep(.5)
                continue
            if not first:
                self.result['live']['reconnects'] += 1
            first = False
            try:
                async with connect(url, ssl=context, additional_headers={'Authorization': 'Bearer ' + device.credential},
                                   open_timeout=10, ping_interval=15, ping_timeout=15, close_timeout=2,
                                   max_size=ws.MAX_FRAME, compression=None, proxy=None, user_agent_header=None) as connection:
                    tls.check_peer(connection.transport.get_extra_info('ssl_object').getpeercert(binary_form=True),
                                   device.bundle)
                    device.connection = connection
                    await connection.send(ws.envelope('hello', dict(version=ws.VERSION, capabilities=['telemetry'])))
                    rate, next_send = 10000, time.perf_counter()
                    while not self.stop.is_set() and not device.offline:
                        wait = max(.001, min(next_send - time.perf_counter(), 1.0))
                        try:
                            message = ws.parse(await asyncio.wait_for(connection.recv(), wait))
                            data = message.get('data')
                            if message['type'] in ('hello', 'status') and isinstance(data, dict) and 'rate_ms' in data:
                                faster = ws.clamp_rate(data['rate_ms'])
                                if faster < rate:
                                    next_send = min(next_send, time.perf_counter() + faster / 1000)
                                rate = faster
                        except asyncio.TimeoutError:
                            pass
                        if time.perf_counter() >= next_send:
                            await connection.send(ws.envelope('telemetry', device.sample()))
                            device.sent += 1
                            next_send = time.perf_counter() + rate / 1000
            except Exception as error:
                if getattr(getattr(error, 'response', None), 'status_code', None) == 403:
                    self.result['live']['refused'] += 1
                await asyncio.sleep(random.uniform(.3, 1.0))
            finally:
                device.connection = None

    # -- the dashboard: live stream with a computer opened, and the overview and fleet pages every 5 s
    async def viewer(self):
        from websockets.asyncio.client import connect
        token = (self.directory / 'admin-token').read_text(encoding='ascii').strip()
        received, latency = self.result['live']['received'], self.result['live']['latency']
        while not self.stop.is_set():
            try:
                async with connect(f'ws://127.0.0.1:{self.admin_port}/v1/stream', open_timeout=10, proxy=None,
                                   additional_headers={'Authorization': 'Bearer ' + token}) as stream:
                    next_watch = 0.0
                    while not self.stop.is_set():
                        if time.perf_counter() >= next_watch:      # a viewer's interest lasts 20 s on the server
                            await stream.send(ws.envelope('status', dict(watch=self.devices[0].id)))
                            next_watch = time.perf_counter() + 10
                        try:
                            message = json.loads(await asyncio.wait_for(stream.recv(), 1))
                        except asyncio.TimeoutError:
                            continue
                        data = message.get('data') or {}
                        if message.get('type') == 'telemetry' and isinstance(data.get('ts'), int):
                            latency.append(time.time() * 1000 - data['ts'])
                            received[data['device_id']] = received.get(data['device_id'], 0) + 1
            except Exception:
                await asyncio.sleep(1)

    async def dashboard(self):
        while not self.stop.is_set():
            for operation, arguments in (('overview', {}), ('fleet', dict(limit=25))):
                started = time.perf_counter()
                try:
                    await self.admin(operation, **arguments)
                    self.result['dashboard']['latency'].append((time.perf_counter() - started) * 1000)
                except Exception:
                    self.result['dashboard']['errors'] += 1
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop.wait(), 5)

    # -- drop-outs and outages
    async def churn(self):
        per_tick = self.args.churn / 6      # checked every 10 s
        while not self.stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop.wait(), 10)
            for device in self.devices[1:]:     # the computer the viewer has open stays connected
                if not device.offline and random.random() < per_tick:
                    device.offline_until = time.perf_counter() + random.uniform(5, 30)
                    device.drop()
                    self.result['churn'] += 1

    async def outages(self, count, after):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stop.wait(), after)
        if self.stop.is_set():
            return
        for device in random.sample(self.devices[1:], min(count, len(self.devices) - 1)):
            device.gone_at = _now()
            device.drop()

    # -- measurements
    async def sampler(self):
        server = ProcessMeter(self.server_pid) if self.server_pid else None
        own = ProcessMeter(os.getpid())
        names = ('server.sqlite', 'server.sqlite-wal', 'monitoring.sqlite', 'monitoring.sqlite-wal',
                 'access.sqlite', 'access.sqlite-wal')
        while not self.stop.is_set():
            sizes = {}
            for name in names:
                with contextlib.suppress(OSError):
                    sizes[name] = (self.directory / name).stat().st_size
            self.result['samples'].append(dict(t=time.perf_counter(), server=server.read() if server else None,
                                               simulator=own.read(), sizes=sizes))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop.wait(), SAMPLE_EVERY)
        self.result['meter'] = server.method if server else None

    # -- checks against local listeners
    async def checks(self):
        servers = []

        async def answer(reader, writer):
            writer.close()
        for _ in range(self.args.checks):
            servers.append(await asyncio.start_server(answer, '127.0.0.1', 0))
        if servers:
            await self.admin('set_check_scopes', scopes=['127.0.0.1/32'])
            for number, server in enumerate(servers):
                await self.admin('save_check', check=dict(name=f'Simulated device {number}', kind='tcp', host='127.0.0.1',
                                                          port=server.sockets[0].getsockname()[1], interval=30, timeout=2))
        return servers

    async def run(self):
        args = self.args
        if args.attach_directory:
            self.directory, self.address, self.admin_port = Path(args.attach_directory), args.attach_address, args.attach_admin_port
            self.server_pid = args.attach_pid
        else:
            self.temporary = tempfile.TemporaryDirectory(prefix='lifetime-fleet-', ignore_cleanup_errors=True)
            self.directory = Path(self.temporary.name) / 'server'
            environment = dict(os.environ, LIFETIME_DATA_DIR=str(Path(self.temporary.name) / 'data'))
            self.process = subprocess.Popen([sys.executable, '-I', '-c', BOOT, str(ROOT), str(self.directory)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=environment)
            ready = json.loads(await asyncio.get_running_loop().run_in_executor(None, self.process.stdout.readline))
            self.address, self.admin_port = ready['public'], int(ready['admin'].rsplit(':', 1)[1])
            self.server_pid = self.process.pid
        self.client = AdminClient(self.directory, self.admin_port, timeout=30)
        servers = []
        try:
            if not args.attach_directory:
                await self.admin('set_alert_rule', rule='offline', minutes=OFFLINE_RULE_MINUTES)
                servers = await self.checks()
            enrollment = await self.enroll()
            tasks = [asyncio.create_task(job) for job in (
                self.sampler(), self.viewer(), self.dashboard(), self.churn(),
                *(self.sync_loop(device) for device in self.devices), *(self.live_loop(device) for device in self.devices))]
            outage_after = min(20, args.duration / 4)
            outage_count = 0 if args.attach_directory else args.outages
            tasks.append(asyncio.create_task(self.outages(outage_count, outage_after)))
            started = time.perf_counter()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop.wait(), args.duration)
            self.stop.set()
            for device in self.devices:
                device.drop()
            await asyncio.wait(tasks, timeout=30)
            elapsed = time.perf_counter() - started
            incidents = await self.admin('incidents', state='', limit=500)
            check_rows = await self.admin('checks') if servers else []
            server_log = await self.admin('server_log', limit=100)
        finally:
            for server in servers:
                server.close()
            if args.attach_directory:
                for device in self.devices:
                    with contextlib.suppress(Exception):
                        await self.admin('delete', device_id=device.id)
            else:
                self.process.stdin.close()
                await asyncio.get_running_loop().run_in_executor(None, functools.partial(self.process.wait, 60))
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.admin_pool.shutdown(wait=False, cancel_futures=True)
        return self.report(enrollment, elapsed, incidents, check_rows, server_log, outage_after)

    def report(self, enrollment, elapsed, incidents, check_rows, server_log, outage_after):
        args, result = self.args, self.result
        samples = result['samples']
        cpu, rss = [], []
        for before, after in zip(samples, samples[1:]):
            if before['server'] and after['server']:
                cpu.append(100 * (after['server'][0] - before['server'][0]) / (after['t'] - before['t']))
        rss = [sample['server'][1] / 2**20 for sample in samples if sample['server']]
        own = [sample['simulator'] for sample in samples if sample['simulator']]
        own_cpu = 100 * (own[-1][0] - own[0][0]) / (samples[-1]['t'] - samples[0]['t']) if len(own) > 1 else None
        size = lambda name, how: round(how(sample['sizes'].get(name, 0) for sample in samples) / 2**20, 2) if samples else None
        sent = sum(device.sent for device in self.devices)
        received = sum(result['live']['received'].values())
        gone = {device.id: device.gone_at for device in self.devices if device.gone_at}
        lags, false_incidents = [], 0
        for incident in incidents:
            if incident['rule'] == 'offline' and incident['device_id'] in gone and incident.get('opened_at'):
                due = gone[incident['device_id']] + dt.timedelta(minutes=OFFLINE_RULE_MINUTES)
                lags.append((dt.datetime.fromisoformat(incident['opened_at'].replace('Z', '+00:00')) - due).total_seconds())
            elif incident.get('opened_at') and not incident['device_id'].startswith('check:'):
                false_incidents += 1
        measurable = elapsed >= outage_after + OFFLINE_RULE_MINUTES * 60 + 20
        return dict(
            label='single run', date=_stamp(_now()), machine=machine(),
            configuration=dict(devices=args.devices, duration_s=args.duration, measured_s=round(elapsed, 1),
                               checks=args.checks, churn_per_minute=args.churn, outages=len(gone),
                               offline_rule_minutes=OFFLINE_RULE_MINUTES,
                               target='attached server' if args.attach_directory else 'test server started on 127.0.0.1'),
            enrollment_seconds=enrollment,
            server_process=dict(method=result.get('meter'), cpu_percent_of_one_core=dict(
                mean=round(sum(cpu) / len(cpu), 1) if cpu else None, **(percentiles(cpu) or {})),
                rss_mb=dict(start=round(rss[0], 1), peak=round(max(rss), 1), end=round(rss[-1], 1)) if rss else None),
            simulator_cpu_percent_of_one_core=round(own_cpu, 1) if own_cpu is not None else None,
            storage_mb=dict(server_sqlite_end=size('server.sqlite', lambda v: list(v)[-1]),
                            server_wal_peak=size('server.sqlite-wal', max),
                            monitoring_sqlite_end=size('monitoring.sqlite', lambda v: list(v)[-1]),
                            monitoring_wal_peak=size('monitoring.sqlite-wal', max),
                            access_sqlite_end=size('access.sqlite', lambda v: list(v)[-1])),
            live=dict(sent=sent, reached_dashboard=received, missing=max(0, sent - received),
                      missing_percent=round(100 * max(0, sent - received) / sent, 2) if sent else None,
                      reconnects=result['live']['reconnects'], refused=result['live']['refused'],
                      latency_ms=percentiles(result['live']['latency'])),
            sync=dict(requests=result['sync']['requests'], errors=result['sync']['errors'],
                      unacknowledged_at_end=sum(len(device.unacked) for device in self.devices),
                      latency_ms=percentiles(result['sync']['latency'])),
            dashboard=dict(calls=len(result['dashboard']['latency']), errors=result['dashboard']['errors'],
                           latency_ms=percentiles(result['dashboard']['latency'])),
            drop_outs=result['churn'],
            incidents=dict(outages=len(gone), opened=len(lags), lag_s=percentiles(lags) if lags else None,
                           measured=measurable, other_incidents=false_incidents),
            checks=dict(configured=len(check_rows), ok=sum(row.get('last_category') == 'ok' for row in check_rows),
                        latency_ms=percentiles([row['last_latency_ms'] for row in check_rows if row.get('last_latency_ms') is not None])),
            server_log_refusals=sum(row['count'] for row in server_log if row['kind'] in ('report-rejected', 'enrollment-refused')))


def markdown(report):
    c, s, live, sync = report['configuration'], report['server_process'], report['live'], report['sync']
    fmt = lambda p, unit='': 'not measured' if not p else f"P50 {p['p50']}{unit}, P95 {p['p95']}{unit}, P99 {p['p99']}{unit}, max {p['max']}{unit} (n={p['count']})"
    m = report['machine']
    incidents = report['incidents']
    lines = [
        f"# Fleet simulator: {c['devices']} computers, {report['label']}", '',
        f"- Date: {report['date']}", f"- Machine: {m['system']} {m['release']} ({m['version']}), {m['processor']}, "
        f"{m['logical_cpus']} logical CPUs, {m['total_ram_gb']} GB RAM ({m.get('free_ram_gb_at_start')} GB free at start), Python {m['python']}",
        f"- Target: {c['target']}; duration {c['duration_s']} s (measured {c['measured_s']} s); {c['checks']} agentless checks; "
        f"drop-outs {c['churn_per_minute']} per computer per minute; {c['outages']} computers taken offline for good",
        f"- Enrollment of all computers (code, enroll, approve, claim): {report['enrollment_seconds']} s", '',
        '| Measure | Result |', '|---|---|',
        f"| Server CPU (% of one core, {s['method']}) | mean {s['cpu_percent_of_one_core'].get('mean')}, P95 {s['cpu_percent_of_one_core'].get('p95')}, max {s['cpu_percent_of_one_core'].get('max')} |",
        f"| Server memory (resident MB) | " + (', '.join(f'{k} {v}' for k, v in s['rss_mb'].items()) if s['rss_mb'] else 'not measured') + ' |',
        f"| Simulator CPU (% of one core) | {report['simulator_cpu_percent_of_one_core']} |",
        '| SQLite (MB) | ' + ', '.join(f"{k.replace('_', ' ')} {v}" for k, v in report['storage_mb'].items()) + ' |',
        f"| Live samples sent / reached the dashboard stream | {live['sent']} / {live['reached_dashboard']} (missing {live['missing']}, {live['missing_percent']} %) |",
        f"| Live update latency, computer to dashboard | {fmt(live['latency_ms'], ' ms')} |",
        f"| Live reconnects / refused | {live['reconnects']} / {live['refused']} |",
        f"| HTTPS syncs / errors | {sync['requests']} / {sync['errors'] or 'none'} (records not acknowledged at the end: {sync['unacknowledged_at_end']}) |",
        f"| Sync latency | {fmt(sync['latency_ms'], ' ms')} |",
        f"| Dashboard overview + fleet calls | {report['dashboard']['calls']} calls, {report['dashboard']['errors']} errors, {fmt(report['dashboard']['latency_ms'], ' ms')} |",
        f"| Random drop-outs | {report['drop_outs']} |",
        f"| Incident engine lag (offline for {c['offline_rule_minutes']} min, then opened) | "
        f"{incidents['opened']} of {incidents['outages']} opened; {fmt(incidents['lag_s'], ' s') if incidents['measured'] else 'run too short to measure'}; other incidents: {incidents['other_incidents']} |",
        f"| Agentless checks ok | {report['checks']['ok']} of {report['checks']['configured']}; {fmt(report['checks']['latency_ms'], ' ms')} |",
        f"| Refused requests in the server log | {report['server_log_refusals']} |", '',
        'Missing live samples are samples a computer sent that never appeared on the dashboard stream: lost when a '
        'drop-out cut the socket, refused by the per-computer rate limit, or merged by the stream (it sends the newest '
        'sample per computer every 0.2 s). Latency includes that 0.2 s batching. Incident lag is measured from the moment '
        'the computer had been offline for the rule\'s duration; the alert engine evaluates every 15 s.', '',
        'This is a single run on one machine, with the simulator and the server sharing it. It shows that this size works '
        'here; it is not a capacity limit and not a guarantee for other hardware.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--devices', type=int, default=50, help='simulated computers (50 by default; 200 and 500 need a bigger machine)')
    parser.add_argument('--duration', type=int, default=120, help='seconds to run after enrollment (default 120)')
    parser.add_argument('--checks', type=int, default=0, help='agentless TCP checks against listeners on 127.0.0.1')
    parser.add_argument('--churn', type=float, default=0.05, help='chance per computer per minute of a 5-30 s drop-out')
    parser.add_argument('--outages', type=int, default=None, help='computers taken offline for good (default: 1 in 25)')
    parser.add_argument('--output', default='fleet-report', help='directory for report.json and report.md')
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--attach-directory', help='an existing server data directory (needs --i-understand)')
    parser.add_argument('--attach-address', help='that server\'s https:// address')
    parser.add_argument('--attach-admin-port', type=int, help='that server\'s loopback admin port')
    parser.add_argument('--attach-pid', type=int, help='that server\'s process ID, to measure its CPU and memory')
    parser.add_argument('--i-understand', action='store_true',
                        help='allow --attach-*: enrolls simulated computers in that server (removed again at the end)')
    args = parser.parse_args(argv)
    if not 1 <= args.devices <= 2000 or args.duration < 10 or not 0 <= args.checks <= 500 or not 0 <= args.churn <= 1:
        parser.error('devices 1-2000, duration at least 10 s, checks 0-500, churn 0-1')
    attach = (args.attach_directory, args.attach_address, args.attach_admin_port)
    if any(attach) and not args.i_understand:
        parser.error('refusing to load a server this tool did not start; add --i-understand if that is really wanted')
    if any(attach) and not all(attach):
        parser.error('--attach-directory, --attach-address and --attach-admin-port go together')
    if any(attach) and args.checks:
        parser.error('agentless checks are only simulated against the test server')
    if args.outages is None:
        args.outages = max(1, args.devices // 25)
    if args.seed is not None:
        random.seed(args.seed)
    report = asyncio.run(Simulation(args).run())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    (output / 'report.md').write_text(markdown(report), encoding='utf-8')
    print(markdown(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
