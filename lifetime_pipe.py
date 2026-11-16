"""Windows named-pipe channel for the local administration API, bound to the caller's Windows identity.

The server learns who is calling from the pipe itself: after the first message it impersonates the client at
*identification* level (the client opens the pipe with SECURITY_IDENTIFICATION, so the server can read the token but
never act as the client), reads the token's user SID and enabled groups, and reverts. A client cannot name itself.
The pipe refuses remote clients, its security descriptor admits only SYSTEM, Administrators, the server's own account
and the SIDs that hold a Lifetime role, and clients get no right to create pipe instances. Clients check that the pipe
is owned by SYSTEM, Administrators or themselves before sending anything (a pipe squatted by another user is refused).

Standard library only (ctypes); pywin32 is not needed. Every function here raises OSError on other systems.
"""
import ctypes
import sys
import threading
import time

PREFIX = '\\\\.\\pipe\\LifetimeServer-admin-'
MAX_MESSAGE = 1024 * 1024 + 4096
SYSTEM, ADMINISTRATORS = 'S-1-5-18', 'S-1-5-32-544'
CLIENT_ACCESS = 0x0012019B      # read, write data and attributes; never FILE_CREATE_PIPE_INSTANCE (0x4)
WRITE_DAC = 0x00040000          # open mode: lets the server replace the pipe's access list when a role changes
DACL_PROTECTED = 0x80000004     # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION

ERROR_FILE_NOT_FOUND, ERROR_ACCESS_DENIED, ERROR_BROKEN_PIPE, ERROR_PIPE_BUSY = 2, 5, 109, 231
ERROR_MORE_DATA, ERROR_PIPE_CONNECTED, ERROR_IO_PENDING, ERROR_NO_DATA = 234, 535, 997, 232
INVALID_HANDLE = ctypes.c_void_p(-1).value


