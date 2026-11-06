"""Linux filesystem fixtures and Windows structured-output/API fixtures."""
import ctypes as C
import json
from pathlib import Path
import tempfile
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import lifetime_signals as s


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent/'fixtures/linux_signals.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, text in self.fixture['files'].items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_battery_rates_capacities_and_wear_multiple_packs(self):
        b = s.linux_battery(self.root/'power')
        self.assertEqual(b['watts'], 20)
        self.assertEqual(b['capacity_wh'], 50)
        self.assertEqual(b['full_wh'], 70)
        self.assertAlmostEqual(b['wear'], 100*(1-70/85))
        self.assertIsNone(b['voltage'])
        self.assertEqual(len(b['packs']), 2)
        p = s.linux_power(self.root/'power')
        self.assertAlmostEqual(p['percent'], 100*50/70)
        self.assertFalse(p['ac'])
        self.assertEqual(p['state'], 'Discharging')

    def test_no_battery_and_unreadable_fields(self):
        self.assertEqual(s.linux_power(self.root/'empty')['state'], 'No battery')
        (self.root/'power/BAT0/power_now').write_text('not supported')
        self.assertIsNone(s.linux_battery(self.root/'power')['watts'])
        (self.root/'power/BAT0/power_now').write_text('-10000000')
        (self.root/'power/BAT1/current_now').write_text('-1000000')
        self.assertEqual(s.linux_battery(self.root/'power')['watts'], 20)

    def test_cpu_and_ram_fixture_text(self):
        before = s.parse_proc_stat(self.fixture['stat_before'])
        after = s.parse_proc_stat(self.fixture['stat_after'])
        self.assertEqual(s.cpu_percent(before, after), 70)
        self.assertEqual(s.parse_meminfo(self.fixture['meminfo'])['percent'], 75)

    def test_network_counters_link_speed_and_no_wifi(self):
        adapters = s.linux_network(self.root/'net')
        self.assertEqual([r['Name'] for r in adapters], ['eth0', 'wlan0'])
        self.assertEqual(adapters[0]['LinkSpeed'], '1000 Mbps')
        self.assertIsNone(adapters[1]['LinkSpeed'])
        self.assertEqual(s.parse_net_dev(self.fixture['netdev']), {'eth0': (1234, 5678), 'wlan0': (300, 900)})
        with patch.object(s, 'command') as cmd:
            self.assertEqual(s.linux_wifi(self.root/'absent', self.root/'absent'), [])
            cmd.assert_not_called()

    def test_wifi_nmcli_and_iw(self):
        wireless = self.root/'wireless'
        wireless.write_text(self.fixture['wireless'])
        self.assertEqual(s.parse_wireless(self.fixture['wireless'])['wlan0'], 80)
        for tool, output in [('nmcli', self.fixture['nmcli']), ('iw', self.fixture['iw'])]:
            with self.subTest(tool=tool), patch.object(s.shutil, 'which', side_effect=lambda name: name if name == tool else None), \
                    patch.object(s, 'command', return_value=output) as cmd:
                row = s.linux_wifi(self.root/'net', wireless)[0]
                self.assertEqual(row['ssid'], 'Office: Upstairs')
                self.assertEqual(row['signal'], 80)
                self.assertEqual(row['state'], 'connected')
                self.assertEqual(cmd.call_args.args[0][0], tool)

    def test_multi_gpu_amd_and_intel(self):
        gpus = s.linux_gpu(self.root/'drm')
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]['watts'], 23.5)
        self.assertEqual(gpus[0]['utilization'], 37)
        self.assertIn('Intel', gpus[1]['name'])
        self.assertIsNone(gpus[1]['watts'])

    def test_disks_use_mount_fixture_and_skip_unreadable_device(self):
        raw = '/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /media/My\\040Disk ext4 rw 0 0\nproc /proc proc rw 0 0\n'
        usage = SimpleNamespace(total=100, used=40)
        with patch.object(s.sys, 'platform', 'linux'), patch.object(s.Path, 'read_text', return_value=raw), \
                patch.object(s.shutil, 'disk_usage', side_effect=[usage, PermissionError()]):
            disks = s.probe_disks()
        self.assertEqual([r['path'] for r in disks], ['/', '/media/My Disk'])
        self.assertEqual(disks[0]['percent'], 40)
        self.assertIsNone(disks[1]['total'])

    def test_clocks_use_monotonic_and_boottime(self):
        with patch.object(s.sys, 'platform', 'linux'), patch.object(s.time, 'CLOCK_BOOTTIME', 7, create=True), \
                patch.object(s.time, 'clock_gettime', return_value=1000, create=True) as clock, \
                patch.object(s.time, 'monotonic', return_value=700), patch.object(s.time, 'time', return_value=5000):
            result = s.probe_clocks()
        clock.assert_called_once_with(7)
        self.assertEqual(result, dict(uptime=1000, awake=700, boot=4000, fast_startup=None))

    def test_journal_verification_and_missing_access(self):
        raw = '\n'.join(json.dumps(row) for row in self.fixture['journal'])
        self.assertEqual(s.parse_journal_awake(raw), 400)
        with patch.object(s, 'command', return_value=raw) as cmd:
            self.assertIn('incomplete suspend intervals', s.verify_linux_awake()['message'])
            self.assertIn('journalctl', cmd.call_args.args[0])
        raw = '\n'.join(json.dumps(row) for row in self.fixture['journal'][:2])
        with patch.object(s, 'command', return_value=raw), patch.object(s, 'probe_clocks', return_value=dict(uptime=1000, awake=410)):
            result = s.verify_linux_awake()
            self.assertEqual(result['event_seconds'], 410)
            self.assertEqual(result['difference'], 0)
        with patch.object(s, 'command', return_value=None):
            self.assertIn('could not be read', s.verify_linux_awake()['message'])


