"""Diagnose connection, Self check and Copy diagnostics. Network: 127.0.0.1 only; discovery and Windows are mocked."""
import json
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
            checks = runtime._self_check(9e9)['checks']        # not the 30 s reuse: each case runs
        found = states(checks)
        self.assertEqual(found['Lifetime Server listens on port {port} of every address of this computer: {addresses}.'], 'good')
        self.assertIn('192.168.1.5', words(checks))
        self.assertEqual(found["The certificate does not name this server's IP address {addresses}."], 'warning')
        self.assertEqual(found['Discovery is off: port 5353 (UDP) could not be opened. Another program may be using it, '
                               'such as Bonjour or Avahi.'], 'warning')
        self.assertEqual(found['The certificate ends on {date}, in {days} days.'], 'warning')
        with patch.object(runtime, 'certificate', dict(runtime.certificate, warning='renewal-failed')), \
                patch.object(runtime, 'discovery_state', 'running'):
            found = states(runtime._self_check(9e9)['checks'])
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
        self.assertEqual(found['{firewall} shows no rule that allows 5353/udp: computers may not find the server with the '
                               'code alone.'], 'warning')
        self.assertIn('sudo ufw allow from 192.168.1.0/24 to any port 5353 proto udp', words(checks))
        unreadable = self.check('linux', {'is-active ufw': (0, 'active\n'), 'ufw status': (1, '')}, conf=conf.name)
        self.assertEqual(states(unreadable)['ufw is on, but only an administrator can read its rules.'], 'warning')

    def test_linux_firewalld_and_no_firewall(self):
        listing = ('public (active)\n  services: dhcpv6-client mdns ssh\n  ports: \n  rich rules: \n'
                   '\trule family="ipv4" source address="192.168.1.0/24" port port="8443" protocol="tcp" accept\n')
        checks = self.check('linux', {'is-active firewalld': (0, 'active\n'), 'firewall-cmd': (0, listing)})
        self.assertEqual([check['state'] for check in checks], ['good', 'good'])
        closed = self.check('linux', {'is-active firewalld': (0, 'active\n'), 'firewall-cmd': (0, '  services: ssh\n  ports: \n')})
        self.assertEqual([check['state'] for check in closed], ['warning', 'warning'])      # not seen is not certain
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

    def test_copy_diagnostics_from_the_administration_page(self):
        from PyQt6.QtWidgets import QApplication
        from qt_test_support import wait_until
        QApplication.clipboard().clear()
        self.window.copy_diagnostics()
        wait_until(lambda: 'Tự kiểm tra' in QApplication.clipboard().text(), 5000)
        copied = QApplication.clipboard().text()
        self.assertIn('Quy tắc tường lửa Windows LifetimeServer-Connect đang tắt.', copied)
        self.assertIn('Thông tin chẩn đoán Lifetime', copied)