def pipe_name(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid admin port')
    return PREFIX + str(port)


if sys.platform == 'win32':
    from ctypes import wintypes
    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _adv = ctypes.WinDLL('advapi32', use_last_error=True)
    _H, _D, _B, _P = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [('Internal', _P), ('InternalHigh', _P), ('Offset', _D), ('OffsetHigh', _D), ('hEvent', _H)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('nLength', _D), ('lpSecurityDescriptor', _P), ('bInheritHandle', _B)]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [('Sid', _P), ('Attributes', _D)]

    for _dll, _name, _args, _result in (
            (_k32, 'CreateNamedPipeW', [wintypes.LPCWSTR, _D, _D, _D, _D, _D, _D, _P], _H),
            (_k32, 'ConnectNamedPipe', [_H, _P], _B),
            (_k32, 'DisconnectNamedPipe', [_H], _B),
            (_k32, 'CreateFileW', [wintypes.LPCWSTR, _D, _D, _P, _D, _D, _H], _H),
            (_k32, 'WaitNamedPipeW', [wintypes.LPCWSTR, _D], _B),
            (_k32, 'SetNamedPipeHandleState', [_H, ctypes.POINTER(_D), _P, _P], _B),
            (_k32, 'ReadFile', [_H, _P, _D, _P, _P], _B),
            (_k32, 'WriteFile', [_H, _P, _D, _P, _P], _B),
            (_k32, 'GetOverlappedResult', [_H, _P, ctypes.POINTER(_D), _B], _B),
            (_k32, 'CancelIoEx', [_H, _P], _B),
            (_k32, 'CreateEventW', [_P, _B, _B, wintypes.LPCWSTR], _H),
            (_k32, 'SetEvent', [_H], _B),
            (_k32, 'WaitForMultipleObjects', [_D, ctypes.POINTER(_H), _B, _D], _D),
            (_k32, 'CloseHandle', [_H], _B),
            (_k32, 'GetCurrentThread', [], _H),
            (_k32, 'GetCurrentProcess', [], _H),
            (_k32, 'LocalFree', [_P], _P),
            (_adv, 'ImpersonateNamedPipeClient', [_H], _B),
            (_adv, 'RevertToSelf', [], _B),
            (_adv, 'OpenThreadToken', [_H, _D, _B, ctypes.POINTER(_H)], _B),
            (_adv, 'OpenProcessToken', [_H, _D, ctypes.POINTER(_H)], _B),
            (_adv, 'GetTokenInformation', [_H, ctypes.c_int, _P, _D, ctypes.POINTER(_D)], _B),
            (_adv, 'ConvertSidToStringSidW', [_P, ctypes.POINTER(wintypes.LPWSTR)], _B),
            (_adv, 'ConvertStringSidToSidW', [wintypes.LPCWSTR, ctypes.POINTER(_P)], _B),
            (_adv, 'LookupAccountSidW', [wintypes.LPCWSTR, _P, wintypes.LPWSTR, ctypes.POINTER(_D),
                                         wintypes.LPWSTR, ctypes.POINTER(_D), ctypes.POINTER(_D)], _B),
            (_adv, 'LookupAccountNameW', [wintypes.LPCWSTR, wintypes.LPCWSTR, _P, ctypes.POINTER(_D),
                                          wintypes.LPWSTR, ctypes.POINTER(_D), ctypes.POINTER(_D)], _B),
            (_adv, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
             [wintypes.LPCWSTR, _D, ctypes.POINTER(_P), _P], _B),
            (_adv, 'SetKernelObjectSecurity', [_H, _D, _P], _B),
            (_adv, 'GetSecurityInfo', [_H, ctypes.c_int, _D, ctypes.POINTER(_P), _P, _P, _P, ctypes.POINTER(_P)], _D)):
        _function = getattr(_dll, _name)
        _function.argtypes, _function.restype = _args, _result


def _windows():
    if sys.platform != 'win32':
        raise OSError('The identity-bound admin channel exists only on Windows')


def _error(code=None):
    return ctypes.WinError(ctypes.get_last_error() if code is None else code)


def _sid_string(pointer):
    text = wintypes.LPWSTR()
    if not _adv.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
        raise _error()
    try:
        return text.value
    finally:
        _k32.LocalFree(ctypes.cast(text, _P))


def _token_info(token, kind):
    size = _D()
    _adv.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not size.value or not _adv.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
        raise _error()
    return buffer


def _token_identity(token):
    """User SID and the groups that count for access: enabled and not deny-only (an unelevated
    administrator's Administrators group is deny-only, so it does not make that account an Admin)."""
    user = _SID_AND_ATTRIBUTES.from_buffer(_token_info(token, 1))           # TokenUser
    sid = _sid_string(user.Sid)
    raw = _token_info(token, 2)                                              # TokenGroups
    count = _D.from_buffer(raw).value
    groups = (_SID_AND_ATTRIBUTES * count).from_buffer(raw, ctypes.sizeof(_P))
    enabled = sorted({_sid_string(g.Sid) for g in groups if g.Attributes & 0x4 and not g.Attributes & 0x10})
    return dict(sid=sid, name=account_name(sid), groups=enabled)


def current_identity():
    """The calling process's own identity (the same shape the server reads from a pipe client)."""
    _windows()
    token = _H()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
        raise _error()
    try:
        return _token_identity(token)
    finally:
        _k32.CloseHandle(token)


def _lookup_sid(sid):
    """(DOMAIN\\name or the SID itself when Windows cannot name it, SID_NAME_USE or None)."""
    pointer = _P()
    if not _adv.ConvertStringSidToSidW(sid, ctypes.byref(pointer)):
        raise ValueError('Invalid SID')
    try:
        name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        sizes, use = (_D(256), _D(256)), _D()
        if not _adv.LookupAccountSidW(None, pointer, name, ctypes.byref(sizes[0]), domain, ctypes.byref(sizes[1]), ctypes.byref(use)):
            return sid, None
        return (f'{domain.value}\\{name.value}' if domain.value else name.value), use.value
    finally:
        _k32.LocalFree(pointer)


def account_name(sid):
    _windows()
    return _lookup_sid(sid)[0]


def lookup_account(text):
    """Resolve 'DOMAIN\\user', 'user', a group name or a SID string to (sid, display name, 'user' | 'group')."""
    _windows()
    if not isinstance(text, str) or not 1 <= len(text) <= 256 or any(ord(c) < 32 for c in text):
        raise ValueError('Invalid account name')
    if not text.upper().startswith('S-1-'):
        sid, size, domain, domain_size, use = ctypes.create_string_buffer(68), _D(68), ctypes.create_unicode_buffer(256), _D(256), _D()
        if not _adv.LookupAccountNameW(None, text, sid, ctypes.byref(size), domain, ctypes.byref(domain_size), ctypes.byref(use)):
            raise ValueError('Unknown account')
        text = _sid_string(sid)
    sid = text.upper()
    name, use = _lookup_sid(sid)
    return sid, name, 'user' if use == 1 else 'group'      # SidTypeUser; groups, aliases and well-known groups


def _event():
    handle = _k32.CreateEventW(None, True, False, None)
    if not handle:
        raise _error()
    return handle


def _wait(handle, overlapped, deadline, stop=None):
    """Finish one overlapped operation: (bytes, more data follows). Cancels it on the deadline or on stop."""
    events = (_H * 2)(overlapped.hEvent, stop or 0)
    remaining = max(0, int((deadline - time.monotonic()) * 1000))
    if _k32.WaitForMultipleObjects(2 if stop else 1, events, False, remaining) != 0:
        _k32.CancelIoEx(handle, ctypes.byref(overlapped))
        _k32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(_D()), True)  # the buffer must outlive the I/O
        raise TimeoutError('Admin pipe timed out')
    count = _D()
    if not _k32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(count), False):
        code = ctypes.get_last_error()
        if code == ERROR_MORE_DATA:
            return count.value, True
        raise _error(code)
    return count.value, False