class WindowsTests(unittest.TestCase):
    def test_gpu_without_nvidia_and_mixed_adapters(self):
        rows = [{'Name': 'AMD Radeon Graphics', 'AdapterRAM': 2*1024**3}, {'Name': 'Intel UHD', 'AdapterRAM': 1024**3}]
        with patch.object(s.sys, 'platform', 'win32'), patch.object(s, 'powershell', return_value=json.dumps(rows)), \
                patch.object(s.shutil, 'which', return_value=None), patch.object(s, 'command') as cmd:
            gpus = s.probe_gpu()
            cmd.assert_not_called()
            self.assertEqual([g['name'] for g in gpus], [r['Name'] for r in rows])
        rows.append({'Name': 'NVIDIA RTX', 'AdapterRAM': 8*1024**3})
        with patch.object(s.sys, 'platform', 'win32'), patch.object(s, 'powershell', return_value=json.dumps(rows)), \
                patch.object(s.shutil, 'which', return_value='nvidia-smi'), \
                patch.object(s, 'command', return_value='NVIDIA RTX, 12, 30, 10, 100, 8192'):
            self.assertEqual(len(s.probe_gpu()), 3)

    def test_multiple_batteries_match_identity_and_sum(self):
        data = {'status': [{'InstanceName': name, 'RemainingCapacity': 10000, 'Voltage': 12000,
                            'DischargeRate': 5000} for name in ('A', 'B')],
                'full': [{'InstanceName': name, 'FullChargedCapacity': 20000} for name in ('B', 'A')],
                'design': [{'InstanceName': name, 'DesignedCapacity': 25000} for name in ('A', 'B')]}
        result = s.parse_battery(data)
        self.assertEqual(result['full_wh'], 40)
        self.assertEqual(result['capacity_wh'], 20)
        self.assertEqual(result['watts'], 10)
        self.assertAlmostEqual(result['wear'], 20)
        self.assertIsNone(result['voltage'])

    def test_wifi_never_uses_localized_netsh(self):
        with patch.object(s.sys, 'platform', 'win32'), patch.object(s, 'windows_wifi', return_value=[]) as api, \
                patch.object(s, 'command') as cmd:
            self.assertEqual(s.probe_wifi(), [])
            api.assert_called_once()
            cmd.assert_not_called()

    def test_wlan_enumeration_unavailable_service_and_no_interfaces(self):
        api = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            'WlanOpenHandle', 'WlanEnumInterfaces', 'WlanQueryInterface', 'WlanFreeMemory', 'WlanCloseHandle')})
        api.WlanOpenHandle.return_value = 1062
        with patch.object(C, 'WinDLL', return_value=api, create=True):
            self.assertIsNone(s.windows_wifi())
        api.WlanOpenHandle.return_value = 0
        buffers = []
        def enum(handle, reserved, output):
            kind = output._obj._type_
            table = kind()
            buffers.append(table)
            C.cast(output, C.POINTER(C.POINTER(kind)))[0] = C.pointer(table)
            return 0
        api.WlanEnumInterfaces.side_effect = enum
        with patch.object(C, 'WinDLL', return_value=api, create=True):
            self.assertEqual(s.windows_wifi(), [])
        api.WlanFreeMemory.assert_called_once()
        api.WlanCloseHandle.assert_called_once()

    def test_wlan_connected_unicode_ssid_and_denied_location_access(self):
        api = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            'WlanOpenHandle', 'WlanEnumInterfaces', 'WlanQueryInterface', 'WlanFreeMemory', 'WlanCloseHandle')})
        buffers = []
        def enum(handle, reserved, output):
            kind = output._obj._type_
            table = kind()
            table.count = 1
            table.items[0].name = 'Wireless adapter'
            table.items[0].state = 1
            buffers.append(table)
            C.cast(output, C.POINTER(C.POINTER(kind)))[0] = C.pointer(table)
            return 0
        def query(handle, guid, opcode, reserved, size, output, opcode_type):
            self.assertEqual(opcode, 7)
            association = 8 + 256*C.sizeof(C.c_wchar)
            raw = bytearray(association+68+16)
            ssid = 'Office: 会议'.encode('utf-8')
            struct.pack_into('<I', raw, association, len(ssid))
            raw[association+4:association+4+len(ssid)] = ssid
            struct.pack_into('<III', raw, association+56, 92, 866700, 780000)
            buffer = C.create_string_buffer(bytes(raw))
            buffers.append(buffer)
            output._obj.value = C.addressof(buffer)
            size._obj.value = len(raw)
            return 0
        api.WlanEnumInterfaces.side_effect = enum
        api.WlanQueryInterface.side_effect = query
        with patch.object(C, 'WinDLL', return_value=api, create=True):
            rows = s.windows_wifi()
            self.assertEqual(rows[0]['ssid'], 'Office: 会议')
            self.assertEqual(rows[0]['signal'], 92)
            self.assertEqual(rows[0]['rx_mbps'], 866.7)
            self.assertEqual(api.WlanFreeMemory.call_count, 2)
            api.WlanQueryInterface.side_effect = None
            api.WlanQueryInterface.return_value = 5
            self.assertNotIn('ssid', s.windows_wifi()[0])


if __name__ == '__main__':
    unittest.main()
