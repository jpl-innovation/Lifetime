"""One protected archive with everything a Lifetime Server needs to come back, a check that needs no server, and
restore into a stopped server.

The archive (a .zip, ACL'd to Administrators and SYSTEM, 0600 elsewhere, because it holds the server's private key)
contains consistent SQLite backups of ``server.sqlite``, ``monitoring.sqlite`` and ``access.sqlite`` (roles and the
audit trail) made with the SQLite backup API through the running server (the same path as the dashboard's database
backup), the TLS key and certificates, the server's configuration, and ``manifest.json`` with the SHA-256 and size of
every file. ``<archive>.sha256`` beside it holds the archive's own SHA-256. Never included: ``admin-token`` (a restore
keeps the new server's token) and ``alert-secrets.bin`` (DPAPI-bound to the account and machine that wrote it).

Restore is staged: the archive is checked and copied into the data directory, and the next server start applies it
before any listener opens (key and certificate first, then the databases through the normal restore, which checks
them again and invalidates every computer credential, code and polling token). The current audit trail and roles are
kept, so a restore can never bring back an older trail or a role that was taken away; ``access.sqlite`` from the
archive is used only when the server has none (a new machine).
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile

import lifetime_permissions as permissions

FORMAT, VERSION = 'lifetime-server-backup', 1
MEMBERS = ('manifest.json', 'configuration.json', 'server.sqlite', 'server.monitoring.sqlite', 'access.sqlite',
           'server-key.pem', 'server-cert.pem', 'server-cert.previous.pem')
REQUIRED = frozenset(MEMBERS) - {'server-cert.previous.pem'}
MAX_MEMBER = 8 * 1024 ** 3
STAGED, FAILED = 'restore-staged.zip', 'restore-failed.zip'
RESERVED = (STAGED, STAGED + '.sha256', FAILED, FAILED + '.sha256')


class Problem(ValueError):
    """Why an archive is refused, as a category only (never a path or file content)."""


def checksum_path(path):
    return Path(str(path) + '.sha256')


def _digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            value.update(chunk)
    return value.hexdigest()


def _protected(path, write):
    """Write a new file that is private before its first byte, then move it into place atomically."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            permissions.protect_private(temporary)
            write(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _configuration(runtime):
    from lifetime_protocol import CLIENT_VERSION
    value = dict(address=runtime.address, host=runtime.host, port=runtime.port, admin_port=runtime.admin_port,
                 server_version=CLIENT_VERSION)
    install = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).parent / 'server-install.json'
    with contextlib.suppress(OSError, ValueError):
        if install.is_file() and install.stat().st_size <= 65536:
            recorded = json.loads(install.read_text(encoding='utf-8-sig'))
            if isinstance(recorded, dict):
                value['install'] = recorded      # the installer's non-secret selections, for reference
    return value


def create(runtime, path):
    """Archive the running server into ``path`` (.zip) and write ``path.sha256``."""
    path = Path(path).resolve()
    if path.suffix.lower() != '.zip':
        raise Problem('Backup archives end in .zip')
    runtime._check_backup_path(str(checksum_path(path)))
    with tempfile.TemporaryDirectory(dir=runtime.directory, prefix='backup-') as work:     # inside the protected directory
        work = Path(work)
        runtime.backup(work / 'server.sqlite')            # and server.monitoring.sqlite beside it
        runtime.access.backup(work / 'access.sqlite')
        for name in ('server-key.pem', 'server-cert.pem', 'server-cert.previous.pem'):
            if (runtime.directory / name).is_file():
                shutil.copyfile(runtime.directory / name, work / name)
        (work / 'configuration.json').write_text(json.dumps(_configuration(runtime), indent=1), encoding='utf-8')
        files = {name: dict(sha256=_digest(work / name), size=(work / name).stat().st_size)
                 for name in MEMBERS[1:] if (work / name).is_file()}
        manifest = dict(format=FORMAT, version=VERSION, created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        server_version=_configuration(runtime)['server_version'], files=files)

        def write(output):
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, indent=1))
                for name in files:
                    archive.write(work / name, name)
        _protected(path, write)
    digest = _digest(path)
    _protected(checksum_path(path), lambda output: output.write(f'{digest}  {path.name}\n'.encode('utf-8')))
    return dict(path=str(path), checksum=str(checksum_path(path)), sha256=digest, files=sorted(files),
                created_at=manifest['created_at'])


def _extract(path, work):
    """Check the checksum file, the archive's members and every member's hash; extract into ``work``."""
    try:
        recorded = checksum_path(path).read_text(encoding='utf-8').split()[0].lower()
    except (OSError, IndexError, UnicodeError):
        raise Problem('checksum-missing') from None
    if not re.fullmatch('[0-9a-f]{64}', recorded) or _digest(path) != recorded:
        raise Problem('checksum')
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) - set(MEMBERS):
                raise Problem('archive')
            if REQUIRED - set(names):
                raise Problem('missing')
            info = archive.getinfo('manifest.json')
            if info.file_size > 65536:
                raise Problem('manifest')
            manifest = json.loads(archive.read(info))
            if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or type(manifest.get('version')) is not int:
                raise Problem('manifest')
            if manifest['version'] > VERSION:
                raise Problem('newer')
            files = manifest.get('files')
            if not isinstance(files, dict) or set(files) != set(names) - {'manifest.json'}:
                raise Problem('manifest')
            for name, expected in files.items():
                info = archive.getinfo(name)
                if (not isinstance(expected, dict) or type(expected.get('size')) is not int
                        or info.file_size != expected['size'] or info.file_size > MAX_MEMBER):
                    raise Problem('damaged')
                value, size = hashlib.sha256(), 0
                with archive.open(info) as source, open(work / name, 'wb') as output:
                    for chunk in iter(lambda: source.read(1 << 20), b''):
                        size += len(chunk)
                        if size > info.file_size:
                            raise Problem('damaged')
                        value.update(chunk)
                        output.write(chunk)
                if value.hexdigest() != expected.get('sha256'):
                    raise Problem('damaged')
            return manifest
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, TypeError, ValueError, OSError) as error:
        if isinstance(error, Problem):
            raise
        raise Problem('archive') from None


