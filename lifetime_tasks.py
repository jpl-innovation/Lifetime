"""Small Qt worker bridge: blocking I/O stays outside the interface thread."""
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal, pyqtSlot


class _Signals(QObject):
    finished = pyqtSignal(int, object, str)


class _Task(QRunnable):
    def __init__(self, number, function):
        super().__init__()
        self.number = number
        self.function = function
        self.signals = _Signals()

    def run(self):
        try:
            result = self.function()
        except Exception as error:
            # Never propagate credential-bearing network exception text into UI/logs.
            self.signals.finished.emit(self.number, None, type(error).__name__)
        else:
            self.signals.finished.emit(self.number, result, '')


class Tasks(QObject):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._number = 0
        self._pending = {}

    def submit(self, function):
        self._number += 1
        number = self._number
        task = _Task(number, function)
        self._pending[number] = task
        task.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(task)
        return number

    @pyqtSlot(int, object, str)
    def _finished(self, number, result, error):
        self._pending.pop(number, None)
        if error:
            self.failed.emit(number, error)
        else:
            self.completed.emit(number, result)
