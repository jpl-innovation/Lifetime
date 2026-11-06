"""Capture real Windows widgets and local OS data, without writing the ledger."""
import json
import os
from pathlib import Path
import sys

os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['LIFETIME_REDUCE_MOTION'] = '1'
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from lifetime_gui import Window


def main():
    app = QApplication(sys.argv)
    if app.platformName() != 'windows':
        raise RuntimeError('Dashboard captures require the native Windows platform')
    window = Window()
    window.resize(1280, 800)
    window.show()
    shots = Path(__file__).parent / 'shots'
    shots.mkdir(exist_ok=True)
    audit = {'platform': app.platformName(), 'font': window.font().family(), 'captures': []}
    jobs = [(1280, 800, 0, 'dash-overview.png'), (1280, 800, 1, 'dash-system.png'),
            (760, 560, 0, 'dash-overview-760x560.png'), (760, 560, 1, 'dash-system-760x560.png'),
            (1280, 800, 2, 'dash-history.png'),
            (1280, 800, 1, 'dash-system-bottom.png'),
            (760, 560, 1, 'dash-system-760x560-bottom.png'),
            (760, 560, 2, 'dash-history-760x560-bottom.png')]

    def capture():
        width, height, index, filename = jobs.pop(0)
        window.resize(width, height)
        window.navigation.group.button(index).click()
        def save():
            scroll = window.scroll.verticalScrollBar()
            scroll.setValue(scroll.maximum() if '-bottom' in filename else 0)
            assert window.grab().save(str(shots / filename))
            audit['captures'].append({'file': filename, 'size': [window.width(), window.height()],
                                      'scroll_maximum': window.scroll.verticalScrollBar().maximum()})
            if jobs:
                QTimer.singleShot(150, capture)
            else:
                audit['signals'] = window.worker.values
                audit['verification'] = window.verification.text()
                (shots/'dash-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
                window.close()
                app.quit()
        QTimer.singleShot(600, save)

    # Exercise the actual Verify action. Allow a full fast-counter delta and all
    # slow probes to settle before the first native QWidget.grab().
    QTimer.singleShot(500, window.verify)
    QTimer.singleShot(12500, capture)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
