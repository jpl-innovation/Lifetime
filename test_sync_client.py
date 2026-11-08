"""SQLite/store simulations plus actual ServerRuntime/AdminClient HTTPS integration.

Windows ACLs are explicitly mocked on temporary fixtures. Real TLS verification,
HTTP routing, enrollment, approval, credential claim and sync run without mocks
in RuntimeIntegrationTests.
"""
import copy
import contextlib
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import lifetime_agent as agent
from lifetime_client import SyncClient
import lifetime_protocol as protocol
import lifetime_server_store as storage
import lifetime_tls as tls
from test_machine_agent import AgentFixture, STAMP


class SyncFixture(AgentFixture):
    def setUp(self):
        super().setUp()
        self.store = storage.Store(Path(self.temporary.name) / 'server.sqlite')
        self.addCleanup(self.store.close)
        certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
        if not certificates:
            self.skipTest('An OS certificate is needed for stdlib bundle test data')
        self.pem = ssl.DER_cert_to_PEM_cert(certificates[0])
        self.code = self.store.create_code()['code']
        self.bundle = tls.encode_bundle('https://localhost:8443', self.code, self.pem)
        self.calls = []
        self.transport = self.enterContext(patch.object(tls, 'request_json', side_effect=self.exchange))
        self.client = SyncClient(self.machine)

    def exchange(self, bundle, path, payload, credential=None, timeout=10):
        self.calls.append((path, copy.deepcopy(payload), credential))
        try:
            if path == '/v1/enroll':
                return self.store.enroll(payload)
            if path == '/v1/claim':
                self.assertEqual(set(payload), {'request_id', 'poll_token'})
                return self.store.claim(**payload)
            if path == '/v1/sync':
                return self.store.ingest(payload['device_id'], credential, payload)
            self.fail('Unexpected route')
        except storage.ConflictError:
            raise tls.HTTPSRequestError(409) from None
        except storage.AuthenticationError:
            raise tls.HTTPSRequestError(403) from None

    def pair(self, policy=None):
        policy = dict(history=False, resources=False) if policy is None else policy
        self.client.start_enrollment(self.bundle, {'display_name': 'M\u00e1y kh\u00e1ch'}, policy)
        request = self.machine._load()['enrollment']
        self.store.approve(request['request_id'])
        self.assertEqual(self.client.step(force=True)['status'], 'approved')
        return request

    def restart(self):
        self.reopen()
        self.client = SyncClient(self.machine)


