"""Pure parser tests use embedded OS output; no probes or real ledger writes."""
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lifetime_signals as s


class ParsingTests(unittest.TestCase):
    def test_netsh_connected(self):
        output = '''There is 1 interface on the system:
    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi
    State                  : connected
    SSID                   : Studio: upstairs
    BSSID                  : 00:11:22:33:44:55
    Band                   : 5 GHz
    Channel                : 149
    Receive rate (Mbps)    : 866.7
    Transmit rate (Mbps)   : 780
    Signal                 : 92%
'''
        wifi = s.parse_netsh(output)[0]
        self.assertEqual(wifi['ssid'], 'Studio: upstairs')
        self.assertEqual(wifi['signal'], 92)
        self.assertEqual(wifi['band'], '5 GHz')
        self.assertEqual(wifi['channel'], 149)
        self.assertEqual(wifi['rx_mbps'], 866.7)
        self.assertEqual(wifi['tx_mbps'], 780)

    def test_netsh_disconnected_and_partial(self):
        wifi = s.parse_netsh('Name : Wi-Fi\nState : disconnected\nSignal : 0%')[0]
        self.assertIsNone(wifi['ssid'])
        self.assertIsNone(wifi['signal'])
        partial = s.parse_netsh('Name : Wi-Fi\nState : connected\nSSID : Network')[0]
        self.assertEqual(partial['ssid'], 'Network')
        self.assertIsNone(partial.get('rx_mbps'))
        self.assertEqual(s.parse_netsh('The Wireless AutoConfig Service is not running.'), [])
        self.assertEqual(s.parse_netsh(None), [])

    def test_multiple_wifi_interfaces(self):
        rows = s.parse_netsh('Name : Wi-Fi\nState : disconnected\nName : Wi-Fi 2\nState : connected\nSSID : Office')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['ssid'], 'Office')

    def test_nvidia(self):
        gpu = s.parse_nvidia('NVIDIA GeForce RTX 4060, 18.65, 47, 12, 1234, 8188')[0]
        self.assertEqual(gpu['name'], 'NVIDIA GeForce RTX 4060')
        self.assertEqual(gpu['watts'], 18.65)
        self.assertEqual(gpu['memory_total'], 8188)
        self.assertEqual(gpu['utilization'], 12)

    def test_gpu_unsupported_fields(self):
        gpu = s.parse_nvidia('NVIDIA GPU, [N/A], N/A, 0, 0, 8192')[0]
        self.assertIsNone(gpu['watts'])
        self.assertEqual(gpu['utilization'], 0)
        self.assertEqual(s.parse_nvidia('error'), [])
        self.assertEqual(s.parse_nvidia(None), [])

    def battery(self, charging=False):
        return {'status': [{'InstanceName': 'BAT0', 'Charging': charging,
                           'DischargeRate': 12500, 'ChargeRate': 23400,
                           'RemainingCapacity': 42000, 'Voltage': 11400}],
                'full': [{'InstanceName': 'BAT0', 'FullChargedCapacity': 50000}],
                'design': [{'InstanceName': 'BAT0', 'DesignedCapacity': 60000}]}

    def test_cim_units(self):
        b = s.parse_battery(json.dumps(self.battery()))
        self.assertEqual(b['watts'], 12.5)
        self.assertEqual(b['voltage'], 11.4)
        self.assertEqual(b['capacity_wh'], 42)
        self.assertEqual(b['full_wh'], 50)
        self.assertEqual(b['design_wh'], 60)
        self.assertAlmostEqual(b['wear'], 100/6)
        self.assertEqual(s.parse_battery(self.battery(True))['watts'], 23.4)

    def test_missing_design_omits_wear(self):
        b = self.battery()
        b['design'] = []
        self.assertIsNone(s.parse_battery(b)['wear'])
        b['status'][0]['DischargeRate'] = 4294967295
        self.assertIsNone(s.parse_battery(b)['watts'])
        self.assertTrue(all(value is None for value in s.parse_battery(None).values()))
        self.assertTrue(all(value is None for value in s.parse_battery('not json').values()))

    def test_adapter_filter(self):
        rows = [{'Name': 'Wi-Fi', 'Status': 'Up', 'LinkSpeed': '866.7 Mbps'},
                {'Name': 'vEthernet (Default Switch)', 'Status': 'Up'},
                {'Name': 'VMware Network Adapter', 'Status': 'Up'},
                {'Name': 'Loopback', 'Status': 'Up'}, {'Name': 'Ethernet', 'Status': 'Down'}]
        self.assertEqual([a['Name'] for a in s.parse_adapters(json.dumps(rows))], ['Wi-Fi'])
        self.assertEqual(s.parse_adapters('null'), [])

    def test_rates_ignore_reset_and_new_adapter(self):
        self.assertEqual(s.counter_rates({1: (100, 200)}, {1: (500, 400)}, 2), {'down': 200, 'up': 100})
        self.assertIsNone(s.counter_rates({1: (100, 200)}, {1: (1, 2)}, 2)['up'])
        self.assertIsNone(s.counter_rates({}, {1: (100, 200)}, 2)['down'])
        self.assertIsNone(s.counter_rates({1: (1, 2)}, {1: (2, 4)}, 0)['down'])

    def test_event_pairing_ignores_107(self):
        events = [dict(id=42, provider='Microsoft-Windows-Kernel-Power', time=200),
                  dict(id=107, provider='Microsoft-Windows-Kernel-Power', time=201),
                  dict(id=27, provider='Microsoft-Windows-Kernel-Boot', time=300),
                  dict(id=42, provider='Microsoft-Windows-Kernel-Power', time=400),
                  dict(id=27, provider='Microsoft-Windows-Kernel-Boot', time=450)]
        self.assertEqual(s.parse_event_awake(100, 600, list(reversed(events))), 350)
        self.assertIsNone(s.parse_event_awake(100, 600, events[:-1]))
        self.assertEqual(s.parse_event_awake(100, 600, []), 500)
        self.assertIsNone(s.parse_event_awake(None, 600, []))

    def test_cpu_deltas(self):
        self.assertEqual(s.cpu_percent((20, 100), (40, 200)), 80)
        self.assertIsNone(s.cpu_percent(None, (20, 100)))
        self.assertIsNone(s.cpu_percent((20, 100), (20, 100)))

    def test_commands_timeout_and_hide_console(self):
        with patch.object(s.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 5)) as run:
            self.assertIsNone(s.command(['probe']))
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            self.assertEqual(run.call_args.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with patch.object(s.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertIsNone(s.command(['missing']))

    def test_probe_exception_boundary(self):
        @s.safe
        def broken():
            raise OSError('unavailable')
        self.assertIsNone(broken())

    def test_linux_routes_do_not_call_windows_commands(self):
        with patch.object(s, 'sys', SimpleNamespace(platform='linux')), patch.object(s, 'powershell') as ps, \
                patch.object(s, 'linux_wifi', return_value=[]) as wifi, \
                patch.object(s, 'verify_linux_awake', return_value={'message': 'fixture'}) as verify:
            self.assertEqual(s.probe_wifi(), [])
            self.assertEqual(s.verify_awake()['message'], 'fixture')
            wifi.assert_called_once()
            verify.assert_called_once()
            ps.assert_not_called()
        with patch.object(s, 'sys', SimpleNamespace(platform='linux')), patch.object(s, 'command') as command:
            self.assertIsNone(s.powershell('local query'))
            command.assert_not_called()

    def test_linux_memory_parsing(self):
        sample = 'MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n'
        with patch.object(s, 'sys', SimpleNamespace(platform='linux')), patch.object(s.Path, 'read_text', return_value=sample):
            ram = s.probe_ram()
            self.assertEqual(ram['total'], 8*1024**3)
            self.assertEqual(ram['used'], 6*1024**3)
            self.assertEqual(ram['percent'], 75)


if __name__ == '__main__':
    unittest.main()
