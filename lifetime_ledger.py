"""Strict saved-ledger validation and explicit, conservative history salvage.

Unknown extensions and legacy optional omissions survive. Accounting and boot
boundaries are never repaired. Import markers are accounting records: losing one
could double count a later import, so damage to one always prevents salvage.
"""
import copy
import datetime as dt
import math

NUMBER_FIELDS = ('total_seconds', 'baseline_seconds', 'current_session_seconds',
                 'longest_session_seconds', 'boot_accounted_seconds', 'unattributed_seconds',
                 'awake_clock_high_water')
DATE_FIELDS = ('first_seen', 'tracking_started', 'history_started')
TEXT_FIELDS = ('boot_id', 'boot_credited_for')
FIRST_SEEN_SOURCES = frozenset(('os-install-date', 'system-file-date', 'tracking-start',
                                'imported-log', 'unknown'))
CORE = frozenset((*NUMBER_FIELDS, *TEXT_FIELDS, 'boots', 'last_clock', 'imported', 'awake_clock_rollbacks'))


def is_number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def is_date(value):
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except (ValueError, OverflowError):
        return False


def is_datetime(value):
    # fromisoformat also accepts date-only strings; timestamp fields must not.
    try:
        return (isinstance(value, str) and len(value) >= 19 and is_date(value[:10])
                and value[10] in ('T', ' ') and value[13] == ':' and value[16] == ':'
                and dt.datetime.fromisoformat(value.replace('Z', '+00:00')) is not None)
    except (ValueError, OverflowError):
        return False


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def hours_list(value):
    return isinstance(value, list) and len(value) == 24 and all(is_number(v) for v in value)


def valid_session(value):
    return (isinstance(value, dict) and is_datetime(value.get('start'))
            and is_number(value.get('seconds')) and isinstance(value.get('imported', False), bool))


def valid_gap(value):
    return (isinstance(value, dict) and isinstance(value.get('reason'), str)
            and all(value.get(k) is None or is_datetime(value[k]) for k in ('start', 'end')))


def allocation_problems(value):
    if not isinstance(value, dict) or not isinstance(value.get('estimated'), bool):
        return True
    return (any(k in value and not is_number(value[k]) for k in ('seconds', 'start', 'end'))
            or ('source' in value and not isinstance(value['source'], str)))


def problems(ledger):
    """Return (field path, reason) pairs, without migrating or mutating input.

    Only total_seconds is mandatory in a legacy ledger. All present known fields
    are checked, including optional nested import accounting and provenance.
    Daily values above 24h remain readable for the existing repair_daily migration.
    """
    if not isinstance(ledger, dict):
        return [('ledger', 'must be a JSON object')]
    found = []

    def bad(field, reason='invalid value'):
        found.append((field, reason))

    for key in NUMBER_FIELDS:
        if (key in ledger or key == 'total_seconds') and not is_number(ledger.get(key)):
            bad(key, 'must be a finite, non-negative number')
    for key in ('boots', 'clock_note_count', 'daily_repair_version', 'awake_clock_rollbacks'):
        if key in ledger and not is_count(ledger[key]):
            bad(key, 'must be a non-negative integer')
    if 'tracking_started_estimated' in ledger and not isinstance(ledger['tracking_started_estimated'], bool):
        bad('tracking_started_estimated', 'must be boolean')
    if ledger.get('last_clock') is not None and not is_number(ledger['last_clock']):
        bad('last_clock', 'must be null or a finite, non-negative number')
    for key in TEXT_FIELDS:
        if ledger.get(key) is not None and not isinstance(ledger[key], str):
            bad(key, 'must be null or text')
    for key in DATE_FIELDS:
        if ledger.get(key) is not None and not is_date(ledger[key]):
            bad(key, 'must be null or a YYYY-MM-DD date')
    for key in ('last_update', 'recording_started', 'first_sample'):
        if ledger.get(key) is not None and not is_datetime(ledger[key]):
            bad(key, 'must be null or an ISO date-time with seconds')
    if 'first_seen_source' in ledger and (not isinstance(ledger['first_seen_source'], str)
                                        or ledger['first_seen_source'] not in FIRST_SEEN_SOURCES):
        bad('first_seen_source', 'unknown provenance')
    for key in ('daily', 'dated_hourly'):
        if key not in ledger:
            continue
        values = ledger[key]
        check = is_number if key == 'daily' else hours_list
        if not isinstance(values, dict):
            bad(key, 'must be an object keyed by dates')
        else:
            for day, value in values.items():
                if not is_date(day) or not check(value):
                    bad(key, f'invalid entry {day!r}')
    if 'hourly' in ledger and not hours_list(ledger['hourly']):
        bad('hourly', 'must contain 24 finite, non-negative numbers')
    for key, check in (('sessions', valid_session), ('coverage_gaps', valid_gap)):
        if key in ledger:
            if not isinstance(ledger[key], list):
                bad(key, 'must be a list')
            elif any(not check(item) for item in ledger[key]):
                bad(key, 'invalid entry')
    if 'daily_allocation' in ledger and allocation_problems(ledger['daily_allocation']):
        bad('daily_allocation', 'invalid allocation provenance')
    if 'daily_repair' in ledger:
        repair = ledger['daily_repair']
        if (not isinstance(repair, dict) or not isinstance(repair.get('allocations', []), list)
                or any(allocation_problems(a) for a in repair.get('allocations', []))
                or ('estimated' in repair and not isinstance(repair['estimated'], bool))):
            bad('daily_repair', 'invalid repair provenance')
    if 'imported' in ledger:
        imported = ledger['imported']
        if not isinstance(imported, dict):
            bad('imported', 'must be an accounting object')
        else:
            # Validate shared aggregates without recursively accepting imports.
            subset = {k: imported[k] for k in ('daily', 'dated_hourly', 'hourly', 'sessions',
                      'boots', 'longest_session_seconds') if k in imported}
            subset['total_seconds'] = imported.get('seconds', 0)
            for field, reason in problems(subset):
                bad('imported.' + ('seconds' if field == 'total_seconds' else field), reason)
            for key in ('sleep_seconds',):
                if key in imported and not is_number(imported[key]):
                    bad('imported.' + key)
            for key in ('unclean_boots', 'boots_found', 'skipped_boots'):
                if key in imported and not is_count(imported[key]):
                    bad('imported.' + key)
            for key in ('first_day', 'last_day'):
                if imported.get(key) is not None and not is_date(imported[key]):
                    bad('imported.' + key)
            if imported.get('until') is not None and not is_datetime(imported['until']):
                bad('imported.until')
            if 'original' in imported:
                original = imported['original']
                if not isinstance(original, dict):
                    bad('imported.original', 'must be an object')
                else:
                    for key in DATE_FIELDS:
                        if original.get(key) is not None and not is_date(original[key]):
                            bad('imported.original.' + key)
                    if ('first_seen_source' in original and
                            (not isinstance(original['first_seen_source'], str) or
                             original['first_seen_source'] not in FIRST_SEEN_SOURCES)):
                        bad('imported.original.first_seen_source')
                    if ('longest_session_seconds' in original and
                            not is_number(original['longest_session_seconds'])):
                        bad('imported.original.longest_session_seconds')
            for key, total in (('seconds', 'total_seconds'), ('boots', 'boots')):
                if (is_number(imported.get(key, 0)) and is_number(ledger.get(total, 0))
                        and imported.get(key, 0) > ledger.get(total, 0)):
                    bad('imported.' + key, 'contribution exceeds saved total')
    return found


