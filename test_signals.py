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


class InventoryTests(unittest.TestCase):
    def test_grouped_specs_keep_values_separate_from_missing_reasons(self):
        from lifetime_system import sections
        translate = lambda key, **values: key.format(**values)
        grouped = dict(sections(dict(cpu={'temperature_c': 0}, fans=[],
                                    adapters=[dict(name='Ethernet', kind='Ethernet', connected=False,
                                                   vendor='Example vendor', mac='02:00:00:00:00:01')],
                                    displays=[dict(width=2560, height=1440)]), translate))
        processor = {label: (value, reason) for _, entries in grouped['Processor & cooling'] for label, value, reason in entries}
        self.assertEqual(processor['CPU temperature'], ('0 °C', ''))
        self.assertEqual(processor['Reported fans'][0], 'Not available')
        self.assertIn('firmware', processor['Reported fans'][1])
        network = {label: (value, reason) for _, entries in grouped['Network cards'] for label, value, reason in entries}
        self.assertEqual(network['Status'][0], 'Disconnected')
        self.assertEqual(network['Link speed'][0], 'Not available')
        self.assertTrue(network['Driver'][1])
        self.assertEqual(network['Manufacturer'][0], 'Example vendor')
        self.assertEqual(network['MAC address'][0], '02:00:00:00:00:01')
        display = {label: value for _, entries in grouped['Graphics & displays'] for label, value, _ in entries}
        self.assertEqual(display['Resolution'], '2560 × 1440 px')
        self.assertEqual(display['Refresh rate'], 'Not available')

    def test_missing_fields_are_explicit_and_thermal_zones_are_not_cpu(self):
        import lifetime_system as system
        rows = dict(system.rows(dict(fans=[], adapters=[dict(name='Wi-Fi', kind='Wi-Fi')]), lambda s: s))
        self.assertIn('Not available', rows['Cooling fans'])
        self.assertIn('Link speed: Not available', rows['Network cards'])
        self.assertIn('Driver: Not available', rows['Network cards'])
        scripts = []
        def ps(script):
            scripts.append(script)
            return '{"_unavailable":"Access denied by Windows"}' if 'try {' in script else None
        with patch.object(s, 'sys', SimpleNamespace(platform='win32')), \
             patch.object(s, 'powershell', side_effect=ps), patch.object(s, 'probe_counters', return_value=[]), \
             patch.object(s, 'windows_cpu_clocks', return_value={}), patch.object(s, 'windows_wifi', return_value=[]), \
             patch.object(s, 'probe_gpu', return_value=[]), patch.object(s, 'windows_displays', return_value=[]):
            result = s.probe_inventory()
        self.assertNotIn('temperature_c', result.get('cpu', {}))
        self.assertEqual(result['unavailable']['fans'], 'Access denied by Windows')
        self.assertFalse(any('MSAcpi_ThermalZoneTemperature' in script for script in scripts))
        system.validate(result)

    def test_strict_contract_and_local_caps(self):
        import lifetime_system as system
        from lifetime_protocol import validate_specs, ProtocolError
        good = dict(cpu=dict(temperature_c=65, current_mhz=2400), fans=[],
                    adapters=[dict(name='Ethernet', kind='Ethernet', connected=True, speed_mbps=1000)],
                    memory=dict(slots_used=2, modules=[dict(kind='DDR5', capacity_bytes=16*2**30)]))
        validate_specs(dict(hardware=good))
        for bad in [dict(cpu={'temperature_c': float('nan')}), dict(fans=[{}]*9),
                    dict(adapters=[{}]*17), dict(cpu={'current_mhz': True}),
                    dict(memory={'slots_used': 1.5}), dict(board={'serial': 'private'}),
                    dict(adapters=[dict(name='x'*161)]), dict(displays=[dict(width=40000)]),
                    dict(os={'name': 'bad\ntext'})]:
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                validate_specs(dict(hardware=bad))
        cleaned = system.clean(dict(fans=[dict(name='fan', rpm=1000, serial='private')]*20))
        self.assertEqual(len(cleaned['fans']), 8)
        self.assertNotIn('serial', cleaned['fans'][0])
        self.assertIn("Not reported by this computer's firmware", dict(system.rows({'fans': []}, lambda s: s))['Cooling fans'])
        self.assertIn('Not available', dict(system.rows({}, lambda s: s))['CPU temperature'])

    def test_windows_partial_domains_do_not_leak_private_fields(self):
        def ps(script):
            if 'Win32_Fan' in script:
                return '[]'
            if 'Win32_PhysicalMemory -' in script:
                return '{"slots_used":2,"slots_total":4,"modules":[{"kind":"DDR5","capacity_bytes":1024}]}'
            return None
        with patch.object(s, 'sys', SimpleNamespace(platform='win32')), patch.object(s, 'powershell', side_effect=ps), \
             patch.object(s, 'probe_counters', return_value=[]), patch.object(s, 'windows_cpu_clocks', return_value={}), \
             patch.object(s, 'windows_wifi', return_value=[]), patch.object(s, 'probe_gpu', return_value=[]), \
             patch.object(s, 'windows_displays', return_value=[dict(width=1920,height=1080,hz=60)]):
            result = s.probe_inventory()
        self.assertEqual(result['fans'], [])
        self.assertEqual(result['memory']['slots_used'], 2)
        self.assertEqual(result['displays'][0]['hz'], 60)
        self.assertNotIn('temperature_c', result.get('cpu', {}))

    def test_linux_hwmon_temperatures_and_fans(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hw = root/'sys/class/hwmon/hwmon0'
            hw.mkdir(parents=True)
            for key, value in [('name','coretemp'),('temp1_input','54000'),('fan1_input','1234')]:
                (hw/key).write_text(value)
            real = Path
            def path(value):
                return root/str(value).lstrip('/')
            with patch.object(s, 'Path', side_effect=path):
                result = s.linux_inventory()
            self.assertEqual(result['cpu']['temperature_c'], 54)
            self.assertEqual(result['fans'][0]['rpm'], 1234)


if __name__ == '__main__':
    unittest.main()
