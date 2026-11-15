"""Who is signed in and what that role may do, Roles and the Audit trail under Administration, and the messages for
archive backups, for the Lifetime Server dashboard.

The server decides (every request is checked there); this only disables what the role cannot do and says why.
Everything goes through the window's AdminClient in background Tasks and reuses its panels, tables and translations,
so Glass/Classic, light/dark and English/Vietnamese follow the window.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QTableWidget, QTableWidgetItem, QWidget)

import lifetime_access as access

ROLE_TEXT = {'admin': 'Admin', 'operator': 'Operator', 'auditor': 'Auditor', 'viewer': 'Viewer', 'none': 'No role'}
BREAK_TEXT = {'edited': 'an entry was changed', 'missing': 'an entry was removed', 'link': 'entries were reordered',
              'truncated': 'the newest entries were removed'}
PROBLEM_TEXT = {
    'checksum-missing': 'The .sha256 file is not beside the archive.',
    'checksum': 'The archive does not match its .sha256 file.',
    'archive': 'This is not a Lifetime Server backup archive.',
    'missing': 'Files are missing from the archive.',
    'manifest': 'The list of files in the archive is invalid.',
    'newer': 'The archive was made by a newer Lifetime Server.',
    'damaged': 'A file in the archive is damaged.',
    'database': 'A database in the archive cannot be restored by this server.',
    'audit': 'The audit trail in the archive does not verify.',
    'identity': 'The key in the archive does not match its certificate.'}
POWER = {'sleep', 'restart', 'shutdown', 'wake'}


class AccessUI:
    def __init__(self, window):
        self.w = window
        self.me = None          # whoami; None until known (an older server has none: the server still decides)
        self.roles, self.rows = [], []
        self._busy = False
        self._gates = []

    def t(self, key, **values):
        return self.w.t(key, **values)

    # ---- what the signed-in role may do ----

    def can(self, operation):
        return self.me is None or operation in self.me.get('operations', ())

    def role_text(self, role):
        return ', '.join(self.t(ROLE_TEXT.get(part, part)) for part in (role or 'none').split(','))

    def reason(self):
        return self.t('Your role ({role}) cannot do this.', role=self.role_text((self.me or {}).get('role')))

    def gate(self, widget, operation):
        self._gates.append((widget, operation))
        return widget

    def power_reason(self, action):
        operation = 'wake' if action == 'wake' else 'refresh' if action == 'refresh' else 'send_command'
        if not self.can(operation):
            return self.reason()
        if action in POWER and self.me and self.me.get('channel') == 'token':
            return self.t('Remote power needs your own Windows account. The shared administrator token cannot be used for it.')
        return ''

    # ---- Administration sections ----

    def roles_section(self, layout):
        w = self.w
        panel, content = w._section('Roles and access')
        self.signed_in = QLabel()
        self.signed_in.setWordWrap(True)
        self.signed_in.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(self.signed_in)
        note = w.label('Administrators (running as administrator) and SYSTEM are always Admin. Give other Windows accounts '
                       'or groups a role: Operator (acknowledge incidents, maintenance windows, refresh, and remote power '
                       'for one computer where its owner allowed it), Auditor (view and audit trail) or Viewer (view only). '
                       'Anyone who can read the administrator token file is also Admin.')
        note.setObjectName('muted')
        content.addWidget(note)
        self.role_table = self._table(4, 180)
        self.role_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        content.addWidget(self.role_table)
        self.roles_empty = w.label('No other account or group has a role yet.')     # not a blank table
        self.roles_empty.setObjectName('muted')
        self.roles_empty.hide()
        content.addWidget(self.roles_empty)
        self.role_editor = QWidget()
        editor = QHBoxLayout(self.role_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        self.principal = QLineEdit()
        self.principal.setMaxLength(256)
        self.role_choice = QComboBox()
        for role in ('operator', 'auditor', 'viewer', 'admin'):
            self.role_choice.addItem('', role)
        self.role_add = w.button('Give role', self.add_role)
        self.role_remove = w.button('Remove selected', self.remove_role)
        editor.addWidget(self.principal, 1)
        editor.addWidget(self.role_choice)
        editor.addWidget(self.role_add)
        editor.addWidget(self.role_remove)
        content.addWidget(self.gate(self.role_editor, 'set_role'))
        self.roles_panel = panel
        layout.addWidget(panel)

    def audit_section(self, layout):
        w = self.w
        panel, content = w._section('Audit trail')
        note = w.label('Every change and every remote command, with the Windows account that did it. Each entry carries the '
                       'previous entry\'s hash, so a changed or removed entry is detected. A local administrator can still '
                       'edit the file, so keep exported copies somewhere else.')
        note.setObjectName('muted')
        content.addWidget(note)
        self.audit_table = self._table(6, 260)
        self.audit_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        content.addWidget(self.audit_table)
        buttons = QHBoxLayout()
        self.audit_result = QLabel()
        self.audit_result.setWordWrap(True)
        buttons.addWidget(self.audit_result, 1)
        self.verify_button = w.button('Verify audit trail', self.verify)
        self.export_button = w.button('Export CSV', self.export)
        buttons.addWidget(self.verify_button)
        buttons.addWidget(self.export_button)
        content.addLayout(buttons)
        self.audit_panel = panel
        layout.addWidget(panel)
        self.retranslate()

    def _table(self, columns, height):
        table = QTableWidget(0, columns)
        self.w._style_table(table)
        table.setFixedHeight(height)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for column in range(columns):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def retranslate(self):
        if not hasattr(self, 'role_table'):
            return
        self.role_table.setHorizontalHeaderLabels([self.t(k) for k in ('Account or group', 'Kind', 'Role', 'Granted by')])
        self.audit_table.setHorizontalHeaderLabels([self.t(k) for k in ('Time', 'Account', 'Role', 'Action', 'Target', 'Result')])
        for index in range(self.role_choice.count()):
            self.role_choice.setItemText(index, self.t(ROLE_TEXT[self.role_choice.itemData(index)]))
        self.principal.setPlaceholderText(self.t('Windows account or group, e.g. SCHOOL\\helpdesk'))
        self.principal.setAccessibleName(self.t('Windows account or group'))
        self.role_choice.setAccessibleName(self.t('Role'))
        self.apply()

    # ---- loading ----

    def load(self):
        if self._busy or self.w._closed:
            return
        self._busy = True
        client = self.w.client

        def work():
            me = client.call('whoami')
            allowed = set(me.get('operations', ()))
            return dict(me=me, roles=client.call('roles') if 'roles' in allowed else [],
                        audit=client.call('audit', limit=100) if 'audit' in allowed else [])
        if not self.w._submit(work, self._loaded, quiet=True):
            self._busy = False

    def _loaded(self, result, success):
        self._busy = False
        if success:
            self.me, self.roles, self.rows = result['me'], result['roles'], result['audit']
            self.apply()

    def apply(self):
        """Enable only what the role may do, with the reason on anything disabled."""
        for widget, operation in self._gates:
            allowed = self.can(operation)
            widget.setEnabled(allowed)
            widget.setToolTip('' if allowed else self.reason())
        if not hasattr(self, 'audit_panel'):
            return
        me = self.me
        if me:
            self.signed_in.setText(self.t('Signed in as {name} · {role}', name=me.get('name') or '?',
                                          role=self.role_text(me.get('role'))) + (
                '' if me.get('channel') == 'pipe' else ' · ' + self.t('administrator token')))
        self.signed_in.setVisible(bool(me))
        self.role_table.setVisible(bool(me) and self.can('roles'))
        self.audit_panel.setVisible(bool(me) and self.can('audit'))
        self.roles_panel.setVisible(bool(me))
        self._render()
        if self.w._detail is not None or hasattr(self.w, 'approve_button'):
            self.w._set_device_actions(bool(self.w._detail))
        if self.w.alerts.result:
            self.w.alerts.render()

    def _render(self):
        self.role_table.setRowCount(len(self.roles))
        for index, row in enumerate(self.roles):
            for column, value in enumerate((row['name'], self.t('Group' if row['kind'] == 'group' else 'Account'),
                                            self.t(ROLE_TEXT[row['role']]), row['granted_by'])):
                item = QTableWidgetItem(value)
                item.setToolTip(row['sid'] if column == 0 else value)
                item.setData(Qt.ItemDataRole.UserRole, row['sid'])
                self.role_table.setItem(index, column, item)
        self.role_table.setVisible(bool(self.roles))
        self.roles_empty.setVisible(not self.roles)
        self.audit_table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            for column, value in enumerate((self.w._timestamp(row['at']), row['actor_name'], row['role'], row['action'],
                                            row['target'], row['result'])):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.audit_table.setItem(index, column, item)

    # ---- actions ----

    def _call(self, operation, arguments, done, button=None):
        client = self.w.client
        self.w._submit(lambda: client.call(operation, **arguments), lambda result, ok: done(result) if ok else None, button)

    def add_role(self):
        principal = self.principal.text().strip()
        if not principal:
            return
        def done(result):
            self.roles = result
            self.principal.clear()
            self.w.status_label.setText(self.t('Changes saved.'))
            self._render()
        self._call('set_role', dict(principal=principal, role=self.role_choice.currentData()), done, self.role_add)

    def remove_role(self):
        item = self.role_table.item(self.role_table.currentRow(), 0)
        if item is None or not self.w._confirm('Remove this role? The account loses access at its next request.'):
            return
        def done(result):
            self.roles = result
            self._render()
        self._call('delete_role', dict(sid=item.data(Qt.ItemDataRole.UserRole)), done, self.role_remove)

    def verify(self):
        def done(result):
            if result.get('ok'):
                self.audit_result.setText(self.t('The audit trail is intact ({count} entries checked).', count=result['checked']))
            else:
                self.audit_result.setText(self.t('The audit trail is broken at entry {seq}: {reason}.', seq=result['first_broken'],
                                                 reason=self.t(BREAK_TEXT.get(result.get('reason'), 'an entry was changed'))))
        self._call('verify_audit', {}, done, self.verify_button)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self.w, self.t('Export CSV'), 'lifetime-audit.csv', self.t('CSV files (*.csv)'))
        if not path:
            return
        if not path.lower().endswith('.csv'):
            path += '.csv'
        client = self.w.client

        def work():
            rows, before = [], None
            while True:
                page = client.call('audit', limit=1000, **({'before': before} if before else {}))
                rows += page
                if len(page) < 1000:
                    break
                before = page[-1]['seq']
            access.write_csv(rows, path)
            return len(rows)
        self.w._submit(work, lambda count, ok: self.w.status_label.setText(
            self.t('Exported {count} audit entries.', count=count)) if ok else None, self.export_button)

    # ---- backup messages ----

    def backup_message(self, operation, result):
        if not isinstance(result, dict):
            return None
        if result.get('ok') is False:
            return self.t(PROBLEM_TEXT.get(result.get('problem'), 'This is not a Lifetime Server backup archive.'))
        if operation == 'backup_archive':
            return self.t('Backup saved with its .sha256 file beside it. Keep both together, where only administrators can read them: the archive holds the server key.')
        if operation == 'verify_backup':
            made = result.get('created_at') or ''
            with_date = made[:16].replace('T', ' ') if made else '?'
            return self.t('The backup is complete and intact (made {time} UTC).', time=with_date)
        if operation == 'stage_restore':
            return self.t('The backup is checked and will be restored the next time the server starts. Restart the server to restore it.')
        return None

