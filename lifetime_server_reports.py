"""Read-only server report models, rendered by the existing local PDF/CSV writers.

Awake time (the computer's own ledger) and availability (the server's monitoring of whether it was reporting during
its expected hours) are reported side by side but in separate sections: they measure different things.
"""
from copy import deepcopy
import datetime as dt

import lifetime_availability as availability
import lifetime_insights as insights
import lifetime_uptime
from lifetime_identity import FIELDS, LABELS
from lifetime_protocol import CLIENT_VERSION
from lifetime_reports import build_report, tr, LABELS as REPORT_LABELS
from lifetime_settings import SYSTEM_FIELDS

RULE_TEXT = {'offline': 'Computer offline', 'no_report': 'No report received', 'disk_low': 'Disk almost full',
             'cpu_high': 'Processor busy', 'memory_high': 'Memory almost full', 'battery_worn': 'Battery worn',
             'temperature_high': 'Running hot', 'crashes': 'Repeated crashes', 'agent_outdated': 'Lifetime version too old',
             'check_down': 'Network check failing'}
STATUS_TEXT = {'resolved': 'Resolved', 'open': 'Needs attention', 'acknowledged': 'Acknowledged',
               'suppressed': 'Paused by maintenance'}
THRESHOLDS = dict(battery_worn=60, disk_low=10, crashes=3)      # the alert rules' defaults
SOURCE = 'Lifetime Server monitoring and the latest snapshot each computer permits'
SEPARATE = ('Awake time and availability are different measures: awake time comes from the computer’s own record, '
            'availability from the server observing whether it was reporting during its expected hours.')
AVAILABILITY_NOTES = [
    SEPARATE,
    'Availability counts only expected-online hours. Unknown time (server down, no report yet, before monitoring) is never counted as up or down; maintenance windows are excluded.',
    'Day labels for availability use each computer’s calendar: the time zone of its expected-online schedule.',
    'Replacement candidates are suggestions from the stated readings and the alert thresholds, not a diagnosis.',
]


def _percent(value):
    return None if value is None else f'{value:g} %'


def _utc(stamp):
    if not stamp:
        return None
    return dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def availability_text(result, language):
    """The headline figure, or why there is none."""
    if result['status'] == 'not-scheduled':
        return tr('Not expected online in this period', language)
    if result['status'] == 'insufficient':
        observed = _percent(result['availability']) or tr('Not available', language)
        return tr('Insufficient evidence (observed {value}, coverage {coverage})', language).format(
            value=observed, coverage=_percent(result['coverage']) or '0 %')
    return _percent(result['availability'])


def availability_rows(result, language):
    yield 'availability', 'Observed availability', availability_text(result, language)
    yield 'availability', 'Monitoring coverage', _percent(result['coverage'])
    yield 'availability', 'Coverage floor', _percent(result['coverage_floor'])
    for key, title in (('good_s', 'Observed online (seconds)'), ('bad_s', 'Observed offline (seconds)'),
                       ('unknown_s', 'Unknown, not observed (seconds)'), ('excluded_s', 'Maintenance, excluded (seconds)'),
                       ('scheduled_s', 'Expected online (seconds)')):
        yield 'availability', title, result[key]


