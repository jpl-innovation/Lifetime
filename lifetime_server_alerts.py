"""Incidents page, alert badge and the alert settings under Administration, for the Lifetime Server dashboard.

Everything goes through the window's AdminClient in background Tasks (never SQLite) and reuses its panels,
tables, buttons and translations, so Glass/Classic, light/dark and English/Vietnamese follow the window.
"""
import datetime as dt
import getpass
import html

from PyQt6.QtCore import QDateTime, QTime, Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDoubleSpinBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
                             QTimeEdit, QVBoxLayout, QWidget)

from lifetime_layout import ControlRow
from lifetime_server_widgets import status_dot, table_action
from lifetime_theme import T

RULE_TEXT = {'offline': 'Computer offline', 'no_report': 'No report received', 'disk_low': 'Disk almost full',
             'cpu_high': 'Processor busy', 'memory_high': 'Memory almost full', 'battery_worn': 'Battery worn',
             'temperature_high': 'Running hot', 'crashes': 'Repeated crashes', 'agent_outdated': 'Lifetime version too old'}
UNIT = {'disk_low': '%', 'cpu_high': '%', 'memory_high': '%', 'battery_worn': '%', 'temperature_high': ' °C', 'crashes': ''}
STATE_TEXT = {'pending': 'Starting', 'open': 'Needs attention', 'acknowledged': 'Acknowledged', 'resolved': 'Resolved',
              'suppressed': 'Paused by maintenance'}
STATE_COLOUR = {'open': '#FF8A8A', 'acknowledged': '#FFC93C', 'suppressed': '#9FB0CC', 'pending': '#9FB0CC',
                'resolved': '#65D782'}
FILTERS = (('active', 'Current problems'), ('open', 'Needs attention'), ('acknowledged', 'Acknowledged'),
           ('suppressed', 'Paused by maintenance'), ('resolved', 'Resolved'), ('', 'All'))
EVENT_TEXT = {'pending': 'Problem started', 'opened': 'Incident opened', 'acknowledged': 'Acknowledged by {who}',
              'returned': 'Problem returned', 'suppressed': 'Paused by maintenance', 'resolved': 'Resolved'}
RESOLUTION_TEXT = {'recovered': 'Recovered', 'not-monitored': 'No longer monitored'}
DELIVERY_TEXT = {'open': 'Incident opened', 'recovery': 'Recovered', 'test': 'Test message', 'summary': 'Daily summary'}
DELIVERY_STATUS = {'queued': 'Waiting to retry', 'sending': 'Sending', 'sent': 'Sent', 'failed': 'Failed'}
ERROR_TEXT = {'connect': 'Could not connect', 'timeout': 'No answer (timeout)', 'tls': 'Secure connection failed',
              'auth': 'Sign-in refused', 'refused': 'Address refused', 'config': 'Settings incomplete',
              'interrupted': 'Interrupted by a server stop', 'smtp': 'Mail server error', 'gone': 'Incident removed'}
KINDS = (('auto', 'Automatic (desktops always, laptops never)'), ('always', 'Always'),
         ('business', 'Business hours'), ('never', 'Never'))
SCOPES = (('default', 'Everyone'), ('group', 'Group'), ('device', 'Computer'))


