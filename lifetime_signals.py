"""Local system telemetry. Parsers are pure; probes return partial data on failure.

No probe starts a shell window or makes a network request. Subprocesses have a
five-second deadline; the GUI also expires outstanding worker results at 5 s.
"""
import csv
import ctypes as C
import io
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time


def number(value):
    try:
        result = float(str(value).strip().rstrip('%'))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def objects(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def parse_json(text):
    try:
        return json.loads(text.lstrip('\ufeff'))
    except (ValueError, TypeError, AttributeError):
        return None


def parse_battery(text):
    """CIM units are mW, mWh and mV, not watts, watt-hours or volts."""
    data = parse_json(text) if isinstance(text, str) else text
    data = data if isinstance(data, dict) else {}
    statuses = objects(data.get('status'))
    fulls = objects(data.get('full'))
    designs = objects(data.get('design'))
    result = dict(watts=None, capacity_wh=None, full_wh=None, design_wh=None,
                  voltage=None, wear=None)
    # Avoid adding rates/voltages from unrelated packs. Match on InstanceName.
    if not statuses:
        return result
    if len(statuses) > 1:
        packs = [parse_battery(dict(status=[row], full=fulls, design=designs)) for row in statuses]
        for key in ('watts', 'capacity_wh', 'full_wh', 'design_wh'):
            values = [p[key] for p in packs]
            result[key] = sum(values) if all(v is not None for v in values) else None
        if result['full_wh'] and result['design_wh']:
            result['wear'] = max(0, 100*(1-result['full_wh']/result['design_wh']))
        result['packs'] = packs
        return result
    status = statuses[0]
    identity = status.get('InstanceName')
    def capacity(rows, key):
        row = next((r for r in rows if r.get('InstanceName') == identity), {})
        value = number(row.get(key))
        return value / 1000 if value is not None and value > 0 else None
    for source, target in [('RemainingCapacity', 'capacity_wh'), ('Voltage', 'voltage')]:
        value = number(status.get(source))
        result[target] = value / 1000 if value is not None else None
    result['full_wh'] = capacity(fulls, 'FullChargedCapacity')
    result['design_wh'] = capacity(designs, 'DesignedCapacity')
    rate = number(status.get('ChargeRate' if status.get('Charging') else 'DischargeRate'))
    if rate is not None and rate < 0xFFFFFFFF:
        result['watts'] = rate / 1000
    if result['design_wh'] and result['full_wh']:
        result['wear'] = max(0, 100 * (1 - result['full_wh'] / result['design_wh']))
    return result


def parse_nvidia(text):
    result = []
    try:
        for row in csv.reader(io.StringIO(text or '')):
            if len(row) != 6:
                continue
            result.append(dict(zip(('name', 'watts', 'temperature', 'utilization',
                                    'memory_used', 'memory_total'),
                                   [row[0].strip()] + [number(v) for v in row[1:]])))
    except (ValueError, TypeError, csv.Error):
        pass
    return result


def parse_netsh(text):
    """Legacy fixture parser only; live Windows queries use the WLAN API."""
    result = []
    current = {}
    fields = {'name': 'name', 'state': 'state', 'ssid': 'ssid', 'signal': 'signal',
              'band': 'band', 'channel': 'channel', 'receive rate (mbps)': 'rx_mbps',
              'transmit rate (mbps)': 'tx_mbps'}
    numeric = {'signal', 'channel', 'rx_mbps', 'tx_mbps'}
    for line in (text or '').splitlines():
        if ':' not in line:
            continue
        key, value = (s.strip() for s in line.split(':', 1))
        key = fields.get(key.lower())
        if key is None:
            continue
        if key == 'name' and current:
            result.append(current)
            current = {}
        current[key] = number(value) if key in numeric else value or None
    if current:
        result.append(current)
    for row in result:
        if str(row.get('state', '')).lower() != 'connected':
            for key in ('ssid', 'signal', 'band', 'channel', 'rx_mbps', 'tx_mbps'):
                row[key] = None
    return result


def parse_adapters(text):
    return [row for row in objects(parse_json(text)) if
            isinstance(row, dict) and str(row.get('Status', '')).lower() == 'up' and
            not re.search(r'vmware|hyper-v|loopback|vethernet',
                          str(row.get('Name', '')) + ' ' + str(row.get('InterfaceDescription', '')), re.I)]


def counter_rates(previous, current, elapsed):
    """Counters keyed by stable interface index; ignore resets and new interfaces."""
    if not previous or elapsed <= 0:
        return dict(down=None, up=None)
    pairs = [(previous[k], v) for k, v in current.items() if k in previous]
    pairs = [(a, b) for a, b in pairs if b[0] >= a[0] and b[1] >= a[1]]
    return dict(down=sum(b[0]-a[0] for a, b in pairs)/elapsed if pairs else None,
                up=sum(b[1]-a[1] for a, b in pairs)/elapsed if pairs else None)


def parse_event_awake(boot, now, events):
    """Subtract each Kernel-Power 42 -> next Kernel-Boot 27 interval.

    Ignore 107 entirely. An unmatched suspend makes the evidence incomplete.
    Timestamps are UNIX seconds, supplied in UTC by the event-log probe.
    """
    if number(boot) is None or number(now) is None or now < boot:
        return None
    pending = None
    off = 0
    for event in sorted(events, key=lambda e: e.get('time', 0)):
        stamp = number(event.get('time'))
        if stamp is None or not boot <= stamp <= now:
            continue
        if event.get('provider') == 'Microsoft-Windows-Kernel-Power' and event.get('id') == 42:
            if pending is None:
                pending = stamp
        elif event.get('provider') == 'Microsoft-Windows-Kernel-Boot' and event.get('id') == 27 and pending is not None:
            off += stamp - pending
            pending = None
    return None if pending is not None else max(0, now - boot - off)


def run_command(args, timeout=5):
    """Run a program and return (exit_code, stdout), or None if it is missing, times out or cannot start."""
    try:
        run = subprocess.run(args, capture_output=True, timeout=timeout,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                             encoding='utf-8', errors='replace')
        return run.returncode, run.stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def command(args, timeout=5, ok_codes=(0,)):
    """Standard output of a program, or None unless it ran and exited with one of ok_codes."""
    result = run_command(args, timeout)
    return result[1] if result is not None and result[0] in ok_codes else None


def powershell(script):
    if sys.platform != 'win32':
        return None
    return command(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                    '-Command', "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); " + script])


def safe(probe):
    def run(*args, **kwargs):
        try:
            return probe(*args, **kwargs)
        except Exception:
            return None
    run.__name__ = getattr(probe, '__name__', 'probe')
    return run