class CopyDiagnosticsTests(unittest.TestCase):
    """Every source of Copy diagnostics gets a secret; none may reach the copied text."""

    def setUp(self):
        import json
        import secrets
        from pathlib import Path
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.code = issue('cd' * 32)
        self.token = secrets.token_urlsafe(32)
        self.hex_token = secrets.token_hex(16)
        self.bundle = 'lifetime-enrollment-v1:' + secrets.token_urlsafe(60)
        plain = self.code[4:].replace('-', '')
        self.spaced = ' '.join(plain[i:i + 4] for i in range(0, 20, 4)).lower()     # no LTS, spaces, lower case
        self.secrets = [self.code, self.code.replace('-', ''), self.code.replace('-', '')[3:], self.code.lower(),
                        self.token, self.bundle[23:], 'hunter2', 'SMTP-PASS-99', 'T000/B000/XXXXSECRET', 'CRED-7f3a',
                        '5CG1234XYZ', 'ASSET-77-SECRET', 'poll-secret-42', self.hex_token,
                        # spellings in fields the allow-lists keep: only scrub() itself can catch them
                        'hunter3', 'SmtpPw9', 'SLACKKEY77', 'ESCAPEDSECRET', 'NEXTLINESECRET', 'Jayden', 'UrlPw5',
                        self.spaced, 'PC-of-Mai']
        (self.root / 'settings.json').write_text(json.dumps(dict(
            language='en', completed=True, history=True, identity=dict(display_name='PC', asset_tag='ASSET-77-SECRET'),
            admin_token=self.token, smtp_password='SMTP-PASS-99')), encoding='utf-8')
        agent = self.root / 'agent'
        agent.mkdir()
        (agent / 'status.json').write_text(json.dumps(dict(
            enabled=True, phase='approved', status='connected', server='https://srv.lan:8443',
            server_name=f'srv {self.spaced} https://u:UrlPw5@srv.lan', error='timeout', error_at='2026-11-23T08:00:00Z', checked_at=0,
            credential='CRED-7f3a', poll_token='poll-secret-42', bundle=self.bundle)), encoding='utf-8')
        self.log = self.root / 'install.log'
        self.log.write_text('\n'.join([
            '2026-11-23 Installing the reporting service',
            f'connection line lifetime-connect-v1:https://srv.lan:8443 {self.code}',
            f'raw code {self.code.replace("-", "")[3:]} and {self.code.lower()}',
            f'bundle {self.bundle}', f'Authorization: Bearer {self.token}', f'admin-token={self.token}',
            'password=hunter2', '"smtp_password": "SMTP-PASS-99"', 'webhook https://hooks.example.com/services/T000/B000/XXXXSECRET',
            'Serial number: 5CG1234XYZ', 'credential: CRED-7f3a', f'token {self.hex_token}',
            'smtp password is hunter3', 'relay smtp://alerts:SmtpPw9@mail.example.com:587',
            'posting to https://hooks.slack.com?key=SLACKKEY77', '{\\"token\\": \\"ESCAPEDSECRET\\"}',
            'token:', '    NEXTLINESECRET', 'log in C:\\Users\\Jayden Phan\\AppData\\Local\\Temp',
            f'code typed: {self.spaced}',
            'Finished']), encoding='utf-8')
        self.checks = [connect._check('info', 'Checking {address}, from the connection line.',
                                      address=f'lifetime-connect-v1:https://srv.lan:8443 {self.code}'),
                       connect._check('danger', '{host} does not resolve on this network.', None, host=self.bundle)]

    def assert_clean(self, text):
        for secret in self.secrets:
            self.assertNotIn(secret, text)
            self.assertNotIn(secret.lower(), text.lower())

    def test_desktop(self):
        text = connect.diagnostics_text(lambda key, **values: key.format(**values), self.root, self.root / 'agent',
                                        self.checks, [self.log, self.root / 'missing.log'])
        self.assert_clean(text)
        from lifetime_protocol import CLIENT_VERSION
        for expected in (f'Lifetime {CLIENT_VERSION}', 'history: True', 'status: connected', 'server: https://srv.lan:8443',
                         'Last problem: No answer from the server.', '== Connection diagnosis ==', '[Problem]',
                         'Log: install.log, last 21 lines', 'Installing the reporting service', 'Finished'):
            self.assertIn(expected, text)
        self.assertNotIn('display_name', text)      # identity is never copied
        none = connect.diagnostics_text(lambda key, **values: key.format(**values), self.root, self.root / 'agent', [], [])
        self.assertIn('Not run. Press Diagnose connection first.', none)

    def test_server(self):
        import lifetime_server_gui as gui
        info = dict(server_version='1.3.0', os='Windows', name='SRV', address='https://srv.lan:8443', host='0.0.0.0', port=8443,
                    listening_addresses=[dict(address='127.0.0.1', port=48123)], admin_token=self.token,
                    specs=dict(serial='5CG1234XYZ'), webhook_url='https://hooks.example.com/services/T000/B000/XXXXSECRET',
                    server_names=['srv.lan', f'x {self.spaced}'], organisation='smtp password is hunter3', certificate=dict(expires='2027-11-01T00:00:00+00:00', names=['srv.lan']),
                    reporting_interval=60, discovery=True, fingerprint='ab' * 32)
        check = dict(checks=self.checks + [dict(state='good', text='Server time: {time} UTC.', values=dict(time='now'), fix=None)])
        log = [dict(time='2026-11-23T08:00:00Z', kind='enrollment-refused', category='code', name='PC-of-Mai', count=2)]
        text = gui.diagnostics_text(lambda key, **values: key.format(**values), info, check, log, [self.log])
        self.assert_clean(text)
        self.assertNotIn('48123', text)         # the administration port
        for expected in ('server_version: 1.3.0', 'port: 8443', 'certificate expires: 2027-11-01', '== Self check ==',
                         '[Passed] Server time: now UTC.', 'enrollment-refused code', 'fingerprint: ' + 'ab' * 32,
                         'Installing the reporting service'):
            self.assertIn(expected, text)
        self.assertIn('Self check could not run.', gui.diagnostics_text(lambda key, **values: key.format(**values), info, None, [], []))

    def test_a_large_log_is_copied_quickly(self):
        import time
        import lifetime_server_gui as gui
        self.log.write_text(('x' * 255 + '\n') * 1000 + 'token=' + 'Q' * 300000 + '\n', encoding='utf-8')
        started = time.perf_counter()
        desktop = connect.diagnostics_text(lambda key, **values: key.format(**values), self.root, self.root / 'agent', [], [self.log])
        server = gui.diagnostics_text(lambda key, **values: key.format(**values), dict(name='SRV'), None, [], [self.log])
        self.assertLess(time.perf_counter() - started, 2.0)
        for text in (desktop, server):
            self.assertNotIn('Q' * 32, text)

    def test_the_connect_page_button_copies_in_the_background(self):
        from PyQt6.QtWidgets import QApplication
        from qt_test_support import qt_app
        from test_company_gui import FakeTasks
        self.app = qt_app()     # kept: a QApplication nobody holds is deleted at once
        self.addCleanup(self.app.processEvents)
        with patch('lifetime_connect.Tasks', FakeTasks), patch.dict('os.environ', LIFETIME_REDUCE_MOTION='1'):
            page = connect.ConnectPage(directory=str(self.root), machine_root=str(self.root / 'agent'))
        page.timer.stop()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        page.diagnosis_checks = self.checks
        with patch('lifetime_connect.setup_logs', return_value=[self.log]):
            page.copy_diagnostics()
            self.assertFalse(page.copy_buttons[0].isEnabled())
            page.copy_tasks.resolve(execute=True)
        self.assertTrue(page.copy_buttons[0].isEnabled())
        copied = QApplication.clipboard().text()
        self.assertIn('== Reporting service ==', copied)
        self.assert_clean(copied)


