"""Small Qt worker bridge: blocking I/O stays outside the interface thread."""
import re

from PyQt6 import sip
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
            result, error = self.function(), ''
        except Exception as failure:
            # Never propagate credential-bearing network exception text into UI/logs. A fixed code the raiser chose
            # (``detail_code``, such as a refusal reason or an installer exit code) may follow: "Refused:power-consent".
            result, error = None, type(failure).__name__
            code = getattr(failure, 'detail_code', None)
            if isinstance(code, str) and re.fullmatch(r'[a-z0-9-]{1,32}', code):
                error += ':' + code
        try:
            self.signals.finished.emit(self.number, result, error)
        except RuntimeError:
            pass    # the owner is gone (window deleted, app exiting): an unhandled error here aborts the process


class Tasks(QObject):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._number = 0
        self._pending = {}

    def submit(self, function):
        if sip.isdeleted(self):
            return None     # a late timer after the owner's window was deleted: connecting would abort the process
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