@safe
def probe_inventory():
    """Infrequent optional hardware reads. Only selected, non-identifying fields.

    Each Windows query has the existing five-second subprocess deadline. A failed
    domain is omitted without losing the others. Call only from a worker.
    """
    from lifetime_system import clean
    if sys.platform != 'win32':
        return clean(linux_inventory())
    date = "function iso($d) {if($d){([datetime]$d).ToString('yyyy-MM-ddTHH:mm:sszzz')}}; "
    queries = {
        # ACPI thermal zones do not identify CPU cores. Never relabel one as CPU temperature.
        'cpu': "$p=Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1; "
            "@{current_mhz=$p.CurrentClockSpeed;max_mhz=$p.MaxClockSpeed}",
        'fans': "@(Get-CimInstance Win32_Fan -ErrorAction Stop | Select-Object -First 8 | ForEach-Object {"
            "@{name=$_.Name}})",  # DesiredSpeed is a target, not a measured RPM.
        'adapters': "@(Get-CimInstance Win32_NetworkAdapter -ErrorAction Stop | Where-Object NetConnectionID | Select-Object -First 16 | ForEach-Object {"
            "@{name=$_.NetConnectionID;model=$_.Name;kind=$(if($_.Name -match 'Bluetooth'){'Bluetooth'}elseif($_.Name -match 'VPN|Virtual|TAP|TUN'){'Virtual / VPN'}elseif($_.Name -match 'Wireless|Wi-Fi|802.11'){'Wi-Fi'}else{'Ethernet'});"
            "connected=($_.NetConnectionStatus -eq 2);speed_mbps=$(if($_.NetConnectionStatus -eq 2 -and $_.Speed -gt 0){[double]$_.Speed/1000000});vendor=$_.Manufacturer;mac=$_.MACAddress}})",
        'memory': "$m=@(Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop);$a=Get-CimInstance Win32_PhysicalMemoryArray;"
            "@{slots_used=$m.Count;slots_total=($a|Measure-Object MemoryDevices -Sum).Sum;modules=@($m|Select-Object -First 8 | ForEach-Object {"
            "@{kind=$(switch($_.SMBIOSMemoryType){18{'DDR'}19{'DDR2'}20{'DDR2 FB-DIMM'}24{'DDR3'}26{'DDR4'}27{'LPDDR'}28{'LPDDR2'}29{'LPDDR3'}30{'LPDDR4'}34{'DDR5'}35{'LPDDR5'}default{$null}});"
            "speed_mhz=$(if($_.ConfiguredClockSpeed -gt 0){$_.ConfiguredClockSpeed}elseif($_.Speed -gt 0){$_.Speed});capacity_bytes=$_.Capacity}})}",
        'disks': "@(Get-PhysicalDisk -ErrorAction Stop | Select-Object -First 8 | ForEach-Object {"
            "@{model=$_.FriendlyName;kind=$(if($_.BusType -eq 17){'NVMe'}elseif($_.MediaType -eq 4){'SSD'}elseif($_.MediaType -eq 3){'HDD'}else{$null});size_bytes=$_.Size;health=[string]$_.HealthStatus}})",
        'board': "$b=Get-CimInstance Win32_BaseBoard -ErrorAction Stop | Select-Object -First 1;$v=Get-CimInstance Win32_BIOS | Select-Object -First 1;"
            "@{manufacturer=$b.Manufacturer;model=$b.Product;bios_vendor=$v.Manufacturer;bios_version=$v.SMBIOSBIOSVersion;bios_date=(iso $v.ReleaseDate)}",
        'gpus': "@(Get-CimInstance Win32_VideoController -ErrorAction Stop | Select-Object -First 8 | ForEach-Object {"
            "@{name=$_.Name;driver=$_.DriverVersion;vram_bytes=$_.AdapterRAM}})",
        'os': "$o=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop;"
            "@{name=$o.Caption;build=$o.BuildNumber;architecture=$o.OSArchitecture;installed=(iso $o.InstallDate);"
            "last_boot=(iso $o.LastBootUpTime);uptime_seconds=([datetime]::Now-$o.LastBootUpTime).TotalSeconds}",
    }
    result = {'unavailable': {'cpu_temperature': 'Windows did not expose an identified CPU temperature sensor'}}
    for key, query in queries.items():
        expression = '@(& {'+query+'})' if key in ('fans', 'adapters', 'disks', 'gpus') else '$(& {'+query+'})'
        output = powershell("$ErrorActionPreference='Stop'; "+date+"try {ConvertTo-Json -Depth 5 -Compress -InputObject "+expression+
            "} catch {if($_.Exception.Message -match 'denied|0x80041003'){ '{\"_unavailable\":\"Access denied by Windows\"}' }"
            "else { '{\"_unavailable\":\"The operating system did not expose this reading\"}' }}")
        value = parse_json(output)
        if isinstance(value, dict) and '_unavailable' in value:
            result['unavailable'][key] = value['_unavailable']
        elif value is not None:
            result[key] = value
    if 'adapters' not in result:
        native = probe_counters(inventory=True)
        if native is not None:
            result['adapters'] = native
    clocks = safe(windows_cpu_clocks)()
    if clocks:
        cpu = result.setdefault('cpu', {})
        for key, value in clocks.items():
            if cpu.get(key) is None:
                cpu[key] = value
    if 'os' not in result:
        import platform, datetime
        kernel = C.WinDLL('kernel32')
        kernel.GetTickCount64.restype = C.c_ulonglong
        uptime = kernel.GetTickCount64()/1000
        result['os'] = dict(name=platform.system(), build=platform.version(), architecture=platform.machine(),
            uptime_seconds=uptime, last_boot=(datetime.datetime.now().astimezone()-datetime.timedelta(seconds=uptime)).isoformat())
    # Wi-Fi standard comes from the existing WLAN API, without sharing the SSID.
    wifi = safe(windows_wifi)(include_ssid=False) or []
    for adapter in result.get('adapters', []):
        w = next((w for w in wifi if w.get('name') in (adapter.get('name'), adapter.get('model'))), {})
        for key in ('wifi_standard', 'band'):
            if w.get(key):
                adapter[key] = w[key]
    # The cheap network driver view includes disconnected adapters.
    drivers = parse_json(powershell("@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Select-Object -First 64 Name,DriverVersion)|ConvertTo-Json -Compress"))
    for adapter in result.get('adapters', []):
        driver = next((d for d in objects(drivers) if d.get('Name') == adapter.get('name')), {})
        if driver.get('DriverVersion'):
            adapter['driver'] = driver['DriverVersion']
    if result.get('adapters'):
        result['unavailable'].pop('adapters', None)
        if not drivers:
            result['unavailable']['adapter_driver'] = 'Windows did not expose network driver information'
    # Win32_VideoController.AdapterRAM is a 32-bit value. Prefer the existing
    # nvidia-smi reading when present; never present a wrapped capacity as real.
    for gpu in result.get('gpus', []):
        gpu['vram_bytes'] = None
    memory = parse_json(powershell("@(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*' -ErrorAction SilentlyContinue | "
        "Select-Object DriverDesc,@{n='bytes';e={$_.'HardwareInformation.qwMemorySize'}})|ConvertTo-Json -Compress"))
    for gpu in result.get('gpus', []):
        known = next((m for m in objects(memory) if m.get('DriverDesc') == gpu.get('name')), {})
        if number(known.get('bytes')) is not None:
            gpu['vram_bytes'] = int(known['bytes'])
    for measured in safe(probe_gpu)() or []:
        for gpu in result.get('gpus', []):
            if gpu.get('name') == measured.get('name') and measured.get('memory_total') is not None:
                gpu['vram_bytes'] = int(measured['memory_total']*2**20)
    displays = safe(windows_displays)()
    if displays is not None:
        result['displays'] = displays
    return clean(result)


