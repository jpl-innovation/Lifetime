"""Use installed fonts for meaningful Qt offscreen layout checks on Windows."""
import os
from pathlib import Path
import sys

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication


def qt_app():
    app = QApplication.instance() or QApplication([])
    if sys.platform == 'win32' and 'Segoe UI' not in QFontDatabase.families():
        root = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'segoeuii.ttf'):
            QFontDatabase.addApplicationFont(str(root/name))
    return app