def _io(handle, function, buffer, size, deadline, stop=None, expected=(ERROR_IO_PENDING,)):
    overlapped = _OVERLAPPED(hEvent=_event())
    try:
        if not function(handle, buffer, size, None, ctypes.byref(overlapped)):
            code = ctypes.get_last_error()
            if code == ERROR_MORE_DATA:
                return size, True
            if code not in expected:
                raise _error(code)
        return _wait(handle, overlapped, deadline, stop)
    finally:
        _k32.CloseHandle(overlapped.hEvent)


def _read(handle, deadline, stop=None, limit=MAX_MESSAGE):
    chunks, total = [], 0
    while True:
        buffer = ctypes.create_string_buffer(65536)
        count, more = _io(handle, _k32.ReadFile, buffer, len(buffer), deadline, stop)
        chunks.append(buffer.raw[:count])
        total += count
        if total > limit:
            raise ValueError('Admin message too large')
        if not more:
            return b''.join(chunks)


def _write(handle, data, deadline):
    buffer = ctypes.create_string_buffer(data, len(data))
    count, _ = _io(handle, _k32.WriteFile, buffer, len(data), deadline)
    if count != len(data):
        raise OSError('Admin pipe write was incomplete')


def _client_identity(handle):
    if not _adv.ImpersonateNamedPipeClient(handle):
        raise _error()
    token = _H()
    try:
        opened = _adv.OpenThreadToken(_k32.GetCurrentThread(), 0x0008, True, ctypes.byref(token))
        code = ctypes.get_last_error()
    finally:
        if not _adv.RevertToSelf():
            raise SystemExit('Lifetime could not stop impersonating an admin pipe client')
    if not opened:
        raise _error(code)
    try:
        return _token_identity(token)
    finally:
        _k32.CloseHandle(token)


