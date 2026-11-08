"""Offscreen native dashboard tests; AdminClient calls are explicitly mocked."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

if importlib.util.find_spec('PyQt6') is None:
    raise unittest.SkipTest('PyQt6 is required for server dashboard checks')

from PyQt6.QtCore import QDate, QItemSelectionModel, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel

import lifetime_server_gui as gui
from qt_test_support import qt_app


def device(number, status='approved'):
    return dict(device_id=str(uuid.UUID(int=number + 1)), identity={
        'display_name': f'Computer {number:03}', 'asset_tag': f'PC-{number:03}',
        'department': 'Support' if number % 2 else 'Sales', 'location': 'HCM',
        'deployment_date': ''}, status=status,
        reporting_status='reporting' if status == 'approved' else 'unavailable',
        pending_requests=[], last_received_at='2026-11-07T12:00:00+00:00',
        totals={'measured_seconds': 3600, 'imported_seconds': 7200, 'baseline_seconds': 10800},
        coverage='unknown', policy={'history': False, 'resources': False}, history=None,
        readings=None, readings_received_at=None, records=[])


class FakeAdmin:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.rows = []
        self.calls = []
        self.block = {}
        self.fail = set()
        self.interval = 60
        self.retained_before = None
        self.gui_thread = threading.get_ident()
        self.lock = threading.Lock()

    def call(self, operation, **arguments):
        if threading.get_ident() == self.gui_thread:
            raise AssertionError('I/O ran on the GUI thread')
        with self.lock:
            self.calls.append((operation, copy.deepcopy(arguments), threading.get_ident()))
        if operation in self.block:
            self.block[operation].wait(5)
        if operation in self.fail:
            raise OSError('SECRET-admin-token-network-path-must-not-be-shown')
        if operation == 'devices':
            rows = copy.deepcopy(self.rows)
            rows = [row for row in rows if
                    arguments.get('search', '').casefold() in str(row['identity']).casefold()
                    and (not arguments.get('group') or row['identity']['department'] == arguments['group'])
                    and (not arguments.get('status') or row['status'] == arguments['status'])]
            offset, limit = arguments.get('offset', 0), arguments.get('limit', 25)
            return rows[offset:offset + limit]
        if operation == 'device':
            return copy.deepcopy(next(row for row in self.rows if row['device_id'] == arguments['device_id']))
        if operation == 'overview':
            counts = dict.fromkeys(('total', 'reporting', 'report-overdue', 'pending', 'approved', 'revoked', 'denied', 'unavailable'), 0)
            counts['total'] = len(self.rows)
            for row in self.rows:
                counts[row['status']] += 1
                counts[row['reporting_status']] += 1
            known = [row for row in self.rows if row.get('totals') is not None]
            return dict(counts=counts,
                        totals={field: sum(row['totals'][field] for row in known)
                                for field in ('measured_seconds', 'imported_seconds', 'baseline_seconds')} if known else None,
                        known_devices=len(known), unknown_devices=len(self.rows) - len(known),
                        recent_events=[dict(device_id=row['device_id'], kind='report-received', time=row['last_received_at'])
                                       for row in self.rows[:20]],
                        health='available', schema_version=4, server_time='2026-11-07T12:00:00+00:00')
        if operation == 'configuration':
            return dict(reporting_interval=self.interval, retained_before=self.retained_before)
        if operation == 'set_configuration':
            self.interval = arguments['reporting_interval']
            return dict(reporting_interval=self.interval, retained_before=self.retained_before)
        if operation == 'identity':
            row = next(row for row in self.rows if row['device_id'] == arguments['device_id'])
            row['identity'] = arguments['identity']
            return copy.deepcopy(row)
        if operation in ('approve', 'deny'):
            for row in self.rows:
                if any(request['request_id'] == arguments['request_id'] for request in row['pending_requests']):
                    row['pending_requests'] = []
                    row['status'] = 'approved' if operation == 'approve' else 'denied'
            return dict(status='approved' if operation == 'approve' else 'denied')
        if operation == 'revoke':
            next(row for row in self.rows if row['device_id'] == arguments['device_id'])['status'] = 'revoked'
            return dict(status='revoked')
        if operation == 'retain':
            self.retained_before = arguments['before']
            return {'deleted': 1}
        if operation in ('backup', 'restore', 'refresh'):
            return dict(arguments)
        if operation == 'code':
            return dict(code='secret-pairing-code', expires_at='2026-11-07T12:10:00Z', uses=arguments['uses'])
        raise ValueError('Unknown mock operation')


class ServerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.api = FakeAdmin(self.root)
        self.client_patch = self.enterContext(patch.object(gui, 'AdminClient', return_value=self.api))
        self.window = gui.ServerWindow(self.root, 8123)
        self.window.timer.stop()
        self.window.show()
        self.addCleanup(self.cleanup_window)
        self.wait_idle()

    def cleanup_window(self):
        for event in self.api.block.values():
            event.set()
        self.window.close()
        self.wait(lambda: not self.window._jobs)

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out waiting for dashboard state')

    def wait_idle(self):
        self.app.processEvents()
        self.wait(lambda: not self.window._jobs and not self.window._search_timer.isActive())

    def load(self, rows):
        self.api.rows = copy.deepcopy(rows)
        self.window.refresh()
        self.wait_idle()

    def select(self, identifier):
        for row in range(self.window.table.rowCount()):
            if self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                self.window.table.selectRow(row)
                self.wait_idle()
                return
        self.fail('Device not present on page')

    def arguments(self, operation):
        return [arguments for op, arguments, thread in self.api.calls if op == operation]

    def test_zero_devices_truthful_empty_and_unknown_totals(self):
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertEqual(self.window.counts['All devices'].text(), '0')
        self.assertEqual(self.window.fleet_totals['Measured'].text(), 'Not available')
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.save_identity_button.isEnabled())
        self.assertFalse(self.window.next_button.isEnabled())
        self.assertIn('No devices match', self.window.empty_label.text())
        self.assertIn('Unknown', gui.text('Unknown'))

    def test_fleet_overview_is_independent_of_pagination_and_filters(self):
        self.load([device(i) for i in range(60)])
        self.assertEqual(self.window.table.rowCount(), 25)
        self.assertEqual(self.window.counts['All devices'].text(), '60')
        self.assertEqual(self.window.fleet_totals['Measured'].text(), '60.00')
        self.assertEqual(self.window.events.rowCount(), 20)
        self.assertEqual(self.window.events.accessibleName(), 'Recent events')
        self.window.search.setText('Computer 059')
        self.wait_idle()
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.counts['All devices'].text(), '60')
        self.assertEqual(self.arguments('overview'), [{}] * len(self.arguments('overview')))

    def test_unknown_device_totals_do_not_become_zero(self):
        known, unknown = device(1), device(2)
        unknown['totals'] = None
        self.load([known, unknown])
        self.assertIn('1 devices have snapshots; 1 have no snapshot', self.window.snapshot_scope.text())
        self.assertEqual(self.window.fleet_totals['Measured'].text(), '1.00')
        self.select(unknown['device_id'])
        self.assertIn('Measured: Not available', self.window.detail_text.toPlainText())
        self.assertIn('Coverage: Unknown', self.window.detail_text.toPlainText())

    def test_debounced_search_group_status_and_paging_bounds(self):
        self.load([device(i) for i in range(60)])
        self.window.next_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1]['offset'], 25)
        self.window.previous_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1]['offset'], 0)
        before = len(self.arguments('devices'))
        self.window.search.setText('C')
        self.window.search.setText('Co')
        self.window.search.setText('Computer 001')
        self.assertEqual(len(self.arguments('devices')), before)
        self.wait_idle()
        self.assertEqual(len(self.arguments('devices')), before + 1)
        self.window.group.setText('Support')
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData('approved'))
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1],
                         dict(limit=25, offset=0, search='Computer 001', group='Support', status='approved'))
        self.window.page_size.setCurrentIndex(self.window.page_size.findData(500))
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1]['limit'], 500)
        self.assertEqual(max(self.window.page_size.itemData(i) for i in range(self.window.page_size.count())), 500)

    def test_selection_uuid_sort_scroll_focus_and_unsaved_identity_survive_refresh(self):
        self.load([device(i) for i in range(25)])
        self.window.tabs.setCurrentIndex(1)
        chosen = self.api.rows[7]['device_id']
        self.select(chosen)
        self.window.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.window.table.setFocus()
        self.window.table.verticalScrollBar().setValue(4)
        scroll = self.window.table.verticalScrollBar().value()
        field = self.window.identity_fields['display_name']
        field.setText('Unsaved edit')
        field.textEdited.emit(field.text())
        self.api.rows.reverse()
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(self.window.selected_device_ids(), [chosen])
        self.assertEqual(self.window.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.window.table.verticalScrollBar().value(), scroll)
        self.assertTrue(self.window.table.hasFocus())
        self.assertEqual(field.text(), 'Unsaved edit')

    def test_multiple_selected_uuids_survive_sort_and_poll(self):
        self.load([device(i) for i in range(25)])
        identifiers = {self.api.rows[1]['device_id'], self.api.rows[9]['device_id']}
        table = self.window.table
        for row in range(table.rowCount()):
            if table.item(row, 0).data(Qt.ItemDataRole.UserRole) in identifiers:
                table.selectionModel().select(table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.wait_idle()
        table.sortItems(1, Qt.SortOrder.AscendingOrder)
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(set(self.window.selected_device_ids()), identifiers)
        self.assertIsNone(self.window._detail)
        self.assertFalse(self.window.save_identity_button.isEnabled())

    def test_requests_run_off_gui_thread_and_do_not_freeze_event_loop(self):
        gate = threading.Event()
        self.api.block['devices'] = gate
        self.window.refresh()
        self.wait(lambda: self.window._list_busy)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(threading.get_ident()))
        self.wait(lambda: ticks)
        self.assertFalse(gate.is_set())
        self.assertTrue(all(thread != threading.get_ident() for _, _, thread in self.api.calls))
        gate.set()
        self.wait_idle()

    def test_stale_filter_result_is_discarded_and_callbacks_stay_on_gui_thread(self):
        self.load([device(1), device(2)])
        gate = threading.Event()
        self.api.block['devices'] = gate
        self.window.refresh()
        self.wait(lambda: self.window._list_busy)
        self.window.search.setText('Computer 002')
        callback_threads = []
        render = self.window._render_rows
        def record(rows):
            callback_threads.append(threading.get_ident())
            render(rows)
        with patch.object(self.window, '_render_rows', side_effect=record):
            gate.set()
            self.wait_idle()
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), 'Computer 002')
        self.assertTrue(callback_threads)
        self.assertEqual(set(callback_threads), {threading.get_ident()})

    def test_details_separate_sources_and_permission_and_stale_reading(self):
        row = device(1)
        row['readings'] = {'observed_at': '2020-01-01T00:00:00Z', 'system': 'PRIVATE-OS'}
        self.load([row])
        self.select(row['device_id'])
        detail = self.window.detail_text.toPlainText()
        self.assertIn('Measured: 1.00', detail)
        self.assertIn('Imported: 2.00', detail)
        self.assertIn('Baseline: 3.00', detail)
        self.assertIn('Not permitted', detail)
        self.assertNotIn('PRIVATE-OS', detail)
        self.api.rows[0]['policy']['resources'] = True
        self.api.rows[0]['readings_received_at'] = '2020-01-01T00:01:00Z'
        self.window.refresh()
        self.wait_idle()
        detail = self.window.detail_text.toPlainText()
        self.assertIn('Stale report', detail)
        self.assertIn('PRIVATE-OS', detail)
        self.assertIn('Received at: 2020-01-01', detail)

    def test_identity_editor_validation_and_selected_device_only(self):
        self.load([device(1), device(2)])
        chosen = self.api.rows[1]['device_id']
        self.select(chosen)
        self.window.identity_fields['deployment_date'].setText('not-a-date')
        self.window.save_identity_button.click()
        self.assertEqual(self.arguments('identity'), [])
        self.assertIn('Identity is invalid', self.window.status_label.text())
        self.window.identity_fields['deployment_date'].setText('2026-11-07')
        self.window.identity_fields['display_name'].setText('Máy hỗ trợ')
        self.window.save_identity_button.click()
        self.wait_idle()
        arguments = self.arguments('identity')[-1]
        self.assertEqual(arguments['device_id'], chosen)
        self.assertEqual(set(arguments['identity']), set(gui.IDENTITY_FIELDS))
        self.assertEqual(arguments['identity']['display_name'], 'Máy hỗ trợ')

    def test_pending_approve_deny_revoke_and_refresh_routes(self):
        row = device(1, 'pending')
        row['pending_requests'] = [{'request_id': str(uuid.uuid4()), 'status': 'pending'}]
        self.load([row])
        self.select(row['device_id'])
        self.assertTrue(self.window.approve_button.isEnabled())
        self.assertFalse(self.window.request_refresh_button.isEnabled())
        self.window.approve_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('approve'), [{'request_id': row['pending_requests'][0]['request_id']}])
        self.window.request_refresh_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('refresh'), [{'device_id': row['device_id']}])
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.revoke_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('revoke'), [{'device_id': row['device_id']}])

    def test_deny_and_cancel_revoke_do_not_approve_or_revoke(self):
        row = device(1, 'pending')
        request = str(uuid.uuid4())
        row['pending_requests'] = [{'request_id': request, 'status': 'pending'}]
        self.load([row])
        self.select(row['device_id'])
        with patch.object(self.window, '_confirm', return_value=False):
            self.window.revoke_button.click()
        self.assertEqual(self.arguments('revoke'), [])
        self.window.deny_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('deny'), [{'request_id': request}])
        self.assertFalse(self.window.approve_button.isEnabled())

    def test_reporting_interval_limits_and_routes(self):
        self.assertEqual(self.window.interval.minimum(), 5)
        self.assertEqual(self.window.interval.maximum(), 3600)
        self.assertEqual(self.window.interval.value(), 60)
        self.window.interval.setValue(120)
        self.window.interval_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('set_configuration'), [{'reporting_interval': 120}])
        self.assertEqual(self.api.interval, 120)

    def test_manual_retention_backup_restore_dialogs_and_cancellation(self):
        self.window.retention_date.setDate(QDate(2026, 1, 2))
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.retain_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('retain'), [{'before': '2026-01-02T00:00:00+00:00'}])
        with patch.object(gui.QFileDialog, 'getSaveFileName', return_value=('backup.sqlite', '')):
            self.window.backup_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('backup'), [{'path': 'backup.sqlite'}])
        with patch.object(gui.QFileDialog, 'getOpenFileName', return_value=('backup.sqlite', '')), \
                patch.object(self.window, '_confirm', return_value=True) as confirm:
            self.window.restore_button.click()
        self.wait_idle()
        self.assertIn('invalidates every device credential', confirm.call_args.args[0])
        self.assertEqual(self.arguments('restore'), [{'path': 'backup.sqlite'}])
        with patch.object(gui.QFileDialog, 'getOpenFileName', return_value=('', '')):
            self.window.restore_button.click()
        self.assertEqual(len(self.arguments('restore')), 1)

    def test_enrollment_certificate_read_and_bundle_creation_in_worker(self):
        reads = []
        original = Path.open
        def read(path, *args, **kwargs):
            reads.append(threading.get_ident())
            return original(path, *args, **kwargs)
        (self.root / 'server-cert.pem').write_text('public certificate')
        with patch.object(Path, 'open', read), patch.object(gui, 'encode_bundle', return_value='safe-enrollment-bundle') as encode:
            self.window.code_button.click()
            self.wait_idle()
        self.assertEqual(self.window.bundle_output.toPlainText(), 'safe-enrollment-bundle')
        self.assertEqual(self.arguments('code'), [{'ttl': 600, 'uses': 1}])
        self.assertTrue(reads)
        self.assertTrue(all(thread != threading.get_ident() for thread in reads))
        self.assertEqual(encode.call_count, 2)
        self.assertNotIn('admin-token', self.window.bundle_output.toPlainText())

    def test_export_signal_selected_and_fleet_opens_shared_dialog(self):
        self.load([device(1), device(2)])
        self.select(self.api.rows[0]['device_id'])
        received = []
        self.window.export_requested.connect(received.append)
        with patch('lifetime_server_export.ServerExportDialog') as dialog:
            self.window.export_button.click()
            self.window.export_requested.emit(None)
        self.assertEqual(received, [[self.api.rows[0]['device_id']], None])
        self.assertEqual(dialog.call_count, 2)
        self.assertIsNone(dialog.call_args.kwargs['device_ids'])
        self.assertEqual(dialog.return_value.exec.call_count, 2)

    def test_vietnamese_live_translation_native_keyboard_and_small_window(self):
        self.load([device(1)])
        self.window.language_choice.setCurrentIndex(1)
        self.assertEqual(self.window.tabs.tabText(0), 'Tổng quan')
        self.assertEqual(self.window.tabs.tabText(1), 'Thiết bị')
        self.assertEqual(self.window.save_identity_button.text(), 'Lưu thông tin')
        self.assertEqual(self.window.table.horizontalHeaderItem(0).text(), 'Tên hiển thị')
        self.window.tabs.setCurrentIndex(1)
        self.window.resize(440, 520)
        self.app.processEvents()
        self.window.search.setFocus()
        QTest.keyClick(self.window.search, Qt.Key.Key_Tab)
        self.assertTrue(self.window.group.hasFocus())
        self.assertLessEqual(self.window.tabs.width(), self.window.width())
        self.assertTrue(self.window.detail_scroll.widgetResizable())
        self.assertTrue(self.window.export_button.accessibleName())

    def test_overview_failure_is_redacted_and_keeps_last_successful_data(self):
        self.load([device(1)])
        self.api.fail.add('overview')
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(self.window.counts['All devices'].text(), '1')
        self.assertIn('Overview unavailable', self.window.overview_health.text())
        all_text = '\n'.join(widget.text() for widget in self.window.findChildren(QLabel))
        self.assertNotIn('SECRET', all_text)
        self.assertNotIn('admin-token', all_text)

    def test_parallel_success_does_not_hide_error_and_refresh_recovers(self):
        self.api.fail.add('devices')
        self.window.refresh()
        self.wait_idle()
        self.assertIn('Request failed', self.window.status_label.text())
        self.api.fail.clear()
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(self.window.status_label.text(), 'Ready')

    def test_live_language_keeps_identity_edits_and_translates_dynamic_labels(self):
        self.load([device(1)])
        self.select(self.api.rows[0]['device_id'])
        field = self.window.identity_fields['display_name']
        field.setText('Unsaved')
        field.textEdited.emit('Unsaved')
        self.window.language_choice.setCurrentIndex(1)
        self.wait_idle()
        self.assertEqual(field.text(), 'Unsaved')
        self.assertTrue(self.window.retention_boundary.text().startswith('Mốc lưu giữ'))
        self.assertEqual(self.window.table.accessibleName(), 'Thiết bị')
        self.assertEqual(self.window.page_size.accessibleName(), 'Số dòng mỗi trang')

    def test_no_dummy_controls_or_local_database_access(self):
        self.assertNotIn('lifetime_server_store', gui.__dict__)
        self.assertEqual(gui.THEMES['dark']['bg'], '#0B1426')
        self.assertEqual(self.window.tasks.parent(), self.window)
        self.assertFalse(any(name.startswith('win32') for name in gui.__dict__))

    def test_close_does_not_wait_for_network_or_update_closed_window(self):
        gate = threading.Event()
        self.api.block['devices'] = gate
        self.window.refresh()
        self.wait(lambda: self.window._list_busy)
        started = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - started, 0.2)
        gate.set()
        self.wait(lambda: not self.window._jobs)


if __name__ == '__main__':
    unittest.main()
