"""Local, read-only report snapshots and atomic PDF/CSV output.

Daily buckets retain the release's existing local-day allocation and its
provenance. Missing history is never interpolated into invented observations.
"""
import csv
import datetime as dt
import html
import math
import os
from pathlib import Path
import platform
import tempfile

from lifetime_settings import IDENTITY_FIELDS, SYSTEM_FIELDS, export_allowed, iso_date
from lifetime_identity import LABELS as IDENTITY_LABELS

VERSION = '1.1.0'
MAX_PREVIEW_DAYS = 90


LABELS = {'display_name': 'Display name', 'asset_tag': 'Asset tag',
          'department': 'Department / location', 'deployment_date': 'Deployment date',
          'system': 'Operating system', 'release': 'OS release',
          'machine': 'Architecture', 'python_version': 'Python version'}
LABELS.update(IDENTITY_LABELS)
SOURCES = {'tracked': 'Directly tracked', 'reconstructed': 'Reconstructed from OS records',
           'combined': 'Tracked and reconstructed', 'unknown': 'Unknown'}
FIRST_SEEN_SOURCES = {'os-install-date': 'OS installation date', 'system-file-date': 'System file timestamp',
                     'tracking-start': 'First observed by Lifetime', 'imported-log': 'Recovered OS log record',
                     'unknown': 'Unknown source'}


def source_totals(ledger, start=None, end=None, history=True):
    """Keep measured, reconstructed and undated baseline counters distinct."""
    imported = ledger.get('imported') or {}
    if start is not None:
        rows = [(day, value) for day, value in ledger.get('daily', {}).items() if history and start <= day <= end]
        if not rows:
            return dict(measured_seconds=None, imported_seconds=None, baseline_seconds=None)
        reconstructed = sum(min(value, imported.get('daily', {}).get(day, 0)) for day, value in rows)
        return dict(measured_seconds=sum(value for _, value in rows)-reconstructed,
                    imported_seconds=reconstructed, baseline_seconds=None)
    available = bool(ledger.get('total_seconds') or ledger.get('last_update') or ledger.get('recording_started') or imported.get('boots'))
    reconstructed = imported.get('seconds', 0) if available else None
    return dict(measured_seconds=max(0, ledger.get('total_seconds', 0)-reconstructed) if available else None,
                imported_seconds=reconstructed, baseline_seconds=ledger.get('baseline_seconds', 0))


def tr(text, language='en'):
    from lifetime_i18n import TABLES
    return TABLES.get(language, TABLES['en']).get(text, text)


