"""Unix domain socket channel for the local administration API on Linux, bound to the caller's Unix account.

The Linux counterpart of lifetime_pipe. The kernel tells the server who is calling (SO_PEERCRED: the uid and gid of the
process that connected); a client cannot name itself. Identities use the same shape as Windows ones, with principal
strings ``unix:uid:<n>`` for a user and ``unix:gid:<n>`` for a group, so roles, the audit chain and the power gate work
unchanged. A caller's groups are its primary group plus its supplementary groups from the user database (getgrouplist):
adding or removing someone from a group takes effect on their next request.

The socket is created under a temporary name (only its owner can connect while the umask decides), then made 0660 with
group ``lifetime-admin`` (or LIFETIME_ADMIN_GROUP) and renamed into place, so it is never reachable by others. A peer
without any role is disconnected before anything is read, like a Windows account the pipe's DACL does not admit.
Clients refuse a socket whose listening process is not root, the ``lifetime`` service account or themselves.

Standard library only. Linux only: every entry point raises OSError where SO_PEERCRED does not exist.
"""
import contextlib
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time

MAX_MESSAGE = 1024 * 1024 + 4096
RUNTIME_DIRECTORY = '/run/lifetime-server'
ADMIN_GROUP = 'lifetime-admin'
SERVICE_USER = 'lifetime'
USER, GROUP = 'unix:uid:', 'unix:gid:'


def _linux():
    if not sys.platform.startswith('linux') or not hasattr(socket, 'SO_PEERCRED'):
        raise OSError('The identity-bound Unix admin channel exists only on Linux')


def socket_path(directory, port):
    """Where the admin socket for ``port`` lives: $LIFETIME_ADMIN_SOCKET_DIR, else /run/lifetime-server when it exists
    (created by the service manager), else the server's data directory (a console or test run). Server and client
    compute the same path."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid admin port')
    base = os.environ.get('LIFETIME_ADMIN_SOCKET_DIR') or (RUNTIME_DIRECTORY if os.path.isdir(RUNTIME_DIRECTORY)
                                                           else str(directory))
    return str(Path(base) / f'admin-{port}.sock')


def admin_group():
    return os.environ.get('LIFETIME_ADMIN_GROUP') or ADMIN_GROUP


def group_id(name):
    """gid of a group name, or None when the group does not exist (or there is no group database)."""
    try:
        import grp
        return grp.getgrnam(name).gr_gid
    except (ImportError, KeyError):
        return None


def user_id(name):
    try:
        import pwd
        return pwd.getpwnam(name).pw_uid
    except (ImportError, KeyError):
        return None


def identity_for(uid, gid):
    """The identity the server uses for a peer: {sid, name, groups} like a Windows token's."""
    try:
        import pwd
        entry = pwd.getpwuid(uid)
        name, groups = entry.pw_name, set(os.getgrouplist(entry.pw_name, gid))
    except (ImportError, KeyError, OSError):
        name, groups = f'uid {uid}', set()
    groups.add(gid)
    return dict(sid=f'{USER}{uid}', name=name, groups=sorted(f'{GROUP}{g}' for g in groups))


def current_identity():
    _linux()
    return identity_for(os.getuid(), os.getgid())


def peer_credentials(connection):
    """(pid, uid, gid) of the process at the other end, from the kernel."""
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    return struct.unpack('3i', raw)


def lookup_account(text):
    """Resolve a user name, a group name (``group:NAME`` or a name with no such user) or ``unix:uid:N`` /
    ``unix:gid:N`` to (principal, display name, 'user' | 'group')."""
    if not isinstance(text, str) or not 1 <= len(text) <= 256 or any(ord(c) < 32 for c in text):
        raise ValueError('Invalid account name')
    import grp
    import pwd
    try:
        if text.startswith(USER) and text[len(USER):].isdigit():
            uid = int(text[len(USER):])
            return f'{USER}{uid}', pwd.getpwuid(uid).pw_name, 'user'
        if text.startswith(GROUP) and text[len(GROUP):].isdigit():
            gid = int(text[len(GROUP):])
            return f'{GROUP}{gid}', grp.getgrgid(gid).gr_name, 'group'
        if not text.startswith('group:'):
            with contextlib.suppress(KeyError):
                entry = pwd.getpwnam(text)
                return f'{USER}{entry.pw_uid}', entry.pw_name, 'user'
        entry = grp.getgrnam(text[6:] if text.startswith('group:') else text)
        return f'{GROUP}{entry.gr_gid}', entry.gr_name, 'group'
    except KeyError:
        raise ValueError('Unknown account') from None


