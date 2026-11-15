"""Alert monitoring: persistence, presence history, rules, calendars, incidents and notifications.

Every test uses a temporary directory, an injectable clock and a fake fleet; nothing waits for real time.
"""
import contextlib
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

import lifetime_monitor as monitoring

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.timezone.utc)      # a Monday
ID = str(uuid.UUID(int=1))


def stamp(value):
    return value.isoformat()


class Fleet:
    """Stands in for Store.monitor_snapshot: one dict per approved computer."""
    def __init__(self):
        self.devices = {}

    def add(self, device_id=ID, **values):
        device = dict(device_id=device_id, name='PC-001', group='Sales', created_at=stamp(T0 - dt.timedelta(days=30)),
                      last_report=stamp(T0), online=True, threshold=600, offline_since=None, reason=None,
                      client_version='1.2.0', resources=True, health=True, laptop=False, crashes_30d=0,
                      metrics=dict(cpu_percent=10, ram_percent=40, disk_free_percent=50, battery_health_percent=None,
                                   temperature_c=None), metrics_fresh=True)
        device.update(values)
        self.devices[device_id] = device
        return device

    def offline(self, since, device_id=ID):
        self.devices[device_id].update(online=False, offline_since=stamp(since), reason='link-closed')

    def online(self, at, device_id=ID):
        self.devices[device_id].update(online=True, offline_since=None, reason=None, last_report=stamp(at))

    def monitor_snapshot(self):
        return [dict(device) for device in self.devices.values()]


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += dt.timedelta(**delta)
        return self.now


class MonitorCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'monitoring.sqlite'
        self.fleet, self.clock = Fleet(), Clock()
        self.monitor = self.open()

    def open(self, **options):
        monitor = monitoring.Monitor(self.path, self.fleet, clock=self.clock, **options).start()
        self.addCleanup(lambda: monitor._db.close())
        return monitor

    def restart(self, gap_minutes=0):
        self.monitor.close()
        self.clock.advance(minutes=gap_minutes)
        self.monitor = self.open()
        return self.monitor

    def run_for(self, minutes, step=1):
        for _ in range(int(minutes / step)):
            self.clock.advance(minutes=step)
            self.monitor.tick()


