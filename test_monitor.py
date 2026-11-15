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


class RuleTests(MonitorCase):
    def states(self, rule=None):
        return [(row['rule'], row['state']) for row in self.monitor.incidents()
                if rule is None or row['rule'] == rule]

    def metric(self, **values):
        self.fleet.devices[ID]['metrics'].update(values)

    def test_sustained_threshold_opens_once_and_hysteresis_holds_inside_the_band(self):
        self.fleet.add()
        self.metric(cpu_percent=99)
        self.monitor.tick()
        self.run_for(14)
        self.assertEqual(self.states('cpu_high'), [('cpu_high', 'pending')])
        self.run_for(1)
        self.assertEqual(self.states('cpu_high'), [('cpu_high', 'open')])
        self.metric(cpu_percent=90)         # below 95 but inside the 10-point band: still a problem
        self.run_for(30)
        self.assertEqual(self.states('cpu_high'), [('cpu_high', 'open')])
        self.metric(cpu_percent=50)
        self.run_for(5)
        self.assertEqual(self.states('cpu_high'), [('cpu_high', 'open')])     # recovery needs five quiet minutes
        self.run_for(1)
        incident = self.monitor.incidents()[0]
        self.assertEqual((incident['state'], incident['resolution']), ('resolved', 'recovered'))
        self.assertEqual(incident['resolved_at'], monitoring._stamp(self.clock.now - dt.timedelta(minutes=5)))

    def test_a_short_spike_never_becomes_an_incident(self):
        self.fleet.add()
        self.metric(cpu_percent=99)
        self.run_for(10)
        self.metric(cpu_percent=20)
        self.run_for(1)
        self.assertEqual(self.states(), [])

    def test_missing_or_stale_data_never_fires_and_never_resolves(self):
        self.fleet.add()
        self.metric(disk_free_percent=None)
        self.run_for(60)
        self.assertEqual(self.states(), [])
        self.assertIn('disk_low', self.monitor.status[ID]['missing'])
        self.metric(disk_free_percent=3)
        self.run_for(31)
        self.assertEqual(self.states('disk_low'), [('disk_low', 'open')])
        self.fleet.devices[ID]['metrics_fresh'] = False
        self.run_for(60)
        self.assertEqual(self.states('disk_low'), [('disk_low', 'open')])
        self.assertEqual(self.monitor.status[ID]['missing'], ['disk_low', 'cpu_high', 'memory_high'])

    def test_consent_off_means_no_rule_fires_on_that_data(self):
        self.fleet.add(resources=False, health=False, crashes_30d=50,
                       metrics=dict(cpu_percent=100, ram_percent=100, disk_free_percent=0, battery_health_percent=1,
                                    temperature_c=120))
        self.run_for(120, step=5)
        self.assertEqual(self.states(), [])
        self.assertEqual(self.monitor.status[ID]['missing'], [])
        self.fleet.devices[ID].update(resources=True, health=True)
        self.run_for(35, step=5)
        self.assertEqual(sorted(rule for rule, state in self.states() if state == 'open'),
                         ['battery_worn', 'cpu_high', 'crashes', 'disk_low', 'memory_high', 'temperature_high'])
        self.fleet.devices[ID].update(resources=False)        # consent withdrawn: watched no longer, quietly closed
        self.monitor.tick()
        self.assertEqual(sorted(row['rule'] for row in self.monitor.incidents('active')), ['crashes'])
        self.assertEqual({row['resolution'] for row in self.monitor.incidents('resolved')}, {'not-monitored'})

    def test_no_sensor_or_no_battery_is_not_a_problem_and_not_missing(self):
        self.fleet.add()
        self.run_for(30)
        self.assertEqual(self.states(), [])
        self.assertEqual(self.monitor.status[ID]['missing'], [])

    def test_offline_waits_its_minutes_and_reconnect_recovers(self):
        self.fleet.add()
        self.monitor.tick()
        self.fleet.offline(self.clock.now)
        self.run_for(14)
        self.assertEqual(self.states('offline'), [('offline', 'pending')])
        self.run_for(1)
        self.assertEqual(self.states('offline'), [('offline', 'open')])
        self.fleet.online(self.clock.now)
        self.run_for(6)
        self.assertEqual(self.states('offline'), [('offline', 'resolved')])

    def test_agent_version_and_no_report_rules(self):
        self.fleet.add(client_version='1.1.4', last_report=stamp(T0))
        self.monitor.set_rule('agent_outdated', enabled=True, version='1.2')
        self.monitor.set_rule('no_report', minutes=60)
        self.run_for(59)
        self.assertEqual(self.states(), [('agent_outdated', 'open')])
        self.run_for(1)
        self.assertEqual(sorted(self.states()), [('agent_outdated', 'open'), ('no_report', 'open')])
        self.fleet.devices[ID].update(client_version='1.2.0', last_report=stamp(self.clock.now))
        self.run_for(6)
        self.assertEqual(sorted(self.states()), [('agent_outdated', 'resolved'), ('no_report', 'resolved')])

    def test_rule_settings_are_validated_saved_and_a_disabled_rule_closes_its_incidents(self):
        for arguments in (dict(rule='nope'), dict(rule='cpu_high', threshold=101), dict(rule='cpu_high', threshold='9'),
                          dict(rule='cpu_high', minutes=-1), dict(rule='cpu_high', enabled='yes'),
                          dict(rule='cpu_high', version='1.0'), dict(rule='agent_outdated', version='one')):
            with self.assertRaises(monitoring.MonitorError, msg=arguments):
                self.monitor.set_rule(**arguments)
        self.monitor.set_rule('disk_low', threshold=20, minutes=0)
        self.fleet.add()
        self.metric(disk_free_percent=15)
        self.monitor.tick()
        self.assertEqual(self.states('disk_low'), [('disk_low', 'open')])
        self.restart()
        self.assertEqual(next(r for r in self.monitor.rules() if r['rule'] == 'disk_low')['threshold'], 20)
        self.monitor.set_rule('disk_low', enabled=False)
        self.monitor.tick()
        self.assertEqual(self.monitor.incidents()[0]['resolution'], 'not-monitored')


