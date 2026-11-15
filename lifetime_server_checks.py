"""Network checks under Administration: add or edit ping, TCP and web checks, the allowed networks, and a status list
with each check's last result and latency. Built into AlertsUI, so it shares its loading, saving and translations;
Glass/Classic and light/dark follow the window.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QSpinBox, QTableWidgetItem)

from lifetime_layout import ControlRow
from lifetime_widgets_dashboard import VISUAL_ROLE, EmptyState

KINDS = (('ping', 'Ping'), ('tcp', 'TCP port'), ('http', 'Web page (HTTP or HTTPS)'))
RESULT_TEXT = {'ok': 'Up', 'dns': 'Name not found', 'blocked': 'Blocked by the allowed networks',
               'refused': 'Connection refused', 'timeout': 'No answer (timeout)', 'unreachable': 'No reply',
               'tls': 'Secure connection failed', 'status': 'Unexpected status {detail}', 'oversize': 'Response too large',
               'keyword': 'Keyword not found', 'error': 'Check failed', None: 'Not checked yet'}
NOTE = ('Watch printers, routers, NAS and websites that do not run Lifetime. A check that fails as many times in a '
        'row as the Network check failing rule says opens one incident.')
SCOPE_NOTE = ('Checks reach only these networks: private (LAN addresses), public (internet addresses) or ranges such '
              'as 192.168.10.0/24. Loopback and link-local addresses need an explicit range; cloud metadata addresses '
              'and this server\'s admin port are always blocked.')


class ChecksUI:
    def __init__(self, alerts):
        self.alerts, self.w = alerts, alerts.w
        self._selected = None
        self._rows = []

    def t(self, key, **values):
        return self.w.t(key, **values)

    def admin(self, layout):
        w = self.w
        panel, content = w._section('Network checks')
        w.access_ui.gate(panel, 'save_check')
        note = w.label(NOTE)
        note.setObjectName('muted')
        content.addWidget(note)
        self.table = self.alerts._table(5, 200)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._picked)
        content.addWidget(self.table)
        self.empty = EmptyState('setup')
        self.empty.hide()
        content.addWidget(self.empty)
        first, second, third = ControlRow(), ControlRow(), ControlRow()
        for row in (first, second, third):
            row.setSpacing(8)
        self.name, self.kind, self.target = QLineEdit(), QComboBox(), QLineEdit()
        self.name.setMaxLength(80)
        for code, _ in KINDS:
            self.kind.addItem('', code)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(443)
        for control in (self.name, self.kind, self.target, self.port):
            first.addWidget(control)
        self.method = QComboBox()
        self.method.addItems(['GET', 'HEAD'])
        self.status_min, self.status_max = QSpinBox(), QSpinBox()
        for control, value in ((self.status_min, 200), (self.status_max, 399)):
            control.setRange(100, 599)
            control.setValue(value)
        self.keyword, self.verify = QLineEdit(), QCheckBox()
        self.keyword.setMaxLength(100)
        self.verify.setChecked(True)
        for control in (self.method, self.status_min, self.status_max, self.keyword, self.verify):
            second.addWidget(control)
        self.interval, self.timeout = QSpinBox(), QSpinBox()
        self.interval.setRange(30, 86400)
        self.interval.setValue(60)
        self.timeout.setRange(1, 30)
        self.timeout.setValue(5)
        self.group, self.enabled = QLineEdit(), QCheckBox()
        self.group.setMaxLength(128)
        self.enabled.setChecked(True)
        for control in (self.interval, self.timeout, self.group, self.enabled):
            third.addWidget(control)
        for row in (first, second, third):
            content.addLayout(row)
        actions = QHBoxLayout()
        actions.addStretch()
        self.new_button = w.button('New check', self.new)
        self.remove_button = w.button('Remove selected', self.remove)
        self.save_button = w.button('Save check', self.save)
        self.save_button.setObjectName('primary')
        for button in (self.new_button, self.remove_button, self.save_button):
            actions.addWidget(button)
        content.addLayout(actions)
        scopes = ControlRow()
        scopes.setSpacing(8)
        self.scopes = QLineEdit('private')
        self.scopes_button = w.button('Save networks', self.save_scopes)
        scopes.addWidget(w.label('Allowed networks'))
        scopes.addWidget(self.scopes, 1)
        scopes.addWidget(self.scopes_button)
        content.addLayout(scopes)
        note = w.label(SCOPE_NOTE)
        note.setObjectName('muted')
        content.addWidget(note)
        layout.addWidget(panel)
        self._kind_changed()

    def _kind_changed(self, *unused):
        kind = self.kind.currentData()
        web = kind == 'http'
        for control in (self.method, self.status_min, self.status_max, self.keyword, self.verify):
            control.setVisible(web)
        self.port.setVisible(kind == 'tcp')
        self.target.setPlaceholderText(self.t('https://intranet.school.local/' if web else 'Name or address, e.g. printer.local'))

    def retranslate(self):
        for index, (_, key) in enumerate(KINDS):
            self.kind.setItemText(index, self.t(key))
        self.table.setHorizontalHeaderLabels([self.t(k) for k in ('Name', 'Target', 'Result', 'Latency', 'Last checked')])
        self.table.setAccessibleName(self.t('Network checks'))
        for control, key in ((self.name, 'Name'), (self.keyword, 'Keyword (optional)'), (self.group, 'Group (optional)'),
                             (self.scopes, 'Allowed networks')):
            control.setPlaceholderText(self.t(key))
            control.setAccessibleName(self.t(key))
        for control, key in ((self.kind, 'Kind'), (self.target, 'Target'), (self.port, 'Port'), (self.method, 'Method'),
                             (self.status_min, 'Expected status from'), (self.status_max, 'Expected status to'),
                             (self.interval, 'Every'), (self.timeout, 'Timeout')):
            control.setAccessibleName(self.t(key))
        self.status_min.setPrefix(self.t('Status') + ' ')
        self.status_max.setPrefix('– ')
        self.interval.setPrefix(self.t('Every') + ' ')
        self.interval.setSuffix(' s')
        self.timeout.setPrefix(self.t('Timeout') + ' ')
        self.timeout.setSuffix(' s')
        self.verify.setText(self.t('Verify certificate'))
        self.enabled.setText(self.t('On'))
        self._kind_changed()
        self.render(self.alerts.result or {})

    def _result(self, check):
        text = self.t(RESULT_TEXT.get(check['last_category'], 'Check failed'), detail=check.get('last_detail') or '')
        if check['last_category'] not in (None, 'ok') and check.get('failures'):
            text += ' · ' + self.t('{count} in a row', count=check['failures'])
        return text

    def render(self, result):
        rows = result.get('checks') or []
        self._rows = rows
        if not self.scopes.hasFocus() and result.get('check_scopes') is not None:
            self.scopes.setText(', '.join(result['check_scopes']))
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.set_state('setup', self.t('No network checks yet'),
                             self.t('Fill in a name and a printer, router or website below, then choose Save check.'))
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for index, check in enumerate(rows):
            target = check.get('url') or check['host'] + (f":{check['port']}" if check['kind'] == 'tcp' else '')
            latency = f"{check['last_latency_ms']:.0f} ms" if check.get('last_latency_ms') is not None else '—'
            values = (check['name'] + ('' if check['enabled'] else ' · ' + self.t('Off')), target, self._result(check),
                      latency, self.alerts._when(check['last_at']) if check['last_at'] else self.t('Not checked yet'))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, check['id'])
                if column == 2:
                    kind = 'neutral' if not check['enabled'] or check['last_category'] is None else (
                        'good' if check['last_category'] == 'ok' else 'danger')
                    item.setData(VISUAL_ROLE, ('chip', kind))
                self.table.setItem(index, column, item)
            if check['id'] == self._selected:
                self.table.selectRow(index)
        self.table.blockSignals(False)

    def _picked(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        identifier = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        check = next((item for item in self._rows if item['id'] == identifier), None)
        if not check:
            return
        self._selected = identifier
        self.name.setText(check['name'])
        self.kind.setCurrentIndex(self.kind.findData(check['kind']))
        self.target.setText(check.get('url') or check.get('host', ''))
        self.port.setValue(check.get('port') or 443)
        self.method.setCurrentText(check.get('method', 'GET'))
        self.status_min.setValue(check.get('status_min', 200))
        self.status_max.setValue(check.get('status_max', 399))
        self.keyword.setText(check.get('keyword', ''))
        self.verify.setChecked(check.get('verify_tls', True))
        self.interval.setValue(check['interval'])
        self.timeout.setValue(check['timeout'])
        self.group.setText(check.get('group', ''))
        self.enabled.setChecked(check['enabled'])

    def new(self):
        self._selected = None
        self.table.clearSelection()
        for control in (self.name, self.target, self.keyword, self.group):
            control.clear()
        self.enabled.setChecked(True)

    def check(self):
        kind = self.kind.currentData()
        value = dict(name=self.name.text().strip(), kind=kind, interval=self.interval.value(), timeout=self.timeout.value(),
                     group=self.group.text().strip(), enabled=self.enabled.isChecked())
        if kind == 'http':
            value.update(url=self.target.text().strip(), method=self.method.currentText(), status_min=self.status_min.value(),
                         status_max=self.status_max.value(), keyword=self.keyword.text().strip(),
                         verify_tls=self.verify.isChecked())
        else:
            value['host'] = self.target.text().strip()
            if kind == 'tcp':
                value['port'] = self.port.value()
        return value

    def save(self):
        arguments = dict(check=self.check())
        if self._selected is not None:
            arguments['check_id'] = self._selected
        self.alerts._call('save_check', arguments, self.save_button)

    def remove(self):
        if self._selected is not None:
            self.alerts._call('delete_check', dict(check_id=self._selected), self.remove_button)
            self.new()

    def save_scopes(self):
        scopes = [part.strip() for part in self.scopes.text().replace(';', ',').split(',') if part.strip()]
        self.alerts._call('set_check_scopes', dict(scopes=scopes), self.scopes_button)
