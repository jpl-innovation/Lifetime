"""Use installed fonts for meaningful Qt offscreen layout checks on Windows."""
import os
from pathlib import Path
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication


def wait_until(condition, timeout=3000):
    """Process events until condition() is true, or fail after timeout ms.

    Use this instead of a fixed QTest.qWait before asserting an animation's end state: on a busy machine (the full
    suite, a CI runner) Qt timers fire late, so a wait just longer than the animation is sometimes too short."""
    import time
    from PyQt6.QtTest import QTest
    deadline = time.monotonic() + timeout / 1000
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f'condition not met within {timeout} ms')
        QTest.qWait(10)


def qt_app():
    if QApplication.instance() is None:
        # Layout tests must not depend on a real window manager: a tiling or Wayland compositor
        # ignores resize() requests, so always render offscreen regardless of the user's session.
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        os.environ['QT_QPA_PLATFORMTHEME'] = ''
    app = QApplication.instance() or QApplication([])
    if sys.platform == 'win32' and 'Segoe UI' not in QFontDatabase.families():
        root = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'segoeuii.ttf'):
            QFontDatabase.addApplicationFont(str(root/name))
    return app


def dispose(widget):
    """Close a window or dialog and delete it now, while the application still exists.

    A widget only closed stays alive until Python's exit, which then tears Qt objects down in no particular order
    and, with background tasks or timers still attached, crashes the process after the tests have passed.
    """
    from lifetime_shell import shut_down
    from PyQt6 import sip
    if sip.isdeleted(widget):       # already disposed by the test itself
        return
    for timer in widget.findChildren(QTimer):
        timer.stop()
    widget.close()
    shut_down(QApplication.instance(), widget)
