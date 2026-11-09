"""Light / dark / auto themes: complete palettes, a saved preference, re-tinting, and no leftover dark-only colours."""
import json
import re
import tempfile
import unittest
from pathlib import Path

import lifetime_settings as settings
import lifetime_theme as theme


class ThemeModeTests(unittest.TestCase):
    def tearDown(self):
        theme.set_theme('dark')

    def test_both_palettes_define_every_token(self):
        self.assertEqual(set(theme.THEMES['dark']), set(theme.THEMES['light']))

    def test_active_theme_switches_tokens_and_ignores_unknown_names(self):
        theme.set_theme('light')
        self.assertEqual(theme.tokens()['bg'], theme.THEMES['light']['bg'])
        self.assertEqual(theme.Tokens()['text'], theme.THEMES['light']['text'])
        theme.set_theme('neon')
        self.assertEqual(theme.active_theme(), 'dark')

    def test_resolve_theme_honours_explicit_choices(self):
        self.assertEqual(theme.resolve_theme('light'), 'light')
        self.assertEqual(theme.resolve_theme('dark'), 'dark')
        self.assertIn(theme.resolve_theme('auto'), ('light', 'dark'))
        self.assertIn(theme.resolve_theme('nonsense'), ('light', 'dark'))

    def test_dark_literals_are_retinted_only_in_light_mode(self):
        text = 'color: #9FB0CC; background: #0B1426; border: 1px solid #123456'
        self.assertEqual(theme.T(text), text)          # dark mode: unchanged
        theme.set_theme('light')
        light = theme.T(text)
        self.assertIn(theme.THEMES['light']['muted'], light)
        self.assertIn(theme.THEMES['light']['bg'], light)
        self.assertIn('#123456', light)                # unknown colours are left alone
        self.assertNotIn('#9FB0CC', light)

    def test_every_dark_literal_maps_to_an_existing_token(self):
        for literal, token in theme._DARK_LITERALS.items():
            self.assertIn(token, theme.THEMES['light'], literal)

    def test_theme_preference_is_validated_and_saved_without_consent(self):
        self.assertEqual(settings.defaults()['theme'], 'auto')
        self.assertEqual(settings.normalize({'theme': 'light'})['theme'], 'light')
        self.assertEqual(settings.normalize({'theme': 'purple'})['theme'], 'auto')
        with tempfile.TemporaryDirectory() as directory:
            saved = settings.update_settings({'theme': 'dark'}, directory)
            self.assertEqual(saved['theme'], 'dark')
            self.assertFalse(saved['completed'])        # choosing a theme never grants tracking consent
            self.assertEqual(settings.load_settings(directory)['theme'], 'dark')

    def test_no_dark_only_colour_literals_remain_in_the_window_modules(self):
        """Surfaces must use tokens (or T(...)) so light mode stays readable."""
        sources = ['lifetime_shell.py', 'lifetime_charts.py', 'lifetime_connect.py', 'lifetime_health_gui.py']
        for name in sources:
            for number, line in enumerate(Path(name).read_text(encoding='utf-8').splitlines(), 1):
                code = line.split('#', 1)[0] if '#' in line and "'#" not in line and '"#' not in line else line
                if re.search(r"QColor\('#(?:0B1426|12203A|08101F|1B2A46|34445E)'", code):
                    self.fail(f'{name}:{number} hard-codes a dark surface colour: {line.strip()}')


if __name__ == '__main__':
    unittest.main()
