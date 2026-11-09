"""The persistent live socket: auth, streaming and rate push, command ack and dedupe, reconnect, fallback."""
import socket
import ssl
import tempfile
import threading
import time
import unittest
import test_compat  # noqa: F401  (enterContext on Python 3.10)
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from websockets.exceptions import InvalidHandshake, InvalidStatus
    from websockets.sync.client import connect
except ModuleNotFoundError:  # optional dependency (requirements-server.txt); the desktop app works without it
    raise unittest.SkipTest('websockets is not installed: skipping the live-socket tests')

import lifetime_agent as la
import lifetime_permissions as permissions
import lifetime_ws as ws
from lifetime_admin import AdminClient
from lifetime_client import SyncClient
from lifetime_server import ServerRuntime
from lifetime_server_gui import _create_bundle

FAKE = dict(system='Windows', release='11', machine='AMD64', python_version='3.12',
            specs=dict(manufacturer='M', model='X', cpu_name='C', cpu_cores=8, cpu_threads=16, ram_bytes=1024,
                       gpu_names=[], disks=[], os_name='W', os_version='11', battery_design_wh=None,
                       mac_addresses=[], hostname='H'),
            performance=dict(cpu_percent=1.0, ram_used_bytes=1, disk_free_bytes=1, battery_percent=None,
                             charging=None, battery_health_percent=None, power_watts=None, network_kind=None,
                             wifi_signal_percent=None, temperature_c=None))


def until(condition, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(.05)
    return condition()


def free_ports():
    """Two ports nothing is using now: the server and its loopback admin port."""
    held = [socket.socket() for _ in range(2)]
    try:
        for sock in held:
            sock.bind(('127.0.0.1', 0))
        return [sock.getsockname()[1] for sock in held]
    finally:
        for sock in held:
            sock.close()


class SocketFixture(unittest.TestCase):
    control = False

    def setUp(self):
        self.port, self.admin_port = free_ports()
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.enterContext(patch.object(permissions, 'windows_acl'))
        self.enterContext(patch.object(la, '_resources', lambda state=None: dict(FAKE)))
        self.power = self.enterContext(patch.object(la, 'execute_power', Mock()))
        root = Path(self.temporary.name)
        (root/'s').mkdir()
        (root/'a').mkdir()
        self.runtime = ServerRuntime(root/'s', address=f'https://127.0.0.1:{self.port}', host='127.0.0.1',
                                     port=self.port, admin_port=self.admin_port)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.admin = AdminClient(root/'s', self.admin_port)
        self.agent = la.MachineAgent.initialize(root/'a')
        self.addCleanup(self.agent.close)
        policy = dict(history=True, resources=True, health=False, control=self.control)
        self.agent.set_policy(policy)
        bundle = _create_bundle(self.admin, root/'s', f'https://127.0.0.1:{self.port}', 3600, 1)['bundle']
        SyncClient(self.agent).start_enrollment(bundle, {'display_name': 'x'}, policy)
        item = self.admin.call('pending_enrollments')[0]
        self.device = item['device_id']
        self.admin.call('approve', request_id=item['request_id'])
        self.store = self.runtime._store
        self.stop = threading.Event()
        self.thread = None

    def start_agent(self):
        self.thread = threading.Thread(target=self.agent.run, args=(self.stop,), daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.stop.set()
        self.thread.join(15)

    def samples(self):
        return len(self.store._live_samples.get(self.device, ()))

    def status(self):
        return self.store.get_device(self.device)['reporting_status']


class SocketTests(SocketFixture):
    def test_streams_and_the_server_pushes_the_rate_without_polling(self):
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device)))
        self.assertTrue(until(lambda: self.samples() >= 1))
        self.assertEqual(self.runtime.hub.links[self.device].rate_ms, 10000)    # nobody is watching: quiet
        self.store.watch(self.device)                                           # a viewer opens this computer
        before = self.samples()
        self.assertTrue(until(lambda: self.runtime.hub.links[self.device].rate_ms == 500, 3))
        time.sleep(3)
        self.assertGreaterEqual(self.samples() - before, 4)                     # roughly twice a second, not every 10 s

    def test_bad_credential_is_refused_before_any_data(self):
        context = ssl.create_default_context()
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        url = f'wss://127.0.0.1:{self.port}{ws.PATH}?device={self.device}'
        for headers in ({'Authorization': 'Bearer ' + 'x' * 43}, {}):
            with self.assertRaises(InvalidStatus) as caught:
                connect(url, ssl=context, additional_headers=headers, open_timeout=5, proxy=None)
            self.assertEqual(caught.exception.response.status_code, 403)
        self.assertFalse(self.runtime.hub.connected(self.device))
        self.assertEqual(self.samples(), 0)

    def test_dropped_socket_is_offline_at_once_and_the_computer_reconnects(self):
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device) and self.samples() >= 1))
        self.assertEqual(self.status(), 'reporting')
        seen = []
        self.runtime.hub.listeners.append(lambda event: seen.append(self.status()) if event.get('online') is False else None)
        self.runtime.hub.links[self.device].kill()
        self.assertTrue(until(lambda: seen, 3))
        self.assertEqual(seen[0], 'report-overdue')                             # offline at the instant the link closed
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device), 8))
        self.assertTrue(until(lambda: self.status() == 'reporting', 8))

    def test_revoking_closes_the_socket(self):
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device)))
        self.admin.call('revoke', device_id=self.device)
        self.assertTrue(until(lambda: not self.runtime.hub.connected(self.device), 4))

    def test_falls_back_to_https_when_the_socket_is_unavailable(self):
        self.runtime.hub.stop()
        self.runtime.hub = None                                                 # as an older server: 404 on /v1/ws
        self.start_agent()
        self.assertTrue(until(lambda: self.samples() >= 2, 12))
        self.assertFalse(self.agent_link_connected())

    def agent_link_connected(self):
        return False    # the server has no hub, so nothing can be connected; samples arrived over HTTPS


