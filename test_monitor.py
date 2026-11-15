"""Alert monitoring: persistence, presence history, rules, calendars, incidents and notifications.

Every test uses a temporary directory, an injectable clock and a fake fleet; nothing waits for real time.
"""
import contextlib
import datetime as dt
from pathlib import Path
import socketserver
import sqlite3
import ssl
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import test_compat  # noqa: F401  (enterContext on Python 3.10)

import lifetime_monitor as monitoring
import lifetime_notify as notify
import lifetime_permissions as permissions

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.timezone.utc)      # a Monday
ID = str(uuid.UUID(int=1))


def stamp(value):
    return value.isoformat()


def zoneinfo_missing():
    import zoneinfo
    return zoneinfo.ZoneInfoNotFoundError('No time zone found')


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


class Transport:
    """Records what would have been sent; ``fail`` makes every attempt raise that error."""
    def __init__(self):
        self.emails, self.hooks, self.fail = [], [], None

    def send_email(self, settings, password, subject, body):
        if self.fail:
            raise self.fail
        self.emails.append(dict(subject=subject, body=body, password=password, to=list(settings['recipients'])))

    def post_webhook(self, url, payload):
        if self.fail:
            raise self.fail
        self.hooks.append(dict(url=url, payload=payload))


class MonitorCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        if sys.platform == 'win32':     # the ACL helper needs elevation; production has no bypass
            self.acl = self.enterContext(patch.object(permissions, 'windows_acl'))
        self.path = Path(self.temporary.name) / 'monitoring.sqlite'
        self.fleet, self.clock, self.transport = Fleet(), Clock(), Transport()
        self.monitor = self.open()

    def open(self, **options):
        monitor = monitoring.Monitor(self.path, self.fleet, clock=self.clock, transport=self.transport,
                                     server_name='School-Server', **options).start()
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

    def test_without_a_time_zone_database_names_are_refused_readably_and_offsets_still_work(self):
        missing = zoneinfo_missing()
        with patch.object(monitoring, 'iana_available', return_value=False), \
                patch.object(monitoring.zoneinfo, 'ZoneInfo', side_effect=missing):
            with self.assertRaisesRegex(monitoring.MonitorError, 'tzdata'):
                self.monitor.set_schedule('default', 'business', timezone='Asia/Ho_Chi_Minh')
            self.monitor.set_schedule('group', 'business', key='Sales', timezone='+07:00')
            self.assertEqual(self.monitor.time_zones(), dict(iana=False, message=monitoring.NO_TZDATA))
            # A name saved while tzdata was installed: monitoring carries on in server time and says so.
            with self.monitor._transaction() as db:
                db.execute("UPDATE schedules SET value=json_set(value,'$.timezone','Asia/Ho_Chi_Minh') WHERE scope='group'")
            self.fleet.add()
            self.monitor.tick()
            self.assertIn('expected', self.monitor.status[ID])
            self.assertFalse(next(row for row in self.monitor.schedules() if row['scope'] == 'group')['zone_ok'])
        self.assertEqual(monitoring.schedule_zone(dict(timezone='+07:00'))[0].utcoffset(None), dt.timedelta(hours=7))

    @unittest.skipUnless(monitoring.iana_available(), 'no IANA time zone data here (install tzdata from requirements-server.txt)')
    def test_iana_names_when_the_database_is_available(self):
        self.monitor.set_schedule('default', 'business', timezone='Asia/Ho_Chi_Minh', start='08:00', end='17:00')
        schedule = self.monitor.schedules()[0]
        self.assertTrue(schedule['zone_ok'])
        self.assertEqual(monitoring.expected(schedule, None, dt.datetime(2026, 11, 16, 1, 30, tzinfo=dt.timezone.utc)),
                         (True, dt.datetime(2026, 11, 16, 1, 0, tzinfo=dt.timezone.utc)))

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


MAIL = dict(email_enabled=True, smtp_host='mail.school.local', smtp_port=587, security='starttls',
            username='alerts', sender='lifetime@school.local', recipients=['it@school.local'])


