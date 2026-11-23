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


if __name__ == '__main__':
    unittest.main()
