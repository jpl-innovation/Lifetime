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
        # /proc level -54 dBm maps to 92 % the way NetworkManager does (not link quality 56/70 = 80 %).
        self.assertEqual(s.parse_wireless(self.fixture['wireless'])['wlan0'], 92)
        for tool, output, expected in [('nmcli', self.fixture['nmcli'], 80), ('iw', self.fixture['iw'], 92)]:
            with self.subTest(tool=tool), patch.object(s.shutil, 'which', side_effect=lambda name: name if name == tool else None), \
                    patch.object(s, 'command', return_value=output) as cmd:
                row = s.linux_wifi(self.root/'net', wireless)[0]
                self.assertEqual(row['ssid'], 'Office: Upstairs')
                self.assertEqual(row['signal'], expected)
                self.assertEqual(row['state'], 'connected')
                self.assertEqual(cmd.call_args.args[0][0], tool)

    def test_wifi_prefers_networkmanager_signal_over_proc(self):
        """Bug 2: /proc said 83 % while NetworkManager (and the desktop) showed 100 %."""
        wireless = self.root/'wireless'
        wireless.write_text('Inter-| sta-| Quality | Discarded packets\n face | tus | link level noise\n wlan0: 0000 58. -48. -256 0 0 0 0 0 0\n')
        self.assertEqual(s.parse_wireless(wireless.read_text())['wlan0'], 100)   # fallback now agrees with NM's mapping
        nmcli = 'yes:Home:100:wlan0:866 Mbit/s\nno:Other:40:wlan0:130 Mbit/s\n'
        with patch.object(s.shutil, 'which', side_effect=lambda name: name if name == 'nmcli' else None), \
                patch.object(s, 'command', return_value=nmcli):
            row = s.linux_wifi(self.root/'net', wireless)[0]
        self.assertEqual((row['ssid'], row['signal'], row['state']), ('Home', 100, 'connected'))
        # nmcli missing or failing: the /proc value is still labelled and sane (0-100).
        with patch.object(s.shutil, 'which', return_value=None):
            self.assertEqual(s.linux_wifi(self.root/'net', wireless)[0]['signal'], 100)
        self.assertEqual(s.dbm_to_percent(-100), 0)
        self.assertEqual(s.dbm_to_percent(-75), 50)
        self.assertEqual(s.dbm_to_percent(-30), 100)

    def test_wifi_link_speed_from_iw_and_nmcli(self):
        """Bug 4: Wi-Fi adapters had no link speed on Linux."""
        iw = 'Connected to aa:bb (on wlan0)\n\tSSID: Home\n\tsignal: -50 dBm\n\trx bitrate: 780.0 MBit/s\n\ttx bitrate: 866.7 MBit/s\n'
        with patch.object(s.shutil, 'which', side_effect=lambda name: name if name == 'iw' else None), patch.object(s, 'command', return_value=iw):
            self.assertEqual(s.wifi_bitrate('wlan0'), 866.7)
            adapters = s.linux_network(self.root/'net', bitrate=s.wifi_bitrate)
        self.assertEqual(adapters[0]['LinkSpeed'], '1000 Mbps')            # Ethernet from sysfs, unchanged
        self.assertEqual(adapters[1]['LinkSpeed'], '866.7 Mbps')           # Wi-Fi from iw
        with patch.object(s.shutil, 'which', side_effect=lambda name: name if name == 'nmcli' else None), \
                patch.object(s, 'command', return_value='yes:Home:90:wlan0:540 Mbit/s\n'):
            self.assertEqual(s.wifi_bitrate('wlan0'), 540)
        with patch.object(s.shutil, 'which', return_value=None):
            self.assertIsNone(s.wifi_bitrate('wlan0'))
            self.assertIsNone(s.linux_network(self.root/'net', bitrate=s.wifi_bitrate)[1]['LinkSpeed'])

    def test_multi_gpu_amd_and_intel(self):
        gpus = s.linux_gpu(self.root/'drm')
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]['watts'], 23.5)
        self.assertEqual(gpus[0]['utilization'], 37)
        self.assertIn('Intel', gpus[1]['name'])
        self.assertIsNone(gpus[1]['watts'])

    def test_gpu_friendly_name_and_frequency(self):
        """Bug 3: 'Intel (0x9a49)' with nothing else. Show a real name and the clock when utilisation is unreadable."""
        (self.root/'drm/card1/device/uevent').write_text('DRIVER=i915\nPCI_SLOT_NAME=0000:00:02.0\n')
        (self.root/'drm/card1/gt_cur_freq_mhz').write_text('1250\n')
        lspci = '00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" "TigerLake-LP GT2 [Iris Xe Graphics] [9a49]" -r01 "Lenovo [17aa]" "Device [22d3]"\n'
        with patch.object(s.shutil, 'which', return_value='/usr/bin/lspci'), patch.object(s, 'command', return_value=lspci) as cmd:
            self.assertEqual(s.lspci_name('0000:00:02.0'), 'Intel Iris Xe Graphics')
            gpus = s.linux_gpu(self.root/'drm', namer=s.lspci_name)
        self.assertEqual(cmd.call_args.args[0], ['lspci', '-mm', '-nn', '-s', '0000:00:02.0'])
        self.assertEqual(gpus[1]['name'], 'Intel Iris Xe Graphics')
        self.assertEqual(gpus[1]['frequency_mhz'], 1250)
        self.assertIsNone(gpus[1]['utilization'])
        self.assertIsNone(gpus[0]['frequency_mhz'])
        amd = '03:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" "Navi 22 [Radeon RX 6700 XT] [73df]"\n'
        with patch.object(s.shutil, 'which', return_value='/usr/bin/lspci'), patch.object(s, 'command', return_value=amd):
            self.assertEqual(s.lspci_name('0000:03:00.0'), 'AMD Radeon RX 6700 XT')
        # No lspci or no match: keep the old readable fallback instead of failing.
        self.assertIn('0x1234', s.linux_gpu(self.root/'drm')[1]['name'])
        with patch.object(s.shutil, 'which', return_value=None):
            self.assertIsNone(s.lspci_name('0000:00:02.0'))

    def test_dashboard_shows_frequency_only_when_known(self):
        import lifetime_dashboard as dashboard
        self.assertEqual(dashboard.fmt(1250.0, 'MHz', 0), ('1,250', 'MHz'))
        self.assertIsNone(dashboard.fmt(None, 'MHz', 0))

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
        with patch.object(s, 'run_command', return_value=(0, raw)) as cmd:
            self.assertIn('incomplete suspend intervals', s.verify_linux_awake()['message'])
            self.assertIn('journalctl', cmd.call_args.args[0])
            self.assertEqual(cmd.call_args.kwargs['timeout'], s.JOURNAL_TIMEOUT)   # bug 5: not the default 5 s
        raw = '\n'.join(json.dumps(row) for row in self.fixture['journal'][:2])
        with patch.object(s, 'run_command', return_value=(0, raw)), patch.object(s, 'probe_clocks', return_value=dict(uptime=1000, awake=410)):
            result = s.verify_linux_awake()
            self.assertEqual(result['event_seconds'], 410)
            self.assertEqual(result['difference'], 0)
        with patch.object(s, 'run_command', return_value=None):
            self.assertIn('could not be read', s.verify_linux_awake()['message'])

    def test_verify_boot_without_suspend_events(self):
        """Bug 1: journalctl --grep exits 1 with no output when nothing matches; that is a clean boot, not an error."""
        clocks = dict(uptime=5000, awake=4990)
        entry = (0, json.dumps(self.fixture['journal'][0]) + '\n')
        def journal(args, timeout=5):
            return (1, '') if '--grep' in args else entry          # grep: no matches; plain query: journal readable
        with patch.object(s, 'run_command', side_effect=journal), patch.object(s, 'probe_clocks', return_value=clocks):
            result = s.verify_linux_awake()
        self.assertEqual((result['event_seconds'], result['awake_seconds'], result['difference']), (5000, 4990, 10))
        self.assertNotIn('Not available', result['message'])
        # The same exit code is still an error when the journal itself is unreadable (permission denied, empty).
        for plain in ((1, ''), (0, ''), (2, 'x'), None):
            with self.subTest(plain=plain), patch.object(s, 'probe_clocks', return_value=clocks), \
                    patch.object(s, 'run_command', side_effect=lambda args, timeout=5, plain=plain: (1, '') if '--grep' in args else plain):
                self.assertIn('could not be read', s.verify_linux_awake()['message'])
        # Other exit codes and timeouts are real failures.
        for failure in ((2, ''), (1, 'x\n'), None):
            with self.subTest(failure=failure), patch.object(s, 'run_command', return_value=failure):
                self.assertIn('could not be read', s.verify_linux_awake()['message'])
        # Missing clocks keep their own message.
        with patch.object(s, 'run_command', side_effect=journal), patch.object(s, 'probe_clocks', return_value=dict(uptime=None, awake=None)):
            self.assertIn('inconsistent', s.verify_linux_awake()['message'])

    def test_verify_boot_with_a_suspend_pair(self):
        """A boot with one completed suspend/resume pair still compares uptime minus sleep with the awake clock."""
        raw = '\n'.join(json.dumps(row) for row in self.fixture['journal'][:2])
        with patch.object(s, 'run_command', return_value=(0, raw)), patch.object(s, 'probe_clocks', return_value=dict(uptime=2000, awake=1410)):
            result = s.verify_linux_awake()
        self.assertEqual(result['event_seconds'], 1410)        # 2000 s uptime minus 590 s asleep
        self.assertEqual(result['difference'], 0)

    def test_command_returns_exit_code_or_ok_codes(self):
        """Bug 1/5: command() must not hide the exit code, and other callers keep their old behaviour."""
        def fake(code, out):
            return patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=code, stdout=out))
        with fake(1, ''):
            self.assertEqual(s.run_command(['x']), (1, ''))
            self.assertIsNone(s.command(['x']))                          # default: only exit 0 counts, as before
            self.assertEqual(s.command(['x'], ok_codes=(0, 1)), '')
        with fake(0, 'out'):
            self.assertEqual(s.command(['x']), 'out')
        with patch.object(s.subprocess, 'run', side_effect=s.subprocess.TimeoutExpired('x', 5)):
            self.assertIsNone(s.run_command(['x']))
            self.assertIsNone(s.command(['x'], ok_codes=(0, 1)))
        with patch.object(s.subprocess, 'run', side_effect=FileNotFoundError()):
            self.assertIsNone(s.run_command(['missing']))
        with fake(0, ''), patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')) as run:
            s.run_command(['x'], timeout=20)
            self.assertEqual(run.call_args.kwargs['timeout'], 20)


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