def windows_cpu_clocks():
    """Processor power information is readable without WMI permissions."""
    import os
    class Processor(C.Structure):
        _fields_ = [(key, C.c_uint32) for key in ('number', 'maximum', 'current', 'limit', 'max_idle', 'idle')]
    processors = (Processor*min(256, os.cpu_count() or 1))()
    api = C.WinDLL('powrprof')
    if api.CallNtPowerInformation(11, None, 0, C.byref(processors), C.sizeof(processors)) != 0:
        return None
    current = [p.current for p in processors if p.current > 0]
    maximum = [p.maximum for p in processors if p.maximum > 0]
    return dict(current_mhz=max(current) if current else None, max_mhz=max(maximum) if maximum else None)


def windows_displays():
    """Active desktop outputs, not GPU count; standard Windows user32 API."""
    import struct
    api = C.WinDLL('user32', use_last_error=True)
    api.EnumDisplayDevicesW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_void_p, C.c_uint32]
    api.EnumDisplaySettingsW.argtypes = [C.c_wchar_p, C.c_uint32, C.c_void_p]
    result = []
    for index in range(32):
        device = C.create_string_buffer(840)
        struct.pack_into('<I', device, 0, 840)
        if not api.EnumDisplayDevicesW(None, index, device, 0):
            break
        flags = struct.unpack_from('<I', device, 324)[0]
        if not flags & 1 or flags & 8:
            continue
        name = C.wstring_at(C.addressof(device)+4, 32).split('\0')[0]
        mode = C.create_string_buffer(220)
        struct.pack_into('<H', mode, 68, 220)
        if api.EnumDisplaySettingsW(name, 0xFFFFFFFF, mode):
            width, height, _, hz = struct.unpack_from('<IIII', mode, 172)
            result.append(dict(width=width, height=height, hz=hz if hz > 1 else None))
    return result[:8]


def linux_inventory():
    """Read standard sysfs/hwmon interfaces; no extra drivers or sensor libraries."""
    import platform
    result, cpu, fans = {}, {}, []
    temperatures = []
    for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
        kind = read_file(zone/'type').lower()
        if any(k in kind for k in ('cpu', 'x86_pkg', 'coretemp', 'k10temp')):
            value = number(read_file(zone/'temp'))
            if value is not None:
                temperatures.append(value/1000)
    for hwmon in Path('/sys/class/hwmon').glob('hwmon*'):
        name = read_file(hwmon/'name')
        if name in ('coretemp', 'k10temp'):
            for path in hwmon.glob('temp*_input'):
                value = number(read_file(path))
                if value is not None:
                    temperatures.append(value/1000)
        for path in hwmon.glob('fan*_input'):
            rpm = number(read_file(path))
            if rpm is not None:
                fans.append(dict(name=read_file(path.with_name(path.name.replace('_input', '_label'))) or name, rpm=rpm))
    if temperatures:
        cpu['temperature_c'] = max(temperatures)
    for key, name in [('current_mhz', 'scaling_cur_freq'), ('max_mhz', 'cpuinfo_max_freq')]:
        value = number(read_file(Path('/sys/devices/system/cpu/cpu0/cpufreq')/name))
        if value is not None:
            cpu[key] = value/1000
    result.update(cpu=cpu, fans=fans)
    result['board'] = {key: read_file('/sys/class/dmi/id/'+name) or None for key, name in
                       [('manufacturer', 'board_vendor'), ('model', 'board_name'), ('bios_vendor', 'bios_vendor'),
                        ('bios_version', 'bios_version'), ('bios_date', 'bios_date')]}
    adapters = []
    for path in sorted(Path('/sys/class/net').glob('*')):
        if path.name == 'lo':
            continue
        driver = path/'device/driver'
        adapters.append(dict(name=path.name, model=read_file(path/'device/uevent').split('MODALIAS=')[-1].splitlines()[0] if 'MODALIAS=' in read_file(path/'device/uevent') else None,
            kind='Wi-Fi' if (path/'wireless').exists() else 'Bluetooth' if path.name.startswith('bnep') else 'Virtual / VPN' if not (path/'device').exists() else 'Ethernet',
            connected=read_file(path/'operstate') == 'up', speed_mbps=number(read_file(path/'speed')),
            mac=read_file(path/'address'), driver=driver.resolve().name if driver.exists() else None,
            vendor=read_file(path/'device/vendor') or None))
    result['adapters'] = adapters
    result['disks'] = [dict(model=read_file(p/'device/model') or p.name,
        kind='NVMe' if p.name.startswith('nvme') else 'HDD' if read_file(p/'queue/rotational') == '1' else 'SSD' if read_file(p/'queue/rotational') == '0' else None,
        size_bytes=int(number(read_file(p/'size')))*512 if number(read_file(p/'size')) is not None else None) for p in Path('/sys/block').glob('*') if (p/'device').exists()]
    displays = []
    for path in Path('/sys/class/drm').glob('card*-*'):
        if read_file(path/'status') == 'connected':
            # modes lists supported resolutions, not the active resolution.
            mode = read_file(path/'mode').splitlines()
            if mode and re.fullmatch(r'\d+x\d+', mode[0]):
                width, height = map(int, mode[0].split('x'))
                displays.append(dict(width=width, height=height))
    result['displays'] = displays
    result['os'] = dict(name=platform.system(), build=platform.release(), architecture=platform.machine(),
                        uptime_seconds=number(read_file('/proc/uptime').split(' ')[0]))
    return result


def read_file(path):
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return ''


def parse_proc_stat(text):
    fields = [int(v) for v in text.splitlines()[0].split()[1:]]
    return fields[3] + fields[4], sum(fields[:8])


def parse_meminfo(text):
    values = {k: int(v.split()[0])*1024 for k, v in
              (line.split(':', 1) for line in text.splitlines())}
    total = values['MemTotal']
    available = values.get('MemAvailable', sum(values.get(k, 0) for k in ('MemFree', 'Buffers', 'Cached')))
    return dict(total=total, used=total-available, percent=100*(total-available)/total)


def parse_net_dev(text):
    result = {}
    for line in text.splitlines():
        if ':' in line:
            name, raw = line.rsplit(':', 1)
            fields = raw.split()
            if len(fields) >= 16 and name.strip() != 'lo':
                result[name.strip()] = (int(fields[0]), int(fields[8]))
    return result


def linux_packs(root='/sys/class/power_supply'):
    packs = []
    for base in sorted(Path(root).glob('*')):
        if read_file(base/'type') != 'Battery' or read_file(base/'present') == '0':
            continue
        def value(name, divisor=1):
            raw = read_file(base/name)
            n = number(raw.lstrip('-') if name in ('current_now', 'power_now') else raw)
            return n/divisor if n is not None else None
        voltage = value('voltage_now', 1e6)
        watts = value('power_now', 1e6)
        current = value('current_now', 1e6)
        if watts is None and current is not None and voltage is not None:
            watts = current*voltage
        def energy(energy_name, charge_name):
            result = value(energy_name, 1e6)
            # charge-only devices need nominal voltage for capacities.
            nominal = value('voltage_min_design', 1e6) or voltage
            charge = value(charge_name, 1e6)
            return result if result is not None else charge*nominal if charge is not None and nominal else None
        full = energy('energy_full', 'charge_full')
        design = energy('energy_full_design', 'charge_full_design')
        packs.append(dict(name=base.name, percent=value('capacity'), state=read_file(base/'status') or None,
                          watts=watts, voltage=voltage, capacity_wh=energy('energy_now', 'charge_now'),
                          full_wh=full, design_wh=design,
                          wear=max(0, 100*(1-full/design)) if full and design else None))
    return packs


