"""Real-store native server dashboard checks, with offscreen Qt and loopback HTTP."""
import datetime as dt
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
from unittest.mock import patch
from urllib.parse import urlsplit

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

import lifetime_permissions as permissions
import lifetime_server_store as storage
from lifetime_server import ServerRuntime
from lifetime_server_gui import ServerWindow
from qt_test_support import qt_app
from render_server import populate

ROOT = Path(__file__).parent


class FleetGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='s1-gui-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))
        self.enterContext(patch.object(socket, 'gethostname', return_value='Demo-Server'))
        self.runtime = ServerRuntime(self.temporary.name, address='https://127.0.0.1:8443', host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.window = ServerWindow(self.temporary.name, urlsplit(self.runtime.admin_address).port)
        self.window.timer.stop()
        self.window.show()
        self.addCleanup(self.close)
        self.wait()

    def close(self):
        self.window.close()
        self.wait()

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.window._jobs and not self.window._fleet_timer.isActive() and not self.window._search_timer.isActive():
                return
            QTest.qWait(5)
        self.fail('Dashboard did not settle')

    def test_remove_computer_goes_through_the_real_admin_connection(self):
        identifier = self.seed()[0]
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.delete_device(dict(device_id=identifier))
        self.wait()
        self.assertNotIn(identifier, [row['device_id'] for row in self.runtime._store.list_devices()])

    def seed(self):
        ids = populate(self.runtime._store, self.runtime.fingerprint)
        self.window.refresh()
        self.wait()
        return ids

    def test_a_dashboard_left_open_past_midnight_moves_on_to_the_new_day(self):
        from PyQt6.QtCore import QDate, QLocale
        today = QDate.currentDate()
        yesterday = today.addDays(-1)
        self.window._today = yesterday                  # as if it had been opened yesterday and left running
        self.window.fleet_date.setDate(yesterday)
        self.wait()
        self.window._load_fleet()
        self.wait()
        self.assertEqual(self.window.fleet_date.date(), today)
        self.assertEqual(self.window._fleet_result['date'], today.toString('yyyy-MM-dd'))
        self.assertIn(QLocale('en_GB').toString(today, 'd MMM yyyy'), self.window.today_label.text())

    def test_a_date_picked_on_purpose_is_not_pulled_back_to_today(self):
        from PyQt6.QtCore import QDate
        picked = QDate.currentDate().addDays(-3)
        self.window.fleet_date.setDate(picked)
        self.wait()
        self.window._today = QDate.currentDate().addDays(-1)    # the day rolled over meanwhile
        self.window._load_fleet()
        self.wait()
        self.assertEqual(self.window.fleet_date.date(), picked)

    def test_empty_fleet_is_truthful_with_live_add_device_action(self):
        self.assertEqual(self.window.kpis['Connected'].text(), '0')
        self.assertEqual(self.window.kpis['Awake today'].text(), 'Not available')
        self.assertTrue(self.window.fleet_empty.isVisible())
        self.assertFalse(self.window.open_device_button.isEnabled())
        self.window.fleet_empty.click()
        self.window.add_device()  # A second click while issuing is coalesced.
        self.wait()
        sheet = self.window.add_device_sheet
        self.assertTrue(sheet.isVisible())
        self.assertTrue(sheet.code.text().startswith('LTS-'))
        self.assertIn(self.runtime.public_address, sheet.line.text())
        self.assertEqual(sheet.issued['uses'], 1)
        sheet.copy_code.click()
        self.assertEqual(self.app.clipboard().text(), sheet.code.text())
        self.assertEqual(self.runtime._store.invitations()[0]['remaining'], 1)
        self.assertEqual(len(self.runtime._store.invitations()), 1)
        sheet.close()

    def test_store_kpis_search_group_sort_pagination_and_detail(self):
        self.seed()
        projected = self.runtime._store.fleet()
        self.assertEqual(self.window.kpis['Connected'].text(), '16')
        self.assertEqual(self.window.kpis['Offline'].text(), '2')
        self.assertEqual(self.window.kpis['Pending approval'].text(), '2')
        self.assertEqual(self.window.kpis['Awake today'].text(), self.window._duration(projected['today']['seconds']))
        self.assertEqual(self.window.fleet_table.rowCount(), 6)
        self.window.fleet_table.selectRow(2)
        self.assertIn('Demo-PC-03', self.window.fleet_detail_text.text())
        self.assertIn('DEMO-003', self.window.fleet_detail_text.text())
        self.assertTrue(self.window.open_device_button.isEnabled())
        selected = []
        self.window.export_requested.disconnect()
        self.window.export_requested.connect(selected.append)
        self.window.open_device_button.click()
        self.assertEqual(self.window.tabs.currentIndex(), 1)    # Open device opens it; it must not start an export
        self.assertEqual(selected, [])
        self.window.tabs.setCurrentIndex(0)
        self.window.fleet_next.click()
        self.wait()
        self.assertIn('7 to 12 of 20', self.window.fleet_page_label.text())
        self.window.fleet_search.setText('Demo-PC-01')
        self.wait()
        self.assertEqual(self.window.fleet_table.rowCount(), 1)
        self.assertEqual(self.window.kpis['Connected'].text(), '16')
        self.window.fleet_search.clear()
        self.window.fleet_group.setCurrentIndex(self.window.fleet_group.findData('Office'))
        self.wait()
        self.assertTrue(all(r['group'] == 'Office' for r in self.window._fleet_result['rows']))
        self.window.fleet_group.setCurrentIndex(0)
        self.window._fleet_sort_clicked(3)
        self.wait()
        values = [r['today'] for r in self.window._fleet_result['rows']]
        self.assertEqual(values, sorted(values))
        self.window._fleet_sort_clicked(3)
        self.wait()
        values = [r['today'] for r in self.window._fleet_result['rows']]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_date_range_and_missing_history_never_invent_zero(self):
        self.seed()
        self.window.chart_range.setCurrentIndex(1)
        self.wait()
        self.assertEqual(len(self.window.daily_chart.samples), 30)
        self.window.fleet_date.setDate(self.window.fleet_date.date().addYears(-1))
        self.wait()
        self.assertEqual(self.window.kpis['Awake today'].text(), 'Not available')
        self.assertTrue(all(r['today'] is None for r in self.window._fleet_result['rows']))
        self.assertFalse(any(s['seconds'] is not None for s in self.window.daily_chart.samples))

    def test_pending_approval_and_invitation_revocation_from_admin(self):
        self.seed()
        self.window.tabs.setCurrentIndex(2)
        self.assertEqual(self.window.admin_pending.rowCount(), 2)
        self.window.admin_pending.selectRow(0)
        self.window.admin_pending.cellWidget(0, 2).findChild(QPushButton).click()
        self.wait()
        self.assertEqual(self.window.admin_pending.rowCount(), 1)
        self.window.add_device()
        self.wait()
        identifier = self.window.add_device_sheet.issued['invitation_id']
        self.window.add_device_sheet.close()
        for index in range(self.window.invitation_table.rowCount()):
            if self.window.invitation_table.item(index, 0).text() == identifier:
                self.window.invitation_table.selectRow(index)
                break
        self.window.invitation_table.cellWidget(index, 5).findChild(QPushButton).click()
        self.wait()
        self.assertTrue(next(i for i in self.runtime._store.invitations() if i['invitation_id'] == identifier)['revoked'])

    def test_native_keyboard_indicator_reduced_motion_and_compact_vietnamese(self):
        self.seed()
        self.window.language_choice.setCurrentIndex(1)
        self.window.resize(1024, 700)
        self.wait()
        self.assertEqual(self.window.width(), 1024)
        self.assertEqual(self.window.fleet_grid.getItemPosition(self.window.fleet_grid.indexOf(self.window.overview_right))[0], 1)
        self.assertIn('Máy chủ đang chạy', self.window.server_status.text())
        self.assertEqual(self.window.fleet_table.horizontalHeaderItem(3).text(), 'Chạy hôm nay')
        self.window.fleet_table.setFocus()
        self.window.fleet_table.selectRow(0)
        QTest.keyClick(self.window.fleet_table, Qt.Key.Key_Down)
        self.assertIn('Demo-PC-02', self.window.fleet_detail_text.text())
        with patch.dict('os.environ', {'LIFETIME_REDUCE_MOTION': '1'}):
            self.window.tabs.setCurrentIndex(2)
            bar = self.window.tabs.tabBar()
            self.assertEqual(bar.animation.state(), bar.animation.State.Stopped)
            self.assertEqual(bar.indicator.geometry().x(), bar.tabRect(2).x() + 5)
        self.window.organisation_edit.setText('Demo Organisation')
        self.window._mutate('organisation', dict(name='Demo Organisation'))
        self.wait()
        self.assertIn('Demo Organisation', self.window.network_label.text())


if __name__ == '__main__':
    unittest.main()