class AlertsUI:
    """Built into a ServerWindow: ``page`` (Incidents), ``admin(layout)`` sections and the header ``badge``."""

    def __init__(self, window):
        self.w = window
        self.result = None
        self._busy = self._again = False
        self._fresh = dict(rules=True, notifications=True)
        self._selected = None
        self.rule_rows = {}
        self.badge = window.button('Open alerts', self.show)
        self.badge.setObjectName('link')
        self.badge.setStyleSheet('color: #FFC93C; font-weight: 600;')
        self.badge.hide()
        window.chrome_actions.layout().insertWidget(1, self.badge)
        self._make_page()

    def t(self, key, **values):
        return self.w.t(key, **values)

    # ---- Incidents page ----

    def _make_page(self):
        w = self.w
        layout = w._scroll_page('Incidents')
        self.index = w.tabs.count() - 1
        title = w.label('Incidents')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        layout.addWidget(title)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #FF8A8A;')
        self.error.hide()
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.setSpacing(16)
        panel, content = w._panel()
        filters = ControlRow()
        heading = w.label('Problems')
        heading.setObjectName('sectionTitle')
        filters.addWidget(heading, 1)
        self.filter = QComboBox()
        for code, _ in FILTERS:
            self.filter.addItem('', code)
        self.filter.currentIndexChanged.connect(self.load)
        filters.addWidget(self.filter)
        content.addLayout(filters)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName('incidentTable')
        w._style_table(self.table)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(360)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        content.addWidget(self.table, 1)
        self.empty = w.label('No incidents. Lifetime opens one when a rule under Administration finds a problem.')
        self.empty.setObjectName('muted')
        content.addWidget(self.empty)
        row.addWidget(panel, 3)
        detail, detail_layout = w._panel()
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        detail_layout.addWidget(self.detail)
        self.acknowledge_button = w.button('Acknowledge', self.acknowledge)
        self.acknowledge_button.setObjectName('primary')
        self.acknowledge_button.setEnabled(False)
        detail_layout.addWidget(self.acknowledge_button, 0, Qt.AlignmentFlag.AlignRight)
        detail_layout.addStretch()
        row.addWidget(detail, 2)
        layout.addLayout(row)
        layout.addStretch()

    def show(self):
        self.w.tabs.setCurrentIndex(self.index)

    # ---- loading ----

    def load(self, *unused):
        if self.w._closed:
            return
        if self._busy:
            self._again = True
            return
        self._busy = True
        client, state = self.w.client, self.filter.currentData() if self.filter.currentData() is not None else 'active'

        def work():
            return dict(incidents=client.call('incidents', state=state, limit=200), rules=client.call('alert_rules'),
                        schedules=client.call('schedules'), maintenance=client.call('maintenance'),
                        notifications=client.call('notifications'), deliveries=client.call('deliveries', limit=50))
        if not self.w._submit(work, self._loaded, quiet=True):
            self._busy = False

    def _loaded(self, result, success):
        self._busy = False
        self.error.setVisible(not success)
        self.error.setText('' if success else self.t('Alerts are unavailable. Showing the last data received, if any.'))
        if success:
            self.result = result
            self.render()
        if self._again:
            self._again = False
            self.load()

    def _call(self, operation, arguments, button=None, after=None):
        """One change; a refused value (HTTP 400) shows a plain message instead of a connection error."""
        client = self.w.client

        def work():
            try:
                return dict(result=client.call(operation, **arguments))
            except ValueError:
                return dict(refused=True)

        def done(result, success):
            if button is not None:
                button.setEnabled(True)
            if success and not result.get('refused'):
                self.w.status_label.setText(self.t('Changes saved.'))
                if after:
                    after(result['result'])
            else:
                self.w.status_label.setText(self.t('The server did not accept this. Check the values and try again.'))
            self.load()
        if button is not None:
            button.setEnabled(False)
        if not self.w._submit(work, done, quiet=True) and button is not None:
            button.setEnabled(True)

    # ---- rendering ----

    def retranslate(self):
        for index, (_, key) in enumerate(FILTERS):
            self.filter.setItemText(index, self.t(key))
        self.filter.setAccessibleName(self.t('Show'))
        self.table.setHorizontalHeaderLabels([self.t(key) for key in ('Computer', 'Problem', 'State', 'Since', 'Acknowledged')])
        self.table.setAccessibleName(self.t('Incidents'))
        if hasattr(self, 'schedule_table'):
            self._retranslate_admin()
        self.overview(getattr(self, '_summary', None))
        if self.result:
            self.render()
        else:
            self._render_detail(None)

    def overview(self, summary):
        """Header badge and tab title from the counts that come with the overview."""
        self._summary = summary
        active = (summary or {}).get('active') or 0
        self.badge.setText(self.t('{count} open alerts', count=active) if active != 1 else self.t('1 open alert'))
        self.badge.setAccessibleName(self.badge.text())
        self.badge.setVisible(bool(active))
        self.w.tabs.setTabText(self.index, self.t('Incidents') + (f' ({active})' if active else ''))

    def _when(self, stamp):
        return self.w._timestamp(stamp) if stamp else self.t('Not available')

    def _problem(self, incident):
        text = self.t(RULE_TEXT.get(incident['rule'], incident['rule']))
        if incident.get('value') is not None and incident['rule'] in UNIT:
            text += f" · {incident['value']:g}{UNIT[incident['rule']]}"
        return text

    def render(self):
        result = self.result
        incidents = result['incidents']
        self.table.setRowCount(len(incidents))
        self.empty.setVisible(not incidents)
        self.table.setVisible(bool(incidents))
        selected = None
        for index, incident in enumerate(incidents):
            values = (incident['device_name'] or incident['device_id'], self._problem(incident),
                      self.t(STATE_TEXT[incident['state']]), self.w._relative(incident['started_at']),
                      incident.get('acknowledged_by') or '')
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, incident['id'])
                item.setToolTip(self._when(incident['started_at']) if column == 3 else value)
                if column == 2:
                    item.setIcon(status_dot(T(STATE_COLOUR[incident['state']])))
                self.table.setItem(index, column, item)
            if incident['id'] == self._selected:
                selected = index
        if selected is not None:
            self.table.blockSignals(True)
            self.table.selectRow(selected)
            self.table.blockSignals(False)
        self._render_detail(next((i for i in incidents if i['id'] == self._selected), None))
        self._render_admin()

    def _selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        self._selected = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None
        incidents = (self.result or {}).get('incidents') or []
        self._render_detail(next((i for i in incidents if i['id'] == self._selected), None))
        if self._selected is not None:      # the timeline comes with the full incident
            client, identifier = self.w.client, self._selected
            self.w._submit(lambda: client.call('incident', incident_id=identifier),
                           lambda result, ok: self._render_detail(result) if ok and result['id'] == self._selected else None,
                           quiet=True)

    def _render_detail(self, incident):
        if not incident:
            self.detail.setText(html.escape(self.t('Select an incident to see its timeline.')))
            self.acknowledge_button.setEnabled(False)
            return
        e = lambda value: html.escape(str(value))
        rows = [('Computer', incident['device_name'] or incident['device_id']), ('Group', incident.get('group_name') or '—'),
                ('Problem', self._problem(incident)), ('State', self.t(STATE_TEXT[incident['state']])),
                ('Started', self._when(incident['started_at'])), ('Opened', self._when(incident.get('opened_at')))]
        if incident.get('acknowledged_by'):
            rows.append(('Acknowledged', self.t('{who} · {time}', who=incident['acknowledged_by'],
                                                time=self._when(incident.get('acknowledged_at')))))
        if incident['state'] == 'resolved':
            rows.append(('Recovered at', self._when(incident.get('resolved_at'))))
            rows.append(('Outcome', self.t(RESOLUTION_TEXT.get(incident.get('resolution'), 'Resolved'))))
        text = '<table width="100%" cellpadding="4">' + ''.join(
            T('<tr><td style="color:#9FB0CC;">') + e(self.t(key)) + '</td><td>' + e(value) + '</td></tr>' for key, value in rows) + '</table>'
        timeline = incident.get('timeline')
        if timeline:
            text += '<p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Timeline')) + '</p><p>'
            text += '<br>'.join(e(self._when(event['time']) + ' · ' + self.t(EVENT_TEXT.get(event['kind'], 'Unknown'), who=event.get('detail') or ''))
                                for event in timeline) + '</p>'
        for delivery in incident.get('deliveries') or []:
            text += '<p style="margin:2px 0">' + e(self._delivery_line(delivery)) + '</p>'
        self.detail.setText(text)
        self.acknowledge_button.setEnabled(incident['state'] == 'open')

    def _delivery_line(self, delivery):
        line = f"{self.t('Email' if delivery['channel'] == 'email' else 'Webhook')}: {self.t(DELIVERY_TEXT[delivery['kind']])} · {self.t(DELIVERY_STATUS[delivery['status']])}"
        return line + (f" · {self._error(delivery['error'])}" if delivery.get('error') else '')

    def _error(self, code):
        if code and code.startswith('http-'):
            return self.t('Server error {code}', code=code[5:])
        return self.t(ERROR_TEXT.get(code, 'Unknown'))

    def acknowledge(self):
        if self._selected is None:
            return
        try:
            who = getpass.getuser()
        except (OSError, KeyError, ImportError):
            who = ''
        self._call('acknowledge', dict(incident_id=self._selected, by=(who or 'Administrator')[:64]), self.acknowledge_button)

    def device_html(self, device):
        """The device detail's incident history and monitoring state (HTML for its details text)."""
        e = lambda value: html.escape(str(value))
        alerts = device.get('alerts')
        if alerts is None:
            return ''
        text = '<p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Incidents')) + '</p>'
        status, notes = alerts.get('status') or {}, []
        if status.get('paused'):
            notes.append(self.t('Paused by maintenance'))
        if status and not status.get('expected'):
            notes.append(self.t('Not expected online now'))
        if status.get('missing'):
            notes.append(self.t('Telemetry missing: {rules}', rules=', '.join(self.t(RULE_TEXT.get(r, r)) for r in status['missing'])))
        if notes:
            text += '<p style="margin:4px 0">' + e(' · '.join(notes)) + '</p>'
        incidents = alerts.get('incidents') or []
        if not incidents:
            return text + '<p style="margin:4px 0">' + e(self.t('No incidents')) + '</p>'
        return text + '<table width="100%" cellpadding="4">' + ''.join(
            '<tr><td>' + e(self._problem(item)) + '</td><td>' + e(self.t(STATE_TEXT[item['state']])) + '</td><td>'
            + e(self.w._relative(item['started_at'])) + '</td></tr>' for item in incidents) + '</table>'

    # ---- Administration ----

    def admin(self, layout):
        w = self.w
        rules, content = w._section('Alert rules')
        note = w.label('A problem must last the whole duration before an incident opens, and stay away five minutes before it resolves. Rules use only what each computer shares.')
        note.setObjectName('muted')
        content.addWidget(note)
        grid = QWidget()
        self.rule_grid = QVBoxLayout(grid)
        self.rule_grid.setContentsMargins(0, 0, 0, 0)
        content.addWidget(grid)
        self.rules_button = w.button('Save rules', self.save_rules)
        content.addWidget(self.rules_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(rules)

        schedules, content = w._section('Expected online hours')
        note = w.label('Offline alerts count only inside these hours. Automatic expects desktops always and laptops never, so a sleeping laptop raises nothing.')
        note.setObjectName('muted')
        content.addWidget(note)
        self.schedule_table = self._table(2, 160)
        self.schedule_table.itemSelectionChanged.connect(self._schedule_selected)
        content.addWidget(self.schedule_table)
        editor = ControlRow()
        editor.setSpacing(8)
        self.schedule_scope, self.schedule_kind = QComboBox(), QComboBox()
        for code, _ in SCOPES:
            self.schedule_scope.addItem('', code)
        for code, _ in KINDS:
            self.schedule_kind.addItem('', code)
        self.schedule_key = QLineEdit()
        self.schedule_zone = QLineEdit('local')
        self.schedule_zone.setMaximumWidth(140)
        self.schedule_start, self.schedule_end = QTimeEdit(QTime(8, 0)), QTimeEdit(QTime(18, 0))
        for control in (self.schedule_start, self.schedule_end):
            control.setDisplayFormat('HH:mm')
        for control in (self.schedule_scope, self.schedule_key, self.schedule_kind, self.schedule_zone,
                        self.schedule_start, self.schedule_end):
            editor.addWidget(control)
        content.addLayout(editor)
        days = ControlRow()
        self.weekday_boxes = []
        for day in range(7):
            box = QCheckBox()
            box.setChecked(day < 5)
            self.weekday_boxes.append(box)
            days.addWidget(box)
        content.addLayout(days)
        self.schedule_holidays = QLineEdit()
        content.addWidget(self.schedule_holidays)
        actions = QHBoxLayout()
        actions.addStretch()
        self.schedule_delete = w.button('Remove selected', self.delete_schedule)
        self.schedule_save = w.button('Save hours', self.save_schedule)
        actions.addWidget(self.schedule_delete)
        actions.addWidget(self.schedule_save)
        content.addLayout(actions)
        layout.addWidget(schedules)

        windows, content = w._section('Maintenance windows')
        note = w.label('No alert opens during a window. A problem still there when it ends opens then; one that clears is closed quietly.')
        note.setObjectName('muted')
        content.addWidget(note)
        self.maintenance_table = self._table(5, 160)
        self.maintenance_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        content.addWidget(self.maintenance_table)
        add = ControlRow()
        add.setSpacing(8)
        self.window_scope = QComboBox()
        for code in ('all', 'group', 'device'):
            self.window_scope.addItem('', code)
        self.window_key = QLineEdit()
        now = QDateTime.currentDateTime()
        self.window_start, self.window_end = QDateTimeEdit(now), QDateTimeEdit(now.addSecs(7200))
        for control in (self.window_start, self.window_end):
            control.setCalendarPopup(True)
            control.setDisplayFormat('d MMM yyyy HH:mm')
        self.window_note = QLineEdit()
        self.window_note.setMaxLength(200)
        self.window_add = w.button('Add window', self.add_window)
        for control in (self.window_scope, self.window_key, self.window_start, self.window_end, self.window_note, self.window_add):
            add.addWidget(control)
        content.addLayout(add)
        layout.addWidget(windows)

        mail, form = w._form_section('Notifications')
        self.email_enabled = QCheckBox()
        self.smtp_host, self.smtp_port = QLineEdit(), QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(587)
        server_row = QHBoxLayout()
        server_row.addWidget(self.smtp_host, 1)
        server_row.addWidget(self.smtp_port)
        self.security = QComboBox()
        self.security.addItem('STARTTLS', 'starttls')
        self.security.addItem('TLS', 'tls')
        self.username, self.password, self.sender, self.recipients = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.language = QComboBox()
        self.language.addItem('English', 'en')
        self.language.addItem('Tiếng Việt', 'vi')
        self.summary_enabled, self.summary_hour = QCheckBox(), QSpinBox()
        self.summary_hour.setRange(0, 23)
        self.summary_hour.setSuffix(':00')
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary_enabled)
        summary_row.addWidget(self.summary_hour)
        summary_row.addStretch()
        self.webhook_enabled, self.webhook_url = QCheckBox(), QLineEdit()
        self.webhook_url.setEchoMode(QLineEdit.EchoMode.Password)
        webhook_row = QHBoxLayout()
        webhook_row.addWidget(self.webhook_enabled)
        webhook_row.addWidget(self.webhook_url, 1)
        self.notification_labels = []
        for key, field in (('Email alerts', self.email_enabled), ('Mail server and port', server_row),
                           ('Security', self.security), ('User name', self.username), ('Password', self.password),
                           ('Sender address', self.sender), ('Recipients (comma separated)', self.recipients),
                           ('Message language', self.language), ('Daily summary at', summary_row),
                           ('Webhook (https://)', webhook_row)):
            label = w.label(key)
            self.notification_labels.append((label, field))
            form.addRow(label, field)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.test_email = w.button('Send test email', lambda: self.send_test('email'))
        self.test_webhook = w.button('Send test webhook', lambda: self.send_test('webhook'))
        self.notifications_button = w.button('Save notifications', self.save_notifications)
        self.notifications_button.setObjectName('primary')
        for button in (self.test_email, self.test_webhook, self.notifications_button):
            buttons.addWidget(button)
        form.addRow(buttons)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        form.addRow(self.test_result)
        note = w.label('The password and webhook address are stored encrypted for the account that runs the server and are never shown again. Messages name the computer and the problem only.')
        note.setObjectName('muted')
        form.addRow(note)
        heading = w.label('Delivery log')
        heading.setObjectName('sectionTitle')
        form.addRow(heading)
        self.delivery_table = self._table(5, 200)
        self.delivery_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        form.addRow(self.delivery_table)
        layout.addWidget(mail)

    def _table(self, columns, height):
        table = QTableWidget(0, columns)
        self.w._style_table(table)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setFixedHeight(height)
        for column in range(columns):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(columns - 1 if columns == 2 else 0, QHeaderView.ResizeMode.Stretch)
        return table

    def _retranslate_admin(self):
        for index, (_, key) in enumerate(SCOPES):
            self.schedule_scope.setItemText(index, self.t(key))
        for index, (_, key) in enumerate(KINDS):
            self.schedule_kind.setItemText(index, self.t(key))
        for index, key in enumerate(('Everyone', 'Group', 'Computer')):
            self.window_scope.setItemText(index, self.t(key))
        for day, box in enumerate(self.weekday_boxes):
            box.setText(self.t(f'weekday.{day}'))
        for control, key in ((self.schedule_key, 'Group name or computer ID'), (self.schedule_zone, 'Time zone'),
                             (self.schedule_holidays, 'Holidays (YYYY-MM-DD, comma separated)'),
                             (self.window_key, 'Group name or computer ID'), (self.window_note, 'Note')):
            control.setPlaceholderText(self.t(key))
            control.setAccessibleName(self.t(key))
        for control, key in ((self.schedule_scope, 'Applies to'), (self.schedule_kind, 'Hours'),
                             (self.schedule_start, 'Opens at'), (self.schedule_end, 'Closes at'),
                             (self.window_scope, 'Applies to'), (self.window_start, 'From'), (self.window_end, 'Until')):
            control.setAccessibleName(self.t(key))
        for label, field in self.notification_labels:
            if isinstance(field, QWidget):
                field.setAccessibleName(label.text())
        for box, key in ((self.summary_enabled, 'Daily summary'), (self.webhook_enabled, 'Webhook')):
            box.setAccessibleName(self.t(key))
        self.smtp_port.setAccessibleName(self.t('Port'))
        self.summary_hour.setAccessibleName(self.t('Daily summary at'))
        self.webhook_url.setAccessibleName(self.t('Webhook (https://)'))
        self.schedule_table.setHorizontalHeaderLabels([self.t('Applies to'), self.t('Hours')])
        self.maintenance_table.setHorizontalHeaderLabels([self.t(k) for k in ('Applies to', 'From', 'Until', 'Note', '')])
        self.delivery_table.setHorizontalHeaderLabels([self.t(k) for k in ('Time', 'Channel', 'Message', 'Status', 'Problem')])
        for table, key in ((self.schedule_table, 'Expected online hours'), (self.maintenance_table, 'Maintenance windows'),
                           (self.delivery_table, 'Delivery log')):
            table.setAccessibleName(self.t(key))
        for name, (box, threshold, minutes, version, label) in self.rule_rows.items():
            label.setText(self.t(RULE_TEXT[name]))
            box.setAccessibleName(self.t(RULE_TEXT[name]))
            threshold.setAccessibleName(self.t('Threshold') + ': ' + self.t(RULE_TEXT[name]))
            minutes.setAccessibleName(self.t('For (minutes)') + ': ' + self.t(RULE_TEXT[name]))
            minutes.setSuffix(' ' + self.t('min'))
            version.setPlaceholderText(self.t('Minimum version, e.g. 1.2.0'))

    def _render_admin(self):
        result = self.result
        if self._fresh['rules']:
            self._render_rules(result['rules'])
            self._fresh['rules'] = False
        if self._fresh['notifications']:
            self._render_notifications(result['notifications'])
            self._fresh['notifications'] = False
        self.schedule_table.setRowCount(len(result['schedules']))
        for index, schedule in enumerate(result['schedules']):
            for column, value in enumerate((self._scope_text(schedule['scope'], schedule['key']), self._hours_text(schedule))):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, schedule)
                item.setToolTip(value)
                self.schedule_table.setItem(index, column, item)
        self.maintenance_table.setRowCount(len(result['maintenance']))
        for index, window in enumerate(result['maintenance']):
            scope = {'all': 'default'}.get(window['scope'], window['scope'])
            for column, value in enumerate((self._scope_text(scope, window['key']), self._when(window['start_at']),
                                            self._when(window['end_at']), window['note'])):
                self.maintenance_table.setItem(index, column, QTableWidgetItem(value))
            button = QPushButton(self.t('Remove'))
            button.setAccessibleName(self.t('Remove') + ': ' + self._when(window['start_at']))
            button.clicked.connect(lambda checked=False, identifier=window['id']:
                                   self._call('delete_maintenance', dict(window_id=identifier)))
            self.maintenance_table.setCellWidget(index, 4, table_action(button))
        self.delivery_table.setRowCount(len(result['deliveries']))
        for index, delivery in enumerate(result['deliveries']):
            problem = self.t(RULE_TEXT.get(delivery.get('rule'), '')) if delivery.get('rule') else ''
            subject = (delivery.get('device_name') or '') + (' · ' + problem if problem else '')
            status = self.t(DELIVERY_STATUS[delivery['status']]) + (f" · {self._error(delivery['error'])}" if delivery.get('error') else '')
            for column, value in enumerate((self._when(delivery['updated_at']),
                                            self.t('Email' if delivery['channel'] == 'email' else 'Webhook'),
                                            self.t(DELIVERY_TEXT[delivery['kind']]), status, subject)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.delivery_table.setItem(index, column, item)

    def _scope_text(self, scope, key):
        if scope == 'default':
            return self.t('Everyone')
        if scope == 'group':
            return self.t('Group: {name}', name=key)
        return self.t('Computer: {name}', name=self._device_name(key))

    def _device_name(self, device_id):
        for row in getattr(self.w, '_rows', []) or []:
            if row.get('device_id') == device_id:
                return row.get('identity', {}).get('display_name') or device_id
        return device_id

    def _hours_text(self, schedule):
        if schedule['kind'] != 'business':
            return self.t(dict(KINDS)[schedule['kind']])
        days = ' '.join(self.t(f'weekday.{day}') for day in schedule['weekdays'])
        text = self.t('{days} · {start}–{end} ({zone})', days=days, start=schedule['start'], end=schedule['end'],
                      zone=self.t('server time') if schedule['timezone'] == 'local' else schedule['timezone'])
        if schedule['holidays']:
            text += ' · ' + self.t('{count} holidays', count=len(schedule['holidays']))
        return text

    def _render_rules(self, rules):
        for name, row in list(self.rule_rows.items()):
            row[-1].parentWidget().deleteLater()
        self.rule_rows = {}
        for rule in rules:
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 0, 0, 0)
            box = QCheckBox()
            box.setChecked(rule['enabled'])
            label = QLabel()
            label.setWordWrap(True)
            threshold = QDoubleSpinBox()
            threshold.setDecimals(0)
            threshold.setFixedWidth(120)
            low, high = rule.get('range') or (0, 0)
            threshold.setRange(low, high)
            threshold.setSuffix(UNIT.get(rule['rule'], ''))
            if rule.get('threshold') is not None:
                threshold.setValue(rule['threshold'])
            minutes = QSpinBox()
            minutes.setRange(*rule['minutes_range'])
            minutes.setValue(rule['minutes'])
            minutes.setFixedWidth(130)
            version = QLineEdit(rule.get('version') or '')
            version.setMaxLength(27)
            version.setFixedWidth(220)
            layout.addWidget(box)
            layout.addWidget(label, 1)
            for control in (threshold, minutes, version):
                layout.addWidget(control)
            # Only once they have a parent: showing a parentless widget would open it as a window of its own.
            threshold.setVisible('range' in rule)
            version.setVisible('version' in rule)
            minutes.setVisible('version' not in rule)
            self.rule_grid.addWidget(line)
            self.rule_rows[rule['rule']] = (box, threshold, minutes, version, label)
        self._retranslate_admin()

    def save_rules(self):
        current = {rule['rule']: rule for rule in (self.result or {}).get('rules', [])}
        changes = []
        for name, (box, threshold, minutes, version, _) in self.rule_rows.items():
            change = dict(rule=name, enabled=box.isChecked(), minutes=minutes.value())
            if 'range' in current.get(name, {}):
                change['threshold'] = int(threshold.value())
            if 'version' in current.get(name, {}):
                change['version'] = version.text().strip()
            if any(current.get(name, {}).get(key) != value for key, value in change.items() if key != 'rule'):
                changes.append(change)
        client = self.w.client

        def work():
            try:
                for change in changes:
                    client.call('set_alert_rule', **change)
                return {}
            except ValueError:
                return dict(refused=True)
        self._fresh['rules'] = True
        self.rules_button.setEnabled(False)

        def done(result, success):
            self.rules_button.setEnabled(True)
            self.w.status_label.setText(self.t('Changes saved.' if success and not result.get('refused')
                                               else 'The server did not accept this. Check the values and try again.'))
            self.load()
        if not self.w._submit(work, done, quiet=True):
            self.rules_button.setEnabled(True)

    def _schedule_selected(self):
        rows = self.schedule_table.selectionModel().selectedRows()
        if not rows:
            return
        schedule = self.schedule_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self.schedule_scope.setCurrentIndex(self.schedule_scope.findData(schedule['scope']))
        self.schedule_key.setText(schedule['key'])
        self.schedule_kind.setCurrentIndex(self.schedule_kind.findData(schedule['kind']))
        self.schedule_zone.setText(schedule['timezone'])
        self.schedule_start.setTime(QTime.fromString(schedule['start'], 'HH:mm'))
        self.schedule_end.setTime(QTime.fromString(schedule['end'], 'HH:mm'))
        for day, box in enumerate(self.weekday_boxes):
            box.setChecked(day in schedule['weekdays'])
        self.schedule_holidays.setText(', '.join(schedule['holidays']))

    def save_schedule(self):
        scope = self.schedule_scope.currentData()
        self._call('set_schedule', dict(
            scope=scope, key='' if scope == 'default' else self.schedule_key.text().strip(),
            kind=self.schedule_kind.currentData(), timezone=self.schedule_zone.text().strip() or 'local',
            start=self.schedule_start.time().toString('HH:mm'), end=self.schedule_end.time().toString('HH:mm'),
            weekdays=[day for day, box in enumerate(self.weekday_boxes) if box.isChecked()],
            holidays=[part.strip() for part in self.schedule_holidays.text().split(',') if part.strip()]), self.schedule_save)

    def delete_schedule(self):
        rows = self.schedule_table.selectionModel().selectedRows()
        if rows:
            schedule = self.schedule_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            self._call('delete_schedule', dict(scope=schedule['scope'], key=schedule['key']), self.schedule_delete)

    def add_window(self):
        scope = self.window_scope.currentData()
        start = self.window_start.dateTime().toSecsSinceEpoch()
        end = self.window_end.dateTime().toSecsSinceEpoch()
        stamp = lambda seconds: dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()
        self._call('add_maintenance', dict(scope=scope, key='' if scope == 'all' else self.window_key.text().strip(),
                                           start=stamp(start), end=stamp(end), note=self.window_note.text().strip()),
                   self.window_add)

    def _render_notifications(self, settings):
        self.email_enabled.setChecked(settings['email_enabled'])
        self.smtp_host.setText(settings['smtp_host'])
        self.smtp_port.setValue(settings['smtp_port'])
        self.security.setCurrentIndex(self.security.findData(settings['security']))
        self.username.setText(settings['username'])
        self.password.clear()
        self.password.setPlaceholderText(self.t('Saved. Type to replace it.') if settings.get('password_set') else '')
        self.sender.setText(settings['sender'])
        self.recipients.setText(', '.join(settings['recipients']))
        self.language.setCurrentIndex(self.language.findData(settings['language']))
        self.summary_enabled.setChecked(settings['summary_enabled'])
        self.summary_hour.setValue(settings['summary_hour'])
        self.webhook_enabled.setChecked(settings['webhook_enabled'])
        self.webhook_url.clear()
        self.webhook_url.setPlaceholderText(settings.get('webhook') or 'https://')
        self.test_result.setText(self.t('The saved password cannot be read by this server account. Enter it again.')
                                 if settings.get('secrets_unreadable') else '')

    def save_notifications(self):
        arguments = dict(email_enabled=self.email_enabled.isChecked(), smtp_host=self.smtp_host.text().strip(),
                         smtp_port=self.smtp_port.value(), security=self.security.currentData(),
                         username=self.username.text().strip(), sender=self.sender.text().strip(),
                         recipients=[part.strip() for part in self.recipients.text().replace(';', ',').split(',') if part.strip()],
                         language=self.language.currentData(), summary_enabled=self.summary_enabled.isChecked(),
                         summary_hour=self.summary_hour.value(), webhook_enabled=self.webhook_enabled.isChecked())
        if self.password.text():
            arguments['password'] = self.password.text()
        if self.webhook_url.text().strip():
            arguments['webhook_url'] = self.webhook_url.text().strip()

        def saved(result):
            self._fresh['notifications'] = True
        self._call('set_notifications', arguments, self.notifications_button, saved)

    def send_test(self, channel):
        button = self.test_email if channel == 'email' else self.test_webhook

        def sent(result):
            self.test_result.setText(self.t('Test message sent.') if result['status'] == 'sent' else
                                     self.t('Test message failed: {reason}', reason=self._error(result.get('error'))))
        self.test_result.setText(self.t('Sending…'))
        self._call('test_notification', dict(channel=channel), button, sent)
