"""Opt-in, local health evidence. Stdlib only; no messages leave this module.

Probes are bounded and fail closed. Parsers accept fixture strings/mappings and
never perform I/O. None means unavailable; [] means an accessible empty source.
"""
import datetime as dt
from functools import wraps
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET

TIMEOUT = 12
UTC = dt.timezone.utc


def safe(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            return None
    return call


def bounded(function):
    """Also bound local filesystem reads (a sysfs driver can stall)."""
    @wraps(function)
    def call(*args, **kwargs):
        result = queue.Queue(maxsize=1)
        def run():
            result.put(safe(function)(*args, **kwargs))
        threading.Thread(target=run, daemon=True).start()
        try:
            return result.get(timeout=TIMEOUT)
        except queue.Empty:
            return None
    return call


@safe
def command(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=TIMEOUT-1, creationflags=0x08000000 if sys.platform == 'win32' else 0,
                            env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'})
    if result.returncode or re.search(r'permission|not seeing messages', result.stderr, re.I):
        return None
    return result.stdout


def powershell(script):
    return command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();$ErrorActionPreference='Stop';" + script])


def stamp(value):
    parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def num(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number < 0xFFFFFFFF else None
    except (ValueError, TypeError):
        return None


def rows(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


@safe
def parse_windows_events(raw):
    root = ET.fromstring(raw)
    events = [root] if root.tag.rsplit('}', 1)[-1] == 'Event' else root.findall('.//{*}Event')
    result = []
    for event in events:
        system = event.find('{*}System')
        provider = system.find('{*}Provider').get('Name')
        identity = int(system.findtext('{*}EventID'))
        when = system.find('{*}TimeCreated').get('SystemTime')
        kind = {('Microsoft-Windows-Kernel-Power', 41): 'Unexpected shutdown',
                ('EventLog', 6008): 'Unexpected shutdown',
                ('Microsoft-Windows-WER-SystemErrorReporting', 1001): 'Crash'}.get((provider, identity))
        if kind:
            result.append(dict(date=stamp(when).isoformat(), kind=kind, provider=provider))
    # 41 and 6008 normally describe the same incident at the next startup.
    result.sort(key=lambda row: row['date'])
    unique = []
    for row in result:
        if any(row['kind'] == old['kind'] == 'Unexpected shutdown' and row['provider'] != old['provider']
               and abs((stamp(row['date'])-stamp(old['date'])).total_seconds()) < 120
               for old in unique[-3:]):
            continue
        unique.append(row)
    return [dict(date=row['date'], kind=row['kind']) for row in unique]


@safe
def parse_windows_sleep(raw):
    transitions, intervals = [], []
    root = ET.fromstring(raw)
    for event in root.findall('.//{*}Event'):
        system = event.find('{*}System')
        provider = system.find('{*}Provider').get('Name')
        identity = int(system.findtext('{*}EventID'))
        when = stamp(system.find('{*}TimeCreated').get('SystemTime')).timestamp()
        data = {node.get('Name'): node.text for node in event.findall('{*}EventData/{*}Data')}
        if provider == 'Microsoft-Windows-Power-Troubleshooter' and identity == 1 and data.get('SleepTime') and data.get('WakeTime'):
            intervals.append((stamp(data['SleepTime']).timestamp(), stamp(data['WakeTime']).timestamp()))
        elif provider == 'Microsoft-Windows-Kernel-Power':
            if identity == 42:
                transitions.append((when, 'sleep'))
            elif identity == 107 and data.get('TargetState') not in ('5', '6'):
                transitions.append((when, 'wake'))
        elif provider == 'Microsoft-Windows-Kernel-Boot' and identity == 27 and data.get('BootType') in ('1', '2'):
            transitions.append((when, 'wake'))
        elif provider == 'Microsoft-Windows-Kernel-General' and identity in (12, 13):
            transitions.append((when, 'reset'))
    return sleep_days(transitions, intervals)


def sleep_days(transitions, intervals=()):
    """Pair retained transitions; split elapsed sleep at local midnight, DST aware."""
    spans = list(intervals)
    asleep = None
    for when, kind in sorted(transitions):
        if kind == 'sleep':
            if asleep is None:
                asleep = when
        elif kind == 'reset':
            asleep = None
        elif kind == 'wake' and asleep is not None:
            if not any(a < when and b > asleep for a, b in intervals):
                spans.append((asleep, when))
            asleep = None
    merged = []
    for start, end in sorted(set(spans)):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    days, nights = {}, {}
    for start, end in merged:
        cursor = start
        while cursor < end:
            local = dt.datetime.fromtimestamp(cursor)
            night = local.date()-dt.timedelta(days=1 if local.hour < 12 else 0)
            noon = dt.datetime.combine(night+dt.timedelta(days=1), dt.time(12)).timestamp()
            stop = min(end, noon)
            key = night.isoformat()
            nights[key] = nights.get(key, 0)+stop-cursor
            cursor = stop
        day = dt.datetime.fromtimestamp(end).date().isoformat()
        days.setdefault(day, dict(cycles=0, seconds=0))['cycles'] += 1
        while start < end:
            local = dt.datetime.fromtimestamp(start)
            tomorrow = dt.datetime.combine(local.date()+dt.timedelta(days=1), dt.time()).timestamp()
            stop = min(end, tomorrow)
            key = local.date().isoformat()
            days.setdefault(key, dict(cycles=0, seconds=0))['seconds'] += stop-start
            start = stop
    return dict(daily=days, nights=[dict(date=day, seconds=seconds) for day, seconds in sorted(nights.items())])


@safe
def parse_journal_sleep(raw):
    from lifetime_import import parse_events
    return sleep_days(parse_events(raw, fallback=True))


@safe
def parse_journal_tail(raw, end, current=False):
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not records:
        return None
    clean = any(str(row.get('_PID')) == '1' and re.search(
        r'Shutting down|Reached target .*System (Reboot|Power Off)|Stopped Record System Boot/Shutdown in UTMP',
        str(row.get('MESSAGE', ''))) for row in records)
    events = []
    if not current and not clean:
        events.append(dict(date=dt.datetime.fromtimestamp(end, UTC).isoformat(), kind='Unexpected shutdown'))
    for row in records:
        if re.search(r'Kernel panic|Oops:|BUG: unable to handle', str(row.get('MESSAGE', ''))):
            when = int(row['__REALTIME_TIMESTAMP'])/1e6
            events.append(dict(date=dt.datetime.fromtimestamp(when, UTC).isoformat(), kind='Crash'))
    return events


def battery(full, design, cycles=None, chemistry=None, unit='Wh'):
    return dict(full=full, design=design, cycles=cycles, chemistry=chemistry, unit=unit,
                health=100*full/design if full is not None and design else None)


@safe
def parse_sysfs(files):
    """A mapping per pack, without mixing energy and charge units."""
    packs = []
    for item in files:
        prefix, divisor, unit = ('energy', 1e6, 'Wh') if num(item.get('energy_full')) is not None else ('charge', 1e6, 'Ah')
        full, design = num(item.get(prefix+'_full')), num(item.get(prefix+'_full_design'))
        packs.append(battery(full/divisor if full is not None else None,
                             design/divisor if design is not None else None,
                             num(item.get('cycle_count')), item.get('technology'), unit))
    return packs


@safe
def parse_wmi_battery(raw):
    data = json.loads(raw)
    packs = []
    for static in rows(data.get('design')):
        name = static.get('InstanceName')
        full = next((r for r in rows(data.get('full')) if r.get('InstanceName') == name), {})
        cycle = next((r for r in rows(data.get('cycles')) if r.get('InstanceName') == name), {})
        f, d = num(full.get('FullChargedCapacity')), num(static.get('DesignedCapacity'))
        packs.append(battery(f/1000 if f is not None else None, d/1000 if d is not None else None,
                             num(cycle.get('CycleCount')), None))
    return packs


def xml_root(raw):
    root = ET.fromstring(raw)
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    return root


def xml_number(node, field):
    if field in node.attrib:
        return num(node.get(field))
    child = node.find(field)
    return num(child.get('Value', child.text)) if child is not None else None


@safe
def parse_battery_report(raw):
    root = xml_root(raw)
    packs = []
    for node in root.findall('./Batteries/Battery'):
        full, design = xml_number(node, 'FullChargeCapacity'), xml_number(node, 'DesignCapacity')
        packs.append(battery(full/1000 if full is not None else None, design/1000 if design is not None else None,
                             xml_number(node, 'CycleCount'), node.findtext('Chemistry')))
    history = []
    for node in root.findall('./History/*'):
        date = node.get('StartDate') or node.findtext('StartDate') or node.findtext('StartTime')
        full = xml_number(node, 'FullChargeCapacity')
        design = xml_number(node, 'DesignCapacity')
        if date and full is not None:
            history.append(dict(date=stamp(date).date().isoformat(), full=full/1000,
                                health=100*full/design if design else None))
    daily = {}
    entries = []
    for node in root.findall('./RecentUsage/*'):
        date = node.get('Timestamp') or node.findtext('Timestamp') or node.findtext('StartTime')
        source = node.get('Ac') or node.findtext('Ac') or node.findtext('PowerSource') or node.get('PowerSource')
        state = node.get('EntryType') or node.findtext('State') or node.get('State')
        if date:
            entries.append((stamp(date).timestamp(), source, state))
    # RecentUsage records transitions. Only bounded Active intervals count;
    # the final observation has no known end and is deliberately omitted.
    for (start, source, state), (end, _, _) in zip(entries, entries[1:]):
        if state not in ('Active', '1') or source not in ('true', 'false', '1', '0', 'AC', 'Battery') or end <= start:
            continue
        field = 'ac' if source in ('true', '1', 'AC') else 'battery'
        while start < end:
            local = dt.datetime.fromtimestamp(start)
            stop = min(end, dt.datetime.combine(local.date()+dt.timedelta(days=1), dt.time()).timestamp())
            daily.setdefault(local.date().isoformat(), dict(ac=0, battery=0))[field] += stop-start
            start = stop
    return dict(packs=packs, history=history, usage=daily)


@safe
def parse_machine(raw):
    data = json.loads(raw) if isinstance(raw, str) else raw
    def date(value):
        if not value:
            return None
        for pattern in ('%Y%m%d', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return dt.datetime.strptime(str(value)[:10] if pattern != '%Y%m%d' else str(value)[:8], pattern).date().isoformat()
            except ValueError:
                pass
        return None
    return dict(model=data.get('model') or None, bios=date(data.get('bios')),
                installed=date(data.get('installed')), estimated=bool(data.get('estimated')))


def event_xml(filters):
    script = "$all=@();"
    for provider, identities in filters:
        script += ("try {$all+=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='" + provider +
                   "';Id=@(" + ','.join(map(str, identities)) + ");StartTime=(Get-Date).AddDays(-366)} -MaxEvents 10000 -ErrorAction Stop)} "
                   "catch {if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {throw}};")
    return powershell(script + "'<Events>'; $all|ForEach-Object {$_.ToXml()}; '</Events>'")


@bounded
def probe_stability():
    if sys.platform == 'win32':
        raw = event_xml([('Microsoft-Windows-Kernel-Power', [41]), ('EventLog', [6008]),
                         ('Microsoft-Windows-WER-SystemErrorReporting', [1001])])
        return parse_windows_events(raw) if raw is not None else None
    from lifetime_import import parse_boots
    raw = command(['journalctl', '--list-boots', '-o', 'json', '--no-pager', '--utc'])
    if raw is None:
        raw = command(['journalctl', '--list-boots', '--no-pager', '--utc'])
    boots = parse_boots(raw)
    result = []
    cutoff = (dt.datetime.now(UTC)-dt.timedelta(days=366)).timestamp()
    for boot in boots[-400:]:
        if boot['end'] < cutoff:
            continue
        tail = command(['journalctl', '-b', boot['id'], '-n', '100', '-o', 'json', '--no-pager'])
        parsed = parse_journal_tail(tail, boot['end'], current=boot['index'] == 0)
        if parsed is None:
            return None
        result.extend(parsed)
        panics = command(['journalctl', '-k', '-b', boot['id'], '-g', 'Kernel panic|Oops:|BUG: unable to handle',
                          '-n', '100', '-o', 'json', '--no-pager'])
        if panics:
            result.extend(parse_journal_tail(panics, boot['end'], current=True) or [])
    return [dict(date=date, kind=kind) for date, kind in sorted({(r['date'], r['kind']) for r in result})]


@bounded
def probe_battery():
    if sys.platform != 'win32':
        if not Path('/sys/class/power_supply').is_dir():
            return None
        packs = []
        for path in Path('/sys/class/power_supply').glob('BAT*'):
            values = {}
            for name in ('energy_full', 'energy_full_design', 'charge_full', 'charge_full_design', 'cycle_count', 'technology'):
                try:
                    values[name] = (path/name).read_text().strip()
                except OSError:
                    pass
            packs.append(values)
        return dict(packs=parse_sysfs(packs), history=[], usage=None)
    wmi = powershell(r"@{design=@(Get-CimInstance -Namespace root\wmi BatteryStaticData -ErrorAction SilentlyContinue | Select-Object InstanceName,DesignedCapacity);full=@(Get-CimInstance -Namespace root\wmi BatteryFullChargedCapacity -ErrorAction SilentlyContinue | Select-Object InstanceName,FullChargedCapacity);cycles=@(Get-CimInstance -Namespace root\wmi BatteryCycleCount -ErrorAction SilentlyContinue | Select-Object InstanceName,CycleCount)}|ConvertTo-Json -Depth 4 -Compress")
    packs = parse_wmi_battery(wmi) if wmi is not None else None
    with tempfile.TemporaryDirectory(prefix='lifetime-battery-') as folder:
        path = Path(folder)/'report.xml'
        command(['powercfg', '/batteryreport', '/xml', '/output', str(path)])
        report = parse_battery_report(path.read_text(encoding='utf-8-sig')) if path.exists() else None
    if report is None and packs is None:
        return None
    result = report or dict(packs=packs, history=[], usage=None)
    if packs:
        for index, pack in enumerate(packs):
            if index < len(result['packs']) and pack.get('cycles') is not None:
                result['packs'][index]['cycles'] = pack['cycles']
        if not result['packs']:
            result['packs'] = packs
    return result


@bounded
def probe_sleep():
    if sys.platform == 'win32':
        raw = event_xml([('Microsoft-Windows-Kernel-Power', [42, 107]),
                         ('Microsoft-Windows-Kernel-Boot', [27]), ('Microsoft-Windows-Power-Troubleshooter', [1]),
                         ('Microsoft-Windows-Kernel-General', [12, 13])])
        return parse_windows_sleep(raw) if raw is not None else None
    raw = command(['journalctl', '--since', '366 days ago', '_TRANSPORT=kernel', '-g', 'PM: (suspend|hibernation) (entry|exit)',
                   '-n', '20000', '-o', 'json', '--no-pager'])
    if not raw:
        raw = command(['journalctl', '--since', '366 days ago', '-u', 'systemd-logind', '-n', '20000', '-o', 'json', '--no-pager'])
    return parse_journal_sleep(raw) if raw is not None else None


@bounded
def probe_machine():
    if sys.platform == 'win32':
        raw = powershell("$b=Get-CimInstance Win32_BIOS;$o=Get-CimInstance Win32_OperatingSystem;$c=Get-CimInstance Win32_ComputerSystem;"
                         "@{model=$c.Model;bios=$b.ReleaseDate.ToString('yyyy-MM-dd');installed=$o.InstallDate.ToString('yyyy-MM-dd')}|ConvertTo-Json -Compress")
        return parse_machine(raw) if raw is not None else None
    from lifetime_tracker import estimate_first_seen
    data = {}
    for key, name in (('model', 'product_name'), ('bios', 'bios_date')):
        try:
            data[key] = (Path('/sys/class/dmi/id')/name).read_text().strip()
        except OSError:
            pass
    date, source = estimate_first_seen(with_source=True)
    data.update(installed=date if source == 'system-file-date' else None, estimated=True)
    return parse_machine(data)


def collect(settings, details=True):
    if not (settings.get('completed') is True and settings.get('health') is True):
        return None
    return dict(stability=probe_stability(), battery=probe_battery(), sleep=probe_sleep() if details else None,
                machine=probe_machine() if details else None,
                observed_at=dt.datetime.now(UTC).isoformat())


def capacity_sample(ledger, data, today=None):
    """Called only by the normal ledger writer. Retain at most 400 civil days."""
    today = today or dt.date.today()
    cutoff = (today-dt.timedelta(days=399)).isoformat()
    history = {k: v for k, v in ledger.get('health_capacity', {}).items() if cutoff <= k <= today.isoformat()}
    packs = (data or {}).get('packs') or []
    if packs:
        history[today.isoformat()] = packs
    ledger['health_capacity'] = dict(sorted(history.items())[-400:])


def detail(data, now=None):
    """Bounded series for the server, in the shapes the Health page already renders."""
    now = now or dt.datetime.now(UTC)
    cutoff = (now-dt.timedelta(days=399)).date().isoformat()
    result = {}
    events = data.get('stability')
    if events is not None:
        result['stability'] = [dict(date=r['date'], kind=r['kind']) for r in events
                               if stamp(r['date']).date().isoformat() >= cutoff][-400:]
    battery_data = data.get('battery')
    if battery_data is not None:
        packs = [{k: p.get(k) for k in ('full', 'design', 'cycles', 'chemistry', 'unit', 'health') if p.get(k) is not None}
                 for p in battery_data.get('packs', [])[:4]]
        for pack in packs:
            if pack.get('cycles') is not None:
                pack['cycles'] = int(pack['cycles'])
        result['battery'] = dict(packs=packs)
        history = [dict(date=r['date'], full=r['full'], **({} if r.get('health') is None else dict(health=r['health'])))
                   for r in battery_data.get('history', []) if r['date'] >= cutoff][-400:]
        if history:
            result['battery']['history'] = history
        usage = battery_data.get('usage')
        if usage:
            result['battery']['usage'] = {d: dict(ac=min(v['ac'], 86400), battery=min(v['battery'], 86400))
                                          for d, v in sorted(usage.items()) if d >= cutoff}
            result['battery']['usage'] = dict(list(result['battery']['usage'].items())[-400:])
    sleep = data.get('sleep')
    if sleep is not None:
        result['sleep'] = dict(daily={d: dict(cycles=v['cycles'], seconds=min(v['seconds'], 86400))
                                      for d, v in sorted(sleep['daily'].items()) if d >= cutoff},
                               nights=[dict(date=n['date'], seconds=min(n['seconds'], 86400))
                                       for n in sleep['nights'] if n['date'] >= cutoff])
        result['sleep']['daily'] = dict(list(result['sleep']['daily'].items())[-400:])
        result['sleep']['nights'] = result['sleep']['nights'][-400:]
    return result


def summary(data, now=None):
    if data is None:
        return None
    now = now or dt.datetime.now(UTC)
    events = data.get('stability')
    recent = [r for r in (events or []) if now-dt.timedelta(days=30) <= stamp(r['date']) <= now]
    crashes = [r['date'] for r in recent if r['kind'] == 'Crash']
    packs = (data.get('battery') or {}).get('packs') or []
    pack = packs[0] if len(packs) == 1 else {}
    value = dict(version=1, observed_at=data['observed_at'],
                shutdowns_30d=sum(r['kind'] == 'Unexpected shutdown' for r in recent) if events is not None else None,
                crashes_30d=len(crashes) if events is not None else None,
                battery_health=pack.get('health'), cycle_count=int(pack['cycles']) if pack.get('cycles') is not None else None,
                last_crash=max(crashes, default=None))
    # Implausible firmware values stay visible locally but cannot break sync.
    for field, limit in (('battery_health', 1000), ('cycle_count', 100000), ('crashes_30d', 10000), ('shutdowns_30d', 10000)):
        if value[field] is not None and value[field] > limit:
            value[field] = None
    if value['crashes_30d'] is None:
        value['last_crash'] = None
    return value
