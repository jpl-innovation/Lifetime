"""Data written by the released 1.1.0 opens with this version and nothing is lost.

``fixtures/upgrade_1_1_0`` was written by the v1.1.0 tag's own code (its server, agent and tracker, run from a git
worktree of the tag on 127.0.0.1 with temporary folders): the server database after one computer enrolled, was
approved and reported five times; that computer's own database; and a desktop ledger and settings. ``expected.json``
is what the 1.1.0 server and tracker themselves reported for that data. Nothing in it is live: the computer's
credential was replaced by a fixed fake value (and its hash on the server), the certificate belongs to a deleted
test key, and names, hardware and boot evidence are made up.
"""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import lifetime_agent as agent
from lifetime_client import SyncClient
import lifetime_ledger as schema
import lifetime_settings as settings
import lifetime_server_store as storage
import lifetime_tls as tls
import lifetime_tracker as tracker

FIXTURE = Path(__file__).parent / 'fixtures' / 'upgrade_1_1_0'
EXPECTED = json.loads((FIXTURE / 'expected.json').read_text(encoding='utf-8'))
DEVICE = EXPECTED['device_id']


def database(name, path):
    """A SQLite file as 1.1.0 left it, from its text dump."""
    db = sqlite3.connect(path)
    try:
        db.executescript((FIXTURE / name).read_text(encoding='utf-8'))
    finally:
        db.close()
    return path


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def store(self, name='server.sqlite'):
        path = database('server.sql', self.root / name)
        raw = sqlite3.connect(path)
        try:
            self.assertEqual(raw.execute('PRAGMA user_version').fetchone()[0], 7)       # what 1.1.0 wrote
        finally:
            raw.close()
        store = storage.Store(path)
        self.addCleanup(store.close)
        return store

    def assert_kept(self, device):
        for key in ('record_count', 'totals', 'retained_usage', 'identity', 'policy'):
            self.assertEqual(device[key], EXPECTED[key], key)
        self.assertEqual(device['history']['daily'], EXPECTED['history_daily'])
        self.assertEqual(device['readings']['specs']['cpu_name'], EXPECTED['cpu_name'])

    def test_server_database_migrates_and_keeps_every_computer_record_and_setting(self):
        store = self.store()
        self.assertEqual(store._db.execute('PRAGMA user_version').fetchone()[0], storage.SCHEMA_VERSION)
        device = store.get_device(DEVICE)
        self.assertEqual(device['status'], 'approved')
        self.assert_kept(device)
        self.assertEqual([group['name'] for group in store.list_groups()], [EXPECTED['identity']['department']])
        self.assertEqual(store.server_names(), dict(names=[]))             # new settings start at their defaults
        self.assertEqual(store.server_log(), [])
        backup = self.root / 'after-upgrade.sqlite'
        store.backup(backup)                                                # validates every row against this version
        self.assertTrue(backup.is_file())

    def test_a_backup_made_by_1_1_0_restores_into_this_version(self):
        old_backup = database('server.sql', self.root / 'backup-from-1.1.0.sqlite')
        fresh = storage.Store(self.root / 'fresh.sqlite')
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.restore(old_backup)['schema_version'], storage.SCHEMA_VERSION)
        device = fresh.get_device(DEVICE)
        self.assertEqual(device['status'], 'revoked')                       # a restore always asks computers to pair again
        self.assert_kept(device)

    def test_the_1_1_0_computer_database_opens_and_keeps_reporting_to_the_upgraded_server(self):
        store = self.store()
        machine_root = self.root / 'machine'
        machine_root.mkdir()
        database('agent.sql', machine_root / agent.DATABASE)
        if sys.platform == 'win32':
            self.enterContext(patch.object(agent, 'protect_root'))
        machine = agent.MachineAgent(machine_root)
        self.addCleanup(machine.close)
        state = machine._load()
        self.assertEqual((state['device_id'], state['phase'], state['ledger']['total_seconds']),
                         (DEVICE, 'approved', EXPECTED['agent_total']))
        self.assertEqual(machine.status()['policy'], EXPECTED['policy'])

        def exchange(bundle, path, payload, credential=None, timeout=10):
            self.assertEqual(path, '/v1/sync')
            return dict(store.ingest(payload['device_id'], credential, copy.deepcopy(payload)),
                        capabilities=['history.detail', 'health.detail', 'hardware', 'problem', 'update'])
        self.enterContext(patch.object(tls, 'request_json', side_effect=exchange))
        self.enterContext(patch('lifetime_discovery.discover', return_value=[]))
        stamp = '2026-11-20T08:00:00+00:00'
        end = 1795161600.0
        tick = state['ledger']['last_clock'] + 30
        with patch.object(storage, '_now', return_value=storage._time(stamp)):
            machine.sample(tick, state['ledger']['boot_id'], stamp, boot_evidence=(end - 30, [(end - 30, end)], False),
                           resources_sampler=lambda: dict(system='Windows', performance=dict(cpu_percent=5)))
            status = SyncClient(machine).step(force=True)
            self.assertEqual((status['status'], status['pending'], status['error']), ('connected', 0, None))
            device = store.get_device(DEVICE)
        self.assertEqual(device['record_count'], EXPECTED['record_count'] + 1)
        self.assertEqual(device['totals']['measured_seconds'], EXPECTED['totals']['measured_seconds'] + 30)
        self.assertEqual(device['readings']['specs']['cpu_name'], EXPECTED['cpu_name'])    # specs kept from before
        self.assertEqual(device['client_version'], __import__('lifetime_protocol').CLIENT_VERSION)

    def test_desktop_ledger_and_settings_load_unchanged_and_the_daily_repair_keeps_every_total(self):
        data = self.root / 'desktop'
        data.mkdir()
        for name in ('ledger.json', 'settings.json'):
            shutil.copyfile(FIXTURE / name, data / name)
        ledger = tracker.read_ledger(data)
        schema.validate(ledger)
        want = EXPECTED['ledger']

        def facts(value):
            return dict(total_seconds=value['total_seconds'], baseline_seconds=value['baseline_seconds'],
                        boots=value['boots'], days=len(value['daily']), daily_sum=sum(value['daily'].values()))
        self.assertEqual(facts(ledger), want)
        tracker.repair_daily(data, evidence=[])
        self.assertEqual(facts(tracker.read_ledger(data)), want)
        value = settings.load_settings(data)
        self.assertEqual((value['completed'], value['history'], value['resources'], value['battery'], value['language'],
                          value['theme'], value['autostart']), (True, True, True, True, 'vi', 'dark', False))
        self.assertEqual((value['identity']['asset_tag'], value['identity']['department']), ('LT-0042', 'Phòng Kế toán'))
        self.assertEqual(value.get('style', 'classic'), 'classic')         # a setting 1.1.0 did not have: its default


if __name__ == '__main__':
    unittest.main()
