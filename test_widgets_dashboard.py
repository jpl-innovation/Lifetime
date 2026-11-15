"""Shared dashboard widgets: the pure rules (worst state, buckets, presence ticks, capacity colours, the health score)
and the widgets themselves in Glass/Classic, dark/light: they paint, say what they show to screen readers, react to
mouse and keyboard, and never animate or repaint on their own."""
import datetime as dt
import unittest

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QWidget

import lifetime_health as health
import lifetime_widgets_dashboard as widgets
from lifetime_glass import active_style, set_style
from lifetime_theme import active_theme, set_theme
from qt_test_support import qt_app


class RuleTests(unittest.TestCase):
    def test_worst_state_wins_and_missing_is_below_every_state(self):
        self.assertEqual(widgets.worst(['good', 'danger', 'warning']), 'danger')
        self.assertEqual(widgets.worst(['good', None]), 'good')
        self.assertEqual(widgets.worst(['good', 'neutral']), 'neutral')     # unknown is not good
        self.assertIsNone(widgets.worst([]))

    def test_buckets_keep_every_state_when_they_fit_and_the_worst_when_not(self):
        self.assertEqual(widgets.bucket(['good', 'danger'], 5), ['good', 'danger'])
        self.assertEqual(widgets.bucket(['good'] * 9 + ['danger'], 5), ['good'] * 4 + ['danger'])
        self.assertEqual(len(widgets.bucket(['good'] * 100, 7)), 7)
        self.assertEqual(widgets.bucket(['good'], 0), [])

    def test_presence_intervals_become_hourly_ticks(self):
        start = dt.datetime(2026, 11, 15, 0, 0, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(hours=4)
        intervals = [('good', start + dt.timedelta(minutes=70), start + dt.timedelta(hours=2, minutes=30)),
                     ('warning', start + dt.timedelta(hours=2, minutes=30), None)]
        self.assertEqual(widgets.interval_ticks(intervals, start, end, 4), [None, 'good', 'warning', 'warning'])

    def test_capacity_turns_amber_under_15_and_red_under_5_percent_free(self):
        self.assertEqual(widgets.capacity_kind(0.5), 'info')
        self.assertEqual(widgets.capacity_kind(0.86), 'warning')
        self.assertEqual(widgets.capacity_kind(0.96), 'danger')
        self.assertEqual(widgets.capacity_kind(None), 'neutral')

    def test_health_score_averages_known_factors_and_is_unknown_without_any(self):
        self.assertEqual(widgets.health_score([('a', 'good', ''), ('b', 'warning', '')]), 75)
        self.assertEqual(widgets.health_score([('a', 'good', ''), ('b', None, '')]), 100)     # missing is left out
        self.assertIsNone(widgets.health_score([('a', None, '')]))
        self.assertEqual(widgets.score_kind(None), 'neutral')
        self.assertEqual(widgets.score_kind(95), 'good')
        self.assertEqual(widgets.score_kind(50), 'warning')
        self.assertEqual(widgets.score_kind(0), 'danger')

    def test_health_factors_follow_the_documented_thresholds(self):
        self.assertEqual(health.factors(0, 85), [('stability', 'good', 0), ('battery', 'good', 85)])
        self.assertEqual(health.factors(2, 79.9), [('stability', 'warning', 2), ('battery', 'warning', 79.9)])
        self.assertEqual(health.factors(3, 59), [('stability', 'danger', 3), ('battery', 'danger', 59)])
        self.assertEqual(health.factors(None, None), [('stability', None, None), ('battery', None, None)])
        self.assertEqual(health.factors(0, None, has_battery=False), [('stability', 'good', 0)])


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.addCleanup(set_style, active_style())
        self.addCleanup(set_theme, active_theme())

    def paints(self, widget, size=(420, 120)):
        """Every style and theme: the widget renders something without errors."""
        widget.resize(*size)
        for style in ('glass', 'classic'):
            for theme in ('dark', 'light'):
                set_style(style)
                set_theme(theme)
                image = widget.grab().toImage()
                self.assertFalse(image.isNull())

    def test_chip_says_its_words_and_sizes_to_them(self):
        chip = widgets.StatusChip('danger', 'Needs attention')
        self.assertEqual(chip.text(), 'Needs attention')
        self.assertEqual(chip.accessibleName(), 'Needs attention')
        narrow = chip.sizeHint().width()
        chip.set_state('good', 'Kết nối ổn định lâu dài')       # longer Vietnamese words get a wider pill
        self.assertGreater(chip.sizeHint().width(), narrow)
        self.paints(chip, (chip.sizeHint().width(), chip.sizeHint().height()))

    def test_chip_paints_its_state_colour_and_glyph(self):
        image = QImage(160, 30, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        width = widgets.paint_chip(painter, image.rect().toRectF(), 'good', 'Online')
        painter.end()
        self.assertLess(width, 160)
        colours = {image.pixelColor(x, y).name() for x in range(0, int(width)) for y in range(30)}
        self.assertIn(widgets.color('good').name(), colours)

    def test_heartbeat_reports_and_shows_the_hovered_ticks(self):
        bar = widgets.HeartbeatBar()
        bar.set_ticks([('good', '09:00 · Online'), (None, '10:00 · No record'), ('warning', '11:00 · Offline')],
                      'Last 24 hours: online in 1 of 24 hours')
        self.assertEqual(bar.accessibleName(), 'Last 24 hours: online in 1 of 24 hours')
        self.paints(bar, (300, 26))
        bar.show()
        self.addCleanup(bar.close)
        QTest.mouseMove(bar, QPoint(bar.width() - 4, 10))       # no error when hovering the newest tick

    def test_capacity_bar_names_its_figures(self):
        bar = widgets.CapacityBar()
        bar.set_usage(480, 512, 'C:', '32 GB free of 512 GB')
        self.assertEqual(bar.segments, [(480, 'warning')])
        self.assertEqual(bar.accessibleName(), 'C: 32 GB free of 512 GB')
        self.paints(bar, (300, 40))

    def test_summary_strip_opens_a_state_by_click_and_by_keyboard(self):
        strip = widgets.SummaryStrip()
        strip.set_counts([('online', 'good', 'Online', 8), ('offline', 'warning', 'Offline', 2)])
        self.assertEqual(strip.accessibleName(), 'Online 8, Offline 2')
        picked = []
        strip.picked.connect(picked.append)
        strip.resize(500, strip.height())
        strip.show()
        self.addCleanup(strip.close)
        self.app.processEvents()
        strip.grab()
        rect = strip._hits[1][0]
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        strip.setFocus()
        QTest.keyClick(strip, Qt.Key.Key_Right)
        QTest.keyClick(strip, Qt.Key.Key_Return)
        self.assertEqual(picked, ['offline', 'offline'])
        self.paints(strip, (500, strip.height()))

    def test_event_rail_grows_with_its_events_and_reads_as_text(self):
        rail = widgets.EventRail()
        rail.set_events([('danger', 'Incident opened', '09:10', ''), ('neutral', 'Problem started', '09:00', 'Lan')], 'Timeline')
        self.assertEqual(rail.height(), 2 * rail.ROW)
        self.assertEqual(rail.text(), '09:10 · Incident opened\n09:00 · Problem started · Lan')
        self.assertIn('Incident opened', rail.accessibleDescription())
        self.paints(rail, (360, rail.height()))

    def test_empty_state_kinds_and_action(self):
        empty = widgets.EmptyState('setup', 'No network checks yet', 'Fill in a name below.', 'Add check')
        clicked = []
        empty.clicked.connect(lambda: clicked.append(True))
        empty.click()
        self.assertEqual(clicked, [True])
        self.assertEqual(empty.accessibleName(), 'No network checks yet')
        empty.set_state('clear', 'No current problems')
        self.assertTrue(empty.button.isHidden())
        self.assertTrue(empty.body.isHidden())
        self.assertEqual(empty.icon.name, 'check')
        self.paints(empty)

    def test_health_badge_shows_unknown_without_readings_and_explains_itself(self):
        badge = widgets.HealthBadge()
        verdicts = dict(good='Good', warning='Needs a look', danger='Poor', neutral='Unknown')
        badge.set_factors([('Stability', None, 'Not available')], verdicts, 'How it is computed.')
        self.assertIsNone(badge.score)
        self.assertEqual(badge.verdict.text(), 'Unknown')
        badge.set_factors([('Stability', 'good', '0'), ('Battery', 'warning', '70%')], verdicts, 'How it is computed.')
        self.assertEqual(badge.score, 75)
        self.assertEqual(badge.verdict.text(), 'Needs a look')
        self.assertEqual(badge.toolTip(), 'How it is computed.')
        self.assertIn('Battery: 70%', badge.accessibleDescription())
        self.paints(badge, (500, 100))

    def test_info_card_and_mini_spark(self):
        from PyQt6.QtWidgets import QLabel
        card = widgets.InfoCard('cpu', 'CPU')
        value = QLabel('Example CPU')
        card.add_row('Model', value)
        self.assertEqual(value.accessibleName(), 'CPU / Model')
        self.paints(card, (300, 120))
        spark = widgets.MiniSpark()
        spark.set_values([1], 'CPU')
        self.assertTrue(spark.isHidden())           # one point is not a trend
        spark.set_values([10, None, 30, 20], 'CPU, last 24 hours')
        self.assertEqual(spark.values, [10, 30, 20])
        self.paints(spark, (72, 22))

    def test_icons_draw_every_glyph(self):
        for name in ('check', 'cross', 'bang', 'info', 'dash', 'cpu', 'memory', 'storage', 'network', 'graphics', 'board',
                     'os', 'battery', 'event', 'list', 'plus', 'calendar', 'restart', 'longest', 'average', 'system', 'clock'):
            self.paints(widgets.Icon(name), (20, 20))

    def test_server_row_delegate_paints_chips_and_makes_room_for_them(self):
        from lifetime_server_devices import RowDelegate
        table = QTableWidget(1, 1)
        self.addCleanup(table.deleteLater)
        delegate = RowDelegate(table)
        table.setItemDelegate(delegate)
        item = QTableWidgetItem('Needs attention')
        table.setItem(0, 0, item)
        option = QStyleOptionViewItem()
        option.initFrom(table)
        plain = delegate.sizeHint(option, table.model().index(0, 0)).width()
        item.setData(widgets.VISUAL_ROLE, ('chip', 'danger'))
        self.assertGreater(delegate.sizeHint(option, table.model().index(0, 0)).width(), plain)
        self.assertEqual(item.text(), 'Needs attention')        # sorting and tests still read the words
        image = QImage(220, 44, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        option.rect = QRect(0, 0, 220, 44)
        delegate.paint(painter, option, table.model().index(0, 0))
        painter.end()
        self.assertIn(widgets.color('danger').name(), {image.pixelColor(x, 22).name() for x in range(220)})

    def test_nothing_repaints_while_idle(self):
        from PyQt6.QtCore import QEvent, QObject
        holder = QWidget()
        self.addCleanup(holder.close)
        strip, bar, chip = widgets.SummaryStrip(), widgets.HeartbeatBar(), widgets.StatusChip('good', 'Online')
        for index, widget in enumerate((strip, bar, chip)):
            widget.setParent(holder)
            widget.move(0, index * 60)
        strip.set_counts([('online', 'good', 'Online', 1)])
        bar.set_ticks([('good', 'x')])
        holder.resize(400, 200)
        holder.show()
        QTest.qWait(100)

        class Counter(QObject):
            count = 0

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.Paint:
                    Counter.count += 1
                return False
        counter = Counter()
        for widget in (strip, bar, chip):
            widget.installEventFilter(counter)
        strip.set_counts([('online', 'good', 'Online', 1)])      # the same data again: nothing to draw
        bar.set_ticks([('good', 'x')])
        chip.set_state('good', 'Online')
        QTest.qWait(300)
        self.assertEqual(Counter.count, 0)


if __name__ == '__main__':
    unittest.main()
