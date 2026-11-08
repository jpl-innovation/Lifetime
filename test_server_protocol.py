import copy
import unittest
import uuid

from lifetime_protocol import ProtocolError, decode, validate_identity, validate_sync


def message():
    return dict(version=1, device_id=str(uuid.uuid4()), policy_version=1,
                policy=dict(history=False, resources=False), records=[dict(
                    seq=1, observed_at='2026-11-07T12:00:00+07:00', awake_seconds=10,
                    measured_seconds=10, imported_seconds=0,
                    totals=dict(measured_seconds=50, imported_seconds=0, baseline_seconds=100))])


class ReadingsV2Tests(unittest.TestCase):
    def test_readings_and_command_result(self):
        value = message()
        value['policy'].update(resources=True, control=True)
        value['readings'] = dict(observed_at='2026-11-07T12:00:00Z',
            specs=dict(cpu_name='CPU', cpu_cores=8, cpu_threads=16, ram_bytes=1000,
                       disks=[dict(name='C:\\', total_bytes=1000, free_bytes=100)],
                       gpu_names=['GPU'], mac_addresses=['AA:BB:CC:DD:EE:FF'], battery_design_wh=None),
            performance=dict(cpu_percent=50, ram_used_bytes=500, disk_free_bytes=100,
                             battery_percent=None, charging=None, battery_health_percent=65,
                             power_watts=None, network_kind='ethernet', wifi_signal_percent=None, temperature_c=-5))
        value['command_result'] = dict(id=str(uuid.uuid4()), status='declined', detail='Owner declined')
        validate_sync(value)
        mutations = [
            lambda m: m['policy'].update(control=1),
            lambda m: m['readings']['specs'].update(cpu_cores=True),
            lambda m: m['readings']['specs'].update(cpu_threads=1.5),
            lambda m: m['readings']['specs'].update(ram_bytes=-1),
            lambda m: m['readings']['specs'].update(cpu_name='x'*129),
            lambda m: m['readings']['specs'].update(gpu_names=['GPU']*9),
            lambda m: m['readings']['specs'].update(mac_addresses=['AA-BB-CC-DD-EE-FF']),
            lambda m: m['readings']['specs']['disks'][0].update(free_bytes=1001),
            lambda m: m['readings']['performance'].update(cpu_percent=101),
            lambda m: m['readings']['performance'].update(ram_used_bytes=0.5),
            lambda m: m['readings']['performance'].update(charging=1),
            lambda m: m['readings']['performance'].update(network_kind='mobile'),
            lambda m: m['readings']['performance'].update(temperature_c=float('nan')),
            lambda m: m['readings']['performance'].update(temperature_c=10**1000),
            lambda m: m['readings']['performance'].update(power_watts=float('inf')),
            lambda m: m['readings']['performance'].update(extra=1),
            lambda m: m['command_result'].update(id='bad'),
            lambda m: m['command_result'].update(status='pending'),
            lambda m: m['command_result'].update(detail='x'*201),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(value)
            mutate(bad)
            with self.subTest(mutate=mutate), self.assertRaises(ProtocolError):
                validate_sync(bad)


class ProtocolTests(unittest.TestCase):
    def test_normal_and_empty(self):
        value = message()
        self.assertIs(validate_sync(value), value)
        value['records'] = []
        validate_sync(value)

    def test_invalid_core(self):
        mutations = [lambda m: m.update(version=True), lambda m: m.update(policy_version=0),
                     lambda m: m.update(device_id='hostname'), lambda m: m.update(command='restart'),
                     lambda m: m['records'][0].update(seq=True),
                     lambda m: m['records'][0].update(seq=10**1000),
                     lambda m: m['records'][0].update(awake_seconds=11),
                     lambda m: m['records'][0].update(observed_at='2026-11-07T12:00:00'),
                     lambda m: m['records'][0]['totals'].update(baseline_seconds=float('nan')),
                     lambda m: m.update(records=m['records'] * 101)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = message()
                mutate(value)
                with self.assertRaises(ProtocolError):
                    validate_sync(value)

    def test_privacy(self):
        for field in ('history', 'readings'):
            value = message()
            value[field] = {}
            with self.assertRaises(ProtocolError):
                validate_sync(value)
        value = message()
        value['policy']['resources'] = True
        value['readings'] = dict(observed_at='2026-11-07T00:00:00Z', serial='secret')
        with self.assertRaises(ProtocolError):
            validate_sync(value)

    def test_json(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff', b'[' * 2000):
            with self.assertRaises(ProtocolError):
                decode(raw)

    def test_identity(self):
        self.assertEqual(validate_identity({})['location'], '')
        self.assertEqual(validate_identity({'display_name': 'Máy lễ tân 🖥'})['display_name'], 'Máy lễ tân 🖥')
        for value in ({'deployment_date': '2026-02-30'}, {'serial': 'private'}, {'asset_tag': False}):
            with self.assertRaises(ProtocolError):
                validate_identity(value)


if __name__ == '__main__':
    unittest.main()
