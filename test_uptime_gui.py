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
        settings.save_settings(w.settings, self.directory)         # a later save of the window's copy keeps it
        self.assertEqual(settings.load_settings(self.directory)['period']['choice'], '7d')
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


def synthetic_events(days=40):
    """A boot each morning, a sleep at lunch, a restart some evenings; an unexpected stop every 10th day."""
    events = []
    for i in range(days, 0, -1):
        base = dt.datetime.combine(TODAY - dt.timedelta(days=i), dt.time(8)).timestamp()
        events += [dict(time=base, kind='start', by='', reason='', source=''),
                   dict(time=base + 4*3600, kind='sleep', by='', reason='', source=''),
                   dict(time=base + 5*3600, kind='wake', by='', reason='', source='Power Button'),
                   dict(time=base + 10*3600, kind='unexpected' if i % 10 == 0 else 'shutdown',
                        by='' if i % 10 == 0 else 'winlogon.exe (EXAMPLE\\someone)', reason='', source='')]
    return events


class UptimePageTests(unittest.TestCase):
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
        self.window.uptime.events_source = lambda: synthetic_events()
        self.window.resize(1280, 900)
        self.window.show()

    def open(self):
        from qt_test_support import wait_until
        self.window.select_page(7)
        wait_until(lambda: self.window.uptime.events is not None)
        APP.processEvents()
        return self.window.uptime

    def test_sidebar_entry_follows_history_consent_and_language(self):
        w = self.window
        button = w.navigation.group.button(7)
        self.assertEqual(button.text(), 'Uptime & downtime')
        self.assertFalse(button.isHidden())
        set_language('vi')
        self.assertEqual(w.navigation.group.button(7).text(), 'Thời gian hoạt động')
        set_language('en')
        w.settings['history'] = False
        w.apply_visibility()
        self.assertTrue(button.isHidden())
        w.select_page(7)
        self.assertNotEqual(w.active_page, 7)

    def test_totals_counts_timeline_and_events_follow_the_range(self):
        page = self.open()
        self.assertTrue(page.isVisible())
        self.window.period.set('7d')
        totals = page.summary['totals']
        # 6 recorded days (today has none yet) of 8 h awake, 10 h asleep, 2 h off, 4 h unknown, plus today unknown.
        self.assertEqual(totals['asleep'], 6 * 36000)
        self.assertEqual(totals['off'], 6 * 7200)
        self.assertEqual(totals['awake'], 6 * 8 * 3600)
        self.assertEqual((totals['restarts'], totals['shutdowns']), (6, 6))
        self.assertEqual(page.values['restarts'].text(), '6')
        self.assertEqual(page.values['share'].text(), f"{8 / 20 * 100:.0f} %")
        self.assertTrue(page.timeline.by_row())
        self.assertEqual(len(page.timeline.rows), 7)
        self.assertEqual(page.timeline.accessibleName(), 'Uptime timeline')
        # Longest spans come from the events: 5 h awake after the lunch sleep, 14 h off overnight.
        self.assertEqual(page.values['longest_awake'].text(), '5 h 00 m')
        self.assertEqual(page.values['longest_off'].text(), '14 h 00 m')
        self.window.period.set('30d')
        self.assertEqual(page.summary['totals']['unexpected_stops'], 2)      # days 10 and 20 back
        self.assertNotEqual(page.values['between_stops'].text(), 'Not available')
        self.assertTrue(page.timeline.by_row())           # up to a month: one row per day
        shown = page.event_rows.count()
        self.assertEqual(shown, 50)
        self.assertFalse(page.more.isHidden())
        page.more.click()
        self.assertGreater(page.event_rows.count(), shown)
        texts = [page.event_rows.itemAt(i).widget().text() for i in range(page.event_rows.count())]
        self.assertTrue(any('wake source: Power Button' in t for t in texts))
        self.assertTrue(any('asked by winlogon.exe' in t for t in texts))
        self.assertTrue(any('Unexpected stop' in t for t in texts))
        self.assertIn('Data from', page.coverage.text())
        self.window.period.set('12m')
        self.assertFalse(page.timeline.by_row())          # longer: one stacked bar per day
        self.assertEqual(page.event_rows.count(), 50)       # a new range starts on the first page of events

    def test_unknown_days_are_never_zero_and_log_failures_are_said(self):
        page = self.open()
        self.window.period.set('custom', TODAY - dt.timedelta(days=400), TODAY - dt.timedelta(days=300))
        totals = page.summary['totals']
        self.assertEqual(totals['awake'] + totals['asleep'] + totals['off'], 0)
        self.assertEqual(len(page.summary['rows']), 101)
        self.assertEqual(totals['unknown'], sum(int(downtime.day_length(row['day'])) for row in page.summary['rows']))
        self.assertEqual(page.values['share'].text(), 'Not available')
        self.assertIn('No power events in this range', page.events_note.text())
        page._events_failed(0, 'ValueError')
        self.assertIn('could not be read', page.events_note.text())

    def test_glass_dark_vietnamese_and_reduced_motion_render(self):
        from lifetime_glass import set_style
        from lifetime_theme import set_theme
        page = self.open()
        self.window.period.set('12m')
        try:
            for style, theme, lang in (('glass', 'dark', 'vi'), ('classic', 'light', 'en'), ('glass', 'light', 'en'), ('classic', 'dark', 'vi')):
                set_style(style)
                set_theme(theme)
                set_language(lang)
                self.window.apply_live_style(style)
                APP.processEvents()
                self.assertFalse(page.grab().isNull())
                self.assertFalse(page.timeline.grab().isNull())
        finally:
            set_style('classic')
            set_theme('dark')
            set_language('en')
        with patch.dict(os.environ, {'LIFETIME_REDUCE_MOTION': '1'}):
            self.window.select_page(0)
            self.window.select_page(7)
            self.assertIsNone(self.window.page_animation)

    def test_five_years_render_fast(self):
        """About 1,900 days: report the time to compute and paint the page, and the 1-second refresh."""
        days = 1900
        ledger = tracker.new_ledger()
        ledger['daily'] = {(TODAY - dt.timedelta(days=i)).isoformat(): 6 * 3600.0 for i in range(1, days)}
        ledger['downtime_daily'] = {(TODAY - dt.timedelta(days=i)).isoformat():
                                    dict(asleep_s=40000, off_s=10000, unknown_s=14800, restarts=i % 2, shutdowns=1,
                                         unexpected_stops=1 if i % 37 == 0 else 0) for i in range(1, days)}
        ledger['total_seconds'] = sum(ledger['daily'].values())
        self.ledger = ledger
        page = self.open()
        self.window.period.set('5y')
        page.fingerprint = None
        started = time.perf_counter()
        page.set_ledger(ledger)
        page.grab()
        render = time.perf_counter() - started
        started = time.perf_counter()
        self.window.refresh()
        refresh = time.perf_counter() - started
        print(f'\n1,900-day Uptime page: compute + paint {render * 1000:.0f} ms; window refresh {refresh * 1000:.0f} ms')
        self.assertEqual(len(page.timeline.rows), (TODAY - downtime.period_bounds('5y', TODAY)[0]).days + 1)
        self.assertLessEqual(len(page.timeline.columns()), page.timeline.area().width() + 1)
        self.assertLess(render, 3.0)
        self.assertLess(refresh, 3.0)


if __name__ == '__main__':
    unittest.main()
