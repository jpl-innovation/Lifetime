"""Desktop date range picker and Uptime & downtime page; offscreen Qt, temporary data, synthetic ledgers only."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_STYLE_OVERRIDE'] = 'Fusion'
os.environ.setdefault('LIFETIME_DATA_DIR', tempfile.mkdtemp(prefix='lifetime-uptime-gui-'))

from qt_test_support import dispose, qt_app
APP = qt_app()

import lifetime_downtime as downtime
import lifetime_gui as gui
import lifetime_settings as settings
import lifetime_tracker as tracker
from lifetime_i18n import set_language
from lifetime_uptime_gui import PeriodModel, PeriodPicker, coverage_line

TODAY = dt.date.today()


def synthetic_ledger(days=40):
    ledger = tracker.new_ledger()
    ledger['daily'] = {(TODAY - dt.timedelta(days=i)).isoformat(): 8 * 3600.0 for i in range(1, days)}
    ledger['total_seconds'] = sum(ledger['daily'].values())
    ledger['recording_started'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=17)).isoformat()
    ledger['downtime_daily'] = {(TODAY - dt.timedelta(days=i)).isoformat():
                                dict(asleep_s=36000, off_s=7200, unknown_s=14400, restarts=1, shutdowns=1,
                                     unexpected_stops=1 if i % 10 == 0 else 0) for i in range(1, days)}
    ledger['downtime_sources'] = dict(log='windows-log', log_from=(TODAY - dt.timedelta(days=days - 1)).isoformat(),
                                      archives=0, archive_note='none')
    return ledger


class PeriodPickerTests(unittest.TestCase):
    def setUp(self):
        set_language('en')

    def test_quick_choices_cover_the_request_and_custom_has_two_calendars(self):
        model = PeriodModel(today=dt.date(2026, 11, 23))
        picker = PeriodPicker(model)
        self.addCleanup(picker.deleteLater)
        self.assertEqual([picker.choices.itemData(i) for i in range(picker.choices.count())], list(downtime.PERIODS))
        self.assertEqual(picker.choices.currentData(), '30d')
        self.assertTrue(picker.from_edit.isHidden())
        self.assertTrue(picker.from_edit.calendarPopup() and picker.to_edit.calendarPopup())
        self.assertEqual(picker.choices.accessibleName(), 'Date range')
        self.assertEqual(picker.from_edit.accessibleName(), 'From date')
        picker.choices.setCurrentIndex(picker.choices.findData('custom'))
        self.assertEqual(model.choice, 'custom')
        self.assertFalse(picker.from_edit.isHidden())
        self.assertEqual((model.start, model.end), (dt.date(2026, 10, 25), dt.date(2026, 11, 23)))
        # No practical lower limit; To never in the future.
        picker.from_edit.setDate(picker.from_edit.minimumDate())
        self.assertLessEqual(model.start.year, 1900)
        self.assertEqual(picker.to_edit.maximumDate().toPyDate(), dt.date(2026, 11, 23))
        model.set('custom', dt.date(2021, 1, 1), dt.date(2030, 1, 1))
        self.assertEqual(model.bounds(), (dt.date(2021, 1, 1), dt.date(2026, 11, 23)))
        # From after To moves To with it.
        picker.from_edit.setDate(picker.from_edit.date().fromString('2026-11-20', 'yyyy-MM-dd'))
        picker.to_edit.setDate(picker.to_edit.date().fromString('2026-11-10', 'yyyy-MM-dd'))
        self.assertLessEqual(model.start, model.end)

    def test_period_bounds(self):
        today = dt.date(2026, 3, 15)
        bounds = lambda choice, **kw: downtime.period_bounds(choice, today, **kw)
        self.assertEqual(bounds('today'), (today, today))
        self.assertEqual(bounds('7d'), (dt.date(2026, 3, 9), today))
        self.assertEqual(bounds('this_month'), (dt.date(2026, 3, 1), today))
        self.assertEqual(bounds('last_month'), (dt.date(2026, 2, 1), dt.date(2026, 2, 28)))
        self.assertEqual(bounds('this_year'), (dt.date(2026, 1, 1), today))
        self.assertEqual(bounds('12m'), (dt.date(2025, 3, 16), today))
        self.assertEqual(bounds('5y'), (dt.date(2021, 3, 16), today))
        self.assertEqual(bounds('all', earliest=dt.date(2019, 5, 2)), (dt.date(2019, 5, 2), today))
        self.assertEqual(bounds('all'), (today, today))
        self.assertEqual(downtime.period_bounds('5y', dt.date(2028, 2, 29))[0], dt.date(2023, 3, 1))
        self.assertEqual(downtime.period_bounds('last_month', dt.date(2026, 1, 10)), (dt.date(2025, 12, 1), dt.date(2025, 12, 31)))

    def test_choice_is_remembered_in_settings(self):
        directory = Path(tempfile.mkdtemp())
        save = lambda value: settings.update_settings({'period': value}, directory)
        model = PeriodModel(settings.load_settings(directory).get('period'), save)
        model.set('custom', dt.date(2022, 1, 1), dt.date(2022, 6, 30))
        again = PeriodModel(settings.load_settings(directory).get('period'), save)
        self.assertEqual((again.choice, again.start, again.end), ('custom', dt.date(2022, 1, 1), dt.date(2022, 6, 30)))
        again.set('5y')
        self.assertEqual(PeriodModel(settings.load_settings(directory)['period']).choice, '5y')
        # Damaged saved values fall back to the default instead of failing.
        self.assertEqual(PeriodModel({'choice': 'custom', 'start': 'nope'}).choice, '30d')
        self.assertEqual(PeriodModel('garbage').choice, '30d')

    def test_coverage_line_says_where_data_starts(self):
        ledger = synthetic_ledger()
        ledger['downtime_sources']['log_from'] = '2026-07-03'
        ledger['recording_started'] = '2026-11-06T02:00:00+00:00'
        line = coverage_line(ledger)
        self.assertIn('Data from 3 Jul 2026 (Windows log)', line)
        self.assertIn('recorded by Lifetime since 6 Nov 2026', line)
        self.assertIn('no archived logs found', line)
        ledger['downtime_sources'].update(archives=2, archive_note='read')
        self.assertIn('Windows log incl. 2 archived logs', coverage_line(ledger))
        ledger['downtime_sources']['archive_note'] = 'denied'
        self.assertIn('access denied', coverage_line(ledger))
        self.assertTrue(coverage_line(tracker.new_ledger()).startswith('No data yet'))
        set_language('vi')
        self.assertIn('Dữ liệu từ', coverage_line(ledger))
        set_language('en')


class WindowPeriodTests(unittest.TestCase):
    def setUp(self):
        set_language('en')
        self.directory = Path(tempfile.mkdtemp())
        self.ledger = synthetic_ledger()
        patches = [patch.object(gui, 'read_ledger', side_effect=lambda: self.ledger),
                   patch.object(gui, 'data_dir', return_value=self.directory)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.window = gui.Window(dict(settings.defaults(), completed=True, history=True))
        self.addCleanup(dispose, self.window)
        self.window.worker.stop()

    def test_activity_and_reports_follow_one_remembered_range(self):
        w = self.window
        self.assertTrue(w.history.ranges.isHidden())
        self.assertIs(w.activity_picker.model, w.reports_picker.model)
        w.period.set('7d')
        self.assertEqual(len(w.history.daily.values), 7)
        self.assertEqual(w.reports_picker.choices.currentData(), '7d')
        self.assertEqual(json.loads((self.directory / 'settings.json').read_text())['period']['choice'], '7d')
        w.period.set('5y')
        start, end = downtime.period_bounds('5y', TODAY)
        self.assertEqual(w.history.daily.dates[0], start)
        self.assertEqual(len(w.history.daily.values), (end - start).days + 1)
        # Days without a record stay unknown (None), never zero.
        self.assertIsNone(w.history.daily.values[0])
        self.assertIn('Data from', w.activity_coverage.text())

    def test_export_dialog_starts_on_the_chosen_period(self):
        from lifetime_technician import ExportDialog
        w = self.window
        w.period.set('custom', dt.date(2026, 1, 1), dt.date(2026, 1, 31))
        dialog = ExportDialog(w, directory=self.directory, language='en', permissions=dict(completed=True, history=True))
        self.addCleanup(dispose, dialog)
        w.prefill_period(dialog)
        self.assertTrue(dialog.range_select.currentData())
        self.assertEqual((dialog.start_date.text(), dialog.end_date.text()), ('2026-01-01', '2026-01-31'))
        w.period.set('all')
        other = ExportDialog(w, directory=self.directory, language='en', permissions=dict(completed=True, history=True))
        self.addCleanup(dispose, other)
        w.prefill_period(other)
        self.assertFalse(other.range_select.currentData())


if __name__ == '__main__':
    unittest.main()
