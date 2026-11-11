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
from PyQt6.QtWidgets import QDialog
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

    def test_sidebar_marker_stretches_towards_its_target_and_settles_exactly(self):
        side = self.w.sidebar
        self.w.select_page(0)
        QTest.qWait(600)
        self.w.select_page(3)                       # Reports is below Overview: the bottom edge leads
        side.indicator_animation.setCurrentTime(80)
        side.trail_animation.setCurrentTime(80)
        row = side.group.checkedButton()
        drawn = side.marker_rect(row)
        self.assertAlmostEqual(drawn.bottom(), side.indicator_y + row.height())
        self.assertLess(drawn.top(), side.indicator_y)          # the top edge is still catching up
        QTest.qWait(700)
        settled = side.marker_rect(row)
        self.assertAlmostEqual(settled.top(), row.y())
        self.assertAlmostEqual(settled.height(), row.height())

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

    def test_an_event_log_with_a_changed_clock_is_explained_in_words(self):
        from unittest.mock import Mock
        for detail, friendly in (('Lifetime import: Boot ends before it starts', True),
                                 ('Lifetime import: Overlapping boot timestamps; refusing ambiguous history', True),
                                 ('Lifetime import: the importer crashed', False)):
            self.w.import_process = Mock()
            self.w.import_process.readAllStandardError.return_value = detail.encode()
            self.w.import_finished(1, None)
            shown = self.w.verification.text()
            self.assertEqual('clock was probably changed' in shown, friendly)
            self.assertNotIn('Boot ends before it starts', shown)

    def test_a_short_window_keeps_the_language_and_theme_switches_reachable(self):
        side = self.w.sidebar
        self.w.resize(900, 600)
        self.app.processEvents()
        self.assertTrue(side.tight)
        for control in (side.language_control, side.theme_control):
            self.assertTrue(control.isVisible())
            self.assertLessEqual(control.geometry().bottom(), side.height())
        self.w.resize(1280, 900)
        self.app.processEvents()
        self.assertFalse(side.tight)                          # the roomy layout returns when there is room
        self.assertTrue(side.credit_logo.isVisible())

    def test_the_rebuild_banner_stays_off_the_connect_page_and_returns_after(self):
        self.w.display_ledger(dict(self.ledger, imported=None))
        self.app.processEvents()
        self.assertTrue(self.w.history_banner.isVisible())
        self.w.select_page(5)                                   # Connect needs the height for its form
        self.app.processEvents()
        self.assertFalse(self.w.history_banner.isVisible())
        self.w.display_ledger(dict(self.ledger, imported=None))   # a refresh while on Connect must not bring it back
        self.app.processEvents()
        self.assertFalse(self.w.history_banner.isVisible())
        self.w.select_page(0)
        self.app.processEvents()
        self.assertTrue(self.w.history_banner.isVisible())

    def test_overview_entrance_plays_once_continues_through_refreshes_and_lands_exactly(self):
        w = self.w
        self.ledger.update(boots=40, total_seconds=36000, current_session_seconds=7200, longest_session_seconds=9000)
        w.display_ledger(self.ledger)       # read_ledger returns this same dict, so later refreshes agree
        QTest.qWait(450)
        self.assertFalse(w.intro.done)
        before = w.intro.value
        self.assertGreater(before, 0)
        from lifetime_reveal import progress
        self.assertLess(progress(w.hero.entrance, eased=False), 1.0)               # the total's digits are still rolling
        self.assertIsNone(w.cards['boots'].entrance)                               # ordinary figures just appear
        self.assertEqual(w.cards['boots'].value.text(), '40')
        self.ledger['total_seconds'] = 36002
        w.display_ledger(self.ledger)                        # fresh data mid-entrance: no restart
        self.assertGreaterEqual(w.intro.value, before)
        QTest.qWait(1900)
        self.assertTrue(w.intro.done)
        self.assertEqual(w.cards['boots'].value.text(), '40')
        self.assertAlmostEqual(w.hero.seconds, w.hero.target, delta=5)
        w.select_page(1)
        w.select_page(0)
        self.assertEqual(w.intro.value, 1.0)                                        # tab switches never replay it
        self.assertEqual(w.cards['boots'].value.text(), '40')

    def test_the_total_rolls_again_every_time_overview_is_opened_or_the_window_comes_back(self):
        w = self.w
        w.display_ledger(self.ledger)
        QTest.qWait(2200)
        self.assertTrue(w.intro.done)
        self.assertEqual(w.hero.replay, 1.0)
        w.select_page(1)
        self.assertEqual(w.hero.replay, 1.0)                  # other pages leave it alone
        w.select_page(0)
        self.assertLess(w.hero.replay, 1.0)                   # opening Overview again rolls it again
        QTest.qWait(1300)
        self.assertEqual(w.hero.replay, 1.0)                  # and it lands exactly
        w.hide()
        w.show()
        self.assertLess(w.hero.replay, 1.0)                   # coming back from the tray rolls it too
        QTest.qWait(1300)
        self.assertEqual(w.hero.replay, 1.0)

    def test_reduced_motion_overview_shows_the_final_numbers_at_once(self):
        self.w.close()
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            w = gui.Window(dict(load_settings(self.directory), completed=True, history=True, language='en'))
            w.timer.stop()
            w.worker.timer.stop()
            self.addCleanup(w.deleteLater)
            w.display_ledger(dict(self.ledger, boots=40))
            w.show()
            self.app.processEvents()
            self.assertTrue(w.intro.done)
            self.assertEqual(w.cards['boots'].value.text(), '40')
            w.hero.roll_again()
            self.assertEqual(w.hero.replay, 1.0)        # nothing rolls under reduced motion
            w.close()

    def test_theme_choice_is_saved_and_applied_in_the_same_window(self):
        import lifetime_theme as theme
        self.addCleanup(theme.set_theme, 'dark')
        self.assertEqual([b.text() for b in self.w.theme_control.group.buttons()], ['Auto', 'Light', 'Dark'])
        with patch.object(gui, 'update_settings') as save, patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.w.theme_control.group.button(1).click()
            save.assert_called_once_with({'theme': 'light'}, self.directory)
        self.assertEqual(theme.active_theme(), 'light')
        self.assertEqual(self.w.theme, 'light')
        self.assertTrue(self.w.isVisible())
        self.assertIn(theme.THEMES['light']['bg'], self.w._lifetime_style and gui.QWidget.styleSheet(self.w))

    def test_theme_reveal_covers_the_window_then_removes_itself(self):
        import lifetime_theme as theme
        from lifetime_shell import ThemeReveal, theme_reveal
        self.addCleanup(theme.set_theme, 'dark')
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '0'}):
            theme_reveal(self.w, self.w.rect().center(), lambda: self.w.apply_live_theme('light'))
        overlay = self.w._theme_reveal
        self.assertIsInstance(overlay, ThemeReveal)
        self.assertEqual(overlay.geometry(), self.w.rect())
        QTest.qWait(ThemeReveal.DURATION + 120)
        self.assertIsNone(self.w._theme_reveal)

    def test_switch_slides_and_keeps_every_segment_reachable(self):
        control = self.w.theme_control
        for button in control.group.buttons():
            self.assertGreaterEqual(button.width(), 36)
            self.assertGreaterEqual(button.height(), 36)
        control.set_vertical(True)
        self.assertEqual(len({b.y() for b in control.group.buttons()}), 3)
        control.set_vertical(False)

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
        # Settings is a page of the window like the others: no dialog opens.
        with patch.object(gui.SetupDialog, 'exec', return_value=0) as dialog:
            QTest.mouseClick(self.w.sidebar.settings_button, Qt.MouseButton.LeftButton)
            dialog.assert_not_called()
        self.assertEqual(self.w.pages.currentWidget(), self.w.settings_page)
        self.assertEqual(self.w.navigation.group.checkedButton(), self.w.sidebar.settings_button)
        self.assertFalse([d for d in gui.QApplication.topLevelWidgets() if isinstance(d, QDialog) and d.isVisible()])

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


