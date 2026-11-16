"""Dashboard pieces shared by Lifetime Server and the Lifetime desktop app: status chips, heartbeat tick bars,
capacity bars, a state summary strip, grouped key-value cards, a health badge with its breakdown, an event rail and
empty-state panels, plus the painters that table rows use for the same visuals.

Everything is painted with QPainter from the active theme's tokens, so Glass/Classic and dark/light follow the window.
Nothing animates and nothing repaints unless its data changes; no graphics effects. State is never colour alone: every
chip and rail dot carries a glyph and words. Ideas came from open-source dashboards (see
docs/design/dashboard-research.md); no code, assets or names were taken from them.
"""
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QToolTip,
                             QVBoxLayout, QWidget)
from lifetime_i18n_qt import PlainLabel as QLabel

from lifetime_health import health_score  # the one score, shared with the server (re-exported)
from lifetime_theme import PANEL_RADIUS, active_theme, tokens, plain_tip

KINDS = ('good', 'warning', 'danger', 'info', 'neutral')
_TOKEN = {'good': 'good', 'warning': 'accent', 'danger': 'danger', 'info': 'info', 'neutral': 'muted'}
GLYPH = {'good': 'check', 'warning': 'bang', 'danger': 'cross', 'info': 'info', 'neutral': 'dash'}
_RANK = {None: -1, 'good': 0, 'info': 1, 'neutral': 2, 'warning': 3, 'danger': 4}
VISUAL_ROLE = Qt.ItemDataRole.UserRole + 40      # a table cell's visual: ('chip', kind) or ('bar', fraction, kind)


def color(kind):
    """The theme colour of a state: good, warning (the amber), danger, info or neutral (anything else)."""
    return QColor(tokens()[_TOKEN.get(kind, 'muted')])


def worst(kinds):
    return max(kinds, key=lambda kind: _RANK.get(kind, 2), default=None)


def capacity_kind(used_fraction):
    """Storage and memory: amber under 15 % free, red under 5 % free."""
    if used_fraction is None:
        return 'neutral'
    return 'danger' if used_fraction >= 0.95 else 'warning' if used_fraction >= 0.85 else 'info'


# ---- glyphs: the same 20 px, round-ended monoline grid as the shell's menu symbols ----

