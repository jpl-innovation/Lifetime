"""Diagnose connection, Self check and Copy diagnostics. Network: 127.0.0.1 only; discovery and Windows are mocked."""
import socket
import tempfile
import unittest
from unittest.mock import patch

import lifetime_connect as connect
from lifetime_connection import connection_line, issue

NO_WINDOWS = dict(profiles=lambda: [('Ethernet', 'Private')], here=lambda port: False)


def states(checks):
    return {check['text']: check['state'] for check in checks}


def words(checks):
    """Every line as the person reads it, in English."""
    return '\n'.join(check['text'].format(**check['values']) + ' ' + (check['fix'] or '').format(**check['values'])
                     for check in checks)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class DiagnoseTests(unittest.TestCase):
    """Real loopback Lifetime Server for the certificate and clock checks; no discovery, no other network."""

    @classmethod
    def setUpClass(cls):
        import sys
        import lifetime_permissions as permissions
        from lifetime_server import ServerRuntime
        cls.root = tempfile.TemporaryDirectory()
        cls.acl = patch.object(permissions, 'windows_acl')     # non-elevated Windows fixtures only
        if sys.platform == 'win32':
            cls.acl.start()
        cls.runtime = ServerRuntime(cls.root.name, address='https://localhost:8443', host='127.0.0.1', port=0).start()
        cls.port = int(cls.runtime.public_address.rsplit(':', 1)[1])

    @classmethod
    def tearDownClass(cls):
        cls.runtime.stop()
        import sys
        if sys.platform == 'win32':
            cls.acl.stop()
        cls.root.cleanup()

    def setUp(self):
        self.enterContext(patch('lifetime_discovery.discover', return_value=[]))
        self.enterContext(patch('lifetime_connection.local_ipv4', return_value=['192.168.1.20']))

    def diagnose(self, value='', saved=None, **options):
        return connect.diagnose(value, saved, **{**NO_WINDOWS, **options})

    def test_a_matching_code_passes_every_check_and_reads_the_server_clock(self):
        code = issue(self.runtime.fingerprint)
        checks = self.diagnose(connection_line(f'https://localhost:{self.port}', code))
        found = states(checks)
        self.assertEqual(found['Checking {address}, from the connection line.'], 'info')
        self.assertEqual(found['{host} resolves to {addresses} on this network.'], 'good')
        self.assertEqual(found['Port {port} on {host} is reachable.'], 'good')
        self.assertEqual(found['The server certificate names {host}.'], 'good')
        self.assertEqual(found['The server certificate matches the code.'], 'good')
        self.assertEqual(found['The server certificate is valid from {start} to {end}.'], 'good')
        self.assertEqual(found["This computer's clock is within {seconds} s of the server's."], 'good')
        self.assertEqual(found['The network search covers {networks} (port 8443).'], 'good')
        self.assertIn('192.168.1.0/24', words(checks))
        self.assertEqual(found['No Lifetime Server answered the network search (port 5353).'], 'warning')
        for check in checks:        # every failing line says what to do
            if check['state'] in ('warning', 'danger'):
                self.assertTrue(check['fix'], check['text'])
        self.assertNotIn(code, repr(checks))
        self.assertNotIn(code.replace('-', ''), repr(checks))

    def test_a_clock_far_from_the_servers_is_reported(self):
        import time
        checks = self.diagnose(connection_line(f'https://localhost:{self.port}', issue(self.runtime.fingerprint)),
                               now=lambda: time.time() + 3600)
        self.assertEqual(states(checks)["This computer's clock differs from the server's by {minutes} minutes."], 'warning')
        self.assertIn('differs from the server\'s by 60 minutes', words(checks))

    def test_a_code_from_another_server_fails_and_nothing_is_sent(self):
        other = issue('ab' * 32)
        with patch('lifetime_connect.server_time') as clock:
            checks = self.diagnose(connection_line(f'https://localhost:{self.port}', other))
        clock.assert_not_called()       # no request before the certificate matched the code
        found = states(checks)
        self.assertEqual(found["This server's certificate does not match the code."], 'danger')
        self.assertEqual(found['Server clock not compared: Lifetime sends nothing to a server before the typed code '
                               'proves it is the right one.'], 'neutral')
        self.assertIn('Create a new code on the server', words(checks))

    def test_an_address_the_certificate_does_not_name(self):
        checks = self.diagnose(connection_line(f'https://127.0.0.1:{self.port}', issue(self.runtime.fingerprint)))
        found = states(checks)
        self.assertEqual(found['{host} is an IP address: no name lookup needed.'], 'good')
        self.assertEqual(found['The server certificate does not name {host}. It names: {names}.'], 'danger')
        self.assertIn('It names: localhost', words(checks))
        self.assertIn('Other server names and IPs', words(checks))

    def test_the_saved_connection_is_checked_without_any_request(self):
        with patch('lifetime_connect.server_time') as clock:
            checks = self.diagnose('', f'https://localhost:{self.port}')
        clock.assert_not_called()
        found = states(checks)
        self.assertEqual(found['Checking {address}, the saved connection.'], 'info')
        self.assertEqual(found['The server certificate names {host}.'], 'good')
        self.assertNotIn('The server certificate matches the code.', found)

    def test_a_closed_port_says_the_server_is_not_running(self):
        checks = self.diagnose('', f'https://127.0.0.1:{free_port()}')
        found = states(checks)
        self.assertEqual(found['Port {port} on {host} refused the connection.'], 'danger')
        self.assertIn('Lifetime Server is not running there', words(checks))
        self.assertEqual(found['Certificate not checked: the server could not be reached.'], 'neutral')

    def test_a_name_that_does_not_resolve(self):
        real = socket.getaddrinfo
        def lookup(host, *args, **kwargs):
            if host == 'server.invalid':
                raise socket.gaierror('no such name')
            return real(host, *args, **kwargs)
        with patch('socket.getaddrinfo', lookup):
            checks = self.diagnose(connection_line('https://server.invalid:8443', issue('ab' * 32)))
        found = states(checks)
        self.assertEqual(found['{host} does not resolve on this network.'], 'danger')
        self.assertEqual(found['Port {port} not checked: the server name did not resolve.'], 'neutral')
        self.assertIn("IP address instead", words(checks))

    def test_only_a_code_uses_what_discovery_found_and_its_source_when_the_name_fails(self):
        code = issue(self.runtime.fingerprint)
        answer = dict(instance='Lifetime-SRV', host='srv.invalid', port=self.port, source='127.0.0.1',
                      address=f'https://srv.invalid:{self.port}', fingerprint_prefix=self.runtime.fingerprint[:10])
        real = socket.getaddrinfo
        def lookup(host, *args, **kwargs):
            if host == 'srv.invalid':
                raise socket.gaierror('no such name')
            return real(host, *args, **kwargs)
        with patch('lifetime_discovery.discover', return_value=[answer]), patch('socket.getaddrinfo', lookup):
            checks = self.diagnose(code)
        found = states(checks)
        self.assertEqual(found['Checking: searching the network for the server (only a code was typed).'], 'info')
        self.assertEqual(found['A Lifetime Server answered on this network: {servers}.'], 'good')
        self.assertIn('Lifetime-SRV', words(checks))
        self.assertEqual(found['{host} does not resolve on this network. Lifetime uses {source}, the address the server '
                               'answered from.'], 'warning')
        self.assertEqual(found['Port {port} on {host} is reachable.'], 'good')
        self.assertEqual(found['The server certificate matches the code.'], 'good')

    def test_only_a_code_and_no_answer_explains_what_was_not_checked(self):
        checks = self.diagnose(issue('ab' * 32))
        found = states(checks)
        self.assertEqual(found['Name, port and certificate not checked: there is no server address to check.'], 'neutral')
        self.assertEqual(found['No Lifetime Server answered the network search (port 5353).'], 'warning')

    def test_an_incomplete_code_and_no_network(self):
        with patch('lifetime_connection.local_ipv4', return_value=[]):
            checks = self.diagnose('LTS-ABCD')
        found = states(checks)
        self.assertEqual(found['The code or connection line is not complete.'], 'danger')
        self.assertEqual(found['This computer has no local IPv4 network to search.'], 'danger')

    def test_the_ubuntu_host_name_is_flagged_as_handled(self):
        real = socket.getaddrinfo
        def lookup(host, *args, **kwargs):
            if host == socket.gethostname():
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.1.1', 0))]
            return real(host, *args, **kwargs)
        with patch('socket.getaddrinfo', lookup):
            checks = self.diagnose(issue('ab' * 32))
        self.assertIn('points to 127.0.1.1, as on Ubuntu. Handled', words(checks))

    @unittest.skipUnless(__import__('sys').platform == 'win32', 'Windows network profiles')
    def test_a_public_network_matters_only_when_this_computer_is_the_server(self):
        server = connect._profile_checks(lambda: [('Wi-Fi', 'Public')], lambda port: True, 8443)
        self.assertEqual(server[0]['state'], 'danger')
        self.assertIn('Set the network to Private', words(server))
        client = connect._profile_checks(lambda: [('Wi-Fi', 'Public')], lambda port: False, 8443)
        self.assertEqual(client[0]['state'], 'info')
        self.assertEqual(connect._profile_checks(lambda: None, lambda port: False, 8443)[0]['state'], 'neutral')
        self.assertEqual(connect._profile_checks(lambda: [('Ethernet', 'DomainAuthenticated')], lambda port: True, 8443)[0]['state'], 'good')

    def test_windows_profiles_are_read_without_changing_anything(self):
        from types import SimpleNamespace
        calls = []
        def run(command, **options):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout='Wi-Fi|Public\r\nEthernet 2|Private\r\n')
        self.assertEqual(connect.windows_profiles(run), [('Wi-Fi', 'Public'), ('Ethernet 2', 'Private')])
        self.assertTrue(all('Set-' not in part for part in calls[0]))
        self.assertIsNone(connect.windows_profiles(lambda command, **options: SimpleNamespace(returncode=1, stdout='')))

    def test_the_report_is_plain_text_without_the_code(self):
        from lifetime_i18n import TABLES
        code = issue(self.runtime.fingerprint)
        checks = self.diagnose(connection_line(f'https://localhost:{self.port}', code))
        checks.append(connect._check('info', 'Checking {address}, from the connection line.', address='lifetime-connect-v1:https://x:8443 ' + code))
        for language in ('en', 'vi'):
            text = connect.diagnosis_report(checks, lambda key, **values: TABLES[language][key].format(**values))
            self.assertNotIn(code, text)
            self.assertNotIn(code.replace('-', '')[3:], text)
            self.assertIn('LTS-****', text)
            self.assertIn(f'localhost:{self.port}', text)
        self.assertIn('[Passed]', connect.diagnosis_report(checks, lambda key, **values: key.format(**values)))