def health_rows(device, thresholds=None, language='en'):
    """(rows, reasons): the health summary and why the computer may need replacing, from shared readings only."""
    limits = dict(THRESHOLDS, **{key: value for key, value in (thresholds or {}).items() if value is not None})
    policy = device.get('policy') or {}
    health = device.get('health') if policy.get('health') else None
    readings = (device.get('readings') or {}) if policy.get('resources') else {}
    specs, performance = readings.get('specs') or {}, readings.get('performance') or {}
    disks = specs.get('disks') or []
    total, free = (disks[0].get('total_bytes') if disks else None), performance.get('disk_free_bytes')
    disk = round(min(100, 100 * free / total)) if total and free is not None else None
    battery = (health or {}).get('battery_health')
    if battery is None:
        battery = performance.get('battery_health_percent')
    crashes = (health or {}).get('crashes_30d')
    rows = [('health', 'Battery health (%)', None if battery is None else round(battery)),
            ('health', 'Battery charge cycles', (health or {}).get('cycle_count')),
            ('health', 'Crashes in the last 30 days', crashes),
            ('health', 'Unexpected shutdowns in the last 30 days', (health or {}).get('shutdowns_30d')),
            ('health', 'System disk free (%)', disk),
            ('health', 'Health reading time', (health or {}).get('observed_at'))]
    reasons = []
    if battery is not None and battery < limits['battery_worn']:
        reasons.append(tr('Battery health {value} % is below {limit} %', language).format(value=round(battery), limit=limits['battery_worn']))
    if disk is not None and disk < limits['disk_low']:
        reasons.append(tr('System disk has less than {limit} % free', language).format(limit=limits['disk_low']))
    if crashes is not None and crashes >= limits['crashes']:
        reasons.append(tr('{value} crashes in 30 days (limit {limit})', language).format(value=crashes, limit=limits['crashes']))
    if not health and not readings:
        rows.append(('health', 'Health readings', tr('Not shared by this computer', language)))
    return rows, reasons


def power_method(power, language):
    text = tr(insights.SOURCE_TEXT[power['source']], language)
    if power['method'] == 'measured':
        text += ' ' + tr('({count} readings)', language).format(count=power['readings'])
    return text


def energy_rows(awake_seconds, power, config, language, section='energy'):
    """Power, how it was obtained, and the energy and cost of the report's awake time."""
    if power is None:
        yield section, 'Energy in the period (kWh)', tr('Not available', language)
        return
    yield section, 'Power draw (W)', power['watts']
    yield section, 'Power method', power_method(power, language)
    if awake_seconds is None:
        yield section, 'Energy in the period (kWh)', tr('Not available', language)
        return
    value = insights.kwh(awake_seconds, power['watts'])
    yield section, 'Energy in the period (kWh)', round(value, 3)
    yield section, 'Cost in the period', insights.money(value * config['price'], config['currency'])


def add_energy(report, items, config, language):
    """Energy section for one computer or the sum of several: ``items`` [(awake seconds or None, power or None)]."""
    rows = [('energy', 'Electricity price', tr('{price} per kWh', language).format(
        price=insights.money(config['price'], config['currency'])))]
    if len(items) == 1:
        rows.extend(energy_rows(*items[0], config, language))
    else:
        known = [(seconds, power) for seconds, power in items if seconds is not None and power is not None]
        total = sum(insights.kwh(seconds, power['watts']) for seconds, power in known)
        rows += [('energy', 'Computers with energy figures', len(known)),
                 ('energy', 'Measured on battery', sum(power['method'] == 'measured' for _, power in known)),
                 ('energy', 'Estimated from typical power', sum(power['method'] == 'estimated' for _, power in known)),
                 ('energy', 'Without shared awake history (unknown)', len(items) - len(known)),
                 ('energy', 'Energy in the period (kWh)', round(total, 3)),
                 ('energy', 'Cost in the period', insights.money(total * config['price'], config['currency']))]
    report['extra_rows'] = [*report.get('extra_rows', ()), *rows]
    report['notes'] = [*report['notes'], insights.ENERGY_NOTE]


def after_rows(afters, config, language):
    """Awake time outside the hours over the last 7 days for one computer or the sum of several (``afters``: their
    lifetime_insights after-hours results, None where unknown)."""
    known = [after for after in afters if after]
    if not known:
        return [('energy', 'Awake after hours, last 7 days (hours)', tr('Not available', language))]
    total = sum(after['week']['kwh'] for after in known)
    rows = [('energy', 'Awake after hours, last 7 days (hours)', round(sum(after['week']['hours'] for after in known), 2)),
            ('energy', 'Energy after hours, last 7 days (kWh)', round(total, 3)),
            ('energy', 'Cost after hours, last 7 days', insights.money(total * config['price'], config['currency']))]
    if len(afters) == 1:
        hours = known[0]['hours']
        rows.append(('energy', 'Hours used', tr('its own business hours' if known[0]['source'] == 'schedule' else 'office hours',
                                                 language) + f" {hours['start']}–{hours['end']}"))
    else:
        rows.append(('energy', 'Without hourly history (unknown)', len(afters) - len(known)))
    return rows


