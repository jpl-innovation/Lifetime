"""Render setup with installed Windows fonts and synthetic consent settings only."""
import json
import argparse
import os
from pathlib import Path
import sys
import subprocess
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtGui import QFont, QFontInfo, QFontDatabase
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from lifetime_gui import THEMES
from lifetime_setup import SetupDialog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('shots'))
    parser.add_argument('--capture', choices=['onboard-1', 'onboard-2', 'onboard-3', 'onboard-4', 'settings', 'onboard-compact'])
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Run on Windows to review the actual Segoe UI font.')
    shots = args.output
    shots.mkdir(parents=True, exist_ok=True)
    if not args.capture:
        captures = []
        # A fresh process for each view avoids offscreen backing-store damage
        # when several same-sized native dialogs are destroyed in one process.
        for name in ['onboard-1', 'onboard-2', 'onboard-3', 'onboard-4', 'settings', 'onboard-compact']:
            result = subprocess.run([sys.executable, __file__, '--output', str(shots), '--capture', name],
                                    capture_output=True, text=True, check=True)
            captures.append(json.loads(result.stdout))
        audit = dict(font='Segoe UI', platform=sys.platform, scale=os.environ.get('QT_SCALE_FACTOR', '1'), captures=captures)
        (shots/'setup-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        print(json.dumps(audit, indent=2))
        return
    app = QApplication([])
    # Qt's offscreen Windows plugin does not enumerate the system font store.
    # Load the actual installed files into this process; nothing is installed.
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    for filename in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'segoeuii.ttf'):
        QFontDatabase.addApplicationFont(str(fonts/filename))
    app.setFont(QFont('Segoe UI', 10))
    actual_font = QFontInfo(app.font()).family()
    if actual_font != 'Segoe UI':
        raise SystemExit('Segoe UI is not installed; refusing a substituted-font review.')
    with tempfile.TemporaryDirectory() as directory:
        current = dict(completed=True, history=True, network=True, resources=True) if args.capture == 'settings' else None
        dialog = SetupDialog(THEMES['dark'], current, directory)
        if args.capture == 'onboard-compact':
            dialog.resize(390, 480)
        else:
            dialog.resize(680, 850)
        if args.capture.startswith('onboard-') and args.capture[-1].isdigit():
            for key in ['history', 'battery', 'resources']:
                dialog.checks[key].setChecked(True)
            dialog.go(int(args.capture[-1])-1)
        dialog.show()
        QTest.qWait(100)
        path = shots/(args.capture+'.png')
        dialog.grab().save(str(path))
        audit = dict(path=str(path), font=actual_font, width=dialog.width(), height=dialog.height(),
                     dpr=dialog.devicePixelRatioF(), scroll=dialog.pages.currentWidget().verticalScrollBar().maximum())
        dialog.close()
    print(json.dumps(audit))


if __name__ == '__main__':
    main()
