"""Synthetic release UI/report captures; never reads the installed ledger."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LIFETIME_REDUCE_MOTION'] = '1'
import datetime as dt
import argparse
from pathlib import Path
import tempfile
from unittest.mock import patch

from qt_test_support import qt_app
import lifetime_gui as gui
import lifetime_i18n as i18n
import lifetime_reports as reports
import lifetime_settings as settings
import lifetime_technician as technician
import lifetime_tracker as tracker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.qa/technician'))
    args = parser.parse_args()
    app = qt_app()
    app.setFont(reports.ensure_report_font())
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'LIFETIME_DATA_DIR': temporary}):
        directory = Path(temporary)
        ledger = tracker.new_ledger()
        stamp = dt.datetime.now().astimezone()
        end = stamp.timestamp()
        tracker.sample(ledger, 7200, 'synthetic-boot', stamp.isoformat(),
                       boot_evidence=(end-7200, [(end-7200, end)], False))
        tracker.atomic_json(directory/'ledger.json', ledger)
        for language in ('en', 'vi'):
            preferences = settings.update_settings(dict(completed=True, history=True, language=language,
                resources=False, identity=dict(display_name='Reception / Máy lễ tân Nguyễn Văn 🖥️',
                asset_tag='PC-014', department='Customer service / Chăm sóc khách hàng', deployment_date='2026-01-15')), directory)
            i18n.set_language(language)
            report = reports.build_report(ledger, preferences, selected_identity=settings.IDENTITY_FIELDS, language=language)
            reports.write_pdf(report, output/f'report-{language}.pdf')
            reports.write_csv(report, output/f'report-{language}.csv')
            for size in ((390, 440), (1280, 800)):
                window = gui.Window(preferences)
                window.worker.timer.stop()
                window.timer.stop()
                window.resize(*size)
                window.show()
                window.timer.stop()
                app.processEvents()
                for index, page in enumerate(('overview', 'system', 'insights')):
                    window.navigation.group.button(index).click()
                    app.processEvents()
                    window.grab().save(str(output/f'{page}-{language}-{size[0]}.png'))
                window.navigation.group.button(0).click()
                window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
                app.processEvents()
                window.grab().save(str(output/f'actions-{language}-{size[0]}.png'))
                for name, cls in (('identity', technician.IdentityDialog), ('coverage', technician.CoverageDialog), ('export', technician.ExportDialog)):
                    dialog = cls(window, directory=directory, language=language)
                    dialog.resize(*size)
                    dialog.show()
                    app.processEvents()
                    if name == 'export':
                        for check in dialog.identity_checks.values():
                            check.setChecked(True)
                        dialog.refresh_preview()
                        app.processEvents()
                    dialog.grab().save(str(output/f'{name}-{language}-{size[0]}.png'))
                    dialog.close()
                window.close()
        assert tracker.read_ledger(directory)['total_seconds'] == ledger['total_seconds']
    print('Saved 28 release UI captures and EN/VI PDF/CSV samples in '+str(output))


if __name__ == '__main__':
    main()