def _day(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().date().isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return None


def coverage(ledger, settings=None):
    """Conservative evidence summary. No OS reads and no completeness guesses."""
    if settings is not None and not settings.get('history'):
        ledger = {**ledger, 'daily': {}, 'hourly': [0]*24, 'sessions': [], 'coverage_gaps': [],
                  'imported': {**(ledger.get('imported') or {}), 'daily': {}, 'sessions': [], 'first_day': None, 'last_day': None}}
    imported = ledger.get('imported') or {}
    has_import = bool(imported.get('boots', 0) or imported.get('seconds', 0) or imported.get('daily'))
    has_live = bool(ledger.get('last_update') or ledger.get('recording_started') or
                    ledger.get('total_seconds', 0) > imported.get('seconds', 0) + 1e-6 or
                    any(day not in imported.get('daily', {}) for day in ledger.get('daily', {})))
    source = 'combined' if has_import and has_live else 'reconstructed' if has_import else 'tracked' if has_live else 'unknown'
    keys = sorted(ledger.get('daily', {}))
    earliest = imported.get('first_day') if has_import else None
    firsts = [v for v in (keys[0] if keys else None, earliest, _day(ledger.get('recording_started'))) if v]
    lasts = [v for v in (keys[-1] if keys else None, imported.get('last_day') if has_import else None,
                         _day(ledger.get('last_update'))) if v]
    notes = ['Coverage cannot be proven complete. Missing or rotated logs, restricted permissions and time without tracking may leave unavailable history.',
             'OS dates are observations, not manufacture, purchase or true first-use dates.']
    if not ledger.get('recording_started'):
        notes.append('No proven recording-start timestamp was saved by this installation.')
    if has_import:
        notes.append('OS reconstruction is limited to retained accessible observations. Missing sleep/resume events, unclean ends and Windows Fast Startup can limit accuracy.')
    if ledger.get('coverage_gaps'):
        notes.append('Known awake-clock discontinuities were excluded from counting; their missing duration is unknown.')
    if ledger.get('clock_note_count'):
        notes.append('Clock changes were observed. Calendar attribution may be ambiguous; awake-clock totals were not adjusted using wall time.')
    known_missing = bool(ledger.get('coverage_gaps') or imported.get('unclean_boots'))
    return dict(source=source, status='partial' if known_missing else 'unknown',
                earliest_known_date=ledger.get('first_seen') if settings is None or settings.get('history') or ledger.get('first_seen_source') != 'imported-log' else None,
                first_seen_source=ledger.get('first_seen_source', 'unknown'),
                recording_started=ledger.get('recording_started'), earliest_record=earliest,
                first_record=min(firsts) if firsts else None, last_record=max(lasts) if lasts else None,
                notes=notes, gaps=[dict(g) for g in ledger.get('coverage_gaps', [])])


def build_report(ledger, settings, start=None, end=None, system_fields=None,
                 language='en', selected_identity=None, verification=None):
    """Build an independent snapshot; optional metadata is opt-in on every call."""
    if language not in ('en', 'vi'):
        raise ValueError('Unsupported report language')
    if not export_allowed(settings, 'report'):
        raise ValueError(tr('Report export is disabled by consent settings.', language))
    if (start is None) != (end is None):
        raise ValueError(tr('Choose both start and end dates.', language))
    if start is not None:
        first, last = iso_date(start), iso_date(end)
        if first > last:
            raise ValueError(tr('Start date must not follow end date.', language))
    selected = set(selected_identity or ())
    system_selected = set(system_fields or ())
    if selected - set(IDENTITY_FIELDS) or system_selected - set(SYSTEM_FIELDS):
        raise ValueError('Unknown report metadata field')
    identity = {key: settings.get('identity', {}).get(key) for key in IDENTITY_FIELDS
                if key in selected and export_allowed(settings, 'identity', key) and settings.get('identity', {}).get(key)}
    getters = {key: getattr(platform, key) for key in SYSTEM_FIELDS}
    system = {key: getters[key]() or None for key in SYSTEM_FIELDS
              if key in system_selected and export_allowed(settings, 'system', key)}
    history_allowed = bool(settings.get('completed') and settings.get('history'))
    visible = ledger if history_allowed else {**ledger, 'daily': {}, 'sessions': [], 'coverage_gaps': []}
    info = coverage(visible, settings)
    daily = [(day, seconds) for day, seconds in sorted(visible.get('daily', {}).items())
             if start is None or start <= day <= end]
    if start is not None:
        imported_daily = (ledger.get('imported') or {}).get('daily', {})
        recovered = any(imported_daily.get(day, 0) > 0 for day, _ in daily)
        direct = any(day not in imported_daily or seconds > imported_daily.get(day, 0) + 1e-6 for day, seconds in daily)
        info['source'] = 'combined' if recovered and direct else 'reconstructed' if recovered else 'tracked' if direct else 'unknown'
        info['status'] = 'partial' if daily else 'unknown'
    has_counter = bool(ledger.get('last_update') or ledger.get('recording_started') or
                       ledger.get('total_seconds') or (ledger.get('imported') or {}).get('boots'))
    awake = (ledger.get('total_seconds', 0) if has_counter else None) if start is None else (sum(v for _, v in daily) if daily else None)
    sessions = visible.get('sessions', [])
    chosen_sessions = [s for s in sessions if start is None or start <= (_day(s.get('start')) or '') <= end]
    stats = dict(awake_seconds=awake,
                 baseline_seconds=ledger.get('baseline_seconds', 0) if start is None else None,
                 session_starts=len(chosen_sessions) if sessions and (start is None or daily) else None,
                 longest_session_seconds=ledger.get('longest_session_seconds') if start is None and has_counter else None,
                 boots=ledger.get('boots') if start is None and has_counter else None)
    stats.update(source_totals(ledger, start, end, history=history_allowed))
    notes = list(info['notes']) + [
        'Awake time means the computer was running and awake. It does not measure human activity, productivity, component health or remaining lifespan.',
        'Live intervals are split across local days. Boot-time allocation uses retained sleep/resume evidence when available; otherwise its calendar distribution is estimated.',
        'Daily history is retained for up to 400 days; only the last 100 sessions are retained. Previously saved dates are not relabeled after timezone changes.',
        'Dates use saved local calendar labels; creation time includes the current UTC offset. Durations are seconds, not proof of continuous coverage.']
    if start is None:
        notes.append('The all-data awake total includes measured and imported counters, but excludes the separately listed manual baseline. Daily rows may cover a shorter retained span.')
    else:
        notes += ['Manual baseline hours have no recorded dates and are excluded from period totals.',
                  'Recording-start and earliest recovered dates describe this installation. Period totals use only selected retained date buckets.',
                  'Session starts are counted from retained records. Exact session durations inside a selected period cannot be reconstructed from aggregate session counters.']
        if not daily:
            notes.append('No recorded data is available for the selected period. This is not evidence of zero usage.')
        if not info['first_record'] or start < info['first_record'] or end > (info['last_record'] or ''):
            notes.append('The requested range extends outside available recorded dates. Totals include only retained records in the range.')
        if (iso_date(end) - iso_date(start)).days + 1 > len(daily):
            notes.append('Missing dates within the selected range are unavailable, not assumed to be zero.')
    if not history_allowed:
        notes.append('History collection is disabled in Settings. Dated history and retained session records are excluded, including previously stored records.')
    if history_allowed and ((ledger.get('daily_allocation') or {}).get('estimated') or any(a.get('estimated') for a in (ledger.get('daily_repair') or {}).get('allocations', []))):
        notes.append('Daily allocation includes estimates; exact historical awake intervals cannot be recovered.')
    if len(daily) > MAX_PREVIEW_DAYS:
        notes.append('Only the first 90 daily rows are shown in the preview and PDF. CSV contains every selected retained daily row.')
    comparison = {}
    if verification:
        for key in ('event_seconds', 'awake_seconds', 'difference'):
            value = verification.get(key)
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                comparison[key] = value
        if len(comparison) == 3:
            comparison['captured'] = verification.get('captured')
            notes.append('Verify compares the current boot only. It cannot prove complete historical coverage or recover missing OS logs.')
        else:
            comparison = {}
    return dict(version=VERSION, language=language,
                created=dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                period=dict(start=start, end=end), identity=identity, system=system,
                stats=stats, coverage=info, daily=daily, notes=notes, verification=comparison,
                first_record=daily[0][0] if daily else None,
                last_record=daily[-1][0] if daily else None)


def _value(value, language):
    if value is None or value == '':
        return tr('Not available', language)
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            return tr('Not available', language)
        return f'{value:.3f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def report_rows(report):
    """Common semantic rows used in both formats (CSV keys remain stable)."""
    lang, c = report['language'], report['coverage']
    period = report['period']
    yield 'report', 'Lifetime version', report['version']
    yield 'report', 'Created', report['created']
    yield 'report', 'Selected period', (f"{period['start']} / {period['end']}" if period['start'] else tr('All available data', lang))
    for section in ('identity', 'system'):
        for key, value in report[section].items():
            yield section, LABELS[key], value
    for key, title in [('awake_seconds', 'Awake time (seconds)'), ('baseline_seconds', 'Manual baseline (seconds, not dated)'),
                       ('measured_seconds', 'Directly measured awake time (seconds)'), ('imported_seconds', 'Reconstructed OS history (seconds)'),
                       ('session_starts', 'Retained session starts'), ('longest_session_seconds', 'Longest recorded session (seconds)'),
                       ('boots', 'Observed boots (all available data)')]:
        if key in ('baseline_seconds', 'boots') and period['start']:
            continue
        yield 'statistics', title, report['stats'][key]
    yield 'coverage', 'Source', tr(SOURCES[c['source']], lang)
    yield 'coverage', 'Coverage', tr('Partial' if c['status'] == 'partial' else 'Unknown', lang)
    if not period['start']:
        yield 'coverage', 'Earliest known date', c.get('earliest_known_date')
        yield 'coverage', 'Date source', tr(FIRST_SEEN_SOURCES.get(c.get('first_seen_source'), 'Unknown source'), lang)
    yield 'coverage', 'Recording began (proven timestamp)', c['recording_started']
    yield 'coverage', 'Earliest recovered OS record', c['earliest_record']
    yield 'coverage', 'First recorded date', report['first_record'] if period['start'] else c['first_record']
    yield 'coverage', 'Last recorded date', report['last_record'] if period['start'] else c['last_record']
    if report.get('verification') and not period['start']:
        yield 'coverage', 'Current-boot Verify difference (seconds)', report['verification']['difference']
        yield 'coverage', 'Verify comparison captured', report['verification'].get('captured')
    for gap in c.get('gaps', []):
        start_day, end_day = _day(gap.get('start')), _day(gap.get('end'))
        if period['start'] and start_day and end_day and (max(start_day, end_day) < period['start'] or min(start_day, end_day) > period['end']):
            continue
        yield 'coverage', 'Known gap observations (timestamps; duration unknown)', f"{gap.get('start') or tr('Unknown', lang)} / {gap.get('end') or tr('Unknown', lang)}"
    yield from report.get('extra_rows', ())


def report_html(report):
    lang = report['language']
    esc = lambda value: html.escape(str(value), quote=True)
    # Insert optional break opportunities without modifying the exported value.
    def cell(value):
        value = _value(value, lang)
        return '<br>'.join('&#8203;'.join(esc(line[i:i+32]) for i in range(0, len(line), 32)) for line in value.split('\n'))
    body = ['<html><head><meta charset="utf-8"><style>',
            'body{font-family:"Segoe UI","DejaVu Sans",sans-serif;font-size:10pt;color:#252d2d;}',
            'h1{font-size:20pt;}h2{font-size:13pt;margin-top:18pt;}table{width:100%;border-collapse:collapse;table-layout:fixed;}',
            'td,th{padding:5pt;border-bottom:1px solid #d8deda;text-align:left;}li{margin-bottom:5pt;}',
            '</style></head><body><h1>', esc(tr('Computer Awake-Time Report', lang)), '</h1>']
    headings = {'identity': 'Device identity', 'system': 'Selected system information',
                'statistics': 'Awake time (seconds)', 'coverage': 'Coverage and sources'}
    previous = None
    for section, key, value in report_rows(report):
        if section != previous:
            if previous is not None:
                body.append('</table>')
            if section in headings:
                body += ['<h2>', esc(tr(headings[section], lang)), '</h2>']
            body.append('<table width="100%" cellspacing="0" cellpadding="4">')
            previous = section
        body += ['<tr><td width="43%">', esc(tr(key, lang)), '</td><td width="57%">', cell(value), '</td></tr>']
    body += ['</table><h2>', esc(tr('Limitations', lang)), '</h2><ul>']
    for note in report['notes']:
        body += ['<li>', esc(tr(note, lang)), '</li>']
    body += ['</ul><h2>', esc(tr('Retained daily records', lang)), '</h2><table width="100%" cellspacing="0" cellpadding="4"><thead><tr><th width="50%">',
             esc(tr('Local date', lang)), '</th><th width="50%">', esc(tr('Awake seconds', lang)), '</th></tr></thead>']
    for day, seconds in report['daily'][:MAX_PREVIEW_DAYS]:
        body += ['<tr><td>', esc(day), '</td><td>', cell(seconds), '</td></tr>']
    if not report['daily']:
        body += ['<tr><td colspan="2">', esc(tr('Not available', lang)), '</td></tr>']
    body.append('</table></body></html>')
    return ''.join(body)


def _csv_safe(value):
    value = str(value)
    leading_controls = ''.join(chr(code) for code in range(33)) + '\ufeff\u200b'
    if value.lstrip(leading_controls).startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def _atomic_output(path, write):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        write(temporary)
        if not temporary.stat().st_size:
            raise OSError('Empty report output')
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(report, path):
    if Path(path).suffix.lower() != '.csv':
        raise ValueError('CSV output requires a .csv filename')
    def write(temporary):
        with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['section', 'field', 'value', 'local_date', 'awake_seconds'])
            for section, key, value in report_rows(report):
                writer.writerow([section, key, _csv_safe(_value(value, report['language'])), '', ''])
            for note in report['notes']:
                writer.writerow(['limitations', 'note', _csv_safe(tr(note, report['language'])), '', ''])
            for day, seconds in report['daily']:
                writer.writerow(['daily', 'awake_seconds', '', day, _value(seconds, report['language'])])
            stream.flush()
            os.fsync(stream.fileno())
    _atomic_output(path, write)


