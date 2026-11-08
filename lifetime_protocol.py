"""Versioned company reporting contract; no I/O or platform dependencies."""
import datetime as dt
import json
import math
import uuid
from lifetime_identity import FIELDS as IDENTITY_FIELDS, normalize as normalize_identity

VERSION = 1
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
    _object(message, ('version', 'device_id', 'policy_version', 'policy', 'records', 'history', 'readings'),
            ('version', 'device_id', 'policy_version', 'policy', 'records'))
    if type(message['version']) is not int or message['version'] != VERSION:
        raise ProtocolError('Unsupported protocol version')
    identifier(message['device_id'])
    if number(message['policy_version'], True) < 1:
        raise ProtocolError('Invalid policy version')
    policy = _object(message['policy'], ('history', 'resources'), ('history', 'resources'))
    if any(type(v) is not bool for v in policy.values()):
        raise ProtocolError('Policy values must be boolean')
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
        history = _object(message['history'], ('daily', 'imported_daily', 'first_seen', 'first_seen_source', 'recording_started', 'daily_estimated'))
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
        readings = _object(message['readings'], ('observed_at', 'system', 'release', 'machine', 'python_version'), ('observed_at',))
        timestamp(readings['observed_at'])
        if any(not isinstance(v, str) or len(v) > 512 for v in readings.values()):
            raise ProtocolError('Invalid system reading')
    if len(canonical(message)) > MAX_BYTES:
        raise ProtocolError('Message too large')
    return message


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
