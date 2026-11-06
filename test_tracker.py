"""Run directly with python3 test_tracker.py; no third-party dependencies."""
import datetime as dt
from unittest.mock import patch
import json
import subprocess
import sys
import time
from pathlib import Path
import tempfile
from functools import wraps

import lifetime_tracker as tracker


def fake_boot_history(function):
    @wraps(function)
    def run(*args, **kwargs):
        with patch('lifetime_daily.boot_history', return_value=(None, [], True)):
            return function(*args, **kwargs)
    return run


class FakeClock:
    def __init__(self, tick=100):
        self.tick = tick

    def advance(self, seconds):
        self.tick += seconds
        return self.tick


@fake_boot_history
def test_samples():
    ledger = tracker.new_ledger()
    clock = FakeClock()
    tracker.sample(ledger, clock.tick, 'boot-a')
    assert ledger['boots'] == 1
    assert ledger['total_seconds'] == 100
    tracker.sample(ledger, clock.advance(30), 'boot-a')
    tracker.sample(ledger, clock.advance(30), 'boot-a')
    assert ledger['total_seconds'] == 160
    assert ledger['current_session_seconds'] == 160
    # A real suspend does not advance the selected awake clock.
    tracker.sample(ledger, clock.advance(0), 'boot-a', '2099-01-01T00:00:00+00:00')
    assert ledger['total_seconds'] == 160
    # Defensive handling of a spurious jump; never count the gap.
    tracker.sample(ledger, clock.advance(3600), 'boot-a')
    assert ledger['total_seconds'] == 160
    assert ledger['current_session_seconds'] == 0
    assert ledger['longest_session_seconds'] == 160
    tracker.sample(ledger, clock.advance(30), 'boot-a')
    assert ledger['total_seconds'] == 190
    # Linux boot identity detects reboot even if new uptime is larger.
    tracker.sample(ledger, clock.advance(30), 'boot-b')
    assert ledger['boots'] == 2
    assert ledger['total_seconds'] == 4010
    # Windows has no boot id; a backwards awake clock detects reboot.
    tracker.sample(ledger, 5, None)
    assert ledger['boots'] == 3
    tracker.sample(ledger, 35, None)
    assert ledger['total_seconds'] == 4045
    tracker.sample(ledger, 335, None)
    assert ledger['total_seconds'] == 4345  # five minutes is allowed


@fake_boot_history
def test_persistence_and_baseline():
    with tempfile.TemporaryDirectory() as directory:
        ledger = tracker.new_ledger()
        tracker.sample(ledger, 100, 'a')
        tracker.sample(ledger, 130, 'a')
        tracker.save(directory, ledger, True)
        restored = tracker.read_ledger(directory)
        tracker.sample(restored, 160, 'a')
        assert restored['boots'] == 1
        assert restored['current_session_seconds'] == 160
        tracker.save(directory, restored, True)
        status_before = (Path(directory) / 'status.json').read_text()
        changed = tracker.set_baseline(12.5, directory)
        assert changed['baseline_seconds'] == 45000
        assert changed['total_seconds'] == 160
        assert tracker.read_ledger(directory) == changed
        assert (Path(directory) / 'status.json').read_text() == status_before
        assert '12 h 32 m awake' in tracker.summary(changed)
        for invalid in (-1, float('nan'), float('inf')):
            try:
                tracker.set_baseline(invalid, directory)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid baseline accepted')
        assert not list(Path(directory).glob('ledger.json.*'))
        (Path(directory) / 'ledger.json').write_text('{broken')
        try:
            tracker.read_ledger(directory)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError('Corrupt ledger silently discarded')


