"""Guard the shared control contract for both native editions."""
from pathlib import Path
import re
import unittest

import lifetime_theme as theme


class ThemeTests(unittest.TestCase):
    def test_complete_shared_controls_and_radius_ownership(self):
        sheet = theme.controls()
        rules = {}
        for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', sheet):
            for selector in selectors.split(','):
                rules.setdefault(selector.strip(), []).append(body)

        def rule(selector):
            self.assertIn(selector, rules)
            return ' '.join(rules[selector])

        for kind in ('QComboBox', 'QDateEdit'):
            body = rule(kind + '::drop-down')
            for declaration in ('border: none', 'background: transparent', 'width:', 'position: absolute', 'subcontrol-position:'):
                self.assertIn(declaration, body)
            for state in ('hover', 'focus'):
                rule(kind + '::drop-down:' + state)
        for kind in ('QSpinBox', 'QDoubleSpinBox', 'QDateEdit'):
            for direction in ('up', 'down'):
                body = rule(f'{kind}::{direction}-button')
                for declaration in ('border: none', 'background: transparent', 'width:', 'position: absolute', 'subcontrol-position:'):
                    self.assertIn(declaration, body)
                for state in ('hover', 'focus'):
                    rule(f'{kind}::{direction}-button:{state}')
        for kind in ('QComboBox', 'QDateEdit', 'QSpinBox', 'QDoubleSpinBox'):
            for direction in (('down',) if kind == 'QComboBox' else ('up', 'down')):
                body = rule(f'{kind}::{direction}-arrow')
                for declaration in ('image: none', 'width: 0', 'height: 0', 'border-left: 4px solid transparent', 'border-right: 4px solid transparent'):
                    self.assertIn(declaration, body)
                self.assertIn(f'border-{"bottom" if direction == "up" else "top"}: 5px solid', body)
                for state in ('hover', 'focus', 'disabled'):
                    rule(f'{kind}::{direction}-arrow:{state}')
        for orientation, dimension in (('vertical', 'height'), ('horizontal', 'width')):
            for part in ('add-line', 'sub-line'):
                self.assertIn(f'{dimension}: 0', rule(f'QScrollBar::{part}:{orientation}'))
            for part in ('add-page', 'sub-page'):
                self.assertIn('background: transparent', rule(f'QScrollBar::{part}:{orientation}'))
            self.assertIn('border-radius:', rule(f'QScrollBar::handle:{orientation}'))
        for selector in ('QAbstractScrollArea::corner', 'QCalendarWidget', 'QCalendarWidget QAbstractItemView',
                         'QMenu', 'QMenu::item:selected', 'QToolTip'):
            rule(selector)
        self.assertIn('border-radius: 0', rule('QToolTip'))
        self.assertEqual((theme.CONTROL_RADIUS, theme.PANEL_RADIUS), (8, 10))

        root = Path(__file__).parent
        for path in root.glob('lifetime*.py'):
            source = path.read_text(encoding='utf-8')
            if path.name != 'lifetime_theme.py':
                self.assertNotRegex(source, r'border-(?:[\w-]+-)?radius\s*:', path.name)
            else:
                # Only circular radio dots and 4px meter caps may depart from
                # box tokens. A square tooltip is required by Qt's clipping.
                allowed = {'CONTROL_RADIUS', 'PANEL_RADIUS', 'SOFT_RADIUS', 'RADIO_RADIUS', 'METER_RADIUS', '0'}
                for value in re.findall(r'border-radius:\s*([^;]+)', source):
                    self.assertIn(value.strip().removesuffix('px').strip('{}'), allowed)
        base = theme.stylesheet()
        self.assertNotRegex(base, r'QWidget\s*\{[^}]*background:\s*#[0-9A-Fa-f]')
        for name in ('lifetime_gui.py', 'lifetime_server_gui.py', 'lifetime_setup.py', 'lifetime_technician.py'):
            self.assertIn('stylesheet()', (root/name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
