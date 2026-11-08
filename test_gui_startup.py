"""Isolated startup repair behavior; does not access saved application data."""
import os
import subprocess
import sys
import unittest


class StartupRepairTests(unittest.TestCase):
    @unittest.skipUnless(__import__('importlib.util').util.find_spec('PyQt6'), 'PyQt6 is required')
    def test_slow_and_failed_repair(self):
        result = subprocess.run([sys.executable, __file__, '--child'], capture_output=True,
                                text=True, timeout=30,
                                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__' and '--child' in sys.argv:
    import tempfile
    temporary = tempfile.TemporaryDirectory()
    os.environ['LIFETIME_DATA_DIR'] = temporary.name
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from threading import Event
    from PyQt6.QtTest import QTest
    from qt_test_support import qt_app
    from lifetime_gui import Window

    app = qt_app()
    w = Window({'completed': True, 'history': True})
    w.refresh = lambda: setattr(w, 'refresh_count', getattr(w, 'refresh_count', 0) + 1)
    w.show()
    entered, release = Event(), Event()
    def slow_repair():
        entered.set()
        release.wait(5)
    w.start_daily_repair(slow_repair)
    assert entered.wait(2)
    app.processEvents()
    assert w.isVisible()
    assert w.technician_actions['Export report'].isEnabled() is False
    release.set()
    QTest.qWait(100)
    app.processEvents()
    assert w.technician_actions['Export report'].isEnabled() is True
    assert w.refresh_count == 1

    w._set_export_ready(False)
    w.start_daily_repair(lambda: (_ for _ in ()).throw(OSError('private detail')))
    QTest.qWait(100)
    app.processEvents()
    assert not w.technician_actions['Export report'].isEnabled()
    assert 'failed' in w.status.text().lower()
    assert 'private detail' not in w.status.text()
    w.close()
    from lifetime_shell import shut_down
    app.aboutToQuit.emit()
    shut_down(app, w)
    app.quit()
