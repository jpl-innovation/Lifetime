"""Agentless checks: result matrix, network policy, concurrency and backpressure, and incidents.

Everything runs against servers on 127.0.0.1 that each test explicitly allows (Policy(['127.0.0.0/8'])); name lookups
that must fail are patched, so nothing reaches the internet.
"""
import asyncio
import datetime as dt
import http.server
from pathlib import Path
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import lifetime_checks as checks
import lifetime_permissions as permissions
from test_monitor import MonitorCase

LOCAL = checks.Policy(['127.0.0.0/8'])


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *arguments):
        pass

    def do_HEAD(self):
        self.do_GET(body=False)

    def do_GET(self, body=True):
        self.server.seen.append(dict(self.headers))
        port = self.server.server_port
        routes = {'/ok': (200, b'hello lifetime'), '/missing': (404, b'no'), '/big': (200, b'x' * (2 * 1024 * 1024)),
                  '/hop': (302, '/ok'), '/loop': (302, '/loop'), '/to-metadata': (302, 'http://169.254.169.254/latest'),
                  '/to-userinfo': (302, f'http://user:secret@127.0.0.1:{port}/ok')}
        if self.path == '/slow':
            time.sleep(3)
            routes['/slow'] = (200, b'late')
        status, value = routes.get(self.path, (404, b''))
        self.send_response(status)
        if 300 <= status < 400:
            self.send_header('Location', value)
            value = b''
        self.send_header('Content-Length', str(len(value)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(value)
            except OSError:
                pass        # the check stopped reading at its size limit


def serve(context=None):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.seen = []
    if context:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, kwargs=dict(poll_interval=0.05), daemon=True).start()
    return server


def closed_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def http_check(url, **values):
    return dict(checks.validate(dict(name='Web', kind='http', url=url, interval=60, timeout=5)), **values)


def run(check, policy=LOCAL):
    return asyncio.run(checks.attempt(check, policy))


class ResultMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = serve()
        cls.base = f'http://127.0.0.1:{cls.web.server_port}'
        cls.directory = tempfile.TemporaryDirectory()
        import lifetime_tls as tls
        with patch.object(permissions, 'windows_acl'):
            tls.generate_certificate(Path(cls.directory.name), 'https://127.0.0.1:8443')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(Path(cls.directory.name) / 'server-cert.pem', Path(cls.directory.name) / 'server-key.pem')
        cls.secure = serve(context)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.web, cls.secure):
            server.shutdown()
            server.server_close()
        cls.directory.cleanup()

    def category(self, check, policy=LOCAL):
        return run(check, policy)['category']

    def test_ok_with_status_latency_keyword_and_no_credentials(self):
        result = run(http_check(self.base + '/ok', keyword='lifetime'))
        self.assertEqual((result['ok'], result['category'], result['detail']), (True, 'ok', '200'))
        self.assertGreaterEqual(result['latency_ms'], 0)
        self.assertEqual(self.category(http_check(self.base + '/ok', method='HEAD')), 'ok')
        headers = self.web.seen[-1]
        self.assertEqual(headers['User-Agent'], 'Lifetime-Check/1')
        self.assertFalse({'Authorization', 'Cookie', 'Proxy-Authorization'} & set(headers))

    def test_wrong_status_missing_keyword_and_oversize_body(self):
        self.assertEqual(run(http_check(self.base + '/missing'))['detail'], '404')
        self.assertEqual(self.category(http_check(self.base + '/missing')), 'status')
        self.assertEqual(self.category(http_check(self.base + '/ok', keyword='absent')), 'keyword')
        self.assertEqual(self.category(http_check(self.base + '/big', max_bytes=1024)), 'oversize')

    def test_redirects_are_followed_limited_and_checked_again(self):
        self.assertEqual(self.category(http_check(self.base + '/hop')), 'ok')
        self.assertEqual(run(http_check(self.base + '/loop', redirects=2, status_max=299))['detail'], '302')
        self.assertEqual(self.category(http_check(self.base + '/to-metadata')), 'blocked')
        self.assertEqual(self.category(http_check(self.base + '/to-userinfo')), 'blocked')
        # Allowed at the start, but the redirect target is outside the scopes: refused before connecting.
        self.assertEqual(self.category(http_check(self.base + '/hop'), checks.Policy(['private'])), 'blocked')

    def test_refused_timeout_and_dns_failure(self):
        port = closed_port()
        # Windows retries a refused loopback connection for about two seconds before giving up.
        self.assertEqual(self.category(dict(name='x', kind='tcp', host='127.0.0.1', port=port, timeout=5)), 'refused')
        self.assertEqual(self.category(http_check(f'http://127.0.0.1:{port}/')), 'refused')
        started = time.monotonic()
        self.assertEqual(self.category(http_check(self.base + '/slow', timeout=1)), 'timeout')
        self.assertLess(time.monotonic() - started, 2.5)        # the deadline, not the slow server, decides
        real = socket.getaddrinfo

        def lookup(host, *arguments, **options):
            if host == 'printer.school.invalid':
                raise socket.gaierror(socket.EAI_NONAME, 'not found')
            return real(host, *arguments, **options)
        with patch.object(socket, 'getaddrinfo', side_effect=lookup):
            self.assertEqual(self.category(dict(name='x', kind='tcp', host='printer.school.invalid', port=80, timeout=2)), 'dns')
            self.assertEqual(self.category(http_check('http://printer.school.invalid/')), 'dns')

    def test_tls_is_verified_by_default(self):
        url = f'https://127.0.0.1:{self.secure.server_port}/ok'
        self.assertEqual(self.category(http_check(url)), 'tls')                 # self-signed: not trusted
        self.assertEqual(self.category(http_check(url, verify_tls=False)), 'ok')    # the administrator's choice
        self.assertEqual(self.category(http_check(f'https://127.0.0.1:{self.web.server_port}/ok')), 'tls')

    def test_tcp_ok_and_the_admin_port_is_never_reached(self):
        check = dict(name='x', kind='tcp', host='127.0.0.1', port=self.web.server_port, timeout=2)
        self.assertEqual(self.category(check), 'ok')
        self.assertEqual(self.category(check, checks.Policy(['127.0.0.0/8'], {self.web.server_port})), 'blocked')
        self.assertEqual(self.category(check, checks.Policy(['private', 'public'])), 'blocked')     # loopback by default

    @unittest.skipUnless(shutil.which('ping'), 'no ping command on this system')
    def test_ping_loopback_with_the_system_command(self):
        self.assertEqual(self.category(dict(name='x', kind='ping', host='127.0.0.1', timeout=3)), 'ok')
        self.assertEqual(self.category(dict(name='x', kind='ping', host='127.0.0.1', timeout=3), checks.Policy()), 'blocked')


