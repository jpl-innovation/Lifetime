"""Atomic Windows DACL replacement is exercised with mocked Win32 APIs."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import lifetime_permissions as permissions


class WindowsACLTests(unittest.TestCase):
    def test_atomic_protected_dacl_precedes_icacls_without_reset(self):
        events = []
        descriptor_value = 0x1234
        advapi = SimpleNamespace()

        def convert(sddl, revision, output, reserved):
            events.append(('convert', sddl, revision))
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(descriptor_value)
            return 1

        def set_security(path, information, descriptor):
            events.append(('set', path, information, descriptor.value))
            return 1

        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW = Mock(side_effect=convert)
        advapi.SetFileSecurityW = Mock(side_effect=set_security)
        local_free = Mock(side_effect=lambda value: events.append(('free', value.value)))
        shell32 = SimpleNamespace(IsUserAnAdmin=Mock(return_value=1))
        fake_windll = SimpleNamespace(shell32=shell32,
                                      kernel32=SimpleNamespace(LocalFree=local_free))
        grants = ['*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private'
            path.touch()
            with patch.object(ctypes, 'windll', fake_windll, create=True), \
                    patch.object(ctypes, 'WinDLL', return_value=advapi, create=True), \
                    patch.object(permissions.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                    patch.object(permissions.subprocess, 'run', side_effect=lambda args, **kwargs:
                                 events.append(('icacls', args, kwargs))):
                permissions.windows_acl(path, 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)', grants)

        self.assertEqual([event[0] for event in events], ['convert', 'set', 'free', 'icacls'])
        self.assertEqual(events[1][1:], (str(Path(path).absolute()), 0x80000004, descriptor_value))
        self.assertEqual(events[2], ('free', descriptor_value))
        command = events[3][1]
        self.assertEqual(command, ['icacls', str(path), '/inheritance:r', '/grant:r', *grants])
        self.assertNotIn('/reset', command)
        self.assertTrue(events[3][2]['check'])


if __name__ == '__main__':
    unittest.main()
