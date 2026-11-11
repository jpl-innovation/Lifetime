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