class SyncTests(SyncFixture):
    def test_no_constructor_or_standalone_capture_network(self):
        self.sample()
        self.client.step(force=True)
        self.restart()
        self.client.step(force=True)
        self.transport.assert_not_called()

    def test_enrollment_recovery_material_is_durable_before_request(self):
        observed = []
        def lost(bundle, path, payload, **kwargs):
            with contextlib.closing(sqlite3.connect(self.root / agent.DATABASE)) as reader:
                saved = json.loads(reader.execute('SELECT value FROM state').fetchone()[0])
            self.assertEqual(saved['enrollment'], payload)
            self.assertEqual(saved['device_id'], payload['device_id'])
            self.assertTrue(saved['enabled'])
            observed.append(copy.deepcopy(payload))
            self.store.enroll(payload)
            raise TimeoutError('lost enrollment acknowledgment')
        self.transport.side_effect = lost
        self.client.start_enrollment(self.bundle, {}, dict(history=False, resources=False))
        self.restart()
        self.transport.side_effect = self.exchange
        self.client.step(force=True)
        self.assertEqual(self.calls[-1][1], observed[0])
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM requests').fetchone()[0], 1)
        self.assertEqual(self.store._db.execute('SELECT remaining FROM codes').fetchone()[0], 0)

    def test_claim_lost_response_retried_and_credential_persisted_before_sync(self):
        self.client.start_enrollment(self.bundle, {}, dict(history=False, resources=False))
        request = self.machine._load()['enrollment']
        self.store.approve(request['request_id'])
        def lost(*args, **kwargs):
            self.exchange(*args, **kwargs)
            raise TimeoutError('lost claim acknowledgment')
        self.transport.side_effect = lost
        self.client.step(force=True)
        self.assertIsNone(self.machine._load()['credential'])
        self.restart()
        self.transport.side_effect = self.exchange
        self.client.step(force=True)
        credential = self.machine._load()['credential']
        self.assertTrue(credential)
        self.restart()
        self.sample()
        self.assertEqual(self.client.step(force=True)['status'], 'connected')
        self.assertEqual(self.calls[-1][2], credential)
        self.assertEqual(self.machine.status()['pending'], 0)
        redacted = (self.root / 'status.json').read_text()
        for secret in (credential, request['request_id'], request['poll_token'], self.code, self.machine.device_id):
            self.assertNotIn(secret, redacted)

    def test_lost_sync_ack_retries_immutable_records_and_adds_offline_capture(self):
        self.pair()
        self.sample(100)
        def lost(*args, **kwargs):
            self.exchange(*args, **kwargs)
            raise ConnectionResetError('ack lost')
        self.transport.side_effect = lost
        self.assertEqual(self.client.step(force=True)['status'], 'unreachable')
        original = self.machine.batch()['records'][0]
        self.restart()
        self.sample(120)
        self.transport.side_effect = self.exchange
        self.client.step(force=True)
        self.assertEqual(self.calls[-1][1]['records'][0], original)
        self.assertEqual(self.machine.status()['pending'], 0)
        detail = self.store.get_device(self.machine.device_id)
        self.assertEqual(detail['totals']['measured_seconds'], 120)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM records').fetchone()[0], 2)

    def test_offline_capture_backoff_survives_restart_and_is_bounded(self):
        self.pair()
        self.transport.side_effect = OSError('offline')
        with patch('lifetime_client.time.time', return_value=1000):
            self.client.step(force=True)
            count = self.transport.call_count
            self.client.step()
            self.assertEqual(self.transport.call_count, count)
            self.restart()
            self.client.step()
            self.assertEqual(self.transport.call_count, count)
            for _ in range(15):
                self.client.step(force=True)
            self.assertLessEqual(self.machine._load()['next_attempt']-1000, 300)
        self.sample(100)
        self.sample(130)
        self.assertEqual(self.machine.ledger()['total_seconds'], 130)
        self.assertEqual(self.machine.status()['pending'], 2)

    def test_ack_delete_is_atomic_and_retries_as_duplicate_after_disk_failure(self):
        self.pair()
        self.sample()
        self.machine._db.execute("CREATE TRIGGER full BEFORE DELETE ON outbox BEGIN SELECT RAISE(ABORT,'disk full'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.client.step(force=True)
        self.assertEqual(self.machine.status()['pending'], 1)
        self.machine._db.execute('DROP TRIGGER full')
        self.restart()
        self.client.step(force=True)
        self.assertEqual(self.machine.status()['pending'], 0)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM records').fetchone()[0], 1)

    def test_conflicting_record_is_retained_and_reported(self):
        self.pair()
        self.sample()
        message = self.machine.batch()
        altered = copy.deepcopy(message)
        altered['records'][0]['totals']['baseline_seconds'] = 9
        self.store.ingest(message['device_id'], self.machine._load()['credential'], altered)
        self.assertEqual(self.client.step(force=True)['status'], 'conflict')
        self.assertEqual(self.machine.batch()['records'], message['records'])

    def test_retired_sequences_are_acknowledged_and_removed(self):
        self.pair()
        self.store.retain('2027-01-01T00:00:00+00:00')
        self.sample()
        self.assertEqual(self.client.step(force=True)['status'], 'connected')
        self.assertEqual(self.machine.status()['pending'], 0)
        self.assertEqual(self.store._db.execute('SELECT count(*) FROM records').fetchone()[0], 0)

    def test_unknown_commands_bad_intervals_and_forged_acks_never_delete(self):
        self.pair()
        self.sample()
        for change in (dict(command='restart'), dict(reporting_interval=0), dict(refresh='yes'),
                       dict(accepted=[999]), dict(accepted=[]), dict(duplicates=[1]),
                       dict(device_id='another-device'), dict(policy_version=0)):
            def poison(*args, change=change, **kwargs):
                reply = self.exchange(*args, **kwargs)
                # Normalize to the expected ack before making one corruption.
                reply.update(accepted=[1], duplicates=[])
                return reply | change
            self.transport.side_effect = poison
            self.client.step(force=True)
            self.assertEqual(self.machine.status()['pending'], 1, change)
        self.transport.side_effect = self.exchange
        self.client.step(force=True)
        self.assertEqual(self.machine.status()['pending'], 0)

    def test_permission_revocation_before_sending_scrubs_server_and_old_queue(self):
        self.pair(dict(history=True, resources=True))
        self.sample(100, boot_evidence=(0, [], True), resources_sampler=lambda: {'system': 'optional'})
        self.client.step(force=True)
        self.assertIsNotNone(self.store.get_device(self.machine.device_id)['history'])
        self.sample(110, resources_sampler=lambda: {'system': 'optional'})
        self.machine.set_policy(dict(history=False, resources=False))
        self.client.step(force=True)
        detail = self.store.get_device(self.machine.device_id)
        self.assertIsNone(detail['history'])
        self.assertIsNone(detail['readings'])
        self.assertNotIn('history', self.calls[-1][1])
        self.assertNotIn('readings', self.calls[-1][1])

    def test_batches_limit_100_and_wire_bytes_with_stock_after_drops(self):
        self.pair()
        for i in range(105):
            self.sample(100+i)
        batch = self.machine.batch()
        self.assertEqual(len(batch['records']), 100)
        self.assertLessEqual(len(json.dumps(batch, ensure_ascii=True, separators=(',', ':')).encode()), protocol.MAX_BYTES)
        self.client.step(force=True)
        self.assertEqual(self.machine.status()['pending'], 5)
        self.client.step(force=True)
        self.assertEqual(self.machine.status()['pending'], 0)
        self.assertEqual(self.store.get_device(self.machine.device_id)['totals']['measured_seconds'], 204)

    def test_disabled_or_revoked_reporting_never_sends_again(self):
        self.pair()
        self.sample()
        self.store.revoke(self.machine.device_id)
        self.assertEqual(self.client.step(force=True)['status'], 'revoked')
        count = self.transport.call_count
        self.sample(110)
        self.client.step(force=True)
        self.assertEqual(self.transport.call_count, count)
        self.assertIsNone(self.machine._load()['credential'])
        self.assertEqual(self.machine.status()['pending'], 2)
        self.machine.disable_reporting()
        self.client.step(force=True)
        self.assertEqual(self.transport.call_count, count)


class RuntimeIntegrationTests(AgentFixture):
    def setUp(self):
        super().setUp()
        import lifetime_server as server
        from lifetime_admin import AdminClient
        self.server_root = Path(self.temporary.name) / 'server'
        if sys.platform == 'win32':
            self.enterContext(patch.object(server, '_protect_directory'))
            self.enterContext(patch.object(tls, 'protect_private'))
        self.runtime = server.ServerRuntime(self.server_root, address='https://127.0.0.1:8443',
                                            host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.admin = AdminClient(self.server_root, urlsplit(self.runtime.admin_address).port, timeout=3)
        pem = (self.server_root / 'server-cert.pem').read_text(encoding='ascii')
        code = self.admin.call('code')['code']
        self.bundle = tls.encode_bundle(self.runtime.public_address, code, pem)
        self.client = SyncClient(self.machine, timeout=3)

    def pair(self):
        self.assertEqual(self.client.start_enrollment(self.bundle, {'display_name': 'M\u00e1y th\u1eadt'},
                         dict(history=False, resources=False))['status'], 'pending')
        request_id = self.machine._load()['enrollment']['request_id']
        self.admin.call('approve', request_id=request_id)
        self.assertEqual(self.client.step(force=True)['status'], 'approved')

    def test_actual_tls_enrollment_claim_sync_restart_and_redacted_status(self):
        self.pair()
        self.sample(100)
        self.sample(120)
        self.reopen()
        self.client = SyncClient(self.machine, timeout=3)
        self.assertEqual(self.client.step(force=True)['status'], 'connected')
        self.assertEqual(self.machine.status()['pending'], 0)
        device = self.admin.call('device', device_id=self.machine.device_id)
        self.assertEqual(device['totals']['measured_seconds'], 120)
        status = json.loads(agent.status_path(self.root).read_text())
        self.assertEqual(status['server'], self.runtime.public_address)
        self.assertNotIn(self.machine._load()['credential'], json.dumps(status))

    def test_actual_server_revocation_retains_offline_records(self):
        self.pair()
        self.admin.call('revoke', device_id=self.machine.device_id)
        self.sample()
        self.assertEqual(self.client.step(force=True)['status'], 'revoked')
        self.assertFalse(self.machine.status()['enabled'])
        self.assertEqual(self.machine.status()['pending'], 1)
        self.sample(120)
        self.assertEqual(self.machine.ledger()['total_seconds'], 120)


if __name__ == '__main__':
    unittest.main()
