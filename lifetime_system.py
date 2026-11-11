"""Shared, bounded hardware contract and presentation; no OS or Qt dependencies."""
import math

# A single schema is used by the wire validator and local sanitization. Numbers
# include explicit limits; strings never include identifiers such as serials.
TEXT = str
REASONS = frozenset(('The operating system did not expose this reading',
                     "Not reported by this computer's firmware", 'Access denied by Windows',
                     'Windows did not expose an identified CPU temperature sensor',
                     'Windows did not expose network driver information'))
SCHEMA = {
    'cpu': {'temperature_c': (-100, 250), 'current_mhz': (0, 20000), 'max_mhz': (0, 20000)},
    'fans': [{'name': TEXT, 'rpm': (0, 100000)}],
    'adapters': [{'name': TEXT, 'model': TEXT, 'kind': TEXT, 'connected': bool,
                  'speed_mbps': (0, 10000000), 'driver': TEXT, 'vendor': TEXT,
                  'mac': TEXT, 'wifi_standard': TEXT, 'band': TEXT}],
    'memory': {'modules': [{'kind': TEXT, 'speed_mhz': (0, 20000), 'capacity_bytes': (0, 2**50, int)}],
               'slots_used': (0, 256, int), 'slots_total': (0, 256, int)},
    'disks': [{'model': TEXT, 'kind': TEXT, 'size_bytes': (0, 2**53-1, int), 'health': TEXT}],
    'board': {'manufacturer': TEXT, 'model': TEXT, 'bios_vendor': TEXT, 'bios_version': TEXT, 'bios_date': TEXT},
    'gpus': [{'name': TEXT, 'driver': TEXT, 'vram_bytes': (0, 2**50, int)}],
    'displays': [{'width': (1, 32768, int), 'height': (1, 32768, int), 'hz': (1, 1000)}],
    'os': {'name': TEXT, 'build': TEXT, 'architecture': TEXT, 'installed': TEXT,
           'last_boot': TEXT, 'uptime_seconds': (0, 3660*86400)},
    'unavailable': {key: REASONS for key in ('cpu', 'cpu_temperature', 'fans', 'adapters', 'adapter_driver',
                                        'memory', 'disks', 'board', 'gpus', 'os')},
}


def validate(value, schema=None):
    """Reject unknown keys, excessive nesting/lists, booleans as numbers and NaN."""
    schema = SCHEMA if schema is None else schema
    if value is None:
        return value
    if isinstance(schema, dict):
        if not isinstance(value, dict) or set(value)-set(schema):
            raise ValueError('Invalid hardware fields')
        for key, item in value.items():
            validate(item, schema[key])
    elif isinstance(schema, list):
        if not isinstance(value, list) or len(value) > (16 if schema is SCHEMA['adapters'] else 8):
            raise ValueError('Too many hardware rows')
        for item in value:
            if not isinstance(item, dict):
                raise ValueError('Invalid hardware row')
            validate(item, schema[0])
    elif isinstance(schema, tuple):
        if type(value) not in (int, float) or not schema[0] <= value <= schema[1] or not math.isfinite(value) or (len(schema) == 3 and type(value) is not int):
            raise ValueError('Hardware number out of range')
    elif schema is bool:
        if type(value) is not bool:
            raise ValueError('Invalid hardware flag')
    elif isinstance(schema, frozenset):
        if not isinstance(value, str) or value not in schema:
            raise ValueError('Invalid hardware unavailable reason')
    elif not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid hardware text')
    return value


def clean(value, schema=None):
    """Drop failed/unsupported local fields individually before transmission."""
    schema = SCHEMA if schema is None else schema
    if isinstance(schema, dict):
        return {k: clean(v, schema[k]) for k, v in (value or {}).items() if k in schema}
    if isinstance(schema, list):
        return [clean(v, schema[0]) for v in (value or [])[:16 if schema is SCHEMA['adapters'] else 8] if isinstance(v, dict)]
    if isinstance(schema, tuple) and len(schema) == 3 and type(value) is float and value.is_integer():
        value = int(value)
    if schema is str and value is not None:
        value = ''.join(c for c in str(value) if ord(c) >= 32)[:160]
    try:
        return validate(value, schema)
    except ValueError:
        return None