def linux_battery(root='/sys/class/power_supply'):
    packs = linux_packs(root)
    result = {key: None for key in ('watts', 'capacity_wh', 'full_wh', 'design_wh', 'voltage', 'wear')}
    for key in ('watts', 'capacity_wh', 'full_wh', 'design_wh'):
        values = [p[key] for p in packs]
        result[key] = sum(values) if values and all(v is not None for v in values) else None
    if len(packs) == 1:
        result['voltage'] = packs[0]['voltage']
    if result['full_wh'] and result['design_wh']:
        result['wear'] = max(0, 100*(1-result['full_wh']/result['design_wh']))
    result['packs'] = packs
    return result


def linux_power(root='/sys/class/power_supply'):
    packs = linux_packs(root)
    ac_values = [read_file(p/'online') for p in Path(root).glob('*') if read_file(p/'type') != 'Battery']
    ac = any(v == '1' for v in ac_values) if any(v in ('0', '1') for v in ac_values) else None
    if not packs:
        return dict(percent=None, state='No battery', remaining=None, ac=ac)
    fulls = [p['full_wh'] for p in packs]
    energies = [p['capacity_wh'] for p in packs]
    percents = [p['percent'] for p in packs if p['percent'] is not None]
    percent = (100*sum(energies)/sum(fulls) if all(v is not None for v in energies+fulls) and sum(fulls)
               else sum(percents)/len(percents) if percents else None)
    states = list(dict.fromkeys(p['state'] for p in packs if p['state']))
    return dict(percent=percent, state=', '.join(states) or None, remaining=None, ac=ac)


def linux_network(root='/sys/class/net', bitrate=None):
    """Active adapters. bitrate(name) supplies the Wi-Fi link rate in Mbps, which sysfs does not expose."""
    result = []
    for base in sorted(Path(root).glob('*')):
        if base.name == 'lo' or read_file(base/'operstate') != 'up':
            continue
        wireless = (base/'wireless').exists()
        speed = number(read_file(base/'speed'))
        if (speed is None or speed <= 0) and wireless and bitrate:
            speed = bitrate(base.name)
        kind = 'Wi-Fi' if wireless else 'Ethernet' if read_file(base/'type') == '1' else 'Network'
        result.append(dict(Name=base.name, Status='Up', InterfaceDescription=kind,
                           LinkSpeed=f'{speed:g} Mbps' if speed and speed > 0 else None))
    return result


def iw_link(name):
    """Details of the current Wi-Fi association from `iw dev NAME link` (None when iw is absent or not connected)."""
    if not shutil.which('iw'):
        return None
    raw = command(['iw', 'dev', name, 'link'])
    if not raw or 'Not connected' in raw:
        return None
    ssid = re.search(r'^\s*SSID: (.*)$', raw, re.M)
    signal = re.search(r'^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm', raw, re.M)
    rate = re.search(r'^\s*tx bitrate:\s*([\d.]+)\s*MBit/s', raw, re.M) or re.search(r'^\s*rx bitrate:\s*([\d.]+)\s*MBit/s', raw, re.M)
    return dict(ssid=ssid[1] if ssid else None, dbm=float(signal[1]) if signal else None,
                rate_mbps=float(rate[1]) if rate else None)


def dbm_to_percent(dbm):
    """NetworkManager's mapping: -100 dBm or worse is 0 %, -50 dBm or better is 100 %."""
    return max(0, min(100, 2*(dbm+100)))


def wifi_bitrate(name):
    """Link rate in Mbps from iw, otherwise from NetworkManager's RATE column."""
    link = iw_link(name)
    if link and link['rate_mbps']:
        return link['rate_mbps']
    return next((row['rate_mbps'] for row in nmcli_wifi() if row['device'] == name and row['rate_mbps']), None)


def split_nmcli(line):
    """nmcli -t escapes colons and backslashes inside field values."""
    fields, current, escaped = [], '', False
    for char in line:
        if escaped:
            current += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(current)
            current = ''
        else:
            current += char
    return fields + [current]


def parse_wireless(text):
    """Signal percent per interface from /proc/net/wireless.

    The level column (dBm) is converted with NetworkManager's mapping so the value matches the desktop's;
    the 0-70 link-quality column is only used when no dBm level is present.
    """
    result = {}
    for line in (text or '').splitlines():
        if ':' in line:
            name, fields = line.split(':', 1)
            values = fields.split()
            if len(values) >= 3:
                quality = number(values[1].rstrip('.'))
                try:
                    level = float(values[2].rstrip('.'))      # signed dBm; number() rejects negatives
                except ValueError:
                    level = None
                if level is not None and level < 0:
                    result[name.strip()] = dbm_to_percent(level)
                else:
                    result[name.strip()] = min(100, quality/70*100) if quality is not None else None
    return result


def nmcli_wifi():
    """Active Wi-Fi connections from NetworkManager: dicts with active, ssid, signal (%), device, rate_mbps."""
    if not shutil.which('nmcli'):          # never launch a program that is not installed
        return []
    base = ['nmcli', '-t', '-f', 'ACTIVE,SSID,SIGNAL,DEVICE,RATE', 'device', 'wifi', 'list']
    raw = command(base + ['--rescan', 'no'], timeout=10)
    if raw is None:
        raw = command(base, timeout=10)
    rows = []
    for line in (raw or '').splitlines():
        fields = split_nmcli(line)
        if len(fields) >= 4 and fields[0] in ('yes', '*'):
            rate = re.match(r'\s*([\d.]+)', fields[4]) if len(fields) > 4 else None
            rows.append(dict(active=True, ssid=fields[1], signal=number(fields[2]), device=fields[3],
                             rate_mbps=float(rate[1]) if rate else None))
    return rows


def linux_wifi(root='/sys/class/net', wireless='/proc/net/wireless'):
    qualities = parse_wireless(read_file(wireless))
    interfaces = {p.parent.name for p in Path(root).glob('*/wireless')} | set(qualities)
    if not interfaces:
        return []
    rows = {name: dict(name=name, state='disconnected', signal=qualities.get(name)) for name in sorted(interfaces)}
    # Signal source order: NetworkManager (what the desktop shows), then iw, then /proc/net/wireless.
    if shutil.which('nmcli'):
        for row in nmcli_wifi():
            if row['device'] in rows:
                rows[row['device']].update(state='connected', ssid=row['ssid'], signal=row['signal'])
    if shutil.which('iw'):
        for name, row in rows.items():
            if row['state'] == 'connected':
                continue
            link = iw_link(name)
            if link and link['ssid']:
                row.update(state='connected', ssid=link['ssid'])
                if link['dbm'] is not None:
                    row['signal'] = dbm_to_percent(link['dbm'])
    return list(rows.values())