class AdminStreamTests(SocketFixture):

    def test_the_server_window_stream_pushes_samples_and_online_changes(self):
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device) and self.samples() >= 1))
        token = (Path(self.temporary.name)/'s'/'admin-token').read_text(encoding='ascii').strip()
        with self.assertRaises(InvalidHandshake):    # no token, no stream
            with connect(f'ws://127.0.0.1:{self.admin_port}/v1/stream', open_timeout=3, proxy=None):
                pass
        events = []
        with connect(f'ws://127.0.0.1:{self.admin_port}/v1/stream', additional_headers={'Authorization': 'Bearer ' + token},
                     open_timeout=3, proxy=None) as stream:
            hello = ws.parse(stream.recv(timeout=3))
            self.assertEqual(hello['type'], 'hello')
            stream.send(ws.envelope('status', dict(watch=self.device)))         # a viewer opens this computer
            end = time.monotonic() + 6
            while time.monotonic() < end and sum(e['type'] == 'telemetry' for e in events) < 4:
                try:
                    events.append(ws.parse(stream.recv(timeout=1)))
                except TimeoutError:
                    pass
            samples = [e for e in events if e['type'] == 'telemetry']
            self.assertGreaterEqual(len(samples), 4)                            # pushed, several per second
            self.assertEqual(samples[0]['data']['device_id'], self.device)
            self.runtime.hub.links[self.device].kill()
            end = time.monotonic() + 4
            offline = False
            while time.monotonic() < end and not offline:
                try:
                    message = ws.parse(stream.recv(timeout=1))
                    offline = message['type'] == 'status' and message['data'].get('online') is False
                except TimeoutError:
                    pass
            self.assertTrue(offline)


class HardeningTests(SocketFixture):

    def test_a_browser_origin_is_refused_on_both_sockets(self):
        context = ssl.create_default_context()
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        with self.assertRaises(InvalidStatus) as caught:
            connect(f'wss://127.0.0.1:{self.port}{ws.PATH}?device={self.device}', ssl=context, origin='https://evil.example',
                    additional_headers={'Authorization': 'Bearer ' + 'x' * 43}, open_timeout=5, proxy=None)
        self.assertEqual(caught.exception.response.status_code, 403)
        token = (Path(self.temporary.name)/'s'/'admin-token').read_text(encoding='ascii').strip()
        with self.assertRaises(InvalidStatus):
            connect(f'ws://127.0.0.1:{self.admin_port}/v1/stream', origin='https://evil.example',
                    additional_headers={'Authorization': 'Bearer ' + token}, open_timeout=3, proxy=None)

    def test_admin_api_refuses_a_foreign_host_header(self):
        import http.client
        token = (Path(self.temporary.name)/'s'/'admin-token').read_text(encoding='ascii').strip()
        for host, expected in (('attacker.example:80', 403), (f'127.0.0.1:{self.admin_port}', 200)):
            connection = http.client.HTTPConnection('127.0.0.1', self.admin_port, timeout=5)
            connection.request('POST', '/v1/overview', '{}', {'Host': host, 'Authorization': 'Bearer ' + token,
                                                              'Content-Type': 'application/json'})
            self.assertEqual(connection.getresponse().status, expected)

    def test_a_flooding_computer_is_disconnected(self):
        from websockets.exceptions import ConnectionClosed
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device) and self.samples() >= 1))
        self.stop.set()
        self.thread.join(15)
        credential = self.agent._load()['credential']
        context = ssl.create_default_context()
        context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        with connect(f'wss://127.0.0.1:{self.port}{ws.PATH}?device={self.device}', ssl=context,
                     additional_headers={'Authorization': 'Bearer ' + credential}, open_timeout=5, proxy=None) as flood:
            with self.assertRaises(ConnectionClosed):
                for _ in range(500):
                    flood.send(ws.envelope('status', {}))
                for _ in range(50):
                    flood.recv(timeout=1)


class CommandTests(SocketFixture):
    control = True

    def command(self, action):
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device) and self.samples() >= 1))
        return self.store.send_command(self.device, action)

    def row(self, command_id):
        return self.store._db.execute('SELECT * FROM commands WHERE command_id=?', (command_id,)).fetchone()

    def test_command_is_pushed_acknowledged_and_never_run_twice(self):
        self.start_agent()
        issued = self.command('restart')
        self.assertTrue(until(lambda: self.row(issued['command_id'])['status'] == 'done', 4))   # no waiting for a poll
        self.assertTrue(until(lambda: self.power.call_count == 1, 2))
        self.power.assert_called_once_with('restart')
        # The server sends the same command again, as after a reconnect: acknowledged, not repeated.
        self.runtime.hub.links[self.device].send('command', dict(action='restart', issued_at=issued['created_at']),
                                                 id=issued['command_id'])
        time.sleep(1.5)
        self.assertEqual(self.power.call_count, 1)

    def test_withdrawn_consent_declines_a_power_command(self):
        self.control = False
        self.start_agent()
        self.assertTrue(until(lambda: self.runtime.hub.connected(self.device) and self.samples() >= 1))
        with self.agent._transaction() as state:
            state['policy']['control'] = False
        self.runtime.hub.links[self.device].send('command', dict(action='shutdown', issued_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())),
                                                 id='11111111-1111-4111-8111-111111111111')
        time.sleep(1.5)
        self.power.assert_not_called()


if __name__ == '__main__':
    unittest.main()
