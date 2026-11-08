"""Real Windows fonts and local evidence; source data is copied before app use."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\Dev\Lifetime-redesign\.redesign-data')
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['QT_SCREEN_SCALE_FACTORS'] = '1'
os.environ['LIFETIME_REDUCE_MOTION'] = '1'


def main():
    if sys.platform != 'win32' or not (SOURCE/'ledger.json').is_file():
        raise SystemExit('Requires Windows and the supplied private source copy')
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.iterdir() if p.is_file()}
    with tempfile.TemporaryDirectory(prefix='.health-render-',dir=ROOT) as folder:
        shutil.copytree(SOURCE,folder,dirs_exist_ok=True)
        os.environ['LIFETIME_DATA_DIR'] = folder
        from PyQt6.QtCore import Qt, QPoint
        from PyQt6.QtGui import QFontInfo, QGuiApplication
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtTest import QTest
        from lifetime_gui import Window
        from lifetime_i18n import set_language
        from lifetime_settings import load_settings, save_settings
        import lifetime_health as health
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.Floor)
        app=QApplication([])
        settings=load_settings(folder)
        settings.update(completed=True,health=True,language='en')
        save_settings(settings,folder)
        window=Window(settings)
        window.resize(1280,800); window.show(); window.select_page(4)
        deadline=time.monotonic()+18
        while window.health.jobs and time.monotonic()<deadline:
            QTest.qWait(50)
        assert app.platformName() == 'windows'
        assert QFontInfo(window.font()).family() == 'Segoe UI'
        output=ROOT/'shots/health'; output.mkdir(parents=True,exist_ok=True)
        data=dict(window.health.data)
        data['observed_at']=health.dt.datetime.now(health.UTC).isoformat()
        (output/'probes.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        audit=dict(platform=app.platformName(),font=QFontInfo(window.font()).family(),captures=[])
        def save(name,target=None):
            bar=window.scroll.verticalScrollBar()
            bar.setValue(target.mapTo(window.scroll.widget(),QPoint()).y()-16 if target else 0)
            QTest.qWait(100)
            assert window.grab().save(str(output/(name+'.png')))
            audit['captures'].append(dict(file=name+'.png',width=window.width(),height=window.height(),scroll=bar.value(),scroll_max=bar.maximum(),canvas=window.scroll.widget().width(),viewport=window.scroll.viewport().width()))
        for code in ('en','vi'):
            set_language(code); QTest.qWait(150)
            save('health-'+code+'-1280x800')
            save('health-'+code+'-battery-1280x800',window.health.battery_body)
            save('health-'+code+'-sleep-1280x800',window.health.sleep_average)
        set_language('en')
        window.resize(860,640); QTest.qWait(100); save('health-en-860x640')
        window.resize(390,640); QTest.qWait(100); save('health-en-390x640')
        window.resize(1280,800)
        window.health.set_settings(dict(settings,health=False)); QTest.qWait(100); save('health-off-en-1280x800')
        window.close(); app.processEvents()
        audit['source_unchanged']=all(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
        assert audit['source_unchanged']
        (output/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
        print(json.dumps(audit,indent=2))


if __name__ == '__main__': main()