class ScrubTests(unittest.TestCase):
    """scrub() itself: every spelling the review found, no false masks of public values, and linear time."""

    def setUp(self):
        from lifetime_connection import scrub
        self.scrub = scrub
        self.code = issue('ef' * 32)
        self.plain = self.code[4:].replace('-', '')
        self.groups = [self.plain[i:i + 4] for i in range(0, 20, 4)]

    def assert_masked(self, text, *secrets):
        result = self.scrub(text)
        for secret in secrets:
            self.assertNotIn(secret.lower(), result.lower(), (text, result))
        return result

    def test_codes_in_every_grouping(self):
        g = self.groups
        for text in (self.code, self.code.lower(), self.plain, self.plain.lower(), ' '.join(g), ' '.join(g).lower(),
                     '\t'.join(g), '\n'.join(g), '  '.join(g), 'LTS  ' + '  '.join(g), 'LTS.' + '.'.join(g),
                     'LTS_' + '_'.join(g), 'lts ' + ' '.join(g), json.dumps({'code': self.code}),
                     'lifetime-connect-v1:https://srv.example:8443 ' + self.plain):
            with self.subTest(text=text):
                result = self.assert_masked('code ' + text + ' end', *g[1:])
                self.assertIn('LTS-****', result)
                self.assertTrue(result.startswith('code ') and result.endswith(' end'), result)
        self.assert_masked('typed LTS-' + g[0] + '-' + g[1][:2], g[0])         # a partial code after LTS

    def test_urls_keys_json_and_paths(self):
        cases = {
            'see https://hooks.slack.com?key=SECRETVALUE1234': 'SECRETVALUE1234',
            'GET https://hooks.example.com/services/T0/B0/XYZSECRET': 'XYZSECRET',
            'smtp://user:Pa55word@mail.example.com:587': 'Pa55word',
            'https://user:Pa55word@mail.example.com': 'Pa55word',
            '{"admin_token": "abc\\"def ghi"}': 'def ghi',
            '{\\"token\\": \\"supersecretvalue\\"}': 'supersecretvalue',
            'password is hunter2': 'hunter2',
            'pwd=hunter2': 'hunter2',
            'smtp_pass=hunter2': 'hunter2',
            'pass: hunter2': 'hunter2',
            'X-Lifetime-Token  :  s3cr3t': 's3cr3t',
            'token:\n  supersecret\nnext line': 'supersecret',
            'Serial Number: ABC123': 'ABC123',
            'Authorization: Bearer abc.def': 'abc.def',
            'lifetime-enrollment-v1:eyJhbGciOi.xyz+abc/def==': 'eyJhbGciOi',
            r'C:\Users\Jayden Phan\AppData\Local\Temp\LifetimeAgent-install.log': 'Jayden',
            r'{"path": "C:\\Users\\Jayden Phan\\x"}': 'Jayden',
            '/home/jayden/.local/state/lifetime-agent': 'jayden',
            'secret value ' + 'Ab3_' * 8: 'Ab3_Ab3_',
        }
        for text, secret in cases.items():
            with self.subTest(text=text):
                self.assert_masked(text, secret)
        self.assertIn('next line', self.scrub('token:\n  supersecret\nnext line'))
        self.assertIn('LifetimeAgent-install.log', self.scrub(r'C:\Users\Jayden Phan\AppData\Local\Temp\LifetimeAgent-install.log'))

    def test_public_values_stay(self):
        for text in ('fingerprint: ' + 'a' * 64, 'fingerprint: ' + 'AB' * 32, 'device_id: 3f2a9c1e-1111-4222-8333-444455556666',
                     'host DESK-TOP1-ABCD-EF12-3456', 'https://server.lan:8443', '2026-11-23T10:00:00+07:00',
                     'Lifetime 1.3.0 · Windows-11-10.0.26200-SP0', 'address: fe80::1234:5678:9abc:def0',
                     '[Passed] Port 8443 on srv is reachable.', 'The network search covers 192.168.1.0/24 (port 8443).'):
            with self.subTest(text=text):
                self.assertEqual(self.scrub(text), text)

    def test_long_input_is_fast(self):
        import time
        for text in ('a' * 256 * 1024, ('x' * 1000 + '\n') * 256, ('token ' * 40 + 'password\n') * 1000,
                     (('ABCD ' * 200) + '\n') * 256, '-' * 256 * 1024):
            started = time.perf_counter()
            self.scrub(text)
            self.assertLess(time.perf_counter() - started, 1.0, text[:40])
        result = self.scrub('start ' + 'Q' * 300000 + ' end')
        self.assertNotIn('Q' * 32, result)          # a cut line never leaves a piece of a secret behind


