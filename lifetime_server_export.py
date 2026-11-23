"""Asynchronous server report preview/save using the shared native export form."""
import copy
import datetime as dt
from pathlib import Path

from PyQt6.QtWidgets import QComboBox, QFileDialog, QMessageBox

from lifetime_reports import ensure_report_font, write_csv, write_pdf
from lifetime_server_reports import device_report, fleet_report
from lifetime_server_uptime import RangeControl
from lifetime_tasks import Tasks
from lifetime_technician import ExportDialog


class ServerExportDialog(ExportDialog):
    def __init__(self, client, device_ids=None, parent=None, language='en', groups=()):
        self.client = client
        self.device_ids = tuple(device_ids) if device_ids is not None else None
        self._as_of = None          # "now" for availability, fixed at the preview so the save matches it
        self._generation = 0
        self._busy = False
        self._jobs = {}
        super().__init__(parent, directory=client.directory, language=language,
                         permissions=dict(completed=True, history=True, resources=True))
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        self.note('Server exports use current reporting permissions. Optional system fields remain excluded unless selected and permitted by the computer.')
        self.scope = QComboBox()
        self.scope.addItem(self.t('All computers'), None)
        for group in groups:
            self.scope.addItem(self.t('Group: {name}', name=group), group)
        self.scope.setAccessibleName(self.t('Report scope'))
        self.scope.setVisible(self.device_ids is None and bool(groups))
        self.form.insertWidget(1, self.scope)
        self.scope.currentIndexChanged.connect(self.invalidate_preview)
        # Any period (1.3): the shared range control replaces the desktop form's All / Custom dates. All is all
        # available history, as before.
        self.range_select.hide()
        self.dates.hide()
        self.period = RangeControl(self.t, default='all')
        self.form.insertWidget(self.form.indexOf(self.range_select), self.period)
        self.period.changed.connect(self.invalidate_preview)

    def options(self):
        period = self.period.period()
        start, end = (period[0].isoformat(), period[1].isoformat()) if period else (None, None)
        self._as_of = self._as_of or dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        return dict(start=start, end=end, language=self.language, as_of=self._as_of,
                    group=self.scope.currentData() if self.device_ids is None else None,
                    selected_identity=tuple(key for key, check in self.identity_checks.items() if check.isChecked()),
                    system_fields=tuple(key for key, check in self.system_checks.items() if check.isChecked()))

    def build(self, options):
        options = dict(options)
        as_of, group = options.pop('as_of'), options.pop('group')
        ids = self.device_ids
        if ids is None:
            ids = []
            offset = 0
            while True:
                page = self.client.call('devices', limit=25, offset=offset, **({'group': group} if group else {}))
                ids.extend(item['device_id'] for item in page)
                if len(page) < 25:
                    break
                offset += 25
                if offset >= 10000:
                    raise ValueError('Fleet selection exceeds the supported limit')
        devices = [self.client.call('device', device_id=identifier, limit=1) for identifier in ids]
        single = self.device_ids is not None and len(devices) == 1
        measured = []
        for index in range(0, len(ids), 100):
            arguments = dict(device_ids=list(ids[index:index + 100]), as_of=as_of, daily=single)
            if options['start']:
                arguments.update(start=options['start'], end=options['end'])
            measured += self.client.call('availability', **arguments)['devices']
        thresholds = {rule['rule']: rule.get('threshold') for rule in self.client.call('alert_rules')}
        try:
            found = self.client.call('insights')
        except ValueError:      # an older server without energy figures
            found = None
        energy = found and dict(settings=found['settings'], today=found.get('today'),
                               computers={c['device_id']: c for c in found['computers']})
        if single:
            return device_report(devices[0], availability=measured[0] if measured else None, thresholds=thresholds,
                                 insights=energy, **options)
        return fleet_report(devices, availability=measured, thresholds=thresholds, group=group, insights=energy, **options)

    # When a computer last reported changes with every sync, so a connected computer would make every save
    # "different from the review" and the file could never be written. The saved report still carries the
    # newest times; only a change to the content the administrator reviewed asks for a new review.
    VOLATILE_ROWS = ('Client observation time', 'Server receipt time', 'System reading time', 'System disk free (%)',
                     # a new on-battery reading can change the measured draw between the review and the save
                     'Power draw (W)', 'Power method', 'Energy in the period (kWh)', 'Cost in the period',
                     'Measured on battery', 'Estimated from typical power', 'Awake after hours, last 7 days (hours)',
                     'Energy after hours, last 7 days (kWh)', 'Cost after hours, last 7 days',
                     'Battery forecast', 'System disk forecast', 'Awake hours so far')

    @staticmethod
    def report_content(report):
        result = ExportDialog.report_content(report)
        result['extra_rows'] = [row for row in result.get('extra_rows', ())
                                if row[1] not in ServerExportDialog.VOLATILE_ROWS]
        return result

    def invalidate_preview(self, *unused):
        self._generation += 1
        self._as_of = None
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
