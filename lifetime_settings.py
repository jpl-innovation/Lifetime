"""Local consent and preferences. Missing or damaged settings fail closed."""
import datetime as dt
from pathlib import Path
import shutil

from lifetime_tracker import atomic_json, data_dir, file_lock, new_ledger
from lifetime_i18n import default_language

CATEGORIES = ('history', 'battery', 'network', 'wifi_name', 'graphics', 'resources')


def defaults():
    return dict(version=1, language=default_language(), completed=False, awake=True, rebuild=False,
                autostart=False, **{key: False for key in CATEGORIES})


def normalize(value):
    result = defaults()
    if isinstance(value, dict):
        if value.get('language') in ('en', 'vi'):
            result['language'] = value['language']
        for key in (*CATEGORIES, 'completed', 'rebuild', 'autostart'):
            result[key] = value.get(key) is True
    result['awake'] = True
    return result


def load_settings(directory=None):
    import json
    try:
        return normalize(json.loads((Path(directory or data_dir()) / 'settings.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return defaults()


def save_settings(value, directory=None):
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        atomic_json(directory / 'settings.json', normalize(value))


def allowed_probes(settings):
    if not settings.get('completed'):
        return set()
    names = {'clocks'}
    for category, probes in (
        ('battery', ('power', 'battery')), ('network', ('network', 'counters')),
        ('wifi_name', ('wifi',)), ('graphics', ('gpu',)),
        ('resources', ('cpu', 'ram', 'disks', 'identity'))):
        if settings.get(category):
            names.update(probes)
    return names


def delete_history(directory=None):
    """Reset under the tracker's write lock; retain an explicit local backup."""
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        backup = directory / ('backup-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir()
        for name in ('ledger.json', 'status.json'):
            source = directory / name
            if source.exists():
                shutil.copy2(source, backup / name)
        ledger = new_ledger()
        # Do not re-credit the current boot after deletion.
        from lifetime_tracker import awake_clock, boot_id
        tick, identity = awake_clock(), boot_id()
        ledger.update(last_clock=tick, boot_id=identity,
                      boot_credited_for=identity, boot_accounted_seconds=tick)
        atomic_json(directory / 'ledger.json', ledger)
        atomic_json(directory / 'status.json', dict(ledger, running=False))
    return backup