def rows(data, tr):
    """Identical labels, units and unavailable explanations on both System pages."""
    data = data or {}
    reasons = data.get('unavailable') or {}
    def unavailable(domain=None):
        return tr('Not available')+' · '+tr(reasons.get(domain) or 'The operating system did not expose this reading')
    missing = unavailable()
    def val(v, unit=''):
        return missing if v is None or v == '' else f'{v:g}{unit}' if type(v) in (int, float) else str(v)
    def fields(item, keys):
        if all(item.get(k) in (None, '') for k, _ in keys):
            return missing
        return ' · '.join(val(item.get(k), unit) for k, unit in keys)
    cpu, memory, board, os = (data.get(k) or {} for k in ('cpu', 'memory', 'board', 'os'))
    result = [('CPU temperature', val(cpu.get('temperature_c'), ' °C') if cpu.get('temperature_c') is not None else unavailable('cpu_temperature')),
              ('CPU current / maximum clock', fields(cpu, [('current_mhz', ' MHz'), ('max_mhz', ' MHz')]))]
    fans = data.get('fans')
    fan_missing = tr('Not available')+' · '+tr(reasons.get('fans') or "Not reported by this computer's firmware")
    result.append(('Cooling fans', fan_missing if not fans else
                   f'{len(fans)} · '+ '; '.join(val(f.get('name'))+' · '+(val(f.get('rpm'), ' RPM') if f.get('rpm') is not None else fan_missing) for f in fans)))
    adapters = data.get('adapters') or []
    result.append(('Network cards', '\n'.join(fields(a, [(k, '') for k in ('name', 'model', 'kind')])+ ' · '+
        (tr('Connected') if a.get('connected') is True else tr('Disconnected') if a.get('connected') is False else missing)+
        ' · '+tr('Link speed')+': '+val(a.get('speed_mbps'), ' Mbps')+
        '\n'+tr('Driver')+': '+(val(a.get('driver')) if a.get('driver') else unavailable('adapter_driver'))+
        (' · '+fields(a, [('wifi_standard', ''), ('band', '')]) if a.get('kind') == 'Wi-Fi' else '') for a in adapters) or unavailable('adapters')))
    result.append(('RAM modules', '\n'.join(fields(m, [('kind', ''), ('speed_mhz', ' MHz')])+ ' · '+val(m.get('capacity_bytes')/2**30 if m.get('capacity_bytes') is not None else None, ' GiB') for m in memory.get('modules') or []) or unavailable('memory')))
    result.append(('RAM slots used / total', fields(memory, [('slots_used', ''), ('slots_total', '')])))
    result.append(('Physical disks', '\n'.join(fields(d, [('model', ''), ('kind', '')])+ ' · '+val(d.get('size_bytes')/2**30 if d.get('size_bytes') is not None else None, ' GiB')+' · '+val(d.get('health')) for d in data.get('disks') or []) or missing))
    result.append(('Motherboard', fields(board, [('manufacturer', ''), ('model', '')])))
    result.append(('BIOS vendor / version / date', fields(board, [('bios_vendor', ''), ('bios_version', ''), ('bios_date', '')])))
    result.append(('GPU driver / VRAM', '\n'.join(fields(g, [('name', ''), ('driver', '')])+' · '+val(g.get('vram_bytes')/2**30 if g.get('vram_bytes') is not None else None, ' GiB') for g in data.get('gpus') or []) or missing))
    displays = data.get('displays') or []
    result.append(('Displays', (f'{len(displays)} · '+'; '.join(fields(d, [('width', ' px'), ('height', ' px'), ('hz', ' Hz')]) for d in displays)) if displays else missing))
    result.append(('OS build / architecture / installed', fields(os, [('name', ''), ('build', ''), ('architecture', ''), ('installed', '')])))
    result.append(('Uptime / last boot', fields(os, [('uptime_seconds', ' s'), ('last_boot', '')])))
    domains = {'RAM slots used / total': 'memory', 'Physical disks': 'disks', 'Motherboard': 'board',
               'BIOS vendor / version / date': 'board', 'GPU driver / VRAM': 'gpus',
               'OS build / architecture / installed': 'os', 'Uptime / last boot': 'os'}
    return [(tr(key), value.replace(missing, unavailable(domains.get(key)))) for key, value in result]


