"""Staged agent updates. Standard library only (the packaged agent has no ``cryptography``).

Server (``Updates``): an Admin uploads one approved agent package (a file, its SHA-256 and its version). The server
offers it to a rollout share of computers (a stable per-computer number from 0 to 99 below the percentage), can pause
the rollout, and can roll back: the previous package becomes the offer again (marked as a rollback, so computers
accept the older version), or, without a previous package, the offer is withdrawn. Computers report what they did.
State lives in ``updates/updates.json`` beside the server's databases; package files are ``updates/<sha256>.pkg``.

Computer (``Updater``): only when its owner allowed updates (a separate switch, off by default) and the server lists the
``update`` capability, it asks for the offer over its pinned connection, downloads the package in chunks over the same
connection, checks the size and SHA-256 the server stated and, on Windows, an Authenticode signature when the file
carries one (an invalid or untrusted signature is refused; a file without one relies on the SHA-256). Only then is it
kept in the agent's protected directory with ``staged.json``. Nothing here ever runs the package: installing it is
left to the existing agent installer (install_agent_windows.ps1 -StagedUpdate), because replacing a running agent
safely, without prompts and with a way back, has not been verified on real machines. When the running agent's
version equals the offer, the computer reports it installed.
"""
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading

MAX_PACKAGE = 64 * 1024 * 1024
CHUNK = 512 * 1024              # base64 keeps one chunk below the protocol's 1 MiB message limit
MAX_REPORTS = 5000
CHECK_EVERY = 900               # seconds between a computer's update checks
STATUSES = ('staged', 'installed', 'failed')
FAILURES = ('download', 'size', 'checksum', 'signature', 'version', 'disk')
VERSION = re.compile(r'[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')
SHA256 = re.compile(r'[0-9a-f]{64}')
OFFER_FIELDS = ('version', 'sha256', 'size', 'name', 'rollback')


class UpdateError(ValueError):
    """An invalid package, setting or request (a short reason, never file content)."""


def version_tuple(text):
    if not isinstance(text, str) or not VERSION.fullmatch(text):
        raise UpdateError('Invalid version')
    return tuple(int(part) for part in text.split('.'))