class CalendarTests(MonitorCase):
    def states(self):
        return [(row['rule'], row['state']) for row in self.monitor.incidents()]

    def test_laptop_asleep_outside_its_schedule_never_creates_an_incident(self):
        for laptop in (True, None):     # a laptop, and a computer whose kind is unknown (no readings shared)
            self.fleet.devices.clear()
            self.fleet.add(laptop=laptop)
            self.monitor.tick()
            self.fleet.offline(self.clock.now)
            self.run_for(48 * 60, step=30)
            self.assertEqual(self.states(), [])
            self.assertFalse(self.monitor.status[ID]['expected'])

    def test_business_hours_with_weekdays_holidays_and_time_zone(self):
        self.monitor.set_schedule('group', 'business', key='SALES', timezone='+07:00', start='08:00', end='17:00',
                                  holidays=['2026-11-17'])
        self.fleet.add(laptop=True)     # a group schedule applies to laptops too
        self.clock.now = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.timezone.utc)       # 16:30 in +07:00, Monday
        self.monitor.tick()
        self.fleet.offline(self.clock.now)
        self.run_for(29)        # 16:59 local: offline 29 minutes inside the hours
        self.assertEqual(self.states(), [('offline', 'open')])
        self.fleet.online(self.clock.now)
        self.run_for(6)
        self.fleet.offline(self.clock.now)      # off at 17:05 local: outside the hours
        self.run_for(16 * 60, step=10)          # Tuesday is a holiday
        self.assertEqual(self.states(), [('offline', 'resolved')])
        self.clock.now = dt.datetime(2026, 11, 18, 1, 5, tzinfo=dt.timezone.utc)        # Wednesday 08:05 local
        self.monitor.tick()
        pending = self.monitor.incidents('pending')[0]
        self.assertEqual(pending['started_at'], '2026-11-18T01:00:00+00:00')         # counted from the opening time
        self.run_for(10)
        self.assertEqual(self.monitor.incidents()[0]['state'], 'open')

    def test_expected_handles_overnight_windows_and_the_auto_default(self):
        schedule = dict(monitoring.DEFAULT_SCHEDULE, kind='business', timezone='UTC', start='22:00', end='06:00')
        late = dt.datetime(2026, 11, 16, 23, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(monitoring.expected(schedule, None, late), (True, late.replace(hour=22)))
        early = dt.datetime(2026, 11, 17, 5, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(monitoring.expected(schedule, None, early), (True, late.replace(hour=22)))
        self.assertEqual(monitoring.expected(schedule, None, early.replace(hour=7)), (False, None))
        saturday = dt.datetime(2026, 11, 21, 23, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(monitoring.expected(schedule, None, saturday), (False, None))
        auto = monitoring.DEFAULT_SCHEDULE
        self.assertEqual(monitoring.expected(auto, False, T0), (True, None))
        self.assertEqual(monitoring.expected(auto, True, T0), (False, None))
        self.assertEqual(monitoring.expected(dict(auto, kind='always'), True, T0), (True, None))

    def test_schedule_and_maintenance_input_is_validated(self):
        for arguments in (dict(scope='group', kind='business'), dict(scope='default', key='x', kind='always'),
                          dict(scope='default', kind='sometimes'), dict(scope='default', kind='business', start='8:00'),
                          dict(scope='default', kind='business', start='08:00', end='08:00'),
                          dict(scope='default', kind='business', timezone='Mars/Base'),
                          dict(scope='default', kind='business', timezone='+15:00'),
                          dict(scope='default', kind='business', weekdays=[7]),
                          dict(scope='default', kind='business', weekdays=[1, 1]),
                          dict(scope='default', kind='business', holidays=['2026-02-30'])):
            with self.assertRaises(monitoring.MonitorError, msg=arguments):
                self.monitor.set_schedule(**arguments)
        start = stamp(T0)
        for arguments in (dict(scope='all', key='x', start=start, end=stamp(T0 + dt.timedelta(hours=1))),
                          dict(scope='group', start=start, end=stamp(T0 + dt.timedelta(hours=1))),
                          dict(scope='all', start=start, end=start),
                          dict(scope='all', start=start, end=stamp(T0 + dt.timedelta(days=32))),
                          dict(scope='all', start='2026-11-16T09:00:00', end=stamp(T0 + dt.timedelta(hours=1))),
                          dict(scope='all', start=start, end=stamp(T0 + dt.timedelta(hours=1)), note='a\nb')):
            with self.assertRaises(monitoring.MonitorError, msg=arguments):
                self.monitor.add_maintenance(**arguments)
        self.monitor.set_schedule('default', 'always')
        self.assertEqual(self.monitor.schedules()[0]['kind'], 'always')
        self.monitor.delete_schedule('default')
        self.assertEqual(self.monitor.schedules()[0]['kind'], 'auto')

    def test_maintenance_pauses_alerts_and_an_ongoing_problem_opens_when_it_ends(self):
        self.fleet.add()
        self.monitor.add_maintenance('group', stamp(T0), stamp(T0 + dt.timedelta(hours=2)), key='sales', note='Updates')
        self.monitor.tick()
        self.assertTrue(self.monitor.status[ID]['paused'])
        self.fleet.offline(self.clock.now)
        self.run_for(60)
        self.assertEqual(self.states(), [('offline', 'suppressed')])
        self.run_for(61)
        self.assertEqual(self.states(), [('offline', 'open')])
        opened = self.monitor.incidents()[0]['opened_at']
        self.assertEqual(opened, monitoring._stamp(T0 + dt.timedelta(hours=2)))
        # A problem that clears inside the window resolves quietly: it was never announced.
        self.monitor.add_maintenance('device', stamp(self.clock.now), stamp(self.clock.now + dt.timedelta(hours=1)), key=ID)
        self.fleet.add('00000000-0000-0000-0000-000000000009', group='Lab')
        self.fleet.online(self.clock.now)
        self.fleet.devices[ID]['metrics']['cpu_percent'] = 100
        self.run_for(20)
        self.assertIn(('cpu_high', 'suppressed'), self.states())
        self.fleet.devices[ID]['metrics']['cpu_percent'] = 5
        self.monitor.tick()
        cpu = next(row for row in self.monitor.incidents() if row['rule'] == 'cpu_high')
        self.assertEqual((cpu['state'], cpu['notified']), ('resolved', 0))
        self.assertEqual(len(self.monitor.maintenance()), 1)
        self.monitor.delete_maintenance(self.monitor.maintenance()[0]['id'])
        self.assertEqual(self.monitor.maintenance(), [])


class IncidentTests(MonitorCase):
    def open_offline(self):
        self.fleet.add()
        self.monitor.tick()
        self.fleet.offline(self.clock.now)
        self.run_for(15)
        return self.monitor.incidents('active')[0]

    def test_acknowledge_records_who_and_when_and_recovery_still_resolves(self):
        incident = self.open_offline()
        for arguments in (dict(incident_id='1', by='Lan'), dict(incident_id=incident['id'], by=''),
                          dict(incident_id=incident['id'], by='x\n'), dict(incident_id=999, by='Lan')):
            with self.assertRaises(monitoring.MonitorError, msg=arguments):
                self.monitor.acknowledge(**arguments)
        self.clock.advance(minutes=2)
        detail = self.monitor.acknowledge(incident['id'], 'Lan Nguyen')
        self.assertEqual((detail['state'], detail['acknowledged_by'], detail['acknowledged_at']),
                         ('acknowledged', 'Lan Nguyen', monitoring._stamp(self.clock.now)))
        self.assertEqual(self.monitor.acknowledge(incident['id'], 'Minh')['acknowledged_by'], 'Lan Nguyen')
        self.assertEqual(self.monitor.summary()['acknowledged'], 1)
        self.fleet.online(self.clock.now)
        self.run_for(6)
        detail = self.monitor.incident(incident['id'])
        self.assertEqual(detail['state'], 'resolved')
        self.assertEqual([event['kind'] for event in detail['timeline']], ['pending', 'opened', 'acknowledged', 'resolved'])
        with self.assertRaises(monitoring.MonitorError):
            self.monitor.acknowledge(incident['id'], 'Lan')
        self.assertEqual(self.monitor.summary()['active'], 0)

    def test_one_incident_per_problem_across_restarts(self):
        incident = self.open_offline()
        for gap in (0, 3, 30):
            self.restart(gap_minutes=gap)
            self.run_for(20)        # through the start-up grace and beyond: the same problem, the same incident
            self.assertEqual([row['id'] for row in self.monitor.incidents()], [incident['id']])
            self.assertEqual(self.monitor.incidents()[0]['state'], 'open')
        with self.assertRaises(sqlite3.IntegrityError):     # the database itself refuses a second active copy
            with self.monitor._transaction() as db:
                db.execute("INSERT INTO incidents(device_id,rule,cause,state,started_at,updated_at) VALUES (?,?,?,'open',?,?)",
                           (ID, 'offline', 'offline', stamp(T0), stamp(T0)))
        self.fleet.online(self.clock.now)
        self.run_for(6)
        self.assertEqual(self.monitor.incident(incident['id'])['state'], 'resolved')

    def test_device_alerts_and_summary_for_badges(self):
        self.open_offline()
        alerts = self.monitor.device_alerts(ID)
        self.assertEqual([row['rule'] for row in alerts['incidents']], ['offline'])
        self.assertEqual(alerts['status']['presence'], 'offline')
        summary = self.monitor.summary()
        self.assertEqual((summary['open'], summary['active'], summary['paused']), (1, 1, 0))
        self.assertEqual(self.monitor.device_alerts(str(uuid.UUID(int=5))), dict(incidents=[], status=None))
        for arguments in (dict(state='bogus'), dict(limit=0), dict(offset=-1)):
            with self.assertRaises(monitoring.MonitorError):
                self.monitor.incidents(**arguments)


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
