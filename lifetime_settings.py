"""Local consent and preferences. Missing or damaged settings fail closed."""
import datetime as dt
from copy import deepcopy
import json
from pathlib import Path
import shutil

from lifetime_tracker import atomic_json, data_dir, file_lock, new_ledger
from lifetime_i18n import default_language
from lifetime_identity import FIELDS as IDENTITY_FIELDS, normalize as normalize_identity

CATEGORIES = ('history', 'battery', 'network', 'wifi_name', 'graphics', 'resources', 'health', 'remote_power')
SYSTEM_FIELDS = ('system', 'release', 'machine', 'python_version')


def bundled_command(tool):
    """Command that starts another program of a packaged (PyInstaller) build: on Windows ``<tool>.exe`` beside this one;
    on Linux ``<tool>`` beside it, or, inside the AppImage, the AppImage itself (its mount path changes every start, so
    a path inside it must never be remembered by an autostart entry or a detached process)."""
    import os
    import sys
    appdir, appimage = os.environ.get('APPDIR'), os.environ.get('APPIMAGE')
    if (sys.platform != 'win32' and appdir and appimage
            and Path(sys.executable).resolve().is_relative_to(Path(appdir).resolve())):
        return [appimage, '--run-tool', tool]
    return [str(Path(sys.executable).with_name(tool + ('.exe' if sys.platform == 'win32' else '')))]


SERVER_INSTALLED = ('/opt/lifetime-server/LifetimeServerRuntime',)     # its settings stay after an uninstall
START_CHOICE = 'appimage-opens.txt'


def appimage_start(ask, directory=None, environ=None, installed=SERVER_INSTALLED):
    """What a plain start of the AppImage opens: 'desktop', 'server' (the Lifetime Server dashboard) or None (the
    question was closed). Only when Lifetime Server is installed on this computer is the person asked, with
    ``ask() -> (choice, remember)``; a remembered answer is kept in their own data folder. AppRun marks a start
    without arguments, and the marker is removed so nothing this program starts inherits it."""
    import os
    environ = os.environ if environ is None else environ
    if environ.pop('LIFETIME_APPIMAGE_START', None) != '1' or not any(os.path.exists(path) for path in installed):
        return 'desktop'
    path = start_choice_path(directory)
    try:
        remembered = path.read_text(encoding='utf-8').strip()
    except (OSError, ValueError):
        remembered = ''
    if remembered in ('desktop', 'server'):
        return remembered
    choice, remember = ask()
    if remember and choice in ('desktop', 'server'):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(choice, encoding='utf-8')
        except OSError:
            pass        # asked again next time
    return choice


def start_choice_path(directory=None):
    """Where the AppImage's remembered choice is kept (exists only once someone ticked Remember my choice)."""
    return Path(directory or data_dir()) / START_CHOICE


def iso_date(value: str) -> dt.date:
    """Parse a calendar date in exact YYYY-MM-DD form; reject other ISO forms."""
    if not isinstance(value, str):
        raise ValueError('Date must be YYYY-MM-DD')
    parsed = dt.date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Date must be YYYY-MM-DD')
    return parsed


def defaults():
    return dict(version=1, language=default_language(), theme='auto', style='classic', completed=False, awake=True, rebuild=False,
                autostart=False, identity={key: '' for key in IDENTITY_FIELDS},
                **{key: False for key in CATEGORIES})


def normalize(value):
    result = defaults()
    if isinstance(value, dict):
        try:
            result.update(deepcopy(value))
        except RecursionError:
            raise ValueError('Settings nesting is too deep') from None
        result['language'] = defaults()['language']
        if value.get('language') in ('en', 'vi'):
            result['language'] = value['language']
        result['theme'] = value.get('theme') if value.get('theme') in ('auto', 'light', 'dark') else 'auto'
        result['style'] = value.get('style') if value.get('style') in ('glass', 'classic') else 'classic'    # classic is how it always looked
        for key in (*CATEGORIES, 'completed', 'rebuild', 'autostart'):
            result[key] = value.get(key) is True
        result['identity'] = normalize_identity(value.get('identity', {}))
    result['awake'] = True
    return result


def load_settings(directory=None):
    try:
        return normalize(json.loads((Path(directory or data_dir()) / 'settings.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, RecursionError):
        return defaults()


read_settings = load_settings


def _merge(original, patch):
    result = deepcopy(original)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _existing(directory):
    try:
        value = json.loads((directory / 'settings.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except RecursionError:
        raise ValueError('Settings nesting is too deep') from None
    if not isinstance(value, dict):
        raise ValueError('Settings must be a JSON object')
    normalize(value)  # Refuse to overwrite malformed identity data.
    return value


def update_settings(patch, directory=None):
    """Atomically merge a patch into fresh settings, preserving consent and extensions.

    Empty identity strings clear fields. Invalid existing data or patches raise;
    no write occurs. This function never collects device information.
    """
    if not isinstance(patch, dict):
        raise ValueError('Settings patch must be an object')
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        try:
            value = normalize(_merge(_existing(directory), patch))
        except RecursionError:
            raise ValueError('Settings nesting is too deep') from None
        atomic_json(directory / 'settings.json', value)
    return value


def save_settings(value, directory=None):
    """Save full UI consent; retain omitted identity and extension fields."""
    normalized = normalize(value)
    directory = Path(directory or data_dir())
    with file_lock(directory, 'ledger.lock'):
        existing = _existing(directory)
        merged = _merge(existing, value if isinstance(value, dict) else {})
        for key in (*CATEGORIES, 'completed', 'rebuild', 'autostart', 'awake', 'language', 'theme'):
            merged[key] = normalized[key]
        atomic_json(directory / 'settings.json', normalize(merged))


def export_allowed(settings, category, field=None):
    """Check consent; callers must explicitly select identity/system fields.

    Optional export/exports containers (also nested under consent) can veto
    all exports, a category, or an individual field with literal False.
    """
    if not isinstance(settings, dict) or settings.get('completed') is not True:
        return False
    if settings.get('consent') is False:
        return False
    containers = [settings]
    if isinstance(settings.get('consent'), dict):
        containers.append(settings['consent'])
    for container in containers:
        if container.get(category) is False and category not in CATEGORIES:
            return False
        for name in ('export', 'exports'):
            policy = container.get(name)
            if policy is False:
                return False
            if isinstance(policy, dict):
                rule = policy.get(category)
                if rule is False or policy.get('enabled') is False:
                    return False
                if isinstance(rule, dict) and (rule.get('enabled') is False or
                                              (field is not None and rule.get(field) is False)):
                    return False
    if category == 'report':
        return True
    if category == 'identity':
        return field in IDENTITY_FIELDS
    if category in ('system', 'systems'):
        return field in SYSTEM_FIELDS and settings.get('resources') is True
    if category in CATEGORIES:
        return settings.get(category) is True
    return False


def allowed_probes(settings):
    if not settings.get('completed'):
        return set()
    names = {'clocks'}
    for category, probes in (
        ('battery', ('power', 'battery')), ('network', ('network', 'counters')),
        ('wifi_name', ('wifi',)), ('graphics', ('gpu',)),
        ('resources', ('cpu', 'ram', 'disks', 'identity', 'inventory'))):
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