class CommandOutputTests(unittest.TestCase):
    """PowerShell output that is not valid in the local code page (a network named "WLAN (Büro)")."""
    CHILD = "import sys; sys.stdout.buffer.write(b'rule|True|8443\\r\\nprofile|Wi-Fi (B\\x81ro)|Public\\r\\nprofile|WLAN (B\\xc3\\xbcro)|Private\\r\\n')"

    def child(self, command, script=None, **options):
        import subprocess
        import sys
        return subprocess.run([sys.executable, '-c', script or self.CHILD], **options)

    def desktop_child(self, command, **options):
        return self.child(command, self.CHILD.replace('rule|True|8443\\r\\n', '').replace('profile|', ''), **options)

    def test_undecodable_bytes_never_hide_the_firewall_rule(self):
        import sys
        import lifetime_server as server
        result = server._command([sys.executable, '-c', self.CHILD], None, 7)
        self.assertEqual(result[0], 0)
        self.assertIn('rule|True|8443', result[1])
        self.assertIn('WLAN (Büro)', result[1])
        checks = server.firewall_checks(8443, self.child, 'win32')
        self.assertEqual(checks[0]['text'], 'The Windows firewall rule LifetimeServer-Connect allows port {port} from the '
                                            'local network.')
        self.assertEqual(checks[1]['state'], 'danger')          # Wi-Fi (B?ro) is Public

    def test_unreadable_output_is_could_not_read_not_missing(self):
        from types import SimpleNamespace
        import lifetime_server as server
        checks = server.firewall_checks(8443, lambda command, **options: SimpleNamespace(returncode=0, stdout=None), 'win32')
        self.assertEqual([check['state'] for check in checks], ['neutral'])
        self.assertIsNone(connect.windows_profiles(lambda command, **options: SimpleNamespace(returncode=0, stdout=None)))

    def test_the_desktop_reads_non_ascii_network_names(self):
        profiles = connect.windows_profiles(self.desktop_child)
        self.assertIn(('WLAN (Büro)', 'Private'), profiles)
        self.assertEqual(profiles[0][1], 'Public')
        seen = []
        connect.windows_profiles(lambda command, **options: seen.append(command) or self.desktop_child(command, **options))
        import lifetime_server as server
        for script in (seen[0][-1], server.WINDOWS_FIREWALL):      # PowerShell is told to write UTF-8
            self.assertTrue(script.startswith('[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); '))


