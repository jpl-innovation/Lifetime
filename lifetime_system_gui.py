"""Shared, responsive hardware specification sheet for desktop and server."""
import copy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QVBoxLayout, QSizePolicy, QWidget, QToolButton, QLayout
from lifetime_i18n_qt import QFrame, QLabel
from lifetime_i18n import tr, on_language_change, language
from lifetime_glass import glass
from lifetime_system import sections


class HardwarePanel(QFrame):
    def __init__(self, remote=False):
        super().__init__()
        self.setObjectName('insetPanel')
        self.setMinimumWidth(0)
        if remote:
            glass(self)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 20, 20, 20)
        box.setSpacing(6)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        title = QLabel(tr('Hardware details'))
        title.setStyleSheet('font-size:18px; font-weight:600;')
        box.addWidget(title)
        note = QLabel(tr('Hardware inventory refreshes about every six hours'))
        note.setWordWrap(True)
        note.setStyleSheet('color:#9FB0CC; font-size:13px;')
        box.addWidget(note)
        self.body = QWidget()
        self.body.setMinimumWidth(0)
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(0, 16, 0, 0)
        self.content.setSpacing(0)
        self.content.setAlignment(Qt.AlignmentFlag.AlignTop)
        box.addWidget(self.body)
        self.fields, self.forms, self.data = [], [], {}
        self._expanded = {}
        self._language = None
        self.set_data({})
        on_language_change(self.retranslate)

    def retranslate(self):
        if hasattr(self, 'data'):
            self.set_data(self.data)

    @staticmethod
    def text(value, muted=False):
        label = QLabel(value)
        label.setMinimumWidth(0)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if muted:
            label.setStyleSheet('color:#9FB0CC; font-size:13px;')
        return label

    def set_data(self, data):
        data = data or {}
        if self._language == language() and self.data == data:
            return
        self.data, self._language = copy.deepcopy(data), language()
        # Ordinary dashboard refreshes reuse the specification widgets.
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self.fields, self.forms = [], []
        for index, (heading, groups) in enumerate(sections(data, tr)):
            section = QWidget()
            section.setMinimumWidth(0)
            box = QVBoxLayout(section)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
            if index:
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet('background:#22345A; border:none;')
                box.addWidget(line)
                box.addSpacing(20)
            key = getattr(heading, 'key', str(heading))
            title = QToolButton()
            title.setText(str(heading).replace('&', '&&'))
            title.setObjectName('hardwareSectionTitle')
            title.setCheckable(True)
            title.setChecked(self._expanded.get(key, True))
            title.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            title.setStyleSheet('QToolButton {background:transparent; font-size:16px; font-weight:600; border:1px solid transparent; padding:3px 0;} QToolButton:focus {border-color:#9FB0CC;}')
            title.setAccessibleName(heading)
            box.addWidget(title)
            body = QWidget()
            body.setMinimumWidth(0)
            body_box = QVBoxLayout(body)
            body_box.setContentsMargins(0, 10, 0, 0)
            body_box.setSpacing(0)
            box.addWidget(body)
            def expand(checked, body=body, button=title, key=key):
                self._expanded[key] = checked
                button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
                body.setVisible(checked)
            title.toggled.connect(expand)
            expand(title.isChecked())
            box = body_box
            reasons = []
            for group_index, (name, entries) in enumerate(groups):
                if group_index:
                    box.addSpacing(16)
                if name:
                    name_label = self.text(name)
                    name_label.setStyleSheet('font-size:15px; font-weight:600;')
                    box.addWidget(name_label)
                    box.addSpacing(6)
                form = QFormLayout()
                form.setContentsMargins(0, 0, 0, 0)
                form.setHorizontalSpacing(20)
                form.setVerticalSpacing(7)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                self.forms.append(form)
                for key, value, reason in entries:
                    label, text = self.text(key, True), self.text(value, bool(reason))
                    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
                    label.setMaximumWidth(156)
                    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    text.setAccessibleName(str(heading)+' / '+str(name)+' / '+str(key))
                    text.setAccessibleDescription(reason)
                    text.setToolTip(reason)
                    form.addRow(label, text)
                    self.fields.append((label, text))
                    if reason and reason not in reasons:
                        reasons.append(reason)
                box.addLayout(form)
            if reasons:
                box.addSpacing(10)
                explanation = self.text(tr('Unavailable readings: {reasons}', reasons='; '.join(reasons)), True)
                explanation.setObjectName('hardwareUnavailable')
                box.addWidget(explanation)
            box.addSpacing(20)
            self.content.addWidget(section)
        self._fit_columns()

    def _fit_columns(self):
        narrow = self.width() < 460
        policy = QFormLayout.RowWrapPolicy.WrapAllRows if narrow else QFormLayout.RowWrapPolicy.DontWrapRows
        for form in self.forms:
            form.setRowWrapPolicy(policy)
        for label, _ in self.fields:
            label.setMinimumWidth(0 if narrow else 156)
            label.setMaximumWidth(16777215 if narrow else 156)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()
