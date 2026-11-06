"""Exercise history interaction in an isolated 200% Qt process."""
import importlib.util
import os
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest

class HistoryGuiTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'PyQt6 is required')
    def test_history_gui(self):
        result = subprocess.run([sys.executable, __file__, '--child'], capture_output=True, text=True,
                                timeout=30, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__' and '--child' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QPA_PLATFORMTHEME'] = ''
    os.environ['QT_STYLE_OVERRIDE'] = 'Fusion'
    os.environ['QT_SCALE_FACTOR'] = '2'
    os.environ['LIFETIME_REDUCE_MOTION'] = '0'
    from pathlib import Path
    from unittest.mock import patch
    from PyQt6.QtWidgets import QApplication, QToolTip
    from PyQt6.QtTest import QTest
    import lifetime_gui as gui
    import lifetime_tracker as tracker
    from qt_test_support import qt_app

    app = qt_app()
    ledger = tracker.new_ledger()
    with patch.object(gui, 'read_ledger', return_value=ledger), patch.object(gui, 'data_dir', return_value=Path('shots/nonexistent-fixture')):
        w = gui.Window(dict(completed=True, history=True))
        w.resize(480, 640)
        w.show()
        w.navigation.group.button(2).click()
        app.processEvents()
        import datetime as dt
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        from lifetime_charts import RANGES
        chart = w.history.daily
        def hover(widget, point):
            # Offscreen's virtual screen is smaller than a 2x window. Dispatch
            # logical Qt coordinates so hover isn't clipped by its OS cursor.
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
                                QPointF(widget.mapToGlobal(point)), Qt.MouseButton.NoButton,
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget, event)
        assert chart.message == 'Needs 7 days of data'
        # The old reusable chart keeps its existing one-time/reduced-motion
        # checks; Insights intentionally adds no entrance animation.
        legacy = gui.BarChart()
        legacy.show()
        assert legacy.entrance.duration() == 350
        QTest.qWait(400)
        assert legacy.progress == 1
        animation = legacy.entrance
        legacy.hide()
        legacy.show()
        assert legacy.entrance is animation
        legacy.close()

        today = dt.date.today()
        ledger['daily'] = {(today-dt.timedelta(days=i)).isoformat(): (i%18)*3600 for i in range(1100)}
        ledger['sessions'] = [dict(start=(today-dt.timedelta(days=i)).isoformat()+'T09:00:00', seconds=3600, imported=True) for i in range(100)]
        ledger['total_seconds'] = sum(ledger['daily'].values())
        ledger['history_started'] = min(ledger['daily'])
        w.history.set_ledger(ledger)
        for selection, count in zip(RANGES, (30, 90, 365, 1100)):
            w.history.ranges.setCurrentText(selection)
            assert len(chart.values) == count
            assert len(w.history.running.values) == count
            assert w.history.running.values[-1] == ledger['total_seconds']/3600
            assert len(w.history.cards[0].rows) == count
            assert sum(w.history.distribution.values) == min(count, 100)
            assert w.history.heatmap.start == chart.dates[0]
        w.history.ranges.setCurrentText('30 days')
        w.scroll.ensureWidgetVisible(chart)
        app.processEvents()
        # Hover lanes cover the plot even when the marks are only a few px.
        hover(chart, chart.plot().center().toPoint())
        app.processEvents()
        assert ' h ' in QToolTip.text(), QToolTip.text()
        assert chart.hover is not None
        QToolTip.hideText()
        for card in w.history.cards:
            card.toggle.click()
            app.processEvents()
            assert not card.table.isHidden()
            assert card.table.rowCount() == len(card.rows)
            assert card.viewport.isHidden()
            card.toggle.click()
            assert card.table.isHidden()
        heat = w.history.heatmap
        w.scroll.ensureWidgetVisible(heat)
        app.processEvents()
        hover(heat, QPoint(50, 30+heat.start.weekday()*20))
        app.processEvents()
        assert '2026-' in QToolTip.text() or str(today.year) in QToolTip.text()
        QToolTip.hideText()
        assert w.history.width() < w.width()
        assert w.history.columns == 1
        # Three-year rendering exercises line reduction and calendar scrolling.
        w.history.ranges.setCurrentText('All')
        app.processEvents()
        assert w.history.cards[0].viewport.horizontalScrollBar().maximum() > 0
        w.history.running.grab()
        shot = w.grab()
        assert shot.devicePixelRatio() == 2
        shot.save(str(Path(tempfile.gettempdir()) / 'lifetime-insights-hidpi.png'))
        gui.REDUCE_MOTION = True
        reduced = gui.BarChart()
        reduced.show()
        assert reduced.progress == 1 and reduced.entrance is None
        reduced.close()
        w.close()
    print('Insights GUI checks passed: four ranges, six tables, hover, 3-year rendering, reduced motion, 2x HiDPI.')

if __name__ == '__main__' and '--child' not in sys.argv:
    unittest.main()
