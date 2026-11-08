"""Optional company panel. Opening it reads redacted local status only."""
import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFileDialog, QLabel, QPlainTextEdit, QHBoxLayout, QWidget

from lifetime_tasks import Tasks
from lifetime_technician import ScrollDialog
from lifetime_settings import read_settings


def read_status(directory=None):
    from lifetime_agent import machine_directory, status_path
    root = Path(directory) if directory is not None else machine_directory()
    # Compatibility with private status until the public sidecar is written.
    candidates = tuple(dict.fromkeys((status_path(root), root / 'status.json')))
    for path in candidates:
        try:
            with path.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('Status file too large')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid status file')
            return {key: value.get(key) for key in ('enabled', 'server', 'status', 'last_sync', 'pending', 'dropped')}
        except FileNotFoundError:
            continue
    return dict(enabled=False, server=None, status='disabled', last_sync=None, pending=None, dropped=None)


class CompanyDialog(ScrollDialog):
    def __init__(self, parent=None, directory=None, language=None, machine_root=None):
        super().__init__(parent, directory, language)
        self.machine_root = machine_root
        self.setWindowTitle(self.t('Company server'))
        self.note('Company reporting is optional. Standalone Lifetime sends no company-server requests.')
        self.note('Pairing sends awake-time totals to the selected server. History and system information are optional. No employee account is required.')
        self.summary = self.note('Reading local status…')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.bundle = QPlainTextEdit()
        self.bundle.setAccessibleName(self.t('Enrollment bundle'))
        self.bundle.setPlaceholderText(self.t('Paste an enrollment bundle or open its file.'))
        self.bundle.setMaximumHeight(90)
        self.form.addWidget(self.bundle)
        self.load_button = self.button('Open enrollment file', self.open_bundle)
        self.history = self.choice('Send dated awake-time history')
        self.resources = self.choice('Send basic system information')
        self.identity = self.choice('Include saved device identity when pairing')
        self.consent = self.choice('Enable reporting to this company server')
        self.trust = self.note('Review the server address and fingerprint before pairing.')
        self.trust.setTextFormat(Qt.TextFormat.PlainText)
        self.bundle.textChanged.connect(self.review_bundle)
        self.consent.toggled.connect(self.update_pair)
        self.pair = self.button('Pair computer', self.enroll)
        self.check = self.button('Check enrollment', self.check_enrollment)
        self.refresh_button = self.button('Refresh local status', self.refresh_status)
        self.stop = self.button('Stop company reporting', self.stop_reporting)
        self.button('Close', self.reject)
        self.note('Pairing and reporting changes require a technician with administrator rights. Install the machine-agent startup task for background recording and reporting.')
        self.note('Stopping reporting prevents future requests. Data already stored on the server must be managed by its administrator or retention policy.')
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        self._busy = False
        self._valid = False
        self.update_pair()
        self.refresh_status()

    def choice(self, title):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        checkbox = QCheckBox()
        checkbox.setAccessibleName(self.t(title))
        text = QLabel(self.t(title))
        text.setWordWrap(True)
        text.setBuddy(checkbox)
        layout.addWidget(checkbox)
        layout.addWidget(text, 1)
        self.form.addWidget(row)
        return checkbox

    def update_pair(self):
        self.pair.setEnabled(not self._busy and self._valid and self.consent.isChecked())

    def review_bundle(self):
        from lifetime_tls import decode_bundle
        try:
            value = decode_bundle(self.bundle.toPlainText().strip())
            self.trust.setText(self.t('Server: {server}\nCertificate SHA-256: {fingerprint}',
                                     server=value['address'], fingerprint=value['fingerprint']))
            self._valid = True
        except ValueError:
            self._valid = False
            self.trust.setText(self.t('Enter a valid enrollment bundle to review the server.'))
        self.update_pair()

    def submit(self, function):
        if self._busy:
            return
        self._busy = True
        for control in (self.check, self.stop, self.refresh_button, self.load_button):
            control.setEnabled(False)
        self.update_pair()
        self.tasks.submit(function)

    def completed(self, number, result):
        self.ready()
        if isinstance(result, str):
            self.bundle.setPlainText(result)
            return
        if not isinstance(result, dict):
            return
        fields = [('Reporting enabled', self.t('Yes') if result.get('enabled') else self.t('No')),
                  ('Server address', result.get('server')), ('Reporting status', self.t(result.get('status') or 'Not available')),
                  ('Last successful sync', result.get('last_sync')), ('Pending records', result.get('pending')),
                  ('Discarded records', result.get('dropped'))]
        self.summary.setText('\n'.join(self.t(title) + ': ' + (self.t('Not available') if value is None else str(value)) for title, value in fields))

    def failed(self, number, error):
        self.ready()
        self.summary.setText(self.t('Company reporting could not be updated. Check administrator access, the server address and its connection, then try again.'))

    def ready(self):
        self._busy = False
        for control in (self.check, self.stop, self.refresh_button, self.load_button):
            control.setEnabled(True)
        self.update_pair()

    def refresh_status(self):
        self.submit(lambda: read_status(self.machine_root))

    def open_bundle(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Open enrollment file'), '', self.t('All files (*)'))
        if path:
            def read():
                with Path(path).open('rb') as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise ValueError('Enrollment file too large')
                return raw.decode('utf-8-sig').strip()
            self.submit(read)

    def enroll(self):
        if not self._valid or not self.consent.isChecked():
            return
        bundle = self.bundle.toPlainText().strip()
        identity = read_settings(self.directory).get('identity', {}) if self.identity.isChecked() else {}
        # Do not leak unknown local extension fields into the wire contract.
        from lifetime_identity import FIELDS
        identity = {key: identity[key] for key in FIELDS if key in identity}
        policy = dict(history=self.history.isChecked(), resources=self.resources.isChecked())
        def pair():
            from lifetime_agent import MachineAgent, machine_directory, DATABASE
            from lifetime_client import SyncClient
            root = Path(self.machine_root) if self.machine_root is not None else machine_directory()
            agent = MachineAgent(root) if (root / DATABASE).exists() else MachineAgent.initialize(root)
            try:
                return SyncClient(agent).start_enrollment(bundle, identity, policy)
            finally:
                agent.close()
        self.submit(pair)

    def check_enrollment(self):
        def check():
            from lifetime_agent import MachineAgent
            from lifetime_client import SyncClient
            agent = MachineAgent(self.machine_root)
            try:
                return SyncClient(agent).step(force=True)
            finally:
                agent.close()
        self.submit(check)

    def stop_reporting(self):
        def stop():
            from lifetime_agent import MachineAgent
            agent = MachineAgent(self.machine_root)
            try:
                agent.disable_reporting()
                return agent.status()
            finally:
                agent.close()
        self.submit(stop)