FORECAST_TITLE = {'battery': 'Battery forecast', 'disk': 'System disk forecast', 'disk-health': 'Disk health'}


def _say(pair, language):
    key, values = pair
    return tr(key, language).format(**values)


def forecast_rows(item, language):
    """One computer's replacement forecasts, its age and awake hours (lifetime_insights)."""
    rows = [('replacement', FORECAST_TITLE[part['part']], _say(insights.when_text(part), language))
            for part in item.get('replace') or []]
    age = item.get('age') or {}
    rows.append(('replacement', 'Age (years)', age.get('years')))
    rows.append(('replacement', 'Awake hours so far', age.get('awake_hours')))
    return rows


PART_NAME = {'stability': 'Stability', 'battery': 'Battery', 'disk': 'System disk free', 'temperature': 'Temperature',
             'agent': 'Lifetime agent'}


def score_rows(item, language):
    """The health score with what it is made of; unknown parts are named and left out of the score."""
    found = item.get('health') or {}
    rows = [('health', 'Health score (0-100)', found.get('score') if found.get('score') is not None else tr('Unknown', language))]
    for key, kind, _ in found.get('factors') or []:
        rows.append(('health', tr('Score part: {part}', language).format(part=tr(PART_NAME[key], language)),
                     tr({'good': 'Good', 'warning': 'Needs a look', 'danger': 'Poor'}.get(kind, 'Unknown'), language)))
    return rows


def plan_rows(plan, config, language):
    rows = []
    for key, title in (('quarter', 'Due now or next quarter'), ('year', 'Due later this year')):
        budget = plan[key + '_budget']
        rows.append(('replacement', title, len(plan[key])))
        rows.extend(('replacement', title, f"{row['name']} · {tr(insights.PART_TEXT[row['part']], language)} · "
                                           f"{_say(insights.when_text(row), language)}") for row in plan[key])
        rows.append(('replacement', 'Budget (parts with a price)', tr('{total} ({unpriced} without a price)', language).format(
            total=insights.money(budget['total'], config['currency']), unpriced=budget['unpriced'])))
    return rows


FORECAST_NOTE = ('Replacement forecasts are straight lines through each computer’s own readings (battery health, '
                 'system disk free space). A date is given only with at least 4 readings over 30 days; otherwise the '
                 'report says not enough data. They are estimates, not a diagnosis.')


def _incident_line(item, language):
    end = _utc(item.get('resolved_at')) or tr('ongoing', language)
    return f"{tr(RULE_TEXT.get(item['rule'], item['rule']), language)} · {_utc(item['started_at'])} – {end} · {tr(STATUS_TEXT.get(item['state'], item['state']), language)}"


def add_monitoring(report, device, result, thresholds=None, language='en'):
    """Availability, incidents, health and replacement sections for one computer, plus the page footer."""
    report['version'] = CLIENT_VERSION
    rows = []
    if result:
        rows.append(('availability', 'Availability period', f"{result['start']} / {result['end']}"))
        rows.append(('availability', 'Calendar time zone', f"{result['timezone']} ({tr(ZONE_SOURCE[result['zone_source']], language)})"))
        rows.extend(availability_rows(result, language))
        rows.append(('incidents', 'Incidents in the period', len(result.get('incidents') or [])))
        rows.extend(('incidents', 'Incident', _incident_line(item, language)) for item in result.get('incidents') or [])
    else:
        rows.append(('availability', 'Observed availability', tr('Not available', language)))
    health, reasons = health_rows(device, thresholds, language)
    rows.extend(health)
    rows.append(('replacement', 'Replacement candidate', tr('Yes' if reasons else 'No', language)))
    rows.extend(('replacement', 'Reason', reason) for reason in reasons)
    report['extra_rows'] = [*report.get('extra_rows', ()), *rows]
    report['notes'] = [*report['notes'], *AVAILABILITY_NOTES]
    zone = result['timezone'] if result else tr('Not available', language)
    report['extra_rows'].insert(1, ('report', 'Data source', tr(SOURCE, language)))
    report['extra_rows'].insert(2, ('report', 'Calendar time zone', zone))
    report['footer'] = footer(zone, language)
    return reasons


