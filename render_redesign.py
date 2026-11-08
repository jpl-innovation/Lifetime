"""Capture native Windows widgets using only the private repository data copy."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['LIFETIME_DATA_DIR'] = str(ROOT/'.redesign-data')
os.environ['LIFETIME_REDUCE_MOTION'] = '0'

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QFontInfo
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from lifetime_gui import Window
from lifetime_i18n import set_language, language


def main():
    if sys.platform != 'win32' or not (ROOT/'.redesign-data/ledger.json').is_file():
        raise SystemExit('Requires Windows and the existing private real-data copy.')
    app = QApplication([])
    if app.platformName() != 'windows':
        raise SystemExit('Requires native Windows Qt.')
    window = Window()
    window.show()
    QTest.qWait(2000)
    output = ROOT/'shots/redesign'
    output.mkdir(parents=True, exist_ok=True)
    audit = dict(platform=app.platformName(), font=QFontInfo(window.verify_button.font()).family(), captures=[])
    assert audit['font'] == 'Segoe UI', audit

    def save(name):
        path = output/(name+'.png')
        assert window.grab().save(str(path))
        audit['captures'].append(dict(file=path.name, size=[window.width(), window.height()],
            rail=window.sidebar.collapsed, page=window.pages.currentIndex(),
            scroll=window.scroll.verticalScrollBar().maximum(),
            scroll_value=window.scroll.verticalScrollBar().value(),
            dpr=window.devicePixelRatioF(), language=language(),
            language_selected=window.language_control.group.checkedId(),
            body_y=window.body.mapTo(window, QPoint()).y(),
            viewport=window.scroll.viewport().width(), canvas=window.scroll.widget().width(),
            indicator_y=window.sidebar.indicator_y))

    set_language('en')
    for width, height in ((1280, 800), (860, 640)):
        window.resize(width, height)
        for page, name in ((0, 'overview'), (2, 'activity'), (1, 'system'), (3, 'reports')):
            window.select_page(page)
            window.scroll.verticalScrollBar().setValue(0)
            QTest.qWait(320)
            save(f'{name}-{width}x{height}')
    window.resize(1280, 800)
    window.select_page(0)
    set_language('vi')
    QTest.qWait(320)
    save('overview-vi-1280x800')
    set_language('en')
    window.resize(760, 560)
    QTest.qWait(320)
    save('overview-rail-760x560')
    window.resize(390, 560)
    QTest.qWait(320)
    save('overview-390x560')
    window.resize(1280, 800)
    window.counting_toggle.click()
    QTest.qWait(320)
    save('overview-counting-1280x800')
    window.import_toggle.click()
    QTest.qWait(240)
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
    QTest.qWait(40)
    save('overview-counting-import-bottom-1280x800')
    window.import_toggle.click()
    window.counting_toggle.click()
    QTest.qWait(320)
    window.select_page(3)
    for elapsed, wait in ((40, 40), (100, 60), (180, 80)):
        QTest.qWait(wait)
        save(f'transition-reports-{elapsed}ms')
    window.resize(390, 560)
    set_language('vi')
    QTest.qWait(320)
    save('reports-vi-390x560')
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
    QTest.qWait(40)
    save('reports-vi-390x560-bottom')
    (output/'audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print(json.dumps(audit, indent=2))
    window.close()


if __name__ == '__main__':
    main()