def lspci_name(slot):
    """Friendly adapter name from `lspci -mm -nn -s SLOT`, e.g. 'Intel Iris Xe Graphics'."""
    if not slot or not shutil.which('lspci'):
        return None
    raw = command(['lspci', '-mm', '-nn', '-s', slot])
    try:
        fields = shlex.split((raw or '').strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    if len(fields) < 4:
        return None
    strip_id = lambda text: re.sub(r'\s*\[[0-9a-fA-F]{4}\]$', '', text).strip()
    vendor, chip = strip_id(fields[2]), strip_id(fields[3])
    marketing = re.findall(r'\[([^\[\]]+)\]', chip)        # 'TigerLake-LP GT2 [Iris Xe Graphics]' -> 'Iris Xe Graphics'
    name = marketing[-1] if marketing else chip
    short = next((label for prefix, label in (('Intel', 'Intel'), ('Advanced Micro Devices', 'AMD'), ('AMD', 'AMD'),
                                              ('NVIDIA', 'NVIDIA')) if vendor.startswith(prefix)), vendor)
    return name if name.lower().startswith(short.lower()) else f'{short} {name}'


def gpu_frequency(base, device):
    """Current graphics clock in MHz (i915 and xe expose it in sysfs; AMD uses pp_dpm_sclk)."""
    for path in (base/'gt_cur_freq_mhz', base/'gt/gt0/rps_cur_freq_mhz', device/'tile0/gt0/freq0/cur_freq'):
        value = number(read_file(path))
        if value is not None:
            return value
    match = re.search(r'(\d+)\s*Mhz\s*\*', read_file(device/'pp_dpm_sclk') or '', re.I)
    return float(match[1]) if match else None


def linux_gpu(root='/sys/class/drm', namer=None):
    """Graphics adapters from sysfs. namer(pci_slot) may supply a friendly device name."""
    result = []
    for base in sorted(Path(root).glob('card[0-9]*')):
        if not re.fullmatch(r'card\d+', base.name):
            continue
        device = base/'device'
        if not device.exists():
            continue
        vendor, model = read_file(device/'vendor'), read_file(device/'device')
        brand = {'0x1002': 'AMD', '0x8086': 'Intel', '0x10de': 'NVIDIA'}.get(vendor, 'Graphics')
        slot = re.search(r'^PCI_SLOT_NAME=(.+)$', read_file(device/'uevent') or '', re.M)
        friendly = read_file(device/'product_name') or (namer(slot[1].strip()) if namer and slot else None)
        hwmon = sorted((device/'hwmon').glob('hwmon*'))
        watts = next((number(read_file(p/'power1_average')) for p in hwmon if number(read_file(p/'power1_average')) is not None), None)
        result.append(dict(name=friendly or (f'{brand} ({model})' if model else brand),
                           vendor=vendor,
                           watts=watts/1e6 if watts is not None else None,
                           utilization=number(read_file(device/'gpu_busy_percent')),
                           frequency_mhz=gpu_frequency(base, device)))
    return result


def windows_wifi(include_ssid=True):
    """Native WLAN structs use fixed-width DWORDs and native pointer alignment."""
    DWORD = C.c_uint32
    class Guid(C.Structure):
        _fields_ = [('a', DWORD), ('b', C.c_uint16), ('c', C.c_uint16), ('d', C.c_ubyte*8)]
    class Interface(C.Structure):
        _fields_ = [('guid', Guid), ('name', C.c_wchar*256), ('state', DWORD)]
    class List(C.Structure):
        _fields_ = [('count', DWORD), ('index', DWORD), ('items', Interface*1)]
    class SSID(C.Structure):
        _fields_ = [('length', DWORD), ('bytes', C.c_ubyte*32)]
    class Association(C.Structure):
        _fields_ = [('ssid', SSID), ('bss', DWORD), ('mac', C.c_ubyte*6),
                    ('phy', DWORD), ('phy_index', DWORD), ('quality', DWORD), ('rx', DWORD), ('tx', DWORD)]
    class Security(C.Structure):
        _fields_ = [('enabled', C.c_int32), ('one_x', C.c_int32), ('auth', DWORD), ('cipher', DWORD)]
    class Connection(C.Structure):
        _fields_ = [('state', DWORD), ('mode', DWORD), ('profile', C.c_wchar*256),
                    ('association', Association), ('security', Security)]
    api = C.WinDLL('wlanapi.dll')
    api.WlanOpenHandle.argtypes = [DWORD, C.c_void_p, C.POINTER(DWORD), C.POINTER(C.c_void_p)]
    api.WlanEnumInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.POINTER(List))]
    api.WlanQueryInterface.argtypes = [C.c_void_p, C.POINTER(Guid), DWORD, C.c_void_p,
                                      C.POINTER(DWORD), C.POINTER(C.c_void_p), C.POINTER(DWORD)]
    api.WlanFreeMemory.argtypes = [C.c_void_p]
    api.WlanCloseHandle.argtypes = [C.c_void_p, C.c_void_p]
    handle, version, pointer = C.c_void_p(), DWORD(), C.POINTER(List)()
    if api.WlanOpenHandle(2, None, C.byref(version), C.byref(handle)):
        return None
    try:
        if api.WlanEnumInterfaces(handle, None, C.byref(pointer)):
            return None
        result = []
        rows = C.cast(C.addressof(pointer.contents)+List.items.offset, C.POINTER(Interface))
        for i in range(pointer.contents.count):
            interface = rows[i]
            row = dict(name=interface.name, state='connected' if interface.state == 1 else 'disconnected')
            data, size, opcode_type = C.c_void_p(), DWORD(), DWORD()
            try:
                # wlan_intf_opcode_current_connection = 7. Windows may deny location access.
                if interface.state == 1 and not api.WlanQueryInterface(handle, C.byref(interface.guid), 7, None,
                        C.byref(size), C.byref(data), C.byref(opcode_type)) and size.value >= C.sizeof(Connection):
                    a = C.cast(data, C.POINTER(Connection)).contents.association
                    row.update(signal=a.quality, rx_mbps=a.rx/1000, tx_mbps=a.tx/1000,
                               wifi_standard={4:'802.11a',5:'802.11b',6:'802.11g',7:'802.11n',8:'802.11ac',9:'802.11ad',10:'802.11ax',11:'802.11be'}.get(a.phy))
                    if include_ssid:
                        row['ssid'] = bytes(a.ssid.bytes[:min(a.ssid.length, 32)]).decode('utf-8', errors='replace')
                    else:
                        row['band'] = safe(windows_wifi_band)(api, handle, interface.guid, bytes(a.mac))
            finally:
                if data.value:
                    api.WlanFreeMemory(data)
            result.append(row)
        return result
    finally:
        if pointer:
            api.WlanFreeMemory(pointer)
        api.WlanCloseHandle(handle, None)


