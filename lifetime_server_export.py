"""Asynchronous server report preview/save using the shared native export form."""
import copy
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lifetime_reports import ensure_report_font, write_csv, write_pdf
from lifetime_server_reports import device_report, fleet_report
from lifetime_tasks import Tasks
from lifetime_technician import ExportDialog, iso_date


class ServerExportDialog(ExportDialog):
    def __init__(self, client, device_ids=None, parent=None, language='en'):
        self.client = client
        self.device_ids = tuple(device_ids) if device_ids is not None else None
        self._generation = 0
        self._busy = False
        self._jobs = {}
        super().__init__(parent, directory=client.directory, language=language,
                         permissions=dict(completed=True, history=True, resources=True))
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        self.note('Server exports use current reporting permissions. Optional system fields remain excluded unless selected and permitted by the device.')

    def options(self):
        start = end = None
        if self.range_select.currentData():
            start, end = iso_date(self.start_date.text().strip()), iso_date(self.end_date.text().strip())
            if not start or not end or start > end:
                raise ValueError('Start date must be on or before end date.')
        return dict(start=start, end=end, language=self.language,
                    selected_identity=tuple(key for key, check in self.identity_checks.items() if check.isChecked()),
                    system_fields=tuple(key for key, check in self.system_checks.items() if check.isChecked()))

    def build(self, options):
        ids = self.device_ids
        if ids is None:
            ids = []
            offset = 0
            while True:
                page = self.client.call('devices', limit=25, offset=offset)
                ids.extend(item['device_id'] for item in page)
                if len(page) < 25:
                    break
                offset += 25
                if offset >= 10000:
                    raise ValueError('Fleet selection exceeds the supported limit')
        devices = [self.client.call('device', device_id=identifier, limit=1) for identifier in ids]
        if self.device_ids is not None and len(devices) == 1:
            return device_report(devices[0], **options)
        return fleet_report(devices, **options)

    def invalidate_preview(self, *unused):
        self._generation += 1
        super().invalidate_preview(*unused)

    def submit(self, operation, function):
        self._busy = True
        self.save_pdf.setEnabled(False)
        self.save_csv.setEnabled(False)
        number = self.tasks.submit(function)
        self._jobs[number] = (self._generation, operation)

    def refresh_preview(self):
        if self._busy:
            return
        try:
            options = self.options()
            self.submit('preview', lambda: self.build(options))
            self.result.setText(self.t('Preparing report…'))
        except ValueError as error:
            self.error('Could not build report', error)

    def completed(self, number, value):
        generation, operation = self._jobs.pop(number, (-1, ''))
        self._busy = False
        if generation != self._generation:
            return
        if operation == 'preview':
            self.show_report(value)
            self.result.setText(self.t('Preview ready. Choose PDF or CSV to save.'))
        elif value[0] == 'changed':
            self.show_report(value[1])
            self.result.setText(self.t('Saved data or permissions changed. Review the updated preview, then save again.'))
        else:
            self.save_pdf.setEnabled(True)
            self.save_csv.setEnabled(True)
            self.result.setText(self.t('Report saved.'))

    def failed(self, number, error):
        self._jobs.pop(number, None)
        self._busy = False
        self.invalidate_preview()
        self.result.setText(self.t('Could not export the server report. Check the local service, administrator access and output folder, then try again.'))

    def save_report(self, kind):
        if self._busy:
            return
        if self.reviewed_report is None:
            self.refresh_preview()
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save PDF' if kind == 'pdf' else 'Save CSV'),
                                             'lifetime-server-report.' + kind, f'{kind.upper()} (*.{kind})',
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != '.' + kind:
            destination = destination.with_name(destination.name + '.' + kind)
        if destination.exists() and QMessageBox.question(self, self.t('Replace file?'),
                self.t('This file already exists. Replace it?'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            options = self.options()
            reviewed = copy.deepcopy(self.reviewed_report)
            font = ensure_report_font() if kind == 'pdf' else None
            def save():
                current = self.build(options)
                if self.report_content(current) != self.report_content(reviewed):
                    return 'changed', current
                current['created'] = reviewed['created']
                if kind == 'pdf':
                    write_pdf(current, destination, font=font)
                else:
                    write_csv(current, destination)
                return 'saved', None
            self.submit('save', save)
        except (ValueError, OSError, RuntimeError) as error:
            self.error('Could not save report', error)
