"""Health fixtures, privacy, bounded failures and the versioned server extension."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import lifetime_health as health
from lifetime_protocol import validate_sync, ProtocolError
from lifetime_settings import normalize


def event(provider, identity, when, data=''):
    return f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="{provider}"/><EventID>{identity}</EventID><TimeCreated SystemTime="{when}"/></System><EventData>{data}</EventData></Event>'


REPORT = '''<BatteryReport xmlns="http://schemas.microsoft.com/battery/2012">
<Batteries><Battery><DesignCapacity>80000</DesignCapacity><FullChargeCapacity>85920</FullChargeCapacity><CycleCount>16</CycleCount><Chemistry>LiP</Chemistry></Battery></Batteries>
<History><HistoryEntry StartDate="2026-11-01T00:00:00Z" FullChargeCapacity="85920" DesignCapacity="80000"/></History>
<RecentUsage><UsageEntry Timestamp="2026-11-01T10:00:00Z" EntryType="Active" Ac="0"/><UsageEntry Timestamp="2026-11-01T11:00:00Z" EntryType="Active" Ac="1"/><UsageEntry Timestamp="2026-11-01T13:00:00Z" EntryType="Suspend" Ac="1"/><UsageEntry Timestamp="2026-11-01T14:00:00Z" EntryType="ReportGenerated" Ac="1"/></RecentUsage></BatteryReport>'''


def summary():
    return dict(version=1, observed_at='2026-11-08T12:00:00Z', shutdowns_30d=2, crashes_30d=3,
                battery_health=107.4, cycle_count=16, last_crash='2026-11-07T10:00:00Z')


def payload():
    return dict(version=1, device_id=str(uuid.uuid4()), policy_version=1,
                policy=dict(history=False, resources=False), records=[])


class ParserTests(unittest.TestCase):
    def test_events_invariant_provider_ids_dedup_and_dates(self):
        raw = '<Events>'+event('Microsoft-Windows-Kernel-Power', 41, '2026-11-01T10:00:00Z')+event('EventLog', 6008, '2026-11-01T10:00:15Z')+event('Microsoft-Windows-WER-SystemErrorReporting', 1001, '2026-11-01T10:00:20.1234567Z')+event('Other', 41, '2026-11-01T10:00:30Z')+'</Events>'
        result = health.parse_windows_events(raw)
        self.assertEqual([r['kind'] for r in result], ['Unexpected shutdown', 'Crash'])
        self.assertEqual(health.stamp(result[1]['date']).microsecond, 123456)
        self.assertIsNone(health.parse_windows_events('<bad>'))
        self.assertEqual(health.parse_windows_events('<Events/>'), [])

    def test_report_real_attribute_schema_health_above_100_usage(self):
        result = health.parse_battery_report(REPORT)
        self.assertAlmostEqual(result['packs'][0]['health'], 107.4)
        self.assertEqual(result['packs'][0]['cycles'], 16)
        self.assertEqual(result['history'][0]['full'], 85.92)
        self.assertEqual(sum(v['battery'] for v in result['usage'].values()), 3600)
        self.assertEqual(sum(v['ac'] for v in result['usage'].values()), 7200)
        self.assertIsNone(health.parse_battery_report('bad'))
        self.assertEqual(health.parse_battery_report('<BatteryReport><Batteries/></BatteryReport>')['packs'], [])

    def test_wmi_matches_instance_not_position(self):
        result = health.parse_wmi_battery(json.dumps(dict(design=[dict(InstanceName='a', DesignedCapacity=50000),dict(InstanceName='b',DesignedCapacity=40000)],
            full=[dict(InstanceName='b',FullChargedCapacity=42000),dict(InstanceName='a',FullChargedCapacity=45000)],cycles=dict(InstanceName='a',CycleCount=4))))
        self.assertEqual([p['health'] for p in result], [90, 105])
        self.assertEqual(result[0]['cycles'], 4)
        self.assertIsNone(result[1]['cycles'])
        self.assertIsNone(health.parse_wmi_battery('bad'))

    def test_sysfs_energy_charge_missing_and_zero_design(self):
        packs = health.parse_sysfs([dict(energy_full='55000000',energy_full_design='50000000',cycle_count='12',technology='Li-ion'),
                                  dict(charge_full='5000000',charge_full_design='0'), {}])
        self.assertEqual(packs[0]['health'], 110)
        self.assertEqual(packs[0]['unit'], 'Wh')
        self.assertEqual(packs[1]['unit'], 'Ah')
        self.assertIsNone(packs[1]['health'])
        self.assertIsNone(packs[2]['full'])
        self.assertEqual(health.parse_sysfs([]), [])

    def test_linux_sysfs_probe_with_fixture_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            pack = root/'BAT0'; pack.mkdir()
            for name, value in dict(energy_full='55000000', energy_full_design='50000000', cycle_count='12', technology='Li-ion').items():
                (pack/name).write_text(value)
            with patch.object(health.sys, 'platform', 'linux'), patch.object(health, 'Path', side_effect=lambda name: root if name == '/sys/class/power_supply' else Path(name)):
                result = health.probe_battery()
            self.assertEqual(result['packs'][0]['health'], 110)
            self.assertEqual(result['packs'][0]['cycles'], 12)
            self.assertIsNone(result['usage'])

    def test_journal_boots_tail_and_panic(self):
        from lifetime_import import parse_boots
        boots = parse_boots('[{"boot_id":"abc","first_entry":1000000,"last_entry":3000000,"index":-1}]')
        self.assertEqual(boots[0]['end'], 3)
        clean = json.dumps(dict(_PID='1', MESSAGE='Reached target System Power Off', __REALTIME_TIMESTAMP='3000000'))
        self.assertEqual(health.parse_journal_tail(clean,3), [])
        raw = json.dumps(dict(MESSAGE='Kernel panic - not syncing',__REALTIME_TIMESTAMP='3000000'))
        self.assertEqual(len(health.parse_journal_tail(raw,3)),2)
        self.assertEqual(len(health.parse_journal_tail(raw,3,current=True)),1)
        self.assertIsNone(health.parse_journal_tail('',3))
        self.assertIsNone(health.parse_journal_tail('bad',3))

    def test_sleep_sources_completed_intervals_and_no_reboot_bridge(self):
        sleep = dt.datetime(2026,11,1,23).timestamp()
        wake = dt.datetime(2026,11,2,7).timestamp()
        raw = '\n'.join(json.dumps(dict(MESSAGE=message,__REALTIME_TIMESTAMP=str(int(when*1e6)))) for message, when in [('PM: suspend entry',sleep),('PM: suspend exit',wake)])
        result = health.parse_journal_sleep(raw)
        self.assertEqual(sum(v['seconds'] for v in result['daily'].values()),28800)
        self.assertEqual(sum(v['cycles'] for v in result['daily'].values()),1)
        self.assertEqual(result['nights'], [dict(date='2026-11-01',seconds=28800)])
        split = health.sleep_days([(sleep, 'sleep'),(wake, 'wake'),(wake+3600,'sleep'),(wake+7200,'wake')])
        self.assertEqual(split['nights'], [dict(date='2026-11-01',seconds=32400)])
        self.assertEqual(health.sleep_days([(sleep,'sleep')])['daily'],{})
        self.assertEqual(health.sleep_days([(sleep,'sleep'),(sleep+10,'reset'),(wake,'wake')])['daily'],{})
        data = '<Data Name="SleepTime">2026-11-01T23:00:00Z</Data><Data Name="WakeTime">2026-11-02T07:00:00Z</Data>'
        xml = '<Events>'+event('Microsoft-Windows-Power-Troubleshooter',1,'2026-11-02T07:00:00Z',data)+'</Events>'
        result = health.parse_windows_sleep(xml)
        self.assertEqual(sum(v['seconds'] for v in result['daily'].values()),28800)
        self.assertIsNone(health.parse_windows_sleep('bad'))
        self.assertIsNone(health.parse_journal_sleep('bad'))

    def test_machine_dates(self):
        result = health.parse_machine(dict(model='Model',bios='03/12/2023',installed='2026-11-01',estimated=True))
        self.assertEqual(result['bios'],'2023-03-12')
        self.assertTrue(result['estimated'])
        self.assertEqual(health.parse_machine('{"bios":"20240102000000.000000+000"}')['bios'],'2024-01-02')
        self.assertIsNone(health.parse_machine('bad'))

    def test_capacity_400_days_replaces_daily_and_summary_unknown(self):
        ledger = {}
        start = dt.date(2025,1,1)
        pack = dict(packs=[dict(full=50,unit='Wh')])
        for day in range(450):
            health.capacity_sample(ledger, pack, start+dt.timedelta(days=day))
        self.assertEqual(len(ledger['health_capacity']),400)
        health.capacity_sample(ledger, pack, start+dt.timedelta(days=449))
        self.assertEqual(len(ledger['health_capacity']),400)
        result = health.summary(dict(observed_at='2026-11-08T12:00:00Z',stability=None,battery=None))
        self.assertIsNone(result['crashes_30d'])

    def test_off_never_probes_and_existing_settings_off(self):
        self.assertFalse(normalize(dict(completed=True))['health'])
        with patch.object(health,'probe_stability') as probe, patch.object(health,'probe_battery') as battery:
            for settings in ({},dict(health=True),dict(completed=True,health=False)):
                self.assertIsNone(health.collect(settings))
            probe.assert_not_called(); battery.assert_not_called()

    def test_probe_deadline_and_subprocess_failures(self):
        @health.bounded
        def slow():
            time.sleep(.1)
            return 1
        with patch.object(health,'TIMEOUT',.01):
            self.assertIsNone(slow())
        with patch.object(health.subprocess,'run',side_effect=OSError()):
            self.assertIsNone(health.command(['missing']))
        with patch.object(health,'command',return_value=None):
            self.assertIsNone(health.probe_stability())
            self.assertIsNone(health.probe_sleep())


class ProtocolHealthTests(unittest.TestCase):
    def test_machine_agent_gate_cache_and_settings_revocation(self):
        import lifetime_agent as agent
        from lifetime_company import update_health_consent
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder, patch.object(agent, 'protect_root'):
            machine = agent.MachineAgent.initialize(Path(folder)/'agent')
            try:
                with patch.object(health, 'collect') as collect:
                    machine.sample(10, 'boot-a', '2026-11-08T12:00:00Z')
                    collect.assert_not_called()
                machine.set_health_consent(True)
                data = dict(observed_at='2026-11-08T12:00:00Z', stability=[],battery=health.parse_battery_report(REPORT))
                with patch.object(health, 'collect',return_value=data) as collect:
                    machine.sample(20, 'boot-a', '2026-11-08T12:00:10Z')
                    machine.sample(30, 'boot-a', '2026-11-08T12:00:20Z')
                    self.assertEqual(collect.call_count,1)
                self.assertEqual(machine.batch()['health']['battery_health'],107.4)
                self.assertIn('health_capacity',machine.ledger())
                update_health_consent(False,Path(folder)/'agent')
                with patch.object(health,'collect') as collect:
                    machine.sample(40,'boot-a','2026-11-08T12:00:30Z')
                    collect.assert_not_called()
                self.assertNotIn('health',machine.batch())
                self.assertNotIn('health_capacity',machine.ledger())
            finally:
                machine.close()

    def test_old_payload_and_versioned_extension(self):
        value = payload(); validate_sync(value)
        value['policy']['health'] = True; value['health'] = summary(); validate_sync(value)
        self.assertEqual(value['health']['battery_health'],107.4)

    def test_bounds_consent_types_versions_and_dates(self):
        for field, bad in [('version',2),('version',True),('battery_health',float('nan')),('battery_health',1001),('cycle_count',100001),('cycle_count',1.5),('crashes_30d',10001),('shutdowns_30d',-1),('last_crash','2020-01-01T00:00:00Z')]:
            value = payload(); value['policy']['health']=True; value['health']=summary(); value['health'][field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(ProtocolError): validate_sync(value)
        value=payload(); value['health']=summary()
        with self.assertRaises(ProtocolError): validate_sync(value)

    def test_store_summary_hint_revoke_stale_and_backup(self):
        import lifetime_server_store as storage
        with tempfile.TemporaryDirectory() as folder:
            store=storage.Store(Path(folder)/'s.sqlite')
            self.addCleanup(store.close)
            import secrets
            request=dict(version=1, request_id=str(uuid.uuid4()),device_id=str(uuid.uuid4()),code=store.create_code()['code'],poll_token=secrets.token_urlsafe(32),identity={},policy=dict(history=False,resources=False,health=True))
            store.enroll(request); store.approve(request['request_id'])
            credential=store.claim(request['request_id'],request['poll_token'])['credential']
            value=payload(); value.update(device_id=request['device_id'],policy=request['policy'],health=summary())
            store.ingest(request['device_id'],credential,value)
            self.assertEqual(store.get_device(request['device_id'])['health'],summary())
            self.assertTrue(store.list_devices()[0]['unstable'])
            stale = copy.deepcopy(value)
            stale['health'].update(observed_at='2026-11-08T11:00:00Z', crashes_30d=0, last_crash=None)
            store.ingest(request['device_id'],credential,stale)
            self.assertEqual(store.get_device(request['device_id'])['health'],summary())
            store.backup(Path(folder)/'backup.sqlite')
            value['policy_version']=2; value['policy']=dict(history=False,resources=False); value.pop('health')
            store.ingest(request['device_id'],credential,value)
            self.assertIsNone(store.get_device(request['device_id'])['health'])
            self.assertFalse(store.list_devices()[0]['unstable'])
            value.update(policy_version=1,policy=request['policy'],health=summary())
            store.ingest(request['device_id'],credential,value)
            self.assertIsNone(store.get_device(request['device_id'])['health'])
            store.close()


class RemoteDetailTests(unittest.TestCase):
    def test_resource_health_persists_without_separate_health_consent_and_revokes(self):
        import contextlib
        import lifetime_server_store as storage
        import secrets
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as cleanup:
            store = storage.Store(Path(folder)/'server.sqlite')
            cleanup.callback(store.close)
            policy = dict(history=True, resources=True)
            request = dict(version=1, request_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                           code=store.create_code()['code'], poll_token=secrets.token_urlsafe(32),
                           identity={}, policy=policy)
            store.enroll(request)
            store.approve(request['request_id'])
            credential = store.claim(request['request_id'], request['poll_token'])['credential']
            value = payload()
            reading = dict(summary(), detail=dict(stability=[], battery={'packs': []}))
            value.update(device_id=request['device_id'], policy=policy,
                         readings=dict(observed_at=reading['observed_at'], health=reading),
                         history=dict(daily={}, hourly=[0]*24, boots=7))
            store.ingest(request['device_id'], credential, value)
            store.close()
            store = storage.Store(Path(folder)/'server.sqlite')
            cleanup.callback(store.close)
            saved = store.get_device(request['device_id'])
            self.assertEqual(saved['readings']['health'], reading)
            self.assertEqual(saved['history']['boots'], 7)
            self.assertIsNone(saved['health'])
            value.update(policy_version=2, policy=dict(history=False, resources=False))
            value.pop('readings')
            value.pop('history')
            store.ingest(request['device_id'], credential, value)
            saved = store.get_device(request['device_id'])
            self.assertIsNone(saved['readings'])
            self.assertIsNone(saved['history'])

    def test_resources_consent_and_nested_bounds(self):
        value = payload()
        value['policy']['resources'] = True
        reading = summary()
        reading['detail'] = dict(stability=[], battery={'packs': []}, sleep={'daily': {}, 'nights': []})
        value['readings'] = dict(observed_at=reading['observed_at'], health=reading)
        validate_sync(value)
        value['policy']['resources'] = False
        with self.assertRaises(ProtocolError):
            validate_sync(value)
        value['policy']['resources'] = True
        for bad in ([{'date': '2026-11-08T12:00:00Z', 'kind': 'Crash'}]*401,
                    [{'date': '2026-11-08T12:00:00Z', 'kind': 'unknown'}]):
            reading['detail']['stability'] = bad
            with self.assertRaises(ProtocolError):
                validate_sync(value)

    def test_history_bounds_and_legacy_format(self):
        import lifetime_protocol as protocol
        value = payload()
        value['policy']['history'] = True
        value['history'] = dict(daily={}, hourly=[0]*24, sessions=[], boots=1)
        validate_sync(value)
        old = protocol.without_extended(value)
        self.assertEqual(old['history'], {'daily': {}})
        value['history']['sessions'] = [dict(start='2026-11-08T12:00:00Z', seconds=True)]
        with self.assertRaises(ProtocolError):
            validate_sync(value)

    def test_retention_trims_nested_series_from_fresh_readings(self):
        from lifetime_server_store import Store
        reading = dict(observed_at='2026-11-08T12:00:00Z', health=dict(last_crash='2026-01-01T00:00:00Z', detail=dict(
            stability=[dict(date='2026-01-01T00:00:00Z', kind='Crash')],
            battery=dict(usage={'2026-01-01': dict(ac=1, battery=2)}, history=[dict(date='2026-01-01', full=30)]),
            sleep=dict(daily={'2026-01-01': dict(cycles=1, seconds=3)}, nights=[dict(date='2026-01-01', seconds=3)]))))
        result = Store._retained_reading(reading, '2026-11-01T00:00:00Z')
        self.assertIsNone(result['health']['last_crash'])
        self.assertEqual(result['health']['detail']['stability'], [])
        self.assertEqual(result['health']['detail']['battery']['usage'], {})
        self.assertEqual(result['health']['detail']['sleep']['nights'], [])
        self.assertIsNotNone(reading['health']['last_crash'])

    def test_detail_shaping_caps_and_privacy(self):
        data = dict(stability=[], battery=dict(packs=[dict(full=50, design=60, cycles=42.0, serial='private')]),
                    sleep=dict(daily={}, nights=[]))
        detail = health.detail(data)
        self.assertNotIn('serial', detail['battery']['packs'][0])
        self.assertIs(type(detail['battery']['packs'][0]['cycles']), int)
        from lifetime_protocol import validate_health_detail
        validate_health_detail(detail)


if __name__ == '__main__':
    unittest.main()
