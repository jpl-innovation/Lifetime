# PyInstaller spec for the Linux builds (run by packaging/linux/build_linux.sh inside an old-glibc container).
#
# dist/Lifetime/        the desktop bundle: Lifetime (window), LifetimeTracker, LifetimeImport, LifetimeAgent and
#                       LifetimeServerDashboard sharing one _internal folder (Qt with its X11 and Wayland plugins).
# dist/LifetimeServer/  the headless server: LifetimeServerRuntime, without Qt.
#
# Every lifetime_*.py module is bundled into every program as a hidden import, so a module imported only inside a
# function can never be left out (test_installed_modules checks that this spec keeps doing so). The Rust accelerator
# (lifetime_fast, when build_linux.sh installed it) goes into the headless server runtime only.
import glob
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, '..', '..'))
MODULES = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(ROOT, 'lifetime_*.py')))
WINDOWS_ONLY = ['win32service', 'win32serviceutil', 'servicemanager', 'pywintypes', 'win32timezone', 'winreg']
ASSETS = [(os.path.join(ROOT, 'assets', name), 'assets')
          for name in ('lifetime.png', 'lifetime-server.png', 'jpl_innovation.png', 'jpl_innovation_light.png')]


def qt_plugins():
    """X11 (xcb) and Wayland platform plugins with what they load, so the window opens on any desktop session."""
    import PyQt6
    base = os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins')
    found = []
    for folder in ('platforms', 'platformthemes', 'platforminputcontexts', 'xcbglintegrations', 'egldeviceintegrations',
                   'wayland-shell-integration', 'wayland-decoration-client', 'wayland-graphics-integration-client',
                   'iconengines', 'imageformats'):
        for path in glob.glob(os.path.join(base, folder, '*.so')):
            found.append((path, os.path.join('PyQt6', 'Qt6', 'plugins', folder)))
    return found


def analysis(script, qt, extra_datas=(), extra_binaries=(), extra_hidden=(), fast=False):
    return Analysis([os.path.join(ROOT, script)], pathex=[ROOT], binaries=list(extra_binaries),
                    datas=list(extra_datas), hiddenimports=MODULES + list(extra_hidden) + (['lifetime_fast'] if fast else []),
                    excludes=WINDOWS_ONLY + ([] if qt else ['PyQt6']) + ([] if fast else ['lifetime_fast']), noarchive=False)


def program(a, name):
    return EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name=name, console=True, strip=False, upx=False)


crypto = collect_all('cryptography')
tz = collect_all('tzdata')
websockets = collect_all('websockets')
server_datas = crypto[0] + tz[0] + websockets[0]
server_binaries = crypto[1] + tz[1] + websockets[1]
server_hidden = crypto[2] + tz[2] + websockets[2]

# ---- desktop bundle (LIFETIME_SERVER_ONLY=1 skips it, for architectures without PyQt6 wheels for glibc 2.28) ----
if os.environ.get('LIFETIME_SERVER_ONLY') != '1':
    gui = analysis('lifetime_gui.py', True, ASSETS, qt_plugins(), server_hidden)
    dashboard = analysis('lifetime_server_gui.py', True, ASSETS + server_datas, server_binaries, server_hidden)
    tracker = analysis('lifetime_tracker.py', False)
    importer = analysis('lifetime_import.py', False)
    agent = analysis('lifetime_agent.py', False, websockets[0], websockets[1], websockets[2])
    desktop = [(gui, 'Lifetime'), (dashboard, 'LifetimeServerDashboard'), (tracker, 'LifetimeTracker'),
               (importer, 'LifetimeImport'), (agent, 'LifetimeAgent')]
    executables = [program(a, name) for a, name in desktop]
    COLLECT(*executables,
            *[item for a, _ in desktop for item in (a.binaries, a.datas)],
            strip=False, upx=False, name='Lifetime')

# ---- headless server ----
runtime = analysis('lifetime_server.py', False, server_datas, server_binaries, server_hidden, fast=True)
COLLECT(program(runtime, 'LifetimeServerRuntime'), runtime.binaries, runtime.datas,
        strip=False, upx=False, name='LifetimeServer')
