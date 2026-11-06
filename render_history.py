"""Render isolated synthetic history fixtures; never reads or writes the live ledger."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QPA_PLATFORMTHEME'] = ''
os.environ['QT_STYLE_OVERRIDE'] = 'Fusion'
os.environ['LIFETIME_REDUCE_MOTION'] = '1'
import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
import lifetime_gui as gui
import lifetime_tracker as tracker

app = QApplication([])
iteration = sys.argv[1] if len(sys.argv) > 1 else 'final'
for theme in ('dark', 'light'):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(gui.THEMES[theme]['bg']))
    app.setPalette(palette)
    for density, count in [('sparse', 1), ('rich', 90)]:
        ledger = tracker.new_ledger()
        today = dt.date.today()
        ledger['daily'] = {(today-dt.timedelta(days=i)).isoformat(): (2+(i*7)%13)*3600 if i%9 != 8 else 0 for i in range(count)}
        ledger['history_started'] = (today-dt.timedelta(days=count-1)).isoformat()
        ledger['hourly'] = [max(0, 12-abs(i-21)*2)*1800*count for i in range(24)]
        ledger['sessions'] = [{'start': today.isoformat()+'T09:00:00', 'seconds': (i+1)*1200} for i in range(min(count, 100))]
        ledger['total_seconds'] = sum(ledger['daily'].values())
        ledger['tracking_started'] = ledger['history_started']
        with patch.object(gui, 'read_ledger', return_value=ledger), patch.object(gui, 'data_dir', return_value=Path('shots/nonexistent-fixture')):
            for width, height in [(480, 640), (760, 560), (950, 1150), (1900, 1150)]:
                window = gui.Window()
                window.worker.stop()
                window.resize(width, height)
                window.navigation.group.button(2).click()
                window.show()
                app.processEvents()
                window.grab().save(f'shots/hist-{iteration}-{theme}-{density}-{width}x{height}.png')
                assert window.history.width() <= width
                if iteration == '3':
                    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
                    app.processEvents()
                    window.grab().save(f'shots/hist-bottom-{theme}-{density}-{width}x{height}.png')
                    for index, days in [(0, 7), (2, 90)]:
                        window.history.ranges.group.button(index).click()
                        assert len(window.history.daily.values) == days
                    window.navigation.group.button(0).click()
                    assert not window.overview.isHidden() and window.history.isHidden()
                    app.processEvents()
                    window.grab().save(f'shots/hist-overview-{theme}-{density}-{width}x{height}.png')
                window.close()
                window.deleteLater()
                app.processEvents()
print('Rendered 16 history screenshots:', iteration)