class SMTPStub(socketserver.StreamRequestHandler):
    """A small local SMTP server: STARTTLS (when offered), AUTH PLAIN, one message per DATA. No real network."""
    def write(self, text):
        self.wfile.write(text.encode('ascii') + b'\r\n')

    def handle(self):
        try:
            self.converse()
        finally:
            self.connection.close()

    def converse(self):
        server, secure = self.server, False
        self.write('220 stub ESMTP')
        while True:
            line = self.rfile.readline(4096).decode('ascii', 'replace').strip()
            verb = line.split(' ', 1)[0].upper()
            if not line or verb == 'QUIT':
                self.write('221 bye')
                return
            if verb in ('EHLO', 'HELO'):
                extensions = ([] if secure or not server.starttls else ['STARTTLS']) + ['AUTH PLAIN']
                self.write('\r\n'.join(['250-stub'] + [f'250-{item}' for item in extensions[:-1]] + [f'250 {extensions[-1]}']))
            elif verb == 'STARTTLS':
                self.write('220 ready')
                self.connection = server.context.wrap_socket(self.connection, server_side=True)
                self.rfile, self.wfile = self.connection.makefile('rb'), self.connection.makefile('wb', buffering=0)
                secure = True
            elif verb == 'AUTH':
                server.auth.append((secure, line))
                self.write('235 ok')
            elif verb == 'DATA':
                self.write('354 go')
                data = b''
                while (chunk := self.rfile.readline(65536)) not in (b'.\r\n', b''):
                    data += chunk
                server.messages.append((secure, data.decode('utf-8', 'replace')))
                self.write('250 queued')
            else:
                self.write('250 ok')


