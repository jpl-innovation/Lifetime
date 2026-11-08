"""Native, loopback-only Lifetime Server dashboard. All I/O runs in Tasks.

The window consumes AdminClient results and never opens SQLite. All English/Vietnamese text uses the shared application catalogue.
"""
import argparse
import datetime as dt
from pathlib import Path
import sys

from PyQt6.QtCore import QDate, QItemSelectionModel, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit,
                            QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                            QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                            QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                            QSpinBox, QSplitter, QTabWidget, QTableWidget,
                            QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from lifetime_admin import AdminClient
import lifetime_i18n as catalog
from lifetime_protocol import IDENTITY_FIELDS, validate_identity
from lifetime_theme import THEMES, stylesheet
from lifetime_tasks import Tasks
from lifetime_tls import encode_bundle

SERVER_TEXT_VI = catalog.SERVER_DASHBOARD_VI


def _create_bundle(client, directory, address, ttl, uses):
    # Read/validate public trust material before consuming a new pairing code.
    with (directory / 'server-cert.pem').open('rb') as source:
        certificate = source.read(16385).decode('ascii')
    encode_bundle(address, 'validate-address', certificate)
    issued = client.call('code', ttl=ttl, uses=uses)
    return dict(bundle=encode_bundle(address, issued['code'], certificate), expires_at=issued['expires_at'])


def text(key, language='en', **values):
    if key in catalog.EN:
        value = catalog.TABLES[language][key]
    else:
        value = SERVER_TEXT_VI.get(key, key) if language == 'vi' else key
    return value.format(**values)


class _Item(QTableWidgetItem):
    def __lt__(self, other):
        left, right = self.data(Qt.ItemDataRole.UserRole + 1), other.data(Qt.ItemDataRole.UserRole + 1)
        return (left is None, left) < (right is None, right)