def glyph(p, name):
    line = lambda *points: [p.drawLine(QPointF(*a), QPointF(*b)) for a, b in zip(points, points[1:])]
    if name == 'check':
        line((4, 10.5), (8.5, 15), (16, 5))
    elif name == 'cross':
        line((5, 5), (15, 15))
        line((15, 5), (5, 15))
    elif name == 'bang':
        line((10, 3), (10, 12))
        p.drawPoint(QPointF(10, 17))
    elif name == 'info':
        p.drawPoint(QPointF(10, 3.5))
        line((10, 8), (10, 17))
    elif name == 'dash':
        line((4, 10), (16, 10))
    elif name == 'cpu':
        p.drawRoundedRect(QRectF(5, 5, 10, 10), 1.5, 1.5)
        for at in (8, 12):
            line((at, 2), (at, 5)), line((at, 15), (at, 18)), line((2, at), (5, at)), line((15, at), (18, at))
    elif name == 'memory':
        p.drawRoundedRect(QRectF(2, 6, 16, 8), 1.5, 1.5)
        for x in (5, 8.5, 12, 15.5):
            line((x, 14), (x, 17))
    elif name == 'storage':
        p.drawRoundedRect(QRectF(2, 5, 16, 10), 2, 2)
        line((5, 12), (11, 12))
        p.drawPoint(QPointF(15, 12))
    elif name == 'network':
        p.drawRoundedRect(QRectF(7, 2, 6, 5), 1, 1)
        p.drawRoundedRect(QRectF(2, 13, 6, 5), 1, 1)
        p.drawRoundedRect(QRectF(12, 13, 6, 5), 1, 1)
        line((10, 7), (10, 10)), line((5, 13), (5, 10), (15, 10), (15, 13))
    elif name == 'graphics':
        p.drawRoundedRect(QRectF(2, 4, 16, 10), 2, 2)
        line((7, 17), (13, 17)), line((10, 14), (10, 17))
        line((5, 11), (8, 8), (11, 10), (15, 6))
    elif name == 'board':
        p.drawRoundedRect(QRectF(2, 2, 16, 16), 2, 2)
        p.drawRect(QRectF(6, 6, 5, 5))
        line((13, 6), (15, 6)), line((13, 9), (15, 9)), line((6, 14), (15, 14))
    elif name == 'os':
        p.drawRoundedRect(QRectF(2, 3, 16, 14), 2, 2)
        line((2, 7), (18, 7))
        for x in (4.5, 7, 9.5):
            p.drawPoint(QPointF(x, 5))
    elif name == 'battery':
        p.drawRoundedRect(QRectF(2, 6, 14, 8), 1.5, 1.5)
        line((18, 9), (18, 11)), line((5, 10), (10, 10))
    elif name == 'event':
        p.drawEllipse(QRectF(6, 6, 8, 8))
    elif name == 'list':
        for y in (5, 10, 15):
            p.drawPoint(QPointF(3, y))
            line((7, y), (17, y))
    elif name == 'plus':
        line((10, 4), (10, 16)), line((4, 10), (16, 10))
    elif name == 'calendar':
        p.drawRoundedRect(QRectF(2, 4, 16, 14), 2, 2)
        line((2, 8), (18, 8)), line((6, 2), (6, 5)), line((14, 2), (14, 5))
    elif name == 'restart':
        p.drawArc(QRectF(3, 3, 14, 14), 60 * 16, 300 * 16)
        line((13.5, 2), (13.5, 5.5), (17, 5.5))
    elif name == 'longest':
        line((2, 6), (2, 14)), line((18, 6), (18, 14)), line((2, 10), (18, 10))
    elif name == 'average':
        line((2, 18), (18, 18))
        for x, top in ((5, 12), (10, 7), (15, 10)):
            line((x, 18), (x, top))
        line((2, 10), (18, 10))
    else:
        from lifetime_shell import symbol
        symbol(p, name)


