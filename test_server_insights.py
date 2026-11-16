"""Energy and lifecycle on Lifetime Server: what the store hands lifetime_insights (consent first), the admin
operations and their roles, and the report sections. Temporary data only; ACL calls mocked as in the other tests."""
import datetime as dt
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import lifetime_insights as insights
import lifetime_permissions as permissions
import lifetime_server_reports as reports
import lifetime_server_store as storage
import test_server_access
import test_server_runtime

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
TODAY = NOW.astimezone().date()


def add_computer(store, policy, name='PC', group='Lab', readings=(), history=None, health=None):
    """Enroll, approve and sync one computer straight through the store; ``readings``: [(minutes ago, performance)]."""
    issued = store.create_invitation('0' * 64)
    request = dict(version=1, request_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), code=issued['code'],
                   poll_token=secrets.token_urlsafe(32), identity=dict(display_name=name, department=group), policy=policy)
    store.enroll(request)
    store.approve(request['request_id'])
    credential = store.claim(request['request_id'], request['poll_token'])['credential']
    specs = dict(ram_bytes=8 * 2**30, battery_design_wh=50, disks=[dict(name='C:', total_bytes=100 * 2**30, free_bytes=40 * 2**30)])
    base = dict(version=1, device_id=request['device_id'], policy_version=1, policy=policy, records=[])
    for minutes, performance in readings:
        stamp = NOW - dt.timedelta(minutes=minutes)
        with patch.object(storage, '_now', return_value=stamp):
            store.ingest(request['device_id'], credential, dict(base, readings=dict(
                observed_at=stamp.isoformat(), specs=specs, performance=performance)))
    extra = {}
    if history is not None:
        extra['history'] = history
    if health is not None:
        extra['health'] = health
    store.ingest(request['device_id'], credential, dict(base, records=[dict(
        seq=1, observed_at=NOW.isoformat(), awake_seconds=60, measured_seconds=60, imported_seconds=0,
        totals=dict(measured_seconds=3600, imported_seconds=0, baseline_seconds=0))], client_version='1.2.0', **extra))
    return request['device_id']


class StoreInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))
        self.store = storage.Store(Path(directory.name) / 'server.sqlite')
        self.addCleanup(self.store.close)

    def test_only_on_battery_discharge_counts_as_a_power_reading(self):
        readings = [(60, dict(power_watts=14.0, charging=False, battery_percent=80)),
                    (50, dict(power_watts=30.0, charging=True, battery_percent=81)),     # charge rate, not draw
                    (40, dict(power_watts=16.0, charging=False, battery_percent=79)),
                    (30, dict(power_watts=18.0, charging=False, battery_percent=78))]
        add_computer(self.store, dict(history=True, resources=True), readings=readings,
                     history=dict(daily={TODAY.isoformat(): 7200}))
        item = self.store.insight_inputs()[0]
        self.assertEqual(sorted(item['discharge']), [14.0, 16.0, 18.0])
        self.assertEqual(item['kind'], 'laptop')
        power = insights.power(item['kind'], item['discharge'])
        self.assertEqual((power['watts'], power['method']), (16.0, 'measured'))     # (14+16+18)/3

    def test_nothing_the_computer_does_not_share(self):
        add_computer(self.store, dict(history=False, resources=False))       # the protocol refuses anything more
        item = self.store.insight_inputs()[0]
        self.assertEqual((item['history'], item['readings'], item['discharge'], item['kind'], item['health']),
                         (None, None, [], None, None))
        result = insights.fleet([item], insights.settings(), TODAY)
        self.assertIsNone(result['computers'][0]['energy'])
        self.assertEqual(result['fleet']['unknown'], 1)