ZONE_SOURCE = {'device': 'from this computer’s schedule', 'group': 'from its group’s schedule',
               'default': 'from the default schedule'}


def footer(zone, language):
    return tr('Lifetime Server {version} · Time zone: {zone} · Source: {source}', language).format(
        version=CLIENT_VERSION, zone=zone, source=tr(SOURCE, language))


UPTIME_NOTE = ('Uptime & downtime: awake time comes from each computer’s record; asleep, off and unknown time and the '
               'restarts and stops only from computers with Lifetime 1.3 or later that share their history (older ones '
               'count as unknown, never as zero). Each figure is shown with the previous period of the same length.')
UPTIME_NO_COMPARISON = 'Uptime & downtime: comparison with the previous period needs a chosen period, not all history.'


def _history(device):
    return (device.get('history') or {}) if (device.get('policy') or {}).get('history') else {}


def _uptime_figures(devices, first, last, measured, as_of):
    """Sums for a computer, a group or the fleet over first..last: awake, asleep, off and unknown seconds, the counts,
    how many computers describe their off time, and availability with outages from the monitoring results."""
    now = dt.datetime.fromisoformat(as_of).astimezone() if as_of else None
    totals = [lifetime_uptime.total(lifetime_uptime.day_breakdown(_history(device), first, last, now)) for device in devices]
    result = {}
    for key in (*lifetime_uptime.PARTS, *lifetime_uptime.COUNTS):
        values = [item[key] for item in totals if item[key] is not None]
        result[key] = sum(values) if values else None
    result['reporting'] = sum(bool(item['reported']) for item in totals)
    found = [item for item in measured or () if item]
    result['availability'] = availability.combine(found, found[0]['coverage_floor']) if found else None
    result['outages'] = sum(item.get('outages') or 0 for item in found) if found else None
    result['longest_outage_s'] = max((item.get('longest_outage_s') or 0 for item in found), default=None) if found else None
    return result


def _hours(seconds):
    return None if seconds is None else f'{seconds / 3600:.1f}'