def sections(data, tr):
    """Grouped specification rows: (label, value, unavailable reason)."""
    data = data or {}
    reasons = data.get('unavailable') or {}
    generic = 'The operating system did not expose this reading'

    def row(label, value, unit='', domain=None, reason=None):
        missing = value is None or value == ''
        shown = tr('Not available') if missing else f'{value:g}{unit}' if type(value) in (int, float) else str(value)
        return tr(label), shown, tr(reason or reasons.get(domain) or generic) if missing else ''

    def gib(value):
        return value / 2**30 if value is not None else None

    def title(key, number):
        return tr(key, number=number)

    cpu = data.get('cpu') or {}
    cooling = [('', [row('CPU temperature', cpu.get('temperature_c'), ' °C', 'cpu_temperature'),
                     row('Current clock', cpu.get('current_mhz'), ' MHz', 'cpu'),
                     row('Maximum clock', cpu.get('max_mhz'), ' MHz', 'cpu')])]
    fans = data.get('fans') or []
    fan_reason = reasons.get('fans') or "Not reported by this computer's firmware"
    cooling[0][1].append(row('Reported fans', len(fans) if fans else None, reason=fan_reason))
    for i, fan in enumerate(fans, 1):
        cooling.append((fan.get('name') or title('Fan {number}', i),
                        [row('Fan speed', fan.get('rpm'), ' RPM', reason=fan_reason)]))

    memory = data.get('memory') or {}
    modules = [('', [row('Slots used', memory.get('slots_used'), domain='memory'),
                     row('Total slots', memory.get('slots_total'), domain='memory')])]
    for i, module in enumerate(memory.get('modules') or [], 1):
        modules.append((title('Memory module {number}', i), [
            row('Memory type', module.get('kind')), row('Memory speed', module.get('speed_mhz'), ' MHz'),
            row('Capacity', gib(module.get('capacity_bytes')), ' GiB')]))
    if not memory.get('modules'):
        modules[0][1].insert(0, row('RAM modules', None, domain='memory'))

    disks = [(disk.get('model') or title('Drive {number}', i), [
        row('Drive type', disk.get('kind')), row('Capacity', gib(disk.get('size_bytes')), ' GiB'),
        row('Disk health', disk.get('health'))]) for i, disk in enumerate(data.get('disks') or [], 1)]

    adapters = []
    for i, adapter in enumerate(data.get('adapters') or [], 1):
        connected = adapter.get('connected')
        entries = [row('Model', adapter.get('model')), row('Connection type', adapter.get('kind')),
                   row('Status', tr('Connected') if connected is True else tr('Disconnected') if connected is False else None),
                   row('Link speed', adapter.get('speed_mbps'), ' Mbps'),
                   row('Driver', adapter.get('driver'), domain='adapter_driver')]
        if adapter.get('vendor'):
            entries.append(row('Manufacturer', adapter['vendor']))
        if adapter.get('mac'):
            entries.append(row('MAC address', adapter['mac']))
        if adapter.get('kind') == 'Wi-Fi':
            entries.extend([row('Wi-Fi standard', adapter.get('wifi_standard')), row('Wi-Fi band', adapter.get('band'))])
        adapters.append((adapter.get('name') or title('Network card {number}', i), entries))

    graphics = [(gpu.get('name') or title('Graphics adapter {number}', i), [
        row('Driver', gpu.get('driver')), row('Video memory', gib(gpu.get('vram_bytes')), ' GiB')])
        for i, gpu in enumerate(data.get('gpus') or [], 1)]
    if not graphics:
        graphics.append(('', [row('GPU driver / VRAM', None, domain='gpus')]))
    for i, display in enumerate(data.get('displays') or [], 1):
        width, height = display.get('width'), display.get('height')
        resolution = f'{width} × {height} px' if width is not None and height is not None else None
        graphics.append((title('Display {number}', i), [row('Resolution', resolution), row('Refresh rate', display.get('hz'), ' Hz')]))
    if not data.get('displays'):
        graphics.append(('', [row('Displays', None)]))

    board, os = data.get('board') or {}, data.get('os') or {}
    return [(tr('Processor & cooling'), cooling), (tr('Memory'), modules),
            (tr('Storage'), disks or [('', [row('Physical disks', None, domain='disks')])]),
            (tr('Network cards'), adapters or [('', [row('Network cards', None, domain='adapters')])]),
            (tr('Graphics & displays'), graphics),
            (tr('Motherboard & BIOS'), [('', [row(label, board.get(key), domain='board') for label, key in (
                ('Manufacturer', 'manufacturer'), ('Model', 'model'), ('BIOS vendor', 'bios_vendor'),
                ('BIOS version', 'bios_version'), ('BIOS date', 'bios_date'))])]),
            (tr('Operating system'), [('', [row(label, os.get(key), unit, 'os') for label, key, unit in (
                ('OS name', 'name', ''), ('OS build', 'build', ''), ('Architecture', 'architecture', ''),
                ('Installed', 'installed', ''), ('Uptime', 'uptime_seconds', ' s'), ('Last boot', 'last_boot', ''))])])]
