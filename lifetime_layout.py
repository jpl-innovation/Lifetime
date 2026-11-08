"""Native rows that wrap whole controls instead of compressing their boxes."""
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtWidgets import QLayout, QWidgetItem, QSpacerItem, QSizePolicy


class ControlRow(QLayout):
    """Flow at each item's full size hint, with Qt height-for-width support.

    Expanding fields/panels share spare space. Spacers keep trailing actions
    aligned on wide windows and disappear at a wrap. No control is squashed to
    make a row fit, including after live translation or a display-scale change.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._stretch = {}
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(12)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def addWidget(self, widget, stretch=0, alignment=Qt.AlignmentFlag(0)):
        self.addChildWidget(widget)
        item = QWidgetItem(widget)
        item.setAlignment(alignment)
        self._stretch[id(item)] = stretch
        self.addItem(item)

    def addLayout(self, layout, stretch=0):
        self.addChildLayout(layout)
        self._stretch[id(layout)] = stretch
        self.addItem(layout)

    def addStretch(self, stretch=1):
        item = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._stretch[id(item)] = stretch
        self.addItem(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        if 0 <= index < self.count():
            item = self._items.pop(index)
            self._stretch.pop(id(item), None)
            self.invalidate()
            return item

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left+right, top+bottom)

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect, apply):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        available = max(1, area.width())
        lines, line, used = [], [], 0
        for item in self._items:
            if item.isEmpty() and not item.spacerItem():
                continue
            width = min(available, max(item.minimumSize().width(), item.sizeHint().width()))
            gap = self.spacing() if line and not item.spacerItem() else 0
            if line and used + gap + width > available:
                lines.append(line)
                line, used, gap = [], 0, 0
            line.append((item, width))
            used += gap + width
        if line:
            lines.append(line)
        y = area.y()
        for line in lines:
            real = [item for item, _ in line if not item.spacerItem()]
            spare = max(0, available - sum(width for _, width in line) - max(0, len(real)-1)*self.spacing())
            weights = [self._stretch.get(id(item), 0) or int(bool(item.expandingDirections() & Qt.Orientation.Horizontal)) for item,_ in line]
            total = sum(weights)
            widths = [min(item.maximumSize().width(), width + (spare*weight//total if total else 0)) for (item,width),weight in zip(line,weights)]
            heights = [max(item.minimumSize().height(), item.heightForWidth(width) if item.hasHeightForWidth() else item.sizeHint().height()) for (item,_),width in zip(line,widths)]
            height = max(heights, default=0)
            x = area.x()
            for (item,_),width in zip(line,widths):
                if apply:
                    item.setGeometry(QRect(x, y, width, height))
                x += width + (0 if item.spacerItem() else self.spacing())
            y += height + self.spacing()
        return max(0, y-area.y()-self.spacing()) + top + bottom