def uptime_rows(devices, start, end, current, previous, as_of, language):
    """The "Uptime & downtime" section: the period's figures, each with the previous period of the same length.
    ``current``/``previous``: the admin availability results of these computers for each period (None: unknown)."""
    if start:
        first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    else:       # all available history: from the first dated record, without a comparison
        dated = [day for device in devices for field in ('daily', 'downtime_daily') for day in _history(device).get(field) or {}]
        last = (dt.datetime.fromisoformat(as_of).astimezone().date() if as_of else dt.date.today())
        first = min(min(dated, default=last.isoformat()), last.isoformat())
        first = max(dt.date.fromisoformat(first), last - dt.timedelta(days=lifetime_uptime.MAX_DAYS - 1))
    now = _uptime_figures(devices, first, last, current, as_of)
    before = _uptime_figures(devices, *lifetime_uptime.previous_period(first, last), previous, as_of) if start else None
    earlier = lifetime_uptime.previous_period(first, last)
    heading = (tr('{first} to {last}, compared with {previous_first} to {previous_last}', language).format(
        first=first.isoformat(), last=last.isoformat(), previous_first=earlier[0].isoformat(),
        previous_last=earlier[1].isoformat()) if start else
        tr('{first} to {last}', language).format(first=first.isoformat(), last=last.isoformat()))
    rows = [('uptime', 'Uptime & downtime', heading)]

    def value(current_value, previous_value):
        shown = tr('Not available', language) if current_value is None else str(current_value)
        if before is None:
            return shown
        return tr('{current} (previous period: {previous})', language).format(
            current=shown, previous=tr('Not available', language) if previous_value is None else previous_value)

    def percent(result):
        return None if result is None else availability_text(result, language)
    for key, title in (('awake_s', 'Awake (hours)'), ('asleep_s', 'Asleep (hours)'), ('off_s', 'Off (hours)'),
                       ('unknown_s', 'Unknown (hours)')):
        rows.append(('uptime', title, value(_hours(now[key]), before and _hours(before[key]))))
    for key, title in (('restarts', 'Restarts'), ('shutdowns', 'Clean shutdowns'), ('unexpected_stops', 'Unexpected stops')):
        rows.append(('uptime', title, value(now[key], before and before[key])))
    rows.append(('uptime', 'Computers that report sleep and off time', value(
        f"{now['reporting']} / {len(devices)}", before and f"{before['reporting']} / {len(devices)}")))
    rows.append(('uptime', 'Availability in expected hours', value(percent(now['availability']),
                                                                     before and percent(before['availability']))))
    rows.append(('uptime', 'Outages in expected hours', value(now['outages'], before and before['outages'])))
    rows.append(('uptime', 'Longest outage (hours)', value(_hours(now['longest_outage_s']),
                                                             before and _hours(before['longest_outage_s']))))
    return rows


def add_uptime(report, devices, start, end, current, previous, as_of, language, per_computer=False):
    rows = uptime_rows(devices, start, end, current, previous, as_of, language)
    if per_computer and len(devices) > 1:
        now = {item['device_id']: item for item in current or ()}
        old = {item['device_id']: item for item in previous or ()}
        for device in devices:
            name = (device.get('identity') or {}).get('display_name') or device['device_id']
            figures = uptime_rows([device], start, end, [now.get(device['device_id'])], [old.get(device['device_id'])],
                                  as_of, language)
            rows.append(('uptime-by-computer', 'Computer', name))
            rows.extend(('uptime-by-computer', *row[1:]) for row in figures[1:] if row[1] in ('Awake (hours)', 'Unexpected stops',
                                                                   'Availability in expected hours'))
    report['extra_rows'] = [*report.get('extra_rows', ()), *rows]
    report['notes'] = [*report['notes'], UPTIME_NOTE if start else UPTIME_NO_COMPARISON]


def device_report(device, start=None, end=None, selected_identity=(), system_fields=(), language='en',
                  availability=None, thresholds=None, insights=None, uptime=None):
    """Use dated client snapshots only with current permission; never report host OS. ``availability``: this
    computer's entry from the admin availability call (None leaves the report as awake time only). ``uptime``:
    dict(current=[availability results], previous=[...], as_of) adds the "Uptime & downtime" section."""
    report = _device_report(device, start, end, selected_identity, system_fields, language)
    if uptime is not None:
        add_uptime(report, [device], start, end, uptime['current'], uptime['previous'], uptime['as_of'], language)
    if availability is not None or thresholds is not None:
        add_monitoring(report, device, availability, thresholds, language)
        if availability and availability.get('daily'):
            report['extra_rows'].extend(
                ('availability', tr('Day {date}', language).format(date=day['date']),
                 tr('online {good} s · offline {bad} s · unknown {unknown} s · maintenance {excluded} s', language).format(
                     good=day['good_s'], bad=day['bad_s'], unknown=day['unknown_s'], excluded=day['excluded_s']))
                for day in availability['daily'])
    if insights is not None:
        item = insights['computers'].get(device['device_id'])
        add_energy(report, [(report['stats']['awake_seconds'] if item and item['energy'] is not None else None,
                             item and item['power'])], insights['settings'], language)
        report['extra_rows'].extend(after_rows([item and item.get('after')], insights['settings'], language))
        if item:
            report['extra_rows'].extend(forecast_rows(item, language))
            report['extra_rows'].extend(score_rows(item, language))
            report['notes'].append(FORECAST_NOTE)
    return report