def windows_wifi_band(api, handle, guid, mac):
    """Read only the associated BSSID's center frequency, without retaining SSIDs."""
    class SSID(C.Structure):
        _fields_ = [('length', C.c_uint32), ('bytes', C.c_ubyte*32)]
    class Rates(C.Structure):
        _fields_ = [('length', C.c_uint32), ('rates', C.c_uint16*126)]
    class Entry(C.Structure):
        _fields_ = [('ssid', SSID), ('phy_id', C.c_uint32), ('mac', C.c_ubyte*6),
            ('bss_type', C.c_uint32), ('phy_type', C.c_uint32), ('rssi', C.c_int32),
            ('quality', C.c_uint32), ('reg_domain', C.c_ubyte), ('beacon', C.c_uint16),
            ('timestamp', C.c_uint64), ('host', C.c_uint64), ('capability', C.c_uint16),
            ('frequency', C.c_uint32), ('rates', Rates), ('ie_offset', C.c_uint32), ('ie_size', C.c_uint32)]
    class Table(C.Structure):
        _fields_ = [('size', C.c_uint32), ('count', C.c_uint32), ('entries', Entry*1)]
    pointer = C.POINTER(Table)()
    api.WlanGetNetworkBssList.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint32, C.c_int32, C.c_void_p, C.c_void_p]
    if api.WlanGetNetworkBssList(handle, C.byref(guid), None, 3, False, None, C.byref(pointer)):
        return None
    try:
        table = pointer.contents
        if not Table.entries.offset+table.count*C.sizeof(Entry) <= table.size <= 1024*1024:
            return None
        entries = C.cast(C.addressof(table)+Table.entries.offset, C.POINTER(Entry))
        for i in range(min(table.count, 2048)):
            if bytes(entries[i].mac) == mac:
                mhz = entries[i].frequency/1000
                return '2.4 GHz' if 2400 <= mhz < 2500 else '5 GHz' if 4900 <= mhz < 5925 else '6 GHz' if 5925 <= mhz <= 7125 else None
    finally:
        api.WlanFreeMemory(pointer)


def parse_journal_awake(text):
    """Use the journal's real/monotonic timestamp pair, independent of messages.

    CLOCK_MONOTONIC excludes suspend. Their difference measures time asleep;
    wall-clock adjustments make this check unavailable instead of inventing data.
    """
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    pairs = [(int(row['__REALTIME_TIMESTAMP'])/1e6, int(row['__MONOTONIC_TIMESTAMP'])/1e6)
             for row in rows if row.get('_TRANSPORT') == 'kernel' and '__MONOTONIC_TIMESTAMP' in row]
    if len(pairs) < 2:
        return None
    offsets = [real-mono for real, mono in pairs]
    if any(b < a-1 for a, b in zip(offsets, offsets[1:])):
        return None
    return pairs[-1][1]-pairs[0][1]


JOURNAL_TIMEOUT = 20   # a large journal can take longer than the default 5 s


SLEEP_START_ID = '6bbd95ee977941e497c48be27c254128'   # systemd: "Performing sleep operation ..."
SLEEP_END_ID = '8811e6df2a8e40f58a94cea26f8ebf14'     # systemd: "System returned from sleep operation ..."


def parse_systemd_sleep(text):
    """Seconds asleep this boot from systemd-sleep's start/return records, or None when there are none.

    Unlike the kernel's "PM: suspend entry/exit" lines these cover suspend-then-hibernate: the hibernate phase
    (hours or days) is a separate sleep operation that the kernel suspend messages never mention. Each
    operation's sleep time is (real-time span) - (monotonic span), because the monotonic clock stops while asleep.
    """
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('MESSAGE_ID') in (SLEEP_START_ID, SLEEP_END_ID) and '__MONOTONIC_TIMESTAMP' in row:
            rows.append((int(row['__REALTIME_TIMESTAMP'])/1e6, int(row['__MONOTONIC_TIMESTAMP'])/1e6, row['MESSAGE_ID']))
    rows.sort()
    start, total, pairs = None, 0.0, 0
    for real, mono, kind in rows:
        if kind == SLEEP_START_ID:
            start = (real, mono)
        elif start is not None:
            total += max(0.0, (real-start[0])-(mono-start[1]))
            pairs += 1
            start = None
    if start is not None or pairs == 0:   # unfinished operation (still asleep / truncated) or no records
        return None
    return total


def systemd_sleep_seconds():
    result = run_command(['journalctl', '-b', '0', '-o', 'json', '--no-pager', 'SYSLOG_IDENTIFIER=systemd-sleep'],
                         timeout=JOURNAL_TIMEOUT)
    if result is None or result[0] != 0:
        return None
    return parse_systemd_sleep(result[1])


def verify_linux_awake():
    # Preferred source: systemd's own sleep records (handles suspend-then-hibernate, where the hibernate phase can
    # last days and is invisible to the kernel suspend messages below).
    sleep = systemd_sleep_seconds()
    if sleep is not None:
        clocks = probe_clocks() or {}
        uptime, awake = clocks.get('uptime'), clocks.get('awake')
        if uptime is not None and awake is not None and 0 <= sleep <= uptime:
            event_seconds = max(0, uptime-sleep)
            return dict(event_seconds=event_seconds, awake_seconds=awake, difference=abs(event_seconds-awake),
                        message='Compared journal suspend intervals with the awake clock')
    # Kernel PM strings are kernel-generated (not translated); classification uses
    # transport metadata and clocks, never translated user-facing journal messages.
    # journalctl exits with 1 when --grep matches nothing, which is normal for a boot that never slept.
    result = run_command(['journalctl', '-k', '-b', '0', '-o', 'json', '--no-pager',
                          '--grep', 'PM: (suspend|hibernation) (entry|exit)'], timeout=JOURNAL_TIMEOUT)
    if result is None:
        return dict(message='Not available · system journal could not be read')
    code, raw = result
    if code == 1 and not raw.strip():
        # Distinguish "no suspend events" from a journal we cannot read: a plain query must return entries.
        probe = run_command(['journalctl', '-k', '-b', '0', '-n', '1', '-o', 'json', '--no-pager'], timeout=JOURNAL_TIMEOUT)
        if probe is None or probe[0] != 0 or not probe[1].strip():
            return dict(message='Not available · system journal could not be read')
        clocks = probe_clocks() or {}
        uptime, awake = clocks.get('uptime'), clocks.get('awake')
        if uptime is None or awake is None:
            return dict(message='Not available · journal clocks are inconsistent')
        return dict(event_seconds=uptime, awake_seconds=awake, difference=abs(uptime-awake),
                    message='Compared journal suspend intervals with the awake clock')
    if code != 0:
        return dict(message='Not available · system journal could not be read')
    seconds = parse_journal_awake(raw)
    if seconds is None:
        return dict(message='Not available · journal has insufficient suspend evidence')
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    pairs = [(int(row['__REALTIME_TIMESTAMP'])/1e6, int(row['__MONOTONIC_TIMESTAMP'])/1e6)
             for row in rows if row.get('_TRANSPORT') == 'kernel' and '__MONOTONIC_TIMESTAMP' in row]
    # Only completed suspend pairs support a whole-boot comparison. An odd final
    # record can be an entry awaiting its resume or a truncated journal.
    if len(pairs) % 2:
        return dict(message='Not available · journal has incomplete suspend intervals')
    sleep = (pairs[-1][0]-pairs[0][0])-seconds
    clocks = probe_clocks() or {}
    uptime, awake = clocks.get('uptime'), clocks.get('awake')
    if uptime is None or awake is None or sleep < 0 or sleep > uptime:
        return dict(message='Not available · journal clocks are inconsistent')
    event_seconds = max(0, uptime-sleep)
    return dict(event_seconds=event_seconds, awake_seconds=awake, difference=abs(event_seconds-awake),
                message='Compared journal suspend intervals with the awake clock')