def validate(ledger):
    """Raise ValueError naming damage; otherwise return the input unchanged."""
    found = problems(ledger)
    if found:
        field, reason = found[0]
        raise ValueError(f'Invalid ledger field: {field} ({reason})')
    return ledger


def salvage(value, defaults):
    """Repair derived fields explicitly, returning (ledger, notes).

    Core damage, missing primary total, and damaged import markers require a
    valid backup. Every changed field is listed. Caller must preserve raw bytes
    before replacing a saved file. Inputs are never mutated.
    """
    found = problems(value)
    for field, reason in found:
        if field.split('.')[0] in CORE or field == 'ledger':
            raise ValueError(f'Cannot salvage core ledger field: {field} ({reason})')
    result = copy.deepcopy(defaults)
    result.update(copy.deepcopy(value))
    if 'boot_accounted_seconds' not in value and 'boot_accounted_seconds' in result:
        # Match the normal legacy migration; a zero default could recredit
        # previously counted uptime when a caller next credits the current boot.
        result['boot_accounted_seconds'] = min(value['total_seconds'], value.get('last_clock') or 0.0)
    # Never apply today's OS estimate to a legacy or damaged historical date.
    result['first_seen_source'] = value.get('first_seen_source', 'unknown')
    result['first_seen'] = value.get('first_seen')
    notes = []
    for field, reason in dict(found).items():
        if field in ('daily', 'dated_hourly') and isinstance(value.get(field), dict):
            check = is_number if field == 'daily' else hours_list
            result[field] = {k: v for k, v in result[field].items() if is_date(k) and check(v)}
        elif field in ('sessions', 'coverage_gaps') and isinstance(value.get(field), list):
            check = valid_session if field == 'sessions' else valid_gap
            result[field] = [v for v in result[field] if check(v)]
        elif field in ('first_seen', 'tracking_started', 'history_started', 'last_update',
                       'recording_started', 'first_sample'):
            result[field] = None
        elif field == 'first_seen_source':
            result[field] = 'unknown'
        elif field == 'dated_hourly':
            result[field] = {}
        elif field in defaults:
            result[field] = copy.deepcopy(defaults[field])
        else:
            result.pop(field, None)
        notes.append(f'{field}: {reason}')
    if any(field == 'first_seen' for field, _ in found):
        if result['first_seen_source'] != 'unknown':
            notes.append('first_seen_source: date unavailable; provenance reset to unknown')
        result['first_seen_source'] = 'unknown'
    validate(result)
    return result, notes
