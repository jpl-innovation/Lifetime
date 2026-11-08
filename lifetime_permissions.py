"""Fail-closed private/public-status permissions; standard library only.

Windows DACL replacement is atomic, then icacls enforces the same explicit grants.
There is no /reset step, inherited-permission exposure or production bypass.
Windows writers must be elevated Administrators or SYSTEM. POSIX uses 0700/0600.
"""
import os
from pathlib import Path
import stat
import subprocess
import sys


def windows_acl(path, sddl, grants):
    import ctypes
    from ctypes import wintypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError('Lifetime machine/server changes require an elevated administrator or SYSTEM')
    descriptor = ctypes.c_void_p()
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    apply = advapi.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(str(Path(path).absolute()), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(descriptor)
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', *grants], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def _regular(path, directory):
    path = Path(path)
    info = path.lstat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or path.is_symlink()
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
            or (not directory and info.st_nlink != 1)):
        raise ValueError('Private material requires a regular, non-linked path')
    return path


def protect_directory(path):
    path = _regular(path, True)
    if sys.platform == 'win32':
        windows_acl(path, 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)',
                    ['*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)'])
    else:
        os.chmod(path, 0o700, follow_symlinks=False)


def protect_private(path):
    path = _regular(path, False)
    if sys.platform == 'win32':
        windows_acl(path, 'D:P(A;;FA;;;BA)(A;;FA;;;SY)',
                    ['*S-1-5-32-544:(F)', '*S-1-5-18:(F)'])
    else:
        os.chmod(path, 0o600, follow_symlinks=False)