class DesktopStyleTests(unittest.TestCase):
    """The desktop's two looks: Classic is how it always looked, Glass is the server's material on the desktop's layout."""
    # sha256 of the Classic window stylesheet (font family normalised), dark and light. Re-pinned when tick boxes became switches.
    CLASSIC = {'dark': '680bbb6522b0523ddcbb9e08bbb8de401933b9b59e4eacddb3e238654f130344',
               'light': '03435fa976207249e1527c8cac17af3076329cad875469bb1ef5f71f146239f4'}

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        import lifetime_glass
        import lifetime_theme as theme
        self.addCleanup(lifetime_glass.set_style, 'glass')
        self.addCleanup(theme.set_theme, 'dark')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for p in (patch.object(gui, 'data_dir', return_value=self.directory),
                  patch.object(gui, 'read_ledger', return_value=dict(new_ledger(), imported={'seconds': 0})),
                  patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'})):
            p.start()
            self.addCleanup(p.stop)

    def window(self, **extra):
        w = gui.Window(dict(load_settings(self.directory), completed=True, history=True, language='en', **extra))
        w.timer.stop()
        w.worker.timer.stop()
        w.resize(1100, 760)
        w.show()
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.close)
        self.app.processEvents()
        return w

    def sheet(self, w):
        return gui.QWidget.styleSheet(w)

    def test_classic_is_the_default_and_its_stylesheet_is_the_one_from_before(self):
        import hashlib
        import lifetime_theme as theme
        from lifetime_glass import active_style, desktop_sheet
        w = self.window()
        self.assertEqual(active_style(), 'classic')
        for name in ('dark', 'light'):
            theme.set_theme(name)
            w.apply_theme()
            plain = self.sheet(w).replace('Segoe UI', 'FAMILY').replace('DejaVu Sans', 'FAMILY')
            self.assertEqual(hashlib.sha256(plain.encode()).hexdigest(), self.CLASSIC[name], name)
            self.assertEqual(desktop_sheet(theme.THEMES[name]), '')
            self.assertNotIn('qlineargradient(x1:0, y1:0, x2:1', plain)

    def test_unknown_style_value_means_classic(self):
        from lifetime_glass import active_style
        self.window(style='neon')
        self.assertEqual(active_style(), 'classic')

    def test_glass_adds_to_the_classic_sheet_and_switching_back_restores_it_exactly(self):
        from lifetime_glass import active_style
        w = self.window()
        classic = self.sheet(w)
        self.assertTrue(w.switch_style('glass'))
        glass = self.sheet(w)
        self.assertEqual(active_style(), 'glass')
        self.assertTrue(glass.startswith(classic))
        self.assertIn('qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2353A0', glass)
        self.assertEqual(load_settings(self.directory)['style'], 'glass')
        self.assertEqual(w.settings['style'], 'glass')
        self.assertTrue(w.switch_style('classic'))
        self.assertEqual(self.sheet(w), classic)
        self.assertEqual(load_settings(self.directory)['style'], 'classic')
        self.assertFalse(w.switch_style('classic'))          # nothing to do
        self.assertFalse(w.switch_style('neon'))

    def test_switching_style_repaints_the_sidebar_and_panels(self):
        w = self.window()
        classic = w.grab().toImage()
        w.switch_style('glass')
        self.app.processEvents()
        glass = w.grab().toImage()
        self.assertNotEqual(classic.pixel(4, 300), glass.pixel(4, 300))               # the sidebar's ground
        self.assertNotEqual(classic.pixel(w.width() - 60, 60), glass.pixel(w.width() - 60, 60))   # the window behind the content
        self.assertTrue(w.stats_panel.property('glass'))
        self.assertTrue(w.verify_button.property('glass'))
        w.switch_style('classic')
        self.app.processEvents()
        self.assertEqual(w.grab().toImage(), classic)                                  # and back, pixel for pixel

    def test_both_styles_in_both_themes_paint_every_page(self):
        import lifetime_theme as theme
        w = self.window()
        for style in ('classic', 'glass'):
            for name in ('dark', 'light'):
                theme.set_theme(name)
                w.apply_live_style(style)
                for page in (0, 1, 2, 3, 4, 5):
                    w.select_page(page)
                    self.app.processEvents()
                    self.assertFalse(w.grab().isNull(), (style, name, page))
                w.sidebar.set_collapsed(True)
                self.assertFalse(w.grab().isNull())
                w.sidebar.set_collapsed(False)

    def test_the_sidebar_marker_still_stretches_towards_its_page_in_glass(self):
        w = self.window(style='glass')
        side = w.sidebar
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '0'}):
            w.select_page(0)
            QTest.qWait(600)
            w.select_page(3)                                    # the lens below leads with its bottom edge, the top catches up
            side.indicator_animation.setCurrentTime(80)
            side.trail_animation.setCurrentTime(80)
            row = side.group.checkedButton()
            self.assertLess(side.marker_rect(row).top(), side.indicator_y)
            self.assertFalse(w.sidebar.grab().isNull())
            QTest.qWait(700)
            self.assertAlmostEqual(side.marker_rect(row).height(), row.height())

    def test_settings_page_offers_the_style_and_changes_the_window_live(self):
        from lifetime_glass import active_style
        w = self.window()
        page = w.settings_page
        self.assertEqual(set(page.style_buttons), {'glass', 'classic'})
        self.assertTrue(page.style_buttons['classic'].isChecked())
        self.assertNotIn('qlineargradient', self.sheet(w))
        page.style_buttons['glass'].setChecked(True)
        self.assertEqual(active_style(), 'glass')
        self.assertEqual(load_settings(self.directory)['style'], 'glass')
        self.assertIn('qlineargradient(x1:0, y1:0, x2:1', self.sheet(w))
        self.assertFalse(page.grab().isNull())

    def test_style_names_are_translated(self):
        from lifetime_i18n import VI
        for key, vi in (('Style', 'Kiểu giao diện'), ('Glass', 'Kính'), ('Classic', 'Cổ điển')):
            self.assertEqual(VI[key], vi)