def _read(connection, deadline, limit=MAX_MESSAGE):
    chunks, total = [], 0
    while True:
        connection.settimeout(max(0.01, deadline - time.monotonic()))
        try:
            chunk = connection.recv(65536)
        except socket.timeout:
            raise TimeoutError('Admin socket timed out') from None
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError('Admin message too large')
        chunks.append(chunk)


def _send(connection, data, deadline):
    connection.settimeout(max(0.01, deadline - time.monotonic()))
    connection.sendall(data)
    connection.shutdown(socket.SHUT_WR)


class SocketServer:
    """One request per connection: identify the peer, refuse it if ``admitted(identity)`` is false, read the request
    to end of stream, answer, close. ``handler(identity, raw) -> bytes``."""

    def __init__(self, path, handler, admitted, workers=16, timeout=10, group=None):
        _linux()
        self.path, self.handler, self.admitted, self.timeout = str(path), handler, admitted, timeout
        self.group = admin_group() if group is None else group
        self._listener, self._thread, self._stopping = None, None, threading.Event()
        self._workers, self._lock = set(), threading.Lock()
        self._slots = threading.BoundedSemaphore(workers)

    def _claim(self):
        """Remove a stale socket left by a crashed server; refuse to touch anything else at that path."""
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return
        import stat
        if not stat.S_ISSOCK(info.st_mode):
            raise FileExistsError('Admin socket path is not a socket')
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1)
            probe.connect(self.path)
        except (ConnectionRefusedError, FileNotFoundError):
            os.unlink(self.path)
            return
        except OSError:
            pass
        finally:
            probe.close()
        raise FileExistsError('Another server is listening on the admin socket')

    def start(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._claim()
        temporary = f'{self.path}.{os.getpid()}.new'
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(temporary)
            gid = group_id(self.group) if self.group else None
            os.chmod(temporary, 0o660 if gid is not None else 0o600)
            if gid is not None:
                with contextlib.suppress(PermissionError):      # not a member: the socket stays owner-only (0660 own group)
                    os.chown(temporary, -1, gid)
            listener.listen(64)
            os.rename(temporary, self.path)
        except BaseException:
            listener.close()
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._accept, name='Lifetime-admin-socket', daemon=True)
        self._thread.start()
        return self

    def _accept(self):
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopping.is_set():
                    return
                continue
            while not self._slots.acquire(timeout=0.1):
                if self._stopping.is_set():
                    connection.close()
                    return
            worker = threading.Thread(target=self._serve, args=(connection,), name='Lifetime-admin-socket-client',
                                      daemon=True)
            with self._lock:
                self._workers.add(worker)
            worker.start()

    def _serve(self, connection):
        deadline = time.monotonic() + self.timeout
        try:
            _, uid, gid = peer_credentials(connection)
            identity = identity_for(uid, gid)
            if not self.admitted(identity):
                return          # like a DACL refusal: nothing read, nothing answered
            raw = _read(connection, deadline)
            reply = self.handler(identity, raw)
            _send(connection, reply, time.monotonic() + self.timeout)
        except Exception:
            pass        # never echo identities, paths or request contents
        finally:
            connection.close()
            with self._lock:
                self._workers.discard(threading.current_thread())
            self._slots.release()

    def stop(self):
        if self._listener is None:
            return
        self._stopping.set()
        if self._thread is not None:
            self._thread.join()
        self._listener.close()
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(self.timeout + 1)
        with contextlib.suppress(OSError):
            if os.path.exists(self.path):
                os.unlink(self.path)
        self._listener = None


def trusted_server_uids():
    return {0, os.getuid()} | ({user_id(SERVICE_USER)} - {None})


def call(path, payload, timeout=10):
    """Send one request and return the reply. FileNotFoundError: no socket (or nobody listening);
    PermissionError: not admitted, or the listener is not the server."""
    _linux()
    deadline = time.monotonic() + timeout
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        try:
            connection.connect(path)
        except (FileNotFoundError, ConnectionRefusedError):
            raise FileNotFoundError('No identity-bound admin channel') from None
        except PermissionError:
            raise PermissionError('This account may not use the admin socket') from None
        if peer_credentials(connection)[1] not in trusted_server_uids():
            raise PermissionError('The admin socket is not owned by the server')
        try:
            _send(connection, payload, deadline)
            reply = _read(connection, deadline)
        except (BrokenPipeError, ConnectionResetError):
            reply = b''         # the server hung up without reading: this account has no role
        if not reply:
            raise PermissionError('This Unix account has no Lifetime Server role')
        return reply
    finally:
        connection.close()