class DiscoveredServersTests(unittest.TestCase):
    """Only a code typed and several servers answered: the one whose certificate matches the code is checked."""
    setUpClass = classmethod(DiagnoseTests.setUpClass.__func__)         # the same loopback server, not its tests
    tearDownClass = classmethod(DiagnoseTests.tearDownClass.__func__)
    setUp, diagnose = DiagnoseTests.setUp, DiagnoseTests.diagnose

    def answer(self, name, port):
        return dict(instance=name, host='localhost', port=port, source='127.0.0.1',
                    address=f'https://localhost:{port}', fingerprint_prefix='0' * 10)

    def test_the_matching_server_is_chosen_not_the_first(self):
        code = issue(self.runtime.fingerprint)
        answers = [self.answer('Lifetime-OTHER', free_port()), self.answer('Lifetime-RIGHT', self.port)]
        with patch('lifetime_discovery.discover', return_value=answers):
            checks = self.diagnose(code)
        found = states(checks)
        self.assertEqual(found['Several Lifetime Servers answered. {name} matches the code.'], 'info')
        self.assertIn('Lifetime-RIGHT matches the code', words(checks))
        self.assertEqual(found['The server certificate matches the code.'], 'good')
        self.assertNotIn("This server's certificate does not match the code.", found)

    def test_when_none_matches_it_says_several_answered(self):
        answers = [self.answer('Lifetime-A', self.port), self.answer('Lifetime-B', self.port)]
        with patch('lifetime_discovery.discover', return_value=answers):
            checks = self.diagnose(issue('ab' * 32))
        found = states(checks)
        self.assertEqual(found['Several Lifetime Servers answered and none has a certificate that matches the code. '
                               'Checking the first one, {name}.'], 'warning')
        self.assertEqual(found["This server's certificate does not match the code."], 'danger')

    def test_a_forged_name_cannot_add_lines(self):
        evil = self.answer('Evil\n[Passed] Everything is fine\x1b[0m' + 'x' * 200, free_port())
        with patch('lifetime_discovery.discover', return_value=[evil]):
            checks = self.diagnose(issue('ab' * 32))
        report = connect.diagnosis_report(checks, lambda key, **values: key.format(**values))
        self.assertNotIn('\n[Passed] Everything', report)
        self.assertNotIn('\x1b', report)
        line = next(check for check in checks if check['text'].startswith('A Lifetime Server answered'))
        self.assertLessEqual(len(line['values']['servers'].split(' (')[0]), 63)
        from lifetime_connection import check_lines
        lines = check_lines([dict(state='good', text='{x}', values=dict(x='a\nb'), fix=None)], lambda key, **v: key.format(**v))
        self.assertEqual(lines, ['[Passed] ab'])


class SelfCheckLimitsTests(unittest.TestCase):
    """Self check ends within its budget, runs once at a time and is reused for 30 s."""

    def setUp(self):
        import sys
        import lifetime_permissions as permissions
        import lifetime_server as server
        self.server = server
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        if sys.platform == 'win32':
            self.enterContext(patch.object(permissions, 'windows_acl'))     # non-elevated Windows fixtures only
        self.calls = []
        self.enterContext(patch.object(server, 'firewall_checks', side_effect=self.firewall))
        self.runtime = server.ServerRuntime(temporary.name, address='https://localhost:8443', host='127.0.0.1', port=0).start()
        self.addCleanup(self.runtime.stop)

    def firewall(self, port, deadline=None, **options):
        import time
        self.calls.append(deadline - time.monotonic())
        time.sleep(0.2)
        return []

    def test_one_run_at_a_time_and_reused_for_30_seconds(self):
        import threading
        import time
        threads = [threading.Thread(target=self.runtime.self_check) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.calls), 1)
        self.assertLessEqual(self.calls[0], self.server.SELF_CHECK_BUDGET)
        self.runtime.self_check()
        self.assertEqual(len(self.calls), 1)
        self.runtime._check_cache = (time.monotonic() - 31, self.runtime._check_cache[1])
        self.runtime.self_check()
        self.assertEqual(len(self.calls), 2)

    def test_a_hanging_name_lookup_is_reported_not_waited_for(self):
        import time
        real = socket.getaddrinfo

        def slow(host, *args, **kwargs):
            if host == 'srv.example':
                time.sleep(5)
            return real(host, *args, **kwargs)
        with patch.object(self.runtime, '_hostname', 'srv.example'), patch('socket.getaddrinfo', slow):
            started = time.monotonic()
            checks = self.runtime._self_check(time.monotonic() + self.server.SELF_CHECK_BUDGET)['checks']
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(states(checks)['{host} did not resolve within 2 s on this server.'], 'warning')