@safe
def probe_battery():
    if sys.platform != 'win32':
        return linux_battery()
    return parse_battery(powershell("$s=@(Get-CimInstance -Namespace root\\wmi BatteryStatus -ErrorAction SilentlyContinue | "
        "Select-Object InstanceName,Charging,DischargeRate,ChargeRate,RemainingCapacity,Voltage); "
        "$f=@(Get-CimInstance -Namespace root\\wmi BatteryFullChargedCapacity -ErrorAction SilentlyContinue | "
        "Select-Object InstanceName,FullChargedCapacity); "
        "$d=@(Get-CimInstance -Namespace root\\wmi BatteryStaticData -ErrorAction SilentlyContinue | "
        "Select-Object InstanceName,DesignedCapacity); "
        "@{status=$s;full=$f;design=$d}|ConvertTo-Json -Depth 4 -Compress"))


@safe
def probe_gpu():
    output = command(['nvidia-smi', '--query-gpu=name,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total',
                      '--format=csv,noheader,nounits']) if shutil.which('nvidia-smi') else None
    nvidia = parse_nvidia(output)
    if sys.platform != 'win32':
        basic = linux_gpu(namer=lspci_name)
    else:
        raw = powershell('Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,PNPDeviceID | ConvertTo-Json -Compress')
        if raw is None:
            return nvidia or None
        basic = [dict(name=row.get('Name'), memory_total=number(row.get('AdapterRAM')) / 1024**2
                      if number(row.get('AdapterRAM')) else None)
                 for row in objects(parse_json(raw))]
    # Match each occurrence once, retaining integrated and secondary adapters.
    result = []
    remaining = list(nvidia)
    for gpu in basic:
        match = next((i for i, row in enumerate(remaining) if row['name'] == gpu['name']), None)
        if match is None and gpu.get('vendor') == '0x10de' and remaining:
            match = 0
        result.append(remaining.pop(match) if match is not None else gpu)
    return result + remaining


@safe
def probe_network():
    if sys.platform != 'win32':
        return linux_network(bitrate=wifi_bitrate)
    output = powershell("Get-CimInstance -Namespace root/StandardCimv2 MSFT_NetAdapter | Where-Object MediaConnectState -eq 1 | "
        "ForEach-Object { @{Name=$_.Name;InterfaceDescription=$_.InterfaceDescription;Status='Up';"
        "LinkSpeed=($_.TransmitLinkSpeed/1000000).ToString([cultureinfo]::InvariantCulture)+' Mbps';ifIndex=$_.InterfaceIndex;"
        "Type=$(if($_.InterfaceType -eq 71){'Wi-Fi'}elseif($_.InterfaceType -eq 6){'Ethernet'}else{'Network'})} } | ConvertTo-Json -Compress")
    return parse_adapters(output) if output is not None else None


@safe
def probe_wifi():
    if sys.platform != 'win32':
        return linux_wifi()
    return windows_wifi()


@safe
def probe_identity():
    if sys.platform == 'win32':
        data = parse_json(powershell("$o=Get-CimInstance Win32_OperatingSystem; "
            "$c=Get-CimInstance Win32_Processor; "
            "@{cpu=($c.Name -join ', ');boot=([DateTimeOffset]$o.LastBootUpTime).ToUnixTimeSeconds()}|ConvertTo-Json -Compress"))
        return data
    text = Path('/proc/cpuinfo').read_text()
    match = re.search(r'(?:model name|Hardware|Processor)\s*:\s*(.+)', text)
    return dict(cpu=match[1] if match else None)


@safe
def probe_hardware():
    """Infrequent inventory; no serial numbers or user identity."""
    if sys.platform == 'win32':
        return parse_json(powershell(
            "$c=Get-CimInstance Win32_ComputerSystem; $p=@(Get-CimInstance Win32_Processor); "
            "$m=@(Get-CimInstance Win32_NetworkAdapter | Where-Object PhysicalAdapter | "
            "ForEach-Object { $_.MACAddress }); "
            "@{manufacturer=$c.Manufacturer;model=$c.Model;cpu_cores=($p|Measure-Object NumberOfCores -Sum).Sum;"
            "cpu_threads=$c.NumberOfLogicalProcessors;mac_addresses=$m}|ConvertTo-Json -Compress"))
    import os
    macs = []
    for base in sorted(Path('/sys/class/net').glob('*')):
        address = read_file(base/'address').upper()
        if (base/'device').exists() and re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', address):
            macs.append(address)
    cores = set()
    for block in read_file('/proc/cpuinfo').split('\n\n'):
        fields = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        if 'core id' in fields:
            cores.add((fields.get('physical id'), fields['core id']))
    return dict(manufacturer=read_file('/sys/class/dmi/id/sys_vendor') or None,
                model=read_file('/sys/class/dmi/id/product_name') or None,
                cpu_cores=len(cores) or None, cpu_threads=os.cpu_count(), mac_addresses=macs)


@safe
def probe_power():
    if sys.platform != 'win32':
        return linux_power()
    class Status(C.Structure):
        _fields_ = [('ac', C.c_ubyte), ('flag', C.c_ubyte), ('percent', C.c_ubyte),
                    ('reserved', C.c_ubyte), ('life', C.c_uint32), ('full', C.c_uint32)]
    s = Status()
    if not C.windll.kernel32.GetSystemPowerStatus(C.byref(s)):
        return None
    absent = bool(s.flag & 128) and s.flag != 255
    return dict(percent=None if absent or s.percent == 255 else s.percent,
                state='No battery' if absent else 'Charging' if s.flag != 255 and s.flag & 8 else
                      'On AC power' if s.ac == 1 else 'On battery' if s.ac == 0 else None,
                remaining=None if absent or s.life == 0xFFFFFFFF else s.life,
                ac=s.ac == 1 if s.ac != 255 else None)


@safe
def probe_clocks():
    if sys.platform == 'win32':
        kernel = C.windll.kernel32
        kernel.GetTickCount64.restype = C.c_ulonglong
        uptime = kernel.GetTickCount64()/1000
        value = C.c_ulonglong()
        awake = value.value / 10_000_000 if kernel.QueryUnbiasedInterruptTime(C.byref(value)) else None
        fast = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Power') as key:
                fast = bool(winreg.QueryValueEx(key, 'HiberbootEnabled')[0])
        except OSError:
            pass
        return dict(uptime=uptime, awake=awake, boot=time.time()-uptime, fast_startup=fast)
    uptime = time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, 'CLOCK_BOOTTIME') else None
    return dict(uptime=uptime, awake=time.monotonic(), boot=time.time()-uptime if uptime else None, fast_startup=None)


@safe
def probe_cpu():
    if sys.platform == 'win32':
        idle, kernel, user = C.c_ulonglong(), C.c_ulonglong(), C.c_ulonglong()
        if not C.windll.kernel32.GetSystemTimes(C.byref(idle), C.byref(kernel), C.byref(user)):
            return None
        return idle.value, kernel.value + user.value
    return parse_proc_stat(Path('/proc/stat').read_text())


def cpu_percent(previous, current):
    if previous is None or current is None or current[1] <= previous[1]:
        return None
    return max(0, min(100, 100 * (1 - (current[0]-previous[0])/(current[1]-previous[1]))))


@safe
def probe_ram():
    if sys.platform == 'win32':
        class Memory(C.Structure):
            _fields_ = [('length', C.c_uint32), ('load', C.c_uint32)] + [(n, C.c_ulonglong) for n in
                       ('total', 'available', 'page', 'avail_page', 'virtual', 'avail_virtual', 'extended')]
        m = Memory()
        m.length = C.sizeof(m)
        if not C.windll.kernel32.GlobalMemoryStatusEx(C.byref(m)):
            return None
        return dict(total=m.total, used=m.total-m.available, percent=m.load)
    return parse_meminfo(Path('/proc/meminfo').read_text())


