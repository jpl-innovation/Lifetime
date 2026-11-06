"""Render Windows charts from a TEMPORARY COPY of the real ledger.

All application ledger reads and data-directory access are redirected to that
copy. Probes are read-only; no tracker, import, setup, or settings action runs.
"""
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['LIFETIME_REDUCE_MOTION'] = '1'

from PyQt6.QtCore import QPoint, QTimer
from PyQt6.QtGui import QFont, QFontDatabase
from qt_test_support import qt_app
import lifetime_gui as gui
import lifetime_tracker as tracker


def main():
    source = Path(os.environ['APPDATA'])/'Lifetime'/'ledger.json'
    shots = Path(__file__).parent/'shots'
    shots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lifetime-insights-') as directory:
        root = Path(directory)
        target = root/'ledger.json'
        shutil.copy2(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        reader = functools.partial(tracker.read_ledger, root)
        ledger = reader()
        app = qt_app()
        app.setStyle('Fusion')
        app.setFont(QFont('Segoe UI', 10))
        assert app.platformName() == 'windows'
        assert 'Segoe UI' in QFontDatabase.families()
        audit = dict(platform=app.platformName(), font='Segoe UI', real_days=len(ledger['daily']),
                     real_sessions=len(ledger['sessions']), isolated_ledger=True, captures=[])
        with patch.object(gui, 'read_ledger', reader), patch.object(gui, 'data_dir', return_value=root):
            settings = dict(completed=True, history=True, battery=True, resources=True,
                            graphics=True, network=True, wifi_name=False, rebuild=False, autostart=False)
            window = gui.Window(settings)
            window.timer.stop()
            window.navigation.group.button(2).click()
            window.history.ranges.setCurrentText('All')
            window.show()
            jobs = [(1280, 800, 2, 'insights-1280x800.png', False),
                    (1280, 800, 2, 'insights-1280x800-middle.png', 'cumulative'),
                    (1280, 800, 2, 'insights-1280x800-bottom.png', True),
                    (760, 560, 2, 'insights-760x560.png', False),
                    (760, 560, 2, 'insights-760x560-daily.png', 'daily'),
                    (760, 560, 2, 'insights-760x560-cumulative.png', 'cumulative'),
                    (760, 560, 2, 'insights-760x560-profile.png', 'profile'),
                    (760, 560, 2, 'insights-760x560-bottom.png', True),
                    (760, 560, 2, 'insights-table.png', 'daily'),
                    (1280, 800, 2, 'insights-30days.png', False),
                    (1280, 800, 1, 'system-sparklines.png', False)]

            def capture():
                width, height, page, filename, bottom = jobs.pop(0)
                window.history.ranges.setCurrentText('30 days' if filename == 'insights-30days.png' else 'All')
                window.history.cards[1].toggle.setChecked(filename == 'insights-table.png')
                window.resize(width, height)
                window.navigation.group.button(page).click()

                def save():
                    bar = window.scroll.verticalScrollBar()
                    if isinstance(bottom, str):
                        index = {'daily': 1, 'cumulative': 2, 'profile': 3}[bottom]
                        card = window.history.cards[index]
                        bar.setValue(card.mapTo(window.scroll.widget(), QPoint(0, 0)).y()-12)
                    else:
                        bar.setValue(bar.maximum() if bottom else 0)
                    app.processEvents()
                    assert window.grab().save(str(shots/filename))
                    audit['captures'].append(dict(file=filename, size=[width, height],
                                                   scroll=bar.value(), scroll_max=bar.maximum()))
                    if jobs:
                        QTimer.singleShot(80, capture)
                    else:
                        audit['spark_samples'] = {k: len(v.buffer.samples) for k, v in window.system.sparklines.charts.items()}
                        audit['spark_available'] = {k: sum(v is not None for _, _, v in chart.buffer.samples) for k, chart in window.system.sparklines.charts.items()}
                        audit['copy_unchanged'] = hashlib.sha256(target.read_bytes()).hexdigest() == digest
                        audit['live_ledger_matches_copy'] = hashlib.sha256(source.read_bytes()).hexdigest() == digest
                        assert audit['copy_unchanged']
                        (shots/'insights-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
                        window.close()
                        app.quit()
                QTimer.singleShot(180, save)

            # Collect actual readings; never backfill a five-minute fake series.
            QTimer.singleShot(16000, capture)
            app.exec()
        print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
