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

import test_compat  # noqa: F401
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
            'a': dict(power=dict(watts=20.0, method='estimated', source='typical-laptop', readings=0), energy={}),
            'b': dict(power=dict(watts=50.0, method='measured', source='battery', readings=12), energy={})})
        report = reports.device_report(self.device(), '2026-11-10', '2026-11-10', insights=energy)
        rows = {title: value for section, title, value in report['extra_rows'] if section == 'energy'}
        self.assertEqual(rows['Power method'], 'estimated: typical laptop')
        self.assertEqual(rows['Energy in the period (kWh)'], 0.2)                # 10 h x 20 W
        self.assertEqual(rows['Cost in the period'], '400 VND')
        self.assertIn(insights.ENERGY_NOTE, report['notes'])
        fleet = reports.fleet_report([self.device('a'), self.device('b'), self.device('c')], '2026-11-10', '2026-11-10',
                                     insights=energy)
        rows = {title: value for section, title, value in fleet['extra_rows'] if section == 'energy'}
        self.assertEqual(rows['Energy in the period (kWh)'], 0.7)               # 0.2 + 10 h x 50 W
        self.assertEqual((rows['Measured on battery'], rows['Estimated from typical power'],
                          rows['Without shared awake history (unknown)']), (1, 1, 1))
        vi = reports.device_report(self.device(), '2026-11-10', '2026-11-10', language='vi', insights=energy)
        self.assertIn('ước tính: máy xách tay điển hình', [value for *_, value in vi['extra_rows']])


class AdminTests(unittest.TestCase):
    _base = test_server_runtime.RuntimeTests
    setUp, public, enrollment, claim, message = _base.setUp, _base.public, _base.enrollment, _base.claim, _base.message
    call, people = test_server_access.AccessTests.call, test_server_access.AccessTests.people

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


if __name__ == '__main__':
    unittest.main()
