"""Lifetime Server on Linux: Unix accounts as principals, the SO_PEERCRED admin socket, role mapping, the admin command
line and the secrets file. Platform-neutral parts run everywhere with patching; the real socket runs only on Linux."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import test_compat  # noqa: F401
import lifetime_access as access
import lifetime_server as server
import lifetime_unix_admin as unix
import test_server_runtime
from lifetime_protocol import canonical, decode

LINUX = sys.platform.startswith('linux')


def fake_accounts():
    """pwd and grp stand-ins: root(0), alice(1000, groups staff 50 and helpdesk 2000), bob(1001, lifetime-console 980),
    lifetime-admin(990)."""
    users = {'root': (0, 0), 'alice': (1000, 1000), 'bob': (1001, 1001), 'lifetime': (999, 999)}
    groups = {'root': 0, 'alice': 1000, 'bob': 1001, 'staff': 50, 'helpdesk': 2000, 'lifetime-admin': 990,
              'lifetime-console': 980, 'lifetime': 999}

    def user(name, uid, gid):
        return types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid)

    def group(name, gid):
        return types.SimpleNamespace(gr_name=name, gr_gid=gid)

    def getpwnam(name):
        if name not in users:
            raise KeyError(name)
        return user(name, *users[name])

    def getpwuid(uid):
        for name, (number, gid) in users.items():
            if number == uid:
                return user(name, number, gid)
        raise KeyError(uid)

    def getgrnam(name):
        if name not in groups:
            raise KeyError(name)
        return group(name, groups[name])

    def getgrgid(gid):
        for name, number in groups.items():
            if number == gid:
                return group(name, number)
        raise KeyError(gid)

    pwd = types.SimpleNamespace(getpwnam=getpwnam, getpwuid=getpwuid)
    grp = types.SimpleNamespace(getgrnam=getgrnam, getgrgid=getgrgid)
    memberships = {'alice': [1000, 50, 2000], 'bob': [1001, 980], 'root': [0], 'lifetime': [999, 990, 980]}
    return pwd, grp, (lambda name, gid: memberships.get(name, [gid]))


class AccountsTests(unittest.TestCase):
    def setUp(self):
        pwd, grp, grouplist = fake_accounts()
        self.enterContext(patch.dict(sys.modules, {'pwd': pwd, 'grp': grp}))
        self.enterContext(patch.object(unix.os, 'getgrouplist', grouplist, create=True))

    def test_peer_identity_has_the_shape_of_a_windows_token(self):
        self.assertEqual(unix.identity_for(1000, 1000),
                         dict(sid='unix:uid:1000', name='alice',
                              groups=['unix:gid:1000', 'unix:gid:2000', 'unix:gid:50']))
        self.assertEqual(unix.identity_for(4242, 4242), dict(sid='unix:uid:4242', name='uid 4242',
                                                             groups=['unix:gid:4242']))

    def test_lookup_users_groups_and_principals(self):
        self.assertEqual(unix.lookup_account('alice'), ('unix:uid:1000', 'alice', 'user'))
        self.assertEqual(unix.lookup_account('helpdesk'), ('unix:gid:2000', 'helpdesk', 'group'))   # no such user
        self.assertEqual(unix.lookup_account('group:alice'), ('unix:gid:1000', 'alice', 'group'))
        self.assertEqual(unix.lookup_account('unix:uid:0'), ('unix:uid:0', 'root', 'user'))
        self.assertEqual(unix.lookup_account('unix:gid:50'), ('unix:gid:50', 'staff', 'group'))
        for bad in ('nobody-here', 'unix:uid:31337', '', 'a\nb', 'x' * 300):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                unix.lookup_account(bad)

    def test_root_and_the_admin_group_are_builtin_admins_others_default_deny(self):
        self.assertTrue(access.builtin_admin(dict(sid='unix:uid:0', groups=['unix:gid:0'])))
        self.assertTrue(access.builtin_admin(dict(sid='unix:uid:999', groups=['unix:gid:999', 'unix:gid:990'])))
        alice = unix.identity_for(1000, 1000)
        self.assertFalse(access.builtin_admin(alice))
        with patch.dict(os.environ, {'LIFETIME_ADMIN_GROUP': 'helpdesk'}):
            self.assertTrue(access.builtin_admin(alice))
        with patch.object(unix, 'group_id', return_value=None):        # no lifetime-admin group: only root
            self.assertFalse(access.builtin_admin(dict(sid='unix:uid:999', groups=['unix:gid:990'])))
        # A Windows identity is judged exactly as before.
        self.assertTrue(access.builtin_admin(dict(sid='S-1-5-18', groups=[])))
        self.assertFalse(access.builtin_admin(dict(sid='S-1-5-21-1-2-3-500', groups=['unix:gid:990'])))

    def test_the_socket_belongs_to_the_console_group_which_grants_no_role(self):
        os.environ.pop('LIFETIME_CONSOLE_GROUP', None)
        self.assertEqual(unix.socket_group(), 'lifetime-console')
        with patch.dict(os.environ, {'LIFETIME_CONSOLE_GROUP': 'helpdesk'}):
            self.assertEqual(unix.socket_group(), 'helpdesk')
        with patch.dict(os.environ, {'LIFETIME_CONSOLE_GROUP': 'no-such-group'}):
            self.assertEqual(unix.socket_group(), 'lifetime-admin')      # older installation: admin group only
        bob = unix.identity_for(1001, 1001)
        self.assertIn('unix:gid:980', bob['groups'])
        self.assertFalse(access.builtin_admin(bob))                     # connecting is not a role


class RoleMappingTests(unittest.TestCase):
    _base = test_server_runtime.RuntimeTests
    setUp = _base.setUp

    def call(self, identity, operation, channel='socket', **arguments):
        raw = self.runtime._pipe_request(identity, canonical(dict(operation=operation, arguments=arguments)), channel)
        reply = decode(raw)
        return reply['status'], reply.get('result')

    def test_unix_principals_get_roles_and_the_same_audit_chain(self):
        pwd, grp, grouplist = fake_accounts()
        alice = dict(sid='unix:uid:1000', name='alice', groups=['unix:gid:1000', 'unix:gid:2000'])
        self.assertEqual(self.call(alice, 'whoami')[0], 403)                   # default deny
        bob = dict(sid='unix:uid:1001', name='bob', groups=['unix:gid:1001', 'unix:gid:980'])   # lifetime-console only
        with patch.dict(sys.modules, {'pwd': pwd, 'grp': grp}):
            self.assertEqual(self.runtime.access.roles_for(bob), set())
            self.assertEqual(self.call(bob, 'whoami')[0], 403)
        with patch.object(server.sys, 'platform', 'linux'), patch.dict(sys.modules, {'pwd': pwd, 'grp': grp}):
            self.runtime.set_role(dict(name='root'), 'group:helpdesk', 'operator')
            with self.assertRaises(access.AccessError):
                self.runtime.set_role(dict(name='root'), 'unix:uid:0', 'viewer')   # root is always Admin
        status, me = self.call(alice, 'whoami')
        self.assertEqual((status, me['role'], me['channel'], me['builtin']), (200, 'operator', 'socket', False))
        self.assertEqual(self.call(alice, 'set_role', principal='alice', role='admin')[0], 403)
        rows = self.runtime.access.audit(limit=5)
        self.assertEqual((rows[0]['actor_sid'], rows[0]['action'], rows[0]['result']), ('unix:uid:1000', 'set_role', 'denied'))
        self.assertTrue(self.runtime.access.verify()['ok'])
        self.assertTrue(access.Access.validate(self.root / 'access.sqlite')['ok'])     # unix rows survive a restore check
        self.runtime.access.delete_role('unix:gid:2000')
        self.assertEqual(self.call(alice, 'whoami')[0], 403)
        for bad in ('unix:uid:', 'unix:uid:12a', 'unix:other:1', 'uid:5'):
            with self.subTest(bad=bad), self.assertRaises(access.AccessError):
                self.runtime.access.set_role(bad, 'x', 'user', 'viewer', 'test')

    def test_admin_command_line_prints_a_result_or_one_field(self):
        directory, port = str(self.root), int(self.runtime.admin_address.rsplit(':', 1)[1])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(server.main(['admin', '--directory', directory, '--admin-port', str(port),
                                          '--operation', 'invitation', '--field', 'code']), 0)
        code = out.getvalue().strip()
        self.assertTrue(code)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(server.main(['admin', '--directory', directory, '--admin-port', str(port),
                                          '--operation', 'invitations']), 0)
        self.assertEqual(len(json.loads(out.getvalue())), 1)
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(server.main(['admin', '--directory', directory, '--admin-port', str(port),
                                          '--operation', 'invitation', '--arguments', '[1]']), 1)
            self.assertEqual(server.main(['admin', '--directory', directory, '--admin-port', str(port),
                                          '--operation', 'no_such_operation']), 1)
        self.assertNotIn(code, error.getvalue())


class SocketPathTests(unittest.TestCase):
    def test_runtime_directory_then_data_directory_and_override(self):
        with patch.object(unix.os.path, 'isdir', return_value=True), patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LIFETIME_ADMIN_SOCKET_DIR', None)
            self.assertEqual(Path(unix.socket_path('/var/lib/x', 8444)), Path('/run/lifetime-server/admin-8444.sock'))
        with patch.object(unix.os.path, 'isdir', return_value=False):
            self.assertEqual(Path(unix.socket_path('/srv/data', 8444)), Path('/srv/data/admin-8444.sock'))
        with patch.dict(os.environ, {'LIFETIME_ADMIN_SOCKET_DIR': '/tmp/forwarded'}):
            self.assertEqual(Path(unix.socket_path('/srv/data', 9)), Path('/tmp/forwarded/admin-9.sock'))
        for port in (0, 70000, '8444', True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                unix.socket_path('/srv', port)

    @unittest.skipIf(LINUX, 'off Linux only')
    def test_the_channel_refuses_to_start_elsewhere(self):
        with self.assertRaises(OSError):
            unix.SocketServer('x.sock', lambda *a: b'', lambda identity: True)
        with self.assertRaises(OSError):
            unix.call('x.sock', b'{}')


@unittest.skipUnless(LINUX, 'Unix socket with SO_PEERCRED')
class RealSocketTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='lt-')
        self.addCleanup(lambda: __import__('shutil').rmtree(self.directory, ignore_errors=True))
        self.path = os.path.join(self.directory, 'admin-1.sock')

    def serve(self, admitted=True, handler=None):
        seen = []
        def answer(identity, raw):
            seen.append((identity, raw))
            return handler(identity, raw) if handler else b'reply:' + raw
        channel = unix.SocketServer(self.path, answer, lambda identity: admitted, group='').start()
        self.addCleanup(channel.stop)
        return channel, seen

    def test_peer_credentials_name_the_caller_and_the_socket_is_private(self):
        _, seen = self.serve()
        self.assertEqual(unix.call(self.path, b'hello', timeout=5), b'reply:hello')
        self.assertEqual(seen[0][0]['sid'], f'unix:uid:{os.getuid()}')
        self.assertIn(f'unix:gid:{os.getgid()}', seen[0][0]['groups'])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)      # no admin group here: owner only
        large = b'x' * 300_000
        self.assertEqual(len(unix.call(self.path, large, timeout=5)), len(large) + 6)

    def test_a_caller_without_a_role_is_disconnected_unread(self):
        _, seen = self.serve(admitted=False)
        with self.assertRaises(PermissionError):
            unix.call(self.path, b'secret request', timeout=5)
        self.assertEqual(seen, [])

    def test_client_refuses_a_listener_that_is_not_the_server(self):
        self.serve()
        with patch.object(unix, 'trusted_server_uids', return_value={123456}), self.assertRaises(PermissionError):
            unix.call(self.path, b'{}', timeout=5)

    def test_stale_socket_is_replaced_but_a_live_one_or_a_file_is_not(self):
        import socket
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(self.path)
        stale.close()                      # left behind by a crashed server
        channel, _ = self.serve()
        with self.assertRaises(FileExistsError):
            unix.SocketServer(self.path, lambda *a: b'', lambda i: True, group='').start()
        channel.stop()
        self.assertFalse(os.path.exists(self.path))
        Path(self.path).write_text('not a socket')
        with self.assertRaises(FileExistsError):
            unix.SocketServer(self.path, lambda *a: b'', lambda i: True, group='').start()

    def test_no_listener_means_no_channel(self):
        with self.assertRaises(FileNotFoundError):
            unix.call(self.path, b'{}', timeout=2)


@unittest.skipUnless(LINUX, 'Unix socket with SO_PEERCRED')
class RuntimeSocketTests(unittest.TestCase):
    _base = test_server_runtime.RuntimeTests
    setUp = _base.setUp

    def test_dashboard_client_uses_the_socket_once_this_account_has_a_role(self):
        port = int(self.runtime.admin_address.rsplit(':', 1)[1])
        self.assertTrue(os.path.exists(unix.socket_path(self.root, port)))
        if os.getuid() == 0:
            self.assertEqual(self.admin.call('whoami')['channel'], 'socket')
            return
        self.assertEqual(self.admin.call('whoami')['channel'], 'token')      # no role yet: the token decides
        me = unix.current_identity()
        self.runtime.access.set_role(me['sid'], me['name'], 'user', 'viewer', 'test')
        whoami = self.admin.call('whoami')
        self.assertEqual((whoami['channel'], whoami['sid'], whoami['role']), ('socket', me['sid'], 'viewer'))
        from lifetime_admin import NotAllowed
        with self.assertRaises(NotAllowed):
            self.admin.call('set_configuration', reporting_interval=120)
        self.runtime.stop()
        self.assertFalse(os.path.exists(unix.socket_path(self.root, port)))


@unittest.skipIf(sys.platform == 'win32', 'POSIX file modes')
class SecretFileTests(unittest.TestCase):
    def test_alert_secrets_are_a_0600_file_readable_back(self):
        import lifetime_notify as notify
        with tempfile.TemporaryDirectory() as directory:
            secrets = notify.Secrets(Path(directory) / notify.SECRET_FILE)
            secrets.save(dict(smtp_password='pw-123', webhook_url='https://hooks.example/abc'))
            info = os.stat(secrets.path)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertTrue(secrets.path.read_bytes().startswith(b'PLAIN1\n'))
            self.assertEqual(secrets.load(), dict(smtp_password='pw-123', webhook_url='https://hooks.example/abc'))


if __name__ == '__main__':
    unittest.main()
