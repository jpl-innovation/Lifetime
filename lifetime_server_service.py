"""Thin, lazy pywin32 adapter for Lifetime Server; no desktop/client imports.

Install/start/stop/remove with pywin32's usual command line, for example
``python lifetime_server_service.py --startup auto install``. The SYSTEM service
uses %PROGRAMDATA%/Lifetime/Server and advertises https://localhost:8443 by
default. Configure LifetimeServer's Parameters registry key with Directory,
Address, Host (strings), Port and AdminPort (DWORDs) before starting to override.
The runtime also has a portable console entry point in lifetime_server.py.

Actual Windows SCM installation, startup and packaging remain unverified.
"""
import os
from pathlib import Path
import sys
import threading


def _configuration():
    import winreg
    configuration = {'directory': str(Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData'))
                                     / 'Lifetime' / 'Server')}
    names = {'Directory': 'directory', 'Address': 'address', 'Host': 'host',
             'Port': 'port', 'AdminPort': 'admin_port'}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r'SYSTEM\CurrentControlSet\Services\LifetimeServer\Parameters')
    except FileNotFoundError:
        return configuration
    with key:
        for source, target in names.items():
            try:
                value, kind = winreg.QueryValueEx(key, source)
            except FileNotFoundError:
                continue
            expected = winreg.REG_DWORD if target in ('port', 'admin_port') else winreg.REG_SZ
            if kind != expected:
                raise ValueError('Invalid service configuration')
            configuration[target] = value
    return configuration


def service_class():
    """Import pywin32 only when explicitly running the service adapter."""
    import win32service
    import win32serviceutil
    from lifetime_server import ServerRuntime

    class LifetimeServerService(win32serviceutil.ServiceFramework):
        _svc_name_ = 'LifetimeServer'
        _svc_display_name_ = 'Lifetime Server'
        _svc_description_ = 'Lifetime HTTPS reporting and local administration service'
        # The class is constructed lazily but SCM must import this module's
        # module-level factory accessor, rather than a non-importable local name.
        _svc_reg_class_ = 'lifetime_server_service.LifetimeServerService'

        def __init__(self, arguments):
            super().__init__(arguments)
            self._stopped = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stopped.set()

        def SvcDoRun(self):
            runtime = ServerRuntime(**_configuration())
            try:
                runtime.start()
                self._stopped.wait()
            finally:
                runtime.stop()

    return LifetimeServerService


def __getattr__(name):
    if name == 'LifetimeServerService':
        result = service_class()
        globals()[name] = result
        return result
    raise AttributeError(name)


def main():
    if sys.platform != 'win32':
        raise SystemExit('The Lifetime service adapter requires Windows and pywin32.')
    try:
        import win32serviceutil
        service = __getattr__('LifetimeServerService')
        if len(sys.argv) > 1:
            win32serviceutil.HandleCommandLine(service)
        else:
            import servicemanager
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(service)
            servicemanager.StartServiceCtrlDispatcher()
    except ImportError:
        raise SystemExit('Install the server-only pywin32 dependency to use the service.') from None


if __name__ == '__main__':
    main()