@safe
def probe_disks():
    if sys.platform == 'win32':
        kernel = C.windll.kernel32
        mask = kernel.GetLogicalDrives()
        paths = [f'{chr(65+i)}:\\' for i in range(26) if mask & (1 << i) and kernel.GetDriveTypeW(f'{chr(65+i)}:\\') == 3]
    else:
        paths = ['/']
        for line in Path('/proc/mounts').read_text().splitlines():
            parts = line.split()
            if parts[0].startswith('/dev/') and parts[1] not in paths:
                paths.append(parts[1].replace('\\040', ' '))
    result = []
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
            result.append(dict(path=path, total=usage.total, used=usage.used,
                               percent=100*usage.used/usage.total))
        except OSError:
            result.append(dict(path=path, total=None, used=None, percent=None))
    return result


@safe
def probe_counters(inventory=False):
    if sys.platform != 'win32':
        active = {row['Name'] for row in linux_network()}
        return {k: v for k, v in parse_net_dev(Path('/proc/net/dev').read_text()).items() if k in active}
    class Guid(C.Structure):
        _fields_ = [('data', C.c_ubyte * 16)]
    class Row(C.Structure):
        _fields_ = [('luid', C.c_ulonglong), ('index', C.c_uint32), ('guid', Guid),
                    ('alias', C.c_wchar * 257), ('description', C.c_wchar * 257),
                    ('address_length', C.c_uint32), ('address', C.c_ubyte * 32), ('permanent', C.c_ubyte * 32)] + \
                   [(n, C.c_uint32) for n in ('mtu', 'type', 'tunnel', 'media', 'physical', 'access', 'direction')] + \
                   [('flags', C.c_ubyte)] + [(n, C.c_uint32) for n in ('oper', 'admin', 'connect')] + \
                   [('network_guid', Guid), ('connection', C.c_uint32)] + \
                   [(n, C.c_ulonglong) for n in ('tx_speed', 'rx_speed', 'in_bytes', 'in_unicast', 'in_nonunicast',
                     'in_discards', 'in_errors', 'in_unknown', 'in_unicast_bytes', 'in_multicast_bytes', 'in_broadcast_bytes',
                     'out_bytes', 'out_unicast', 'out_nonunicast', 'out_discards', 'out_errors',
                     'out_unicast_bytes', 'out_multicast_bytes', 'out_broadcast_bytes', 'queue')]
    class Table(C.Structure):
        _fields_ = [('count', C.c_uint32), ('rows', Row * 1)]
    pointer = C.POINTER(Table)()
    api = C.windll.iphlpapi
    if api.GetIfTable2(C.byref(pointer)):
        return None
    try:
        rows = C.cast(C.addressof(pointer.contents) + Table.rows.offset, C.POINTER(Row))
        if inventory:
            return [dict(name=row.alias, model=row.description,
                kind='Bluetooth' if 'bluetooth' in row.description.lower() else 'Virtual / VPN' if not row.flags & 1 else 'Wi-Fi' if row.type == 71 else 'Ethernet',
                connected=row.oper == 1, speed_mbps=row.tx_speed/1000000 if row.oper == 1 and row.tx_speed > 0 else None,
                mac=':'.join(f'{b:02X}' for b in row.address[:min(row.address_length, 32)]))
                for row in sorted((rows[i] for i in range(min(pointer.contents.count, 256))), key=lambda r: not r.flags & 1)
                if row.type != 24 and not row.flags & 2][:16]  # Filter layers are not additional network cards.
        return {row.index: (row.in_bytes, row.out_bytes) for row in (rows[i] for i in range(pointer.contents.count))
                if row.oper == 1 and row.type != 24 and not re.search(r'vmware|hyper-v|loopback|vethernet', row.alias+' '+row.description, re.I)
                and row.flags & 1}
    finally:
        api.FreeMibTable(pointer)


@safe
def verify_awake():
    if sys.platform != 'win32':
        return verify_linux_awake()
    script = r"""
$ErrorActionPreference='Stop'
$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$now=Get-Date
$old=Get-WinEvent -LogName System -Oldest -MaxEvents 1
$filter=@{LogName='System';ProviderName=@('Microsoft-Windows-Kernel-Power','Microsoft-Windows-Kernel-Boot');Id=@(42,27);StartTime=$boot;EndTime=$now}
$found=@()
try { $found=@(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop) }
catch { if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw } }
$events=@($found | ForEach-Object {
    @{id=$_.Id;provider=$_.ProviderName;time=([DateTimeOffset]$_.TimeCreated).ToUnixTimeMilliseconds()/1000}
})
@{boot=([DateTimeOffset]$boot).ToUnixTimeMilliseconds()/1000;now=([DateTimeOffset]$now).ToUnixTimeMilliseconds()/1000;
covered=($old.TimeCreated -le $boot);events=$events} | ConvertTo-Json -Depth 5 -Compress
"""
    data = parse_json(powershell(script))
    clocks = probe_clocks() or {}
    if not isinstance(data, dict):
        return dict(message='Not available · Windows event log could not be read')
    if not data.get('covered'):
        return dict(message='Not available · Windows event log does not cover this boot')
    seconds = parse_event_awake(data.get('boot'), data.get('now'), data.get('events', []))
    awake = clocks.get('awake')
    # Align the independent clocks to the event-log snapshot instant.
    if awake is not None:
        awake -= max(0, time.time()-data['now'])
    if seconds is None or awake is None:
        return dict(message='Not available · Windows event log has incomplete sleep intervals')
    return dict(event_seconds=seconds, awake_seconds=awake, difference=abs(seconds-awake))


def verify_debug():
    """Raw journal evidence for diagnosing a Verify gap. Run: python3 lifetime_signals.py --verify-debug"""
    lines = [f'clocks: {probe_clocks()}']
    queries = [('kernel suspend/resume messages', ['journalctl', '-k', '-b', '0', '-o', 'json', '--no-pager',
                                                   '--grep', 'PM: (suspend|hibernation) (entry|exit)']),
               ('systemd-sleep', ['journalctl', '-b', '0', '-o', 'json', '--no-pager', 'SYSLOG_IDENTIFIER=systemd-sleep']),
               ('logind', ['journalctl', '-b', '0', '-o', 'json', '--no-pager', '_SYSTEMD_UNIT=systemd-logind.service',
                           '--grep', 'Suspend|sleep|Lid'])]
    for title, args in queries:
        result = run_command(args, timeout=JOURNAL_TIMEOUT)
        lines.append(f'--- {title}: ' + ('could not run' if result is None else f'exit {result[0]}'))
        for raw in (result[1].splitlines() if result else []):
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            lines.append('  real={} mono={} source_mono={} id={} | {}'.format(
                row.get('__REALTIME_TIMESTAMP'), row.get('__MONOTONIC_TIMESTAMP'), row.get('_SOURCE_MONOTONIC_TIMESTAMP'),
                row.get('MESSAGE_ID'), str(row.get('MESSAGE'))[:90]))
    lines.append(f'verify_linux_awake(): {verify_linux_awake()}')
    return '\n'.join(lines)


if __name__ == '__main__' and '--verify-debug' in sys.argv:
    print(verify_debug())
