"""The server window's end of the local administration stream.

One WebSocket to 127.0.0.1 (same bearer token as the admin API). The server pushes each computer's live
sample and online/offline changes as they happen, so the window no longer waits for a poll. If the stream
is down the window keeps using the admin API exactly as before.
"""
import random
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from websockets.sync.client import connect

import lifetime_ws as ws


class AdminStream(QObject):
    received = pyqtSignal(object)   # {'type': 'telemetry' | 'status' | 'ack', ...}
    changed = pyqtSignal(bool)      # connected or not

    def __init__(self, directory, port, parent=None):
        super().__init__(parent)
        self.directory, self.port = Path(directory), port
        self.connected = False
        self._watch, self._sent = None, object()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name='lifetime-admin-stream', daemon=True)

    def start(self):
        self._thread.start()

    def watch(self, device_id):
        """Tell the server which computer is open, so it sends that one faster."""
        if device_id != self._watch:
            self._watch = device_id
            self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _set(self, value):
        if value != self.connected:
            self.connected = value
            self.changed.emit(value)

    def _run(self):
        failures = 0
        while not self._stop.is_set():
            try:
                token = (self.directory / 'admin-token').read_text(encoding='ascii').strip()
                with connect(f'ws://127.0.0.1:{self.port}/v1/stream', additional_headers={'Authorization': 'Bearer ' + token},
                             open_timeout=3, ping_interval=10, ping_timeout=10, close_timeout=1,
                             max_size=ws.MAX_FRAME * 8, compression=None, proxy=None, user_agent_header=None) as connection:
                    self._set(True)
                    failures = 0
                    self._sent, resend = object(), 0.0
                    while not self._stop.is_set():
                        now = time.monotonic()
                        if self._watch != self._sent or now >= resend:      # also a keep-alive: a watch lapses after 20 s
                            connection.send(ws.envelope('status', dict(watch=self._watch)))
                            self._sent, resend = self._watch, now + 5
                        try:
                            message = ws.parse(connection.recv(timeout=.5))
                        except TimeoutError:
                            continue
                        if message['type'] in ('telemetry', 'status', 'ack') and isinstance(message.get('data'), dict):
                            self.received.emit(message['data'])
            except Exception:
                pass
            self._set(False)
            failures = min(failures + 1, 6)
            self._wake.wait(min(10, 2 ** failures * .25) * random.uniform(.8, 1.2))     # the window polls meanwhile
            self._wake.clear()