def paint_icon(painter, rect, name, colour, width=1.5):
    """Draw glyph `name` scaled into `rect` (square) with a round pen `width` pixels wide on screen."""
    painter.save()
    scale = rect.height() / 20
    painter.translate(rect.left(), rect.top())
    painter.scale(scale, scale)
    pen = QPen(QColor(colour), width / scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    glyph(painter, name)
    painter.restore()


class Icon(QWidget):
    """A glyph as a small widget, e.g. in a card header."""
    def __init__(self, name, size=20, kind=None):
        super().__init__()
        self.name, self.kind = name, kind
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = color(self.kind) if self.kind else QColor(tokens()['muted'])
        paint_icon(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.name, colour, 1.6)


# ---- P1 status chip ----

CHIP_H = 24


def chip_width(metrics, text):
    return 7 + 12 + 6 + metrics.horizontalAdvance(text) + 10


def paint_chip(painter, rect, kind, text):
    """A pill with a state glyph and words, left-aligned and vertically centred in `rect`. Returns its width."""
    metrics = painter.fontMetrics()
    height = min(CHIP_H, rect.height())
    width = min(rect.width(), chip_width(metrics, text))
    pill = QRectF(rect.left(), rect.center().y() - height / 2, width, height).adjusted(0.5, 0.5, -0.5, -0.5)
    tone = color(kind)
    dark = active_theme() == 'dark'
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill, edge = QColor(tone), QColor(tone)
    fill.setAlpha(38 if dark else 30)
    edge.setAlpha(150 if dark else 170)
    painter.setPen(QPen(edge, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
    paint_icon(painter, QRectF(pill.left() + 7, pill.center().y() - 6, 12, 12), GLYPH.get(kind, 'dash'), tone, 2.0)
    painter.setPen(QColor(tokens()['text']))
    room = QRectF(pill.left() + 25, pill.top(), max(0.0, pill.right() - pill.left() - 33), pill.height())
    painter.drawText(room, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                     metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(room.width())))
    painter.restore()
    return width


class StatusChip(QWidget):
    """One state as a pill: glyph + words in the state's colour. The words are its accessible name."""
    def __init__(self, kind='neutral', text=''):
        super().__init__()
        self.kind, self.words = kind, text
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_state(kind, text)

    def text(self):
        return self.words

    def set_state(self, kind, text, tooltip=''):
        if (kind, text) != (self.kind, self.words) or not self.accessibleName():
            self.kind, self.words = kind, text
            self.setAccessibleName(text)
            self.updateGeometry()
            self.update()
        self.setToolTip(tooltip)

    def sizeHint(self):
        return QSize(chip_width(self.fontMetrics(), self.words) + 1, CHIP_H + 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_chip(painter, QRectF(self.rect()), self.kind, self.words)


# ---- P2 heartbeat / uptime ticks ----

def bucket(states, slots):
    """Fit `states` (oldest first) into at most `slots` ticks; the worst state in a bucket wins."""
    if slots <= 0:
        return []
    if len(states) <= slots:
        return list(states)
    size = len(states) / slots
    return [worst(states[int(i * size):max(int(i * size) + 1, int((i + 1) * size))]) for i in range(slots)]


def tick_rects(rect, count, gap=2.0, minimum=3.0):
    slots = max(1, min(count, int((rect.width() + gap) // (minimum + gap))))
    width = (rect.width() - gap * (slots - 1)) / slots
    return [QRectF(rect.left() + i * (width + gap), rect.top(), width, rect.height()) for i in range(slots)]


def paint_ticks(painter, rect, states, gap=2.0):
    """One rounded tick per state, newest on the right; None (no data) is an outline, not a colour."""
    if not states:
        return
    rects = tick_rects(rect, len(states), gap)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for tick, kind in zip(rects, bucket(states, len(rects))):
        if kind is None:
            painter.setPen(QPen(QColor(tokens()['line']), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(tick.adjusted(0.5, 0.5, -0.5, -0.5), 1.5, 1.5)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color(kind))
            painter.drawRoundedRect(tick, 1.5, 1.5)
    painter.restore()


class HeartbeatBar(QWidget):
    """A row of ticks, oldest left, newest right; hovering a tick shows its time and result."""
    def __init__(self, height=26):
        super().__init__()
        self.ticks = []
        self.setFixedHeight(height)
        self.setMinimumWidth(80)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_ticks(self, ticks, summary=''):
        """ticks: [(kind or None, tooltip)], oldest first. `summary` is the accessible sentence."""
        ticks = list(ticks)
        if ticks != self.ticks:
            self.ticks = ticks
            self.update()
        self.setAccessibleName(summary)
        self.setToolTip('' if ticks else summary)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_ticks(painter, QRectF(self.rect()).adjusted(0, 2, 0, -2), [kind for kind, _ in self.ticks])

    def mouseMoveEvent(self, event):
        if not self.ticks:
            return
        rects = tick_rects(QRectF(self.rect()), len(self.ticks))
        x = event.position().x()
        slot = min(range(len(rects)), key=lambda i: abs(rects[i].center().x() - x))
        size = len(self.ticks) / len(rects)
        group = self.ticks[int(slot * size):max(int(slot * size) + 1, int((slot + 1) * size))]
        QToolTip.showText(event.globalPosition().toPoint(), plain_tip('\n'.join(tip for _, tip in group[-4:])), self)


def interval_ticks(intervals, start, end, slots):
    """States per slot from [(kind, start, end or None)] (None = still going at `end`). A slot takes the worst state
    that overlaps it, and None when nothing was recorded then."""
    step = (end - start) / slots
    result = []
    for index in range(slots):
        low, high = start + step * index, start + step * (index + 1)
        result.append(worst([kind for kind, begin, finish in intervals if begin < high and (finish or end) > low]))
    return result


# ---- P3 mini sparkline (a short series beside its value) ----

class MiniSpark(QWidget):
    """A small line with the latest point marked, no axes; for a value whose trend matters (CPU, memory). The path is
    built once per data and size. (lifetime_charts.Sparkline is the live, time-positioned one; this one takes a
    stored series.)"""
    def __init__(self, width=72, height=22):
        super().__init__()
        self.values, self._path, self._key = [], None, None
        self.setFixedSize(width, height)

    def set_values(self, values, description=''):
        values = [v for v in values if v is not None][-120:]
        if values != self.values:
            self.values = values
            self._path = None
            self.update()
        self.setVisible(len(values) >= 2)
        self.setAccessibleName(description)
        self.setToolTip(description)

    def paintEvent(self, event):
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 3, -4, -3)
        if self._path is None or self._key != rect:
            low, high = min(self.values), max(self.values)
            span = (high - low) or 1.0
            points = [QPointF(rect.left() + rect.width() * i / (len(self.values) - 1),
                              rect.bottom() - rect.height() * (v - low) / span) for i, v in enumerate(self.values)]
            self._path, self._key = QPainterPath(points[0]), rect
            for point in points[1:]:
                self._path.lineTo(point)
            self._last = points[-1]
        painter.setPen(QPen(QColor(tokens()['muted']), 1.4))
        painter.drawPath(self._path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens()['accent']))
        painter.drawEllipse(self._last, 2.5, 2.5)


# ---- P5 capacity bar ----

def paint_bar(painter, rect, segments, total, track=True):
    """Segments [(value, kind)] laid end to end over `total`, clipped to a rounded track."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    radius = rect.height() / 2
    path.addRoundedRect(rect, radius, radius)
    if track:
        painter.fillPath(path, QColor(tokens()['track']))
    painter.setClipPath(path)
    x = rect.left()
    for value, kind in segments:
        width = rect.width() * max(0.0, min(1.0, (value or 0) / total)) if total else 0
        painter.fillRect(QRectF(x, rect.top(), width, rect.height()), color(kind))
        x += width
        if width:
            x += 0 if (value, kind) == segments[-1] else 2      # a gap between segments
    painter.restore()


class CapacityBar(QWidget):
    """Used against total (disk, memory), with the name on the left and the exact figures on the right. The bar turns
    amber under 15 % free and red under 5 % free; the words still say it."""
    def __init__(self, thin=False):
        super().__init__()
        self.thin = thin
        self.label, self.detail, self.segments, self.total = '', '', [], 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(6 if thin else 40)

    def value(self):
        """The filled share in thousandths, as the progress bar it replaces reported it."""
        return round(1000 * sum(value or 0 for value, _ in self.segments) / self.total) if self.total else 0

    def set_usage(self, used, total, label='', detail='', description=''):
        fraction = used / total if total and used is not None else None
        self.set_segments([(used or 0, capacity_kind(fraction))], total or 0, label, detail, description)

    def set_segments(self, segments, total, label='', detail='', description=''):
        value = (list(segments), total, label, detail)
        if value != (self.segments, self.total, self.label, self.detail):
            self.segments, self.total, self.label, self.detail = value
            self.update()
        self.setAccessibleName(description or f'{label} {detail}'.strip())

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = QRectF(self.rect())
        if not self.thin:
            painter.setPen(QColor(tokens()['text']))
            top = QRectF(rect.left(), rect.top(), rect.width(), 22)
            metrics = painter.fontMetrics()
            right = metrics.horizontalAdvance(self.detail)
            painter.drawText(top.adjusted(0, 0, -right - 12, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(self.label, Qt.TextElideMode.ElideRight, int(top.width() - right - 12)))
            painter.setPen(QColor(tokens()['muted']))
            painter.drawText(top, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.detail)
            rect = QRectF(rect.left(), rect.bottom() - 9, rect.width(), 8)
        paint_bar(painter, rect, self.segments, self.total)


# ---- P15 state summary strip ----

class SummaryStrip(QWidget):
    """How bad is it right now: one proportional bar of states, with a chip per state (count + words) under it.
    Clicking a chip, or Enter on the focused one, emits `picked(key)`."""
    picked = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.counts, self.focus, self._hits = [], 0, []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(CHIP_H + 26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_counts(self, counts, description=''):
        """counts: [(key, kind, words, number)] in display order."""
        counts = [item for item in counts]
        if counts != self.counts:
            self.counts = counts
            self.focus = min(self.focus, max(0, len(counts) - 1))
            self.update()
        self.setAccessibleName(description or ', '.join(f'{words} {number}' for _, _, words, number in counts))

    def paintEvent(self, event):
        painter = QPainter(self)
        total = sum(number for *_, number in self.counts)
        bar = QRectF(0, 1, self.width(), 8)
        if total:
            paint_bar(painter, bar, [(number, kind) for _, kind, _, number in self.counts if number], total)
        else:
            paint_bar(painter, bar, [], 1)
        self._hits = []
        x = 0.0
        metrics = painter.fontMetrics()
        for index, (key, kind, words, number) in enumerate(self.counts):
            text = f'{number} {words}'
            width = chip_width(metrics, text)
            rect = QRectF(x, 18, width, CHIP_H)
            paint_chip(painter, rect, kind, text)
            if self.hasFocus() and index == self.focus:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(tokens()['accent']), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), CHIP_H / 2 + 2, CHIP_H / 2 + 2)
            self._hits.append((rect, key))
            x += width + 10

    def mousePressEvent(self, event):
        for rect, key in self._hits:
            if rect.adjusted(-4, -20, 4, 4).contains(event.position()):
                self.picked.emit(key)
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.counts:
            self.focus = (self.focus + (1 if event.key() == Qt.Key.Key_Right else -1)) % len(self.counts)
            self.update()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.counts:
            self.picked.emit(self.counts[self.focus][0])
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)


# ---- P11 key-value card ----

class Tile(QFrame):
    """A quiet rounded tile inside a panel (the theme's tile colours), painted here so the application stylesheet
    stays as it is."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        painter.setPen(QPen(QColor(t['tile_border']), 1))
        painter.setBrush(QColor(t['tile']))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), PANEL_RADIUS, PANEL_RADIUS)


class InfoCard(Tile):
    """A titled group of readings with an icon, an optional headline value, and muted key / strong value rows.
    Rows without data are left out instead of printing a column of 'Not available'."""
    def __init__(self, icon, title, columns=1):
        super().__init__()
        self.setObjectName('infoCard')
        self.columns = columns
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(10)
        self.icon = Icon(icon, 22)
        header.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel(title)
        self.title.setObjectName('infoCardTitle')
        self.title.setWordWrap(True)
        self.title.setStyleSheet('font-size:15px; font-weight:600;')
        header.addWidget(self.title, 1, Qt.AlignmentFlag.AlignVCenter)
        self.headline = QLabel()
        self.headline.setObjectName('infoCardHeadline')
        self.headline.setStyleSheet('font-size:20px; font-weight:600;')
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.headline.hide()
        header.addWidget(self.headline, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(header)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        box.addLayout(self.grid)
        self.extra = QVBoxLayout()
        self.extra.setContentsMargins(0, 0, 0, 0)
        self.extra.setSpacing(8)
        box.addLayout(self.extra)
        box.addStretch(1)       # cards in one grid row share a height; spare room goes below the rows
        self.rows = 0
        self.pairs = []

    def set_headline(self, text, tooltip=''):
        self.headline.setText(text or '')
        self.headline.setVisible(bool(text))
        self.headline.setToolTip(tooltip)

    def add_row(self, key, value):
        """`value` is a QLabel the caller keeps (so it can keep updating it); returns the key label."""
        label = QLabel(key)
        label.setObjectName('muted')
        label.setWordWrap(True)
        label.setStyleSheet('font-size:13px;')
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setMinimumWidth(0)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        value.setAccessibleName(f'{self.title.text()} / {key}')
        index = self.rows
        row, column = divmod(index, self.columns)
        self.grid.addWidget(label, row * 2, column)
        self.grid.addWidget(value, row * 2 + 1, column)
        self.rows += 1
        self.pairs.append((label, value))
        return label

    def text(self):
        """The card as plain lines, 'Key: value', then any notes below the rows (what a screen reader is given)."""
        lines = [self.title.text()] + [f'{key.text()}: {value.text()}' for key, value in self.pairs if not value.isHidden()]
        lines += [widget.text() for widget in self.findChildren(QLabel) if widget.parentWidget() is self
                  and widget.text() and not widget.isHidden() and self.extra.indexOf(widget) >= 0]
        return '\n'.join(line for line in lines if line)


# ---- P10 health badge ----

def score_kind(score):
    return 'neutral' if score is None else 'good' if score >= 90 else 'warning' if score >= 50 else 'danger'


class ScoreRing(QWidget):
    def __init__(self, size=76):
        super().__init__()
        self.score = None
        self.setFixedSize(size, size)

    def set_score(self, score):
        if score != self.score:
            self.score = score
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        pen = QPen(QColor(tokens()['track']), 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)
        if self.score is not None:
            pen.setColor(color(score_kind(self.score)))
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, -round(360 * 16 * self.score / 100))
        font = painter.font()
        font.setPixelSize(24)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(tokens()['text'] if self.score is not None else tokens()['muted']))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, '–' if self.score is None else str(self.score))


class HealthBadge(QWidget):
    """One answer (a 0-100 score in a ring, or a dash when unknown) with the reasons beside it: one chip per factor.
    `explain` is the sentence saying how the score is computed; it is the caption and the tooltip."""
    def __init__(self):
        super().__init__()
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        self.ring = ScoreRing()
        box.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignTop)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.verdict = QLabel()
        self.verdict.setStyleSheet('font-size:16px; font-weight:600;')
        side.addWidget(self.verdict)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(8)
        side.addLayout(self.chips)
        self.caption = QLabel()
        self.caption.setObjectName('muted')
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet('font-size:12px;')
        side.addWidget(self.caption)
        box.addLayout(side, 1)
        self.score = None

    def set_factors(self, factors, verdicts, explain):
        """factors: [(name, kind or None, short value)]; verdicts: words for good / warning / danger / unknown."""
        self.score = health_score(factors)
        kind = score_kind(self.score)
        self.ring.set_score(self.score)
        self.verdict.setText(verdicts[kind if self.score is not None else 'neutral'])
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name, factor_kind, value in factors:
            chip = StatusChip(factor_kind or 'neutral', f'{name}: {value}')
            self.chips.addWidget(chip)
        self.chips.addStretch()
        self.caption.setText(explain)
        self.setToolTip(explain)
        self.setAccessibleName(self.verdict.text() + ('' if self.score is None else f' {self.score}/100'))
        self.setAccessibleDescription('; '.join(f'{name}: {value}' for name, _, value in factors) + '. ' + explain)


# ---- P9 event rail ----

class EventRail(QWidget):
    """A vertical rail, newest at the top: a state-coloured dot with a glyph per event, the event's words and its
    time. Paints only rows; height follows the number of events."""
    ROW = 44

    def __init__(self):
        super().__init__()
        self.events = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def set_events(self, events, title=''):
        """events: [(kind, words, time text, detail)] in display order."""
        events = [tuple(event) for event in events]
        if events != self.events:
            self.events = events
            self.setFixedHeight(self.ROW * len(events))
            self.update()
        self.setAccessibleName(title)
        self.setAccessibleDescription(self.text())

    def text(self):
        return '\n'.join(' · '.join(part for part in (when, words, detail) if part) for _, words, when, detail in self.events)

    def paintEvent(self, event):
        if not self.events:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        metrics = painter.fontMetrics()
        painter.setPen(QPen(QColor(t['line']), 2))
        painter.drawLine(QPointF(11, self.ROW / 2), QPointF(11, self.ROW * (len(self.events) - 0.5)))
        small = painter.font()
        small.setPixelSize(12)
        for index, (kind, words, when, detail) in enumerate(self.events):
            top = index * self.ROW
            centre = QPointF(11, top + self.ROW / 2)
            tone = color(kind)
            painter.setPen(QPen(tone, 1.5))
            painter.setBrush(QColor(t['card']))
            painter.drawEllipse(centre, 9, 9)
            paint_icon(painter, QRectF(centre.x() - 5.5, centre.y() - 5.5, 11, 11), GLYPH.get(kind, 'dash'), tone, 1.8)
            painter.setPen(QColor(t['text']))
            text_rect = QRectF(30, top + 4, self.width() - 30, 20)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(words, Qt.TextElideMode.ElideRight, int(text_rect.width())))
            painter.save()
            painter.setFont(small)
            painter.setPen(QColor(t['muted']))
            line = ' · '.join(part for part in (when, detail) if part)
            painter.drawText(QRectF(30, top + 23, self.width() - 30, 17), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(small).elidedText(line, Qt.TextElideMode.ElideRight, int(self.width() - 30)))
            painter.restore()


