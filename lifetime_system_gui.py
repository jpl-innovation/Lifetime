"""Shared hardware details panel for desktop and remote device System pages."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QVBoxLayout, QSizePolicy
from lifetime_i18n_qt import QFrame, QLabel
from lifetime_i18n import tr, on_language_change
from lifetime_glass import glass
from lifetime_system import rows


class HardwarePanel(QFrame):
    def __init__(self, remote=False):
        super().__init__()
        self.setObjectName('insetPanel')
        self.setMinimumWidth(0)
        if remote:
            glass(self)
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        box.setSpacing(8)
        title = QLabel(tr('Hardware details'))
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        title.setStyleSheet('font-size:18px; font-weight:600;')
        box.addWidget(title)
        note = QLabel(tr('Hardware inventory refreshes about every six hours'))
        note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        note.setWordWrap(True)
        note.setObjectName('caption')
        box.addWidget(note)
        self.form = QFormLayout()
        self.form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setVerticalSpacing(12)
        box.addLayout(self.form)
        self.fields, self.data = [], {}
        self.set_data({})
        on_language_change(self.retranslate)

    def retranslate(self):
        self.set_data(self.data)

    def set_data(self, data):
        self.data = data or {}
        values = rows(self.data, tr)
        if not self.fields:
            for key, value in values:
                label, text = QLabel(key), QLabel(value)
                label.setObjectName('caption')
                label.setStyleSheet('font-size:13px;')
                for widget in (label, text):
                    widget.setWordWrap(True)
                    widget.setMinimumWidth(0)
                    widget.setTextFormat(Qt.TextFormat.PlainText)
                    widget.setSizePolicy(QSizePolicy.Policy.Preferred if widget is label else QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                text.setAccessibleName(key)
                self.form.addRow(label)
                self.form.addRow(text)
                self.fields.append((label, text))
        else:
            for (label, text), (key, value) in zip(self.fields, values):
                label.setText(key)
                text.setText(value)
                text.setAccessibleName(key)