class PolicyAndValidation(unittest.TestCase):
    def test_scopes_and_always_blocked_addresses(self):
        lan = checks.Policy(['private'])
        self.assertIsNone(lan.refuse('10.1.2.3'))
        self.assertIsNone(lan.refuse('192.168.1.20'))
        self.assertEqual(lan.refuse('8.8.8.8'), 'outside-scopes')
        self.assertIsNone(checks.Policy(['public']).refuse('8.8.8.8'))
        self.assertIsNone(checks.Policy(['192.168.5.0/24']).refuse('192.168.5.9'))
        self.assertEqual(checks.Policy(['192.168.5.0/24']).refuse('192.168.6.9'), 'outside-scopes')
        everything = checks.Policy(['private', 'public', '0.0.0.0/0', '::/0', '169.254.0.0/16'])
        for address in ('169.254.169.254', 'fd00:ec2::254', '0.0.0.0', '224.0.0.1', '255.255.255.255', '::'):
            self.assertEqual(everything.refuse(address), 'always-blocked', address)
        for address in ('127.0.0.1', '::1', '::ffff:127.0.0.1', '169.254.10.1', 'fe80::1'):
            self.assertEqual(lan.refuse(address), 'loopback-or-link-local', address)
        self.assertIsNone(checks.Policy(['127.0.0.0/8']).refuse('127.0.0.1', 80))
        self.assertEqual(checks.Policy(['127.0.0.0/8'], {8123}).refuse('127.0.0.1', 8123), 'admin-port')
        self.assertEqual(checks.validate_scopes(['Private', '10.0.0.1/8', 'private']), ['private', '10.0.0.0/8'])
        for scopes in (['lan'], ['10.0.0.0/33'], 'private', ['x'] * 65):
            with self.assertRaises(checks.CheckError, msg=scopes):
                checks.validate_scopes(scopes)

    def test_checks_are_validated(self):
        good = checks.validate(dict(name=' Printer ', kind='tcp', host='Printer.School.local', port=9100))
        self.assertEqual((good['name'], good['host'], good['port'], good['interval']), ('Printer', 'printer.school.local', 9100, 60))
        self.assertEqual(checks.validate(dict(name='NAS', kind='ping', host='[fd00::5]'))['host'], 'fd00::5')
        for check in (dict(name='x', kind='http', url='http://user:pw@nas.local/'), dict(name='x', kind='http', url='ftp://nas/'),
                      dict(name='x', kind='http', url='https://nas/', method='HEAD', keyword='ok'),
                      dict(name='x', kind='http', url='https://nas/', method='POST'),
                      dict(name='x', kind='http', url='https://nas/', status_min=400, status_max=200),
                      dict(name='x', kind='tcp', host='-oProxyCommand=x', port=22), dict(name='x', kind='tcp', host='a b'),
                      dict(name='x', kind='ping', host='nas', interval=10), dict(name='x', kind='ping', host='nas', timeout=60),
                      dict(name='x', kind='ping', host='nas', cookie='a'), dict(name='', kind='ping', host='nas'),
                      dict(name='x', kind='smtp', host='nas')):
            with self.assertRaises(checks.CheckError, msg=check):
                checks.validate(check)

    def test_ping_uses_a_fixed_argument_list(self):
        with patch.object(sys, 'platform', 'win32'):
            self.assertEqual(checks.ping_command('192.168.1.5', 3), ['ping', '-n', '1', '-w', '3000', '192.168.1.5'])
        with patch.object(sys, 'platform', 'linux'):
            self.assertEqual(checks.ping_command('192.168.1.5', 3), ['ping', '-c', '1', '-W', '3', '192.168.1.5'])
        with self.assertRaises(ValueError):
            checks.ping_command('-c 9 host', 3)


