"""Versioned company reporting contract; no I/O or platform dependencies."""
import datetime as dt
import json
import math
import re
import uuid
from lifetime_identity import FIELDS as IDENTITY_FIELDS, normalize as normalize_identity

VERSION = 1
CLIENT_VERSION = '1.2.0'
MAX_BYTES = 1024 * 1024
MAX_RECORDS = 100


class ProtocolError(ValueError):
    pass


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ProtocolError('Invalid or incompatible fields')
    return value


def number(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError('Expected a nonnegative number')
    if value < 0 or value > 2**53 - 1 or not math.isfinite(value):
        raise ProtocolError('Number outside supported range')
    if integer and not isinstance(value, int):
        raise ProtocolError('Expected an integer')
    return value


def identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ProtocolError('Expected a canonical device UUID') from None
    return value


def timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (ValueError, OverflowError):
        raise ProtocolError('Expected a timezone-aware ISO timestamp') from None


def validate_identity(value):
    try:
        return normalize_identity(value, network=True)
    except ValueError as error:
        raise ProtocolError(str(error)) from None


def validate_sync(message):
    _object(message, ('version', 'device_id', 'policy_version', 'policy', 'records', 'history', 'readings', 'health', 'client_version', 'command_result'),
            ('version', 'device_id', 'policy_version', 'policy', 'records'))
    if type(message['version']) is not int or message['version'] != VERSION:
        raise ProtocolError('Unsupported protocol version')
    identifier(message['device_id'])
    if 'client_version' in message and (not isinstance(message['client_version'], str)
            or not 1 <= len(message['client_version']) <= 64
            or any(ord(c) < 33 or ord(c) > 126 for c in message['client_version'])):
        raise ProtocolError('Invalid client version')
    if number(message['policy_version'], True) < 1:
        raise ProtocolError('Invalid policy version')
    policy = _object(message['policy'], ('history', 'resources', 'health', 'control'), ('history', 'resources'))
    if any(type(v) is not bool for v in policy.values()):
        raise ProtocolError('Policy values must be boolean')
    if 'health' in message:
        if policy.get('health') is not True:
            raise ProtocolError('Health is not permitted')
        validate_health(message['health'])
    records = message['records']
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ProtocolError('Batch exceeds record limit')
    fields = ('seq', 'observed_at', 'awake_seconds', 'measured_seconds', 'imported_seconds', 'totals')
    for record in records:
        _object(record, fields, fields)
        if number(record['seq'], True) < 1:
            raise ProtocolError('Invalid sequence')
        timestamp(record['observed_at'])
        for key in ('awake_seconds', 'measured_seconds', 'imported_seconds'):
            number(record[key])
        if abs(record['awake_seconds'] - record['measured_seconds'] - record['imported_seconds']) > 0.000001:
            raise ProtocolError('Source deltas do not match awake duration')
        totals = _object(record['totals'], ('measured_seconds', 'imported_seconds', 'baseline_seconds'),
                         ('measured_seconds', 'imported_seconds', 'baseline_seconds'))
        for value in totals.values():
            number(value)
    if 'history' in message:
        if not policy['history']:
            raise ProtocolError('History is not permitted')
        history = _object(message['history'], ('daily', 'imported_daily', 'first_seen', 'first_seen_source', 'recording_started', 'daily_estimated',
                                               *HISTORY_EXTRA))
        validate_history_extra(history)
        for field in ('daily', 'imported_daily'):
            days = history.get(field, {})
            if not isinstance(days, dict) or len(days) > 3660:
                raise ProtocolError('Invalid daily history')
            for day, seconds in days.items():
                try:
                    if dt.date.fromisoformat(day).isoformat() != day:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ProtocolError('Invalid history date') from None
                if number(seconds) > 86400:
                    raise ProtocolError('Daily history exceeds one day')
        for field in ('first_seen', 'recording_started'):
            if history.get(field) is not None:
                timestamp(history[field])
        if 'daily_estimated' in history and type(history['daily_estimated']) is not bool:
            raise ProtocolError('Invalid estimate flag')
        if history.get('first_seen_source', 'unknown') not in ('os-install-date', 'system-file-date', 'tracking-start', 'imported-log', 'unknown'):
            raise ProtocolError('Invalid provenance')
    if 'readings' in message:
        if not policy['resources']:
            raise ProtocolError('System readings are not permitted')
        readings = _object(message['readings'], ('observed_at', 'system', 'release', 'machine', 'python_version', 'specs', 'performance', 'health'), ('observed_at',))
        timestamp(readings['observed_at'])
        for key in ('system', 'release', 'machine', 'python_version'):
            if key in readings:
                _text(readings[key])
        if 'specs' in readings:
            validate_specs(readings['specs'])
        if 'health' in readings:
            validate_health(readings['health'])
        if 'performance' in readings:
            validate_performance(readings['performance'])
    if 'command_result' in message:
        result = _object(message['command_result'], ('id', 'status', 'detail'), ('id', 'status', 'detail'))
        identifier(result['id'])
        if result['status'] not in ('done', 'failed', 'declined'):
            raise ProtocolError('Invalid command result')
        _text(result['detail'], 200)
    if len(canonical(message)) > MAX_BYTES:
        raise ProtocolError('Message too large')
    return message


def _text(value, maximum=128):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ProtocolError('Invalid text reading')
    return value


HISTORY_EXTRA = ('hourly', 'dated_hourly', 'sessions', 'boots', 'current_session_seconds', 'longest_session_seconds')
MAX_SESSIONS = 100
MAX_DETAIL_DAYS = 400


def _day(value):
    try:
        if not isinstance(value, str) or len(value) != 10 or dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProtocolError('Invalid date') from None
    return value


def _hours(value):
    if not isinstance(value, list) or len(value) != 24:
        raise ProtocolError('Expected 24 hourly values')
    for item in value:
        if number(item) > 3660 * 3600:
            raise ProtocolError('Hourly value out of range')


def validate_history_extra(history):
    """Activity detail (1.2): bounded shapes the desktop's own charts consume."""
    if 'hourly' in history:
        _hours(history['hourly'])
    if 'dated_hourly' in history:
        days = history['dated_hourly']
        if not isinstance(days, dict) or len(days) > 120:
            raise ProtocolError('Invalid dated hourly history')
        for day, hours in days.items():
            _day(day)
            _hours(hours)
            if sum(hours) > 86400 * 1.01:
                raise ProtocolError('Dated hourly history exceeds one day')
    if 'sessions' in history:
        sessions = history['sessions']
        if not isinstance(sessions, list) or len(sessions) > MAX_SESSIONS:
            raise ProtocolError('Invalid session list')
        for item in sessions:
            _object(item, ('start', 'seconds', 'imported'), ('start', 'seconds'))
            timestamp(item['start'])
            if number(item['seconds']) > 3660*86400:
                raise ProtocolError('Session too long')
            if 'imported' in item and type(item['imported']) is not bool:
                raise ProtocolError('Invalid session flag')
    if 'boots' in history:
        if number(history['boots'], True) > 100000000:
            raise ProtocolError('Boot count out of range')
    for key in ('current_session_seconds', 'longest_session_seconds'):
        if key in history:
            if number(history[key]) > 3660*86400:
                raise ProtocolError('Session too long')


def validate_health_detail(detail):
    """Stability, battery and sleep series (1.2) in the shapes the desktop Health page consumes."""
    _object(detail, ('stability', 'battery', 'sleep'))
    if detail.get('stability') is not None:
        events = detail['stability']
        if not isinstance(events, list) or len(events) > MAX_DETAIL_DAYS:
            raise ProtocolError('Invalid stability list')
        for event in events:
            _object(event, ('date', 'kind'), ('date', 'kind'))
            timestamp(event['date'])
            if event['kind'] not in ('Unexpected shutdown', 'Crash'):
                raise ProtocolError('Invalid stability event')
    battery = detail.get('battery')
    if battery is not None:
        _object(battery, ('packs', 'history', 'usage'))
        packs = battery.get('packs', [])
        if not isinstance(packs, list) or len(packs) > 4:
            raise ProtocolError('Invalid battery packs')
        for pack in packs:
            _object(pack, ('full', 'design', 'cycles', 'chemistry', 'unit', 'health'))
            for key in ('full', 'design', 'cycles', 'health'):
                limit = 1000 if key == 'health' else 100000 if key == 'cycles' else 1000000
                if pack.get(key) is not None and number(pack[key], key == 'cycles') > limit:
                    raise ProtocolError('Battery value out of range')
            if pack.get('chemistry') is not None:
                _text(pack['chemistry'], 32)
            if pack.get('unit', 'Wh') not in ('Wh', 'Ah'):
                raise ProtocolError('Invalid battery unit')
        history = battery.get('history', [])
        if not isinstance(history, list) or len(history) > MAX_DETAIL_DAYS:
            raise ProtocolError('Invalid battery history')
        for item in history:
            _object(item, ('date', 'full', 'health'), ('date', 'full'))
            _day(item['date'])
            for key in ('full', 'health'):
                if item.get(key) is not None and number(item[key]) > (1000 if key == 'health' else 1000000):
                    raise ProtocolError('Battery value out of range')
        usage = battery.get('usage')
        if usage is not None:
            if not isinstance(usage, dict) or len(usage) > MAX_DETAIL_DAYS:
                raise ProtocolError('Invalid battery usage')
            for day, values in usage.items():
                _day(day)
                _object(values, ('ac', 'battery'), ('ac', 'battery'))
                if any(number(v) > 90000 for v in values.values()):
                    raise ProtocolError('Battery usage exceeds one day')
    sleep = detail.get('sleep')
    if sleep is not None:
        _object(sleep, ('daily', 'nights'), ('daily', 'nights'))
        if not isinstance(sleep['daily'], dict) or len(sleep['daily']) > MAX_DETAIL_DAYS:
            raise ProtocolError('Invalid sleep days')
        for day, values in sleep['daily'].items():
            _day(day)
            _object(values, ('cycles', 'seconds'), ('cycles', 'seconds'))
            if number(values['cycles'], True) > 1000 or number(values["seconds"]) > 90000:
                raise ProtocolError('Sleep value out of range')
        if not isinstance(sleep['nights'], list) or len(sleep['nights']) > MAX_DETAIL_DAYS:
            raise ProtocolError('Invalid sleep nights')
        for night in sleep['nights']:
            _object(night, ('date', 'seconds'), ('date', 'seconds'))
            _day(night['date'])
            if number(night["seconds"]) > 90000:
                raise ProtocolError('Night exceeds one day')


def without_extended(message):
    """The 1.1 wire format, for servers that predate Activity/Health detail."""
    result = dict(message)
    if 'history' in result:
        result['history'] = {k: v for k, v in result['history'].items() if k not in HISTORY_EXTRA}
    if 'health' in result:
        result['health'] = {k: v for k, v in result['health'].items() if k != 'detail'}
    if 'readings' in result:
        result['readings'] = {k: v for k, v in result['readings'].items() if k != 'health'}
    if 'specs' in result.get('readings', {}):
        result['readings']['specs'] = {k: v for k, v in result['readings']['specs'].items() if k != 'hardware'}
    return result


def _list(value):
    if not isinstance(value, list) or len(value) > 8:
        raise ProtocolError('Invalid reading list')
    return value


def validate_specs(value):
    strings = ('manufacturer', 'model', 'cpu_name', 'os_name', 'os_version', 'hostname')
    integers = ('cpu_cores', 'cpu_threads', 'ram_bytes')
    _object(value, (*strings, *integers, 'gpu_names', 'disks', 'battery_design_wh', 'mac_addresses', 'hardware'))
    for key, item in value.items():
        if key == 'hardware':
            from lifetime_system import validate
            try:
                if not isinstance(item, dict):
                    raise ValueError('Invalid hardware inventory')
                validate(item)
            except ValueError as error:
                raise ProtocolError(str(error)) from None
        elif key in strings:
            if item is not None:
                _text(item)
        elif key in integers:
            if item is not None:
                number(item, True)
                if key != 'ram_bytes' and item > 65536:
                    raise ProtocolError('Invalid processor count')
        elif key == 'battery_design_wh':
            if item is not None and number(item) > 100000:
                raise ProtocolError('Invalid battery capacity')
        elif key in ('gpu_names', 'mac_addresses'):
            for name in _list(item):
                _text(name)
                if key == 'mac_addresses' and not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', name):
                    raise ProtocolError('Invalid MAC address')
        elif key == 'disks':
            for disk in _list(item):
                _object(disk, ('name', 'total_bytes', 'free_bytes'), ('name', 'total_bytes', 'free_bytes'))
                _text(disk['name'])
                for field in ('total_bytes', 'free_bytes'):
                    if disk[field] is not None:
                        number(disk[field], True)
                if disk['total_bytes'] is not None and disk['free_bytes'] is not None and disk['free_bytes'] > disk['total_bytes']:
                    raise ProtocolError('Invalid disk capacity')
    return value


def validate_performance(value):
    percentages = ('cpu_percent', 'battery_percent', 'battery_health_percent', 'wifi_signal_percent')
    _object(value, (*percentages, 'ram_used_bytes', 'disk_free_bytes', 'charging', 'power_watts', 'network_kind', 'temperature_c'))
    for key, item in value.items():
        if item is None:
            continue
        if key in percentages:
            if number(item) > 100:
                raise ProtocolError('Invalid percentage')
        elif key in ('ram_used_bytes', 'disk_free_bytes'):
            number(item, True)
        elif key == 'charging':
            if type(item) is not bool:
                raise ProtocolError('Invalid charging flag')
        elif key == 'network_kind':
            if item not in ('wifi', 'ethernet', 'none'):
                raise ProtocolError('Invalid network kind')
        elif key == 'temperature_c':
            if type(item) not in (int, float) or not -100 <= item <= 250 or not math.isfinite(item):
                raise ProtocolError('Invalid temperature')
        elif number(item) > 1000000:
            raise ProtocolError('Invalid power reading')
    return value


def validate_health(value):
    fields = ('version', 'observed_at', 'shutdowns_30d', 'crashes_30d', 'battery_health', 'cycle_count', 'last_crash')
    _object(value, (*fields, 'detail'), fields)
    if 'detail' in value:
        validate_health_detail(value['detail'])
    if type(value['version']) is not int or value['version'] != 1:
        raise ProtocolError('Unsupported health summary version')
    observed = timestamp(value['observed_at'])
    for field, maximum in (('shutdowns_30d', 10000), ('crashes_30d', 10000), ('cycle_count', 100000), ('battery_health', 1000)):
        if value[field] is not None and number(value[field], integer=field != 'battery_health') > maximum:
            raise ProtocolError('Health reading outside supported range')
    if value['last_crash'] is not None:
        crash = timestamp(value['last_crash'])
        if not observed-dt.timedelta(days=30) <= crash <= observed or not value['crashes_30d']:
            raise ProtocolError('Invalid last crash date')
    return value


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError('Invalid JSON value') from None


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ProtocolError('Message too large')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError('Duplicate JSON field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError('Nonfinite JSON number')))
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError('Invalid JSON message') from None
