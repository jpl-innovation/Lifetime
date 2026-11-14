"""Saved-data safety regressions. All I/O is isolated in temporary directories."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lifetime_import as imp
import lifetime_ledger as schema
import lifetime_tracker as tracker


DAY = '2026-11-06'
STAMP = DAY + 'T12:00:00+00:00'


def ledger():
    with patch.object(tracker, 'estimate_first_seen', return_value=(DAY, 'os-install-date')):
        value = tracker.new_ledger()
    value.update(total_seconds=600, baseline_seconds=120, boots=1,
                 tracking_started=DAY, history_started=DAY, recording_started=STAMP,
                 current_session_seconds=600, longest_session_seconds=600,
                 last_clock=600, boot_id='a', boot_credited_for='a', boot_accounted_seconds=600,
                 last_update=STAMP, daily={DAY: 600}, hourly=[25]*24,
                 dated_hourly={DAY: [25]*24}, sessions=[dict(start=STAMP, seconds=600)],
                 coverage_gaps=[dict(start=STAMP, end=STAMP, reason='test')], clock_note_count=2)
    return value


def contribution(seconds=60, day='2026-01-01'):
    return dict(seconds=seconds, boots=int(bool(seconds)), daily={day: seconds} if seconds else {},
                hourly=[seconds]+[0]*23, sessions=[dict(start=day+'T00:00:00+00:00',
                seconds=seconds, imported=True)] if seconds else [],
                first_day=day if seconds else None, last_day=day if seconds else None,
                until=STAMP, longest_session_seconds=seconds, sleep_seconds=0,
                unclean_boots=0, boots_found=1, skipped_boots=0)


class ValidationTests(unittest.TestCase):
    def test_named_bad_fields_fail_before_readers_consume_them(self):
        cases = [
            ('total_seconds', True), ('total_seconds', -1), ('total_seconds', float('nan')),
            ('total_seconds', float('inf')), ('total_seconds', 10**1000),
            ('baseline_seconds', -1), ('current_session_seconds', '5'),
            ('longest_session_seconds', False), ('boot_accounted_seconds', -1),
            ('unattributed_seconds', -1), ('boots', -1), ('boots', '1'), ('boots', True),
            ('last_clock', -1), ('last_clock', '1'), ('boot_id', 1), ('boot_credited_for', []),
            ('first_seen', 'garbage'), ('first_seen', DAY+' trailing'),
            ('first_seen', STAMP), ('first_seen', '20261106'),
            ('tracking_started', 'garbage'), ('history_started', '2026-02-30'),
            ('last_update', 'garbage'), ('last_update', DAY), ('recording_started', DAY),
            ('first_sample', 'garbage'), ('first_seen_source', 'invented'), ('first_seen_source', []),
            ('daily', []), ('daily', {DAY: '1'}), ('daily', {DAY: -1}),
            ('daily', {'2026-02-30': 1}), ('daily', {STAMP: 1}),
            ('hourly', {}), ('hourly', [0]*23), ('hourly', [False]+[0]*23),
            ('hourly', [float('inf')]+[0]*23),
            ('dated_hourly', None), ('dated_hourly', []), ('dated_hourly', {STAMP: [0]*24}),
            ('dated_hourly', {DAY: [0]*23}), ('dated_hourly', {DAY: [-1]+[0]*23}),
            ('dated_hourly', {DAY: [10**1000]+[0]*23}),
            ('sessions', {}), ('sessions', [dict(start=STAMP)]),
            ('sessions', [dict(start=DAY, seconds=0)]),
            ('sessions', [dict(start=STAMP, seconds=-1)]),
            ('sessions', [dict(start=STAMP, seconds=1, imported=1)]),
            ('imported', []), ('imported', None), ('coverage_gaps', {}),
            ('coverage_gaps', [dict(reason='test', start=DAY)]), ('coverage_gaps', [{}]),
            ('clock_note_count', True), ('clock_note_count', -1),
            ('tracking_started_estimated', 1), ('daily_allocation', None),
            ('daily_allocation', dict(estimated=1)),
            ('daily_allocation', dict(estimated=True, seconds=-1)),
            ('daily_repair_version', True), ('daily_repair_version', -1),
            ('daily_repair', dict(allocations='bad')),
            ('daily_repair', dict(allocations=[None])),
            ('daily_repair', dict(allocations=[dict(estimated=True, start='bad')])),
        ]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'ledger.json')
            for field, bad in cases:
                with self.subTest(field=field, bad=str(bad)[:80]):
                    value = ledger()
                    value[field] = bad
                    raw = json.dumps(value).encode()
                    path.write_bytes(raw)
                    for read in (tracker.load_ledger, imp.read_path):
                        with self.assertRaisesRegex(ValueError, field):
                            read(path)
                    self.assertEqual(path.read_bytes(), raw)

    def test_nested_imported_damage_is_named_and_never_salvaged(self):
        good = imp.merge(ledger(), contribution())
        cases = [
            ('seconds', 'bad'), ('seconds', 10**1000), ('seconds', 10000),
            ('boots', True), ('boots', 100), ('daily', []), ('daily', {DAY: -1}),
            ('hourly', [0]*23), ('dated_hourly', {STAMP: [0]*24}),
            ('sessions', [dict(start=STAMP)]), ('sleep_seconds', -1),
            ('longest_session_seconds', False), ('unclean_boots', -1),
            ('boots_found', '1'), ('skipped_boots', False), ('first_day', STAMP),
            ('last_day', 'bad'), ('until', DAY), ('original', None),
        ]
        for field, bad in cases:
            with self.subTest(field=field):
                value = copy.deepcopy(good)
                value['imported'][field] = bad
                with self.assertRaisesRegex(ValueError, 'imported.'+field):
                    tracker.validate_ledger(value)
                with self.assertRaisesRegex(ValueError, 'imported.'+field):
                    schema.salvage(value, tracker.new_ledger())
        for field, bad in [('first_seen', STAMP), ('tracking_started', 'bad'),
                           ('history_started', 5), ('first_seen_source', 'bad'),
                           ('longest_session_seconds', -1)]:
            with self.subTest(original=field):
                value = copy.deepcopy(good)
                value['imported']['original'][field] = bad
                with self.assertRaisesRegex(ValueError, 'imported.original.'+field):
                    tracker.validate_ledger(value)
                with self.assertRaisesRegex(ValueError, 'imported.original.'+field):
                    schema.salvage(value, {})

    def test_missing_primary_total_never_defaults_to_zero(self):
        for value in ({}, {'baseline_seconds': 10}, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tracker.validate_ledger(value)
                with self.assertRaises(ValueError):
                    schema.salvage(value, tracker.new_ledger())

    def test_legacy_defaults_and_large_daily_values_survive(self):
        value = dict(total_seconds=200000, last_clock=50, last_update=STAMP,
                     daily={DAY: 200000}, extension={'kept': True})
        result = tracker.validate_ledger(value)
        self.assertEqual(result['total_seconds'], 200000)
        self.assertEqual(result['boot_accounted_seconds'], 50)
        self.assertEqual(result['daily'], value['daily'])
        self.assertEqual(result['first_seen_source'], 'unknown')
        self.assertTrue(result['tracking_started_estimated'])
        self.assertEqual(result['extension'], value['extension'])
        value['first_sample'] = '2025-12-01T01:00:00+00:00'
        result = tracker.validate_ledger(value)
        self.assertEqual(result['tracking_started'], '2025-12-01')
        self.assertFalse(result['tracking_started_estimated'])
        self.assertNotIn('tracking_started', value)

    def test_strict_dates_and_explicit_timestamps(self):
        for value in (DAY+'junk', STAMP, '20261106', '2026-02-30', None, 1):
            self.assertFalse(schema.is_date(value), value)
        for value in (DAY, DAY+'junk', '2026-02-30T00:00:00', 1):
            self.assertFalse(schema.is_datetime(value), value)
        for value in (STAMP, DAY+'T12:00:00Z', DAY+'T12:00:00', DAY+' 12:00:00.123+07:00'):
            self.assertTrue(schema.is_datetime(value), value)
        self.assertFalse(schema.is_number(10**1000))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'ledger.json'

    def backups(self):
        return list(self.root.glob('ledger.json.corrupt-*'))

    def test_salvage_legacy_boot_accounting_matches_normal_migration(self):
        value = ledger()
        value.pop('boot_accounted_seconds')
        value['hourly'] = []
        before = copy.deepcopy(value)
        defaults = tracker.new_ledger()
        default_snapshot = copy.deepcopy(defaults)
        result, notes = schema.salvage(value, defaults)
        self.assertEqual(result['boot_accounted_seconds'], 600)
        self.assertEqual(value, before)
        self.assertEqual(defaults, default_snapshot)
        self.assertEqual(len(notes), 1)
        tracker.sample(result, 600, 'a', STAMP, credit_current=True, history=False)
        self.assertEqual(result['total_seconds'], 600)

    def test_recovery_preserves_legacy_boot_credit_boundary(self):
        value = ledger()
        value.pop('boot_accounted_seconds')
        value.pop('first_seen_source')
        value['hourly'] = []
        self.path.write_text(json.dumps(value))
        result, _, _ = tracker.recover(self.root)
        self.assertEqual(result['first_seen_source'], 'unknown')
        tracker.sample(result, 600, 'a', STAMP, credit_current=True, history=False)
        self.assertEqual(result['total_seconds'], 600)

    def test_salvage_lists_changes_and_retains_core_and_valid_metadata(self):
        before = ledger()
        before.update(daily={DAY: 600, 'bad': -1}, dated_hourly={DAY: [25]*24, 'bad': []},
                      hourly=[0]*23, sessions=[dict(start=STAMP, seconds=5), {}],
                      extension={'preserved': True})
        raw = b'\xef\xbb\xbf' + json.dumps(before, indent=3).encode() + b'\r\n'
        self.path.write_bytes(raw)
        result, how, notes = tracker.recover(self.root)
        self.assertEqual(how, 'salvaged')
        self.assertEqual({n.split(':')[0] for n in notes}, {'daily', 'dated_hourly', 'hourly', 'sessions'})
        self.assertEqual(self.backups()[0].read_bytes(), raw)
        for field in schema.CORE - {'imported'}:
            self.assertEqual(result.get(field), before.get(field), field)
        for field in ('recording_started', 'coverage_gaps', 'clock_note_count', 'extension', 'first_seen_source'):
            self.assertEqual(result[field], before[field], field)
        self.assertEqual(result['daily'], {DAY: 600})
        self.assertEqual(result['dated_hourly'], {DAY: [25]*24})
        self.assertEqual(result['sessions'], [dict(start=STAMP, seconds=5)])
        self.assertEqual(tracker.read_ledger(self.root), result)
        saved = self.path.read_bytes()
        self.assertEqual(tracker.recover(self.root)[1:], ('unchanged', []))
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(len(self.backups()), 1)

    def test_bad_dates_reset_to_unknown_instead_of_current_os_estimate(self):
        value = ledger()
        value.update(first_seen='broken', last_update='broken', recording_started='broken')
        self.path.write_text(json.dumps(value))
        result, _, notes = tracker.recover(self.root)
        self.assertIsNone(result['first_seen'])
        self.assertEqual(result['first_seen_source'], 'unknown')
        self.assertIsNone(result['recording_started'])
        self.assertEqual(result['total_seconds'], 600)
        self.assertTrue(any(n.startswith('first_seen_source:') for n in notes))

    def test_newest_valid_backup_skips_newer_invalid_or_undecodable_candidates(self):
        for raw in (b'\xff\xfe\x00damaged', b'{broken', b'{"total_seconds":true}', b'{}'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                old = self.root/'ledger.json.bak-old'
                newest = self.root/'backup-newest'/'ledger.json'
                newest.parent.mkdir(exist_ok=True)
                tracker.atomic_json(old, dict(ledger(), total_seconds=500))
                tracker.atomic_json(newest, ledger())
                invalid = self.root/'ledger.json.bak-invalid'
                invalid.write_bytes(b'{"total_seconds":false}')
                broken = self.root/'ledger.json.bak-broken'
                broken.write_bytes(b'\xff')
                for index, path in enumerate((old, newest, invalid, broken)):
                    os.utime(path, (100+index, 100+index))
                result, how, notes = tracker.recover(self.root)
                self.assertEqual(result['total_seconds'], 600)
                self.assertIn('backup-newest', how)
                self.assertTrue(notes)
                self.assertIn(raw, [p.read_bytes() for p in self.backups()])

    def test_missing_valid_backup_leaves_original_bytes_unchanged(self):
        for raw in (b'\xff', b'{broken', b'{}', b'{"total_seconds":-1}', b'[]'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                (self.root/'ledger.json.bak-invalid').write_bytes(b'{}')
                with self.assertRaisesRegex(ValueError, 'left unchanged; no valid backup'):
                    tracker.recover(self.root)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertEqual(self.backups(), [])

    def test_each_core_field_refuses_salvage_instead_of_resetting_totals(self):
        for field in schema.CORE - {'imported'}:
            with self.subTest(field=field):
                value = ledger()
                value[field] = []
                self.path.write_text(json.dumps(value))
                raw = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, field):
                    tracker.recover(self.root)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertEqual(self.backups(), [])

    def test_damaged_import_marker_requires_backup_and_restores_idempotence(self):
        good = imp.merge(ledger(), contribution())
        broken = copy.deepcopy(good)
        broken['imported']['seconds'] = 'damaged'
        self.path.write_text(json.dumps(broken))
        raw = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'imported.seconds'):
            tracker.recover(self.root)
        self.assertEqual(self.path.read_bytes(), raw)
        tracker.atomic_json(self.root/'ledger.json.bak-good', good)
        recovered, how, _ = tracker.recover(self.root)
        self.assertIn('restored', how)
        self.assertEqual(imp.merge(recovered, contribution()), recovered)
        self.assertEqual(self.backups()[0].read_bytes(), raw)

    def test_valid_file_is_not_rewritten_and_missing_file_is_not_created(self):
        self.path.write_text(json.dumps(ledger(), indent=3))
        raw = self.path.read_bytes()
        self.assertEqual(tracker.recover(self.root)[1:], ('unchanged', []))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups(), [])
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            tracker.recover(self.root)
        self.assertFalse(self.path.exists())

    def test_recovery_preserves_bytes_before_replace_and_does_not_write_status(self):
        self.path.write_bytes(b'\xffbroken')
        raw = self.path.read_bytes()
        tracker.atomic_json(self.root/'ledger.json.bak-good', ledger())
        def failed_replace(source, destination):
            self.assertEqual(Path(destination), self.path)
            self.assertEqual(self.backups()[0].read_bytes(), raw)
            raise OSError('interrupted replace')
        with patch.object(tracker.os, 'replace', side_effect=failed_replace):
            with self.assertRaises(OSError):
                tracker.recover(self.root)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse((self.root/'status.json').exists())
        self.assertEqual(set(self.root.iterdir()),
                         {self.path, self.root/'ledger.lock', self.root/'ledger.json.bak-good', *self.backups()})

    def test_failed_corrupt_copy_fsync_prevents_replacement(self):
        self.path.write_bytes(b'\xffbroken')
        raw = self.path.read_bytes()
        tracker.atomic_json(self.root/'ledger.json.bak-good', ledger())
        with patch.object(tracker.os, 'fsync', side_effect=OSError('disk full')), \
                patch.object(tracker.os, 'replace') as replace:
            with self.assertRaises(OSError):
                tracker.recover(self.root)
            replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), raw)

    def test_replacement_fsync_failure_keeps_corrupt_copy_and_cleans_temp(self):
        self.path.write_bytes(b'\xffbroken')
        raw = self.path.read_bytes()
        backup = self.root/'ledger.json.bak-good'
        tracker.atomic_json(backup, ledger())
        real_fsync = os.fsync
        calls = []
        def full_disk(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError('disk full during replacement fsync')
            real_fsync(fd)
        with patch.object(tracker.os, 'fsync', side_effect=full_disk):
            with self.assertRaises(OSError):
                tracker.recover(self.root)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.backups()[0].read_bytes(), raw)
        self.assertEqual(set(self.root.iterdir()),
                         {self.path, backup, self.root/'ledger.lock', *self.backups()})

    def test_recover_cli_reports_action_and_leaves_unrecoverable_data(self):
        self.path.write_text(json.dumps(dict(ledger(), hourly=[])))
        with patch.object(tracker, 'data_dir', return_value=self.root), \
                patch('sys.argv', ['tracker', '--recover']), contextlib.redirect_stdout(io.StringIO()) as output:
            tracker.main()
        self.assertIn('salvaged', output.getvalue())
        self.assertIn('hourly', output.getvalue())
        self.assertIn('earliest known date', output.getvalue())
        self.path.write_bytes(b'\xff')
        with patch.object(tracker, 'data_dir', return_value=self.root), \
                patch('sys.argv', ['tracker', '--recover']), contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                tracker.main()
        self.assertEqual(error.exception.code, 1)
        self.assertIn('no valid backup', output.getvalue())
        self.assertEqual(self.path.read_bytes(), b'\xff')


class PersistenceTests(unittest.TestCase):
    def test_replace_write_and_fsync_failure_keep_original_and_remove_temp(self):
        for failure in ('replace', 'write', 'fsync'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                path = Path(root, 'ledger.json')
                tracker.atomic_json(path, ledger())
                raw = path.read_bytes()
                target = {'replace': 'os.replace', 'write': 'json.dump', 'fsync': 'os.fsync'}[failure]
                def failed_write(value, stream, **kwargs):
                    stream.write('{"partial":')
                    raise OSError('disk full during write')
                effect = failed_write if failure == 'write' else OSError('disk full or interrupted')
                with patch('lifetime_tracker.'+target, side_effect=effect):
                    with self.assertRaises(OSError):
                        tracker.atomic_json(path, dict(ledger(), total_seconds=900))
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(list(Path(root).iterdir()), [path])


class ProvenanceAndImportTests(unittest.TestCase):
    def test_default_api_and_new_ledger_support_old_string_mocks(self):
        self.assertIsInstance(tracker.estimate_first_seen(), str)
        day, source = tracker.estimate_first_seen(with_source=True)
        self.assertTrue(schema.is_date(day))
        self.assertIn(source, schema.FIRST_SEEN_SOURCES)
        with patch.object(tracker, 'estimate_first_seen', return_value=DAY):
            value = tracker.new_ledger()
        self.assertEqual(value['first_seen'], DAY)
        self.assertEqual(value['first_seen_source'], 'unknown')

    def test_os_file_and_tracking_start_sources(self):
        fake_reg = SimpleNamespace(HKEY_LOCAL_MACHINE=1,
            OpenKey=lambda *args: contextlib.nullcontext('key'), QueryValueEx=lambda *args: (100, 0))
        with patch.object(tracker.time, 'time', return_value=200), \
                patch.object(tracker.sys, 'platform', 'win32'), patch.dict('sys.modules', winreg=fake_reg):
            self.assertEqual(tracker.estimate_first_seen(True)[1], 'os-install-date')
        with patch.object(tracker.time, 'time', return_value=200), \
                patch.object(tracker.sys, 'platform', 'linux'), \
                patch.object(Path, 'stat', return_value=SimpleNamespace(st_mtime=100)):
            self.assertEqual(tracker.estimate_first_seen(True)[1], 'system-file-date')
        with patch.object(tracker.time, 'time', return_value=200), \
                patch.object(tracker.sys, 'platform', 'linux'), \
                patch.object(Path, 'stat', return_value=SimpleNamespace(st_mtime=300)):
            self.assertEqual(tracker.estimate_first_seen(True)[1], 'tracking-start')

    def test_import_updates_and_restores_original_date_source_on_reimport(self):
        for source in ('os-install-date', 'system-file-date', 'tracking-start', 'unknown'):
            with self.subTest(source=source):
                live = ledger()
                live['first_seen_source'] = source
                merged = imp.merge(live, contribution())
                self.assertEqual(merged['first_seen'], '2026-01-01')
                self.assertEqual(merged['first_seen_source'], 'imported-log')
                self.assertEqual(merged, imp.merge(merged, contribution()))
                restored = imp.merge(merged, contribution(0))
                for field in ('total_seconds', 'baseline_seconds', 'first_seen', 'first_seen_source', 'boots'):
                    self.assertEqual(restored[field], live[field], field)
                equal = imp.merge(merged, contribution(60, DAY))
                self.assertEqual(equal['first_seen_source'], source)

    def test_legacy_provenance_and_incomplete_markers_fail_closed_for_reimport(self):
        live = ledger()
        live.pop('first_seen_source')
        merged = imp.merge(live, contribution())
        merged['imported']['original'].pop('first_seen_source')
        self.assertEqual(imp.merge(merged, contribution(0))['first_seen_source'], 'unknown')
        for marker in ({}, {'daily': {DAY: 5}, 'hourly': [0]*24}, {'seconds': 5, 'boots': 1}):
            with self.subTest(marker=marker):
                value = dict(ledger(), imported=marker)
                self.assertEqual(tracker.validate_ledger(value)['imported'], marker)
                with self.assertRaisesRegex(ValueError, 'refusing reimport'):
                    imp.merge(value, contribution())
                with self.assertRaisesRegex(ValueError, 'refusing reimport'):
                    imp.rebuild([], 100, value)


class BootBoundaryTests(unittest.TestCase):
    def setUp(self):
        fake = patch('lifetime_daily.boot_history', return_value=(None, [], True))
        fake.start()
        self.addCleanup(fake.stop)

    def test_zero_300_301_backwards_and_boot_identity_boundaries(self):
        value = tracker.new_ledger()
        tracker.sample(value, 100, 'a', STAMP, history=False)
        # After rollback, 650..660 is below the prior observed watermark (701).
        # It is ambiguous overlap, not new evidence of awake time.
        for tick, identity, total, boots in ((100, 'a', 100, 1), (400, 'a', 400, 1),
                (701, 'a', 400, 1), (650, 'a', 400, 1), (660, 'a', 400, 1),
                (900, 'b', 1300, 2), (900, 'b', 1300, 2), (5, 'c', 1305, 3)):
            with self.subTest(tick=tick, identity=identity):
                tracker.sample(value, tick, identity, STAMP, history=False)
                self.assertEqual(value['total_seconds'], total)
                self.assertEqual(value['boots'], boots)
        # No identity available: a backwards awake clock is the reboot evidence.
        tracker.sample(value, 1, None, STAMP, history=False)
        self.assertEqual(value['boots'], 4)
        self.assertEqual(value['total_seconds'], 1306)

    def test_credit_cap_and_repeat_credit_with_new_boot(self):
        for tick in (tracker.MAX_BOOT_CREDIT-1, tracker.MAX_BOOT_CREDIT, tracker.MAX_BOOT_CREDIT+1):
            with self.subTest(tick=tick):
                value = tracker.new_ledger()
                tracker.sample(value, tick, 'a', STAMP, history=False)
                credited = min(tick, tracker.MAX_BOOT_CREDIT)
                self.assertEqual(value['total_seconds'], credited)
                tracker.sample(value, tick, 'a', STAMP, credit_current=True, history=False)
                self.assertEqual(value['total_seconds'], credited)
                tracker.sample(value, tick, 'b', STAMP, history=False)
                self.assertEqual(value['total_seconds'], 2*credited)

    def test_wall_clock_changes_only_record_notes_and_history_retains_gaps(self):
        value = tracker.new_ledger()
        tracker.sample(value, 10, 'a', STAMP)
        tracker.sample(value, 40, 'a', '2026-11-05T12:00:00+00:00')
        self.assertEqual(value['total_seconds'], 40)
        self.assertEqual(value['clock_note_count'], 1)
        tracker.sample(value, 341, 'a', STAMP)
        self.assertEqual(value['total_seconds'], 40)
        self.assertEqual(value['coverage_gaps'][0]['reason'], 'awake_clock_discontinuity')
        self.assertEqual(value['recording_started'], STAMP)

    @unittest.skipUnless(hasattr(time, 'tzset'), 'time.tzset unavailable on Windows')
    def test_dst_and_timezone_changes_preserve_totals(self):
        previous = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'America/New_York'
            time.tzset()
            for start, end in (('2026-03-08T06:59:30+00:00', '2026-03-08T07:00:30+00:00'),
                               ('2026-11-01T05:59:30+00:00', '2026-11-01T06:00:30+00:00')):
                value = tracker.new_ledger()
                tracker.sample(value, 0, 'a', start)
                tracker.sample(value, 60, 'a', end)
                self.assertEqual(value['total_seconds'], 60)
                self.assertEqual(sum(value['daily'].values()), 60)
                self.assertEqual(sum(value['hourly']), 60)
            os.environ['TZ'] = 'Asia/Ho_Chi_Minh'
            time.tzset()
            tracker.sample(value, 90, 'a', '2026-11-01T06:01:00+00:00')
            self.assertEqual(value['total_seconds'], 90)
            self.assertEqual(sum(value['daily'].values()), 90)
            self.assertEqual(sum(value['hourly']), 90)
        finally:
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()


if __name__ == '__main__':
    unittest.main()