class NotificationTests(MonitorCase):
    def setUp(self):
        super().setUp()
        self.monitor.set_notifications(password='Pa55-word', **MAIL)
        self.fleet.add()
        self.monitor.tick()

    def unplug(self):
        self.fleet.offline(self.clock.now)
        self.run_for(15)
        self.monitor.deliver()

    def test_open_and_recovery_are_sent_once_and_a_test_creates_no_incident(self):
        self.unplug()
        self.assertEqual(len(self.transport.emails), 1)
        email = self.transport.emails[0]
        self.assertEqual(email['subject'], '[School-Server] Problem: Computer offline on PC-001')
        self.assertEqual((email['password'], email['to']), ('Pa55-word', ['it@school.local']))
        for _ in range(3):
            self.run_for(5)
            self.monitor.deliver()
        self.assertEqual(len(self.transport.emails), 1)
        self.fleet.online(self.clock.now)
        self.run_for(10)
        self.monitor.deliver()
        self.assertEqual([item['subject'][:27] for item in self.transport.emails],
                         ['[School-Server] Problem: Co', '[School-Server] Resolved: C'])
        result = self.monitor.test_notification('email')
        self.assertEqual(result, dict(channel='email', status='sent', error=None))
        self.assertEqual(len(self.monitor.incidents()), 1)
        self.assertEqual([row['kind'] for row in self.monitor.deliveries()], ['test', 'recovery', 'open'])
        for item in self.transport.emails:     # no history, readings, addresses or secrets in any message
            self.assertNotIn('Pa55', item['body'])
            self.assertNotIn(ID, item['body'])

    def test_mail_server_down_retries_with_backoff_then_fails_without_crashing(self):
        self.transport.fail = ConnectionRefusedError('mail server down')
        self.unplug()
        log = self.monitor.deliveries()[0]
        self.assertEqual((log['status'], log['attempts'], log['error']), ('queued', 1, 'connect'))
        for seconds in monitoring.BACKOFF:
            self.assertEqual(self.monitor.deliver(), 0)     # not due yet
            self.clock.advance(seconds=seconds)
            self.assertEqual(self.monitor.deliver(), 1)
        log = self.monitor.deliveries()[0]
        self.assertEqual((log['status'], log['attempts'], log['error']), ('failed', 5, 'connect'))
        self.clock.advance(days=1)
        self.assertEqual(self.monitor.deliver(), 0)
        self.assertEqual(self.transport.emails, [])
        self.assertEqual(self.monitor.test_notification('email')['error'], 'connect')

    def test_a_restart_never_sends_twice_even_when_it_interrupted_a_send(self):
        self.unplug()
        self.restart(gap_minutes=1)
        self.run_for(20)
        self.monitor.deliver()
        self.assertEqual(len(self.transport.emails), 1)
        with self.monitor._transaction() as db:      # as if the server stopped in the middle of sending
            db.execute("UPDATE deliveries SET status='sending'")
        self.restart()
        self.monitor.deliver()
        self.assertEqual(len(self.transport.emails), 1)
        self.assertEqual(self.monitor.deliveries()[0]['error'], 'interrupted')

    def test_webhook_is_optional_https_only_and_its_address_is_a_secret(self):
        for url in ('http://hooks.example/x', 'https://user:pw@hooks.example/x', 'https:///x', 'https://a b/', 'ftp://x'):
            with self.assertRaises(monitoring.MonitorError, msg=url):
                self.monitor.set_notifications(webhook_url=url, webhook_enabled=True)
        with self.assertRaises(monitoring.MonitorError):
            self.monitor.set_notifications(webhook_enabled=True)        # enabled without an address
        settings = self.monitor.set_notifications(webhook_url='https://hooks.example/services/T0KEN', webhook_enabled=True)
        self.assertEqual(settings['webhook'], 'https://hooks.example/…')
        self.assertTrue(settings['password_set'])       # keeping the password: None leaves it alone
        self.unplug()
        hook = self.transport.hooks[0]
        self.assertEqual(hook['url'], 'https://hooks.example/services/T0KEN')
        self.assertEqual((hook['payload']['event'], hook['payload']['computer']), ('open', 'PC-001'))
        self.assertNotIn('T0KEN', str(self.monitor.notifications()) + str(self.monitor.deliveries()))
        self.monitor.set_notifications(webhook_url='', webhook_enabled=False)
        self.assertEqual(self.monitor.notifications()['webhook'], '')

    def test_settings_are_validated_and_secrets_stay_out_of_the_database(self):
        for change in (dict(security='none'), dict(smtp_port=0), dict(smtp_host='bad host'), dict(sender='nobody'),
                       dict(recipients=['a@b.c'] * 21), dict(recipients='it@school.local'), dict(summary_hour=24),
                       dict(language='fr'), dict(email_enabled='yes'), dict(colour='red'),
                       dict(recipients=[], email_enabled=True), dict(password='a\nb')):
            with self.assertRaises(monitoring.MonitorError, msg=change):
                self.monitor.set_notifications(**change)
        settings = self.monitor.notifications()
        self.assertTrue(settings['password_set'])
        self.assertNotIn('Pa55-word', str(settings))
        self.monitor.close()
        for name in ('monitoring.sqlite', 'monitoring.sqlite-wal'):
            path = Path(self.temporary.name) / name
            if path.exists():
                self.assertNotIn(b'Pa55-word', path.read_bytes())
        secret = (Path(self.temporary.name) / notify.SECRET_FILE).read_bytes()
        if sys.platform == 'win32':
            self.assertTrue(secret.startswith(b'DPAPI1\n'))
            self.assertNotIn(b'Pa55-word', secret)
            self.acl.assert_called()        # restricted to Administrators and SYSTEM before it was written
        self.monitor = self.open()
        self.assertEqual(self.monitor.secrets.load()['smtp_password'], 'Pa55-word')
        (Path(self.temporary.name) / notify.SECRET_FILE).write_bytes(b'DPAPI1\ngarbage' if sys.platform == 'win32' else b'??')
        self.assertTrue(self.monitor.notifications()['secrets_unreadable'])
        self.assertEqual(self.monitor.test_notification('email')['error'], 'config')

    def test_daily_summary_is_off_by_default_and_sent_once_a_day(self):
        self.unplug()
        self.run_for(12 * 60, step=60)
        self.monitor.deliver()
        self.assertEqual([row['kind'] for row in self.monitor.deliveries()], ['open'])
        self.monitor.set_notifications(summary_enabled=True, summary_hour=0)
        self.run_for(30, step=10)
        self.monitor.deliver()
        summaries = [item for item in self.transport.emails if 'daily summary' in item['subject']]
        self.assertEqual(len(summaries), 1)
        self.assertIn('Open 1', summaries[0]['body'])
        self.assertIn('PC-001: Computer offline', summaries[0]['body'])

    def test_vietnamese_messages(self):
        self.monitor.set_notifications(language='vi')
        self.unplug()
        self.assertEqual(self.transport.emails[0]['subject'], '[School-Server] Sự cố: Máy tính ngoại tuyến trên PC-001')

    def test_real_smtp_over_starttls_with_a_local_stub_and_no_plain_text_fallback(self):
        import lifetime_tls as tls
        directory = Path(self.temporary.name) / 'tls'
        directory.mkdir()
        tls.generate_certificate(directory, 'https://127.0.0.1:8443')
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(directory / 'server-cert.pem', directory / 'server-key.pem')
        for starttls in (True, False):
            stub = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTPStub)
            stub.daemon_threads = True
            stub.context, stub.starttls, stub.messages, stub.auth = server_context, starttls, [], []
            thread = threading.Thread(target=stub.serve_forever, kwargs=dict(poll_interval=0.05))
            thread.start()
            try:
                transport = notify.Transport()
                transport.context = ssl.create_default_context(cadata=(directory / 'server-cert.pem').read_text())
                self.monitor.transport = transport
                self.monitor.set_notifications(smtp_host='127.0.0.1', smtp_port=stub.server_address[1])
                result = self.monitor.test_notification('email')
            finally:
                stub.shutdown()
                stub.server_close()
                thread.join()
            if starttls:
                self.assertEqual(result['status'], 'sent')
                self.assertEqual([secure for secure, _ in stub.auth + stub.messages], [True, True])
                self.assertIn('Subject: Lifetime Server test message', stub.messages[0][1])
            else:       # a server without STARTTLS gets nothing, not even the password
                self.assertEqual(result, dict(channel='email', status='failed', error='tls'))
                self.assertEqual((stub.auth, stub.messages), ([], []))

    def test_error_categories_are_secret_free_words(self):
        import smtplib
        import urllib.error
        for error, expected in ((smtplib.SMTPAuthenticationError(535, b'no'), 'auth'), (TimeoutError(), 'timeout'),
                                (smtplib.SMTPRecipientsRefused({}), 'refused'), (ssl.SSLError(), 'tls'),
                                (urllib.error.HTTPError('https://x', 503, 'busy', {}, None), 'http-503'),
                                (OSError('secret path'), 'connect'), (RuntimeError('x'), 'other')):
            self.assertEqual(notify.category(error), expected)


