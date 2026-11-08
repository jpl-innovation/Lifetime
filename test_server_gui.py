"""Offscreen native dashboard tests; AdminClient calls are explicitly mocked."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
from unittest.mock import Mock, patch
import uuid

if importlib.util.find_spec('PyQt6') is None:
    raise unittest.SkipTest('PyQt6 is required for server dashboard checks')

from PyQt6.QtCore import QDate, QItemSelectionModel, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QPushButton, QComboBox

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
        if operation == 'live':
            return dict(devices=[], ring=[], device_id=arguments.get('device_id'))
        if operation == 'server_info':
            return dict(address='https://localhost:8443', host='127.0.0.1', port=8443,
                        name='Demo-Server', fingerprint='0' * 64, discovery=False)
        if operation == 'invitations':
            return []
        if operation == 'pending_enrollments':
            return [dict(request_id=p['request_id'], device_id=r['device_id'], name=r['identity']['display_name'],
                         created_at='2026-11-08T12:00:00Z') for r in self.rows for p in r['pending_requests']]
        if operation == 'fleet':
            rows = [dict(device_id=r['device_id'], name=r['identity']['display_name'],
                         group=r['identity']['department'], identity=r['identity'],
                         status=r['reporting_status'] if r['status'] == 'approved' else r['status'],
                         today=None, last=r['last_received_at'], totals=r['totals'],
                         coverage='unknown', client_version=None, tracking_since=None) for r in self.rows]
            counts = dict(total=len(rows), enrolled=sum(r['status'] == 'approved' for r in self.rows),
                          connected=sum(r['status'] == 'reporting' for r in rows),
                          overdue=sum(r['status'] == 'report-overdue' for r in rows),
                          pending=sum(r['status'] == 'pending' for r in rows))
            filtered = [r for r in rows if arguments.get('search', '').casefold() in r['name'].casefold()
                        and (not arguments.get('group') or r['group'] == arguments['group'])
                        and (not arguments.get('status') or r['status'] == arguments['status'] or
                             next(d for d in self.rows if d['device_id'] == r['device_id'])['status'] == arguments['status'])]
            filtered.sort(key=lambda r: str(r[arguments.get('sort', 'name')]), reverse=arguments.get('descending', False))
            offset, limit = arguments.get('offset', 0), arguments.get('limit', 6)
            return dict(counts=counts, today=dict(seconds=None, devices=0), daily=[],
                        rows=filtered[offset:offset+limit], matching=len(filtered),
                        groups=sorted({r['group'] for r in rows}), recent_events=[
                            dict(device_id=r['device_id'], name=r['name'], kind='report-received', time=r['last']) for r in rows[:20]],
                        updated_at='2026-11-08T12:00:00Z', date=arguments.get('day'))
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
        if operation in ('backup', 'restore', 'refresh', 'send_command', 'wake'):
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
        self.wait(lambda: not self.window._jobs and not self.window._search_timer.isActive()
                  and not self.window._readings_timer.isActive())

    def load(self, rows):
        self.api.rows = copy.deepcopy(rows)
        self.window.refresh()
        self.wait_idle()

    def select(self, identifier):
        for row in range(self.window.table.rowCount()):
            if self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                self.window._select_box(identifier)  # selectRow would obey a Ctrl state left by earlier tests
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
        self.assertIn('No computers match', self.window.empty_label.text())
        self.assertIn('Unknown', gui.text('Unknown'))

    def test_fleet_overview_is_independent_of_pagination_and_filters(self):
        self.load([device(i) for i in range(60)])
        self.assertEqual(self.window.table.rowCount(), 25)
        self.assertEqual(self.window.counts['All devices'].text(), '60')
        self.assertEqual(self.window.fleet_totals['Measured'].text(), '60.00')
        # The overview panel shows the five most recent events without a scrollbar; the full log is in Administration.
        self.assertEqual(self.window.events.rowCount(), 5)
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
        self.assertIn('Measured\nNot available', self.window.detail_text.toPlainText())
        self.assertIn('Coverage\nUnknown', self.window.detail_text.toPlainText())

    def test_debounced_search_group_status_and_paging_bounds(self):
        self.load([device(i) for i in range(60)])
        self.window.next_button.click()
        self.wait_idle()
        self.assertEqual(self.window._offset, 25)
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
        self.window.group.setCurrentIndex(self.window.group.findData('Support'))
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData('approved'))
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1],
                         dict(limit=500, offset=0, search='Computer 001', group='Support', status='approved'))
        self.window.page_size.setCurrentIndex(self.window.page_size.findData(500))
        self.wait_idle()
        self.assertEqual(self.arguments('devices')[-1]['limit'], 500)
        self.assertEqual(max(self.window.page_size.itemData(i) for i in range(self.window.page_size.count())), 500)

    def test_selection_uuid_sort_scroll_focus_and_unsaved_identity_survive_refresh(self):
        self.load([device(i) for i in range(25)])
        self.window.tabs.setCurrentIndex(1)
        chosen = self.api.rows[7]['device_id']
        self.select(chosen)
        self.window.device_order.setCurrentIndex(self.window.device_order.findData('name'))
        self.wait_idle()
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
        self.assertEqual(self.window.device_order.currentData(), 'name')
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
        self.window.device_order.setCurrentIndex(self.window.device_order.findData('last'))
        self.wait_idle()
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
        self.assertIn('Measured\n1.00', detail)
        self.assertIn('Imported\n2.00', detail)
        self.assertIn('Baseline\n3.00', detail)
        self.assertIn('Not permitted', detail)
        self.assertNotIn('PRIVATE-OS', detail)
        self.api.rows[0]['policy']['resources'] = True
        self.api.rows[0]['readings_received_at'] = '2020-01-01T00:01:00Z'
        self.window.refresh()
        self.wait_idle()
        detail = self.window.detail_text.toPlainText()
        self.assertIn('Stale report', detail)
        self.assertIn('PRIVATE-OS', detail)
        self.assertIn('Received at: ' + self.window._timestamp('2020-01-01T00:01:00Z'), detail)

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
        row['last_received_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        row['pending_requests'] = [{'request_id': str(uuid.uuid4()), 'status': 'pending'}]
        self.load([row])
        self.select(row['device_id'])
        self.assertTrue(self.window.approve_button.isEnabled())
        self.assertFalse(self.window.request_refresh_button.isEnabled())
        self.window.approve_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('approve'), [{'request_id': row['pending_requests'][0]['request_id']}])
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.request_refresh_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('send_command'), [{'device_id': row['device_id'], 'action': 'refresh'}])
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.revoke_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('revoke'), [{'device_id': row['device_id']}])
        with patch.object(self.window, '_confirm', return_value=False):
            self.window.delete_button.click()
        self.assertEqual(self.arguments('delete'), [])
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.delete_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('delete'), [{'device_id': row['device_id']}])

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
        self.assertIn('computers need a new code and approval', confirm.call_args.args[0])
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
        self.assertEqual(self.window.table.horizontalHeaderItem(0).text(), gui.text('Device', 'vi'))
        self.window.tabs.setCurrentIndex(1)
        self.window.resize(440, 520)
        self.app.processEvents()
        self.window.search.setFocus()
        QTest.keyClick(self.window.search, Qt.Key.Key_Tab)
        self.assertTrue(self.window.group.hasFocus())
        self.assertLessEqual(self.window.tabs.width(), self.window.width())
        self.assertTrue(self.window.tabs.widget(1).widgetResizable())
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

    def test_invitation_failure_is_visible_in_the_main_window(self):
        self.api.fail.add('invitation')
        self.window.add_device_button.click()
        self.wait_idle()
        self.assertTrue(self.window.status_label.isVisible())
        self.assertEqual(self.window.status_label.text(), "Can't reach the Lifetime server. Make sure it is running.")
        self.assertTrue(self.window.add_device_button.isEnabled())
        self.assertFalse(hasattr(self.window, 'add_device_sheet'))

    def test_parallel_success_does_not_hide_error_and_refresh_recovers(self):
        self.api.fail.add('devices')
        self.window.refresh()
        self.wait_idle()
        self.assertIn("Can't reach the Lifetime server", self.window.status_label.text())
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

    def test_devices_structure_icons_dates_export_and_pagination(self):
        self.load([device(i) for i in range(60)])
        self.window.tabs.setCurrentIndex(1)
        self.assertIsInstance(self.window.group, QComboBox)
        self.assertEqual(self.window.table.parentWidget().objectName(), 'panel')
        self.assertEqual(self.window.device_detail_panel.objectName(), 'panel')
        self.assertEqual(self.window.page_label.text(), 'Showing 1 to 25 of 60 devices')
        self.assertEqual([self.window.device_pages.itemAt(i).widget().text() for i in range(3)], ['1', '2', '3'])
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertNotEqual(self.window.export_button.objectName(), 'primary')
        for row in range(self.window.table.rowCount()):
            self.assertNotIn('●', self.window.table.item(row, 2).text())
            self.assertNotIn('â—', self.window.table.item(row, 2).text())
            self.assertFalse(self.window.table.item(row, 2).icon().isNull())
            self.assertNotRegex(self.window.table.item(row, 4).text(), r'\d{4}-\d{2}-\d{2}')
        self.assertTrue(self.window.table.item(0, 3).textAlignment() & Qt.AlignmentFlag.AlignRight)
        self.assertTrue(self.window.table.horizontalHeaderItem(3).textAlignment() & Qt.AlignmentFlag.AlignRight)
        self.select(self.api.rows[0]['device_id'])
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertEqual(self.window.export_button.objectName(), 'primary')
        self.window.table.clearSelection()
        self.assertFalse(self.window.export_button.isEnabled())
        self.window.device_pages.itemAt(2).widget().click()
        self.wait_idle()
        self.assertEqual(self.window._offset, 50)
        self.assertEqual(self.window.page_label.text(), 'Showing 51 to 60 of 60 devices')
        self.assertFalse(self.window.next_button.isEnabled())

    def test_detail_fields_copy_localized_dates_and_responsive_grid(self):
        row = device(1)
        row['identity']['deployment_date'] = '2026-01-02'
        row['client_version'] = '1.0.1'
        row['policy']['history'] = True
        row['history'] = {'recording_started': '2026-01-01T00:00:00Z', 'daily': {'2026-11-08': 60}}
        self.load([row])
        self.select(row['device_id'])
        self.assertEqual(self.window.detail_name.text(), row['identity']['display_name'])
        self.assertEqual(self.window.detail_total.text(), '3.0 h')
        for key in ('Asset tag', 'Department', 'Location', 'Deployment date', 'Last seen', 'Client version', 'Coverage', 'Tracking since'):
            self.assertIn(key + '\n', self.window.detail_text.toPlainText())   # labels carry no colon, as on Overview
        self.assertIn('2 Jan 2026', self.window.detail_text.toPlainText())
        self.assertNotRegex(self.window.detail_text.toPlainText(), r'\d{4}-\d{2}-\d{2}')
        self.window.copy_device_id.click()
        self.assertEqual(self.app.clipboard().text(), row['device_id'])
        self.window.language_choice.setCurrentIndex(1)
        self.wait_idle()
        self.assertNotIn('2026-01-02', self.window.identity_fields['deployment_date'].text())
        self.window.save_identity_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('identity')[-1]['identity']['deployment_date'], '2026-01-02')
        self.window.tabs.setCurrentIndex(1)
        for width, height, expected in ((1600, 1000, (0, 1)), (1024, 700, (0, 1))):
            self.window.resize(width, height)
            self.app.processEvents()
            index = self.window.device_grid.indexOf(self.window.device_detail_scroll)
            self.assertEqual(self.window.device_grid.getItemPosition(index)[:2], expected)
            self.assertLessEqual(self.window.tabs.widget(1).horizontalScrollBar().maximum(), 0)

    def test_relative_dates_and_localized_fallback(self):
        stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=12)).isoformat()
        self.assertIn(self.window._relative(stamp), ('12 s ago', '13 s ago'))
        self.assertEqual(self.window._relative('2020-01-02T12:00:00Z'), '2 Jan 2020')
        self.window.language_choice.setCurrentIndex(1)
        self.assertEqual(self.window._relative('2020-01-02T12:00:00Z'), self.window._date('2020-01-02'))

    def test_administration_row_actions_expiry_and_busy_state(self):
        row = device(1, 'pending')
        request_id = str(uuid.uuid4())
        row['pending_requests'] = [{'request_id': request_id, 'status': 'pending'}]
        self.load([row])
        result = copy.deepcopy(self.window._admin_result)
        result['invitations'] = [dict(invitation_id='DEMO', expires_at='2099-01-02T12:00:00Z',
                                      remaining=2, failures=1, revoked=False)]
        self.window._render_server_info(result)
        invitation_table = self.window.invitation_table
        self.assertEqual(invitation_table.parentWidget().objectName(), 'panel')
        self.assertIn('2 Jan 2099', invitation_table.item(0, 1).text())
        self.assertFalse(invitation_table.item(0, 4).icon().isNull())
        self.assertEqual(self.window.retention_date.displayFormat(), 'd MMM yyyy')
        self.assertTrue(self.window.fingerprint.isReadOnly())
        approve = self.window._admin_row_actions[0]
        self.assertEqual(approve.objectName(), 'primary')
        self.window._mutation_busy = True
        self.window._admin_selection_changed()
        self.assertFalse(approve.isEnabled())
        self.assertFalse(self.window._invitation_row_actions[0].isEnabled())
        self.window._mutation_busy = False
        self.window._admin_selection_changed()
        approve.click()
        self.wait_idle()
        self.assertEqual(self.arguments('approve'), [{'request_id': request_id}])
        self.window.language_choice.setCurrentIndex(1)
        self.window._render_server_info(result)
        self.assertIn(gui.text('month.1', 'vi'), invitation_table.item(0, 1).text())
        self.assertNotRegex(invitation_table.item(0, 1).text(), r'\d{4}-\d{2}-\d{2}')

    def test_no_dummy_controls_or_local_database_access(self):
        self.assertNotIn('lifetime_server_store', gui.__dict__)
        self.assertEqual(gui.THEMES['dark']['bg'], '#0B1426')
        self.assertEqual(self.window.tasks.parent(), self.window)
        self.assertFalse(any(name.startswith('win32') for name in gui.__dict__))

    def test_cards_attention_filters_keyboard_and_reuse_for_200_computers(self):
        rows = [device(i) for i in range(200)]
        rows[-1]['attention'] = ['disk_low']
        self.load(rows)
        self.window.tabs.setCurrentIndex(1)
        box = self.window.table.cellWidget(0, 0)
        self.assertEqual(box.device_id, rows[-1]['device_id'])
        self.assertIn('Low disk space', box.attention.text())
        self.assertIn('Last seen', box.bottom.text())
        self.assertIn('CPU:', box.spec.text())
        self.assertTrue(box.menu_button.accessibleName())
        box.setFocus()
        QTest.keyClick(box, Qt.Key.Key_Return)
        self.wait_idle()
        self.assertEqual(self.window._detail['device_id'], rows[-1]['device_id'])
        self.window.refresh()
        self.wait_idle()
        self.assertIs(self.window.table.cellWidget(0, 0), box)
        self.window.device_chips['attention'].click()
        self.wait_idle()
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window._device_matching, 1)
        self.window.device_chips['offline'].click()
        self.wait_idle()
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertIsNone(self.window._detail)

    def test_visible_readings_are_bounded_cached_and_scrubbed_on_revocation(self):
        rows = [device(i) for i in range(200)]
        for row in rows:
            row['policy']['resources'] = True
            row['specs'] = dict(ram_bytes=16 * 1024**3)
            row['performance'] = dict(cpu_percent=42, ram_used_bytes=8 * 1024**3)
        self.load(rows)
        self.window.tabs.setCurrentIndex(1)
        self.wait_idle()
        calls = len(self.arguments('device'))
        self.assertGreater(calls, 0)
        self.assertLess(calls, 10)
        box = self.window.table.cellWidget(0, 0)
        self.assertEqual(box.meters['CPU'][1].value(), 42)
        self.assertEqual(box.meters['RAM'][1].value(), 50)
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(len(self.arguments('device')), calls)
        for row in self.api.rows:
            row['policy']['resources'] = False
        self.window.refresh()
        self.wait_idle()
        self.assertFalse(box.meters['CPU'][1].isVisible())
        self.assertIn('Not available', box.meters['CPU'][0].text())

    def test_loading_placeholders_clear_and_refresh_shows_progress_then_recovers(self):
        self.wait(lambda: all(skeleton.isHidden() for skeleton in self.window.skeletons.values()))
        self.window.manual_refresh()
        self.assertFalse(self.window.refresh_button.isEnabled())
        self.assertEqual(self.window.refresh_button.text(), 'Refreshing…')
        self.wait_idle()
        self.wait(lambda: self.window.refresh_button.isEnabled())
        self.assertEqual(self.window.refresh_button.text(), 'Refresh')
        self.assertFalse(self.window.loading_bar.busy)

    def test_routine_status_stays_quiet_and_problems_show_a_strip(self):
        self.assertTrue(self.window.notice.isHidden())
        self.window.status_label.setText('Something needs attention')
        self.assertFalse(self.window.notice.isHidden())
        self.window.status_label.setText(self.window.t('Ready'))
        self.assertTrue(self.window.notice.isHidden())

    def test_segmented_stack_native_keyboard_shortcuts_and_interruptible_motion(self):
        from PyQt6.QtWidgets import QStackedWidget
        self.assertIsInstance(self.window.tabs, QStackedWidget)
        self.assertEqual(self.window.tabs.count(), 4)
        bar = self.window.tabs.tabBar()
        bar.setFocus()
        with patch('lifetime_server_devices.reduced_motion', return_value=False):
            QTest.keyClick(bar, Qt.Key.Key_Right)
            self.assertEqual(self.window.tabs.currentIndex(), 1)
            self.assertEqual(bar.animation.state(), bar.animation.State.Running)
            QTest.qWait(50)
            intermediate = bar.indicator.geometry()
            self.window.tabs.setCurrentIndex(2)
            self.assertEqual(bar.animation.startValue(), intermediate)
            QTest.qWait(300)
            self.assertEqual(bar.indicator.geometry(), bar.tabRect(2).adjusted(5, 5, -5, -5))
        with patch.dict('os.environ', {'LIFETIME_REDUCE_MOTION': '1'}):
            self.window.tabs.setCurrentIndex(3)
            self.assertEqual(bar.animation.state(), bar.animation.State.Stopped)
            self.assertEqual(bar.indicator.geometry(), bar.tabRect(3).adjusted(5, 5, -5, -5))
        self.window.activateWindow()
        bar.setFocus()
        self.app.processEvents()
        QTest.keyClick(bar, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.window.tabs.currentIndex(), 1)

    def test_power_consent_confirmation_named_target_and_worker_routes(self):
        row = device(1)
        row['last_received_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        self.load([row])
        self.select(row['device_id'])
        self.assertFalse(self.window.power_buttons['restart'].isEnabled())
        self.assertIn('owner has not allowed', self.window.control_reason.text())
        self.assertTrue(self.window.power_buttons['refresh'].isEnabled())
        self.api.rows[0]['policy']['control'] = True
        self.window.refresh()
        self.wait_idle()
        for action in ('sleep', 'restart', 'shutdown', 'refresh'):
            before = len(self.arguments('send_command'))
            with patch.object(self.window, '_confirm', return_value=False) as confirm:
                self.window.power_buttons[action].click()
            self.assertIn(row['identity']['display_name'], confirm.call_args.args[0])
            self.assertEqual(len(self.arguments('send_command')), before)
            with patch.object(self.window, '_confirm', return_value=True):
                self.window.power_buttons[action].click()
            self.wait_idle()
            self.assertEqual(self.arguments('send_command')[-1], dict(device_id=row['device_id'], action=action))
        self.api.rows[0]['last_received_at'] = '2020-01-01T00:00:00Z'
        self.api.rows[0]['specs'] = {'mac_addresses': ['00:11:22:33:44:55']}
        self.window.refresh()
        self.wait_idle()
        self.assertTrue(self.window.power_buttons['wake'].isEnabled())
        self.assertFalse(self.window.power_buttons['restart'].isEnabled())
        with patch.object(self.window, '_confirm', return_value=True):
            self.window.power_buttons['wake'].click()
        self.wait_idle()
        self.assertEqual(self.arguments('wake')[-1], dict(device_id=row['device_id']))

    def test_detail_specs_history_gaps_pending_command_and_menu(self):
        row = device(1)
        row['policy'].update(resources=True, control=True)
        row['last_received_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        row['specs'] = dict(cpu_name='Example CPU', ram_bytes=16 * 1024**3)
        row['performance_history'] = [dict(observed_at='2026-11-08T00:00:00Z', cpu_percent=0),
                                      dict(observed_at='2026-11-08T00:05:00Z', cpu_percent=None)]
        row['commands'] = [dict(status='pending')]
        self.load([row])
        self.select(row['device_id'])
        self.assertEqual(self.window.spec_values['CPU'].text(), 'Example CPU')
        self.assertEqual(self.window.performance_range.currentData(), 'live')
        self.window.performance_range.setCurrentIndex(1)
        self.assertEqual(self.window.performance_plots['cpu_percent'].values, [0, None])
        self.assertEqual(self.window.performance_plots['cpu_percent'].bar_color, '#FFC93C')
        self.assertFalse(self.window.power_buttons['refresh'].isEnabled())
        self.assertIn('pending', self.window.control_reason.text())
        self.window.tabs.setCurrentIndex(1)
        self.window.table.cellWidget(0, 0).menu_button.click()
        self.wait_idle()
        self.assertTrue(self.window.device_menu.isVisible())
        self.assertFalse(self.window.device_menu.actions()[0].isEnabled())
        self.window.device_menu.close()

    def test_live_updates_200_cards_in_place_and_preserves_selection(self):
        self.window.live_timer.stop()
        self.window.page_size.setCurrentIndex(3)
        rows = [device(i) for i in range(200)]
        for row in rows:
            row['policy']['resources'] = True
        self.load(rows)
        self.window.tabs.setCurrentIndex(1)
        self.select(rows[1]['device_id'])
        self.window._readings_timer.stop()
        boxes = [self.window.table.cellWidget(i, 0) for i in range(200)]
        selection = self.window.selected_device_ids()
        edit = self.window.identity_fields['display_name']
        edit.setFocus()
        focus = self.app.focusWidget()
        scroll = self.window.table.verticalScrollBar().value()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        sample = dict.fromkeys(__import__('lifetime_live').FIELDS)
        sample.update(cpu_percent=73, ram_percent=52, observed_at=now)
        self.window._live_items = {r['device_id']: (dict(sample=sample, age_seconds=0), time.monotonic()) for r in rows}
        self.window._live_focus = rows[1]['device_id']
        self.window._live_ring = [sample]
        self.window._paint_live()
        self.assertEqual(self.window.selected_device_ids(), selection)
        self.assertIs(self.app.focusWidget(), focus)
        self.assertEqual(self.window.table.verticalScrollBar().value(), scroll)
        self.assertEqual([self.window.table.cellWidget(i, 0) for i in range(200)], boxes)
        self.assertIn(73, self.window.performance_plots['cpu_percent'].values)
        self.assertIn('Live', self.window.live_marker.text())
        self.assertFalse(boxes[0].user.isVisible())
        for item, _ in self.window._live_items.values():
            item['age_seconds'] = 21
        self.window._paint_live()
        self.assertIn('Last seen', self.window.live_marker.text())

    def test_live_poll_never_overlaps_and_pauses_when_hidden(self):
        self.window.live_timer.stop()
        gate = threading.Event()
        self.api.block['live'] = gate
        self.window._load_live()
        self.wait(lambda: any(op == 'live' for op, *_ in self.api.calls))
        before = len([op for op, *_ in self.api.calls if op == 'live'])
        self.window._load_live()
        self.assertEqual(len([op for op, *_ in self.api.calls if op == 'live']), before)
        gate.set()
        self.wait_idle()
        self.window.hide()
        self.window._load_live()
        self.wait_idle()
        self.assertEqual(len([op for op, *_ in self.api.calls if op == 'live']), before)

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



class AddComputerSheetTests(unittest.TestCase):
    def setUp(self):
        from test_company_gui import FakeTasks
        from lifetime_server_widgets import AddDeviceSheet
        from lifetime_connection import issue
        from lifetime_i18n import tr, set_language
        self.app = qt_app()
        set_language('en')
        self.client = Mock()
        self.enterContext(patch('lifetime_tasks.Tasks', FakeTasks))
        self.sheet = AddDeviceSheet(dict(code=issue('0'*64), expires_at='2026-11-08T12:00:00Z', uses=1), 'https://server:8443', tr, client=self.client)
        self.sheet.timer.stop()
        self.addCleanup(self.sheet.close)
        self.sheet.show()
        self.app.processEvents()

    def test_approve_in_sheet_and_network_line_is_quiet(self):
        self.assertFalse(self.sheet.line_panel.isVisible())
        self.sheet.other_network.click()
        self.assertTrue(self.sheet.line_panel.isVisible())
        self.sheet.poll()
        self.sheet.tasks.resolve([dict(request_id='r1', name='Office PC')])
        self.assertEqual(self.sheet.message.text(), 'Office PC wants to connect')
        self.assertTrue(self.sheet.approve.isVisible())
        self.sheet.approve.click()
        self.sheet.tasks.jobs[-1]()
        self.client.call.assert_called_with('approve', request_id='r1')
        self.sheet.tasks.resolve(dict(status='approved'))
        self.assertEqual(self.sheet.message.text(), 'Office PC connected')
        self.assertFalse(self.sheet.decisions.isVisible())
        self.assertFalse(self.sheet.timer.isActive())

    def test_admin_failure_is_visible(self):
        self.sheet.poll()
        self.sheet.tasks.resolve(error='OSError')
        self.assertTrue(self.sheet.error.isVisible())
        self.assertEqual(self.sheet.error.text(), "Can't reach the Lifetime server. Make sure it is running.")


if __name__ == '__main__':
    unittest.main()