class ReportTests(unittest.TestCase):
    def device(self, device_id='a'):
        return dict(device_id=device_id, policy=dict(history=True), identity=dict(display_name=device_id),
                    history=dict(daily={'2026-11-10': 36000}), status='approved',
                    totals=dict(measured_seconds=36000, imported_seconds=0, baseline_seconds=0))

    def test_device_and_fleet_reports_state_the_method_and_the_period_energy(self):
        config = insights.settings(dict(price=2000))
        energy = dict(settings=config, computers={
            'a': dict(power=dict(watts=20.0, method='estimated', source='typical-laptop', readings=0), energy={},
                      after=dict(source='office-hours', hours=insights.settings()['hours'],
                                 week=dict(hours=3.0, kwh=0.06, cost=120.0)),
                      health=dict(score=100, factors=[('stability', 'good', 0), ('battery', None, None)], unknown=['battery'])),
            'b': dict(power=dict(watts=50.0, method='measured', source='battery', readings=12), energy={})})
        report = reports.device_report(self.device(), '2026-11-10', '2026-11-10', insights=energy)
        rows = {title: value for section, title, value in report['extra_rows'] if section == 'energy'}
        self.assertEqual(rows['Power method'], 'estimated: typical laptop')
        self.assertEqual(rows['Energy in the period (kWh)'], 0.2)                # 10 h x 20 W
        self.assertEqual(rows['Cost in the period'], '400 VND')
        self.assertIn(insights.ENERGY_NOTE, report['notes'])
        health = {title: value for section, title, value in report['extra_rows'] if section == 'health'}
        self.assertEqual((health['Health score (0-100)'], health['Score part: Battery']), (100, 'Unknown'))
        self.assertEqual((rows['Awake after hours, last 7 days (hours)'], rows['Cost after hours, last 7 days'],
                          rows['Hours used']), (3.0, '120 VND', 'office hours 08:00–18:00'))
        fleet = reports.fleet_report([self.device('a'), self.device('b'), self.device('c')], '2026-11-10', '2026-11-10',
                                     insights=energy)
        rows = {title: value for section, title, value in fleet['extra_rows'] if section == 'energy'}
        self.assertEqual(rows['Energy in the period (kWh)'], 0.7)               # 0.2 + 10 h x 50 W
        self.assertEqual((rows['Awake after hours, last 7 days (hours)'], rows['Without hourly history (unknown)']), (3.0, 2))
        self.assertEqual((rows['Measured on battery'], rows['Estimated from typical power'],
                          rows['Without shared awake history (unknown)']), (1, 1, 1))
        vi = reports.device_report(self.device(), '2026-11-10', '2026-11-10', language='vi', insights=energy)
        self.assertIn('ước tính: máy xách tay điển hình', [value for *_, value in vi['extra_rows']])


