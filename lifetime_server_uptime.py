"""Uptime and downtime on the Lifetime Server dashboard (1.3): the shared date range, a computer's uptime and
downtime, the fleet uptime bars and "Keep detailed records for".

Built into a ServerWindow (like InsightsUI), so lifetime_server_gui.py only places these pieces. All I/O runs in the
window's Tasks; panels, buttons and translations are the window's, so Glass/Classic, light/dark and English/Vietnamese
follow it. Missing data is shown as unknown (grey), never as zero.
"""
from PyQt6.QtWidgets import QHBoxLayout, QSpinBox
from lifetime_i18n_qt import PlainLabel as QLabel

RETENTION_NOTE = ('Online and offline history, incident timelines and sent alerts older than this are removed '
                  'automatically. Daily awake and off-time totals and incident summaries are kept until you delete '
                  'records before a date.')


class UptimeUI:
    def __init__(self, window):
        self.w = window
        self._retention_loaded = False

    def t(self, key, **values):
        return self.w.t(key, **values)

    # ---- Administration: "Keep detailed records for" ----

    def retention_rows(self, form):
        w = self.w
        self.detail_days = QSpinBox()
        self.detail_days.setRange(30, 3660)
        self.detail_days.setValue(400)
        self.detail_days.setMaximumWidth(150)
        self.detail_days.setEnabled(False)
        self.detail_save = w.button('Save', self.save_retention)
        self.detail_save.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.detail_days)
        row.addWidget(self.detail_save)
        row.addStretch()
        title = w.label('Keep detailed records for')
        title.setBuddy(self.detail_days)
        form.addRow(title, row)
        note = w.label(RETENTION_NOTE)
        note.setObjectName('muted')
        form.addRow(note)
        self.retranslate()

    def load_retention(self):
        client = self.w.client
        self.w._submit(lambda: client.call('detail_retention'), self._retention_finished, quiet=True)

    def _retention_finished(self, result, success):
        if success and isinstance(result, dict) and type(result.get('days')) is int:
            self.detail_days.setValue(result['days'])
            self._retention_loaded = True
        self.detail_days.setEnabled(self._retention_loaded)
        self.detail_save.setEnabled(self._retention_loaded)

    def save_retention(self):
        client, days = self.w.client, self.detail_days.value()

        def done(result, success):
            self.detail_save.setEnabled(True)
            self.w.status_label.setText(self.t('Changes saved.' if success else
                                               'The server did not accept this. Check the values and try again.'))
        self.w._submit(lambda: client.call('set_detail_retention', days=days), done, self.detail_save, quiet=True)

    def retranslate(self):
        if hasattr(self, 'detail_days'):
            self.detail_days.setSuffix(' ' + self.t('days'))
            self.detail_days.setAccessibleName(self.t('Keep detailed records for'))
