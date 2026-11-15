"""Read-only chart models and dependency-free QPainter views.

Daily keys are local civil dates. Missing dates are unknown, never invented zeroes.
All public aggregation values are seconds; presentation converts to hours.
"""
from collections import deque
import datetime as dt
from lifetime_i18n import tr, language, set_language, on_language_change, date_text, datetime_text, month, weekdays
import math
import os
import sys
import time

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QVariantAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QFontMetrics
from PyQt6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                            QHBoxLayout, QGridLayout, QButtonGroup, QScrollArea,
                            QTableWidget, QTableWidgetItem, QHeaderView, QToolTip,
                            QAbstractItemView)
from lifetime_i18n_qt import QLabel, QPushButton, QWidget, QFrame, QTableWidget
from lifetime_i18n import duration, time_text
from lifetime_shell import DisclosureButton
from lifetime_glass import active_style, paint_glass, paint_track, solid
from lifetime_reveal import Intro, phase, progress
from lifetime_theme import CONTROL_RADIUS, PANEL_RADIUS, Tokens

TOKENS = Tokens()   # follows the active (light or dark) palette
# A single warm hue mixed into the navy ground. Relative luminance increases
# at every step (including at the transition out of the navy-tinted low end).
def heat_ramp():
    return TOKENS['heat']

RANGES = ('30 days', '90 days', '1 year', 'All')
BIN_LABELS = ('<15m', '15–60m', '1–3h', '3–6h', '6–12h', '12h+')
BIN_LIMITS = (900, 3600, 10800, 21600, 43200)


def local_date(value, tz=None):
    """Date-only and naive ISO values already mean local time.

    Convert aware instants with the historical OS timezone rules (or an explicit
    test timezone), rather than replacing their offset or stepping by 86400 s.
    """
    if isinstance(value, dt.datetime):
        stamp = value
    elif isinstance(value, dt.date):
        return value
    elif len(str(value)) == 10:
        return dt.date.fromisoformat(value)
    else:
        stamp = dt.datetime.fromisoformat(value)
    return stamp.astimezone(tz).date() if stamp.tzinfo else stamp.date()