def cohort(device_id, sha256):
    """0..99, stable for a computer and a package; a new package draws a new first 10 %."""
    return int(hashlib.sha256(f'{sha256}:{device_id}'.encode('ascii')).hexdigest()[:8], 16) % 100


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def _fields(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise UpdateError('Invalid update request')
    return value


# ---- server ----

class Updates:
    def __init__(self, directory):
        self.root = Path(directory) / 'updates'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self._lock = threading.Lock()
        self._state = self._read()

    def _read(self):
        empty = dict(current=None, previous=None, percent=0, paused=False, rollback=False, reports={})
        try:
            state = json.loads((self.root / 'updates.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return empty
        except (OSError, ValueError):
            return empty            # unreadable: offer nothing rather than something unknown
        if not isinstance(state, dict) or set(state) != set(empty):
            return empty
        for key in ('current', 'previous'):
            package = state[key]
            if package is not None and not (isinstance(package, dict) and SHA256.fullmatch(str(package.get('sha256')))
                                            and (self.root / f"{package['sha256']}.pkg").is_file()):
                state[key] = None
        return state

    def _save(self):
        import lifetime_permissions as permissions
        descriptor, temporary = tempfile.mkstemp(prefix='.updates.', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                permissions.protect_private(temporary)
                json.dump(self._state, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.root / 'updates.json')
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)

    # -- administration
    def summary(self):
        with self._lock:
            state = self._state
            current = state['current']
            reports = [dict(report, device_id=device) for device, report in state['reports'].items()]
            counts = dict.fromkeys(STATUSES, 0)
            for report in reports:
                if current and report['sha256'] == current['sha256']:
                    counts[report['status']] += 1
            reports.sort(key=lambda report: report['at'], reverse=True)
            return dict(current=current, previous=state['previous'], percent=state['percent'], paused=state['paused'],
                        rollback=state['rollback'], counts=counts, reports=reports[:200])

    def upload(self, path, version, sha256, by='admin'):
        """Accept a package only when its SHA-256 equals the one the administrator gives (from the release)."""
        version_tuple(version)
        expected = sha256.strip().lower() if isinstance(sha256, str) else ''
        if not SHA256.fullmatch(expected):
            raise UpdateError('Give the package SHA-256 (64 hexadecimal characters)')
        source = Path(path)
        name = source.name
        if not NAME.fullmatch(name):
            raise UpdateError('Package file names use letters, digits, dot, dash and underscore')
        try:
            regular = source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= MAX_PACKAGE
        except OSError:
            regular = False
        if not regular:
            raise UpdateError('The package must be a regular file of at most 64 MiB')
        import lifetime_permissions as permissions
        descriptor, temporary = tempfile.mkstemp(prefix='.package.', dir=self.root)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(descriptor, 'wb') as output, source.open('rb') as data:
                permissions.protect_private(temporary)
                for block in iter(lambda: data.read(1 << 20), b''):
                    size += len(block)
                    if size > MAX_PACKAGE:
                        raise UpdateError('The package must be a regular file of at most 64 MiB')
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != expected:
                raise UpdateError('The file does not match the SHA-256 given')
            os.replace(temporary, self.root / f'{expected}.pkg')
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
        with self._lock:
            state = self._state
            if state['current'] and state['current']['sha256'] != expected:
                state['previous'] = state['current']
            state['current'] = dict(version=version, sha256=expected, size=size, name=name, uploaded_at=_now(),
                                    uploaded_by=str(by)[:64])
            state.update(percent=0, paused=False, rollback=False)      # a new package starts offered to nobody
            self._prune()
            self._save()
        return self.summary()

    def set_rollout(self, percent=None, paused=None):
        if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
            raise UpdateError('Rollout is a whole percentage from 0 to 100')
        if paused is not None and type(paused) is not bool:
            raise UpdateError('Invalid pause flag')
        with self._lock:
            if self._state['current'] is None:
                raise UpdateError('Upload a package first')
            if percent is not None:
                self._state['percent'] = percent
            if paused is not None:
                self._state['paused'] = paused
            self._save()
        return self.summary()

    def rollback(self):
        """Offer the previous package to every computer again (older version allowed), or withdraw the offer."""
        with self._lock:
            state = self._state
            if state['current'] is None:
                raise UpdateError('Nothing to roll back')
            if state['previous'] is not None:
                state['current'], state['previous'] = state['previous'], state['current']
                state.update(percent=100, paused=False, rollback=True)
            else:
                state.update(current=None, percent=0, paused=False, rollback=False)
            self._prune()
            self._save()
        return self.summary()

    def _prune(self):
        keep = {f"{package['sha256']}.pkg" for package in (self._state['current'], self._state['previous']) if package}
        for path in self.root.glob('*.pkg'):
            if path.name not in keep:
                with contextlib.suppress(OSError):
                    path.unlink()

    # -- computers (already authenticated by the caller)
    def handle(self, device_id, message):
        operation = message.get('op') if isinstance(message, dict) else None
        if operation == 'offer':
            _fields(message, ('op', 'device_id', 'version'), ('op', 'device_id', 'version'))
            version_tuple(message['version'])
            with self._lock:
                current = self._state['current']
                if current is None:
                    return dict(target=None, go=False)
                go = (not self._state['paused'] and cohort(device_id, current['sha256']) < self._state['percent'])
                return dict(target=dict({key: current[key] for key in OFFER_FIELDS if key != 'rollback'},
                                        rollback=self._state['rollback']), go=go)
        if operation == 'chunk':
            _fields(message, ('op', 'device_id', 'sha256', 'offset'), ('op', 'device_id', 'sha256', 'offset'))
            offset = message['offset']
            with self._lock:
                current = self._state['current']
                if (current is None or message['sha256'] != current['sha256'] or self._state['paused']
                        or cohort(device_id, current['sha256']) >= self._state['percent']):
                    raise UpdateError('Not offered to this computer')
                if type(offset) is not int or offset % CHUNK or not 0 <= offset < current['size']:
                    raise UpdateError('Invalid offset')
                with open(self.root / f"{current['sha256']}.pkg", 'rb') as source:
                    source.seek(offset)
                    data = source.read(CHUNK)
            return dict(sha256=message['sha256'], offset=offset, size=current['size'],
                        data=base64.b64encode(data).decode('ascii'))
        if operation == 'report':
            _fields(message, ('op', 'device_id', 'sha256', 'status', 'version', 'category'),
                    ('op', 'device_id', 'sha256', 'status', 'version'))
            version_tuple(message['version'])
            if (not SHA256.fullmatch(str(message['sha256'])) or message['status'] not in STATUSES
                    or (message['status'] == 'failed') != ('category' in message)
                    or message.get('category', 'download') not in FAILURES):
                raise UpdateError('Invalid update report')
            with self._lock:
                reports = self._state['reports']
                reports.pop(device_id, None)
                reports[device_id] = dict(sha256=message['sha256'], status=message['status'], version=message['version'],
                                          category=message.get('category'), at=_now())
                while len(reports) > MAX_REPORTS:
                    reports.pop(next(iter(reports)))
                self._save()
            return dict(recorded=True)
        raise UpdateError('Invalid update request')


# ---- computer ----

def authenticode(path):
    """'valid', 'none' (the file carries no Authenticode signature, or is not a signable type) or 'invalid'.

    Windows only, offline (no revocation lookup); other systems return 'none' and rely on the SHA-256."""
    if sys.platform != 'win32':
        return 'none'
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_ubyte * 8)]

    class FileInfo(ctypes.Structure):
        _fields_ = [('cbStruct', wintypes.DWORD), ('pcwszFilePath', wintypes.LPCWSTR), ('hFile', wintypes.HANDLE),
                    ('pgKnownSubject', ctypes.POINTER(GUID))]

    class TrustData(ctypes.Structure):
        _fields_ = [('cbStruct', wintypes.DWORD), ('pPolicyCallbackData', ctypes.c_void_p),
                    ('pSIPClientData', ctypes.c_void_p), ('dwUIChoice', wintypes.DWORD),
                    ('fdwRevocationChecks', wintypes.DWORD), ('dwUnionChoice', wintypes.DWORD),
                    ('pFile', ctypes.POINTER(FileInfo)), ('dwStateAction', wintypes.DWORD),
                    ('hWVTStateData', wintypes.HANDLE), ('pwszURLReference', wintypes.LPCWSTR),
                    ('dwProvFlags', wintypes.DWORD), ('dwUIContext', wintypes.DWORD),
                    ('pSignatureSettings', ctypes.c_void_p)]
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    info = FileInfo(ctypes.sizeof(FileInfo), str(Path(path).resolve()), None, None)
    data = TrustData()
    data.cbStruct = ctypes.sizeof(TrustData)
    data.dwUIChoice = 2                     # WTD_UI_NONE
    data.fdwRevocationChecks = 0            # WTD_REVOKE_NONE
    data.dwUnionChoice = 1                  # WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(info)
    data.dwStateAction = 1                  # WTD_STATEACTION_VERIFY
    data.dwProvFlags = 0x10 | 0x1000        # no revocation check, cached URLs only: never goes online
    verify = ctypes.WinDLL('wintrust').WinVerifyTrust
    verify.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_void_p]
    verify.restype = wintypes.LONG
    result = verify(None, ctypes.byref(action), ctypes.byref(data)) & 0xFFFFFFFF
    data.dwStateAction = 2                  # WTD_STATEACTION_CLOSE
    verify(None, ctypes.byref(action), ctypes.byref(data))
    if result == 0:
        return 'valid'
    # TRUST_E_NOSIGNATURE, TRUST_E_SUBJECT_FORM_UNKNOWN (not a signable file), TRUST_E_PROVIDER_UNKNOWN
    return 'none' if result in (0x800B0100, 0x800B0003, 0x800B0001) else 'invalid'


