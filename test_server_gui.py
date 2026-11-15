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

import lifetime_insights as insights
import lifetime_server_gui as gui
from lifetime_widgets_dashboard import VISUAL_ROLE
import lifetime_server_access_ui as gui_access
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
        self.incidents = []
        self.alerts = None
        self.iana = True
        self.checks = []
        self.updates = None         # None: a server without agent updates
        self.whoami = None          # None: an older server without roles (whoami is unknown)
        self.roles, self.audit = [], []
        self.insight_items, self.insight_config = [], insights.settings()     # computed by the real lifetime_insights
        self.sleep_policies = {}
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
                    arguments.get('search', '').casefold() in (row['device_id'] + str(row['identity'])).casefold()   # as the store
                    and (not arguments.get('group') or row['identity']['department'] == arguments['group'])
                    and (not arguments.get('status') or row['status'] == arguments['status'])]
            offset, limit = arguments.get('offset', 0), arguments.get('limit', 25)
            return rows[offset:offset + limit]
        if operation == 'live':
            return dict(devices=[], ring=[], device_id=arguments.get('device_id'))
        if operation == 'server_info':
            return dict(address='https://localhost:8443', host='127.0.0.1', port=8443,
                        name='Demo-Server', fingerprint='0' * 64, discovery=False)
        if operation in ('invitations', 'server_log'):
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
                        health='available', schema_version=4, server_time='2026-11-07T12:00:00+00:00',
                        **({'alerts': self.alerts} if self.alerts else {}))
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
        if operation in ('backup', 'restore', 'refresh', 'send_command', 'wake', 'names'):
            return dict(arguments)
        if operation == 'whoami' and self.whoami:
            return copy.deepcopy(self.whoami)
        if operation == 'roles':
            return copy.deepcopy(self.roles)
        if operation == 'set_role':
            self.roles.append(dict(sid='S-1-5-21-1-2-3-1500', name=arguments['principal'], kind='user',
                                   role=arguments['role'], granted_by='LAB\\boss', granted_at='2026-11-15T00:00:00+00:00'))
            return copy.deepcopy(self.roles)
        if operation == 'audit':
            return [row for row in copy.deepcopy(self.audit) if 'before' not in arguments or row['seq'] < arguments['before']][:arguments['limit']]
        if operation == 'verify_audit':
            return dict(ok=False, checked=2, first_broken=3, reason='edited', archived_through=0)
        if operation == 'backup_archive':
            return dict(path=arguments['path'], sha256='0' * 64, files=[])
        if operation == 'verify_backup':
            return dict(ok=False, problem='checksum')
        if operation == 'stage_restore':
            return dict(ok=True, staged=True, restart_required=True)
        if operation == 'code':
            return dict(code='secret-pairing-code', expires_at='2026-11-07T12:10:00Z', uses=arguments['uses'])
        if operation == 'incidents':
            wanted = arguments.get('state', '')
            return copy.deepcopy([item for item in self.incidents if not wanted or item['state'] == wanted
                                  or wanted == 'active' and item['state'] in ('open', 'acknowledged', 'suppressed')])
        if operation == 'incident':
            item = next(item for item in self.incidents if item['id'] == arguments['incident_id'])
            return dict(copy.deepcopy(item), timeline=[dict(time=item['started_at'], kind='pending', detail=''),
                                                       dict(time=item['started_at'], kind='opened', detail='')],
                        deliveries=[dict(kind='open', channel='email', status='failed', attempts=5, error='auth',
                                         updated_at=item['started_at'])])
        if operation == 'acknowledge':
            item = next(item for item in self.incidents if item['id'] == arguments['incident_id'])
            item.update(state='acknowledged', acknowledged_by=arguments['by'], acknowledged_at=item['started_at'])
            return copy.deepcopy(item)
        if operation == 'alert_rules':
            import lifetime_monitor
            return [dict(rule=name, enabled=rule['enabled'], threshold=rule['threshold'], minutes=rule['minutes'],
                         **({'version': rule['version']} if 'version' in rule else {}),
                         **({'range': list(rule['range'])} if 'range' in rule else {}),
                         minutes_range=list(rule.get('minutes_range', (0, 10080))))
                    for name, rule in lifetime_monitor.RULES.items()]
        if operation == 'schedules':
            return [dict(scope='default', key='', kind='auto', timezone='local', start='08:00', end='18:00',
                         weekdays=[0, 1, 2, 3, 4], holidays=[])]
        if operation in ('maintenance', 'deliveries'):
            return []
        if operation == 'notifications':
            import lifetime_notify
            return dict(lifetime_notify.DEFAULTS, password_set=True, webhook='', secrets_unreadable=False)
        if operation == 'time_zones':
            return dict(iana=self.iana, message='')
        if operation == 'coverage_floor':
            return 80
        if operation == 'checks':
            return copy.deepcopy(self.checks)
        if operation == 'presence':
            return copy.deepcopy(getattr(self, 'presence', []))
        if operation == 'check_scopes':
            return ['private']
        if operation == 'set_check_scopes':
            return arguments['scopes']
        if operation == 'save_check':
            import lifetime_checks
            check = dict(lifetime_checks.validate(arguments['check']), id=len(self.checks) + 1, failures=0, last_at=None,
                         last_category=None, last_latency_ms=None, last_detail='', down_since=None)
            self.checks.append(check)
            return copy.deepcopy(self.checks)
        if operation == 'delete_check':
            self.checks = [item for item in self.checks if item['id'] != arguments['check_id']]
            return copy.deepcopy(self.checks)
        if operation in ('updates', 'upload_update', 'set_update', 'rollback_update') and self.updates is not None:
            if operation == 'set_update':
                self.updates.update(arguments)
            return copy.deepcopy(self.updates)
        if operation == 'insights':
            return dict(insights.fleet(copy.deepcopy(self.insight_items), self.insight_config, dt.date.today()),
                        sleep_policies=copy.deepcopy(self.sleep_policies))
        if operation == 'set_sleep_policy':
            self.sleep_policies[arguments['group'].casefold()] = dict(group=arguments['group'], name='LAB\admin',
                                                                      since='2026-11-17T11:00:00+00:00')
            if not arguments['enabled']:
                self.sleep_policies.pop(arguments['group'].casefold())
            return {}
        if operation == 'set_insight_settings':
            self.insight_config = insights.settings(dict(self.insight_config, **arguments['values']))
            return copy.deepcopy(self.insight_config)
        if operation == 'set_coverage_floor':
            return dict(coverage_floor=arguments['percent'])
        if operation == 'test_notification':
            return dict(channel=arguments['channel'], status='failed', error='tls')
        if operation in ('set_alert_rule', 'set_schedule', 'delete_schedule', 'add_maintenance', 'delete_maintenance',
                         'set_notifications'):
            return dict(arguments)
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

    def test_shared_hardware_panel_follows_resources(self):
        row = device(0)
        row['policy']['resources'] = True
        row['specs'] = dict(hardware=dict(cpu={'temperature_c': 53}, fans=[]))
        self.window._render_specs(row)
        self.assertFalse(self.window.hardware_detail.isHidden())
        texts = [value.text() for _, value in self.window.hardware_detail.fields]
        self.assertIn('53', texts[0])
        self.assertTrue(any('firmware' in value.accessibleDescription() for _, value in self.window.hardware_detail.fields))
        row['policy']['resources'] = False
        self.window._render_specs(row)
        self.assertTrue(self.window.hardware_detail.isHidden())
        self.assertNotIn('53', self.window.hardware_detail.fields[0][1].text())

    def test_hardware_sections_remain_readable_and_keep_disclosure_state(self):
        from PyQt6.QtWidgets import QToolButton, QFormLayout
        from lifetime_i18n import set_language
        from lifetime_system_gui import HardwarePanel
        panel = HardwarePanel(remote=True)
        self.addCleanup(panel.deleteLater)
        panel.set_data(dict(adapters=[dict(name='Office Ethernet', model='Gigabit adapter', kind='Ethernet',
                                          connected=True, speed_mbps=1000, driver='1.2')]))
        panel.resize(390, 900)
        panel.show()
        self.addCleanup(panel.close)
        self.app.processEvents()
        self.assertTrue(all(form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows for form in panel.forms))
        self.assertLessEqual(panel.minimumSizeHint().width(), 390)
        headings = panel.findChildren(QToolButton, 'hardwareSectionTitle')
        self.assertEqual(len(headings), 7)
        network = next(button for button in headings if button.text() == 'Network cards')
        network.click()
        first = panel.fields[0][1]
        panel.set_data(panel.data)
        self.assertIs(panel.fields[0][1], first)
        set_language('vi')
        self.addCleanup(set_language, 'en')
        self.app.processEvents()
        headings = panel.findChildren(QToolButton, 'hardwareSectionTitle')
        current = [button for button in headings if not button.parentWidget().isHidden()]
        network = next(button for button in current if button.text() == 'Card mạng')
        self.assertFalse(network.isChecked())
        self.assertTrue(any(value.text() == '1000 Mbps' for _, value in panel.fields))
        network.setFocus()
        QTest.keyClick(network, Qt.Key.Key_Space)
        self.assertTrue(network.isChecked())

    def test_remote_pages_use_resources_and_history_consent(self):
        row = device(0)
        row['policy'] = dict(history=True, resources=True)
        row['history'] = dict(daily={dt.date.today().isoformat(): 3600}, hourly=[0]*24, boots=2)
        row['readings'] = dict(health=dict(detail=dict(stability=[], battery={'packs': []}, sleep={'daily': {}, 'nights': []})))
        with patch.object(gui.HealthPage, 'refresh', side_effect=AssertionError('local probe')):
            self.window._render_remote(row)
        self.assertFalse(self.window.remote_activity.isHidden())
        self.assertFalse(self.window.remote_health.isHidden())
        row['policy'] = dict(history=False, resources=False)
        self.window._render_remote(row)
        self.assertTrue(self.window.remote_activity.isHidden())
        self.assertTrue(self.window.remote_health.isHidden())
        self.assertEqual(self.window.health_state.text(), self.window.t('System readings are off on this computer'))

    def cleanup_window(self):
        for event in self.api.block.values():
            event.set()
        self.window.close()
        self.wait(lambda: not self.window._jobs)
        for label in self.window.findChildren(gui.RollLabel):
            label._rolling = None  # cancel delayed entrances before releasing the test window
        self.window.deleteLater()
        self.app.sendPostedEvents(None, gui.QEvent.Type.DeferredDelete)

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
        self.assertEqual(len(self.window.events.events), 5)       # an event rail: dot, words, then time and computer
        self.assertEqual(self.window.events.accessibleName(), 'Recent events')
        self.assertEqual(self.window.events.height(), 5 * self.window.events.ROW)
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

    def test_device_daily_chart_and_empty_states_follow_policy(self):
        row = device(1)
        self.load([row])
        self.select(row['device_id'])
        self.assertEqual(self.window.device_daily.message, 'History is off on this computer')
        self.assertEqual(self.window.performance_plots['disk_free_percent'].message, 'System readings are off on this computer')
        today = dt.date.today()
        self.api.rows[0]['policy']['history'] = True
        self.api.rows[0]['history'] = {'daily': {today.isoformat(): 7200, (today - dt.timedelta(days=3)).isoformat(): 36000,
                                                 (today - dt.timedelta(days=40)).isoformat(): 3600}}
        self.window.refresh()
        self.wait_idle()
        chart = self.window.device_daily
        self.assertEqual(chart.message, '')
        self.assertEqual(len(chart.values), 14)
        self.assertEqual(chart.values[-1], 2.0)
        self.assertEqual(chart.values[-4], 10.0)
        self.assertIn('10.0', self.window.daily_summary.text())
        self.window.daily_range.setCurrentIndex(1)
        self.assertEqual(len(chart.values), 30)
        self.assertEqual(sum(v is not None for v in chart.values), 2)      # the 40-day-old entry stays out

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
        with patch.object(gui.QFileDialog, 'getSaveFileName', return_value=('backup', '')):
            self.window.backup_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('backup_archive'), [{'path': 'backup.zip'}])
        self.assertIn('.sha256', self.window.status_label.text())
        with patch.object(gui.QFileDialog, 'getOpenFileName', return_value=('backup.zip', '')):
            self.window.verify_backup_button.click()
        self.wait_idle()
        self.assertEqual(self.window.status_label.text(), 'The archive does not match its .sha256 file.')
        with patch.object(gui.QFileDialog, 'getOpenFileName', return_value=('backup.zip', '')), \
                patch.object(self.window, '_confirm', return_value=True) as confirm:
            self.window.restore_button.click()
        self.wait_idle()
        self.assertIn('need a new code and approval', confirm.call_args.args[0])
        self.assertEqual(self.arguments('stage_restore'), [{'path': 'backup.zip'}])
        self.assertIn('next time the server starts', self.window.status_label.text())
        with patch.object(gui.QFileDialog, 'getOpenFileName', return_value=('', '')):
            self.window.restore_button.click()
        self.assertEqual(len(self.arguments('stage_restore')), 1)

    def signed_in(self, role, channel='pipe'):
        import lifetime_access as access
        self.api.whoami = dict(name='LAB\\' + role, sid='S-1-5-21-1-2-3-1100', channel=channel, roles=[role], role=role,
                               builtin=False, operations=sorted(access.PERMISSIONS[role]))
        self.window.access_ui.load()
        self.wait_idle()

    def test_viewer_sees_everything_but_can_change_nothing_and_is_told_why(self):
        self.load([device(0)])
        self.signed_in('viewer')
        w = self.window
        self.assertIn('LAB\\viewer', w.access_ui.signed_in.text())
        self.assertIn('Viewer', w.access_ui.signed_in.text())
        for widget in (w.backup_button, w.restore_button, w.retain_button, w.interval_button, w.names_button,
                       w.invitation_button, w.admin_revoke, w.alerts.rules_button, w.alerts.window_add,
                       w.alerts.notifications_button, w.access_ui.role_add):
            self.assertFalse(widget.isEnabled(), widget.text())
        self.assertIn('Viewer', w.access_ui.reason())
        self.assertTrue(w.access_ui.audit_panel.isHidden())
        self.assertTrue(w.access_ui.role_table.isHidden())
        row = device(0)
        row['policy']['control'] = True
        self.assertEqual(w._power_reason(row, 'restart'), 'Your role (Viewer) cannot do this.')
        self.assertEqual(w._power_reason(row, 'refresh'), 'Your role (Viewer) cannot do this.')
        w._detail = row
        w._set_device_actions(True)
        self.assertFalse(w.delete_button.isEnabled())
        self.assertFalse(w.save_identity_button.isEnabled())
        self.assertEqual(w.delete_button.toolTip(), 'Your role (Viewer) cannot do this.')

    def test_operator_may_act_on_incidents_and_power_but_not_settings(self):
        self.signed_in('operator')
        w = self.window
        self.assertTrue(w.alerts.window_add.isEnabled())               # maintenance windows suppress alerts
        self.assertFalse(w.alerts.rules_button.isEnabled())
        self.assertFalse(w.backup_button.isEnabled())
        row = device(0)
        row['policy']['control'] = True
        self.assertNotIn('role', w._power_reason(row, 'restart'))
        self.signed_in('admin', channel='token')
        self.assertIn('own Windows account', w._power_reason(row, 'restart'))
        self.assertNotIn('own Windows account', w._power_reason(row, 'refresh'))     # refresh is not power

    def test_power_confirmation_names_the_exact_computer(self):
        row = device(3)
        row['policy']['control'] = True
        with patch.object(self.window, '_power_reason', return_value=''), \
                patch.object(self.window, '_confirm', return_value=False) as confirm:
            self.window._device_command(row, 'shutdown')
        message = confirm.call_args.args[0]
        for part in ('Shut down', 'Computer 003', 'Support', row['device_id']):
            self.assertIn(part, message)
        self.assertEqual(self.arguments('send_command'), [])

    def test_auditor_reads_verifies_and_exports_the_audit_trail_and_admin_assigns_roles(self):
        self.api.audit = [dict(seq=n, at='2026-11-15T00:00:00+00:00', actor_sid=None, actor_name='=admin', role='admin',
                               action='set_configuration', target='', result='ok', correlation_id=str(n),
                               prev_hash='0' * 64, hash='1' * 64) for n in range(1500, 0, -1)]
        self.signed_in('auditor')
        w = self.window
        self.assertFalse(w.access_ui.audit_panel.isHidden())
        self.assertEqual(w.access_ui.audit_table.rowCount(), 100)
        w.access_ui.verify_button.click()
        self.wait_idle()
        self.assertEqual(w.access_ui.audit_result.text(), 'The audit trail is broken at entry 3: an entry was changed.')
        target = self.root / 'audit'
        with patch.object(gui_access.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            w.access_ui.export_button.click()
        self.wait_idle()
        lines = (self.root / 'audit.csv').read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(len(lines), 1501)
        self.assertIn("'=admin", lines[1])
        self.assertFalse(w.access_ui.role_add.isEnabled())
        self.signed_in('admin')
        w.access_ui.principal.setText('LAB\\helpdesk')
        w.access_ui.role_choice.setCurrentIndex(w.access_ui.role_choice.findData('operator'))
        w.access_ui.role_add.click()
        self.wait_idle()
        self.assertEqual(self.arguments('set_role'), [dict(principal='LAB\\helpdesk', role='operator')])
        self.assertEqual(w.access_ui.role_table.item(0, 2).text(), 'Operator')

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
        self.window.language_button.click()
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
        self.window.language_button.click()
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
            # The status is drawn as a chip (glyph + words) from the item's visual role, not a dot icon.
            self.assertEqual(self.window.table.item(row, 2).data(VISUAL_ROLE)[0], 'chip')
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
        row['last_problem'] = dict(kind='timeout', at='2020-01-02T12:00:00+00:00')
        row['policy']['history'] = True
        row['history'] = {'recording_started': '2026-01-01T00:00:00Z', 'daily': {'2026-11-08': 60}}
        self.load([row])
        self.select(row['device_id'])
        self.assertEqual(self.window.detail_name.text(), row['identity']['display_name'])
        self.assertEqual(self.window.detail_total.text(), '3.0 h')
        for key in ('Asset tag', 'Department', 'Location', 'Deployment date', 'Last seen', 'Client version', 'Last problem', 'Coverage', 'Tracking since'):
            self.assertIn(key + '\n', self.window.detail_text.toPlainText())   # labels carry no colon, as on Overview
        self.assertIn('No answer (timeout) · 2 Jan 2020', self.window.detail_text.toPlainText())
        self.assertIn('1.0.1', self.window.detail_text.toPlainText())
        self.assertIn('2 Jan 2026', self.window.detail_text.toPlainText())
        self.assertNotRegex(self.window.detail_text.toPlainText(), r'\d{4}-\d{2}-\d{2}')
        self.window.copy_device_id.click()
        self.assertEqual(self.app.clipboard().text(), row['device_id'])
        self.window.language_button.click()
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
        self.window.language_button.click()
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
        self.assertEqual(invitation_table.item(0, 4).data(VISUAL_ROLE), ('chip', 'good'))     # a chip, not a dot icon
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
        self.window.language_button.click()
        self.window._render_server_info(result)
        self.assertIn(gui.text('month.1', 'vi'), invitation_table.item(0, 1).text())
        self.assertNotRegex(invitation_table.item(0, 1).text(), r'\d{4}-\d{2}-\d{2}')

    def test_certificate_expiry_names_and_renewal_warning(self):
        self.load([])
        result = copy.deepcopy(self.window._admin_result)
        result['info'].update(certificate=dict(expires='2027-12-08T10:00:00+00:00', names=['127.0.0.1', 'lifetime.school.local'],
                                               key='a' * 64, warning=None),
                              key_fingerprint='a' * 64, server_names=['lifetime.school.local'])
        self.window._render_server_info(result)
        self.assertIn('8 Dec 2027', self.window.certificate_expiry.text())
        self.assertIn('lifetime.school.local', self.window.host_values['Certificate expires'].text())
        self.assertEqual(self.window.names_edit.text(), 'lifetime.school.local')
        self.assertTrue(self.window.certificate_warning.isHidden())
        result['info']['certificate']['warning'] = 'renewal-failed'
        self.window._render_server_info(result)
        self.assertFalse(self.window.certificate_warning.isHidden())
        self.assertIn('8 Dec 2027', self.window.certificate_warning.text())
        self.window.language_button.click()
        self.assertIn('không thể gia hạn', self.window.certificate_warning.text())
        result['log'] = [dict(time='2026-11-15T01:00:00+00:00', kind='report-rejected', category='invalid',
                              device_id='d1', name='Lab PC', count=3),
                         dict(time='2026-11-15T00:00:00+00:00', kind='server-started', category=None, device_id=None, name=None, count=1)]
        self.window._render_server_info(result)
        self.assertFalse(self.window.server_log_scroll.isHidden())
        self.assertTrue(self.window.server_log_empty.isHidden())
        (kind, words, when, device), started = self.window.server_log.events
        self.assertEqual((kind, words, device), ('warning', 'Từ chối báo cáo: dữ liệu không hợp lệ (3 lần)', 'Lab PC'))   # a refused request is amber
        self.assertEqual(started[:2], ('good', 'Máy chủ đã khởi động'))
        self.window.language_button.click()                        # the rail follows the language without a reload
        self.assertEqual(self.window.server_log.events[1][1], 'Server started')
        self.window.language_button.click()
        self.window.names_edit.setText('a.local, 192.0.2.5; b.local')
        self.window.names_button.click()
        self.wait_idle()
        self.assertEqual(self.arguments('names'), [{'names': ['a.local', '192.0.2.5', 'b.local']}])

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

    def test_all_chip_also_clears_the_pending_filter_that_review_sets(self):
        # Review (on the overview) narrows the list through the status box. Choosing "All" afterwards used to
        # leave that box on "Pending approval", so the page listed nothing while four computers existed.
        self.load([device(i) for i in range(4)])
        self.window.review_pending()
        self.wait_idle()
        self.assertEqual(self.window.table.rowCount(), 0)
        self.window.device_chips['all'].click()
        self.wait_idle()
        self.assertEqual(self.window.status_filter.currentData(), '')
        self.assertEqual(self.window.table.rowCount(), 4)

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
        self.wait(lambda: box.meters['CPU'][1].value() == 42 and box.meters['RAM'][1].value() == 50)   # the meters fill in once
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

    def test_pages_slide_in_from_the_side_the_menu_pill_moved_towards(self):
        pages = self.window.tabs
        pages.setCurrentIndex(0)
        QTest.qWait(400)
        with patch('lifetime_server_devices.reduced_motion', return_value=False):
            pages.setCurrentIndex(2)
            self.assertGreater(pages.entrance.offset.x(), 0)       # the page's picture arrives from the right
            self.wait(lambda: pages.entrance is None)
            self.assertEqual(pages.widget(2).pos(), pages.contentsRect().topLeft())
            pages.setCurrentIndex(1)
            self.assertLess(pages.entrance.offset.x(), 0)          # and from the left
            self.wait(lambda: pages.entrance is None)
        self.assertIsNone(pages.widget(1).graphicsEffect())
        self.assertEqual(pages.widget(1).pos(), pages.contentsRect().topLeft())

    def test_a_tab_switch_paints_the_page_twice_at_most_and_asks_the_server_nothing_on_this_thread(self):
        """However much a page holds, a switch draws it once for its entrance picture and once when the live page
        takes over, never once per animation frame (a whole-page opacity effect did). Its data comes from the
        background tasks, never from an admin call made on the window's own thread."""
        from PyQt6.QtCore import QEvent, QObject
        from PyQt6.QtWidgets import QWidget
        pages = self.window.tabs
        calls, real = [], self.window.client.call
        def spy(name, *args, **kwargs):
            if threading.current_thread() is threading.main_thread():
                calls.append(name)
            return real(name, *args, **kwargs)
        self.window.client.call = spy
        self.addCleanup(setattr, self.window.client, 'call', real)
        class Paints(QObject):
            count = 0
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.Paint:
                    Paints.count += 1
                return False
        counter = Paints()
        with patch('lifetime_server_devices.reduced_motion', return_value=False):
            for index in list(range(1, pages.count())) + [0]:
                page = pages.widget(index)
                widgets = [page] + page.findChildren(QWidget)
                for widget in widgets:
                    widget.installEventFilter(counter)
                Paints.count = 0
                pages.setCurrentIndex(index)
                self.assertIsNotNone(pages.entrance)
                self.assertEqual(page.graphicsEffect().opacity(), 0.0)     # hidden behind its picture, never rendered
                self.wait(lambda: pages.entrance is None)
                for widget in widgets:
                    widget.removeEventFilter(counter)
                self.assertLessEqual(Paints.count, 2 * len(widgets) + 10, pages.tabText(index))
                self.assertIsNone(page.graphicsEffect())
        self.assertEqual(calls, [])

    def test_menu_pill_stretches_leading_edge_first_and_settles_exactly(self):
        from PyQt6.QtCore import QRectF
        bar = self.window.tabs.tabBar()
        self.window.tabs.setCurrentIndex(0)
        QTest.qWait(700)
        with patch('lifetime_server_devices.reduced_motion', return_value=False):
            self.window.tabs.setCurrentIndex(2)
            QTest.qWait(90)
            drawn, leading = bar.pill_rect(), QRectF(bar.indicator.geometry())
            self.assertEqual(drawn.right(), leading.right())    # moving right: the right edge leads
            self.assertLess(drawn.left(), leading.left())       # the left edge is still catching up
            QTest.qWait(700)
        self.assertEqual(bar.pill_rect().toRect(), bar.tabRect(2).adjusted(5, 5, -5, -5))
        with patch.dict('os.environ', {'LIFETIME_REDUCE_MOTION': '1'}):
            self.window.tabs.setCurrentIndex(0)
            self.assertEqual(bar.pill_rect().toRect(), bar.tabRect(0).adjusted(5, 5, -5, -5))

    def test_energy_page_says_measured_or_estimated_sums_groups_and_saves_the_price(self):
        today = dt.date.today().isoformat()
        self.api.insight_items = [
            dict(device_id='a', name='Lab PC', group='Lab', kind='laptop', discharge=[10, 10, 10], history=dict(daily={today: 36000})),
            dict(device_id='b', name='Office PC', group='Office', kind='desktop', history=dict(daily={today: 36000})),
            dict(device_id='c', name='Private PC', group='Office', kind=None, history=None)]
        self.load([device(0)])
        ui = self.window.insights
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        card, cost = ui.cards['today']
        self.assertEqual((card.headline.text(), cost.text()), ('0.90 kWh', '2,250 VND'))     # 10 h x (10 + 80) W x 2500
        self.assertIn('1 of 2 computers measured on battery', ui.method.text())
        self.assertIn('1 do not share their awake history', ui.method.text())
        self.assertEqual([ui.top.item(row, 0).text() for row in range(ui.top.rowCount())], ['Office PC', 'Lab PC'])
        self.assertEqual(ui.top.item(0, 2).text(), '80 W · estimated: typical desktop')
        self.assertEqual(ui.top.item(1, 2).text(), '10 W · measured on battery')
        ui.scope.setCurrentIndex(ui.scope.findData('Lab'))
        self.assertEqual(card.headline.text(), '0.10 kWh')
        ui.price.setValue(3000)
        ui.currency.setText('usd')
        ui._save()
        self.wait_idle()
        self.assertIn(('set_insight_settings', dict(values=dict(price=3000.0, currency='USD', laptop_watts=20.0, desktop_watts=80.0))),
                      [(o, a) for o, a, _ in self.api.calls])
        self.assertEqual(cost.text(), '300.00 USD')
        self.window.language_button.click()
        self.assertIn('máy được đo khi chạy pin', ui.method.text())
        self.assertEqual(self.window.tabs.tabText(ui.index), 'Năng lượng')

    def test_after_hours_lists_computers_groups_saving_and_toggles_the_sleep_policy(self):
        today = dt.date.today()
        night = [0.0] * 24
        night[20] = night[21] = 3600.0                      # 2 h after 18:00
        self.api.insight_items = [dict(device_id='a', name='Lab PC', group='Lab', room='A101', kind='desktop', control=True,
                                       history=dict(daily={}, dated_hourly={today.isoformat(): night}))]
        self.load([device(0)])
        ui = self.window.insights
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        self.assertIn('2.0 h awake outside hours on 1 computers', ui.after_summary.text())
        self.assertEqual([ui.after_top.item(0, c).text() for c in (0, 2, 3, 5)],
                         ['Lab PC', '2.0 h', '0.16 kWh', 'office hours 08:00–18:00'])      # 2 h x 80 W
        self.assertEqual(ui.after_groups.item(0, 4).text(), '1,714 VND (est., 1 allow remote power)')   # 0.16 x 30/7 x 2500
        self.assertEqual(ui.after_rooms.item(0, 0).text(), 'A101')
        self.assertEqual(ui.policy_toggle.text(), 'Turn on for this group')
        ui._toggle_policy()
        self.wait_idle()
        self.assertIn(('set_sleep_policy', dict(group='Lab', enabled=True)), [(o, a) for o, a, _ in self.api.calls])
        self.assertEqual(ui.policy_toggle.text(), 'Turn off for this group')
        self.assertEqual(ui.after_groups.item(0, 5).text(), 'On')
        self.assertIn('Lab: on since', ui.policies.text())
        ui.opens.setTime(ui.opens.time().addSecs(3600))
        ui._save_hours()
        self.wait_idle()
        self.assertEqual(self.api.insight_config['hours']['start'], '09:00')

    def test_replacement_plan_lists_due_parts_budget_and_says_not_enough_data(self):
        today = dt.date.today()
        filling = [((today - dt.timedelta(days=d)).isoformat(), None, 10 + d * 0.1) for d in (90, 60, 30, 0)]   # 19 -> 10 %
        self.api.insight_items = [dict(device_id='a', name='Lab PC', group='Lab', kind='desktop', readings=dict(specs={}),
                                       trends=filling),
                                  dict(device_id='b', name='New PC', group='Lab', kind='laptop', readings=dict(specs={}),
                                       trends=filling[-2:])]
        self.api.insight_config = insights.settings(dict(unit_prices=dict(disk=1_200_000)))
        self.load([device(0)])
        ui = self.window.insights
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        table = ui.plan_tables['quarter']
        self.assertEqual([table.item(0, c).text() for c in range(4)],
                         ['Lab PC', 'Lab', 'System disk space', 'now (10 %, limit 10 %)'])
        self.assertEqual(ui.budgets['quarter'].text(), '1 parts · budget 1,200,000 VND for 1 priced, 0 without a price')
        self.assertEqual(ui.forecasts.item(1, 1).text(), 'not enough data (0 readings over 0 days)')
        self.assertEqual(ui.forecasts.item(1, 2).text(), 'not enough data (2 readings over 30 days)')
        self.assertEqual(ui.forecasts.item(0, 1).text(), 'No battery in this computer')

    def test_health_score_section_averages_known_scores_and_names_unknown_parts(self):
        self.api.insight_items = [dict(device_id='a', name='Good PC', group='Lab', kind='desktop', client_version='1.2.0',
                                       health=dict(crashes_30d=0, shutdowns_30d=0)),
                                  dict(device_id='b', name='Silent PC', group='Lab', kind='laptop')]
        self.load([device(0)])
        ui = self.window.insights
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        self.assertEqual(ui.ring.score, 100)          # only the known score; the silent computer is left out
        self.assertIn('Average of 1 computers with a score; 1 have no reading yet', ui.health_summary.text())
        self.assertEqual([ui.health_table.item(1, c).text() for c in range(5)],
                         ['Silent PC', 'Unknown', 'Unknown', 'Unknown', 'Unknown'])
        self.assertEqual(ui.health_table.item(0, 3).text(), 'Does not apply')     # a desktop has no battery

    def test_health_part_values_round_down_like_their_thresholds(self):
        # QA: 79.6 % is below the 80 % line, so it must read 79 %, not 'Needs a look · 80%'.
        self.api.insight_items = [dict(device_id='c', name='Worn PC', group='Lab', kind='laptop', health=dict(battery_health=79.6))]
        self.load([device(0)])
        ui = self.window.insights
        self.window.tabs.setCurrentIndex(ui.index)
        self.wait_idle()
        self.assertEqual(ui.health_table.item(0, 3).text(), 'Needs a look · 79%')

    def test_segmented_stack_native_keyboard_shortcuts_and_interruptible_motion(self):
        from PyQt6.QtWidgets import QStackedWidget
        self.assertIsInstance(self.window.tabs, QStackedWidget)
        self.assertEqual(self.window.tabs.count(), 6)      # Overview, Devices, Incidents, Energy, Administration, This Server
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
            QTest.qWait(700)
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
        self.assertIsNone(self.window.performance_plots['cpu_percent'].bar_color)   # None = the active theme's accent
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

    def test_style_preference_defaults_to_glass_and_round_trips(self):
        directory = Path(self.temporary.name) / 'prefs'
        directory.mkdir()
        self.assertEqual(gui.read_style_preference(directory), 'glass')
        gui.write_theme_preference(directory, 'light')
        gui.write_style_preference(directory, 'classic')
        self.assertEqual(gui.read_style_preference(directory), 'classic')
        self.assertEqual(gui.read_theme_preference(directory), 'light')
        gui.write_style_preference(directory, 'neon')
        self.assertEqual(gui.read_style_preference(directory), 'glass')
        (directory / 'ui.json').write_text('{"style": "bogus"}', encoding='utf-8')
        self.assertEqual(gui.read_style_preference(directory), 'glass')

    def test_style_switch_changes_background_and_both_styles_paint_in_both_themes(self):
        from lifetime_server_devices import active_style, set_style
        from lifetime_theme import active_theme, set_theme
        self.addCleanup(set_style, 'glass')
        self.addCleanup(set_theme, active_theme())
        glass_sheet = self.window.styleSheet()
        self.assertIn('qlineargradient(x1:0, y1:0, x2:1', glass_sheet)
        self.window.style_control.group.button(1).click()       # one live switch (retint_all is slow with many open widgets)
        self.wait(lambda: active_style() == 'classic')
        self.assertEqual(gui.read_style_preference(self.root), 'classic')
        self.assertNotEqual(self.window.styleSheet(), glass_sheet)
        self.assertNotIn('qlineargradient(x1:0, y1:0, x2:1', self.window.styleSheet())
        for style in ('glass', 'classic'):
            for theme in ('dark', 'light'):
                set_style(style)           # no retint_all here: it restyles every widget earlier tests left behind
                set_theme(theme)
                self.window._apply_window_style()
                self.assertFalse(self.window.grab().isNull())


    def incident(self, number=1, state='open', **values):
        return dict(dict(id=number, device_id=device(0)['device_id'], rule='disk_low', cause='disk_low', state=state,
                         started_at='2026-11-07T11:00:00+00:00', opened_at='2026-11-07T11:30:00+00:00',
                         acknowledged_by=None, acknowledged_at=None, resolved_at=None, resolution=None, notified=1,
                         value=4.0, device_name='Computer 000', group_name='Sales'), **values)

    def test_incidents_page_badge_acknowledge_and_vietnamese(self):
        self.api.incidents = [self.incident(), self.incident(2, 'resolved', rule='offline', value=None,
                                                             resolved_at='2026-11-07T12:00:00+00:00', resolution='recovered')]
        self.api.alerts = dict(open=1, acknowledged=0, suppressed=0, pending=0, active=1, paused=0, telemetry_missing=0)
        self.load([device(0)])
        alerts = self.window.alerts
        self.assertTrue(alerts.badge.isVisible())
        self.assertEqual(alerts.badge.text(), '1 open alert')
        self.assertEqual(self.window.tabs.tabText(2), 'Incidents (1)')
        alerts.badge.click()
        self.assertEqual(self.window.tabs.currentIndex(), 2)
        self.assertEqual(alerts.table.rowCount(), 1)        # current problems only by default
        self.assertEqual([alerts.table.item(0, column).text() for column in (0, 1, 2)],
                         ['Computer 000', 'Disk almost full · 4%', 'Needs attention'])
        alerts.filter.setCurrentIndex(alerts.filter.findData(''))
        self.wait_idle()
        self.assertEqual(alerts.table.rowCount(), 2)
        alerts.table.selectRow(0)
        self.wait_idle()
        # The timeline is an event rail now (dots and words), deliveries a line under it; the facts stay in the detail.
        self.assertIn('Incident opened', alerts.rail.text())
        self.assertIn('Sign-in refused', alerts.deliveries.text())
        self.assertEqual(alerts.state_chip.text(), 'Needs attention')
        self.assertTrue(alerts.acknowledge_button.isEnabled())
        alerts.acknowledge_button.click()
        self.wait_idle()
        call = next(arguments for operation, arguments, _ in self.api.calls if operation == 'acknowledge')
        self.assertEqual(call['incident_id'], 1)
        self.assertTrue(call['by'])
        self.assertEqual(alerts.table.item(0, 2).text(), 'Acknowledged')
        self.assertFalse(alerts.acknowledge_button.isEnabled())
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertEqual(self.window.tabs.tabText(2), 'Sự cố (1)')
        self.assertEqual(alerts.table.item(0, 2).text(), 'Đã xác nhận')
        self.assertEqual(alerts.badge.text(), '1 cảnh báo đang mở')
        self.api.alerts = None
        self.window.refresh()
        self.wait_idle()
        self.assertFalse(alerts.badge.isVisible())

    def test_device_detail_shows_hourly_presence_and_a_status_chip(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.api.presence = [dict(state='online', reason='report', start_at=(now - dt.timedelta(hours=3)).isoformat(), end_at=None),
                             dict(state='offline', reason='silent', start_at=(now - dt.timedelta(hours=10)).isoformat(),
                                  end_at=(now - dt.timedelta(hours=3)).isoformat())]
        row = device(0)
        self.load([row])
        self.window.tabs.setCurrentIndex(1)
        self.window._select_box(row['device_id'])
        self.wait_idle()
        kinds = [kind for kind, _ in self.window.presence_bar.ticks]
        self.assertEqual(len(kinds), 24)
        self.assertIsNone(kinds[0])                 # nothing recorded 24 hours ago: an outline, not "online"
        self.assertEqual(kinds[-1], 'good')
        self.assertIn('warning', kinds)
        self.assertIn('online in 3 of 24 hours', self.window.presence_bar.accessibleName())
        self.assertFalse(self.window.presence_box.isHidden())
        self.assertTrue(self.window.detail_status.text())
        self.assertTrue(any(op == 'presence' for op, _, _ in self.api.calls))
        self.api.presence = []
        self.window._select_box(row['device_id'])
        self.window._selection_changed()
        self.wait_idle()
        self.assertTrue(self.window.presence_box.isHidden())     # no monitoring history: nothing drawn

    def test_the_header_subtitle_elides_with_the_full_text_as_its_tooltip(self):
        """QA click-through: 'Demo-Server · Mạng nội bộ' was cut mid-word to 'Mạng nộ' with no way to read the rest."""
        self.load([device(0)])
        self.window.language_button.click()
        self.wait_idle()
        label = self.window.network_label
        self.assertEqual(label.text(), 'Demo-Server · Mạng nội bộ')
        label.resize(90, label.height())
        shown = super(type(label), label).text()
        self.assertTrue(shown.endswith('…'), shown)
        self.assertEqual(label.toolTip(), 'Demo-Server · Mạng nội bộ')
        label.resize(400, label.height())
        self.assertEqual(super(type(label), label).text(), 'Demo-Server · Mạng nội bộ')
        self.assertEqual(label.toolTip(), '')
        self.window.language_button.click()

    def test_pending_approvals_show_whole_rows(self):
        """QA click-through: the 140 px table cut the second request in half behind a scroll bar."""
        rows = [device(i, status='pending') for i in range(2)]
        for i, row in enumerate(rows):
            row['pending_requests'] = [dict(request_id=f'r{i}')]
        self.load(rows)
        table = self.window.admin_pending
        self.assertEqual(table.rowCount(), 2)
        self.assertGreaterEqual(table.height(), table.horizontalHeader().sizeHint().height() + 2 * 56)
        self.assertEqual(table.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_open_device_from_overview_clears_a_filter_that_would_hide_it(self):
        """QA click-through: after Review pending, Open device on an approved computer showed 'No computers match'."""
        rows = [device(0), device(1, status='pending')]
        self.load(rows)
        self.window.review_pending()
        self.wait_idle()
        self.window.tabs.setCurrentIndex(0)
        self.window._fleet_selected = rows[0]['device_id']
        self.window.open_fleet_device()
        self.wait_idle()
        self.assertEqual(self.window.status_filter.currentData(), '')
        self.assertEqual(self.window.selected_device_ids(), [rows[0]['device_id']])

    def test_device_cards_show_24_hour_ticks_loaded_off_the_gui_thread(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.api.presence = [dict(state='online', reason='report', start_at=(now - dt.timedelta(hours=4.5)).isoformat(), end_at=None)]
        self.load([device(0), device(1, status='pending')])
        self.window.tabs.setCurrentIndex(1)
        self.wait_idle()
        boxes = {self.window.table.cellWidget(r, 0).device_id: self.window.table.cellWidget(r, 0) for r in range(self.window.table.rowCount())}
        approved, pending = boxes[device(0)['device_id']], boxes[device(1)['device_id']]
        self.assertEqual(len(approved.presence.ticks), 24)
        self.assertEqual(approved.presence.ticks[-1][0], 'good')
        self.assertFalse(approved.presence.isHidden())
        self.assertIn('online in 5 of 24 hours', approved.accessibleName())
        self.assertTrue(pending.presence.isHidden())         # not connected yet: no monitoring record to show
        presence_calls = [c for c in self.api.calls if c[0] == 'presence']
        self.assertEqual([c[1]['device_id'] for c in presence_calls], [device(0)['device_id']])   # the fake client fails on the GUI thread
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(len([c for c in self.api.calls if c[0] == 'presence']), 1)    # cached for a minute, not asked on every refresh

    def test_device_detail_shows_its_incident_history_and_monitoring_state(self):
        row = device(0)
        row['alerts'] = dict(incidents=[self.incident(state='acknowledged', acknowledged_by='Lan')],
                             status=dict(presence='online', expected=True, paused=True, missing=['cpu_high']))
        self.load([row])
        self.window.tabs.setCurrentIndex(1)
        self.window._select_box(row['device_id'])
        self.wait_idle()
        text = self.window.detail_text.toPlainText()
        for expected in ('Incidents', 'Disk almost full · 4%', 'Acknowledged', 'Paused by maintenance',
                         'Telemetry missing: Processor busy'):
            self.assertIn(expected, text)

    def test_alert_settings_save_through_the_admin_api(self):
        self.load([device(0)])
        alerts = self.window.alerts
        box, threshold, minutes, version, label = alerts.rule_rows['disk_low']
        self.assertEqual(label.text(), 'Disk almost full')
        # No windows and no messages yet: a sentence says so instead of a blank table.
        self.assertTrue(alerts.maintenance_table.isHidden())
        self.assertFalse(alerts.maintenance_empty.isHidden())
        self.assertTrue(alerts.delivery_table.isHidden())
        self.assertIn('No alert messages', alerts.delivery_empty.text())
        threshold.setValue(20)
        alerts.rule_rows['agent_outdated'][3].setText('1.2.0')
        alerts.save_rules()
        self.wait_idle()
        changes = [arguments for operation, arguments, _ in self.api.calls if operation == 'set_alert_rule']
        self.assertEqual(changes, [dict(rule='disk_low', enabled=True, minutes=30, threshold=20),
                                   dict(rule='agent_outdated', enabled=False, minutes=0, version='1.2.0')])
        # A computer is picked by name and group, never typed as an ID.
        self.assertTrue(alerts.schedule_device.isHidden())
        alerts.schedule_scope.setCurrentIndex(alerts.schedule_scope.findData('device'))
        self.assertFalse(alerts.schedule_device.isHidden())
        self.assertTrue(alerts.schedule_key.isHidden())
        self.assertEqual(alerts.schedule_device.currentText(), 'Computer 000 · Sales')
        alerts.save_schedule()
        self.wait_idle()
        schedule = next(arguments for operation, arguments, _ in self.api.calls if operation == 'set_schedule')
        self.assertEqual((schedule['scope'], schedule['key']), ('device', device(0)['device_id']))
        self.api.calls.clear()
        alerts.window_scope.setCurrentIndex(alerts.window_scope.findData('device'))
        alerts.add_window()
        self.wait_idle()
        window = next(arguments for operation, arguments, _ in self.api.calls if operation == 'add_maintenance')
        self.assertEqual((window['scope'], window['key']), ('device', device(0)['device_id']))
        self.api.calls.clear()
        alerts.schedule_scope.setCurrentIndex(alerts.schedule_scope.findData('group'))
        alerts.schedule_key.setText('Sales')
        alerts.schedule_kind.setCurrentIndex(alerts.schedule_kind.findData('business'))
        alerts.schedule_holidays.setText('2026-12-25, 2027-01-01')
        alerts.save_schedule()
        self.wait_idle()
        schedule = next(arguments for operation, arguments, _ in self.api.calls if operation == 'set_schedule')
        self.assertEqual((schedule['scope'], schedule['key'], schedule['kind'], schedule['weekdays'], schedule['holidays']),
                         ('group', 'Sales', 'business', [0, 1, 2, 3, 4], ['2026-12-25', '2027-01-01']))
        alerts.window_scope.setCurrentIndex(0)
        alerts.add_window()
        self.wait_idle()
        window = next(arguments for operation, arguments, _ in self.api.calls if operation == 'add_maintenance')
        self.assertEqual(window['scope'], 'all')
        self.assertLess(window['start'], window['end'])
        self.assertEqual(alerts.password.placeholderText(), 'Saved. Type to replace it.')
        alerts.email_enabled.setChecked(True)
        alerts.smtp_host.setText('mail.school.local')
        alerts.recipients.setText('it@school.local; head@school.local')
        alerts.save_notifications()
        self.wait_idle()
        saved = next(arguments for operation, arguments, _ in self.api.calls if operation == 'set_notifications')
        self.assertNotIn('password', saved)         # an empty field keeps the stored password
        self.assertEqual(saved['recipients'], ['it@school.local', 'head@school.local'])
        alerts.send_test('email')
        self.wait_idle()
        self.assertEqual(alerts.test_result.text(), 'Test message failed: Secure connection failed')

    def test_time_zone_names_without_tzdata_say_why_and_vietnamese_picker(self):
        self.api.iana = False
        self.load([device(0), device(1)])
        alerts = self.window.alerts
        self.assertFalse(alerts.zone_note.isHidden())
        self.assertIn('tzdata', alerts.zone_note.text())
        alerts.schedule_scope.setCurrentIndex(alerts.schedule_scope.findData('default'))
        alerts.schedule_zone.setText('Asia/Ho_Chi_Minh')
        alerts.save_schedule()
        self.wait_idle()
        self.assertFalse(any(operation == 'set_schedule' for operation, _, _ in self.api.calls))
        self.assertIn('+07:00', self.window.status_label.text())
        alerts.coverage_floor.setValue(90)
        alerts.save_floor()
        self.wait_idle()
        self.assertIn(('set_coverage_floor', dict(percent=90)), [(operation, arguments) for operation, arguments, _ in self.api.calls])
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertIn('tzdata', alerts.zone_note.text())
        self.assertIn('múi giờ', alerts.zone_note.text())
        self.assertEqual(alerts.schedule_device.itemText(1), 'Computer 001 · Support')
        self.assertEqual(alerts.schedule_device.accessibleName(), 'Máy tính')


    def test_network_checks_are_added_listed_with_latency_and_translated(self):
        self.load([device(0)])
        ui = self.window.alerts.checks_ui
        self.assertTrue(ui.method.isHidden())       # TCP by default: no web fields
        ui.kind.setCurrentIndex(ui.kind.findData('http'))
        self.assertFalse(ui.method.isHidden())
        ui.name.setText('Intranet')
        ui.target.setText('https://intranet.school.local/')
        ui.keyword.setText('Welcome')
        ui.save()
        self.wait_idle()
        saved = next(arguments for operation, arguments, _ in self.api.calls if operation == 'save_check')
        self.assertEqual((saved['check']['url'], saved['check']['keyword'], saved['check']['verify_tls']),
                         ('https://intranet.school.local/', 'Welcome', True))
        self.assertNotIn('check_id', saved)
        self.api.checks[0].update(last_at='2026-11-07T12:00:00+00:00', last_category='ok', last_latency_ms=42.4)
        self.window.refresh()
        self.wait_idle()
        self.assertEqual([ui.table.item(0, column).text() for column in (0, 2, 3)], ['Intranet', 'Up', '42 ms'])
        ui.table.selectRow(0)
        ui.save()
        self.wait_idle()
        self.assertEqual([a for o, a, _ in self.api.calls if o == 'save_check'][-1]['check_id'], 1)
        ui.scopes.setText('private, 192.168.10.0/24')
        ui.save_scopes()
        self.wait_idle()
        self.assertIn(('set_check_scopes', dict(scopes=['private', '192.168.10.0/24'])),
                      [(o, a) for o, a, _ in self.api.calls])
        self.api.checks[0].update(last_category='timeout', failures=4, last_latency_ms=None)
        self.window.language = 'vi'
        self.window.retranslate()
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(ui.table.item(0, 2).text(), self.window.t('No answer (timeout)') + ' · 4 lần liên tiếp')
        self.assertEqual(ui.kind.itemText(2), 'Trang web (HTTP hoặc HTTPS)')

    def test_agent_updates_upload_share_pause_rollback_and_reports_translated(self):
        self.load([device(0)])
        ui = self.window.alerts.updates_ui
        self.assertEqual(ui.package_label.text(), 'Not available')       # a server without agent updates
        self.api.updates = dict(current=None, previous=None, percent=0, paused=False, rollback=False,
                                counts=dict(staged=0, installed=0, failed=0), reports=[])
        self.window.refresh()
        self.wait_idle()
        self.assertEqual(ui.package_label.text(), 'No package uploaded.')
        self.assertFalse(ui.save_button.isEnabled())
        ui.path.setText('D:/packages/LifetimeAgent.exe')
        ui.version.setText('1.3.0')
        ui.sha256.setText('ab' * 32)
        ui.upload()
        self.wait_idle()
        self.assertIn(('upload_update', dict(path='D:/packages/LifetimeAgent.exe', version='1.3.0', sha256='ab' * 32)),
                      [(o, a) for o, a, _ in self.api.calls])
        self.api.updates.update(current=dict(version='1.3.0', sha256='ab' * 32, size=15 * 1048576, name='LifetimeAgent.exe',
                                             uploaded_at='2026-11-07T11:00:00+00:00', uploaded_by='SCHOOL\\admin'),
                                counts=dict(staged=1, installed=0, failed=1),
                                reports=[dict(device_id=device(0)['device_id'], sha256='ab' * 32, status='failed',
                                              version='1.2.0', category='checksum', at='2026-11-07T12:00:00+00:00')])
        self.window.refresh()
        self.wait_idle()
        self.assertIn('Package 1.3.0 · 15.0 MB', ui.package_label.text())
        self.assertEqual(ui.counts.text(), 'Ready to install: 1 · Installed: 0 · Failed: 1')
        self.assertEqual([ui.table.item(0, column).text() for column in (0, 1, 2)],
                         ['Computer 000', 'Failed · SHA-256 did not match', '1.2.0'])
        ui.percent.setValue(10)
        ui.save()
        self.wait_idle()
        ui.pause()
        self.wait_idle()
        self.assertEqual(ui.pause_button.text(), 'Resume')
        with patch.object(self.window, '_confirm', return_value=False):
            ui.rollback()                                                   # declined: nothing is sent
        with patch.object(self.window, '_confirm', return_value=True):
            ui.rollback()
        self.wait_idle()
        sent = [(o, a) for o, a, _ in self.api.calls if o in ('set_update', 'rollback_update')]
        self.assertEqual(sent, [('set_update', dict(percent=10)), ('set_update', dict(paused=True)), ('rollback_update', {})])
        self.window.language = 'vi'
        self.window.retranslate()
        self.wait_idle()
        self.assertEqual(ui.table.item(0, 1).text(), 'Không thành công · SHA-256 không khớp')
        self.assertEqual(ui.pause_button.text(), 'Tiếp tục')


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


class ControlRowTests(unittest.TestCase):
    def test_height_for_width_is_kept_until_the_layout_is_invalidated(self):
        # Laying out the first show of a long page asked the same width hundreds of times.
        from PyQt6.QtWidgets import QWidget
        from lifetime_layout import ControlRow
        self.app = qt_app()
        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        row = ControlRow(holder)
        buttons = [QPushButton('Button text') for _ in range(4)]
        for button in buttons:
            row.addWidget(button)
        wide = row.heightForWidth(2000)
        with patch.object(row, '_arrange', side_effect=AssertionError('recomputed')):
            self.assertEqual(row.heightForWidth(2000), wide)
        self.assertGreater(row.heightForWidth(120), wide)              # a narrow row still wraps
        for button in buttons:
            button.setMinimumWidth(900)                                 # Qt invalidates the layout
        self.assertGreater(row.heightForWidth(2000), wide)


if __name__ == '__main__':
    unittest.main()