class DiagnoseButtonTests(unittest.TestCase):
    def setUp(self):
        from qt_test_support import qt_app
        from test_company_gui import FakeTasks
        self.app = qt_app()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.enterContext(patch('lifetime_connect.Tasks', FakeTasks))
        self.enterContext(patch.dict('os.environ', LIFETIME_REDUCE_MOTION='1'))
        self.page = connect.ConnectPage(directory=self.root.name, machine_root=self.root.name + '/agent')
        self.page.timer.stop()
        self.addCleanup(self.app.processEvents)
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.close)

    def test_the_button_runs_in_the_background_and_lists_one_line_per_check(self):
        page = self.page
        code = issue('ab' * 32)
        page.code.setText(code)
        status_jobs = len(page.tasks.jobs)      # the status poll started by the page is still running
        page.diagnose()
        self.assertEqual(len(page.diagnose_tasks.jobs), 1)
        self.assertEqual(len(page.tasks.jobs), status_jobs)      # its own worker: the poll and Connect are untouched
        self.assertFalse(page.diagnose_buttons[0].isEnabled())
        with patch('lifetime_connect.diagnose', return_value=[]) as run:
            page.diagnose_tasks.jobs[-1]()
        run.assert_called_once_with(code, None)
        checks = [connect._check('good', 'Port {port} on {host} is reachable.', host='srv', port=8443),
                  connect._check('danger', '{host} does not resolve on this network.',
                                 "Type the connection line with the server's IP address instead (Administration on the "
                                 "server shows it), or ask IT to add {host} to this network's DNS.", host='srv')]
        page.diagnose_tasks.resolve(checks)
        self.assertTrue(page.diagnose_buttons[0].isEnabled())
        self.assertFalse(page.diagnosis.isHidden())
        self.assertEqual(page.diagnosis_rows.count(), 2)
        page.copy_report()
        from PyQt6.QtWidgets import QApplication
        copied = QApplication.clipboard().text()
        self.assertIn('[Passed] Port 8443 on srv is reachable.', copied)
        self.assertIn('What to do:', copied)
        self.assertNotIn(code, copied)
        page.show_step(2)
        self.assertTrue(page.diagnosis.isHidden())      # not while connecting

    def test_a_failed_diagnosis_says_so(self):
        self.page.diagnose()
        self.page.diagnose_tasks.resolve(error='RuntimeError')
        self.assertEqual(self.page.diagnosis_checks[0]['text'], 'The diagnosis could not finish.')

    def test_vietnamese_lines(self):
        page = connect.ConnectPage(directory=self.root.name, machine_root=self.root.name + '/agent', language='vi')
        page.timer.stop()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        page._diagnosed([connect._check('warning', 'No Lifetime Server answered the network search (port 5353).',
                                        'On the server, allow 5353/udp in the firewall and set its network to Private. Guest '
                                        'and some office Wi-Fi block these answers: then type the connection line from the '
                                        'server (Add device) instead of only the code.')])
        texts = [label.text() for label in page.diagnosis.findChildren(type(page.code_error))]
        self.assertIn('Không có Lifetime Server nào trả lời khi tìm trong mạng (cổng 5353).', texts)
        self.assertTrue(any(text.startswith('Cách xử lý:') for text in texts))


