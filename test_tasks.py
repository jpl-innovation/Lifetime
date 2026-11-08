import threading
import time
import unittest
import tempfile
from pathlib import Path

from qt_test_support import qt_app
from lifetime_tasks import Tasks


class TaskTests(unittest.TestCase):
    def wait(self, values):
        deadline = time.monotonic() + 5
        while not values and time.monotonic() < deadline:
            qt_app().processEvents()
            time.sleep(0.005)
        self.assertTrue(values)

    def test_worker_and_result_thread(self):
        app = qt_app()
        owner = threading.get_ident()
        tasks = Tasks()
        values = []
        tasks.completed.connect(lambda number, result: values.append((number, result, threading.get_ident())))
        number = tasks.submit(threading.get_ident)
        self.wait(values)
        self.assertEqual(values[0][0], number)
        self.assertNotEqual(values[0][1], owner)
        self.assertEqual(values[0][2], owner)
        self.assertFalse(tasks._pending)

    def test_errors_redacted(self):
        app = qt_app()
        tasks = Tasks()
        values = []
        tasks.failed.connect(lambda number, error: values.append(error))
        def fail():
            raise ValueError('credential-secret')
        tasks.submit(fail)
        self.wait(values)
        self.assertEqual(values, ['ValueError'])

    def test_pdf_export_in_worker(self):
        app = qt_app()
        from lifetime_reports import ensure_report_font, write_pdf
        from lifetime_server_reports import fleet_report
        font = ensure_report_font()
        tasks = Tasks()
        result, errors = [], []
        tasks.completed.connect(lambda *_: result.append(True))
        tasks.failed.connect(lambda _, error: errors.append(error))
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / 'worker.pdf'
            tasks.submit(lambda: write_pdf(fleet_report([]), destination, font=font))
            deadline = time.monotonic() + 5
            while not result and not errors and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertEqual(errors, [])
            self.assertTrue(result)
            self.assertIn(b'/FontFile', destination.read_bytes())


if __name__ == '__main__':
    unittest.main()