class Concurrency(unittest.TestCase):
    def test_50_targets_5_checks_each_with_slow_ones_stay_within_the_cap_and_queue(self):
        # 45 TCP targets that answer at once and 5 slow HTTP targets that accept but never reply (10 %).
        listeners = []
        for _ in range(50):
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(64)
            listeners.append(listener)
        self.addCleanup(lambda: [listener.close() for listener in listeners])
        configured = {}
        for number, listener in enumerate(listeners):
            port = listener.getsockname()[1]
            if number % 10 == 9:
                configured[number] = dict(name=f'slow {number}', kind='http', url=f'http://127.0.0.1:{port}/', method='GET',
                                          status_min=200, status_max=399, keyword='', verify_tls=True, redirects=0,
                                          max_bytes=1024, interval=0.2, timeout=1, group='', enabled=True)
            else:
                configured[number] = dict(name=f'fast {number}', kind='tcp', host='127.0.0.1', port=port, interval=0.2,
                                          timeout=1, group='', enabled=True)
        checker = checks.Checker(LOCAL, concurrency=10, queue_size=20, spacing=0.0)
        checker.configure(configured)
        checker.start()
        self.addCleanup(checker.stop)
        results, deepest = {key: [] for key in configured}, 0
        deadline = time.monotonic() + 40
        while min(len(items) for items in results.values()) < 5 and time.monotonic() < deadline:
            deepest = max(deepest, checker.results.qsize())
            for result in checker.drain(limit=15):          # a slower consumer than producer: backpressure
                results[result['check_id']].append(result)
            time.sleep(0.05)
        checker.stop()
        self.assertTrue(all(len(items) >= 5 for items in results.values()), {k: len(v) for k, v in results.items()})
        self.assertLessEqual(checker.peak, 10)
        self.assertLessEqual(deepest, 20)
        for key, items in results.items():
            expected = 'timeout' if key % 10 == 9 else 'ok'
            self.assertEqual({item['category'] for item in items}, {expected}, key)

    def test_the_same_target_is_spaced_and_a_full_queue_pauses_scheduling(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(64)
            port = listener.getsockname()[1]
            check = dict(name='x', kind='tcp', host='127.0.0.1', port=port, interval=0.01, timeout=1, group='', enabled=True)
            checker = checks.Checker(LOCAL, concurrency=4, queue_size=3, spacing=0.3)
            checker.configure({1: check, 2: dict(check, name='y')})      # two checks, one target
            checker.start()
            time.sleep(1.0)
            checker.stop()
        self.assertEqual(checker.results.qsize(), 3)        # full, and nothing was lost to an exception
        stamps = [item['at'] for item in checker.drain()]
        self.assertEqual(len(stamps), 3)


class FakeChecker:
    def __init__(self):
        self.pending, self.configured = [], None

    def configure(self, configured, policy=None):
        self.configured, self.policy = configured, policy

    def drain(self, limit=2000):
        items, self.pending = self.pending, []
        return items


class IncidentsFromChecks(MonitorCase):
    def result(self, ok, category='ok'):
        return dict(check_id=1, ok=ok, category=category, latency_ms=3.0 if ok else None, detail='',
                    at=self.clock.now.isoformat())

    def test_n_failures_open_one_incident_and_a_success_resolves_it(self):
        fake = self.monitor.attach_checker(FakeChecker())
        self.fleet.add()        # a computer in the same pass: check rules never apply to it
        self.monitor.save_check(dict(name='Front printer', kind='tcp', host='192.168.1.50', port=9100, group='Office'))
        self.assertEqual(fake.configured[1]['host'], '192.168.1.50')
        self.assertEqual(fake.policy.refuse('192.168.1.50'), None)
        self.monitor.set_check_scopes(['192.168.1.0/24'])
        self.assertEqual(fake.policy.refuse('10.0.0.1'), 'outside-scopes')
        for minute in range(5):
            fake.pending.append(self.result(False, 'timeout'))
            self.clock.advance(minutes=1)
            self.monitor.tick()
            if minute == 1:
                self.assertEqual(self.monitor.incidents('open'), [])     # two failures: not yet
        incidents = self.monitor.incidents('active')
        self.assertEqual([(row['device_name'], row['group_name'], row['rule'], row['state']) for row in incidents],
                         [('Front printer', 'Office', 'check_down', 'open')])
        self.assertEqual(len(self.monitor.incidents()), 1)      # five failures, one incident
        listed = self.monitor.checks()[0]
        self.assertEqual((listed['failures'], listed['last_category']), (5, 'timeout'))
        fake.pending.append(self.result(True))
        self.clock.advance(minutes=1)
        self.monitor.tick()
        self.assertEqual(self.monitor.checks()[0]['last_latency_ms'], 3.0)
        for _ in range(6):
            fake.pending.append(self.result(True))
            self.clock.advance(minutes=1)
            self.monitor.tick()
        self.assertEqual(self.monitor.incidents()[0]['state'], 'resolved')
        self.assertEqual(self.monitor.incidents(device_id='00000000-0000-0000-0000-000000000001'), [])
        self.monitor.delete_check(1)
        self.assertEqual((self.monitor.checks(), fake.configured), ([], {}))
        with self.assertRaises(Exception):
            self.monitor.save_check(dict(name='x', kind='http', url='http://user:pw@nas/'))


if __name__ == '__main__':
    unittest.main()