class SelfCheckTests(unittest.TestCase):
    """The server's Self check on a loopback runtime (no discovery, no LAN); firewall commands are faked."""

    def setUp(self):
        import sys
        import lifetime_permissions as permissions
        import lifetime_server as server
        self.server = server
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))     # non-elevated Windows fixtures only
        self.enterContext(patch.object(server, 'firewall_checks', return_value=[]))
        self.runtime = server.ServerRuntime(temporary.name, address='https://localhost:8443', host='127.0.0.1', port=0).start()
        self.addCleanup(self.runtime.stop)

    def test_a_loopback_server_says_other_computers_cannot_reach_it(self):
        result = self.runtime.self_check()
        found = states(result['checks'])
        self.assertEqual(found['Lifetime Server listens only on {host}: other computers cannot reach it.'], 'danger')
        self.assertEqual(found['{host} means this computer itself: other computers cannot use it.'], 'warning')
        self.assertEqual(found['The server certificate names {host}.'], 'good')
        self.assertEqual(found['Discovery is off: the server listens only on this computer.'], 'info')
        self.assertEqual(found['The certificate is valid until {date} ({days} days). Lifetime renews it 30 days before.'], 'good')
        self.assertIn('Server time: {time} UTC.', found)
        self.assertIn('Computers are told to use https://localhost:', words(result['checks']))
        for check in result['checks']:
            self.assertEqual(check['message'], check['text'].format(**check['values']))     # English for the command line
            if check['state'] in ('warning', 'danger'):
                self.assertTrue(check['what_to_do'], check['text'])
        admin_port = self.runtime.admin_address.rsplit(':', 1)[1]
        self.assertNotIn(':' + admin_port, repr(result))      # the administration port is never reported

    def test_every_address_its_ip_names_discovery_and_an_ending_certificate(self):
        import datetime as dt
        runtime = self.runtime
        port = runtime._servers[0].server_port
        ending = (runtime.clock() + dt.timedelta(days=10)).isoformat()
        with patch.object(runtime._servers[0], 'server_address', ('0.0.0.0', port)), \
                patch.object(runtime, '_lan_names', return_value=['192.168.1.5']), \
                patch.object(runtime, 'certificate', dict(runtime.certificate, expires=ending)), \
                patch.object(runtime, 'discovery_state', 'unavailable'):
            checks = runtime.self_check()['checks']
        found = states(checks)
        self.assertEqual(found['Lifetime Server listens on port {port} of every address of this computer: {addresses}.'], 'good')
        self.assertIn('192.168.1.5', words(checks))
        self.assertEqual(found["The certificate does not name this server's IP address {addresses}."], 'warning')
        self.assertEqual(found['Discovery is off: port 5353 (UDP) could not be opened. Another program may be using it, '
                               'such as Bonjour or Avahi.'], 'warning')
        self.assertEqual(found['The certificate ends on {date}, in {days} days.'], 'warning')
        with patch.object(runtime, 'certificate', dict(runtime.certificate, warning='renewal-failed')), \
                patch.object(runtime, 'discovery_state', 'running'):
            found = states(runtime.self_check()['checks'])
        self.assertEqual(found['Lifetime Server could not renew its certificate. It works until {date}.'], 'danger')
        self.assertEqual(found['Discovery is answering on port 5353 (UDP): computers find this server with the code alone.'], 'good')

    def test_a_viewer_may_run_it_and_a_caller_without_a_role_may_not(self):
        import lifetime_access as access
        self.assertIn('self_check', access.READ)
        self.assertFalse(access.state_changing('self_check'))
        viewer = dict(sid='S-1-5-21-1000-1000-1000-1001', name='LAB\\viewer', groups=[])
        stranger = dict(sid='S-1-5-21-1000-1000-1000-1002', name='LAB\\nobody', groups=[])
        self.runtime.access.set_role(viewer['sid'], viewer['name'], 'user', 'viewer', 'test')
        self.assertIn('checks', self.runtime.admin_call('self_check', {}, viewer))
        self.assertIn('checks', self.runtime.admin_call('self_check', {}, access.TOKEN_ACTOR))
        with self.assertRaises(self.server._HTTPError) as refused:
            self.runtime.admin_call('self_check', {}, stranger)
        self.assertEqual(refused.exception.status, 403)
        with self.assertRaises(Exception):
            self.runtime.admin_call('self_check', {'extra': 1}, viewer)

    def test_the_command_line_runs_it_through_the_admin_client(self):
        import contextlib
        import io
        from lifetime_admin import OPERATIONS
        self.assertIn('self_check', OPERATIONS)
        output = io.StringIO()
        with patch('lifetime_admin.AdminClient.call', return_value=dict(checks=[], checked_at='now')) as call, \
                contextlib.redirect_stdout(output):
            code = self.server.main(['admin', '--directory', self.runtime.directory.as_posix(), '--operation', 'self_check',
                                     '--admin-port', '8444'])
        self.assertEqual(code, 0)
        call.assert_called_once_with('self_check')
        self.assertIn('"checks"', output.getvalue())