class PersistenceTests(MonitorCase):
    def test_new_database_has_schema_wal_identity_and_refuses_foreign_files(self):
        db = self.monitor._db
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], monitoring.SCHEMA_VERSION)
        self.assertEqual(db.execute('PRAGMA application_id').fetchone()[0], monitoring.APPLICATION_ID)
        self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(db.execute('PRAGMA auto_vacuum').fetchone()[0], 2)
        foreign = Path(self.temporary.name) / 'other.sqlite'
        with contextlib.closing(sqlite3.connect(foreign)) as other:
            other.execute('CREATE TABLE devices (x)')
        with self.assertRaises(monitoring.MonitorError):
            monitoring.Monitor(foreign)
        newer = Path(self.temporary.name) / 'newer.sqlite'
        with contextlib.closing(sqlite3.connect(newer)) as other:
            other.execute(f'PRAGMA application_id={monitoring.APPLICATION_ID}')
            other.execute(f'PRAGMA user_version={monitoring.SCHEMA_VERSION + 1}')
        with self.assertRaises(monitoring.MonitorError):
            monitoring.Monitor(newer)

    def test_presence_transitions_survive_a_restart_and_downtime_is_unknown(self):
        self.fleet.add()
        self.monitor.tick()
        self.clock.advance(minutes=5)
        self.fleet.offline(self.clock.now - dt.timedelta(minutes=1))
        self.monitor.tick()
        self.restart(gap_minutes=30)
        history = self.monitor.presence(ID)
        self.assertEqual([(row['state'], row['reason']) for row in history],
                         [('unknown', 'server-down'), ('offline', 'link-closed'), ('online', 'reporting')])
        self.assertEqual(history[0]['end_at'], monitoring._stamp(self.clock.now))
        self.assertEqual(history[1]['end_at'], history[0]['start_at'])
        # Still offline from before the downtime: unknown until a full reporting window has passed, never "down since".
        self.monitor.tick()
        self.assertEqual(self.monitor.presence(ID, 1)[0]['state'], 'unknown')
        self.assertEqual(self.monitor.presence(ID, 1)[0]['reason'], 'awaiting-report')
        self.clock.advance(minutes=11)
        self.monitor.tick()
        latest = self.monitor.presence(ID, 1)[0]
        self.assertEqual((latest['state'], latest['end_at']), ('offline', None))
        self.assertEqual(latest['start_at'], monitoring._stamp(self.monitor.started_at))

    def test_a_computer_that_is_no_longer_approved_ends_its_history_and_forget_removes_it(self):
        self.fleet.add()
        self.monitor.tick()
        del self.fleet.devices[ID]
        self.clock.advance(minutes=1)
        self.monitor.tick()
        self.assertIsNotNone(self.monitor.presence(ID)[0]['end_at'])
        self.monitor.forget(ID)
        self.assertEqual(self.monitor.presence(ID), [])

    def test_retention_and_size_quota_remove_oldest_closed_history_only(self):
        self.fleet.add()
        for minute in range(400):       # flapping produces many closed intervals
            self.clock.advance(minutes=1)
            if minute % 2:
                self.fleet.online(self.clock.now)
            else:
                self.fleet.offline(self.clock.now)
            self.monitor.tick()
        rows = self.monitor._db.execute('SELECT COUNT(*) FROM presence').fetchone()[0]
        self.assertGreater(rows, 300)
        self.monitor.quota = 8192       # far below what the history needs
        self.monitor._housekeeping_at = None
        self.monitor.tick()
        remaining = self.monitor._db.execute('SELECT COUNT(*) FROM presence').fetchone()[0]
        self.assertLess(remaining, rows)
        self.assertEqual(self.monitor._db.execute('SELECT COUNT(*) FROM presence WHERE end_at IS NULL').fetchone()[0], 1)
        self.clock.advance(days=monitoring.RETENTION_DAYS + 1)
        self.monitor._housekeeping_at = None
        self.monitor.tick()
        self.assertEqual([row['end_at'] for row in self.monitor.presence(ID)], [None])


class SnapshotTests(unittest.TestCase):
    """Store.monitor_snapshot hands the rules only what each computer's policy shares."""
    def test_policy_filters_readings_and_health(self):
        import json
        from unittest.mock import patch
        import lifetime_server_store as storage
        with tempfile.TemporaryDirectory() as directory:
            store = storage.Store(Path(directory) / 'server.sqlite')
            try:
                with patch.object(storage, '_now', return_value=T0):
                    with store._transaction() as db:
                        for number, policy in ((1, dict(history=False, resources=False, health=False)),
                                               (2, dict(history=False, resources=True, health=False))):
                            db.execute('''INSERT INTO devices (device_id,identity,status,policy_version,policy,created_at,
                                last_received_at,readings,readings_received_at,health,search_text,group_name)
                                VALUES (?,?,'approved',1,?,?,?,?,?,?,'','')''', (
                                str(uuid.UUID(int=number)), json.dumps({'display_name': f'PC {number}'}), json.dumps(policy),
                                stamp(T0), stamp(T0), json.dumps(dict(observed_at=stamp(T0), specs=dict(disks=[dict(
                                    name='C:', total_bytes=100, free_bytes=5)], ram_bytes=100, battery_design_wh=50),
                                    performance=dict(cpu_percent=99, disk_free_bytes=5, ram_used_bytes=97))),
                                stamp(T0), json.dumps(dict(crashes_30d=9))))
                    shared, private = sorted(store.monitor_snapshot(), key=lambda d: d['device_id'])[::-1]
            finally:
                store.close()
        self.assertEqual((private['metrics'], private['laptop'], private['crashes_30d']), (None, None, None))
        self.assertTrue(private['online'])
        self.assertEqual(shared['metrics']['cpu_percent'], 99)
        self.assertEqual(shared['metrics']['disk_free_percent'], 5)
        self.assertEqual(shared['metrics']['ram_percent'], 97)
        self.assertTrue(shared['metrics_fresh'])
        self.assertTrue(shared['laptop'])
        self.assertIsNone(shared['crashes_30d'])        # health is not shared, even though it is stored


if __name__ == '__main__':
    unittest.main()
