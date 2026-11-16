"""Thin, lazy pywin32 adapter for Lifetime Server; no desktop/client imports.

Install/start/stop/remove with pywin32's usual command line, for example
``python lifetime_server_service.py --startup auto install``. The SYSTEM service
uses %PROGRAMDATA%/Lifetime/Server and advertises https://localhost:8443 by
default. Configure LifetimeServer's Parameters registry key with Directory,
Address, Host (strings), Port and AdminPort (DWORDs) before starting to override.
The runtime also has a portable console entry point in lifetime_server.py.

How the service is hosted. pywin32's default host, pythonservice.exe, is moved into a virtual environment's root by
pywin32, where the SYSTEM account cannot find the Python DLL it needs, so the service never starts. A venv's own
python.exe is only a launcher that runs the real interpreter as a child process, and Windows refuses a service
dispatcher from a child process. So the service is registered to run the BASE interpreter with ``-E -s`` (no
environment variables, no user site) on this script with ``--service-host <venv>``; the script then adds the
environment's site-packages (which loads pywin32's DLL path) before it touches pywin32. ``--self-check`` runs exactly
that start-up path without contacting Windows, so the installer can fail early with a readable message.
"""
import os
from pathlib import Path
import sys
import threading

SERVICE_HOST = '--service-host'
SELF_CHECK = '--self-check'


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


def host_command():
    """(executable, arguments) Windows should run for the service, or (None, None) to keep pywin32's own host.

    A frozen (PyInstaller) build is its own host. Otherwise use the base interpreter, never a venv launcher."""
    if getattr(sys, 'frozen', False):
        return None, None
    executable = getattr(sys, '_base_executable', None) or sys.executable
    if Path(executable).name.lower() == 'pythonw.exe':      # a service has no window, but keep the console build
        executable = str(Path(executable).with_name('python.exe'))
    arguments = f'-E -s "{Path(__file__).resolve()}" {SERVICE_HOST}'
    if sys.prefix != sys.base_prefix:                       # running inside a virtual environment
        arguments += f' "{Path(sys.prefix).resolve()}"'
    return executable, arguments


def use_environment(environment):
    """Put a virtual environment's packages first on sys.path, processing its .pth files (pywin32 needs them)."""
    import site
    packages = Path(environment) / 'Lib' / 'site-packages'
    if not packages.is_dir():
        raise SystemExit(f'Lifetime Server service: the Python environment {environment} has no site-packages.')
    before = list(sys.path)
    site.addsitedir(str(packages))
    added = [entry for entry in sys.path if entry not in before]
    sys.path[:] = added + [entry for entry in sys.path if entry not in added]
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


def service_class():
    """Import pywin32 only when explicitly running the service adapter."""
    import win32service
    import win32serviceutil
    from lifetime_server import ServerRuntime, error_kind

    class LifetimeServerService(win32serviceutil.ServiceFramework):
        _svc_name_ = 'LifetimeServer'
        _svc_display_name_ = 'Lifetime Server'
        _svc_description_ = 'Lifetime HTTPS reporting and local administration service'
        # The class is constructed lazily but SCM must import this module's
        # module-level factory accessor, rather than a non-importable local name.
        _svc_reg_class_ = 'lifetime_server_service.LifetimeServerService'
        _exe_name_, _exe_args_ = host_command()

        def __init__(self, arguments):
            super().__init__(arguments)
            self._stopped = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stopped.set()

        def SvcDoRun(self):
            import servicemanager
            try:
                runtime = ServerRuntime(**_configuration())
                runtime.start()
            except Exception as error:   # say why in the Windows event log instead of a bare "stopped"
                # The kind of error only, as everywhere else: its text can carry paths and addresses.
                servicemanager.LogErrorMsg(f'Lifetime Server could not start: {error_kind(error)}')
                raise
            try:
                self._stopped.wait()
            finally:
                runtime.stop()

    if LifetimeServerService._exe_name_ is None:
        del LifetimeServerService._exe_name_, LifetimeServerService._exe_args_
    return LifetimeServerService


def __getattr__(name):
    if name == 'LifetimeServerService':
        result = service_class()
        globals()[name] = result
        return result
    raise AttributeError(name)


def self_check():
    """Everything the service needs at start, without talking to the service manager."""
    import servicemanager  # noqa: F401  (loads pywin32's DLLs)
    import win32service  # noqa: F401
    import lifetime_server  # noqa: F401
    service_class()
    print('Lifetime Server service host OK:', sys.executable)


def main():
    if sys.platform != 'win32':
        raise SystemExit('The Lifetime service adapter requires Windows and pywin32.')
    arguments = sys.argv[1:]
    hosted = bool(arguments) and arguments[0] == SERVICE_HOST
    if hosted:
        arguments = arguments[1:]
        if arguments and arguments[0] != SELF_CHECK:
            use_environment(arguments.pop(0))
    try:
        if arguments == [SELF_CHECK]:
            self_check()
            return
        import win32serviceutil
        service = __getattr__('LifetimeServerService')
        if arguments and not hosted:
            win32serviceutil.HandleCommandLine(service)
        else:
            import servicemanager
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(service)
            servicemanager.StartServiceCtrlDispatcher()
    except ImportError as error:
        raise SystemExit(f'Install the server-only pywin32 dependency to use the service ({error}).') from None


if __name__ == '__main__':
    main()
