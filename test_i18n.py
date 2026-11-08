"""Catalogue integrity and live language changes, using isolated preferences."""
import ast
import datetime as dt
import os
from pathlib import Path
from string import Formatter
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

import lifetime_i18n as i18n


class CatalogueTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('en')

    def test_all_keys_exist_and_format_signatures_match(self):
        self.assertEqual(set(i18n.TABLES['en']), set(i18n.TABLES['vi']))
        def fields(value):
            return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(value) if name is not None)
        for key in i18n.EN:
            self.assertTrue(i18n.EN[key], key)
            self.assertTrue(i18n.VI[key], key)
            self.assertEqual(fields(i18n.EN[key]), fields(i18n.VI[key]), key)

    def test_literal_translation_references_exist(self):
        for path in Path(__file__).parent.glob('lifetime_*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args:
                    key = node.args[0]
                    if isinstance(key, ast.Constant):
                        self.assertIn(key.value, i18n.EN, (path.name, node.lineno))

    def test_vietnamese_dates_weekdays_and_credit(self):
        i18n.set_language('vi')
        self.assertEqual(i18n.date_text(dt.date(2026, 11, 6)), '6 thg 11, 2026')
        self.assertEqual(i18n.weekdays(), ('T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'))
        self.assertIn('Phát triển bởi JPL Innovation', i18n.tr('Lifetime · Made by JPL Innovation'))

    def test_os_ui_locale_default(self):
        with patch.object(i18n.sys, 'platform', 'linux'), patch.object(i18n.locale, 'getlocale', return_value=('vi_VN', 'UTF-8')):
            self.assertEqual(i18n.default_language(), 'vi')
        # ctypes.windll only exists on Windows, so create it for this check on every platform.
        with patch.object(i18n.sys, 'platform', 'win32'), patch('ctypes.windll', create=True) as windll:
            windll.kernel32.GetUserDefaultUILanguage.return_value = 0x042A
            self.assertEqual(i18n.default_language(), 'vi')


class LiveLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_test_support import qt_app
        cls.app = qt_app()

    def test_window_settings_tables_and_setup_relabel_without_restart(self):
        import lifetime_gui as gui
        import lifetime_settings as settings
        import lifetime_tracker as tracker
        from lifetime_setup import SetupDialog
        from PyQt6.QtWidgets import QLabel
        i18n.set_language('en')
        with tempfile.TemporaryDirectory() as root, patch.object(gui, 'data_dir', return_value=Path(root)):
            ledger = tracker.new_ledger()
            ledger['daily'] = {dt.date.today().isoformat(): 3600}
            tracker.atomic_json(Path(root)/'ledger.json', ledger)
            settings.save_settings(dict(completed=True, history=True, battery=True, language='en'), root)
            window = gui.Window(settings.load_settings(root))
            window.worker.timer.stop()
            window.timer.stop()
            window.resize(760, 560)
            window.show()
            self.app.processEvents()
            window.history.cards[0].toggle.click()
            window.switch_language('vi')
            self.app.processEvents()
            for width, height in ((760, 560), (1280, 800)):
                window.resize(width, height)
                self.app.processEvents()
                for card in window.cards.values():
                    self.assertTrue(card.rect().contains(card.value.geometry()), (width, card.value.geometry()))
            self.assertEqual(window.navigation.group.button(0).text(), 'Tổng quan')
            self.assertEqual(window.verify_button.text(), 'Đối chiếu')
            self.assertEqual(self.app.translate('QPlatformTheme', '&Yes'), '&Có')
            self.assertEqual(window.history.ranges.itemText(0), '30 ngày')
            self.assertEqual(window.history.cards[0].table.horizontalHeaderItem(0).text(), 'Ngày địa phương')
            self.assertTrue(any(label.text() == str(i18n.tr('Earliest known date')) for label in window.findChildren(QLabel)))
            self.assertEqual(settings.load_settings(root)['language'], 'vi')
            self.assertGreaterEqual(window.language_control.group.button(1).width(), 40)
            window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
            self.assertTrue(window.language_control.isVisible())
            self.assertLess(window.language_control.mapTo(window, window.language_control.rect().topLeft()).y(), 80)
            dialog = SetupDialog(gui.THEMES['dark'], settings.load_settings(root), root, window)
            self.assertEqual(dialog.pages.count(), 5)
            self.assertEqual(dialog.progress.text(), 'Bước 1 / 5')
            dialog.language_buttons['en'].click()
            self.assertEqual(window.navigation.group.button(0).text(), 'Overview')
            self.assertEqual(dialog.next.text(), 'Next')
            self.assertEqual(dialog.progress.text(), 'Step 1 of 5')
            self.assertEqual(settings.load_settings(root)['language'], 'en')
            self.assertTrue(dialog.checks['history'].isChecked())
            dialog.close()
            window.close()
            dialog.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_language_preference_does_not_grant_tracking_consent(self):
        import lifetime_gui as gui
        from lifetime_setup import SetupDialog
        from lifetime_settings import load_settings
        with tempfile.TemporaryDirectory() as root:
            dialog = SetupDialog(gui.THEMES['dark'], dict(language='en'), root)
            dialog.language_buttons['vi'].click()
            self.assertFalse(load_settings(root)['completed'])
            self.assertEqual(load_settings(root)['language'], 'vi')
            dialog.language_buttons['en'].click()
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_gui_startup_repairs_a_temporary_ledger_once(self):
        import lifetime_tracker as tracker
        from lifetime_settings import save_settings
        code = '''
import runpy
from PyQt6 import QtWidgets
from PyQt6.QtCore import QTimer
import datetime as dt
import lifetime_tracker as tracker
# Supply fixed boot evidence so the result does not depend on this machine's real uptime
# (a freshly started CI machine cannot hold 153 awake hours and would show a blocking dialog).
original_repair = tracker.repair_daily
end = dt.datetime.fromisoformat('2026-11-06T19:00:00').timestamp()
evidence = (end-153*3600, [(end-153*3600, end)], True)
tracker.repair_daily = lambda directory=None, given=None: original_repair(directory, evidence if given is None else given)
BaseApplication = QtWidgets.QApplication
class AutoCloseApplication(BaseApplication):
    def __init__(self, *args):
        super().__init__(*args)
        # Wait long enough for slow CI machines to build the window before closing it, then always quit.
        QTimer.singleShot(2500, lambda: (self.closeAllWindows(), self.quit()))
QtWidgets.QApplication = AutoCloseApplication
runpy.run_path('lifetime_gui.py', run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'ledger.json'
            ledger = tracker.new_ledger()
            ledger.update(total_seconds=153*3600, daily={'2026-11-06': 153*3600},
                          unattributed_seconds=153*3600,
                          sessions=[dict(start='2026-11-06T19:00:00', seconds=153*3600)])
            tracker.atomic_json(path, ledger)
            original = path.read_bytes()
            save_settings(dict(completed=True, language='en', history=True), root)
            environment = {**os.environ, 'LIFETIME_DATA_DIR': root, 'QT_QPA_PLATFORM': 'offscreen',
                           'LIFETIME_REDUCE_MOTION': '1'}
            for run in range(2):
                try:
                    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                            env=environment, capture_output=True, text=True, timeout=60)
                except subprocess.TimeoutExpired as error:
                    self.fail(f'GUI did not exit. stdout={error.stdout!r} stderr={error.stderr!r}')
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                repaired = tracker.read_ledger(root)
                self.assertEqual(repaired['total_seconds'], ledger['total_seconds'])
                self.assertEqual(repaired['daily_repair_version'], 1)
                self.assertLessEqual(max(repaired['daily'].values()), 86400)
                self.assertEqual((Path(root)/'ledger.json.bak-before-daily-repair').read_bytes(), original)
                if run == 0:
                    first = path.read_bytes()
                else:
                    self.assertEqual(path.read_bytes(), first)


if __name__ == '__main__':
    unittest.main()
