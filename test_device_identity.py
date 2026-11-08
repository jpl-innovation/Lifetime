"""Identity persistence and report consent using the real settings module."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lifetime_settings as settings
import lifetime_tracker as tracker


class DeviceIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'settings.json'

    def test_empty_identity_collects_nothing(self):
        value = settings.read_settings(self.root)
        self.assertFalse(settings.export_allowed(value, 'report'))
        self.assertEqual(value['identity'], dict.fromkeys(settings.IDENTITY_FIELDS, ''))
        self.assertEqual(settings.allowed_probes(value), set())
        self.assertFalse(self.path.exists())
        settings.update_settings({'identity': {}}, self.root)
        self.assertEqual(settings.read_settings(self.root), value)

    def test_edit_clear_unicode_and_unknowns(self):
        original = {'completed': True, 'history': True, 'resources': False,
                    'extension': {'one': 1, 'two': 2},
                    'identity': {'display_name': 'Máy tính 日本語', 'future': {'a': 1}}}
        settings.save_settings(original, self.root)
        value = settings.update_settings({'identity': {'asset_tag': '資産-01',
                                                      'deployment_date': '2024-02-29'},
                                          'extension': {'two': 3}}, self.root)
        self.assertEqual(value['identity']['display_name'], 'Máy tính 日本語')
        self.assertEqual(value['identity']['future'], {'a': 1})
        self.assertEqual(value['extension'], {'one': 1, 'two': 3})
        self.assertTrue(value['history'])
        self.assertFalse(value['resources'])
        for field in settings.IDENTITY_FIELDS:
            settings.update_settings({'identity': {field: ''}}, self.root)
        self.assertEqual(settings.read_settings(self.root)['identity'],
                         dict(settings.defaults()['identity'], future={'a': 1}))

    def test_dates_are_exact_calendar_dates(self):
        self.assertEqual(settings.iso_date('2024-02-29'), dt.date(2024, 2, 29))
        for bad in ('2023-02-29', '2024-13-01', '20240101', '2024-W01-1',
                    '2024-1-01', '2024-01-01T00:00:00', '', None, 20240101):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                settings.iso_date(bad)
        settings.update_settings({'identity': {'department': 'Lab'}}, self.root)
        previous = self.path.read_bytes()
        for identity in ({'deployment_date': '2023-02-29'}, {'asset_tag': None}, []):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                settings.update_settings({'identity': identity}, self.root)
            self.assertEqual(self.path.read_bytes(), previous)

    def test_malformed_existing_refuses_update_and_load_fails_closed(self):
        for raw in ('broken', 'null', '[]', '{"completed":true,"identity":[]}',
                    '{"completed":true,"identity":{"deployment_date":"bad"}}'):
            self.path.write_text(raw, encoding='utf-8')
            self.assertEqual(settings.load_settings(self.root), settings.defaults())
            with self.assertRaises(ValueError):
                settings.update_settings({'identity': {'asset_tag': 'new'}}, self.root)
            self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

    def test_save_preserves_omitted_extensions_but_never_grants_consent(self):
        settings.save_settings({'completed': True, 'history': True, 'resources': True,
                                'identity': {'display_name': 'Owner', 'custom': 7},
                                'custom': {'nested': 1}}, self.root)
        settings.save_settings({'completed': True, 'battery': True}, self.root)
        value = settings.read_settings(self.root)
        self.assertEqual(value['identity']['display_name'], 'Owner')
        self.assertEqual(value['identity']['custom'], 7)
        self.assertEqual(value['custom'], {'nested': 1})
        self.assertFalse(value['history'])
        self.assertFalse(value['resources'])
        self.assertTrue(value['battery'])
        settings.save_settings({'identity': {'display_name': ''}}, self.root)
        self.assertFalse(settings.read_settings(self.root)['completed'])
        self.assertEqual(settings.read_settings(self.root)['identity']['display_name'], '')

    def test_atomic_failure_preserves_bytes(self):
        settings.save_settings({'completed': True, 'history': True}, self.root)
        previous = self.path.read_bytes()
        with patch.object(tracker.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                settings.update_settings({'identity': {'asset_tag': 'lost'}}, self.root)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('settings.json*')), [self.path])

    def test_legacy_nontext_location_preserves_extension_and_consent(self):
        raw = dict(completed=True, history=True, identity=dict(location={'room': 2}, _legacy_location='keep'))
        self.path.write_text(json.dumps(raw), encoding='utf-8')
        value = settings.read_settings(self.root)
        self.assertTrue(value['completed'])
        self.assertTrue(value['history'])
        self.assertEqual(value['identity']['location'], '')
        self.assertEqual(value['identity']['_legacy_location'], 'keep')
        self.assertEqual(value['identity']['_legacy_location_2'], {'room': 2})
        settings.update_settings({'identity': {'asset_tag': 'PC-014'}}, self.root)
        self.assertEqual(settings.read_settings(self.root)['identity']['_legacy_location_2'], {'room': 2})

    def test_deep_settings_fail_closed_without_overwrite(self):
        raw = '{"unknown":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        self.path.write_text(raw, encoding='utf-8')
        self.assertFalse(settings.read_settings(self.root)['completed'])
        with self.assertRaises(ValueError):
            settings.update_settings({'identity': {'asset_tag': 'PC'}}, self.root)
        self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

    def test_concurrent_patches_preserve_fresh_consent_and_unknowns(self):
        settings.save_settings({'completed': True, 'history': True,
                                'unknown': {'keep': True}}, self.root)
        patches = [{'identity': {field: value}} for field, value in
                   zip(settings.IDENTITY_FIELDS, ('Owner', 'Tag', 'Lab', '2026-11-07', 'Tầng 2'))]
        patches += [{'history': False}, {'unknown': {'new': 2}}]
        with ThreadPoolExecutor(max_workers=len(patches)) as pool:
            list(pool.map(lambda value: settings.update_settings(value, self.root), patches))
        value = settings.read_settings(self.root)
        self.assertEqual(value['identity'], dict(zip(settings.IDENTITY_FIELDS,
                                                    ('Owner', 'Tag', 'Lab', '2026-11-07', 'Tầng 2'))))
        self.assertFalse(value['history'])
        self.assertEqual(value['unknown'], {'keep': True, 'new': 2})

    def test_real_consent_and_explicit_field_selection(self):
        settings.save_settings({'completed': True, 'history': True}, self.root)
        value = settings.read_settings(self.root)
        self.assertTrue(settings.export_allowed(value, 'history'))
        self.assertFalse(settings.export_allowed(value, 'identity'))
        self.assertFalse(settings.export_allowed(value, 'system'))
        for field in settings.IDENTITY_FIELDS:
            self.assertTrue(settings.export_allowed(value, 'identity', field))
        for field in settings.SYSTEM_FIELDS:
            self.assertFalse(settings.export_allowed(value, 'system', field))
        value = settings.update_settings({'resources': True}, self.root)
        for field in settings.SYSTEM_FIELDS:
            self.assertTrue(settings.export_allowed(value, 'system', field))
        self.assertFalse(settings.export_allowed(value, 'identity', 'unknown'))
        self.assertFalse(settings.export_allowed(value, 'unknown'))
        for completed in (False, 1, 'yes', None):
            for category, field in (('history', None), ('identity', 'asset_tag'),
                                    ('system', 'machine')):
                self.assertFalse(settings.export_allowed(dict(value, completed=completed),
                                                         category, field))
        value = settings.update_settings({'history': False}, self.root)
        self.assertFalse(settings.export_allowed(value, 'history'))

    def test_explicit_export_vetoes_survive_roundtrip(self):
        for policy in ({'export': False}, {'exports': False},
                       {'export': {'identity': False}},
                       {'exports': {'identity': {'asset_tag': False}}},
                       {'consent': {'export': False}}):
            # Start a fresh real settings file for each policy.
            self.path.write_text(json.dumps(dict(completed=True, resources=True, **policy)))
            value = settings.update_settings({'identity': {'asset_tag': 'A'}}, self.root)
            self.assertFalse(settings.export_allowed(value, 'identity', 'asset_tag'))

    def test_report_gate_and_explicit_veto(self):
        self.assertFalse(settings.export_allowed(settings.defaults(), 'report'))
        self.assertTrue(settings.export_allowed({'completed': True}, 'report'))
        for policy in (False, {'report': False}, {'enabled': False}):
            self.assertFalse(settings.export_allowed({'completed': True, 'export': policy},
                                                     'report'))


if __name__ == '__main__':
    unittest.main()
