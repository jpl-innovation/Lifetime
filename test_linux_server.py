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


class RestartBindTests(unittest.TestCase):
    def test_a_restart_can_bind_through_time_wait_but_windows_never_shares_the_port(self):
        self.assertEqual(server._Server.allow_reuse_address, sys.platform != 'win32')

    @unittest.skipUnless(LINUX, 'Linux TIME_WAIT semantics')
    def test_rebinding_after_server_side_close(self):
        import socket
        def listener(port=0):
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, int(server._Server.allow_reuse_address))
            s.bind(('127.0.0.1', port))
            s.listen()
            return s
        first = listener()
        port = first.getsockname()[1]
        client = socket.create_connection(('127.0.0.1', port))
        accepted, _ = first.accept()
        accepted.close()                    # the server closes first: its side of the port enters TIME_WAIT
        client.close()
        first.close()
        listener(port).close()              # a restarted server binds at once


@unittest.skipIf(sys.platform == 'win32', 'POSIX file modes')
class OldGlibcChmodTests(unittest.TestCase):
    def test_private_modes_without_fchmodat_nofollow(self):
        """glibc < 2.32 (Debian 10/11, Ubuntu 20.04, RHEL 8): chmod(follow_symlinks=False) raises ENOTSUP."""
        import errno
        import lifetime_permissions as permissions
        real = os.chmod
        def old_glibc(path, mode, *, follow_symlinks=True):
            if not follow_symlinks:
                raise OSError(errno.ENOTSUP, 'Operation not supported')
            return real(path, mode)
        with tempfile.TemporaryDirectory() as directory, patch.object(permissions.os, 'chmod', old_glibc):
            folder, private = Path(directory) / 'data', Path(directory) / 'data' / 'admin-token'
            folder.mkdir(mode=0o755)
            private.write_text('x')
            permissions.protect_directory(folder)
            permissions.protect_private(private)
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(private).st_mode), 0o600)
            os.symlink(private, Path(directory) / 'link')
            with self.assertRaises(ValueError):
                permissions.protect_private(Path(directory) / 'link')


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


class PairingScriptTests(unittest.TestCase):
    """CI's pairing test keeps doing what 1.2.1 lacked (packaging/linux/pairing_test.sh runs in GitHub Actions)."""

    def test_the_client_pairs_by_code_only_with_ubuntus_host_name_and_an_unresolvable_server_name(self):
        root = Path(__file__).parent
        script = (root / 'packaging' / 'linux' / 'pairing_test.sh').read_text(encoding='utf-8')
        enrollments = [line for line in script.splitlines() if '--connection-stdin' in line]
        self.assertEqual(len(enrollments), 2)
        for enroll in enrollments:
            self.assertNotIn('--server', enroll)
        # The second client cannot send the discovery query, so only the network search can find the server.
        second = script[script.index('--- second client'):]
        self.assertLess(second.index('ip route add unreachable 224.0.0.0/4'), second.index('--connection-stdin'))
        self.assertIn("then echo 'Multicast is not blocked'; exit 1; fi", second)
        self.assertIn('127.0.1.1', script)
        self.assertIn('grep -v -w -e "$name"', script)          # Docker's line with the real address is removed
        self.assertIn('--address https://lifetime.school.lan:8443 --host 0.0.0.0', script)
        self.assertNotIn('|| true\n', script[script.index('--connection-stdin'):])
        workflow = (root / '.github' / 'workflows' / 'windows.yml').read_text(encoding='utf-8')
        self.assertIn('bash packaging/linux/pairing_test.sh artifacts', workflow)
        self.assertIn('linux-pairing, linux-docker]', workflow)          # a release waits for it


class PairingHostsTests(unittest.TestCase):
    """The pairing test's /etc/hosts rewrite, run in bash on temporary files with a stand-in for getent."""

    def test_the_computers_own_name_is_left_with_127_0_1_1_only(self):
        from test_linux_desktop import BASH, run_bash
        if not BASH:
            self.skipTest('bash (Git for Windows on Windows)')
        script = (Path(__file__).parent / 'packaging' / 'linux' / 'pairing_test.sh').read_text(encoding='utf-8')
        snippet = script[script.index("ubuntu_hosts='") + len("ubuntu_hosts='"):script.index("'\n\ndocker network create")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hosts, name = root / 'hosts', root / 'hostname'
            name.write_text('pc-01\n', newline='\n')
            # What Docker writes: the container's real address for its name, which a 1.2.1 client would have used.
            hosts.write_text('127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n'
                             'fe00::0\tip6-localnet\n172.18.0.3\tpc-01\n', newline='\n')
            for old, new in (('/etc/hosts', hosts.as_posix()), ('/tmp/hosts', (root / 'new').as_posix()),
                             ('/proc/sys/kernel/hostname', name.as_posix())):
                self.assertIn(old, snippet)
                snippet = snippet.replace(old, new)
            getent = 'getent() { awk -v n="$2" \'{for (i = 2; i <= NF; i++) if ($i == n) print $1}\' "$HOSTS"; }\n'
            result = run_bash(getent + snippet + '\n', HOSTS=hosts.as_posix())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = hosts.read_text(encoding='utf-8').splitlines()
            self.assertEqual([line for line in lines if 'pc-01' in line], ['127.0.1.1 pc-01'])
            self.assertIn('127.0.0.1 localhost', lines)
            self.assertNotIn('172.18.0.3', hosts.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
