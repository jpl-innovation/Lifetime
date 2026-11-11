"""Sliding switches (every QCheckBox), Settings as a page, and the server header's one-click language and theme buttons."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import Qt, QEasingCurve
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QRadioButton, QVBoxLayout, QWidget
from qt_test_support import qt_app
import lifetime_gui as gui
import lifetime_theme as theme
from lifetime_i18n import set_language
from lifetime_settings import load_settings
from lifetime_tracker import new_ledger


class SwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        theme.install()

    def setUp(self):
        self.addCleanup(theme.set_theme, 'dark')
        self.holder = QWidget()
        self.addCleanup(self.holder.deleteLater)
        box = QVBoxLayout(self.holder)
        self.box = QCheckBox('Allow')
        self.other = QCheckBox('Also')
        box.addWidget(self.box)
        box.addWidget(self.other)
        self.holder.resize(300, 120)
        self.holder.show()
        self.app.processEvents()
        self.events = []
        self.box.toggled.connect(lambda value: self.events.append(('toggled', value)))
        self.box.stateChanged.connect(lambda value: self.events.append(('state', value)))
        self.box.clicked.connect(lambda value: self.events.append(('clicked', value)))

    def overlay(self):
        return self.box._lifetime_switch

    def click(self):
        QTest.mouseClick(self.box, Qt.MouseButton.LeftButton, pos=self.overlay().geometry().center())

    def test_every_checkbox_is_a_switch_of_the_right_size(self):
        for box in (self.box, self.other):
            overlay = box._lifetime_switch
            self.assertEqual((overlay.width(), overlay.height()), (theme.SWITCH_W, theme.SWITCH_H))
            self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual((self.overlay().animation.duration(), self.overlay().animation.easingCurve().type()),
                         (180, QEasingCurve.Type.OutCubic))

    def test_click_toggles_and_keeps_the_checkbox_signals(self):
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.click()
        self.assertTrue(self.box.isChecked())
        self.assertEqual(sorted(self.events), [('clicked', True), ('state', 2), ('toggled', True)])
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.click()
        self.assertFalse(self.box.isChecked())

    def test_space_and_enter_toggle_and_show_the_focus_ring(self):
        self.box.setFocus(Qt.FocusReason.TabFocusReason)
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            QTest.keyClick(self.box, Qt.Key.Key_Space)
            self.assertTrue(self.box.isChecked())
            QTest.keyClick(self.box, Qt.Key.Key_Return)
            self.assertFalse(self.box.isChecked())
            QTest.keyClick(self.box, Qt.Key.Key_Enter)
            self.assertTrue(self.box.isChecked())
        self.assertTrue(self.box._lifetime_keyboard)
        QTest.mouseClick(self.box, Qt.MouseButton.LeftButton, pos=self.overlay().geometry().center())
        self.assertFalse(self.box._lifetime_keyboard)       # a mouse click leaves no ring

    def test_the_knob_slides_over_180_ms_and_jumps_when_motion_is_reduced(self):
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '0'}), patch.object(theme, 'motion_off', return_value=False):
            self.click()
            overlay = self.overlay()
            self.assertEqual(overlay.animation.state(), overlay.animation.State.Running)
            overlay.animation.setCurrentTime(60)
            self.assertTrue(0.0 < overlay.position < 1.0)
            QTest.qWait(260)
            self.assertEqual(overlay.position, 1.0)
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.click()
            self.assertEqual(self.overlay().position, 0.0)       # instantly, with no animation running
            self.assertNotEqual(self.overlay().animation.state(), self.overlay().animation.State.Running)

    def test_code_changes_follow_even_with_signals_blocked(self):
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.box.blockSignals(True)
            self.box.setChecked(True)
            self.box.blockSignals(False)
            self.box.update()
            self.app.processEvents()
        self.assertEqual(self.overlay().position, 1.0)
        self.assertEqual(self.events, [])

    def test_a_disabled_switch_does_not_toggle(self):
        self.box.setEnabled(False)
        self.click()
        QTest.keyClick(self.box, Qt.Key.Key_Space)
        self.assertFalse(self.box.isChecked())

    def test_track_is_the_themes_green_when_on_and_neutral_when_off(self):
        import lifetime_glass
        self.addCleanup(lifetime_glass.set_style, 'glass')
        lifetime_glass.set_style('classic')
        for name in ('dark', 'light'):
            theme.set_theme(name)
            theme.retint_all()
            for checked in (True, False):
                self.box.blockSignals(True)
                self.box.setChecked(checked)
                self.box.blockSignals(False)
                self.overlay().position = 1.0 if checked else 0.0
                image = self.box.grab().toImage()
                row = self.overlay().geometry().center().y()
                x = self.overlay().geometry().left() + (8 if checked else 40)       # the track, away from the knob
                colour = QColor(image.pixel(x, row)).name().upper()
                if checked:
                    self.assertEqual(colour, theme.THEMES[name]['good'].upper(), name)
                else:
                    self.assertEqual(colour, theme.THEMES[name]['bar'].upper(), name)

    def test_no_tick_box_or_radio_button_is_left_in_the_apps(self):
        import lifetime_setup
        import lifetime_technician
        import inspect
        for module in (lifetime_setup, lifetime_technician):
            self.assertNotIn('QRadioButton(', inspect.getsource(module))


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        import lifetime_glass
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
        set_language('en')
        self.addCleanup(set_language, 'en')
        settings = dict(load_settings(self.directory), completed=True, history=True, language='en')
        self.w = gui.Window(settings)
        self.w.timer.stop()
        self.w.worker.timer.stop()
        self.w.resize(1100, 760)
        self.w.show()
        self.addCleanup(self.w.deleteLater)
        self.addCleanup(self.w.close)
        self.app.processEvents()

    def open_dialogs(self):
        return [d for d in QApplication.topLevelWidgets() if isinstance(d, QDialog) and d.isVisible()]

    def test_settings_is_a_page_reached_from_the_sidebar_and_no_dialog_opens(self):
        with patch.object(gui.SetupDialog, 'exec') as dialog:
            QTest.mouseClick(self.w.sidebar.settings_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            dialog.assert_not_called()
        self.assertEqual(self.w.pages.currentWidget(), self.w.settings_page)
        self.assertTrue(self.w.settings_page.isVisible())
        self.assertEqual(self.w.navigation.group.checkedButton(), self.w.sidebar.settings_button)
        self.assertEqual(self.open_dialogs(), [])
        self.assertFalse(self.w.settings_page.findChildren(QRadioButton))
        self.w.select_page(0)
        self.assertFalse(self.w.settings_page.isVisible())

    def test_shortcut_keyboard_and_other_entry_points_select_the_page(self):
        self.w.select_page(0)
        QTest.keyClick(self.w, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.w.pages.currentIndex(), 6)
        self.w.select_page(0)
        self.w.sidebar.settings_button.setFocus()
        QTest.keyClick(self.w.sidebar.settings_button, Qt.Key.Key_Return)
        self.assertEqual(self.w.pages.currentIndex(), 6)
        self.w.select_page(0)
        self.w.open_settings()           # what the Health page's "Open Settings" button calls
        self.assertEqual(self.w.pages.currentIndex(), 6)
        self.assertEqual(self.open_dialogs(), [])

    def test_every_old_option_is_here_and_the_switches_save(self):
        page = self.w.settings_page
        self.w.select_page(6)
        self.assertEqual(set(page.checks), {'awake', 'history', 'health', 'battery', 'network', 'wifi_name', 'graphics', 'resources'})
        self.assertEqual(set(page.answers), {'rebuild', 'autostart'})
        self.assertEqual(set(page.language_buttons), {'en', 'vi'})
        self.assertEqual(set(page.style_buttons), {'glass', 'classic'})
        self.assertFalse(page.checks['awake'].isEnabled())
        self.assertTrue(page.checks['awake'].isChecked())
        with patch.object(gui, 'configure_login'), patch.object(gui, 'configure_tray_login'):
            page.checks['battery'].click()
            self.assertTrue(load_settings(self.directory)['battery'])
            self.assertTrue(self.w.settings['battery'])
            page.select_all.switch.click()
            saved = load_settings(self.directory)
            self.assertTrue(all(saved[key] for key in ('history', 'health', 'battery', 'network', 'wifi_name', 'graphics', 'resources')))
            page.checks['wifi_name'].click()
            self.assertFalse(load_settings(self.directory)['wifi_name'])
            self.assertFalse(page.select_all.switch.isChecked())
        self.assertEqual(self.open_dialogs(), [])

    def test_language_and_style_controls_change_the_window(self):
        from lifetime_glass import active_style
        page = self.w.settings_page
        self.w.select_page(6)
        page.language_buttons['vi'].click()
        self.assertEqual(load_settings(self.directory)['language'], 'vi')
        self.assertEqual(self.w.sidebar.settings_button.text(), 'Cài đặt')
        page.language_buttons['en'].click()
        page.style_buttons['glass'].click()
        self.assertEqual(active_style(), 'glass')
        self.assertEqual(load_settings(self.directory)['style'], 'glass')


class ServerHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        import lifetime_server_gui as server
        from lifetime_server_devices import set_style
        from test_server_gui import FakeAdmin
        self.server = server
        self.addCleanup(set_style, 'glass')
        self.addCleanup(theme.set_theme, 'dark')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(server, 'AdminClient', return_value=FakeAdmin(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        reduced = patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'})
        reduced.start()
        self.addCleanup(reduced.stop)
        # retint_all restyles every widget earlier tests left open, which takes minutes after a long run; the live
        # change itself (preference saved, theme/style applied) is what these tests check.
        retint = patch.object(server, 'retint_all')
        retint.start()
        self.addCleanup(retint.stop)
        self.window = server.ServerWindow(self.root, 8123)
        self.window.timer.stop()
        self.window.resize(1600, 1000)
        self.window.show()
        self.addCleanup(self.window.close)
        self.app.processEvents()

    def test_language_button_cycles_and_says_what_a_click_does(self):
        button = self.window.language_button
        self.assertEqual(button.height(), self.window.refresh_button.height())
        self.assertIn('English', button.accessibleName())
        self.assertIn('Tiếng Việt', button.toolTip())
        button.click()
        self.assertEqual(self.window.language, 'vi')
        self.assertIn('Ngôn ngữ', button.accessibleName())
        self.assertEqual(button.index, 1)
        button.click()
        self.assertEqual(self.window.language, 'en')

    def test_theme_button_cycles_auto_light_dark_and_saves(self):
        button = self.window.theme_button
        seen = []
        for _ in range(3):
            button.click()
            seen.append(self.server.read_theme_preference(self.root))
        self.assertEqual(seen, ['light', 'dark', 'auto'])
        self.assertIn('Auto', button.toolTip())          # after a full turn it is back on Auto
        self.assertIn('Light', button.toolTip())         # and says the next click goes to Light

    def test_keyboard_toggles_and_shows_the_focus_ring(self):
        button = self.window.language_button
        button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertEqual(self.window.language, 'vi')
        QTest.keyClick(button, Qt.Key.Key_Return)
        self.assertEqual(self.window.language, 'en')
        self.assertTrue(button.keyboard)
        self.assertFalse(button.grab().isNull())

    def test_the_slide_is_skipped_under_reduced_motion(self):
        button = self.window.language_button
        button.click()
        self.assertEqual(button.phase, 1.0)
        with patch('lifetime_server_widgets.reduced_motion', return_value=False):
            button.click()
            self.assertEqual(button.phase, 0.0)
            self.assertEqual(button.animation.state(), button.animation.State.Running)

    def test_the_cluster_stays_beside_the_status_then_joins_the_menu_row(self):
        window = self.window
        for width, in_actions in ((1600, True), (900, True), (640, True), (520, False)):
            window.resize(width, 700)
            self.app.processEvents()
            self.assertEqual(window.chrome_actions.layout().indexOf(window.chrome_switches) >= 0, in_actions, width)
            for widget in (window.language_button, window.theme_button, window.refresh_button):
                corner = widget.mapTo(window, widget.rect().bottomRight())
                self.assertLessEqual(corner.x(), window.width(), (width, widget))
            self.assertFalse(window.language_button.geometry().intersects(window.theme_button.geometry()))

    def test_administration_keeps_only_the_style_control_and_it_works(self):
        from lifetime_server_devices import active_style
        self.assertFalse(hasattr(self.window, 'language_choice'))
        self.assertFalse(hasattr(self.window, 'theme_choice'))
        self.window.style_control.group.button(1).click()
        self.app.processEvents()
        self.assertEqual(self.server.read_style_preference(self.root), 'classic')
        self.assertEqual(active_style(), 'classic')


if __name__ == '__main__':
    unittest.main()