def valid_seconds(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def clean_daily(daily):
    result = {}
    for key, value in daily.items():
        try:
            day = local_date(key)
        except (ValueError, TypeError):
            continue
        if valid_seconds(value):
            result[day] = result.get(day, 0) + value
    return result


def range_bounds(daily, selection='30 days', today=None):
    today = today or dt.date.today()
    if selection == 'All':
        return min((d for d in daily if d <= today), default=today), today
    if selection == '1 year':
        # Inclusive trailing calendar year, also valid on Feb 29.
        try:
            anniversary = today.replace(year=today.year-1)
        except ValueError:
            anniversary = today.replace(year=today.year-1, day=28)
        return anniversary + dt.timedelta(days=1), today
    return today-dt.timedelta(days=int(selection.split()[0])-1), today


def filter_range(daily, start, end):
    """Inclusive date filtering without mutating the ledger."""
    return {d: v for d, v in daily.items() if start <= d <= end}


def date_series(daily, start, end):
    return [(start+dt.timedelta(days=i), daily.get(start+dt.timedelta(days=i)))
            for i in range(max(0, (end-start).days+1))]


def weekly(daily):
    """Monday-aligned weekly sums, retaining recorded zeroes."""
    result = {}
    for day, value in sorted(daily.items()):
        monday = day-dt.timedelta(days=day.weekday())
        result[monday] = result.get(monday, 0) + value
    return result


def moving_average(points, window=7):
    """Trailing calendar window. Require every day; gaps break the line."""
    if window < 1:
        raise ValueError('window must be positive')
    values = dict(points)
    return [(day, sum(values[d] for d in days)/window if all(values.get(d) is not None for d in days) else None)
            for day, _ in points
            for days in [[day-dt.timedelta(days=i) for i in range(window)]]]


def weekday_averages(daily):
    groups = [[] for _ in range(7)]
    for day, value in daily.items():
        groups[day.weekday()].append(value)
    return [sum(g)/len(g) if g else None for g in groups]


def filter_sessions(sessions, start, end, tz=None):
    """Histogram scope is the local session start, not an inferred end."""
    result = []
    for session in sessions:
        try:
            day = local_date(session['start'], tz)
            if start <= day <= end and valid_seconds(session['seconds']):
                result.append(session)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def histogram(sessions):
    counts = [0]*6
    for session in sessions:
        value = session.get('seconds')
        if valid_seconds(value):
            counts[next((i for i, limit in enumerate(BIN_LIMITS) if value < limit), 5)] += 1
    return counts


def cumulative(daily, start, end, total_seconds=None, baseline_seconds=0):
    """Keep the lifetime origin when zooming. Anchor to the ledger's total.

    A retained daily series may omit older or unattributed credits. Carry those
    in the opening balance, never distribute them over invented dates.
    """
    opening = max(0, (total_seconds if total_seconds is not None else sum(daily.values()))-sum(daily.values())) + baseline_seconds
    opening += sum(v for day, v in daily.items() if day < start)
    result = []
    for day, value in date_series(daily, start, end):
        if value is not None:
            opening += value
        result.append((day, opening))
    return result


def live_started(ledger):
    imported = ledger.get('imported') or {}
    original = imported.get('original') or {}
    # merge() moves tracking_started back to the earliest import date. The
    # original value is the actual live boundary, not the merged field.
    value = original.get('history_started') or original.get('tracking_started') or imported.get('until') or ledger.get('history_started') or ledger.get('tracking_started')
    try:
        return local_date(value) if value else None
    except (ValueError, TypeError):
        return None


def hour_profile(ledger, start, end, daily, tz=None):
    """Average seconds per recorded day. Never spread live sessions over sleep.

    hourly[24] has no dates. It is valid only if the selected range contains all
    retained history. Subranges use *imported* sessions: those are awake spans,
    unlike live boot sessions, which can contain sleep and initial boot credit.
    """
    selected = filter_range(daily, start, end)
    if not selected:
        return [None]*24, tr('Needs recorded days')
    hourly = ledger.get('hourly', [])
    first_history = ledger.get('history_started')
    try:
        complete_daily_span = not first_history or local_date(first_history) >= min(daily)
    except (ValueError, TypeError):
        complete_daily_span = False
    if daily and complete_daily_span and start <= min(daily) and end >= max(daily) and len(hourly) == 24 and all(valid_seconds(v) for v in hourly):
        return [v/len(selected) for v in hourly], tr('Recorded hourly totals / recorded days' if ledger.get('daily_repair_version') or ledger.get('daily_allocation') else 'Recorded hourly totals / recorded days; boot credit excluded')
    totals = [0.0]*24
    observed = set()
    for session in ledger.get('sessions', []):
        if not session.get('imported') or not valid_seconds(session.get('seconds')):
            continue
        try:
            stamp = dt.datetime.fromisoformat(session['start'])
            cursor = stamp.timestamp()
        except (ValueError, TypeError, KeyError, OSError):
            continue
        stop = cursor + session['seconds']
        while cursor < stop:
            local = dt.datetime.fromtimestamp(cursor, tz)
            boundary = cursor + 3600-local.minute*60-local.second-local.microsecond/1e6
            segment_end = min(stop, boundary)
            day = local.date()
            if start <= day <= end:
                totals[local.hour] += segment_end-cursor
                observed.add(day)
            cursor = segment_end
    for key, hours in ledger.get('dated_hourly', {}).items():
        day = local_date(key)
        if start <= day <= end and len(hours) == 24:
            totals = [a+b for a, b in zip(totals, hours)]
            observed.add(day)
    if not observed:
        return [None]*24, tr('Dated hourly history is unavailable for this range')
    return [v/len(observed) for v in totals], tr('coverage.partial', days=len(observed))


def hm(seconds):
    return duration(seconds, days=False)


def font(size=13, bold=False):
    value = QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans')
    value.setPixelSize(size)
    if hasattr(value, 'setFeature'):
        value.setFeature(QFont.Tag('tnum'), 1)
    value.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return value


def caption(text):
    label = QLabel(text)
    label.setObjectName('caption')
    label.setWordWrap(True)
    return label


class Plot(QWidget):
    """One axis, explicit missing data, generous hover lanes, bounded painting."""
    def __init__(self, kind='bars', unit='h'):
        super().__init__()
        self.kind, self.unit = kind, unit
        self.keys, self.values, self.overlay = [], [], []
        self.dates = []
        self.today = dt.date.today()
        self.marker = None
        self.hover = None
        self.message = ''
        self.scope = ''
        self.details = []
        self.bar_color = None
        self.entrance = None      # (intro, start, end): axis settles, then bars rise oldest to newest
        self.setMouseTracking(True)
        self.setMinimumWidth(120)
        self.setMinimumHeight(140)
        self.setAccessibleName(tr('Chart; use Show data for underlying numbers'))

    def set_data(self, keys, values, overlay=None, dates=None, message=''):
        self.keys, self.values = list(keys), list(values)
        self.overlay = list(overlay or [])
        self.dates = list(dates or [])
        self.message = message
        self.hover = None
        available = [v for v in values if v is not None]
        self.setAccessibleDescription(tr('chart.values', count=len(available), message=message))
        self.update()
        if len(available) >= (2 if self.kind == 'spark' else 1) and not message:
            self.arm_entrance()

    def arm_entrance(self, duration=800):
        """One entrance per chart, the first time it is on screen with data; live refreshes never replay it.
        A chart whose entrance belongs to a page (the Overview trend) keeps that one."""
        if self.entrance is None:
            self.entrance = (Intro(self, duration, 0, motion_reduced), 0.0, 1.0)
        if self.entrance[1:] == (0.0, 1.0) and self.entrance[0].parent() is self:
            self.entrance[0].ready()

    def plot(self):
        left = max(58, QFontMetrics(font()).horizontalAdvance(f'{self.scale():,.0f} '+tr('unit.hour'))+10)
        return QRectF(left, 24, max(1, self.width()-left-16), max(1, self.height()-53))

    def display(self, value):
        if value is None:
            return tr('No recorded data')
        if self.unit == 'h':
            return hm(value*3600)
        if self.unit == 'sessions':
            return tr('chart.sessions', count=value)
        return f'{value:,.2f} {self.unit}'

    def scale(self):
        values = [v for v in self.values+self.overlay if v is not None]
        peak = max(values, default=0)
        if self.kind == 'daily':
            return max(24, math.ceil(peak/6)*6)
        if self.unit == '%':
            return 100
        if self.unit == 'sessions':
            return max(3, math.ceil(peak/3)*3)
        return max(1, peak*1.18)

    def positions(self, rect):
        n = len(self.values)
        if self.kind in ('line', 'spark'):
            return [rect.left()+i*rect.width()/max(1, n-1) for i in range(n)]
        return [rect.left()+(i+.5)*rect.width()/max(1, n) for i in range(n)]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font())
        r = self.plot()
        available = [v for v in self.values if v is not None]
        if self.message or not available:
            p.setPen(QColor(TOKENS['muted']))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message or tr('No recorded data in this range'))
            return
        maximum = self.scale()
        u = progress(self.entrance, eased=False)
        entering = u < 1.0
        p.setOpacity(phase(u, 0.0, 0.3))        # the axis settles first, so the bars have something to stand on
        for fraction in (0, 1/3, 2/3, 1):
            y = r.bottom()-r.height()*fraction
            p.setPen(QPen(QColor(TOKENS['border']), .6))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(TOKENS['muted']))
            value = maximum*fraction
            # Counts (events, cycles) are whole numbers: never "1.0 events".
            whole = maximum >= 9 or getattr(self, 'unit', None) in ('events', 'cycles')
            tick = f'{value:,.0f}' if whole else f'{value:.1f}'
            if self.unit == 'h':
                tick += ' '+tr('unit.hour')
            elif self.unit != 'sessions':
                tick += self.unit if self.unit == '%' else ''
            p.drawText(QRectF(0, y-9, r.left()-8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tick)
        p.setOpacity(1.0)
        xs = self.positions(r)
        ys = [r.bottom()-r.height()*v/maximum if v is not None else None for v in self.values]
        if self.kind in ('line', 'spark'):
            self.draw_line(p, xs, ys, r, area=self.kind == 'line', color=self.bar_color, reveal=phase(u, 0.1, 1.0))
        else:
            step = r.width()/len(self.values)
            width = min(18, max(.5, step-2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setClipRect(r.adjusted(-1, 0, 1, 0))
            # Pixel buckets keep several years cheap while hover/table retain
            # every date. Max preserves short peaks when marks are subpixel.
            stride = max(1, math.ceil(len(xs)/max(1, r.width())))
            for i in range(0, len(xs), stride):
                candidates = [j for j in range(i, min(i+stride, len(xs))) if ys[j] is not None]
                if not candidates:
                    continue
                j = min(candidates, key=lambda k: ys[k])
                if self.values[j] == 0:
                    continue
                is_today = bool(self.dates and self.dates[j] == self.today)
                color = QColor(self.bar_color or TOKENS['accent'])   # every bar in brand yellow
                grow = 1.0
                if entering:    # oldest day first, the newest bar last and a little slower
                    begin = 0.16 + 0.5 * j / max(1, len(xs) - 1)
                    grow = phase(u, begin, begin + (0.34 if j == len(xs) - 1 else 0.28))
                bar = QRectF(xs[j]-width/2, r.bottom()-(r.bottom()-ys[j])*grow, width, max(.6*grow, (r.bottom()-ys[j])*grow))
                p.setBrush(color)
                # Clip a round rectangle below the baseline: only the data
                # ends are rounded, the baseline stays square and anchored.
                p.drawRoundedRect(bar.adjusted(0, 0, 0, 4), min(4, width/2), 4)
            p.setClipping(False)
            if self.overlay:
                overlay_y = [r.bottom()-r.height()*v/maximum if v is not None else None for v in self.overlay]
                self.draw_line(p, xs, overlay_y, r, color=TOKENS['text'])
        if self.marker and self.dates and self.dates[0] <= self.marker <= self.dates[-1]:
            index = next((i for i, d in enumerate(self.dates) if d >= self.marker), 0)
            x = xs[index]
            p.setPen(QPen(QColor(TOKENS['muted']), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(TOKENS['muted']))
            label_width = min(210, r.width())
            lx = max(r.left(), min(x+6, r.right()-label_width))
            p.drawText(QRectF(lx, r.top()+3, label_width, 20), Qt.AlignmentFlag.AlignLeft, tr('Live tracking began'))
        # Sparse ticks selected by pixel width rather than data length.
        count = min(len(xs), max(2, int(r.width()/115)))
        indices = sorted(set(round(i*(len(xs)-1)/max(1, count-1)) for i in range(count)))
        if self.kind == 'line':
            # Health capacity samples may be weekly then daily. Choose ticks
            # by their painted positions so clustered dates cannot overlap.
            candidates = sorted(set(min(range(len(xs)), key=lambda j: abs(xs[j]-(r.left()+i*r.width()/max(1, count-1))))
                                    for i in range(count)))
            indices = []
            for i in candidates:
                center = min(max(xs[i], r.left()+50), r.right()-50)
                previous = min(max(xs[indices[-1]], r.left()+50), r.right()-50) if indices else None
                if previous is None or center-previous >= 105:
                    indices.append(i)
                elif i == candidates[-1]:
                    indices[-1] = i
        if self.kind == 'bars' and len(xs) <= 24 and r.width()/len(xs) >= 54:
            indices = list(range(len(xs))) if len(xs) <= 7 else list(range(0, len(xs), 4))
        p.setOpacity(phase(u, 0.0, 0.3))
        p.setPen(QColor(TOKENS['muted']))
        for i in ([] if self.kind == 'spark' else indices):
            label_width = min(100, r.width()/max(1, count)) if self.dates else min(80, r.width()/max(1, len(indices)))
            x = min(max(xs[i]-label_width/2, r.left()), r.right()-label_width)
            text = date_text(self.dates[i], year=False) if self.dates else str(self.keys[i])
            p.drawText(QRectF(x, r.bottom()+6, label_width, 22), Qt.AlignmentFlag.AlignCenter, text)
        if self.kind == 'spark':
            for fraction, text in ((0, tr('−5m')), (.5, tr('−2m 30s')), (1, tr('Now'))):
                x = max(r.left(), min(r.left()+r.width()*fraction-30, r.right()-60))
                p.drawText(QRectF(x, r.bottom()+6, 60, 22), Qt.AlignmentFlag.AlignCenter, text)
        # Label only latest for lines, maximum for columns; reserve space above.
        idx = next((i for i in reversed(range(len(ys))) if ys[i] is not None), 0) if self.kind in ('line', 'spark') else max(range(len(ys)), key=lambda i: self.values[i] if self.values[i] is not None else -1)
        if self.values[idx] is not None:
            text = (tr('Latest ') if self.kind in ('line', 'spark') else tr('Max '))+self.display(self.values[idx])
            p.setPen(QColor(TOKENS['muted']))
            p.drawText(QRectF(r.left(), 0, r.width(), 20), Qt.AlignmentFlag.AlignRight, text)
        p.setOpacity(1.0)
        if self.hover is not None and self.hover < len(xs):
            i = self.hover
            p.setPen(QPen(QColor(TOKENS['muted']), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(xs[i], r.top()), QPointF(xs[i], r.bottom()))
            if ys[i] is not None:
                p.setBrush(QColor(TOKENS['text']))
                p.drawEllipse(QPointF(xs[i], ys[i]), 3, 3)

    def draw_line(self, p, xs, ys, rect, area=False, color=None, reveal=1.0):
        clip = rect.adjusted(-2, -2, 2, 2)
        clip.setWidth(clip.width() * reveal)       # lines draw on left to right during the entrance
        p.setClipRect(clip)
        segments, segment = [], []
        for x, y in zip(xs, ys):
            if y is None:
                if segment:
                    segments.append(segment)
                    segment = []
            else:
                segment.append(QPointF(x, y))
        if segment:
            segments.append(segment)
        for segment in segments:
            # Retain extrema and endpoints in each horizontal pixel bucket.
            if len(segment) > rect.width()*2:
                buckets = {}
                for point in segment:
                    buckets.setdefault(int(point.x()), []).append(point)
                segment = [point for group in buckets.values() for point in sorted(
                    [group[0], group[-1], min(group, key=lambda q: q.y()), max(group, key=lambda q: q.y())], key=lambda q: q.x())]
            path = QPainterPath(segment[0])
            for point in segment[1:]:
                path.lineTo(point)
            if area:
                fill = QPainterPath(path)
                fill.lineTo(segment[-1].x(), rect.bottom())
                fill.lineTo(segment[0].x(), rect.bottom())
                fill.closeSubpath()
                shade = QColor(TOKENS['accent'])
                shade.setAlpha(18)
                p.fillPath(fill, shade)
            p.setPen(QPen(QColor(color or TOKENS['accent']), 2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            if len(segment) == 1:
                p.drawPoint(segment[0])
        p.setClipping(False)

    def tooltip(self, index):
        label = date_text(self.dates[index]) if self.dates else str(self.keys[index])
        text = f'{label}\n{self.display(self.values[index])}'
        if self.scope:
            text = self.scope+'\n'+text
        if self.details:
            text += '\n'+self.details[index]
        if self.overlay and self.overlay[index] is not None:
            text += tr('\n7-day average: ')+self.display(self.overlay[index])
        if self.dates and self.dates[index] == self.today:
            text += tr('\nToday · partial day')
        return text

    def mouseMoveEvent(self, event):
        rect = self.plot()
        if self.values and rect.contains(event.position()) and not self.message:
            ratio = (event.position().x()-rect.left())/rect.width()
            index = round(ratio*(len(self.values)-1)) if self.kind in ('line', 'spark') else int(ratio*len(self.values))
            self.hover = min(len(self.values)-1, max(0, index))
            bounded_tooltip(self, event.globalPosition().toPoint(), self.tooltip(self.hover))
        else:
            self.hover = None
            hide_tooltip(self)
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        hide_tooltip(self)
        self.update()
        super().leaveEvent(event)


class Calendar(Plot):
    def __init__(self):
        super().__init__('calendar')
        self.daily = {}
        self.start = self.end = dt.date.today()
        self.setMinimumHeight(162)

    def set_calendar(self, daily, start, end):
        self.daily, self.start, self.end = daily, start, end
        self.origin = start-dt.timedelta(days=start.weekday())
        self.weeks = ((end-self.origin).days//7)+1
        self.setMinimumWidth(max(240, 58+self.weeks*14))
        self.update()
        if daily:
            self.arm_entrance(700)

    def geometry_for_cells(self):
        step = min(22, (self.width()-58)/max(1, self.weeks))
        return 48, 25, step, min(18, step-2)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font())
        left, top, step, size = self.geometry_for_cells()
        u = progress(self.entrance, eased=False)
        p.setPen(QColor(TOKENS['muted']))
        for row in (0, 2, 4):
            p.drawText(QRectF(0, top+row*20, 40, 20), Qt.AlignmentFlag.AlignRight, weekdays()[row])
        previous_label_right = -100
        previous_month = None
        for week in range(self.weeks):
            day = self.origin+dt.timedelta(days=week*7)
            # First in-range day establishes the first month label.
            label_day = max(day, self.start)
            month_key = (label_day.year, label_day.month)
            x = left+week*step
            if month_key != previous_month:
                previous_month = month_key
                if x >= previous_label_right+8:
                    text = str(month(label_day.month))+' '+str(label_day.year) if label_day.month == 1 else month(label_day.month)
                    width = p.fontMetrics().horizontalAdvance(text)
                    if x+width < self.width():
                        p.setPen(QColor(TOKENS['muted']))
                        p.drawText(QRectF(x, 0, width+2, 20), Qt.AlignmentFlag.AlignLeft, text)
                        previous_label_right = x+width
            for row in range(7):
                date = day+dt.timedelta(days=row)
                if not self.start <= date <= self.end:
                    continue
                rect = QRectF(x, top+row*20, size, 17)
                begin = 0.6 * week / max(1, self.weeks - 1)      # cells fade up week by week, left to right
                p.setOpacity(phase(u, begin, begin + 0.4))
                value = self.daily.get(date)
                if value is None:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(TOKENS['border']), .8))
                else:
                    level = min(4, int(value/3600/24*5))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(heat_ramp()[level]))
                p.drawRoundedRect(rect, 2, 2)
                if date == self.today:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(TOKENS['text']), 1))
                    p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 2, 2)

    def mouseMoveEvent(self, event):
        left, top, step, size = self.geometry_for_cells()
        week = int((event.position().x()-left)//step)
        row = int((event.position().y()-top)//20)
        day = self.origin+dt.timedelta(days=week*7+row)
        if event.position().x() >= left and event.position().y() >= top and 0 <= week < self.weeks and 0 <= row < 7 and self.start <= day <= self.end:
            value = self.daily.get(day)
            bounded_tooltip(self, event.globalPosition().toPoint(), date_text(day)+'\n'+(hm(value) if value is not None else tr('No recorded data')))
        else:
            hide_tooltip(self)


class HeatScale(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(25)
        self.intro = Intro(self, 600, 0, motion_reduced)
        self.intro.ready()      # starts the first time the scale is shown
        self.setAccessibleName(tr('Awake hours scale: 0 to 24 hours, five increasing steps. Outline means no recorded data.'))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(font())
        p.setPen(QColor(TOKENS['muted']))
        p.drawText(QRectF(0, 1, 28, 22), Qt.AlignmentFlag.AlignVCenter, tr('0 h'))
        for i, color in enumerate(heat_ramp()):
            p.setOpacity(1.0 if self.intro.done else phase(self.intro.value, i * 0.12, i * 0.12 + 0.52))
            p.fillRect(QRectF(33+i*21, 6, 19, 12), QColor(color))
        p.setOpacity(1.0)
        p.drawText(QRectF(145, 1, 38, 22), Qt.AlignmentFlag.AlignVCenter, tr('24 h'))
        if self.width() >= 360:
            p.drawText(QRectF(195, 1, self.width()-195, 22), Qt.AlignmentFlag.AlignVCenter, tr('Outline = no record'))


def motion_reduced():
    gui = sys.modules.get('lifetime_gui')
    return (os.environ.get('LIFETIME_REDUCE_MOTION') == '1' or
            os.environ.get('REDUCE_MOTION') == '1' or bool(getattr(gui, 'REDUCE_MOTION', False)))


def bounded_tooltip(widget, position, text):
    """Clamp the entire native tooltip to the application window."""
    window = widget.window()
    tip = getattr(window, '_chart_tooltip', None)
    if tip is None:
        tip = QLabel(window)
        tip.setObjectName('chartTooltip')
        tip.setWordWrap(True)
        tip.setStyleSheet(f'background:{TOKENS["track"]}; color:{TOKENS["text"]}; border:1px solid {TOKENS["border"]}; padding:8px; font-size:13px;')
        tip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window._chart_tooltip = tip
    tip.setText(text)
    tip.setFixedWidth(min(320, max(120, window.width()-24)))
    tip.adjustSize()
    point = window.mapFromGlobal(position)
    tip.move(max(8, min(point.x()+12, window.width()-tip.width()-8)),
             max(8, min(point.y()+16, window.height()-tip.height()-8)))
    tip.show()
    tip.raise_()


def hide_tooltip(widget):
    tip = getattr(widget.window(), '_chart_tooltip', None)
    if tip:
        tip.hide()


class RangeSegments(QWidget):
    currentIndexChanged = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.index = 0
        self.indicator = QRectF()
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(260)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.move_indicator)
        self.group = QButtonGroup(self)
        box = QHBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(0)
        self.buttons = []
        for index, key in enumerate(RANGES):
            button = QPushButton(tr(key))
            button.setCheckable(True)
            button.setFixedHeight(36)
            button.setStyleSheet('QPushButton {background:transparent; border:0; padding:4px 12px; font-size:13px;} QPushButton:focus {border:1px solid ' + TOKENS['accent'] + ';} QPushButton:hover {color:' + TOKENS['text'] + ';}')
            self.group.addButton(button, index)
            box.addWidget(button)
            self.buttons.append(button)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.setCurrentIndex)
        self.setAccessibleName(tr('Insights date range; applies to every chart'))

    def move_indicator(self, rect):
        self.indicator = rect
        self.update()

    def setCurrentIndex(self, index):
        if not 0 <= index < len(RANGES):
            return
        self.animation.stop()
        changed = index != self.index
        self.index = index
        self.buttons[index].setChecked(True)
        target = QRectF(self.buttons[index].geometry())
        if motion_reduced() or self.indicator.isEmpty():
            self.move_indicator(target)
        else:
            self.animation.setStartValue(self.indicator)
            self.animation.setEndValue(target)
            self.animation.start()
        if changed:
            self.currentIndexChanged.emit(index)

    def setCurrentText(self, text):
        for index, key in enumerate(RANGES):
            if text in (key, tr(key)):
                self.setCurrentIndex(index)
                break

    def setItemText(self, index, text):
        self.buttons[index].setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.animation.stop()
        self.move_indicator(QRectF(self.buttons[self.index].geometry()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if active_style() == 'glass':
            paint_track(painter, QRectF(self.rect()), PANEL_RADIUS)
            paint_glass(painter, self.indicator, CONTROL_RADIUS, pill=True)
            return
        painter.setBrush(QColor(TOKENS['card']))
        painter.drawRoundedRect(QRectF(self.rect()), PANEL_RADIUS, PANEL_RADIUS)
        painter.setBrush(QColor(TOKENS['track']))
        painter.drawRoundedRect(self.indicator, CONTROL_RADIUS, CONTROL_RADIUS)


class ChartCard(QFrame):
    def __init__(self, title, subtitle, chart):
        super().__init__()
        self.setObjectName('signalCard')
        self.setMinimumWidth(0)
        self.setFixedHeight(310)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(18, 14, 18, 12)
        self.box.setSpacing(6)
        row = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        self.title.setFont(font(18, True))
        self.title.setStyleSheet('font-size:18px; font-weight:600;')
        row.addWidget(self.title, 1)
        self.box.addLayout(row)
        self.subtitle = caption(subtitle)
        self.subtitle.setMinimumHeight(36)
        self.box.addWidget(self.subtitle)
        self.chart = chart
        self.extra = None
        if isinstance(chart, Calendar):
            self.viewport = QScrollArea()
            self.viewport.setFrameShape(QFrame.Shape.NoFrame)
            self.viewport.setWidgetResizable(True)
            self.viewport.setWidget(chart)
            self.viewport.setMinimumHeight(181)
            self.viewport.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.box.addWidget(self.viewport, 1)
            self.extra = HeatScale()
            self.box.addWidget(self.extra)
        else:
            self.viewport = chart
            self.box.addWidget(chart, 1)
        self.table = QTableWidget()
        self.table.setAccessibleName(title+tr(' data table'))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.box.addWidget(self.table, 1)
        self.table.hide()
        self.data_visible = False
        self.headers, self.rows = [], []
        self.restyle_glass()

    def restyle_glass(self):
        """The calendar's scroll area and the data table sit on the card: solid in Classic, see-through on Glass."""
        if isinstance(self.chart, Calendar):
            self.viewport.setStyleSheet(f'QScrollArea, QScrollArea > QWidget > QWidget {{background:{solid(TOKENS["card"])};}}')
        edge = 'border:none; ' if active_style() == 'glass' else ''        # the glass card is the frame
        self.table.setStyleSheet(f'QTableWidget {{{edge}background:{solid(TOKENS["card"])}; gridline-color:{TOKENS["border"]}; font-size:13px;}} QHeaderView::section {{background:{solid(TOKENS["track"])}; color:{TOKENS["muted"]}; border:0; padding:5px;}}')

    def set_table(self, headers, rows):
        self.headers, self.rows = headers, rows
        if self.data_visible:
            self.populate_table()

    def populate_table(self):
        self.table.setUpdatesEnabled(False)
        self.table.setColumnCount(len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setRowCount(len(self.rows))
        for row, values in enumerate(self.rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFont(font(13))
                if col > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.setUpdatesEnabled(True)

    def show_table(self, checked):
        self.viewport.setVisible(not checked)
        if self.extra:
            self.extra.setVisible(not checked)
        self.table.setVisible(checked)
        self.data_visible = checked
        if checked:
            self.populate_table()


class Insights(QWidget):
    def restyle_glass(self):
        self.data_toggle.setStyleSheet('QPushButton:checked {background:' + solid(TOKENS['track']) + ';}')

    def __init__(self):
        super().__init__()
        self.ledger = {}
        self.selection = '30 days'
        self.fingerprint = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        row = QHBoxLayout()
        title = QLabel(tr('Activity'))
        title.setObjectName('pageTitle')
        row.addWidget(title)
        row.addStretch()
        self.ranges = RangeSegments()
        row.addWidget(self.ranges)
        self.data_toggle = QPushButton(tr('Show data'))
        self.data_toggle.setCheckable(True)
        self.data_toggle.setAccessibleName(tr('Show data for all charts'))
        self.data_toggle.setFixedHeight(40)
        self.restyle_glass()
        row.addWidget(self.data_toggle)
        self.ranges.currentIndexChanged.connect(lambda index: self.set_range(RANGES[index]))
        box.addLayout(row)
        figures = QHBoxLayout()
        figures.setSpacing(32)
        self.figures = []
        for key in ('Range', 'Recorded days', 'Awake'):
            group = QVBoxLayout()
            group.setSpacing(4)
            value = QLabel('')
            value.setWordWrap(True)
            value.setFont(font(20))
            value.setStyleSheet('font-size:20px; font-weight:500;')
            group.addWidget(value)
            group.addWidget(caption(tr(key)))
            figures.addLayout(group, 2 if key == 'Range' else 1)
            self.figures.append(value)
        box.addLayout(figures)
        self.counting_toggle = DisclosureButton(tr('How this is counted'))
        self.counting_toggle.setCheckable(True)
        self.counting_toggle.setFixedHeight(36)
        self.counting_toggle.setStyleSheet('QPushButton {text-align:left; background:transparent; border:0; padding:4px 28px 4px 0; font-size:13px;} QPushButton:focus {border-bottom:1px solid ' + TOKENS['accent'] + ';}')
        box.addWidget(self.counting_toggle)
        self.sources = caption('')
        self.source_panel = QWidget()
        source_box = QVBoxLayout(self.source_panel)
        source_box.setContentsMargins(0, 0, 0, 0)
        source_box.addWidget(self.sources)
        self.source_panel.setMaximumHeight(0)
        self.source_panel.hide()
        box.addWidget(self.source_panel)
        self.counting_animation = QVariantAnimation(self)
        self.counting_animation.setDuration(200)
        self.counting_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.counting_animation.valueChanged.connect(lambda height: self.source_panel.setMaximumHeight(round(height)))
        self.counting_animation.finished.connect(self.finish_counting)
        self.counting_toggle.toggled.connect(self.toggle_counting)
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        box.addLayout(self.grid)
        self.heatmap = Calendar()
        self.daily = Plot('daily')
        self.running = Plot('line')
        self.profile = Plot()
        self.weekdays = Plot()
        self.distribution = Plot(unit='sessions')
        self.cards = [
            ChartCard(tr('Calendar · awake hours'), tr('Local days · outlined: today'), self.heatmap),
            ChartCard(tr('Daily awake hours'), tr('Daily hours incl. boot credit · line: trailing 7-day average'), self.daily),
            ChartCard(tr('Cumulative lifetime hours'), tr('Includes imported history and the opening balance'), self.running),
            ChartCard(tr('Hour-of-day profile'), tr('Average awake hours in each local hour slot'), self.profile),
            ChartCard(tr('Weekday average'), tr('Average per recorded weekday; today is a partial day'), self.weekdays),
            ChartCard(tr('Session length distribution'), tr('Retained sessions, grouped by local start date'), self.distribution)]
        for index, card in enumerate(self.cards):
            card.setFixedHeight(330 if index < 2 else 280)
        self.data_toggle.toggled.connect(self.show_data)
        self.columns = 0
        self.set_columns(2)
        self.note = caption(tr('history.note'))
        box.addWidget(self.note)
        self.set_ledger({})
        on_language_change(self.retranslate_content)

    def toggle_counting(self, expanded):
        self.counting_animation.stop()
        self.source_panel.show()
        self.source_panel.layout().activate()
        target = max(self.sources.sizeHint().height(), self.sources.heightForWidth(self.source_panel.width())) if expanded else 0
        if motion_reduced():
            self.source_panel.setMaximumHeight(target)
            self.finish_counting()
        else:
            self.counting_animation.setStartValue(float(self.source_panel.height()))
            self.counting_animation.setEndValue(float(target))
            self.counting_animation.start()

    def finish_counting(self):
        if self.counting_toggle.isChecked():
            self.source_panel.setMaximumHeight(16777215)
        else:
            self.source_panel.hide()

    def show_data(self, checked):
        for card in self.cards:
            card.show_table(checked)

    def retranslate_content(self):
        self.ranges.blockSignals(True)
        for index, key in enumerate(RANGES):
            self.ranges.setItemText(index, tr(key))
        self.ranges.setAccessibleName(tr('Insights date range; applies to every chart'))
        self.ranges.blockSignals(False)
        self.fingerprint = None
        self.set_ledger(self.ledger)

    def set_columns(self, count):
        if self.columns == count:
            return
        for card in self.cards:
            self.grid.removeWidget(card)
        if count == 2:
            positions = [(i//2, i % 2, 1) for i in range(6)]
        else:
            positions = [(i, 0, 1) for i in range(6)]
        for card, (row, col, span) in zip(self.cards, positions):
            self.grid.addWidget(card, row, col, 1, span)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if count == 2 else 0)
        self.columns = count

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_columns(2 if self.width() >= 820 else 1)

    def set_range(self, selection):
        self.selection = selection
        self.fingerprint = None
        self.set_ledger(self.ledger)

    def set_ledger(self, ledger):
        # Refresh polls every second; no aggregation/table rebuild if unchanged.
        fingerprint = repr((ledger.get('daily'), ledger.get('sessions'), ledger.get('hourly'),
                            ledger.get('total_seconds'), ledger.get('baseline_seconds'),
                            ledger.get('imported'), ledger.get('dated_hourly'), self.selection, language(), dt.date.today()))
        self.ledger = ledger
        if fingerprint == self.fingerprint:
            return
        self.fingerprint = fingerprint
        daily = clean_daily(ledger.get('daily', {}))
        start, end = range_bounds(daily, self.selection)
        selected = filter_range(daily, start, end)
        scope = tr('date.range', start=date_text(start), end=date_text(end))
        for label, text in zip(self.figures, (scope, str(len(selected)), hm(sum(selected.values())))):
            label.setText(text)
        from lifetime_reports import source_totals
        totals = source_totals(ledger)
        shown = lambda value: hm(value) if value is not None else tr('Not available')
        self.sources.setText(tr('All-data sources · measured: {measured} · OS logs: {imported} · manual baseline: {baseline}',
            measured=shown(totals['measured_seconds']), imported=shown(totals['imported_seconds']), baseline=shown(totals['baseline_seconds'])))
        estimated = (ledger.get('daily_repair') or {}).get('estimated') or (ledger.get('daily_allocation') or {}).get('estimated')
        provenance = tr('history.estimated') if estimated else tr('history.logged') if ledger.get('daily_repair_version') or ledger.get('daily_allocation') else ''
        self.note.setText(tr('history.note')+('\n'+provenance if provenance else ''))
        self.heatmap.set_calendar(selected, start, end)
        self.cards[0].set_table([tr('Local date'), tr('Awake time')], [(date_text(d), hm(v) if v is not None else tr('No record')) for d, v in date_series(selected, start, end)])
        points = date_series(daily, start-dt.timedelta(days=6), end)
        averages = dict(moving_average(points))
        points = date_series(selected, start, end)
        dates = [d for d, _ in points]
        values = [v/3600 if v is not None else None for _, v in points]
        overlay = [averages[d]/3600 if averages[d] is not None else None for d in dates]
        low = tr('Needs 7 days of data') if len(selected) < 7 else ''
        self.daily.set_data(dates, values, overlay, dates, low)
        self.cards[1].set_table([tr('Local date'), tr('Daily'), tr('7-day average')], [(date_text(d), hm(v) if v is not None else tr('No record'), hm(averages[d]) if averages[d] is not None else tr('Needs 7 consecutive days')) for d, v in points])
        running = cumulative(daily, start, end, ledger.get('total_seconds'), ledger.get('baseline_seconds', 0))
        self.running.marker = live_started(ledger)
        self.running.set_data(dates, [v/3600 for _, v in running], dates=dates, message=low)
        self.cards[2].set_table([tr('Local date'), tr('Lifetime total')], [(date_text(d), hm(v)) for d, v in running])
        boundary = self.running.marker
        boundary_text = tr('live.began', date=date_text(boundary)) if boundary else tr('Live start date unavailable')
        self.cards[2].subtitle.setText(tr('Opening balance included · ')+boundary_text)
        profile, coverage = hour_profile(ledger, start, end, daily)
        self.profile.scope = scope
        self.profile.set_data([tr('time.hour', hour=h) for h in range(24)], [v/3600 if v is not None else None for v in profile], message=low if len(selected) < 7 else '')
        self.cards[3].subtitle.setText(coverage)
        self.cards[3].set_table([tr('Local hour'), tr('Average awake')], [(tr('time.hour', hour=h), hm(v) if v is not None else tr('Unavailable')) for h, v in enumerate(profile)])
        weekday_values = weekday_averages(selected)
        self.weekdays.scope = scope
        self.weekdays.set_data(weekdays(), [v/3600 if v is not None else None for v in weekday_values], message=low)
        self.cards[4].set_table([tr('Weekday'), tr('Average awake')], [(k, hm(v) if v is not None else tr('No record')) for k, v in zip(weekdays(), weekday_values)])
        sessions = filter_sessions(ledger.get('sessions', []), start, end)
        bins = histogram(sessions)
        self.distribution.scope = scope
        self.distribution.set_data([tr(k) for k in BIN_LABELS], bins, message='' if sessions else tr('No recorded sessions in this range'))
        self.distribution.details = [hm(sum(s['seconds'] for s in sessions if next((i for i, lim in enumerate(BIN_LIMITS) if s['seconds'] < lim), 5) == index))+tr(' total awake') for index in range(6)]
        self.cards[5].subtitle.setText(tr('sessions.retained', count=len(sessions)))
        self.cards[5].set_table([tr('Session length'), tr('Sessions'), tr('Total awake time')], [(tr(k), count, hm(sum(s['seconds'] for s in sessions if next((i for i, lim in enumerate(BIN_LIMITS) if s['seconds'] < lim), 5) == index))) for index, (k, count) in enumerate(zip(BIN_LABELS, bins))])
        for card in self.cards:
            card.table.setAccessibleDescription(tr('Selected date range: ')+scope)


class SampleBuffer:
    """Bounded memory only. Monotonic age survives system-clock adjustments."""
    def __init__(self, seconds=300, maxlen=302):
        self.seconds = seconds
        self.samples = deque(maxlen=maxlen)

    def append(self, value, now=None, wall=None):
        now = time.monotonic() if now is None else now
        self.prune(now)
        if self.samples and now-self.samples[-1][0] < 1:
            return
        value = value if valid_seconds(value) else None
        self.samples.append((now, wall or dt.datetime.now().astimezone(), value))

    def prune(self, now=None):
        now = time.monotonic() if now is None else now
        while self.samples and self.samples[0][0] < now-self.seconds:
            self.samples.popleft()


class Sparkline(Plot):
    def __init__(self, unit):
        super().__init__('spark', unit)
        self.buffer = SampleBuffer()
        self.setFixedSize(120, 28)

    def plot(self):
        return QRectF(2, 3, self.width()-4, self.height()-6)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot()
        painter.setPen(QPen(QColor(TOKENS['border']), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if sum(v is not None for v in self.values) < 2:
            return
        maximum = self.scale()
        ys = [rect.bottom()-rect.height()*v/maximum if v is not None else None for v in self.values]
        self.draw_line(painter, self.positions(rect), ys, rect, color=TOKENS['muted'],
                       reveal=progress(self.entrance))

    def positions(self, rect):
        now = time.monotonic()
        return [rect.right()-(now-stamp)/300*rect.width() for stamp, _, _ in self.buffer.samples]

    def refresh(self):
        self.buffer.prune()
        rows = list(self.buffer.samples)
        self.set_data([time_text(wall, seconds=True) for _, wall, _ in rows], [v for _, _, v in rows],
                      message='')

    def mouseMoveEvent(self, event):
        rect = self.plot()
        if self.values and rect.contains(event.position()) and not self.message:
            xs = self.positions(rect)
            if event.position().x() < xs[0]-8 or event.position().x() > xs[-1]+8:
                self.hover = None
                hide_tooltip(self)
                self.update()
                return
            self.hover = min(range(len(xs)), key=lambda i: abs(xs[i]-event.position().x()))
            wall = self.buffer.samples[self.hover][1]
            bounded_tooltip(self, event.globalPosition().toPoint(), datetime_text(wall)+'\n'+self.display(self.values[self.hover]))
        else:
            self.hover = None
            hide_tooltip(self)
        self.update()


class LiveSparklines(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = {}
        self.pending = {}
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(10)
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        self.box.addLayout(self.grid)
        self.charts, self.cards = {}, {}
        for key, title, unit in [('cpu', tr('CPU'), '%'), ('ram', tr('RAM'), '%'),
                                 ('watts', tr('Battery power'), 'W'), ('gpu', tr('GPU power'), 'W'),
                                 ('down', tr('Download'), 'KiB/s'), ('up', tr('Upload'), 'KiB/s')]:
            chart = Sparkline(unit)
            card = ChartCard(title, tr('Waiting for a local reading'), chart)
            card.setFixedHeight(200)
            card.show_table(True)
            chart.setParent(self)
            chart.hide()
            card.subtitle.setMinimumHeight(18)
            self.charts[key], self.cards[key] = chart, card
        self.columns = 0
        self.set_columns(3)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sample)
        self.timer.start(2000)

    def set_columns(self, count):
        if self.columns == count:
            return
        for card in self.cards.values():
            self.grid.removeWidget(card)
        for i, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, i//count, i % count)
        for i in range(3):
            self.grid.setColumnStretch(i, 1 if i < count else 0)
        self.columns = count

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_columns(3 if self.width() >= 1000 else 2 if self.width() >= 620 else 1)

    def set_settings(self, settings):
        self.settings = settings
        for key, category in [('cpu', 'resources'), ('ram', 'resources'), ('watts', 'battery'),
                              ('gpu', 'graphics'), ('down', 'network'), ('up', 'network')]:
            enabled = bool(settings.get(category))
            self.cards[key].setVisible(enabled)
            if not enabled:
                hide_tooltip(self.charts[key])
                self.charts[key].buffer.samples.clear()
                self.charts[key].set_data([], [])
                self.cards[key].set_table([tr('Local time'), self.charts[key].unit], [])
                self.cards[key].table.setRowCount(0)
                self.cards[key].subtitle.setText(tr('Waiting for a local reading'))
                self.pending.pop(key, None)
        self.setVisible(any(settings.get(k) for k in ('resources', 'battery', 'graphics', 'network')))

    def update_values(self, data):
        ram, battery, counters = data.get('ram') or {}, data.get('battery') or {}, data.get('counters') or {}
        gpus = data.get('gpu') or []
        watts = [g.get('watts') for g in gpus]
        values = dict(cpu=data.get('cpu'), ram=ram.get('percent'), watts=battery.get('watts'),
                      gpu=sum(watts) if watts and all(valid_seconds(v) for v in watts) else None,
                      down=counters.get('down')/1024 if valid_seconds(counters.get('down')) else None,
                      up=counters.get('up')/1024 if valid_seconds(counters.get('up')) else None)
        for key, value in values.items():
            self.pending[key] = value

    def sample(self):
        for key, chart in self.charts.items():
            if self.cards[key].isHidden():
                continue
            value = self.pending.get(key)
            chart.buffer.append(value)
            chart.refresh()
            if value is None and not any(v is not None for _, _, v in chart.buffer.samples):
                chart.message = ''
            self.cards[key].subtitle.setText(tr('Not available') if value is None else tr('live.latest', value=value, unit=chart.unit))
        if self.isVisible():
            self.refresh_tables()

    def refresh_tables(self):
        """Build the optional data tables only when opened, keeping samples live while hidden."""
        for key, chart in self.charts.items():
            if self.cards[key].isHidden():
                continue
            self.cards[key].set_table([tr('Local time'), chart.unit], [(date_text(wall.date())+' '+time_text(wall, seconds=True), f'{v:.3f}' if v is not None else tr('Unavailable')) for _, wall, v in chart.buffer.samples])

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_tables()