class PipeServer:
    """Serves one request per connection: read a message, identify the caller, answer, wait for the client to close.

    ``handler(identity, raw) -> bytes``; ``descriptor() -> SDDL`` is read again for every new pipe instance, so a
    role granted or removed changes who may connect from the next connection on."""
    def __init__(self, name, handler, descriptor, workers=16, timeout=10):
        _windows()
        self.name, self.handler, self.descriptor, self.timeout = name, handler, descriptor, timeout
        self._first, self._thread, self._stop, self._listening = True, None, None, None
        self._workers, self._lock = set(), threading.Lock()
        self._slots = threading.BoundedSemaphore(workers)

    def _create(self):
        descriptor = _P()
        if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.descriptor(), 1, ctypes.byref(descriptor), None):
            raise _error()
        try:
            attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
            # duplex | overlapped | WRITE_DAC | first instance (fails if someone created the name first); message mode,
            # no remote clients
            flags = 0x3 | 0x40000000 | WRITE_DAC | (0x80000 if self._first else 0)
            handle = _k32.CreateNamedPipeW(self.name, flags, 0x4 | 0x2 | 0x8, 255, 65536, 65536, 0,
                                           ctypes.byref(attributes))
            if handle not in (None, INVALID_HANDLE) and not self._first:
                # Windows keeps ONE access list for the whole pipe, the one its first instance was created with: the
                # descriptor given to a later instance is ignored. Set it again here, or a role granted or removed
                # after the server started would never change who may connect until the next start.
                _adv.SetKernelObjectSecurity(handle, DACL_PROTECTED, descriptor)
        finally:
            _k32.LocalFree(descriptor)
        if handle in (None, INVALID_HANDLE):
            raise _error()
        self._first = False
        return handle

    def refresh(self):
        """Apply the current roles to the pipe now. Called after a role changes, so the account that was just given
        (or denied) a role is admitted (or refused) on its very next connection."""
        handle = self._listening
        descriptor = _P()
        if handle in (None, INVALID_HANDLE) or not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                self.descriptor(), 1, ctypes.byref(descriptor), None):
            return False
        try:
            return bool(_adv.SetKernelObjectSecurity(handle, DACL_PROTECTED, descriptor))
        finally:
            _k32.LocalFree(descriptor)

    def start(self):
        self._stop = _event()
        try:
            first = self._create()
        except BaseException:
            _k32.CloseHandle(self._stop)
            raise
        self._thread = threading.Thread(target=self._accept, args=(first,), name='Lifetime-admin-pipe', daemon=True)
        self._thread.start()
        return self

    def _connect(self, handle):
        overlapped = _OVERLAPPED(hEvent=_event())
        try:
            if _k32.ConnectNamedPipe(handle, ctypes.byref(overlapped)):
                return True
            code = ctypes.get_last_error()
            if code == ERROR_PIPE_CONNECTED:
                return True
            if code != ERROR_IO_PENDING:
                raise _error(code)
            events = (_H * 2)(overlapped.hEvent, self._stop)
            if _k32.WaitForMultipleObjects(2, events, False, 0xFFFFFFFF) != 0:
                _k32.CancelIoEx(handle, ctypes.byref(overlapped))
                _k32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(_D()), True)
                return False
            return bool(_k32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(_D()), False))
        finally:
            _k32.CloseHandle(overlapped.hEvent)

    def _stopping(self, milliseconds):
        return _k32.WaitForMultipleObjects(1, (_H * 1)(self._stop), False, milliseconds) == 0

    def _next(self):
        while True:
            try:
                return self._create()
            except OSError:
                if self._stopping(100):
                    return None

    def _accept(self, handle):
        # The next listening instance is created before a connected one is handed to its worker, so a client always
        # finds the pipe (busy at worst, never missing) and never falls back to the token by mistake.
        while handle is not None:
            self._listening = handle
            try:
                connected = self._connect(handle)
            except OSError:
                connected = None        # a client that left before we saw it: start this instance again
            if connected is False:
                _k32.CloseHandle(handle)
                return
            if not connected:
                _k32.DisconnectNamedPipe(handle)
                _k32.CloseHandle(handle)
                handle = self._next()
                continue
            while not self._slots.acquire(timeout=0.1):     # bounded workers; busy instances keep the name alive
                if self._stopping(0):
                    _k32.DisconnectNamedPipe(handle)
                    _k32.CloseHandle(handle)
                    return
            following = self._next()
            worker = threading.Thread(target=self._serve, args=(handle,), name='Lifetime-admin-pipe-client', daemon=True)
            with self._lock:
                self._workers.add(worker)
            worker.start()
            handle = following

    def _serve(self, handle):
        deadline = time.monotonic() + self.timeout
        try:
            raw = _read(handle, deadline, self._stop)
            identity = _client_identity(handle)
            reply = self.handler(identity, raw)
            deadline = time.monotonic() + self.timeout      # a slow operation (a backup) does not eat the reply's time
            _write(handle, reply, deadline)
            try:        # wait for the client to read the reply and close, so the reply is never discarded
                _read(handle, deadline, self._stop, limit=0)
            except (OSError, ValueError):
                pass
        except Exception:
            pass        # never echo identities, paths or request contents
        finally:
            _k32.DisconnectNamedPipe(handle)
            _k32.CloseHandle(handle)
            with self._lock:
                self._workers.discard(threading.current_thread())
            self._slots.release()

    def stop(self):
        if self._stop is None:
            return
        _k32.SetEvent(self._stop)
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(self.timeout + 1)
        _k32.CloseHandle(self._stop)
        self._stop = None


def _owner(handle):
    owner, descriptor = _P(), _P()
    code = _adv.GetSecurityInfo(handle, 6, 0x1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
    if code:
        raise _error(code)
    try:
        return _sid_string(owner)
    finally:
        _k32.LocalFree(descriptor)


def call(name, payload, timeout=10):
    """Send one request and return the reply. FileNotFoundError: no such pipe; PermissionError: not admitted."""
    _windows()
    deadline = time.monotonic() + timeout
    while True:
        # GENERIC_READ | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES; overlapped; identification-level impersonation only
        handle = _k32.CreateFileW(name, 0x80000000 | 0x2 | 0x100, 0, None, 3, 0x40000000 | 0x00100000 | 0x00010000, None)
        if handle not in (None, INVALID_HANDLE):
            break
        code = ctypes.get_last_error()
        if code == ERROR_FILE_NOT_FOUND:
            raise FileNotFoundError('No identity-bound admin channel')
        if code == ERROR_ACCESS_DENIED:
            raise PermissionError('This Windows account has no Lifetime Server role')
        if code != ERROR_PIPE_BUSY or time.monotonic() >= deadline:
            raise TimeoutError('Admin pipe is busy') if code == ERROR_PIPE_BUSY else _error(code)
        _k32.WaitNamedPipeW(name, max(1, int((deadline - time.monotonic()) * 1000)))
    try:
        if _owner(handle) not in (SYSTEM, ADMINISTRATORS, current_identity()['sid']):
            raise PermissionError('The admin pipe is not owned by the server')
        mode = _D(0x2)     # PIPE_READMODE_MESSAGE
        if not _k32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise _error()
        _write(handle, payload, deadline)
        return _read(handle, deadline)
    finally:
        _k32.CloseHandle(handle)