class AdminTests(unittest.TestCase):
    _base = test_server_runtime.RuntimeTests
    setUp, public, enrollment, claim, message = _base.setUp, _base.public, _base.enrollment, _base.claim, _base.message
    call, people = test_server_access.AccessTests.call, test_server_access.AccessTests.people
    admin_raw, wire = _base.admin_raw, _base.wire

    def test_everyone_reads_only_admins_change_and_values_are_checked(self):
        people = self.people()
        status, result = self.call(people['viewer'], 'insights')
        self.assertEqual(status, 200)
        self.assertEqual(result['settings']['currency'], 'VND')
        self.assertEqual(self.call(people['operator'], 'set_insight_settings', values=dict(price=3000))[0], 403)
        self.assertEqual(self.call(people['admin'], 'set_insight_settings', values=dict(currency='dong'))[0], 400)
        status, saved = self.call(people['admin'], 'set_insight_settings', values=dict(price=3000, group_watts={'Lab': 60}))
        self.assertEqual((status, saved['price'], saved['group_watts']), (200, 3000.0, {'lab': 60.0}))
        self.assertEqual(self.admin.call('insights')['settings']['price'], 3000.0)
        self.assertEqual(self.runtime.access.audit(limit=1)[0]['action'], 'set_insight_settings')
        # Daily readings kept by monitoring reach the forecast only while the computer shares its readings.
        shared, private = self.computer(cpu=5), self.computer()
        rows = [(device, (TODAY - dt.timedelta(days=d)).isoformat(), None, 20 + d / 10) for device in (shared, private)
                for d in (0, 30, 60, 90)]
        with self.runtime.monitor._transaction() as db:
            db.executemany('INSERT OR REPLACE INTO trends VALUES (?,?,?,?)', rows)
        found = {c['device_id']: c for c in self.admin.call('insights')['computers']}
        disk = next(p for p in found[shared]['replace'] if p['part'] == 'disk')
        self.assertEqual((disk['points'], disk['status']), (4, 'expected'))
        self.assertEqual(found[private]['replace'], [])


    def computer(self, control=True, cpu=None, group='Lab'):
        """An approved computer reporting now, in ``group``, with or without remote-power consent."""
        request = self.enrollment()
        request['identity'] = dict(display_name='PC', department=group)
        request['policy'] = dict(history=False, resources=cpu is not None, control=control)
        self.public('enroll', request)
        self.admin.call('approve', request_id=request['request_id'])
        credential = self.claim(request)['credential']
        message = self.message(request)
        if cpu is not None:
            message['readings'] = dict(observed_at=dt.datetime.now(dt.timezone.utc).isoformat(), specs={},
                                       performance=dict(cpu_percent=cpu))
        self.public('sync', message, credential)
        return request['device_id']

    def test_groups_are_admin_operations_and_a_rename_carries_the_group_settings(self):
        people = self.people()
        device = self.computer()
        self.assertEqual(self.call(people['viewer'], 'groups')[0], 200)
        self.assertEqual(self.call(people['operator'], 'add_group', group='Spare')[0], 403)
        status, groups = self.call(people['admin'], 'add_group', group='Spare')
        self.assertEqual((status, [g['name'] for g in groups]), (200, ['Lab', 'Spare']))
        self.assertEqual(self.runtime.access.audit(limit=1)[0]['target'], 'group=Spare')
        self.assertEqual(self.call(people['admin'], 'set_sleep_policy', group='Lab', enabled=True)[0], 200)
        self.admin.call('set_insight_settings', values=dict(group_watts={'Lab': 60}))
        self.admin.call('set_schedule', scope='group', key='Lab', kind='business')
        status, groups = self.call(people['admin'], 'rename_group', group='Lab', new_name='Science')
        self.assertEqual((status, [g['name'] for g in groups]), (200, ['Science', 'Spare']))
        row = self.runtime.access.audit(limit=1)[0]
        self.assertEqual((row['action'], row['target'], row['result']), ('rename_group', 'group=Lab', 'ok'))
        self.assertEqual(self.admin.call('device', device_id=device)['identity']['department'], 'Science')
        found = self.admin.call('insights')
        self.assertEqual(list(found['sleep_policies']), ['science'])
        self.assertEqual(found['settings']['group_watts'], {'science': 60.0})
        self.assertEqual([c['group'] for c in found['computers']], ['Science'])
        self.assertIn(('group', 'science'), [(s['scope'], s['key']) for s in self.admin.call('schedules')])

    def test_sleep_after_hours_needs_an_account_role_and_sends_single_audited_commands(self):
        people = self.people()
        idle, busy, refused, other = self.computer(), self.computer(cpu=80), self.computer(control=False), self.computer(group='Office')
        # The shared token and an administrator by group membership only cannot turn it on; an assigned Admin can.
        self.assertEqual(self.admin_raw('/v1/set_sleep_policy', {'group': 'Lab', 'enabled': True})[0], 403)
        builtin = dict(sid=test_server_access.DOMAIN + '9', name=r'LAB\local-admin', groups=['S-1-5-32-544'], channel='pipe')
        self.assertEqual(self.call(builtin, 'set_sleep_policy', group='Lab', enabled=True)[0], 403)
        self.assertEqual(self.call(people['operator'], 'set_sleep_policy', group='Lab', enabled=True)[0], 403)
        self.assertEqual(self.call(people['admin'], 'set_sleep_policy', group='Lab', enabled=True)[0], 200)
        self.assertEqual(self.admin.call('insights')['sleep_policies']['lab']['name'], people['admin']['name'])
        local = dt.datetime.now().astimezone().tzinfo
        working = dt.datetime(2026, 11, 17, 10, 0, tzinfo=local)        # Tuesday 10:00: open
        self.assertEqual(self.runtime.sleep_after_hours(working), [])
        just_closed = dt.datetime(2026, 11, 17, 18, 20, tzinfo=local)   # 20 minutes after closing: grace
        self.assertEqual(self.runtime.sleep_after_hours(just_closed), [])
        night = dt.datetime(2026, 11, 17, 23, 0, tzinfo=local)
        self.assertEqual(self.runtime.sleep_after_hours(night), [idle])      # not busy, not refused, not Office
        commands = self.runtime._store.get_device(idle)['commands']
        self.assertEqual([(c['action'], c['status']) for c in commands], [('sleep', 'pending')])
        row = self.runtime.access.audit(limit=1)[0]
        self.assertEqual((row['action'], row['actor_sid'], row['actor_name'], row['target']),
                         ('send_command', people['admin']['sid'], people['admin']['name'] + ' (after-hours policy)',
                          f'device_id={idle}'))
        for device in (busy, refused, other):
            self.assertEqual(self.runtime._store.get_device(device)['commands'], [])
        self.runtime._store.cancel_command(commands[0]['command_id'])      # e.g. it was woken again: not sent twice
        self.assertEqual(self.runtime.sleep_after_hours(night + dt.timedelta(hours=1)), [])
        # Removing the account's role stops the policy at once.
        self.runtime._sleep_tried.clear()
        with patch.object(self.runtime._store, 'sleep_candidates', return_value=[dict(
                device_id=idle, group='lab', online=True, busy=False, waiting=False, last_sleep=None)]):
            self.runtime.access.delete_role(people['admin']['sid'])
            self.assertEqual(self.runtime.sleep_after_hours(night), [])
        self.assertEqual(self.call(people['viewer'], 'set_sleep_policy', group='Lab', enabled=False)[0], 403)


if __name__ == '__main__':
    unittest.main()
