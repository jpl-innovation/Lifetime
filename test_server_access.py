"""Named identities, roles, the audit trail, remote power hardening and archive backups (Stage 3).

A real runtime on loopback with temporary data; Windows ACL calls are mocked as in test_server_runtime. Identities
other than the test's own Windows account are passed to the runtime directly (the same entry point the pipe uses), so
the role matrix runs everywhere; tests that need the real pipe are Windows-only and skip elsewhere.
"""
import concurrent.futures
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
import uuid
import zipfile
from unittest.mock import patch

import test_compat  # noqa: F401
import lifetime_access as access
import lifetime_server as server
import lifetime_server_backup as archive
import lifetime_server_store as storage
import lifetime_tls as tls
from lifetime_protocol import canonical, decode
import test_server_runtime
from lifetime_admin import AdminClient

DOMAIN = 'S-1-5-21-1000-2000-3000-'
WINDOWS = sys.platform == 'win32'


def person(role, number):
    return dict(sid=DOMAIN + str(number), name=f'LAB\\{role}', groups=[DOMAIN + '513'], channel='pipe')


class AccessTests(unittest.TestCase):
    _base = test_server_runtime.RuntimeTests      # its helpers; its own tests run in their module
    setUp, public, enrollment, claim, message = _base.setUp, _base.public, _base.enrollment, _base.claim, _base.message
    wire, admin_raw, pair, status = _base.wire, _base.admin_raw, _base.pair, _base.status

    def restart(self):
        self.runtime.stop()
        self.runtime.start()
        self.admin = AdminClient(self.root, int(self.runtime.admin_address.rsplit(':', 1)[1]), timeout=3)
        self.pem = (self.root / 'server-cert.pem').read_text(encoding='ascii')
        self.bundle = tls.decode_bundle(tls.encode_bundle(self.runtime.public_address, 'unused', self.pem))

    def call(self, identity, operation, **arguments):
        """The runtime's pipe entry point with a given Windows identity: (status, result)."""
        reply = decode(self.runtime._pipe_request(identity, canonical(dict(operation=operation, arguments=arguments))))
        return reply['status'], reply.get('result')

    def people(self):
        found = {}
        for number, role in enumerate(access.ROLES, 1101):
            found[role] = person(role, number)
            self.runtime.access.set_role(found[role]['sid'], found[role]['name'], 'user', role, 'test')
        return found

    def online(self, control=True):
        """An approved, recently reporting computer whose owner allowed (or not) remote power."""
        request = self.enrollment()
        request['policy'] = dict(history=False, resources=False, control=control)
        self.public('enroll', request)
        self.admin.call('approve', request_id=request['request_id'])
        credential = self.claim(request)['credential']
        self.public('sync', self.message(request), credential)
        return request['device_id']

    # ---- roles (items 1, 2, 6) ----

    def test_every_operation_has_exactly_one_role_class_and_the_client_allows_it(self):
        from lifetime_admin import OPERATIONS
        classes = (access.READ, access.AUDIT_READ, access.OPERATE, access.ADMINISTER)
        for operation in server._ADMIN:
            self.assertEqual(sum(operation in c for c in classes), 1, operation)    # unclassified = refused to all
        self.assertEqual(set().union(*classes), set(server._ADMIN))
        self.assertEqual(OPERATIONS, set(server._ADMIN))

    def test_role_by_operation_matrix_default_deny(self):
        people = self.people()
        nobody = person('nobody', 1999)
        for operation in server._ADMIN:
            for role, identity in people.items():
                with self.subTest(operation=operation, role=role):
                    status, _ = self.call(identity, operation)
                    if operation in access.PERMISSIONS[role]:
                        self.assertNotEqual(status, 403)
                    else:
                        self.assertEqual(status, 403)
            self.assertEqual(self.call(nobody, operation)[0], 403, operation)
        viewer = people['viewer']
        for operation in server._ADMIN:
            if access.state_changing(operation):
                self.assertNotIn(operation, access.PERMISSIONS['viewer'])
                self.assertNotIn(operation, access.PERMISSIONS['auditor'])
        denied = [row for row in self.runtime.access.audit(limit=1000) if row['actor_sid'] == viewer['sid']]
        self.assertTrue(denied and all(row['result'] == 'denied' for row in denied))

    def test_builtin_admins_token_and_unelevated_administrators(self):
        self.assertEqual(self.runtime.access.roles_for(dict(sid='S-1-5-18', groups=[])), {'admin'})
        elevated = dict(sid=DOMAIN + '500', groups=[access.ADMINISTRATORS])
        self.assertEqual(self.runtime.access.roles_for(elevated), {'admin'})
        unelevated = dict(sid=DOMAIN + '500', groups=[])         # Administrators present but deny-only: not passed on
        self.assertEqual(self.runtime.access.roles_for(unelevated), set())
        self.assertEqual(self.runtime.access.roles_for(access.TOKEN_ACTOR), {'admin'})
        with self.assertRaises(access.AccessError):
            self.runtime.access.set_role(access.ADMINISTRATORS, 'BUILTIN\\Administrators', 'group', 'viewer', 'x')
        group = DOMAIN + '7777'
        self.runtime.access.set_role(group, 'LAB\\Helpdesk', 'group', 'operator', 'test')
        member = dict(sid=DOMAIN + '42', name='LAB\\amy', groups=[group])
        self.assertEqual(self.call(member, 'whoami')[1]['role'], 'operator')
        self.runtime.access.set_role(DOMAIN + '42', 'LAB\\amy', 'user', 'auditor', 'test')
        self.assertEqual(set(self.call(member, 'whoami')[1]['roles']), {'operator', 'auditor'})

    def test_claimed_identity_is_never_believed(self):
        viewer = self.people()['viewer']
        # Extra top-level fields (a claimed identity) are refused outright, not merged.
        raw = canonical(dict(operation='whoami', arguments={}, identity=dict(sid='S-1-5-18', name='NT AUTHORITY\\SYSTEM')))
        self.assertEqual(decode(self.runtime._pipe_request(viewer, raw))['status'], 400)
        self.assertEqual(self.call(viewer, 'whoami', sid='S-1-5-18')[0], 400)
        self.assertEqual(self.call(viewer, 'whoami')[1]['sid'], viewer['sid'])
        # Acknowledging records the Windows account, not the name the dashboard sent.
        operator = self.people()['operator']
        with patch.object(self.runtime.monitor, 'acknowledge', return_value={'ok': True}) as acknowledge:
            self.assertEqual(self.call(operator, 'acknowledge', incident_id=1, by='Mallory')[0], 200)
        self.assertEqual(acknowledge.call_args.kwargs['by'], operator['name'])

    @unittest.skipUnless(WINDOWS, 'Windows named pipe')
    def test_real_pipe_reports_the_callers_windows_account(self):
        import lifetime_pipe
        me = lifetime_pipe.current_identity()
        name = lifetime_pipe.pipe_name(self.admin.port)
        reply = decode(lifetime_pipe.call(name, canonical(dict(operation='whoami', arguments={}))))
        self.assertEqual((reply['status'], reply['result']['sid'], reply['result']['channel']), (200, me['sid'], 'pipe'))
        reply = decode(lifetime_pipe.call(name, canonical(dict(operation='whoami', arguments={}, sid='S-1-5-18'))))
        self.assertEqual(reply['status'], 400)
        self.assertEqual(self.admin.call('whoami')['sid'], me['sid'])          # the dashboard client uses the pipe
        # A pipe that is not owned by SYSTEM, Administrators or this account is refused before anything is sent.
        with patch.object(lifetime_pipe, '_owner', return_value=DOMAIN + '666'), self.assertRaises(PermissionError):
            lifetime_pipe.call(name, b'{}')
        # Nobody can take over the name while the server holds it (first-instance flag).
        with self.assertRaises(OSError):
            lifetime_pipe.PipeServer(name, lambda *a: b'', lambda: 'D:P(A;;FA;;;SY)').start()

    @unittest.skipUnless(WINDOWS, 'Windows named pipe')
    def test_pipe_security_descriptor_and_refused_accounts(self):
        import lifetime_pipe
        self.runtime.access.set_role(DOMAIN + '1234', 'LAB\\v', 'user', 'viewer', 'test')
        sddl = self.runtime.access.pipe_descriptor(DOMAIN + '1')
        self.assertTrue(sddl.startswith('D:P(D;;FA;;;NU)(A;;FA;;;SY)(A;;FA;;;BA)'))
        self.assertIn(f'(A;;0x12019b;;;{DOMAIN}1234)', sddl)              # read/write, never create-instance
        self.assertNotIn(f'(A;;FA;;;{DOMAIN}1234)', sddl)
        name = lifetime_pipe.PREFIX + 'test-' + uuid.uuid4().hex
        refused = lifetime_pipe.PipeServer(name, lambda *a: b'{}', lambda: 'D:P(A;;FA;;;SY)').start()
        try:
            with self.assertRaises(PermissionError):
                lifetime_pipe.call(name, b'{}')
        finally:
            refused.stop()

    @unittest.skipUnless(WINDOWS, 'Windows named pipe')
    def test_pipe_large_reply_after_a_slow_operation_and_concurrent_clients(self):
        import threading
        import time
        import lifetime_pipe
        name = lifetime_pipe.PREFIX + 'test-' + uuid.uuid4().hex
        own = lifetime_pipe.current_identity()['sid']
        def slow(identity, raw):
            time.sleep(1.2 if raw == b'slow' else 0)
            return b'x' * 300_000
        pipe = lifetime_pipe.PipeServer(name, slow, lambda: f'D:P(A;;FA;;;SY)(A;;FA;;;{own})', timeout=1).start()
        self.addCleanup(pipe.stop)
        self.assertEqual(len(lifetime_pipe.call(name, b'slow', timeout=5)), 300_000)
        sizes = []
        threads = [threading.Thread(target=lambda: sizes.append(len(lifetime_pipe.call(name, b'fast', timeout=5))))
                   for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sizes, [300_000] * 24)       # never "no such pipe" while instances are busy

    def test_start_without_pipe_keeps_token_channel_and_logs_it(self):
        if not WINDOWS:
            self.skipTest('Windows named pipe')
        import lifetime_pipe
        with patch.object(lifetime_pipe.PipeServer, 'start', side_effect=OSError(5, 'taken')):
            self.restart()
        self.assertIn('admin-pipe-unavailable', [row['kind'] for row in self.runtime._store.server_log()])
        self.assertEqual(self.admin.call('whoami')['channel'], 'token')

    def test_roles_page_operations_and_revocation_takes_effect_on_next_request(self):
        operator = person('operator', 1500)
        self.assertEqual(self.call(operator, 'whoami')[0], 403)
        if WINDOWS:
            roles = self.admin.call('set_role', principal=operator['sid'], role='operator')
            self.assertEqual([(r['sid'], r['role']) for r in roles], [(operator['sid'], 'operator')])
            self.assertEqual(self.admin_raw('/v1/set_role', {'principal': DOMAIN + '1', 'role': 'owner'})[0], 400)
        else:
            self.runtime.access.set_role(operator['sid'], 'LAB\\op', 'user', 'operator', 'test')
        self.assertEqual(self.call(operator, 'set_role', principal=operator['sid'], role='admin')[0], 403)
        self.assertEqual(self.call(operator, 'whoami')[1]['role'], 'operator')
        self.admin.call('delete_role', sid=operator['sid'])
        self.assertEqual(self.call(operator, 'whoami')[0], 403)

    # ---- audit (item 3) ----

    def test_audit_rows_for_changes_and_denials_not_reads(self):
        before = len(self.runtime.access.audit(limit=1000))
        self.admin.call('devices')
        self.admin.call('overview')
        self.assertEqual(len(self.runtime.access.audit(limit=1000)), before)
        self.admin.call('set_configuration', reporting_interval=120)
        self.admin_raw('/v1/set_configuration', {'reporting_interval': 1})
        rows = self.runtime.access.audit(limit=2)
        self.assertEqual([(r['action'], r['result']) for r in rows],
                         [('set_configuration', 'failed-400'), ('set_configuration', 'ok')])
        self.assertEqual(rows[0]['actor_name'], 'admin-token')
        self.assertEqual(self.admin.call('verify_audit')['ok'], True)
        # Secrets never reach the audit trail: only identifier fields become the target.
        self.admin.call('set_notifications', password='hunter2-secret', webhook_url='https://hooks.example/abc-secret')
        dump = json.dumps(self.runtime.access.audit(limit=1000))
        self.assertNotIn('hunter2', dump)
        self.assertNotIn('abc-secret', dump)

    def test_verify_detects_edits_deletions_and_truncation(self):
        for number in range(6):
            self.admin.call('set_configuration', reporting_interval=60 + number)
        self.assertTrue(self.admin.call('verify_audit')['ok'])
        path = self.root / 'access.sqlite'
        def tamper(sql, *values):
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute(sql, values)
                db.commit()
        rows = list(reversed(self.runtime.access.audit(limit=100)))
        seqs = [row['seq'] for row in rows]
        tamper("UPDATE audit SET actor_name='someone else' WHERE seq=?", seqs[2])
        report = self.runtime.access.verify()
        self.assertEqual((report['ok'], report['first_broken'], report['reason']), (False, seqs[2], 'edited'))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(['verify-audit', '--directory', str(self.root)]), 1)
        tamper('UPDATE audit SET actor_name=? WHERE seq=?', rows[2]['actor_name'], seqs[2])
        self.assertTrue(self.runtime.access.verify()['ok'])
        tamper('DELETE FROM audit WHERE seq=?', seqs[3])
        self.assertEqual((self.runtime.access.verify()['first_broken'], self.runtime.access.verify()['reason']), (seqs[3], 'missing'))

    def test_truncating_the_newest_rows_is_detected(self):
        for number in range(3):
            self.admin.call('set_configuration', reporting_interval=60 + number)
        last = self.runtime.access.audit(limit=1)[0]['seq']
        with contextlib.closing(sqlite3.connect(self.root / 'access.sqlite')) as db:
            db.execute('DELETE FROM audit WHERE seq=?', (last,))
            db.commit()
        report = self.runtime.access.verify()
        self.assertEqual((report['ok'], report['reason']), (False, 'truncated'))

    def test_retention_archives_the_oldest_segment_and_the_chain_still_verifies(self):
        store = access.Access(self.root / 'small.sqlite', archive=self.root / 'small-archive', max_rows=5, segment=3)
        self.addCleanup(store.close)
        actor = dict(sid=DOMAIN + '9', name='LAB\\x', groups=[])
        for number in range(8):
            store.record(actor, {'admin'}, 'set_configuration', f'n={number}', 'ok', str(number))
        files = sorted((self.root / 'small-archive').glob('audit-*.jsonl'))
        self.assertEqual(len(files), 1)
        archived = [json.loads(line) for line in files[0].read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['seq'] for row in archived], [1, 2, 3])
        self.assertEqual(access.verify_rows(archived)[1], None)          # the archive verifies on its own
        report = store.verify()
        self.assertEqual((report['ok'], report['checked'], report['archived_through']), (True, 5, 3))

    def test_audit_csv_export_is_formula_safe(self):
        rows = [dict(seq=1, at='2026-11-15T00:00:00+00:00', actor_sid=None, actor_name='=cmd|calc', role='admin',
                     action='set_role', target='principal=+x', result='ok', correlation_id='c', prev_hash='0', hash='1')]
        path = self.root / 'audit.csv'
        access.write_csv(rows, path)
        text = path.read_text(encoding='utf-8-sig')
        self.assertIn("'=cmd|calc", text)
        self.assertEqual(text.splitlines()[0].split(',')[:3], ['seq', 'at', 'actor_name'])

    # ---- remote power (item 4) ----

    def test_power_needs_a_named_authorised_actor_and_the_owners_consent(self):
        device, refused = self.online(), self.online(control=False)
        people = self.people()
        # The shared token cannot say who is asking: refresh yes, power no.
        self.assertEqual(self.admin_raw('/v1/send_command', {'device_id': device, 'action': 'restart'})[0], 403)
        self.assertEqual(self.admin_raw('/v1/wake', {'device_id': device})[0], 403)
        self.assertEqual(self.admin_raw('/v1/send_command', {'device_id': device, 'action': 'refresh'})[0], 200)
        self.runtime._store.cancel_power_commands()
        for command in self.runtime._store.get_device(device)['commands']:
            with contextlib.suppress(storage.StoreError):
                self.runtime._store.cancel_command(command['command_id'])
        for role in ('viewer', 'auditor'):
            self.assertEqual(self.call(people[role], 'send_command', device_id=device, action='restart')[0], 403)
        self.assertEqual(self.call(people['operator'], 'send_command', device_id=refused, action='restart')[0], 400)
        status, command = self.call(people['operator'], 'send_command', device_id=device, action='restart')
        self.assertEqual(status, 200)
        uuid.UUID(command['command_id'])
        self.assertEqual(dt.datetime.fromisoformat(command['expires_at']) - dt.datetime.fromisoformat(command['created_at']),
                         dt.timedelta(minutes=10))
        row = self.runtime.access.audit(limit=1)[0]
        self.assertEqual((row['action'], row['actor_sid'], row['correlation_id'], row['target']),
                         ('send_command', people['operator']['sid'], command['command_id'], f'device_id={device}'))
        # No bulk power: one computer per request, nothing else accepted.
        self.assertEqual(self.call(people['admin'], 'send_command', device_id=[device, refused], action='sleep')[0], 400)
        self.assertEqual(self.call(people['admin'], 'send_command', device_ids=[device], action='sleep')[0], 400)

    def test_power_rate_limits_per_computer_and_per_account(self):
        operator = self.people()['operator']
        with patch.object(self.runtime._store, 'send_command', return_value={'command_id': str(uuid.uuid4())}):
            device = str(uuid.uuid4())
            statuses = [self.call(operator, 'send_command', device_id=device, action='sleep')[0] for _ in range(4)]
            self.assertEqual(statuses, [200, 200, 200, 429])
            statuses = [self.call(operator, 'send_command', device_id=str(uuid.uuid4()), action='sleep')[0] for _ in range(8)]
            self.assertEqual(statuses[-1], 429)                           # ten per account in ten minutes
            self.assertEqual(statuses.count(200), server.POWER_PER_ACTOR - 3)
            other = self.people()['admin']
            self.assertEqual(self.call(other, 'send_command', device_id=str(uuid.uuid4()), action='sleep')[0], 200)

    def test_commands_expire_after_ten_minutes(self):
        device = self.online()
        operator = self.people()['operator']
        command = self.call(operator, 'send_command', device_id=device, action='shutdown')[1]
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10, seconds=1)
        with patch.object(storage, '_now', return_value=later):
            self.assertIsNone(self.runtime._store.take_command(device))
            self.assertEqual(self.runtime._store.get_device(device)['commands'][0]['status'], 'expired')
        self.assertEqual(command['status'], 'pending')

    def test_revocation_wins_races(self):
        operator = self.people()['operator']
        # Removing the role cancels what is still waiting, and the next request is refused.
        device = self.online()
        command = self.call(operator, 'send_command', device_id=device, action='restart')[1]
        self.runtime.delete_role(operator['sid'])
        self.assertEqual(self.runtime._store.get_device(device)['commands'][0]['status'], 'cancelled')
        self.assertEqual(self.call(operator, 'send_command', device_id=device, action='restart')[0], 403)
        self.assertIsNone(self.runtime._store.take_command(device))
        self.assertTrue(command['command_id'])
        # Disconnecting a computer while commands are being sent: no command survives for a revoked computer.
        self.runtime.access.set_role(operator['sid'], operator['name'], 'user', 'operator', 'test')
        for _ in range(5):
            device = self.online()
            self.runtime._power_log.clear()
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                sent = pool.submit(self.call, operator, 'send_command', device_id=device, action='sleep')
                pool.submit(self.admin.call, 'revoke', device_id=device).result()
                sent.result()
            commands = self.runtime._store.get_device(device)['commands']
            self.assertFalse([c for c in commands if c['status'] in ('pending', 'delivered')])
            self.assertIsNone(self.runtime._store.take_command(device))

    # ---- backup and restore (item 5) ----

    def test_archive_backup_verify_and_restore_into_a_stopped_server(self):
        request, credential = self.pair()
        self.public('sync', self.message(request), credential)
        self.admin.call('set_schedule', scope='group', key='Lab', kind='always')
        self.runtime.access.set_role(DOMAIN + '77', 'LAB\\keep', 'user', 'viewer', 'test')
        path = self.root / 'backups'
        path.mkdir()
        made = self.admin.call('backup_archive', path=str(path / 'server.zip'))
        self.assertEqual(set(made['files']), set(archive.MEMBERS) - {'manifest.json', 'server-cert.previous.pem'})
        with zipfile.ZipFile(path / 'server.zip') as zipped:
            self.assertNotIn('admin-token', zipped.namelist())
            self.assertNotIn('alert-secrets.bin', zipped.namelist())
        self.assertEqual((path / 'server.zip.sha256').read_text().split()[0], made['sha256'])
        self.assertEqual(self.admin.call('verify_backup', path=str(path / 'server.zip'))['ok'], True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(['verify-backup', '--directory', str(self.root), '--archive', str(path / 'server.zip')]), 0)
        self.assertEqual(self.admin_raw('/v1/backup_archive', {'path': str(self.root / archive.STAGED)})[0], 400)
        # Changes after the backup; the restore must bring back the backed-up state but keep roles and audit.
        self.admin.call('delete_schedule', scope='group', key='Lab')
        self.runtime.access.set_role(DOMAIN + '88', 'LAB\\new', 'user', 'admin', 'test')
        audit_before = self.runtime.access.audit(limit=1)[0]['seq']
        staged = self.admin.call('stage_restore', path=str(path / 'server.zip'))
        self.assertEqual((staged['staged'], staged['restart_required']), (True, True))
        self.restart()
        self.assertFalse((self.root / archive.STAGED).exists())
        self.assertIn(('group', 'lab'), [(row['scope'], row['key']) for row in self.admin.call('schedules')])
        self.status(403, 'sync', self.message(request, 2), credential)      # credentials invalidated
        self.assertEqual(self.admin.call('device', device_id=request['device_id'])['record_count'], 1)
        self.assertIn(DOMAIN + '88', [row['sid'] for row in self.runtime.access.roles()])  # roles kept
        actions = [row['action'] for row in self.runtime.access.audit(limit=1000)]
        self.assertIn('stage_restore', actions)
        self.assertEqual(actions[0], 'restore-applied')
        self.assertGreater(self.runtime.access.audit(limit=1)[0]['seq'], audit_before)
        self.assertTrue(self.runtime.access.verify()['ok'])
        self.assertIn('restore-applied', [row['kind'] for row in self.runtime._store.server_log()])

    def test_damaged_or_foreign_archives_are_refused_and_never_applied(self):
        path = self.root / 'b.zip'
        self.admin.call('backup_archive', path=str(path))
        good = path.read_bytes()
        path.write_bytes(good[:-10] + b'0123456789')
        self.assertEqual(self.admin.call('verify_backup', path=str(path)), dict(ok=False, problem='checksum'))
        path.write_bytes(good)
        (self.root / 'b.zip.sha256').unlink()
        self.assertEqual(self.admin.call('verify_backup', path=str(path))['problem'], 'checksum-missing')
        # A consistent checksum over altered content: the manifest hashes catch it.
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(self.root / 'c.zip', 'w') as target:
            for name in source.namelist():
                data = source.read(name)
                target.writestr(name, data + b'x' if name == 'configuration.json' else data)
        (self.root / 'c.zip.sha256').write_text(archive._digest(self.root / 'c.zip') + '  c.zip\n')
        self.assertEqual(self.admin.call('verify_backup', path=str(self.root / 'c.zip'))['problem'], 'damaged')
        self.assertEqual(self.admin.call('stage_restore', path=str(self.root / 'c.zip'))['ok'], False)
        self.assertFalse((self.root / archive.STAGED).exists())
        # A staged archive damaged before the next start is set aside; the server starts with its current data.
        (self.root / 'b.zip.sha256').write_text(archive._digest(path) + '  b.zip\n')
        self.admin.call('stage_restore', path=str(path))
        staged = self.root / archive.STAGED
        staged.write_bytes(staged.read_bytes()[:-5] + b'xxxxx')
        self.restart()
        self.assertTrue((self.root / archive.FAILED).exists())
        self.assertIn('restore-failed', [row['kind'] for row in self.runtime._store.server_log()])


if __name__ == '__main__':
    unittest.main()