class Updater:
    """One computer's update check. ``tick()`` returns what happened, as a short word for status and tests."""

    def __init__(self, agent, request=None, version=None):
        import lifetime_protocol as protocol
        self.agent = agent
        self.version = version or protocol.CLIENT_VERSION
        self._request = request
        self._reported = set()      # (sha256, status, category) already told to the server by this process
        self.root = Path(agent.directory) / 'updates'

    def request(self, *arguments, **options):
        if self._request is None:
            from lifetime_tls import request_json
            self._request = request_json
        return self._request(*arguments, **options)

    def staged(self):
        try:
            value = json.loads((self.root / 'staged.json').read_text(encoding='utf-8'))
            if (set(value) == {'version', 'sha256', 'size', 'name', 'signature', 'staged_at'}
                    and SHA256.fullmatch(value['sha256']) and NAME.fullmatch(value['name'])
                    and (self.root / ('package-' + value['name'])).is_file()):
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def discard(self):
        if self.root.is_dir() and not self.root.is_symlink():
            shutil.rmtree(self.root, ignore_errors=True)

    def _state(self):
        with self.agent._lock:
            return self.agent._load()

    def _call(self, state, message):
        return self.request(dict(state['bundle']), '/v1/update', dict(message, device_id=state['device_id']),
                            credential=state['credential'], timeout=30)

    def _report(self, state, sha256, status, category=None):
        key = (sha256, status, category)
        if key in self._reported:
            return
        message = dict(op='report', sha256=sha256, status=status, version=self.version)
        if category:
            message['category'] = category
        with contextlib.suppress(Exception):
            self._call(state, message)
            self._reported.add(key)

    def tick(self):
        state = self._state()
        if not state.get('updates'):
            self.discard()                      # consent withdrawn: nothing is kept
            return 'off'
        if (state.get('phase') != 'approved' or not state.get('credential') or not state.get('enabled')
                or 'update' not in (state.get('capabilities') or [])):
            return 'unavailable'
        reply = self._call(state, dict(op='offer', version=self.version))
        _fields(reply, ('target', 'go'), ('target', 'go'))
        target, go = reply['target'], reply['go']
        if type(go) is not bool:
            raise UpdateError('Invalid offer')
        if target is not None:
            _fields(target, OFFER_FIELDS, OFFER_FIELDS)
            version_tuple(target['version'])
            if (not SHA256.fullmatch(str(target['sha256'])) or not NAME.fullmatch(str(target['name']))
                    or type(target['size']) is not int or not 0 < target['size'] <= MAX_PACKAGE
                    or type(target['rollback']) is not bool):
                raise UpdateError('Invalid offer')
        staged = self.staged()
        if staged and (target is None or staged['sha256'] != target['sha256']):
            self.discard()                      # withdrawn, rolled back or replaced by a newer package
            staged = None
        if target is None:
            return 'none'
        if target['version'] == self.version:
            if staged:
                self._report(state, target['sha256'], 'installed')
                self.discard()
            return 'current'
        if staged:
            return 'staged'
        if not go:
            return 'waiting'
        if version_tuple(target['version']) < version_tuple(self.version) and not target['rollback']:
            self._report(state, target['sha256'], 'failed', 'version')      # never an unrequested downgrade
            return 'failed:version'
        problem = self._download(state, target)
        self._report(state, target['sha256'], 'failed' if problem else 'staged', problem)
        return 'failed:' + problem if problem else 'staged'

    def _download(self, state, target):
        """Download, check and keep the package; returns a failure category or None. A failed file is deleted."""
        self.root.mkdir(mode=0o700, exist_ok=True)
        partial = self.root / 'download.part'
        try:
            digest, offset = hashlib.sha256(), 0
            with open(partial, 'wb') as output:
                while offset < target['size']:
                    try:
                        reply = self._call(state, dict(op='chunk', sha256=target['sha256'], offset=offset))
                        _fields(reply, ('sha256', 'offset', 'size', 'data'), ('sha256', 'offset', 'size', 'data'))
                        if reply['sha256'] != target['sha256'] or reply['offset'] != offset or reply['size'] != target['size']:
                            return 'download'
                        block = base64.b64decode(reply['data'], validate=True)
                    except Exception:
                        return 'download'
                    if not block or len(block) > CHUNK or offset + len(block) > target['size']:
                        return 'size'
                    digest.update(block)
                    output.write(block)
                    offset += len(block)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != target['sha256']:
                return 'checksum'
            signature = authenticode(partial)
            if signature == 'invalid':
                return 'signature'
            if not self._state().get('updates'):        # the owner switched updates off meanwhile
                return 'download'
            final = self.root / ('package-' + target['name'])
            os.replace(partial, final)
            if _digest(final) != target['sha256']:
                final.unlink()
                return 'checksum'
            record = dict(version=target['version'], sha256=target['sha256'], size=target['size'], name=target['name'],
                          signature=signature, staged_at=_now())
            (self.root / 'staged.json').write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
            return None
        except OSError:
            return 'disk'
        finally:
            with contextlib.suppress(FileNotFoundError, OSError):
                partial.unlink()
