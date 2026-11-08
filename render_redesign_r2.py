"""R2 native Windows captures, isolated to the existing repository data copy."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['QT_SCREEN_SCALE_FACTORS'] = '1'
os.environ['LIFETIME_DATA_DIR'] = str(ROOT / '.redesign-data')
os.environ['LIFETIME_REDUCE_MOTION'] = '1'

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFontInfo, QGuiApplication
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from lifetime_gui import Window
from lifetime_i18n import set_language


def main():
    ledger = ROOT / '.redesign-data/ledger.json'
    if sys.platform != 'win32' or not ledger.is_file():
        raise SystemExit('Requires Windows and the existing private repository ledger.')
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.Floor)
    app = QApplication([])
    assert app.platformName() == 'windows'
    window = Window()
    window.show()
    QTest.qWait(14000)  # Actual local probe samples, never backfilled.
    output = ROOT / 'shots/redesign'
    output.mkdir(parents=True, exist_ok=True)
    audit = dict(platform=app.platformName(), font=QFontInfo(window.font()).family(), captures=[])
    assert audit['font'] == 'Segoe UI'

    def save(name, target=None):
        bar = window.scroll.verticalScrollBar()
        if target is not None:
            bar.setValue(target.mapTo(window.scroll.widget(), QPoint()).y()-16)
        else:
            bar.setValue(0)
        QTest.qWait(80)
        shot = window.grab()
        assert (shot.width(), shot.height()) == (window.width(), window.height())
        assert shot.save(str(output / (name+'.png')))
        audit['captures'].append(dict(file=name+'.png', size=[window.width(), window.height()],
                                     scroll=bar.value(), scroll_max=bar.maximum(),
                                     viewport=window.scroll.viewport().width(),
                                     canvas=window.scroll.widget().width()))

    for width, height in ((1280, 800), (860, 640)):
        window.resize(width, height)
        set_language('en')
        for page, name in ((1, 'system'), (2, 'activity')):
            window.select_page(page)
            QTest.qWait(240)
            save(f'{name}-{width}x{height}')
            if page == 1:
                save(f'{name}-computer-{width}x{height}', window.system.cards[3])
            else:
                save(f'{name}-daily-{width}x{height}', window.history.cards[1])
                save(f'{name}-secondary-{width}x{height}', window.history.cards[2])
                save(f'{name}-bottom-{width}x{height}', window.history.cards[4])
            panel = window.system if page == 1 else window.history
            panel.data_toggle.setChecked(True)
            QTest.qWait(80)
            save(f'{name}-data-{width}x{height}', window.system.sparklines if page == 1 else None)
            panel.data_toggle.setChecked(False)
        set_language('vi')
        QTest.qWait(180)
        save(f'activity-vi-{width}x{height}')
        save(f'activity-vi-daily-{width}x{height}', window.history.cards[1])
    window.resize(1280, 800)
    window.history.counting_toggle.click()
    QTest.qWait(220)
    save('activity-vi-counting-1280x800')
    audit['spark_samples'] = {key: len(chart.buffer.samples) for key, chart in window.system.sparklines.charts.items()}
    audit['ledger_unchanged'] = hashlib.sha256(ledger.read_bytes()).hexdigest() == before
    assert audit['ledger_unchanged']
    (output / 'r2-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print(json.dumps(audit, indent=2))
    window.close()


if __name__ == '__main__':
    main()
