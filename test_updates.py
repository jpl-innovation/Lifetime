"""Staged agent updates: the server's package, rollout, pause and rollback, and a computer that downloads, checks,
keeps and reports over a real pinned HTTPS connection on 127.0.0.1. Nothing is ever run."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import lifetime_permissions as permissions
import lifetime_updates as updates


def package(directory, name='LifetimeAgent.exe', size=1300 * 1024):
    path = Path(directory) / name
    path.write_bytes(os.urandom(size))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class ServerSideTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(permissions, 'windows_acl'))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'server').mkdir()
        self.updates = updates.Updates(self.root / 'server')

    def test_upload_needs_the_matching_sha256_a_version_and_a_plain_name(self):
        path, digest = package(self.root)
        for arguments, reason in ((dict(version='1.3.0', sha256='0' * 64), 'does not match'),
                                  (dict(version='1.3 beta', sha256=digest), 'version'),
                                  (dict(version='1.3.0', sha256='abc'), 'SHA-256')):
            with self.subTest(reason=reason), self.assertRaisesRegex(updates.UpdateError, reason):
                self.updates.upload(path, **arguments)
        odd, odd_digest = package(self.root, 'agent build (1).exe')
        with self.assertRaisesRegex(updates.UpdateError, 'names'):
            self.updates.upload(odd, '1.3.0', odd_digest)
        with self.assertRaisesRegex(updates.UpdateError, 'regular file'):
            self.updates.upload(self.root / 'missing.exe', '1.3.0', digest)
        with patch.object(updates, 'MAX_PACKAGE', 1024), self.assertRaisesRegex(updates.UpdateError, '64 MiB'):
            self.updates.upload(path, '1.3.0', digest)
        self.assertIsNone(self.updates.summary()['current'])
        self.assertEqual(list((self.root / 'server' / 'updates').glob('*.pkg')), [])     # nothing half-kept

    def test_rollout_share_pause_chunks_and_reports(self):
        path, digest = package(self.root)
        summary = self.updates.upload(path, '1.3.0', digest.upper(), by='SCHOOL\\admin')
        self.assertEqual((summary['current']['sha256'], summary['percent']), (digest, 0))   # offered to nobody yet
        devices = [str(uuid.uuid4()) for _ in range(1000)]
        self.assertFalse(any(self.updates.handle(d, dict(op='offer', device_id=d, version='1.2.0'))['go'] for d in devices))
        self.updates.set_rollout(percent=10)
        chosen = [d for d in devices if self.updates.handle(d, dict(op='offer', device_id=d, version='1.2.0'))['go']]
        self.assertTrue(60 < len(chosen) < 140)                                    # about one in ten, stable
        self.assertEqual(chosen, [d for d in devices if updates.cohort(d, digest) < 10])
        outside = next(d for d in devices if d not in chosen)
        with self.assertRaisesRegex(updates.UpdateError, 'Not offered'):
            self.updates.handle(outside, dict(op='chunk', device_id=outside, sha256=digest, offset=0))
        first = self.updates.handle(chosen[0], dict(op='chunk', device_id=chosen[0], sha256=digest, offset=0))
        self.assertEqual(len(updates.base64.b64decode(first['data'])), updates.CHUNK)
        for offset in (1, -updates.CHUNK, 3 * updates.CHUNK):
            with self.assertRaises(updates.UpdateError):
                self.updates.handle(chosen[0], dict(op='chunk', device_id=chosen[0], sha256=digest, offset=offset))
        self.updates.set_rollout(paused=True)
        self.assertFalse(self.updates.handle(chosen[0], dict(op='offer', device_id=chosen[0], version='1.2.0'))['go'])
        with self.assertRaises(updates.UpdateError):
            self.updates.handle(chosen[0], dict(op='chunk', device_id=chosen[0], sha256=digest, offset=0))
        for status, extra in (('staged', {}), ('failed', dict(category='checksum'))):
            self.updates.handle(chosen[0], dict(op='report', device_id=chosen[0], sha256=digest, status=status, version='1.2.0', **extra))
        for bad in (dict(status='failed'), dict(status='staged', category='checksum'), dict(status='done'),
                    dict(status='failed', category='anything')):
            with self.subTest(bad=bad), self.assertRaises(updates.UpdateError):
                self.updates.handle(chosen[1], dict(op='report', device_id=chosen[1], sha256=digest, version='1.2.0', **bad))
        summary = self.updates.summary()
        self.assertEqual(summary['counts'], dict(staged=0, installed=0, failed=1))    # the latest report per computer
        self.assertEqual(summary['reports'][0]['category'], 'checksum')
        reopened = updates.Updates(self.root / 'server').summary()                     # survives a restart
        self.assertEqual((reopened['percent'], reopened['paused'], reopened['counts']), (10, True, summary['counts']))
        for bad in (dict(percent=101), dict(percent=5.5), dict(paused='yes')):
            with self.assertRaises(updates.UpdateError):
                self.updates.set_rollout(**bad)

    def test_rollback_offers_the_previous_package_or_withdraws(self):
        first, first_digest = package(self.root, 'LifetimeAgent-1.3.exe')
        second, second_digest = package(self.root, 'LifetimeAgent-1.4.exe')
        self.updates.upload(first, '1.3.0', first_digest)
        self.updates.upload(second, '1.4.0', second_digest)
        self.assertEqual(self.updates.summary()['previous']['sha256'], first_digest)
        summary = self.updates.rollback()
        self.assertEqual((summary['current']['version'], summary['percent'], summary['rollback']), ('1.3.0', 100, True))
        device = str(uuid.uuid4())
        self.assertTrue(self.updates.handle(device, dict(op='offer', device_id=device, version='1.4.0'))['target']['rollback'])
        self.updates.rollback()                     # back to 1.4.0, again as a rollback
        third, third_digest = package(self.root, 'LifetimeAgent-1.5.exe')
        self.updates.upload(third, '1.5.0', third_digest)
        kept = sorted(path.name for path in (self.root / 'server' / 'updates').glob('*.pkg'))
        self.assertEqual(kept, sorted([f'{third_digest}.pkg', f'{second_digest}.pkg']))  # only current and previous
        (self.root / 'other').mkdir()
        fresh = updates.Updates(self.root / 'other')
        fresh.upload(first, '1.3.0', first_digest)
        self.assertIsNone(fresh.rollback()['current'])                                  # nothing older: withdrawn
        self.assertEqual(fresh.handle(device, dict(op='offer', device_id=device, version='1.2.0')), dict(target=None, go=False))

    def test_a_damaged_state_file_offers_nothing(self):
        path, digest = package(self.root)
        self.updates.upload(path, '1.3.0', digest)
        (self.root / 'server' / 'updates' / 'updates.json').write_text('{"current": 1}', encoding='utf-8')
        self.assertIsNone(updates.Updates(self.root / 'server').summary()['current'])

    @unittest.skipUnless(sys.platform == 'win32', 'Authenticode is checked on Windows')
    def test_authenticode_valid_none_and_tampered(self):
        self.assertEqual(updates.authenticode(self.root / Path(package(self.root)[0]).name), 'none')
        if updates.authenticode(sys.executable) != 'valid':
            self.skipTest('this Python is not Authenticode-signed')
        copy = self.root / 'signed.exe'
        shutil.copyfile(sys.executable, copy)
        data = bytearray(copy.read_bytes())
        data[1000] ^= 0xFF
        copy.write_bytes(data)
        self.assertEqual(updates.authenticode(copy), 'invalid')


class EndToEndTests(unittest.TestCase):
    """A real server on 127.0.0.1 and a real machine agent; the package is random bytes and is never run."""

    def setUp(self):
        import lifetime_agent as la
        import lifetime_tls as tls
        from lifetime_admin import AdminClient
        from lifetime_client import SyncClient
        from lifetime_server import ServerRuntime
        self.enterContext(patch.object(permissions, 'windows_acl'))
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = ServerRuntime(self.root / 's', address='https://127.0.0.1:8443', host='127.0.0.1', port=0)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.admin = AdminClient(self.root / 's', int(self.runtime.admin_address.rsplit(':', 1)[1]))
        self.agent = la.MachineAgent.initialize(self.root / 'a')
        self.addCleanup(self.agent.close)
        certificate = (self.root / 's' / 'server-cert.pem').read_text(encoding='ascii')
        bundle = tls.encode_bundle(self.runtime.public_address, self.admin.call('code')['code'], certificate)
        client = SyncClient(self.agent)
        client.start_enrollment(bundle, {'display_name': 'Update test'}, dict(history=False, resources=False))
        self.admin.call('approve', request_id=self.admin.call('pending_enrollments')[0]['request_id'])
        client.step(force=True)         # claim
        client.step(force=True)         # first report: learns the server's capabilities
        self.assertIn('update', self.agent._load()['capabilities'])
        self.path, self.digest = package(self.root, size=2 * updates.CHUNK + 777)
        self.calls = []

        def counted(*arguments, **options):
            self.calls.append(arguments[2]['op'])
            return tls.request_json(*arguments, **options)
        self.updater = updates.Updater(self.agent, request=counted, version='1.2.0')

    def staged_bytes(self):
        staged = self.updater.staged()
        return staged and (self.updater.root / ('package-' + staged['name'])).read_bytes()

    def test_consent_off_by_default_then_download_check_keep_and_report(self):
        self.admin.call('upload_update', path=str(self.path), version='1.3.0', sha256=self.digest)
        self.admin.call('set_update', percent=100)
        self.assertEqual(self.updater.tick(), 'off')
        self.assertEqual(self.calls, [])                        # without the owner's consent nothing is even asked
        self.agent.set_update_consent(True)
        self.assertTrue(self.agent.status()['updates'])
        self.assertEqual(self.updater.tick(), 'staged')
        self.assertEqual(self.staged_bytes(), self.path.read_bytes())
        self.assertEqual(self.updater.staged()['signature'], 'none')
        self.assertEqual(self.calls, ['offer', 'chunk', 'chunk', 'chunk', 'report'])
        self.assertEqual(self.admin.call('updates')['counts']['staged'], 1)
        self.assertEqual(self.updater.tick(), 'staged')            # kept, not downloaded again
        installed = updates.Updater(self.agent, version='1.3.0')   # the installer ran: this is the new agent
        self.assertEqual(installed.tick(), 'current')
        self.assertIsNone(installed.staged())
        self.assertEqual(self.admin.call('updates')['counts'], dict(staged=0, installed=1, failed=0))
        self.agent.set_update_consent(False)
        self.assertEqual(self.updater.tick(), 'off')

    def test_a_changed_package_is_refused_and_nothing_is_kept(self):
        self.agent.set_update_consent(True)
        self.admin.call('upload_update', path=str(self.path), version='1.3.0', sha256=self.digest)
        self.admin.call('set_update', percent=100)
        stored = self.root / 's' / 'updates' / f'{self.digest}.pkg'
        data = bytearray(stored.read_bytes())
        data[-1] ^= 0xFF
        stored.write_bytes(data)                                    # the file on the server changed after approval
        self.assertEqual(self.updater.tick(), 'failed:checksum')
        self.assertIsNone(self.updater.staged())
        self.assertEqual(sorted(p.name for p in self.updater.root.iterdir()), [])
        self.assertEqual(self.admin.call('updates')['reports'][0]['category'], 'checksum')

    def test_pause_rollback_downgrade_and_reserved_paths(self):
        self.agent.set_update_consent(True)
        self.admin.call('upload_update', path=str(self.path), version='1.3.0', sha256=self.digest)
        self.assertEqual(self.updater.tick(), 'waiting')            # 0 % until the administrator chooses a share
        self.admin.call('set_update', percent=100, paused=True)
        self.assertEqual(self.updater.tick(), 'waiting')
        self.admin.call('set_update', paused=False)
        self.assertEqual(self.updater.tick(), 'staged')
        self.admin.call('rollback_update')                          # no older package: the offer is withdrawn
        self.assertEqual(self.updater.tick(), 'none')
        self.assertIsNone(self.updater.staged())                    # and the kept copy is discarded
        older, older_digest = package(self.root, 'LifetimeAgent-old.exe', size=1000)
        self.admin.call('upload_update', path=str(older), version='1.1.0', sha256=older_digest)
        self.admin.call('set_update', percent=100)
        self.assertEqual(self.updater.tick(), 'failed:version')    # never a downgrade that is not a rollback
        key = self.root / 's' / 'server-key.pem'
        with self.assertRaises(ValueError):                         # nothing from the data directory is offered
            self.admin.call('upload_update', path=str(key), version='1.3.0',
                            sha256=hashlib.sha256(key.read_bytes()).hexdigest())

    def test_wrong_credential_is_refused(self):
        import lifetime_tls as tls
        state = self.agent._load()
        with self.assertRaises(tls.HTTPSRequestError) as caught:
            tls.request_json(dict(state['bundle']), '/v1/update', dict(op='offer', device_id=state['device_id'],
                             version='1.2.0'), credential='x' * 43)
        self.assertEqual(caught.exception.status, 403)


if __name__ == '__main__':
    unittest.main()
