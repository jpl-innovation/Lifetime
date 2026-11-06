"""Use installed fonts for meaningful Qt offscreen layout checks on Windows."""
import os
from pathlib import Path
import sys

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication


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