class GoldenCases(MonitorCase):
    """The alert state machine end to end: incidents and the messages a recipient would actually receive."""
    def setUp(self):
        super().setUp()
        self.monitor.set_notifications(password='pw', **MAIL)
        self.fleet.add()
        self.step()

    def step(self, minutes=1):
        for _ in range(minutes):
            self.clock.advance(minutes=1)
            self.monitor.tick()
            self.monitor.deliver()

    def subjects(self):
        return [item['subject'].split('] ')[1].split(':')[0] for item in self.transport.emails]

    def test_unplug_gives_one_incident_and_one_email_and_reconnect_one_recovery(self):
        self.fleet.offline(self.clock.now)
        self.step(120)
        self.assertEqual((len(self.monitor.incidents()), self.subjects()), (1, ['Problem']))
        self.fleet.online(self.clock.now)
        self.step(60)
        self.assertEqual(self.subjects(), ['Problem', 'Resolved'])
        self.assertEqual(self.monitor.incidents()[0]['state'], 'resolved')

    def test_server_restart_gives_no_duplicate_incident_or_email(self):
        self.fleet.offline(self.clock.now)
        self.step(20)
        for gap in (1, 45):
            self.restart(gap_minutes=gap)
            self.step(30)
        self.assertEqual((len(self.monitor.incidents()), self.subjects()), (1, ['Problem']))
        self.fleet.online(self.clock.now)
        self.restart(gap_minutes=2)         # it came back while the server was down
        self.step(30)
        self.assertEqual(self.subjects(), ['Problem', 'Resolved'])

    def test_flapping_gives_no_storm(self):
        for minute in range(180):           # three hours of three-minute drop-outs: never a sustained problem
            (self.fleet.offline if minute % 6 < 3 else self.fleet.online)(self.clock.now)
            self.step()
        self.assertEqual((self.monitor.incidents(), self.transport.emails), ([], []))
        self.fleet.offline(self.clock.now)
        self.step(20)
        for minute in range(120):           # a real outage, then flapping: still one incident, nothing more sent
            (self.fleet.offline if minute % 4 < 2 else self.fleet.online)(self.clock.now)
            self.step()
        self.assertEqual((len(self.monitor.incidents()), self.subjects()), (1, ['Problem']))
        self.fleet.online(self.clock.now)
        self.step(10)
        self.assertEqual(self.subjects(), ['Problem', 'Resolved'])

    def test_laptop_asleep_outside_its_hours_gives_nothing(self):
        self.fleet.devices[ID]['laptop'] = True
        self.fleet.offline(self.clock.now)
        self.step(3 * 24 * 60)
        self.assertEqual((self.monitor.incidents(), self.transport.emails), ([], []))

    def test_mail_server_down_retries_then_fails_and_monitoring_continues(self):
        self.transport.fail = smtplib_error()
        self.fleet.offline(self.clock.now)
        self.step(150)
        log = self.monitor.deliveries()[0]
        self.assertEqual((log['status'], log['attempts'], log['error']), ('failed', 5, 'connect'))
        self.transport.fail = None
        self.fleet.online(self.clock.now)
        self.step(10)
        self.assertEqual(self.subjects(), ['Resolved'])      # the next message still goes out
        self.assertEqual(self.monitor.incidents()[0]['state'], 'resolved')

    def test_consent_off_means_no_rule_fires_on_that_data(self):
        self.fleet.devices[ID].update(resources=False, health=False, crashes_30d=99, metrics=dict(
            cpu_percent=100, ram_percent=100, disk_free_percent=0, battery_health_percent=5, temperature_c=110))
        self.step(240)
        self.assertEqual((self.monitor.incidents(), self.transport.emails), ([], []))


def smtplib_error():
    import smtplib
    return smtplib.SMTPServerDisconnected('Connection unexpectedly closed')


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