class SelfCheckProblemTests(unittest.TestCase):
    def test_only_an_unknown_operation_says_the_server_is_too_old(self):
        import lifetime_server_gui as gui
        from lifetime_admin import NotAllowed, _failed
        self.assertEqual(gui.self_check_problem(_failed(404)), 'unsupported')
        self.assertEqual(gui.self_check_problem(_failed(500)), 'failed')
        self.assertEqual(gui.self_check_problem(_failed(403)), 'denied')
        self.assertIsInstance(_failed(403), NotAllowed)
        self.assertEqual(gui.self_check_problem(TimeoutError()), 'timeout')
        self.assertEqual(gui.self_check_problem(socket.timeout()), 'timeout')
        self.assertEqual(gui.self_check_problem(ConnectionRefusedError()), 'failed')
        for cause, fix in gui.SELF_CHECK_PROBLEMS.values():
            for key in (cause, fix):
                self.assertEqual(gui.text(key, 'en'), key)
                self.assertNotEqual(gui.text(key, 'vi'), key)

    def test_firewall_commands_stop_at_the_deadline(self):
        import time
        from types import SimpleNamespace
        import lifetime_server as server
        commands = []

        def run(command, **options):
            commands.append((command, options['timeout']))
            return SimpleNamespace(returncode=0, stdout='active\n')
        for platform in ('win32', 'linux'):
            checks = server.firewall_checks(8443, run, platform, 'missing.conf', deadline=time.monotonic() + 0.1)
            self.assertEqual(checks[-1]['text'], server.OUT_OF_TIME)
        self.assertEqual(commands, [])
        server.firewall_checks(8443, run, 'linux', 'missing.conf', deadline=time.monotonic() + 3)
        self.assertTrue(commands and all(timeout <= 3 for command, timeout in commands))


class FirewallRuleTests(FirewallTests):
    """Rules ufw and firewalld show in more than one way; what Lifetime cannot see is Check, never Problem."""
    test_windows_rule_and_profiles = test_linux_ufw = test_linux_firewalld_and_no_firewall = None

    def ufw(self, status):
        import os
        with tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False) as conf:
            conf.write('ENABLED=yes\n')
        self.addCleanup(os.unlink, conf.name)
        return self.check('linux', {'is-active ufw': (0, 'active\n'), 'ufw status': (0, 'Status: active\n' + status)},
                          conf=conf.name)

    def test_ufw_default_allow_anywhere_and_ranges(self):
        for status in ('Default: allow (incoming), allow (outgoing), disabled (routed)\n',
                       'To                         Action      From\nAnywhere                   ALLOW IN    192.168.1.0/24\n',
                       '8000:9000/tcp              ALLOW IN    Anywhere\n5300:5400/udp              ALLOW IN    Anywhere\n',
                       '8443                       ALLOW IN    Anywhere\n5353 (v6)                  ALLOW IN    Anywhere (v6)\n'):
            with self.subTest(status=status):
                self.assertEqual([check['state'] for check in self.ufw(status)], ['good', 'good'])
        closed = self.ufw('Default: deny (incoming), allow (outgoing)\n22/tcp                     ALLOW IN    Anywhere\n')
        self.assertEqual([check['state'] for check in closed], ['warning', 'warning'])
        self.assertIn('Check with: sudo ufw status verbose', words(closed))

    def test_firewalld_zones_ranges_and_targets(self):
        def zones(text):
            return self.check('linux', {'is-active firewalld': (0, 'active\n'), 'firewall-cmd': (0, text)})
        work = 'public (default)\n  services: ssh\n  ports: \n\nwork (active)\n  services: mdns\n  ports: 8000-9000/tcp\n'
        self.assertEqual([check['state'] for check in zones(work)], ['good', 'good'])
        trusted = 'trusted (active)\n  target: ACCEPT\n  ports: \n'
        self.assertEqual([check['state'] for check in zones(trusted)], ['good', 'good'])
        inactive = 'public (default, active)\n  services: ssh\n  ports: \n\nwork\n  services: mdns\n  ports: 8443/tcp\n'
        closed = zones(inactive)
        self.assertEqual([check['state'] for check in closed], ['warning', 'warning'])
        self.assertIn('Check with: sudo firewall-cmd --list-all-zones', words(closed))


if __name__ == '__main__':
    unittest.main()
