"""Qt text bindings. Keep source keys so open widgets relabel without rebuilding."""
from PyQt6 import QtWidgets
from PyQt6.QtCore import QTranslator
from lifetime_i18n import EN, Text, tr, on_language_change


class NativeTranslator(QTranslator):
    """Use the same catalogue for Qt's standard dialog buttons and edit menus."""
    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        return str(tr(source)) if source in EN else ''

    def retranslate(self):
        app = QtWidgets.QApplication.instance()
        app.removeTranslator(self)
        app.installTranslator(self)


def native_translator():
    app = QtWidgets.QApplication.instance()
    if not hasattr(app, '_lifetime_translator'):
        app._lifetime_translator = NativeTranslator(app)
        app.installTranslator(app._lifetime_translator)
        on_language_change(app._lifetime_translator.retranslate)


class Localized:
    def __init__(self, *args, **kwargs):
        self._text_bindings = {}
        super().__init__(*args, **kwargs)
        native_translator()
        if args and isinstance(args[0], Text) and hasattr(self, 'setText'):
            self._text_bindings['setText'] = args[0]
        on_language_change(self.retranslate)

    def retranslate(self):
        for method, value in list(self._text_bindings.items()):
            getattr(self, method)(tr(value.key, **value.fmt))
        self.update()


def setter(name):
    def set_value(self, value):
        if isinstance(value, Text):
            self._text_bindings[name] = value
        else:
            self._text_bindings.pop(name, None)
        getattr(super(Localized, self), name)(value)
    return set_value


for _method in ('setText', 'setWindowTitle', 'setToolTip', 'setAccessibleName', 'setAccessibleDescription', 'setSuffix'):
    setattr(Localized, _method, setter(_method))

# These are ordinary PyQt6 widgets with a small, shared text-binding mixin.
for _name in ('QLabel', 'QPushButton', 'QCheckBox', 'QRadioButton', 'QWidget',
              'QFrame', 'QDialog', 'QDoubleSpinBox', 'QTableWidget'):
    globals()[_name] = type(_name, (Localized, getattr(QtWidgets, _name)), {'__module__': __name__})