def _device_report(device, start=None, end=None, selected_identity=(), system_fields=(), language='en'):
    policy = device.get('policy') or {}
    history = deepcopy(device.get('history') or {}) if policy.get('history') else {}
    totals = device.get('totals')
    ledger = dict(total_seconds=(totals['measured_seconds'] + totals['imported_seconds']) if totals else 0,
                  baseline_seconds=totals['baseline_seconds'] if totals else 0,
                  last_update=device.get('last_observed_at') if totals else None,
                  recording_started=history.get('recording_started'),
                  first_seen=history.get('first_seen'), first_seen_source=history.get('first_seen_source', 'unknown'),
                  daily=history.get('daily', {}), sessions=[],
                  imported=dict(seconds=totals['imported_seconds'] if totals else 0,
                                daily=history.get('imported_daily', {})))
    settings = dict(completed=True, history=bool(policy.get('history')), resources=False,
                    identity=deepcopy(device.get('identity') or {}))
    if set(selected_identity) - set(FIELDS) or set(system_fields) - set(SYSTEM_FIELDS):
        raise ValueError('Unknown report metadata field')
    report = build_report(ledger, settings, start, end, language=language, selected_identity=selected_identity)
    # A report describes the client, not the machine producing the PDF.
    readings = device.get('readings') or {} if policy.get('resources') else {}
    report['system'] = {key: readings.get(key) for key in system_fields if readings.get(key) is not None}
    report['stats'].update(session_starts=None, longest_session_seconds=None, boots=None)
    if not totals and start is None:
        report['stats'].update(awake_seconds=None, measured_seconds=None, imported_seconds=None, baseline_seconds=None)
    report['notes'] = [
        'Awake time means the computer was running and awake. It does not measure human activity, productivity, component health or remaining lifespan.',
        'Server reports use the latest permitted client snapshot. Missing dates are unavailable, not evidence of zero usage.',
        'The server receipt time and the client observation time are different. A stopped report stream does not prove shutdown or sleep.',
        'Daily labels use the recording computer’s saved local dates. Clock and timezone changes can affect calendar attribution.',
        'Session statistics were not transmitted and are not available.',
    ]
    if start:
        report['notes'].append('Manual baseline hours have no recorded dates and are excluded from period totals.')
    if not policy.get('history'):
        report['notes'].append('History collection is disabled in Settings. Dated history and retained session records are excluded, including previously stored records.')
    if device.get('retained_before'):
        report['notes'].append('Server retention removed earlier detail; the latest cumulative snapshot can describe a longer period than retained daily rows.')
    if history.get('daily_estimated'):
        report['notes'].append('Daily allocation includes estimates; exact historical awake intervals cannot be recovered.')
    if len(report['daily']) > 90:
        report['notes'].append('Only the first 90 daily rows are shown in the preview and PDF. CSV contains every selected retained daily row.')
    report['coverage']['status'] = 'unknown'
    report['extra_rows'] = [('report', 'Report scope', tr('One server device', language)),
                            ('coverage', 'Client observation time', device.get('last_observed_at')),
                            ('coverage', 'Server receipt time', device.get('last_received_at'))]
    if report['system']:
        report['extra_rows'].append(('system', 'System reading time', readings.get('observed_at')))
    return report


