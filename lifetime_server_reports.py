"""Read-only server report models, rendered by the existing local PDF/CSV writers."""
from copy import deepcopy

from lifetime_identity import FIELDS, LABELS
from lifetime_reports import build_report, tr, LABELS as REPORT_LABELS
from lifetime_settings import SYSTEM_FIELDS


def device_report(device, start=None, end=None, selected_identity=(), system_fields=(), language='en'):
    """Use dated client snapshots only with current permission; never report host OS."""
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


def fleet_report(devices, start=None, end=None, selected_identity=(), system_fields=(), language='en'):
    """Sum one contribution per UUID. Unknown devices remain explicitly missing."""
    seen = set()
    reports = []
    for device in devices:
        identifier = device['device_id']
        if identifier in seen:
            raise ValueError('Duplicate device in fleet report')
        seen.add(identifier)
        reports.append(device_report(device, start, end, selected_identity, system_fields, language))
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
    return report