# ---- P16 empty state ----

EMPTY_GLYPH = {'clear': ('check', 'good'), 'setup': ('plus', 'info'), 'error': ('bang', 'danger'), 'off': ('dash', 'neutral')}


class EmptyState(QFrame):
    """Why a list is empty, in one sentence: all clear, nothing set up yet, could not load, or switched off. One
    optional action button."""
    def __init__(self, kind='clear', title='', text='', action=''):
        super().__init__()
        self.setObjectName('emptyState')
        box = QHBoxLayout(self)
        box.setContentsMargins(4, 16, 4, 16)
        box.setSpacing(16)
        self.icon = Icon('check', 36)
        box.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        side = QVBoxLayout()
        side.setSpacing(4)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet('font-size:16px; font-weight:600;')
        self.body = QLabel()
        self.body.setObjectName('muted')
        self.body.setWordWrap(True)
        self.button = QPushButton()
        self.button.hide()
        self.clicked = self.button.clicked
        side.addWidget(self.title)
        side.addWidget(self.body)
        side.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        box.addLayout(side, 1)
        self.set_state(kind, title, text, action)

    def click(self):
        self.button.click()

    def text(self):
        return '\n'.join(part for part in (self.title.text(), self.body.text()) if part)

    def set_state(self, kind, title, text='', action=''):
        self.kind = kind
        self.icon.name, self.icon.kind = EMPTY_GLYPH.get(kind, EMPTY_GLYPH['off'])
        self.icon.update()
        self.title.setText(title)
        self.body.setText(text)
        self.body.setVisible(bool(text))
        self.button.setText(action)
        self.button.setVisible(bool(action))
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)


# ---- table cells (used by the server's row delegate) ----

def paint_cell(painter, rect, visual, text):
    """Draw a table cell's visual. ('chip', kind): the cell's text as a chip. ('bar', fraction, kind): a slim bar
    with the text after it."""
    rect = QRectF(rect).adjusted(8, 0, -8, 0)
    if visual[0] == 'chip':
        paint_chip(painter, rect, visual[1], text)
    elif visual[0] == 'bar':
        metrics = painter.fontMetrics()
        room = metrics.horizontalAdvance(text) + 10
        bar = QRectF(rect.left(), rect.center().y() - 3, max(24.0, min(90.0, rect.width() - room)), 6)
        paint_bar(painter, bar, [(visual[1], visual[2])], 1)
        painter.save()
        painter.setPen(QColor(tokens()['text']))
        painter.drawText(QRectF(bar.right() + 10, rect.top(), rect.right() - bar.right() - 10, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()