def _check(work):
    """Every database must be one this server can restore, the audit chain must verify, and the key must match the
    certificate."""
    from lifetime_access import Access, AccessError
    from lifetime_monitor import Monitor
    from lifetime_server_store import Store
    import lifetime_tls as tls
    try:
        with contextlib.closing(sqlite3.connect((work / 'server.sqlite').as_uri() + '?mode=ro', uri=True,
                                                isolation_level=None)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            with contextlib.closing(sqlite3.connect(':memory:', isolation_level=None)) as copy:
                db.backup(copy)
                Store.upgrade_backup(copy)      # an archive from the previous schema restores like its own backup
                Store._validate_backup(copy)
        Monitor.read_backup(work / 'server.monitoring.sqlite').close()
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise Problem('database') from None
    try:
        if not Access.validate(work / 'access.sqlite')['ok']:
            raise Problem('audit')
    except AccessError:
        raise Problem('audit') from None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        certificate = (work / 'server-cert.pem').read_bytes()
        tls.certificate_info(certificate.decode('ascii'))
        key = serialization.load_pem_private_key((work / 'server-key.pem').read_bytes(), None)
        spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(*spki) != x509.load_pem_x509_certificate(certificate).public_key().public_bytes(*spki):
            raise Problem('identity')
    except (ValueError, TypeError, UnicodeError):
        raise Problem('identity') from None


def verify(path, directory):
    """Check an archive without changing anything. ``directory`` is a protected place for the temporary copy (the
    data directory). Returns {ok, problem} or {ok, created_at, server_version, files}."""
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(dir=directory, prefix='verify-') as work:
        try:
            manifest = _extract(path, Path(work))
            _check(Path(work))
        except Problem as problem:
            return dict(ok=False, problem=str(problem))
    return dict(ok=True, created_at=manifest.get('created_at'), server_version=manifest.get('server_version'),
                files=sorted(manifest['files']))


def stage(directory, path):
    """Check an archive and copy it into the data directory; the next server start restores it."""
    directory, path = Path(directory), Path(path).resolve()
    report = verify(path, directory)
    if not report['ok']:
        return report
    with open(path, 'rb') as source:
        _protected(directory / STAGED, lambda output: shutil.copyfileobj(source, output))
    _protected(directory / (STAGED + '.sha256'), lambda output: output.write(checksum_path(path).read_bytes()))
    return dict(report, staged=True, restart_required=True)


def prepare_staged(directory):
    """Start, step 1 (before the TLS identity or any database opens): check the staged archive again and put its key
    and certificate in place (the replaced ones are kept as *.before-restore.pem). Returns the extracted folder for
    step 2, None when nothing is staged, or 'failed' (the archive is renamed restore-failed.zip and nothing changed)."""
    directory = Path(directory)
    staged = directory / STAGED
    if not staged.is_file():
        return None
    work = Path(tempfile.mkdtemp(dir=directory, prefix='restore-'))
    try:
        _extract(staged, work)
        _check(work)
    except Problem:
        shutil.rmtree(work, ignore_errors=True)
        for name in ('', '.sha256'):
            with contextlib.suppress(OSError):
                os.replace(directory / (STAGED + name), directory / (FAILED + name))
        return 'failed'
    for name in ('server-key.pem', 'server-cert.pem'):
        current = directory / name
        if current.is_file() and current.read_bytes() == (work / name).read_bytes():
            continue
        if current.is_file():
            os.replace(current, directory / name.replace('.pem', '.before-restore.pem'))
        _protected(current, lambda output, name=name: output.write((work / name).read_bytes()))
    if not (directory / 'access.sqlite').exists():
        _protected(directory / 'access.sqlite', lambda output: output.write((work / 'access.sqlite').read_bytes()))
    return work


def finish_staged(runtime, work):
    """Start, step 2 (databases open, no listener yet): the normal restore, which validates again and invalidates
    every computer credential, code and polling token; then the staged files go."""
    try:
        runtime.restore(str(work / 'server.sqlite'))          # monitoring comes from server.monitoring.sqlite beside it
        runtime._store.log_event('restore-applied')
        runtime.access.record(dict(sid=None, name='server', groups=[]), {'admin'}, 'restore-applied', '', 'ok',
                              str(uuid.uuid4()))
        for name in ('', '.sha256'):
            with contextlib.suppress(OSError):
                os.unlink(runtime.directory / (STAGED + name))
    except Exception:
        runtime._store.log_event('restore-failed')
        for name in ('', '.sha256'):
            with contextlib.suppress(OSError):
                os.replace(runtime.directory / (STAGED + name), runtime.directory / (FAILED + name))
    finally:
        shutil.rmtree(work, ignore_errors=True)