class ServerWindow(QMainWindow):
    export_requested = pyqtSignal(object)

    def __init__(self, directory, admin_port, language='en', parent=None):
        super().__init__(parent)
        if language not in ('en', 'vi'):
            raise ValueError('Unsupported language')
        self.directory, self.language = Path(directory), language
        self.client = AdminClient(directory, admin_port)
        self.tasks = Tasks(self)
        self.tasks.completed.connect(lambda number, result: self._completed(number, result, True))
        self.tasks.failed.connect(lambda number, error: self._completed(number, None, False))
        self._jobs = {}
        self._closed = False
        self._revision, self._list_busy, self._list_again = 0, False, False
        self._offset, self._rows, self._detail = 0, [], None
        self._detail_revision = 0
        self._identity_dirty = False
        self._mutation_busy = False
        self._configuration_busy = False
        self._configuration_loaded = False
        self._configuration_dirty = False
        self._overview_busy, self._overview_again, self._overview_result = False, False, None
        self._configuration_result = None
        self._bundle_expiry = None
        self._status_failure = False
        self._bindings = []
        self.setWindowTitle('Lifetime Server')
        self.resize(1120, 820)
        self.setMinimumSize(420, 400)
        self.setStyleSheet(stylesheet() + '\nQGroupBox { border: 1px solid ' + THEMES['dark']['border'] +
                           '; border-radius: 6px; margin-top: 16px; padding: 16px; }'
                           'QGroupBox::title { subcontrol-origin: margin; left: 12px; }'
                           'QTextBrowser, QPlainTextEdit { background: ' + THEMES['dark']['card'] +
                           '; border: 1px solid ' + THEMES['dark']['border'] + '; }')
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        title = QLabel('Lifetime Server')
        font = title.font()
        font.setPointSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        title.setStyleSheet('font-size: 23px; font-weight: 600;')
        heading.addWidget(title, 1)
        self.language_choice = QComboBox()
        self.language_choice.addItem('English', 'en')
        self.language_choice.addItem('Tiếng Việt', 'vi')
        self.language_choice.setCurrentIndex(0 if language == 'en' else 1)
        self.language_choice.setAccessibleName(self.t('Language'))
        heading.addWidget(self.language_choice)
        self.refresh_button = self.button('Refresh', self.refresh)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.status_label = self.label('Ready')
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName(self.t('Local administration'))
        layout.addWidget(self.status_label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(body)
        self._make_overview()
        self._make_devices()
        self._make_admin()
        self.language_choice.currentIndexChanged.connect(self._language_changed)
        self.export_requested.connect(self._show_export)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._load_devices)
        self.search.textChanged.connect(self._filters_changed)
        self.group.textChanged.connect(self._filters_changed)
        self.status_filter.currentIndexChanged.connect(self._filters_changed)
        self.page_size.currentIndexChanged.connect(self._filters_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        self.retranslate()
        QTimer.singleShot(0, self.refresh)

    def t(self, key, **values):
        return text(key, self.language, **values)

    def bind(self, widget, key, setter='setText'):
        self._bindings.append((widget, key, setter))
        getattr(widget, setter)(self.t(key))
        widget.setAccessibleName(self.t(key))
        return widget

    def label(self, key):
        label = self.bind(QLabel(), key)
        label.setWordWrap(True)
        return label

    def button(self, key, callback):
        widget = self.bind(QPushButton(), key)
        widget.clicked.connect(callback)
        return widget

    def _scroll_page(self, key):
        page, scroll = QWidget(), QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        self.tabs.addTab(scroll, self.t(key))
        return layout

    def _make_overview(self):
        layout = self._scroll_page('Overview')
        self.scope = self.label('Fleet overview; independent of the device search and filters.')
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.overview_grid = QGridLayout()
        self.overview_grid.setSpacing(24)
        self.overview_counts = QWidget()
        form = QFormLayout(self.overview_counts)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        self.counts = {}
        for key in ('All devices', 'Reporting', 'Report overdue', 'Pending approval', 'Approved', 'Denied', 'Revoked', 'Unavailable'):
            value = QLabel(self.t('Not available'))
            self.counts[key] = value
            form.addRow(self.label(key), value)
        self.overview_usage = QWidget()
        usage_layout = QVBoxLayout(self.overview_usage)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.setSpacing(16)
        usage_layout.addWidget(self.label('Latest cumulative snapshots (hours)'))
        totals_form = QFormLayout()
        self.fleet_totals = {}
        for key in ('Measured', 'Imported', 'Baseline'):
            value = QLabel(self.t('Not available'))
            self.fleet_totals[key] = value
            totals_form.addRow(self.label(key), value)
        usage_layout.addLayout(totals_form)
        note = self.label('One latest snapshot per device; snapshots are not added over time.')
        note.setWordWrap(True)
        usage_layout.addWidget(note)
        self.snapshot_scope = QLabel()
        self.snapshot_scope.setWordWrap(True)
        usage_layout.addWidget(self.snapshot_scope)
        usage_layout.addStretch()
        layout.addLayout(self.overview_grid)
        self._arrange_overview()
        self.overview_health = QLabel(self.t('Loading…'))
        self.overview_health.setWordWrap(True)
        layout.addWidget(self.overview_health)
        layout.addWidget(self.label('Recent events'))
        self.events = QTableWidget(0, 3)
        self.events.setAccessibleName(self.t('Recent events'))
        self.events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.events.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.events.setMinimumHeight(180)
        self.events.setMaximumHeight(340)
        self.events.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.events)
        layout.addWidget(self.button('Export fleet', lambda: self.export_requested.emit(None)))
        layout.addStretch()

    def _make_devices(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(8)
        filters = QGridLayout()
        self.search, self.group = QLineEdit(), QLineEdit()
        self.bind(self.search, 'Search name or asset tag', 'setPlaceholderText')
        self.bind(self.group, 'All groups (exact department filter)', 'setPlaceholderText')
        self.status_filter = QComboBox()
        for code in ('', 'pending', 'approved', 'denied', 'revoked'):
            self.status_filter.addItem('', code)
        for row, key, widget in ((0, 'Search', self.search), (1, 'Group', self.group),
                                 (2, 'Status', self.status_filter)):
            label = self.label(key)
            label.setBuddy(widget)
            filters.addWidget(label, row, 0)
            filters.addWidget(widget, row, 1)
        self.status_filter.setAccessibleName(self.t('Status'))
        layout.addLayout(filters)
        self.empty_label = self.label('No devices match. Create a pairing code in Administration to enroll a computer.')
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName(self.t('Devices'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(140)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.table)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        detail_page = QWidget()
        self.detail_scroll.setWidget(detail_page)
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setSpacing(16)
        self.detail_text = QTextBrowser()
        self.detail_text.setOpenExternalLinks(False)
        self.detail_text.setAccessibleName(self.t('Device details'))
        self.detail_text.setMinimumHeight(190)
        detail_layout.addWidget(self.detail_text)
        identity_box = self.bind(QGroupBox(), 'Server-owned identity', 'setTitle')
        identity_form = QFormLayout(identity_box)
        identity_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.identity_fields = {}
        for field, key in zip(IDENTITY_FIELDS, ('Display name', 'Asset tag', 'Department', 'Location', 'Deployment date')):
            edit = QLineEdit()
            edit.setMaxLength(4096)
            label = self.label(key)
            label.setBuddy(edit)
            edit.setAccessibleName(self.t(key))
            edit.textEdited.connect(self._mark_identity_dirty)
            self.identity_fields[field] = edit
            identity_form.addRow(label, edit)
        self.identity_fields['deployment_date'].setPlaceholderText('YYYY-MM-DD')
        self.save_identity_button = self.button('Save identity', self.save_identity)
        self.revoke_button = self.button('Revoke credential', self.revoke)
        self.request_refresh_button = self.button('Request fresh report', self.request_fresh_report)
        identity_form.addRow(self.save_identity_button)
        identity_form.addRow(self.revoke_button)
        identity_form.addRow(self.request_refresh_button)
        refresh_note = self.label('A request is delivered on the next sync; this does not contact or control the computer.')
        refresh_note.setWordWrap(True)
        identity_form.addRow(refresh_note)
        detail_layout.addWidget(identity_box)
        pending = self.bind(QGroupBox(), 'Enrollment requests', 'setTitle')
        pending_layout = QVBoxLayout(pending)
        self.pending_requests = QComboBox()
        self.pending_requests.setAccessibleName(self.t('Pending requests'))
        pending_layout.addWidget(self.pending_requests)
        self.approve_button = self.button('Approve', lambda: self.decide('approve'))
        self.deny_button = self.button('Deny', lambda: self.decide('deny'))
        pending_layout.addWidget(self.approve_button)
        pending_layout.addWidget(self.deny_button)
        detail_layout.addWidget(pending)
        splitter.addWidget(self.detail_scroll)
        splitter.setSizes([280, 330])
        layout.addWidget(splitter, 1)
        pagination = QGridLayout()
        self.previous_button = self.button('Previous', lambda: self._page(-1))
        self.next_button = self.button('Next', lambda: self._page(1))
        self.page_label = QLabel()
        self.page_label.setWordWrap(True)
        self.page_size = QComboBox()
        for number in (25, 50, 100, 250, 500):
            self.page_size.addItem(str(number), number)
        self.page_size.setAccessibleName(self.t('Page size'))
        pagination.addWidget(self.previous_button, 0, 0)
        pagination.addWidget(self.next_button, 0, 1)
        pagination.addWidget(self.page_size, 0, 2)
        pagination.addWidget(self.page_label, 1, 0, 1, 3)
        layout.addLayout(pagination)
        self.export_button = self.button('Export selected', self.export_selection)
        layout.addWidget(self.export_button)
        self.tabs.addTab(page, self.t('Devices'))
        self._set_device_actions(False)

    def _make_admin(self):
        self.admin_layout = self._scroll_page('Administration')
        configuration = self.bind(QGroupBox(), 'Configuration', 'setTitle')
        form = QFormLayout(configuration)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.interval = QSpinBox()
        self.interval.setRange(5, 3600)
        self.interval.setEnabled(False)
        self.interval.setAccessibleName(self.t('Reporting interval (seconds)'))
        self.interval.valueChanged.connect(self._configuration_edited)
        form.addRow(self.label('Reporting interval (seconds)'), self.interval)
        self.interval_button = self.button('Save reporting interval', self.save_configuration)
        self.interval_button.setEnabled(False)
        form.addRow(self.interval_button)
        self.retention_boundary = QLabel()
        self.retention_boundary.setWordWrap(True)
        form.addRow(self.retention_boundary)
        self.admin_layout.addWidget(configuration)
        enrollment = self.bind(QGroupBox(), 'Pair a computer', 'setTitle')
        form = QFormLayout(enrollment)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.server_address = QLineEdit('https://localhost:8443')
        self.server_address.setAccessibleName(self.t('Server HTTPS address'))
        self.code_ttl, self.code_uses = QSpinBox(), QSpinBox()
        self.code_ttl.setRange(1, 86400)
        self.code_ttl.setValue(600)
        self.code_uses.setRange(1, 100)
        for key, widget in (('Server HTTPS address', self.server_address),
                            ('Code lifetime (seconds)', self.code_ttl), ('Allowed uses', self.code_uses)):
            label = self.label(key)
            label.setBuddy(widget)
            widget.setAccessibleName(self.t(key))
            form.addRow(label, widget)
        self.code_button = self.button('Create enrollment bundle', self.create_code)
        form.addRow(self.code_button)
        self.bundle_output = QPlainTextEdit()
        self.bundle_output.setReadOnly(True)
        self.bundle_output.setMaximumHeight(130)
        self.bundle_output.setAccessibleName(self.t('Enrollment bundle'))
        form.addRow(self.bundle_output)
        self.code_expiry = QLabel()
        self.code_expiry.setWordWrap(True)
        form.addRow(self.code_expiry)
        note = self.label('Share this bundle only with the technician pairing the computer. Approval is still required.')
        note.setWordWrap(True)
        form.addRow(note)
        self.admin_layout.addWidget(enrollment)
        retention = self.bind(QGroupBox(), 'Retention', 'setTitle')
        form = QFormLayout(retention)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.retention_date = QDateEdit(QDate.currentDate())
        self.retention_date.setCalendarPopup(True)
        self.retention_date.setDisplayFormat('yyyy-MM-dd')
        self.retention_date.setAccessibleName(self.t('Delete records before (UTC)'))
        form.addRow(self.label('Delete records before (UTC)'), self.retention_date)
        self.retain_button = self.button('Apply retention', self.apply_retention)
        form.addRow(self.retain_button)
        self.admin_layout.addWidget(retention)
        backups = self.bind(QGroupBox(), 'Backup and restore', 'setTitle')
        layout = QVBoxLayout(backups)
        self.backup_button = self.button('Save backup', self.backup)
        self.restore_button = self.button('Restore backup', self.restore)
        layout.addWidget(self.backup_button)
        layout.addWidget(self.restore_button)
        note = self.label('Restore invalidates every device credential, pairing code and polling token. Computers must enroll again.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.admin_layout.addWidget(backups)
        self.admin_layout.addStretch()

    def _set_device_actions(self, enabled):
        enabled = enabled and not self._mutation_busy
        for widget in (*self.identity_fields.values(), self.save_identity_button, self.revoke_button,
                       self.request_refresh_button):
            widget.setEnabled(enabled)
        self.request_refresh_button.setEnabled(bool(enabled and self._detail and self._detail.get('status') == 'approved'))
        self.export_button.setEnabled(bool(self.selected_device_ids()) if hasattr(self, 'export_button') else False)
        pending = enabled and self.pending_requests.count() > 0
        self.approve_button.setEnabled(pending)
        self.deny_button.setEnabled(pending)

    def _mark_identity_dirty(self):
        self._identity_dirty = True

    def _language_changed(self):
        self.language = self.language_choice.currentData()
        self.retranslate()

    def retranslate(self):
        for widget, key, setter in self._bindings:
            getattr(widget, setter)(self.t(key))
            widget.setAccessibleName(self.t(key))
        for index, key in enumerate(('Overview', 'Devices', 'Administration')):
            self.tabs.setTabText(index, self.t(key))
        for index, key in enumerate(('All statuses', 'Pending approval', 'Approved', 'Denied', 'Revoked')):
            self.status_filter.setItemText(index, self.t(key))
        self.table.setHorizontalHeaderLabels([self.t(key) for key in
                                              ('Display name', 'Asset tag', 'Department', 'Status', 'Last report received')])
        self.events.setHorizontalHeaderLabels([self.t(key) for key in ('Device ID', 'Event', 'Time')])
        if self._overview_result is not None:
            self._render_overview(self._overview_result)
        if self._configuration_result is not None:
            self._render_configuration(self._configuration_result)
        if self._bundle_expiry:
            self.code_expiry.setText(self.t('Expires at {time}', time=self._bundle_expiry))
        for field, key in zip(IDENTITY_FIELDS, ('Display name', 'Asset tag', 'Department', 'Location', 'Deployment date')):
            self.identity_fields[field].setAccessibleName(self.t(key))
        for widget, key in ((self.language_choice, 'Language'), (self.status_filter, 'Status'),
                            (self.table, 'Devices'), (self.detail_text, 'Device details'),
                            (self.page_size, 'Page size'), (self.pending_requests, 'Pending requests'),
                            (self.interval, 'Reporting interval (seconds)'), (self.events, 'Recent events'),
                            (self.bundle_output, 'Enrollment bundle'), (self.retention_date, 'Delete records before (UTC)')):
            widget.setAccessibleName(self.t(key))
        self._render_rows(self._rows)
        if self._detail:
            self._render_detail(self._detail, preserve_edits=self._identity_dirty)
        else:
            self.detail_text.setPlainText(self.t('Select a device to view its usage, permissions and server-owned identity.'))

    def _submit(self, work, callback, button=None):
        if self._closed or len(self._jobs) >= 8:
            return False
        number = self.tasks.submit(work)
        self._jobs[number] = (callback, button)
        if button is not None:
            button.setEnabled(False)
        if not self._status_failure:
            self.status_label.setText(self.t('Working…'))
        return True

    def _completed(self, number, result, success):
        callback, button = self._jobs.pop(number)
        if self._closed:
            return
        if button is not None:
            button.setEnabled(True)
        if not success:
            self._status_failure = True
            self.status_label.setText(self.t(
                'Request failed. Check the service, admin port and administrator permissions, then refresh.'))
        elif not self._status_failure:
            self.status_label.setText(self.t('Working…' if self._jobs else 'Ready'))
        callback(result, success)

    def refresh(self):
        self._status_failure = False
        self._load_devices()
        self._load_configuration()
        self._load_overview()

    def _load_overview(self):
        if self._closed:
            return
        if self._overview_busy:
            self._overview_again = True
            return
        self._overview_busy = True
        client = self.client
        if not self._submit(lambda: client.call('overview'), self._overview_finished):
            self._overview_busy = False

    def _overview_finished(self, result, success):
        self._overview_busy = False
        if success:
            self._overview_result = result
            self._render_overview(result)
        else:
            self.overview_health.setText(self.t('Overview unavailable. Showing the last successful data, if any.'))
        if self._overview_again:
            self._overview_again = False
            self._load_overview()

    def _render_overview(self, result):
        for key, code in (('All devices', 'total'), ('Reporting', 'reporting'),
                          ('Report overdue', 'report-overdue'), ('Pending approval', 'pending'),
                          ('Approved', 'approved'), ('Denied', 'denied'), ('Revoked', 'revoked'), ('Unavailable', 'unavailable')):
            self.counts[key].setText(str(result['counts'][code]))
        totals = result.get('totals') or {}
        for key, field in (('Measured', 'measured_seconds'), ('Imported', 'imported_seconds'), ('Baseline', 'baseline_seconds')):
            value = totals.get(field)
            self.fleet_totals[key].setText(self.t('Not available') if value is None else f'{value / 3600:,.2f}')
        self.snapshot_scope.setText(self.t('{known} devices have snapshots; {unknown} have no snapshot. Totals include known devices only.',
                                            known=result['known_devices'], unknown=result['unknown_devices']))
        self.overview_health.setText(self.t('Service available · schema {version} · server time {time}',
                                            version=result['schema_version'], time=result['server_time']))
        vertical, horizontal = self.events.verticalScrollBar().value(), self.events.horizontalScrollBar().value()
        self.events.setRowCount(len(result['recent_events']))
        for row, event in enumerate(result['recent_events']):
            kind = self.t('Report received' if event['kind'] == 'report-received' else 'Enrollment pending')
            for column, value in enumerate((event['device_id'], kind, event['time'])):
                self.events.setItem(row, column, QTableWidgetItem(value))
        self.events.verticalScrollBar().setValue(vertical)
        self.events.horizontalScrollBar().setValue(horizontal)

    def _configuration_edited(self):
        self._configuration_dirty = True

    def _load_configuration(self):
        if self._configuration_busy or self._closed:
            return
        client = self.client
        self._configuration_busy = True
        if not self._submit(lambda: client.call('configuration'), self._configuration_finished):
            self._configuration_busy = False

    def _configuration_finished(self, result, success):
        self._configuration_busy = False
        if success:
            self._configuration_result = result
            self._configuration_loaded = True
            if not self._configuration_dirty:
                self.interval.blockSignals(True)
                self.interval.setValue(result['reporting_interval'])
                self.interval.blockSignals(False)
            self._render_configuration(result)
        self.interval.setEnabled(self._configuration_loaded)
        self.interval_button.setEnabled(self._configuration_loaded)

    def _render_configuration(self, result):
        self.retention_boundary.setText(self.t('Current boundary (UTC): {time}',
                                              time=result.get('retained_before') or self.t('Not available')))

    def _filters_changed(self):
        self._revision += 1
        self._offset = 0
        self._search_timer.start()

    def _page(self, direction):
        self._offset = max(0, self._offset + direction * self.page_size.currentData())
        self._revision += 1
        self._load_devices()

    def _load_devices(self):
        if self._closed:
            return
        if self._list_busy:
            self._list_again = True
            return
        arguments = dict(limit=self.page_size.currentData(), offset=self._offset,
                         search=self.search.text(), group=self.group.text(), status=self.status_filter.currentData())
        revision, client = self._revision, self.client
        self._list_busy = True
        submitted = self._submit(lambda: client.call('devices', **arguments),
                                lambda result, ok: self._devices_finished(result, ok, revision))
        if not submitted:
            self._list_busy = False

    def _devices_finished(self, rows, success, revision):
        self._list_busy = False
        if success and revision == self._revision:
            self._rows = rows
            self._render_rows(rows)
        if self._list_again or revision != self._revision:
            self._list_again = False
            self._load_devices()

    def selected_device_ids(self):
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def _render_rows(self, rows):
        selected = set(self.selected_device_ids())
        focused = QApplication.focusWidget()
        vertical = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        current = self.table.currentItem()
        current_uuid = current.data(Qt.ItemDataRole.UserRole) if current else None
        current_column = self.table.currentColumn()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_number, device in enumerate(rows):
            identity = device.get('identity', {})
            status = device.get('reporting_status') if device.get('status') == 'approved' else device.get('status')
            values = [identity.get('display_name') or device['device_id'], identity.get('asset_tag', ''),
                      identity.get('department', ''), self._status_text(status),
                      device.get('last_received_at') or self.t('Not available')]
            for column, value in enumerate(values):
                item = _Item(str(value))
                item.setData(Qt.ItemDataRole.UserRole, device['device_id'])
                item.setData(Qt.ItemDataRole.UserRole + 1, str(value).casefold())
                self.table.setItem(row_number, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(max(0, sort_column), sort_order)
        self.table.clearSelection()
        for row_number in range(self.table.rowCount()):
            identifier = self.table.item(row_number, 0).data(Qt.ItemDataRole.UserRole)
            if identifier in selected:
                for column in range(self.table.columnCount()):
                    self.table.item(row_number, column).setSelected(True)
            if identifier == current_uuid:
                self.table.setCurrentCell(row_number, max(0, current_column), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(vertical)
        self.table.horizontalScrollBar().setValue(horizontal)
        if focused is not None:
            focused.setFocus(Qt.FocusReason.OtherFocusReason)
        self.empty_label.setVisible(not rows)
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(len(rows) == self.page_size.currentData())
        self.page_label.setText(self.t('Page {page} · {count} devices',
                                     page=self._offset // self.page_size.currentData() + 1, count=len(rows)))
        self._selection_changed()

    def _status_text(self, value):
        return self.t({'pending': 'Pending approval', 'approved': 'Approved', 'denied': 'Denied',
                       'revoked': 'Revoked', 'reporting': 'Reporting', 'report-overdue': 'Report overdue',
                       'unavailable': 'Unavailable'}.get(value, 'Unknown'))

    def _selection_changed(self):
        selected = self.selected_device_ids()
        self.export_button.setEnabled(bool(selected))
        if len(selected) != 1:
            self._detail_revision += 1
            self._detail = None
            self.detail_text.setPlainText(self.t('Select a device to view its usage, permissions and server-owned identity.'))
            self._set_device_actions(False)
            return
        device_id = selected[0]
        if not self._detail or self._detail['device_id'] != device_id:
            self._detail = None
            self._identity_dirty = False
            self._set_device_actions(False)
            self.detail_text.setPlainText(self.t('Loading…'))
        self._detail_revision += 1
        revision, client = self._detail_revision, self.client
        self._submit(lambda: client.call('device', device_id=device_id, limit=25, after_seq=0),
                     lambda result, ok: self._detail_finished(result, ok, revision, device_id))

    def _detail_finished(self, result, success, revision, device_id):
        if revision != self._detail_revision or self.selected_device_ids() != [device_id]:
            return
        if success:
            same = self._detail and self._detail['device_id'] == device_id
            self._detail = result
            self._render_detail(result, preserve_edits=bool(same and self._identity_dirty))
        else:
            self._set_device_actions(False)

    def _render_detail(self, device, preserve_edits=False):
        missing = self.t('Not available')
        totals = device.get('totals') or {}
        def hours(key):
            value = totals.get(key)
            return missing if value is None else f'{value / 3600:,.2f}'
        lines = [f"{self.t('Device ID')}: {device['device_id']}",
                 f"{self.t('Status')}: {self._status_text(device.get('status'))} / {self._status_text(device.get('reporting_status'))}",
                 f"{self.t('Last report received')}: {device.get('last_received_at') or missing}", '',
                 self.t('Usage snapshots (hours; never added together)'),
                 f"{self.t('Measured')}: {hours('measured_seconds')}",
                 f"{self.t('Imported')}: {hours('imported_seconds')}",
                 f"{self.t('Baseline')}: {hours('baseline_seconds')}",
                 f"{self.t('Coverage')}: {self.t('Unknown')}", '', self.t('Optional system readings')]
        policy = device.get('policy') or {}
        readings = device.get('readings') if policy.get('resources') is True else None
        if policy.get('resources') is not True:
            lines.append(self.t('Not permitted'))
        elif not readings:
            lines.append(missing)
        else:
            observed = readings.get('observed_at')
            try:
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(observed.replace('Z', '+00:00'))).total_seconds()
                freshness = self.t('Stale report') if age > max(600, self.interval.value() * 3) or device.get('status') != 'approved' else (
                    self.t('Current report') if age >= -300 else self.t('Reading age is unknown.'))
            except (ValueError, TypeError, AttributeError):
                freshness = self.t('Reading age is unknown.')
            lines += [freshness, f"{self.t('Observed at')}: {observed or missing}",
                      f"{self.t('Received at')}: {device.get('readings_received_at') or missing}"]
            for field, key in (('system', 'System'), ('release', 'Release'), ('machine', 'Machine'), ('python_version', 'Python version')):
                if readings.get(field):
                    lines.append(f'{self.t(key)}: {readings[field]}')
        history = self.t('Not permitted') if policy.get('history') is not True else (
            self.t('Available') if device.get('history') is not None else missing)
        lines += ['', f"{self.t('Optional history')}: {history}"]
        scroll = self.detail_text.verticalScrollBar().value()
        self.detail_text.setPlainText('\n'.join(lines))
        self.detail_text.verticalScrollBar().setValue(scroll)
        if not preserve_edits:
            for field, edit in self.identity_fields.items():
                edit.setText(device.get('identity', {}).get(field, ''))
            self._identity_dirty = False
        selected_request = self.pending_requests.currentData()
        self.pending_requests.clear()
        for request in device.get('pending_requests', []):
            self.pending_requests.addItem(request['request_id'], request['request_id'])
        if self.pending_requests.findData(selected_request) >= 0:
            self.pending_requests.setCurrentIndex(self.pending_requests.findData(selected_request))
        self._set_device_actions(True)

    def save_identity(self):
        if not self._detail or self.selected_device_ids() != [self._detail['device_id']]:
            return
        identity = {field: edit.text() for field, edit in self.identity_fields.items()}
        try:
            validate_identity(identity)
        except ValueError:
            self.status_label.setText(self.t('Identity is invalid. Use an optional YYYY-MM-DD deployment date.'))
            return
        self._mutate('identity', dict(device_id=self._detail['device_id'], identity=identity))

    def revoke(self):
        if self._detail and self._confirm('Immediately reject this device credential? Re-enrollment requires approval.'):
            self._mutate('revoke', dict(device_id=self._detail['device_id']))

    def decide(self, operation):
        request = self.pending_requests.currentData()
        if request and operation in ('approve', 'deny'):
            self._mutate(operation, dict(request_id=request))

    def request_fresh_report(self):
        if self._detail:
            self._mutate('refresh', dict(device_id=self._detail['device_id']))

    def _mutate(self, operation, arguments, button=None):
        if self._mutation_busy:
            return
        self._mutation_busy = True
        self._set_device_actions(False)
        client = self.client
        if not self._submit(lambda: client.call(operation, **arguments),
                            lambda result, ok: self._mutation_finished(operation, result, ok), button):
            self._mutation_busy = False
            self._set_device_actions(bool(self._detail))

    def _mutation_finished(self, operation, result, success):
        self._mutation_busy = False
        if success:
            if operation == 'identity':
                self._identity_dirty = False
            if operation == 'set_configuration':
                self._configuration_dirty = False
            self.status_label.setText(self.t('Changes saved.'))
            self._revision += 1
            self._load_devices()
            self._load_overview()
            if operation == 'set_configuration':
                self._load_configuration()
            if operation in ('retain', 'restore'):
                self._load_configuration()
        self._set_device_actions(bool(self._detail))

    def _confirm(self, message):
        dialog = QMessageBox(QMessageBox.Icon.Warning, self.t('Continue?'), self.t(message), parent=self)
        yes = dialog.addButton(self.t('Continue?'), QMessageBox.ButtonRole.AcceptRole)
        no = dialog.addButton(self.t('Cancel'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(no)
        dialog.setEscapeButton(no)
        dialog.exec()
        return dialog.clickedButton() is yes

    def create_code(self):
        client, directory = self.client, self.directory
        address, ttl, uses = self.server_address.text(), self.code_ttl.value(), self.code_uses.value()
        self.bundle_output.clear()
        self._bundle_expiry = None
        self.code_expiry.clear()
        self._submit(lambda: _create_bundle(client, directory, address, ttl, uses), self._code_finished, self.code_button)

    def _code_finished(self, result, success):
        if success:
            self._bundle_expiry = result['expires_at']
            self.bundle_output.setPlainText(result['bundle'])
            self.code_expiry.setText(self.t('Expires at {time}', time=result['expires_at']))

    def save_configuration(self):
        if self._configuration_loaded:
            self._mutate('set_configuration', dict(reporting_interval=self.interval.value()), self.interval_button)

    def apply_retention(self):
        if self._confirm('Delete older records permanently? Latest totals remain, with their original timestamps.'):
            before = self.retention_date.date().toString('yyyy-MM-dd') + 'T00:00:00+00:00'
            self._mutate('retain', dict(before=before), self.retain_button)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save backup'), 'lifetime-backup.sqlite',
                                             self.t('SQLite backups (*.sqlite *.db);;All files (*)'))
        if path:
            self._mutate('backup', dict(path=path), self.backup_button)

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Restore backup'), '',
                                             self.t('SQLite backups (*.sqlite *.db);;All files (*)'))
        if path and self._confirm('Restore invalidates every device credential, pairing code and polling token. Computers must enroll again.'):
            self._mutate('restore', dict(path=path), self.restore_button)

    def export_selection(self):
        selected = self.selected_device_ids()
        if selected:
            self.export_requested.emit(selected)

    def _show_export(self, device_ids):
        try:
            from lifetime_server_export import ServerExportDialog
            ServerExportDialog(self.client, device_ids=device_ids, parent=self, language=self.language).exec()
        except Exception:
            self.status_label.setText(self.t('Export could not open. Check the reporting components and try again.'))

    def closeEvent(self, event):
        self._closed = True
        self.timer.stop()
        self._search_timer.stop()
        super().closeEvent(event)

    def _arrange_overview(self):
        self.overview_grid.addWidget(self.overview_counts, 0, 0)
        wide = self.width() >= 740
        self.overview_grid.addWidget(self.overview_usage, 0 if wide else 1, 1 if wide else 0)
        self.overview_grid.setColumnStretch(0, 1)
        self.overview_grid.setColumnStretch(1, 1 if wide else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'overview_grid'):
            self._arrange_overview()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lifetime Server local dashboard')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--admin-port', type=int, required=True)
    parser.add_argument('--language', choices=('en', 'vi'), default='en')
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = ServerWindow(args.directory, args.admin_port, args.language)
    window.show()
    return application.exec()


if __name__ == '__main__':
    raise SystemExit(main())
