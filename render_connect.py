"""Capture all Connect states with fake local status and no network/agent calls."""
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
os.environ['LIFETIME_REDUCE_MOTION'] = '1'
os.environ['QT_SCALE_FACTOR'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QScrollArea
from qt_test_support import qt_app
from lifetime_i18n import set_language
from lifetime_shell import Sidebar, PulseDot
from lifetime_connect import ConnectPage
from lifetime_gui import Window, segments
from lifetime_i18n_qt import QLabel
from test_company_gui import FakeTasks


def main():
    app = qt_app()
    output = Path(__file__).resolve().parent/'shots/connect'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as root, patch('lifetime_connect.Tasks', FakeTasks):
        for language in ('en', 'vi'):
            set_language(language)
            window = QWidget()
            window.resize(1280, 800)
            window.setObjectName('Window')
            # Reuse the app's exact theme; apply_theme only needs setStyleSheet.
            window.hero = Mock()
            window.baseline = Mock()
            Window.apply_theme(window)
            window.setStyleSheet(window.styleSheet() + 'QWidget#Window { background: #0B1220; }')
            outer = QHBoxLayout(window)
            outer.setContentsMargins(0, 0, 0, 0)
            language_control = segments(['EN', 'VI'], lambda index: None)
            language_control.group.button(('en', 'vi').index(language)).setChecked(True)
            sidebar = Sidebar(lambda index: None, lambda: None, lambda: True,
                              language_control, QLabel('Lifetime'), PulseDot(reduce_motion=True))
            sidebar.group.button(5).setChecked(True)
            sidebar.group.button(5).setFocus()
            outer.addWidget(sidebar)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = ConnectPage(directory=root, machine_root=root, language=language)
            page.timer.stop()
            page.tasks.resolve(dict(enabled=False))
            page.server_name = 'Office Server'
            page.server_address = 'https://office-server:8443'
            page.review_title.setText(page.t('Connected to {server}', server=page.server_name))
            page.review_address.setText(page.server_address)
            page.waiting_text.setText(page.t('{server} needs to approve this computer.', server=page.server_name))
            page.status = dict(enabled=True, phase='approved', status='connected',
                               policy=dict(history=True, resources=True, health=False, control=False), last_sync='2026-11-08 15:30')
            page.render_status()
            scroll.setWidget(page)
            outer.addWidget(scroll, 1)
            window.show()
            page.set_stage(3)
            for step in range(1, 6):
                page.show_step(step)
                app.processEvents()
                scroll.verticalScrollBar().setValue(0)
                assert window.size().width() == 1280 and window.size().height() == 800
                assert window.grab().save(str(output/f'{language}-step-{step}.png'))
            window.close()
            window.deleteLater()
            app.processEvents()
    print(output)


if __name__ == '__main__':
    main()