def fleet_report(devices, start=None, end=None, selected_identity=(), system_fields=(), language='en',
                 availability=None, thresholds=None, group=None, insights=None, uptime=None):
    """Sum one contribution per UUID. Unknown devices remain explicitly missing. ``availability``: the admin
    availability results (a list) for these computers; ``group`` names a group report; ``uptime`` as for
    device_report, summed over these computers with a few figures per computer."""
    seen = set()
    reports = []
    for device in devices:
        identifier = device['device_id']
        if identifier in seen:
            raise ValueError('Duplicate device in fleet report')
        seen.add(identifier)
        reports.append(_device_report(device, start, end, selected_identity, system_fields, language))
    empty = dict(device_id='fleet', policy={}, identity={}, totals=None)
    report = device_report(empty, start, end, (), (), language)
    for key in ('awake_seconds', 'measured_seconds', 'imported_seconds', 'baseline_seconds'):
        values = [item['stats'][key] for item in reports if item['stats'][key] is not None]
        report['stats'][key] = sum(values) if values else None
    daily = {}
    for item in reports:
        for day, seconds in item['daily']:
            daily[day] = daily.get(day, 0) + seconds
    report['daily'] = sorted(daily.items())
    report['first_record'] = report['daily'][0][0] if daily else None
    report['last_record'] = report['daily'][-1][0] if daily else None
    report['coverage'].update(first_record=report['first_record'], last_record=report['last_record'])
    known = sum(item['stats']['awake_seconds'] is not None for item in reports)
    measured, imported = report['stats']['measured_seconds'], report['stats']['imported_seconds']
    report['coverage']['source'] = 'combined' if measured and imported else 'reconstructed' if imported else 'tracked' if measured is not None else 'unknown'
    report['extra_rows'] = [('report', 'Report scope', tr('Fleet summary', language)),
                            ('statistics', 'Selected devices', len(reports)),
                            ('statistics', 'Devices with available totals', known),
                            ('statistics', 'Devices with unavailable totals', len(reports) - known)]
    for index, item in enumerate(reports, 1):
        report['extra_rows'].append(('fleet', 'Device', index))
        report['extra_rows'].append(('fleet', 'Device awake seconds', item['stats']['awake_seconds']))
        for field, title in (('measured_seconds', 'Directly measured awake time (seconds)'),
                             ('imported_seconds', 'Reconstructed OS history (seconds)'),
                             ('baseline_seconds', 'Manual baseline (seconds, not dated)')):
            report['extra_rows'].append(('fleet', title, item['stats'][field]))
        for section, title, value in item.get('extra_rows', ()):
            if title in ('Client observation time', 'Server receipt time', 'System reading time'):
                report['extra_rows'].append(('fleet', title, value))
        for key, value in item['identity'].items():
            report['extra_rows'].append(('fleet', LABELS[key], value))
        for key, value in item['system'].items():
            report['extra_rows'].append(('fleet', REPORT_LABELS[key], value))
    report['notes'] = [
        'Awake time means the computer was running and awake. It does not measure human activity, productivity, component health or remaining lifespan.',
        'Fleet totals sum one latest snapshot per selected device, never overlapping cumulative snapshots. Devices without available totals are excluded from the known subtotal.',
        'Fleet daily rows sum matching device-local date labels; different timezones do not describe one shared UTC day. Missing contributions remain unavailable.',
        'Coverage is unknown. Summing devices does not establish continuous recording, productivity or hardware condition.',
    ]
    if start:
        report['notes'].append('Manual baseline hours have no recorded dates and are excluded from period totals.')
    if len(report['daily']) > 90:
        report['notes'].append('Only the first 90 daily rows are shown in the preview and PDF. CSV contains every selected retained daily row.')
    if group is not None:
        report['extra_rows'][0] = ('report', 'Report scope', tr('Group: {name}', language).format(name=group or tr('No group', language)))
    if insights is not None:
        _fleet_energy(report, devices, reports, insights, language)
    if uptime is not None:
        add_uptime(report, devices, start, end, uptime['current'], uptime['previous'], uptime['as_of'], language,
                   per_computer=True)
    if availability is None and thresholds is None:
        return report
    _fleet_monitoring(report, devices, reports, availability or [], thresholds, language)
    return report