class FirewallTests(unittest.TestCase):
    """Only reads: the fake command runner records every command; none of them changes anything."""

    def runner(self, outputs):
        from types import SimpleNamespace
        self.commands = []

        def run(command, **options):
            self.commands.append(command)
            for key, (code, text) in outputs.items():
                if key in ' '.join(command):
                    return SimpleNamespace(returncode=code, stdout=text)
            return SimpleNamespace(returncode=3, stdout='inactive\n')
        return run

    def check(self, platform, outputs, port=8443, conf=None):
        import lifetime_server as server
        with patch('lifetime_connection.local_ipv4', return_value=['192.168.1.5']):
            checks = server.firewall_checks(port, self.runner(outputs), platform, conf or 'missing-ufw.conf')
        for command in self.commands:
            text = ' '.join(command)
            for verb in ('Set-', 'New-', 'Remove-', 'Enable-', 'Disable-', ' allow', ' deny', '--add', '--remove', '--permanent'):
                self.assertNotIn(verb, text)
        return checks

    def test_windows_rule_and_profiles(self):
        good = self.check('win32', {'powershell': (0, 'rule|True|8443\r\nprofile|Ethernet|DomainAuthenticated\r\n')})
        self.assertEqual([check['state'] for check in good], ['good', 'good'])
        missing = self.check('win32', {'powershell': (0, 'profile|Wi-Fi|Public\r\n')})
        self.assertEqual(states(missing)['The Windows firewall rule LifetimeServer-Connect is missing: other computers cannot '
                                         'reach port {port}.'], 'danger')
        self.assertEqual(states(missing)['The network {networks} of this server is Public: Windows lets no computer reach it.'], 'danger')
        self.assertIn('Set the network to Private', words(missing))
        moved = self.check('win32', {'powershell': (0, 'rule|True|9443\r\n')})
        self.assertIn('allows port 9443, but the server uses port 8443', words(moved))
        off = self.check('win32', {'powershell': (0, 'rule|False|8443\r\n')})
        self.assertEqual(off[0]['text'], 'The Windows firewall rule LifetimeServer-Connect is turned off.')
        unread = self.check('win32', {'powershell': (1, '')})
        self.assertEqual(unread[0]['state'], 'neutral')

    def test_linux_ufw(self):
        import os
        with tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False) as conf:
            conf.write('# ufw\nENABLED=yes\n')
        self.addCleanup(os.unlink, conf.name)
        status = ('Status: active\n\nTo                         Action      From\n--                         ------      ----\n'
                  '8443/tcp                   ALLOW       192.168.1.0/24\n')
        checks = self.check('linux', {'is-active ufw': (0, 'active\n'), 'ufw status': (0, status)}, conf=conf.name)
        found = states(checks)
        self.assertEqual(found['{firewall} allows {port}/tcp.'], 'good')
        self.assertEqual(found['{firewall} does not allow 5353/udp: computers cannot find the server with the code alone.'], 'warning')
        self.assertIn('sudo ufw allow from 192.168.1.0/24 to any port 5353 proto udp', words(checks))
        unreadable = self.check('linux', {'is-active ufw': (0, 'active\n'), 'ufw status': (1, '')}, conf=conf.name)
        self.assertEqual(states(unreadable)['ufw is on, but only an administrator can read its rules.'], 'warning')

    def test_linux_firewalld_and_no_firewall(self):
        listing = ('public (active)\n  services: dhcpv6-client mdns ssh\n  ports: \n  rich rules: \n'
                   '\trule family="ipv4" source address="192.168.1.0/24" port port="8443" protocol="tcp" accept\n')
        checks = self.check('linux', {'is-active firewalld': (0, 'active\n'), 'firewall-cmd': (0, listing)})
        self.assertEqual([check['state'] for check in checks], ['good', 'good'])
        closed = self.check('linux', {'is-active firewalld': (0, 'active\n'), 'firewall-cmd': (0, '  services: ssh\n  ports: \n')})
        self.assertEqual([check['state'] for check in closed], ['danger', 'warning'])
        self.assertIn("--add-rich-rule='rule family=ipv4 source address=192.168.1.0/24 port port=8443", words(closed))
        none = self.check('linux', {})
        self.assertEqual(none[0]['text'], 'No firewall (ufw or firewalld) is on: nothing blocks port {port}.')
        self.assertEqual(self.check('darwin', {}), [])


class SelfCheckPageTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from qt_test_support import qt_app
        import lifetime_server_gui as gui
        from test_server_gui import FakeAdmin
        self.app, self.gui = qt_app(), gui
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        result = dict(checked_at='2026-11-23T10:00:00+00:00', checks=[
            dict(state='danger', text='The Windows firewall rule LifetimeServer-Connect is turned off.', values={},
                 fix='Turn it on in Windows Defender Firewall > Inbound rules, or run the Lifetime Server installer again.'),
            dict(state='good', text='Lifetime Server listens on {host}, port {port}.', values=dict(host='10.0.0.2', port='8443'), fix=None)])

        class Admin(FakeAdmin):
            def call(self, operation, **arguments):
                if operation == 'self_check':
                    return result
                return super().call(operation, **arguments)
        self.api = Admin(Path(temporary.name))
        self.enterContext(patch.object(gui, 'AdminClient', return_value=self.api))
        self.window = gui.ServerWindow(Path(temporary.name), 8123, language='vi')
        self.window.timer.stop()
        self.addCleanup(self.close)

    def close(self):
        from qt_test_support import wait_until
        self.window.close()
        wait_until(lambda: not self.window._jobs, 5000)
        self.window.deleteLater()
        self.app.sendPostedEvents(None, self.gui.QEvent.Type.DeferredDelete)

    def test_the_administration_page_lists_each_check_with_what_to_do(self):
        from qt_test_support import wait_until
        self.window.run_self_check()
        wait_until(lambda: self.window.self_check_rows.count() == 2, 5000)
        texts = [label.text() for label in self.window.findChildren(self.gui.QLabel)]
        self.assertIn('Quy tắc tường lửa Windows LifetimeServer-Connect đang tắt.', texts)
        self.assertTrue(any(text.startswith('Cách xử lý: Hãy bật nó') for text in texts))
        self.assertIn('Lifetime Server lắng nghe trên 10.0.0.2, cổng 8443.', texts)


if __name__ == '__main__':
    unittest.main()
