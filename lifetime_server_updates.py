"""Agent updates under Administration: upload one approved agent package (file, its published SHA-256 and version),
choose the share of computers it is offered to, pause, roll back, and see what computers reported. Built into AlertsUI,
so it shares its loading, saving and translations; Glass/Classic and light/dark follow the window.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QLabel, QLineEdit, QSpinBox, QTableWidgetItem

from lifetime_layout import ControlRow
from lifetime_widgets_dashboard import VISUAL_ROLE

NOTE = ('Offer an approved Lifetime agent package to computers whose owner allowed updates. They download it over '
        'their secure connection, check its SHA-256 (and its signature when it has one) and keep it ready; '
        'install_agent_windows.ps1 -StagedUpdate installs it, for example from a startup script. Start with a small share.')
STATUS_TEXT = {'staged': 'Ready to install', 'installed': 'Installed', 'failed': 'Failed'}
FAILURE_TEXT = {'download': 'download failed', 'size': 'wrong size', 'checksum': 'SHA-256 did not match',
                'signature': 'signature not valid', 'version': 'older version refused', 'disk': 'could not be saved'}
KINDS = {'staged': 'info', 'installed': 'good', 'failed': 'danger'}


class UpdatesUI:
    def __init__(self, alerts):
        self.alerts, self.w = alerts, alerts.w
        self._paused = False

    def t(self, key, **values):
        return self.w.t(key, **values)

    def admin(self, layout):
        w = self.w
        panel, content = w._section('Agent updates')
        w.access_ui.gate(panel, 'upload_update')
        note = w.label(NOTE)
        note.setObjectName('muted')
        content.addWidget(note)
        self.package_label = QLabel()
        self.package_label.setWordWrap(True)
        self.package_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(self.package_label)
        upload, rollout = ControlRow(), ControlRow()
        for row in (upload, rollout):
            row.setSpacing(8)
        self.path, self.version, self.sha256 = QLineEdit(), QLineEdit(), QLineEdit()
        self.version.setMaxLength(27)
        self.sha256.setMaxLength(64)
        self.browse_button = w.button('Choose file', self.browse)
        self.upload_button = w.button('Upload package', self.upload)
        for control in (self.path, self.browse_button, self.version, self.sha256, self.upload_button):
            upload.addWidget(control)
        self.percent = QSpinBox()
        self.percent.setRange(0, 100)
        self.percent.setSuffix(' %')
        self.save_button = w.button('Save share', self.save)
        self.save_button.setObjectName('primary')
        self.pause_button = w.button('Pause', self.pause)
        self.rollback_button = w.button('Roll back', self.rollback)
        rollout.addWidget(w.label('Offered to'))
        for control in (self.percent, self.save_button, self.pause_button, self.rollback_button):
            rollout.addWidget(control)
        content.addLayout(upload)
        content.addLayout(rollout)
        self.counts = QLabel()
        self.counts.setWordWrap(True)
        content.addWidget(self.counts)
        self.table = self.alerts._table(4, 160)
        content.addWidget(self.table)
        layout.addWidget(panel)

    def retranslate(self):
        for control, key in ((self.path, 'Package file'), (self.version, 'Version, e.g. 1.3.0'),
                             (self.sha256, 'Published SHA-256 of the file')):
            control.setPlaceholderText(self.t(key))
            control.setAccessibleName(self.t(key))
        self.percent.setAccessibleName(self.t('Offered to'))
        self.table.setHorizontalHeaderLabels([self.t(k) for k in ('Computer', 'Status', 'Version', 'Reported')])
        self.table.setAccessibleName(self.t('Agent updates'))
        self.render(self.alerts.result or {})

    def render(self, result):
        summary = result.get('updates')
        current = (summary or {}).get('current')
        self._paused = bool((summary or {}).get('paused'))
        if summary is None:
            self.package_label.setText(self.t('Not available'))
        elif current:
            text = self.t('Package {version} · {size} MB · SHA-256 {sha256}', version=current['version'],
                          size=f"{current['size'] / 1048576:.1f}", sha256=current['sha256'])
            self.package_label.setText(text + (' · ' + self.t('rolled back') if summary.get('rollback') else ''))
        else:
            self.package_label.setText(self.t('No package uploaded.'))
        if summary is not None and not self.percent.hasFocus():
            self.percent.setValue(summary.get('percent') or 0)
        self.pause_button.setText(self.t('Resume' if self._paused else 'Pause'))
        for button in (self.save_button, self.pause_button, self.rollback_button):
            button.setEnabled(bool(current))
        counts = (summary or {}).get('counts') or {}
        self.counts.setText('' if not current else self.t(
            'Ready to install: {staged} · Installed: {installed} · Failed: {failed}', **{key: counts.get(key, 0) for key in
                                                                                         ('staged', 'installed', 'failed')})
            + (' · ' + self.t('Paused') if self._paused else ''))
        names = {device['device_id']: device['name'] for device in result.get('devices') or []}
        reports = (summary or {}).get('reports') or []
        self.table.setRowCount(len(reports))
        for index, report in enumerate(reports):
            status = self.t(STATUS_TEXT.get(report['status'], report['status']))
            if report.get('category'):
                status += ' · ' + self.t(FAILURE_TEXT.get(report['category'], report['category']))
            if current and report['sha256'] != current['sha256']:
                status += ' · ' + self.t('earlier package')
            values = (names.get(report['device_id'], report['device_id']), status, report['version'],
                      self.alerts._when(report['at']))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 1:
                    item.setData(VISUAL_ROLE, ('chip', KINDS.get(report['status'], 'neutral')))
                self.table.setItem(index, column, item)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self.w, self.t('Choose file'), '', self.t('Agent packages (*.exe *.zip *.msi)'))
        if path:
            self.path.setText(path)

    def upload(self):
        self.alerts._call('upload_update', dict(path=self.path.text().strip(), version=self.version.text().strip(),
                                                sha256=self.sha256.text().strip()), self.upload_button)

    def save(self):
        self.alerts._call('set_update', dict(percent=self.percent.value()), self.save_button)

    def pause(self):
        self.alerts._call('set_update', dict(paused=not self._paused), self.pause_button)

    def rollback(self):
        if self.w._confirm('Roll back the agent update? Computers are offered the previous package again, or nothing '
                           'if there is none, and drop a downloaded copy they have not installed.'):
            self.alerts._call('rollback_update', {}, self.rollback_button)