def test_shutdown_and_single_instance():
    if sys.platform == 'win32':
        return  # SIGTERM has different process semantics on Windows.
    with tempfile.TemporaryDirectory() as directory:
        from lifetime_settings import save_settings
        save_settings(dict(completed=True, history=True), directory)
        code = 'import lifetime_tracker as t; t.run(' + repr(directory) + ')'
        process = subprocess.Popen([sys.executable, '-c', code])
        try:
            deadline = time.monotonic() + 5
            status_path = Path(directory) / 'status.json'
            while not status_path.exists():
                assert process.poll() is None, 'Tracker exited before its first write'
                assert time.monotonic() < deadline, 'Tracker startup timed out'
                time.sleep(0.02)
            duplicate = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=5)
            assert duplicate.returncode != 0, 'Duplicate tracker was allowed'
            process.terminate()
            assert process.wait(timeout=5) == 0
            status = json.loads(status_path.read_text())
            assert status['running'] is False
            assert tracker.read_ledger(directory)['total_seconds'] > 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


@fake_boot_history
def test_credit_and_migration():
    with tempfile.TemporaryDirectory() as directory:
        legacy = tracker.new_ledger()
        legacy.update(total_seconds=60, current_session_seconds=60,
                      longest_session_seconds=60, boots=1, boot_id='a', last_clock=160)
        for field in ('tracking_started', 'boot_credited_for', 'boot_accounted_seconds'):
            legacy.pop(field)
        tracker.atomic_json(Path(directory) / 'ledger.json', legacy)
        with patch.object(tracker, 'awake_clock', return_value=190), patch.object(tracker, 'boot_id', return_value='a'):
            credited = tracker.credit_current_boot(directory)
            assert credited['total_seconds'] == 190
            assert credited['current_session_seconds'] == 190
            assert credited['longest_session_seconds'] == 190
            assert credited['boots'] == 1
            assert credited['boot_credited_for'] == 'a'
            assert tracker.credit_current_boot(directory)['total_seconds'] == credited['total_seconds']
        tracker.sample(credited, 190, 'a', '2099-01-01T00:00:00+00:00', credit_current=True)
        assert credited['total_seconds'] == 190  # wall-clock sleep adds nothing
        tracker.sample(credited, 200, 'b')
        assert credited['total_seconds'] == 390
        assert credited['current_session_seconds'] == 200
        assert credited['boots'] == 2
        tracker.sample(credited, 200, 'b', credit_current=True)
        assert credited['total_seconds'] == 390
        assert credited['tracking_started'] == dt.date.today().isoformat()
        with patch.object(tracker, 'data_dir', return_value=Path(directory)), patch.object(tracker, 'awake_clock', return_value=190), patch.object(tracker, 'boot_id', return_value='a'), patch.object(sys, 'argv', ['tracker', '--credit-current-boot']):
            tracker.main()
        assert tracker.read_ledger(directory)['total_seconds'] == 190
        tracker.sample(credited, 1000, 'b')  # rejected gap must not enable recredit
        tracker.sample(credited, 1000, 'b', credit_current=True)
        assert credited['total_seconds'] == 390
    backwards = tracker.new_ledger()
    tracker.sample(backwards, 100, 'same')
    tracker.sample(backwards, 50, 'same')
    assert backwards['total_seconds'] == 100
    assert backwards['boots'] == 1
    capped = tracker.new_ledger()
    tracker.sample(capped, tracker.MAX_BOOT_CREDIT * 2, 'c', '2026-11-06T12:00:00+00:00')
    assert capped['total_seconds'] == tracker.MAX_BOOT_CREDIT


@fake_boot_history
def test_average():
    ledger = tracker.new_ledger()
    tracker.sample(ledger, 3600, 'a', '2026-11-05T12:00:00+00:00')
    ledger['baseline_seconds'] = 999999
    assert tracker.average_awake(ledger, dt.date(2026, 11, 5)) is None
    assert tracker.average_awake(ledger, dt.date(2026, 11, 6)) == 1
    assert tracker.average_awake(ledger, dt.date(2026, 11, 7)) == .5
    tracker.sample(ledger, 3630, 'a', '2026-11-07T12:00:00+00:00')
    assert ledger['tracking_started'] == '2026-11-05'


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in globals().items()
                              if name.startswith('test_') and callable(fn))


if __name__ == '__main__':
    test_credit_and_migration()
    test_average()
    test_samples()
    test_persistence_and_baseline()
    test_shutdown_and_single_instance()
    print('All tracker tests passed.')
