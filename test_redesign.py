"""Dashboard shell regressions with isolated data and real Qt input events."""
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from qt_test_support import qt_app
import lifetime_gui as gui
from lifetime_i18n import set_language
from lifetime_settings import load_settings
from lifetime_tracker import new_ledger


class DashboardShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.ledger = dict(new_ledger(), imported={'seconds': 0})   # a user whose past history was already rebuilt
        self.patches = [patch.object(gui, 'data_dir', return_value=self.directory),
                        patch.object(gui, 'read_ledger', return_value=self.ledger)]
        for p in self.patches:
            p.start()
        settings = dict(load_settings(self.directory), completed=True, history=True, language='en')
        self.w = gui.Window(settings)
        self.w.timer.stop()
        self.w.worker.timer.stop()
        self.w.show()
        self.w.activateWindow()
        self.app.processEvents()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_click_keyboard_and_shortcuts_follow_visual_order(self):
        # Legacy IDs remain stable, while the new sidebar has the requested order.
        self.assertEqual([row.text() for row in self.w.sidebar.rows],
                         ['Overview', 'Activity', 'System', 'Health', 'Connect', 'Reports', 'Settings'])
        first = self.w.navigation.group.button(0)
        first.setFocus()
        QTest.keyClick(first, Qt.Key.Key_Down)
        activity = self.w.navigation.group.button(2)
        self.assertTrue(activity.hasFocus())
        self.assertEqual(self.w.pages.currentIndex(), 0)
        QTest.keyClick(activity, Qt.Key.Key_Return)
        self.assertEqual(self.w.pages.currentIndex(), 2)
        QTest.keyClick(activity, Qt.Key.Key_Up)
        self.assertTrue(first.hasFocus())
        QTest.keyClick(first, Qt.Key.Key_Tab)
        self.assertTrue(activity.hasFocus())
        for key, page in ((Qt.Key.Key_6, 3), (Qt.Key.Key_5, 5), (Qt.Key.Key_4, 4), (Qt.Key.Key_3, 1),
                          (Qt.Key.Key_2, 2), (Qt.Key.Key_1, 0)):
            QTest.keyClick(self.w, key, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(self.w.pages.currentIndex(), page)
            self.assertEqual(self.w.navigation.group.checkedId(), page)
        with patch.object(self.w, 'open_technician') as action:
            QTest.mouseClick(self.w.navigation.group.button(3), Qt.MouseButton.LeftButton)
            self.assertTrue(self.w.reports.isVisible())

    def test_indicator_retargets_from_onscreen_position_and_last_page_wins(self):
        self.w.select_page(3)
        animation = self.w.sidebar.indicator_animation
        self.assertIsNotNone(animation)
        animation.setCurrentTime(90)
        position = self.w.sidebar.indicator_y
        self.w.select_page(2)
        self.assertAlmostEqual(self.w.sidebar.indicator_animation.startValue(), position)
        self.w.select_page(1)
        QTest.qWait(300)
        self.assertEqual(self.w.pages.currentIndex(), 1)
        self.assertAlmostEqual(self.w.sidebar.indicator_y, self.w.navigation.group.button(1).y())
        self.assertIsNone(self.w.pages.currentWidget().graphicsEffect())
        self.assertTrue(self.w.navigation.group.button(3).isEnabled())
        self.w.select_page(0)
        QTest.qWait(300)
        self.assertEqual(self.w.scroll.verticalScrollBar().maximum(), 0)

    def test_rebuild_banner_shows_only_until_history_is_imported(self):
        self.assertFalse(self.w.history_banner.isVisible())
        self.w.display_ledger(dict(self.ledger, imported=None))
        self.app.processEvents()
        self.assertTrue(self.w.history_banner.isVisible())
        with patch.object(self.w, 'rebuild_history') as action:
            QTest.mouseClick(self.w.rebuild_button, Qt.MouseButton.LeftButton)
            action.assert_called_once()
        self.w.display_ledger(self.ledger)
        self.app.processEvents()
        self.assertFalse(self.w.history_banner.isVisible())

    def test_theme_choice_is_saved_then_the_app_restarts_itself(self):
        self.assertEqual([b.text() for b in self.w.theme_control.group.buttons()], ['Auto', 'Light', 'Dark'])
        self.assertTrue(self.w.theme_control.group.button(0).isChecked())
        with patch.object(gui, 'update_settings') as save, patch.object(self.w, 'relaunch') as relaunch:
            self.w.theme_control.group.button(1).click()
            save.assert_called_once_with({'theme': 'light'}, self.directory)
            relaunch.assert_called_once()
        with patch.object(gui, 'update_settings') as save, patch.object(self.w, 'relaunch') as relaunch:
            self.w.switch_theme('light')                    # already light: nothing to do
            save.assert_not_called()
            relaunch.assert_not_called()

    def test_both_reduced_motion_paths_are_instant(self):
        for flag, environment in ((True, '0'), (False, '1')):
            with patch.object(gui, 'REDUCE_MOTION', flag), patch.dict(os.environ, LIFETIME_REDUCE_MOTION=environment):
                for index in (1, 3, 0):
                    self.w.select_page(index)
                    self.assertIsNone(self.w.page_animation)
                    self.assertIsNone(self.w.pages.currentWidget().graphicsEffect())
                    self.assertIsNone(self.w.sidebar.indicator_animation)
                    self.assertEqual(self.w.sidebar.indicator_y, self.w.navigation.group.button(index).y())
                self.w.counting_toggle.click()
                self.assertIsNone(self.w.counting_animation)
                self.assertTrue(self.w.counting_panel.isVisible())
                self.w.counting_toggle.click()

    def test_rail_never_hides_navigation_and_all_pages_fit_390(self):
        for width in (1280, 900, 899, 760, 599, 390):
            self.w.resize(width, 560)
            self.app.processEvents()
            self.assertEqual(self.w.sidebar.width(), 232 if width >= 900 else 64)
            for page in (0, 2, 1, 4, 5, 3):
                self.w.select_page(page)
                self.app.processEvents()
                self.assertLessEqual(self.w.scroll.widget().width(), self.w.scroll.viewport().width())
                self.assertTrue(self.w.navigation.group.button(page).isVisible())
                self.assertTrue(self.w.navigation.group.button(page).toolTip())
            self.assertTrue(self.w.sidebar.settings_button.isVisible())
        self.w.select_page(0)
        with patch.object(gui, 'REDUCE_MOTION', True):
            self.w.counting_toggle.click()
            self.w.import_toggle.click()
        self.app.processEvents()
        self.assertLessEqual(self.w.scroll.widget().width(), self.w.scroll.viewport().width())
        self.w.scroll.ensureWidgetVisible(self.w.baseline)
        self.assertTrue(self.w.baseline.isVisible())

    def test_logos_settings_and_reports_dispatch_existing_actions(self):
        self.assertFalse(self.w.sidebar.logo.pixmap().isNull())
        self.assertFalse(self.w.sidebar.credit_logo.pixmap().isNull())
        self.w.select_page(3)
        self.assertEqual(len(self.w.report_rows), 3)
        self.assertEqual(len(self.w.report_list.findChildren(gui.QFrame, 'divider')), 2)
        with patch.object(self.w, 'open_technician') as action:
            for title, dialog in (('Export report', gui.ExportDialog), ('Device identity', gui.IdentityDialog),
                                  ('Coverage details', gui.CoverageDialog)):
                QTest.mouseClick(self.w.technician_actions[title], Qt.MouseButton.LeftButton)
                action.assert_called_with(dialog)
        # The settings row uses the existing dialog, not a dead fifth page.
        with patch.object(gui.SetupDialog, 'exec', return_value=0) as settings:
            QTest.mouseClick(self.w.sidebar.settings_button, Qt.MouseButton.LeftButton)
            settings.assert_called_once()

    def test_consent_hides_activity_and_live_language_relabels_the_shell(self):
        self.w.select_page(2)
        self.w.settings['history'] = False
        self.w.apply_visibility()
        self.assertTrue(self.w.navigation.group.button(2).isHidden())
        self.assertTrue(self.w.trend_panel.isHidden())
        self.assertEqual(self.w.pages.currentIndex(), 0)
        self.w.select_page(2)
        self.assertEqual(self.w.pages.currentIndex(), 0)
        set_language('vi')
        self.assertEqual(self.w.navigation.group.button(2).text(), 'Hoạt động')
        self.assertEqual(self.w.navigation.group.button(3).toolTip(), 'Báo cáo')
        self.assertEqual(self.w.counting_toggle.text(), 'Cách tính thời gian')
        set_language('en')

    def test_trend_preserves_unknown_dates_and_opens_activity(self):
        today = dt.date.today()
        self.ledger['daily'] = {today.isoformat(): 3600, (today-dt.timedelta(days=2)).isoformat(): 0}
        self.w.display_ledger(self.ledger)
        self.assertEqual(len(self.w.trend.values), 14)
        self.assertEqual(self.w.trend.values[-1], 1)
        self.assertIsNone(self.w.trend.values[-2])
        self.assertEqual(self.w.trend.values[-3], 0)
        QTest.mouseClick(self.w.trend, Qt.MouseButton.LeftButton)
        self.assertEqual(self.w.pages.currentIndex(), 2)