def ensure_report_font():
    """Reuse installed Unicode fonts; Windows offscreen Qt has no font discovery."""
    from PyQt6.QtGui import QFont, QFontDatabase
    if os.name == 'nt':
        fonts = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'
        for family, filename in (('Segoe UI Emoji', 'seguiemj.ttf'), ('Segoe UI Symbol', 'seguisym.ttf')):
            candidate = fonts/filename
            if family not in QFontDatabase.families() and candidate.is_file():
                QFontDatabase.addApplicationFont(str(candidate))
    if not QFontDatabase.families():
        if os.name == 'nt':
            fonts = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'
            candidates = [fonts / name for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisym.ttf', 'seguiemj.ttf', 'arial.ttf')]
        else:
            candidates = [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                          Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
                          Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf')]
        for candidate in candidates:
            if candidate.is_file():
                QFontDatabase.addApplicationFont(str(candidate))
    families = QFontDatabase.families()
    if not families:
        raise OSError('No Unicode font is available. Install a Vietnamese-capable system font.')
    family = next((name for name in ('Segoe UI', 'DejaVu Sans', 'Arial', 'Liberation Sans') if name in families), families[0])
    return QFont(family, 10)


def write_pdf(report, path, font=None):
    if Path(path).suffix.lower() != '.pdf':
        raise ValueError('PDF output requires a .pdf filename')
    from PyQt6.QtCore import QMarginsF, QSizeF
    from PyQt6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument
    if QGuiApplication.instance() is None:
        raise RuntimeError('PDF export requires the Lifetime GUI application')
    # Dashboards prepare fonts on the GUI thread and pass a QFont value to workers.
    if font is None:
        font = ensure_report_font()

    def write(temporary):
        writer = QPdfWriter(str(temporary))
        document = None
        try:
            writer.setResolution(144)
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            writer.setPageMargins(QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter)
            if writer.width() <= 0 or writer.height() <= 0:
                raise OSError(tr('The PDF page layout is unavailable.', report['language']))
            writer.setTitle(tr('Computer Awake-Time Report', report['language']))
            writer.setCreator('Lifetime ' + VERSION)
            document = QTextDocument()
            document.setDefaultFont(font)
            document.documentLayout().setPaintDevice(writer)
            document.setDocumentMargin(0)
            document.setPageSize(QSizeF(writer.width(), writer.height()))
            document.setHtml(report_html(report))
            document.documentLayout().documentSize()
            document.print(writer)
        finally:
            # Close the Windows handle even if layout or printing fails.
            del document
            del writer
        data = temporary.read_bytes()
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:] or b'/FontFile' not in data:
            raise OSError(tr('PDF generation failed or produced no text.', report['language']))
    _atomic_output(path, write)