def _fleet_energy(report, devices, reports, results, language):
    """Each computer's power and energy after its awake-time rows, and the sum in an Energy section."""
    items, position = [], {}
    for index, (device, item) in enumerate(zip(devices, reports), 1):
        result = results['computers'].get(device['device_id'])
        seconds = item['stats']['awake_seconds'] if result and result['energy'] is not None else None
        items.append((seconds, result and result['power']))
        position[index] = list(energy_rows(seconds, result and result['power'], results['settings'], language, 'fleet'))
    merged, index = [], None
    for row in report['extra_rows']:
        if row[:2] == ('fleet', 'Device'):
            merged.extend(position.get(index, []))
            index = row[2]
        merged.append(row)
    merged.extend(position.get(index, []))
    report['extra_rows'] = merged
    add_energy(report, items, results['settings'], language)
    report['extra_rows'].extend(after_rows([(results['computers'].get(device['device_id']) or {}).get('after')
                                            for device in devices], results['settings'], language))
    chosen = {device['device_id'] for device in devices}
    found = [results['computers'][i] for i in chosen if i in results['computers']]
    plan = insights.plan(found, results['settings'], dt.date.fromisoformat(results['today'])) if results.get('today') else None
    if found and all('health' in c for c in found):
        score = insights.health_summary(found)
        report['extra_rows'] += [('health', 'Average health score (0-100)', score['score'] if score['score'] is not None
                                  else tr('Unknown', language)),
                                 ('health', 'Computers with a score', score['known']),
                                 ('health', 'Computers without a reading (left out)', score['unknown'])]
    if plan:
        report['extra_rows'].extend(plan_rows(plan, results['settings'], language))
        report['notes'].append(FORECAST_NOTE)


def _fleet_monitoring(report, devices, reports, results, thresholds, language):
    """Each computer's availability beside its awake time, the group or fleet sum, and replacement candidates."""
    report['version'] = CLIENT_VERSION
    by_id = {item['device_id']: item for item in results}
    rows, candidates, measured = [], [], []
    position = {}
    for index, (device, item) in enumerate(zip(devices, reports), 1):
        result = by_id.get(device['device_id'])
        if result:
            measured.append(result)
        _, reasons = health_rows(device, thresholds, language)
        name = (device.get('identity') or {}).get('display_name') or device['device_id']
        if reasons:
            candidates.append((name, reasons))
        position[index] = [('fleet', 'Observed availability', availability_text(result, language) if result else tr('Not available', language)),
                           ('fleet', 'Monitoring coverage', _percent(result['coverage']) if result else None),
                           ('fleet', 'Incidents in the period', len(result.get('incidents') or []) if result else None),
                           ('fleet', 'Replacement candidate', tr('Yes' if reasons else 'No', language))]
    if measured:
        total = availability.combine(measured, measured[0]['coverage_floor'])
        rows.extend(availability_rows(total, language))
        rows.append(('availability', 'Computers with availability data', len(measured)))
        rows.append(('incidents', 'Incidents in the period', sum(len(item.get('incidents') or []) for item in measured)))
    else:
        rows.append(('availability', 'Observed availability', tr('Not available', language)))
    rows.append(('replacement', 'Replacement candidates', len(candidates)))
    for name, reasons in candidates:
        rows.append(('replacement', 'Computer', name))
        rows.extend(('replacement', 'Reason', reason) for reason in reasons)
    # Interleave each computer's availability right after its awake-time rows ("Device", index, ... ).
    merged, index = [], None
    for row in report['extra_rows']:
        if row[:2] == ('fleet', 'Device'):
            merged.extend(position.get(index, []))
            index = row[2]
        merged.append(row)
    merged.extend(position.get(index, []))
    zones = sorted({item['timezone'] for item in measured})
    zone = zones[0] if len(zones) == 1 else tr('each computer’s own calendar ({zones})', language).format(zones=', '.join(zones) or '—')
    merged.insert(1, ('report', 'Data source', tr(SOURCE, language)))
    merged.insert(2, ('report', 'Calendar time zone', zone))
    report['extra_rows'] = merged + rows
    report['notes'] = [*report['notes'], *AVAILABILITY_NOTES]
    report['footer'] = footer(zone, language)
