"""Text that comes from another computer or from an administrator is never rendered as HTML (security audit H1).

A computer chooses its own name and hardware strings, an administrator types group, check and role names, and a
server tells the desktop app its name. A stock QLabel, tooltip or message box renders such text as HTML when it
looks like HTML; `<img src="\\\\host\\x">` then makes the console fetch that file. These tests fill both windows
with markup and walk every widget.
"""
import ast
import html
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QLabel, QMessageBox, QTableWidget, QTextEdit, QToolTip,
                             QWidget)

import lifetime_gui as gui
import lifetime_permissions as permissions
import lifetime_tracker as tracker
from lifetime_i18n_qt import PlainLabel
from lifetime_server import ServerRuntime
from lifetime_server_gui import ServerWindow
from lifetime_settings import CATEGORIES
from lifetime_shell import shut_down
from lifetime_theme import install, plain_tip
from qt_test_support import qt_app
from render_server import populate

ROOT = Path(__file__).parent
PICTURE = (ROOT / 'assets' / 'lifetime.png').as_posix()       # 1280 px wide: a label that loads it becomes huge
HOSTILE = f'<b>B</b><img src="{PICTURE}"><a href="http://lifetime.invalid/">A</a>'
SHORT = '<b>B</b><a href="x:">A</a>'       # check names are limited to 80 characters
# The only labels allowed to be rich text. Each builds its HTML from html.escape()d values.
RICH = {'lifetime_dashboard.py': 1, 'lifetime_server_alerts.py': 1, 'lifetime_server_gui.py': 1}


def tooltip_shown(app, widget, position=QPoint(2, 2)):
    QToolTip.hideText()
    app.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, position, widget.mapToGlobal(position)))
    return QToolTip.text()


def check_widgets(test, app, window):
    """No label guesses its format, no rich text carries the markup unescaped, no tooltip shows it as HTML."""
    seen = 0
    for label in window.findChildren(QLabel):
        test.assertNotEqual(label.textFormat(), Qt.TextFormat.AutoText, f'{label.objectName()} {label.text()[:60]!r}')
        if '<b>B</b>' in label.text():
            seen += 1
            test.assertEqual(label.textFormat(), Qt.TextFormat.PlainText, label.text()[:80])
    for edit in window.findChildren(QTextEdit):
        document = edit.document()
        block = document.begin()
        while block.isValid():
            for fragment in block.textFormats():
                test.assertFalse(fragment.format.isImageFormat(), 'a picture from a name')
                test.assertFalse(fragment.format.isAnchor(), 'a link from a name')
            block = block.next()
        seen += '<b>B</b>' in document.toPlainText()
    for widget in window.findChildren(QWidget):
        if '<' in widget.toolTip():
            seen += 1
            test.assertEqual(tooltip_shown(app, widget), plain_tip(widget.toolTip()))
    for table in window.findChildren(QTableWidget):
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None and '<' in item.toolTip() and not table.isRowHidden(row) and table.isVisible():
                    table.scrollToItem(item)
                    spot = table.visualItemRect(item).center()
                    if table.viewport().rect().contains(spot):
                        seen += 1
                        test.assertEqual(tooltip_shown(app, table.viewport(), spot), plain_tip(item.toolTip()))
    QToolTip.hideText()
    return seen


class ServerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='s1-gui-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))
        self.enterContext(patch.object(socket, 'gethostname', return_value='Demo-Server' + HOSTILE))
        self.runtime = ServerRuntime(self.temporary.name, address='https://127.0.0.1:8443', host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.window = ServerWindow(self.temporary.name, urlsplit(self.runtime.admin_address).port)
        self.window.timer.stop()
        self.window.resize(1600, 1000)
        self.window.show()
        self.addCleanup(self.close)
        self.wait()

    def close(self):
        self.window.close()
        self.wait()
        shut_down(self.app, self.window)

    def wait(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.window._jobs and not self.window._fleet_timer.isActive() and not self.window._search_timer.isActive():
                return
            QTest.qWait(5)
        self.fail('Dashboard did not settle')

    def test_markup_from_computers_groups_checks_and_roles_is_shown_as_text_on_every_page(self):
        populate(self.runtime._store, self.runtime.fingerprint, count=6, detailed=True, mark=HOSTILE)
        self.runtime._store.add_group('Lab' + HOSTILE)
        self.runtime.monitor.save_check(dict(name='Printer' + SHORT, kind='tcp', host='192.0.2.10', port=9100,
                                             group='Lab' + HOSTILE))
        self.runtime.access.set_role('S-1-5-21-1-2-3-1001', 'LAB\\tech' + HOSTILE, 'user', 'viewer', 'Admin' + HOSTILE)
        self.window.refresh()
        self.wait()
        seen = 0
        for index in range(self.window.tabs.count()):
            self.window.tabs.setCurrentIndex(index)
            self.wait()
            for table in (self.window.fleet_table, self.window.table, self.window.admin_pending):
                if table.isVisible() and table.rowCount():
                    table.selectRow(0)
            self.wait()
            QTest.qWait(50)
            self.wait()
            seen += check_widgets(self, self.app, self.window)
        self.assertGreater(seen, 20, 'the markup never reached the window: this test checked nothing')
        self.assertLess(self.window.width(), 1700, 'a picture named in a computer name was loaded and stretched the window')


class DesktopWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.enterContext(patch.object(gui, 'read_ledger', return_value=tracker.new_ledger()))
        self.enterContext(patch.object(gui, 'data_dir', return_value=Path(self.temporary.name)))
        self.window = gui.Window(dict(completed=True, **{key: True for key in CATEGORIES}))
        self.window.timer.stop()
        self.window.worker.timer.stop()
        self.addCleanup(lambda: (self.window.close(), shut_down(self.app, self.window)))

    def test_a_server_name_with_markup_is_shown_as_text(self):
        page = self.window.connect_page
        page.timer.stop()
        for status in (dict(enabled=True, status='pending', phase='pending'),
                       dict(enabled=True, status='connected', reported=True, last_sync='2026-11-01T00:00:00+00:00',
                            policy=dict(history=True))):
            page._operation = 'status'
            page.completed(0,dict(status, server='https://192.0.2.1:8443', server_name='School' + HOSTILE,
                                              device_name='PC' + HOSTILE))
            self.app.processEvents()
            self.assertGreater(check_widgets(self, self.app, self.window), 0)


class BackstopTests(unittest.TestCase):
    """Labels and tooltips Qt builds itself (message boxes, table cells) go through the application filter."""
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        install()

    def test_a_message_box_shows_markup_as_text_and_loads_no_picture(self):
        box = QMessageBox(QMessageBox.Icon.Warning, 'Title', 'Could not save ' + HOSTILE)
        self.addCleanup(box.deleteLater)
        box.setInformativeText(HOSTILE)
        box.show()
        self.app.processEvents()
        labels = [label for label in box.findChildren(QLabel) if label.text()]
        self.assertTrue(labels)
        for label in labels:
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertLess(box.width(), 1200)
        box.close()

    def test_no_label_loads_the_picture_its_text_names(self):
        plain, stock = PlainLabel(HOSTILE), QLabel(HOSTILE)
        self.assertEqual(plain.textFormat(), Qt.TextFormat.PlainText)
        self.assertLess(plain.sizeHint().height(), 100)
        # A stock label is made plain when Qt polishes it, which is before it is first measured or painted.
        self.assertLess(stock.sizeHint().height(), 100)
        self.assertEqual(stock.textFormat(), Qt.TextFormat.PlainText)
        rich = QLabel(HOSTILE)
        rich.setTextFormat(Qt.TextFormat.RichText)
        self.assertGreater(rich.sizeHint().height(), 200, 'the test picture did not load: the checks above prove nothing')

    def test_a_tooltip_with_markup_is_escaped_and_an_ordinary_one_is_left_alone(self):
        widget = QWidget()
        self.addCleanup(widget.deleteLater)
        widget.show()
        widget.setToolTip('Last seen\n10:00')
        self.assertEqual(tooltip_shown(self.app, widget), 'Last seen\n10:00')
        widget.setToolTip(HOSTILE)
        self.assertEqual(tooltip_shown(self.app, widget), '<p style="white-space:pre-wrap">' + html.escape(HOSTILE) + '</p>')
        QToolTip.hideText()


class GuardTests(unittest.TestCase):
    """New code is safe by default: these fail when a module goes back to Qt's guessing label."""

    def test_no_module_uses_the_stock_label_and_rich_text_is_limited_to_the_known_escaped_labels(self):
        rich = {}
        for path in sorted(ROOT.glob('lifetime_*.py')):
            source = path.read_text(encoding='utf-8')
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom) and node.module == 'PyQt6.QtWidgets' and path.name != 'lifetime_theme.py':
                    self.assertNotIn('QLabel', [name.name for name in node.names],
                                     f'{path.name}: import QLabel from lifetime_i18n_qt (PlainLabel), not from PyQt6')
                if isinstance(node, ast.Attribute) and node.attr == 'QLabel' and path.name != 'lifetime_i18n_qt.py':
                    self.fail(f'{path.name}:{node.lineno}: QtWidgets.QLabel bypasses PlainLabel')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == 'setTextFormat' and 'RichText' in ast.unparse(node):
                        rich[path.name] = rich.get(path.name, 0) + 1
                    if node.func.attr == 'showText' and path.name != 'lifetime_theme.py':
                        self.assertIn('plain_tip(', ast.unparse(node), f'{path.name}:{node.lineno}: QToolTip.showText needs plain_tip()')
        self.assertEqual(rich, RICH, 'a new rich-text label: escape every value it shows with html.escape, then list it here')


if __name__ == '__main__':
    unittest.main()
