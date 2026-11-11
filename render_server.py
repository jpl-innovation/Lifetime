"""Render synthetic Lifetime Server fixtures using the real Windows Qt backend.

No customer directories, services, firewall or outbound network. The runtime and
admin client communicate only over 127.0.0.1. Fixture ACL calls are mocked only in
this rendering utility, exactly as in the tests. Nothing ships as demo UI data.
"""
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

import lifetime_permissions as permissions
import lifetime_server_store as storage
from lifetime_server import ServerRuntime
from lifetime_server_gui import ServerWindow

ROOT = Path(__file__).parent.resolve()


def populate(store, fingerprint, count=20, detailed=False):
    """All names and measurements are deliberately synthetic."""
    now = dt.datetime.now(dt.timezone.utc)
    day = now.astimezone().date()
    identifiers = []
    for index in range(count):
        issued = store.create_invitation(fingerprint)
        request = dict(version=1, request_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                       code=issued['code'], poll_token=secrets.token_urlsafe(32),
                       identity=dict(display_name=f'Demo-PC-{index+1:02}', asset_tag=f'DEMO-{index+1:03}',
                                     department=('Engineering', 'Office', 'Operations')[index % 3]),
                       policy=dict(history=True, resources=detailed, **({'control': True, 'health': True} if detailed else {})))
        identifiers.append(request['device_id'])
        with patch.object(storage, '_now', return_value=now - dt.timedelta(hours=4, minutes=index)):
            store.enroll(request)
        if index >= count-2:
            continue
        store.approve(request['request_id'])
        credential = store.claim(request['request_id'], request['poll_token'])['credential']
        daily = {(day-dt.timedelta(days=i)).isoformat(): (3+(index+i*3) % 8)*3600 + index*60 for i in range(30)}
        observed = now - dt.timedelta(hours=2) if index in (3, 7) else now-dt.timedelta(seconds=8+index*2)
        specs = dict(manufacturer='Demo manufacturer', model='Demo workstation', cpu_name='Example 8-core CPU',
                     cpu_cores=8, cpu_threads=16, ram_bytes=16 * 1024**3, gpu_names=['Example graphics'],
                     os_name='Windows', os_version='11', mac_addresses=['02:00:00:00:00:01'],
                     disks=[dict(name='C:', total_bytes=512 * 1024**3, free_bytes=(24 if index == 1 else 180) * 1024**3)],
                     battery_design_wh=60)
        def readings(stamp, sample):
            return dict(observed_at=stamp.isoformat(), specs=specs,
                        performance=dict(cpu_percent=12 + (sample * 13 + index) % 65,
                                         ram_used_bytes=(5 + sample % 4) * 1024**3,
                                         disk_free_bytes=(24 if index == 1 else 180) * 1024**3,
                                         battery_percent=82 - sample, charging=False, temperature_c=45 + sample))
        if detailed:
            for sample in range(6):
                stamp = observed - dt.timedelta(minutes=(6 - sample) * 10)
                with patch.object(storage, '_now', return_value=stamp):
                    store.ingest(request['device_id'], credential, dict(version=1, device_id=request['device_id'],
                                 policy_version=1, policy=request['policy'], records=[], readings=readings(stamp, sample)))
        history = dict(daily=daily, recording_started=(now-dt.timedelta(days=60)).isoformat())
        health = None
        if detailed:
            weekdays_hours = [0.0]*24
            for hour in range(24):
                weekdays_hours[hour] = 3600*[0, 0, 0, 0, 0, 0, .1, .4, .8, 1, 1, .9, .7, .9, 1, 1, .9, .6, .4, .5, .6, .4, .2, .05][hour]*len(daily)/2
            history.update(hourly=weekdays_hours, boots=120+index, current_session_seconds=5400+index*60,
                           longest_session_seconds=86400*2+index*600,
                           sessions=[dict(start=(now-dt.timedelta(hours=7*i+3)).astimezone().isoformat(), seconds=300+(i*i*337+index*91) % 40000) for i in range(60)])
            usage = {(day-dt.timedelta(days=i)).isoformat(): dict(ac=3600*(2+i % 5), battery=3600*(1+(i+index) % 4)) for i in range(60)}
            nights = [dict(date=(day-dt.timedelta(days=i)).isoformat(), seconds=3600*(5+(i+index) % 4)) for i in range(1, 45)]
            health = dict(version=1, observed_at=observed.isoformat(), shutdowns_30d=index % 3, crashes_30d=1 if index % 4 == 0 else 0,
                          battery_health=88.5-index, cycle_count=240+index*7,
                          last_crash=(observed-dt.timedelta(days=5)).isoformat() if index % 4 == 0 else None,
                          detail=dict(stability=[dict(date=(observed-dt.timedelta(days=9*i+2)).isoformat(), kind='Crash' if i % 3 == 0 else 'Unexpected shutdown') for i in range(index % 6+3)],
                                      battery=dict(packs=[dict(full=50.0-index*.4, design=60.0, cycles=240+index*7, chemistry='LION', unit='Wh', health=88.5-index)],
                                                   history=[dict(date=(day-dt.timedelta(days=7*i)).isoformat(), full=55.0-i*.15-index*.3) for i in range(51, -1, -1)], usage=usage),
                                      sleep=dict(daily={n['date']: dict(cycles=1+int(n['seconds']) % 3, seconds=n['seconds']) for n in nights}, nights=nights)))
        with patch.object(storage, '_now', return_value=observed):
            store.ingest(request['device_id'], credential, dict(version=1, device_id=request['device_id'],
                         policy_version=1, policy=request['policy'], client_version='1.2.0',
                         records=[dict(seq=1, observed_at=observed.isoformat(), awake_seconds=3600,
                                       measured_seconds=3600, imported_seconds=0,
                                       totals=dict(measured_seconds=(200+index*37)*3600, imported_seconds=0, baseline_seconds=0))],
                         history=history,
                         **({'readings': dict(readings(observed, 6), health=health), 'health': {k: v for k, v in health.items() if k != 'detail'}} if detailed else {})))
    return identifiers


def settle(app, window):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        app.processEvents()
        if not window._jobs and not window._fleet_timer.isActive() and not window._readings_timer.isActive() and not window._search_timer.isActive():
            app.processEvents()
            return
        time.sleep(.01)
    raise RuntimeError('Dashboard did not settle')


def main():
    if sys.platform != 'win32':
        raise SystemExit('These reference captures require Windows and real Windows fonts.')
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ['LIFETIME_REDUCE_MOTION'] = '1'
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.Floor)
    app = QApplication([])
    app.setFont(QFont('Segoe UI', 11))
    if 'Segoe UI' not in QFontDatabase.families():
        raise RuntimeError('Real Segoe UI fonts are required')
    destination = ROOT / 'shots/server-p2a'
    destination.mkdir(parents=True, exist_ok=True)
    captures = []
    with tempfile.TemporaryDirectory(prefix='server-demo-', dir=ROOT) as directory, patch.object(permissions, 'windows_acl'), patch.object(socket, 'gethostname', return_value='Demo-Server'):
        with contextlib.ExitStack() as stack:
            runtime = ServerRuntime(directory, address='https://127.0.0.1:8443', host='127.0.0.1', port=0)
            runtime.start()
            stack.callback(runtime.stop)
            port = urlsplit(runtime.admin_address).port
            window = ServerWindow(directory, port)
            window.timer.stop()
            window.show()
            settle(app, window)
            for language in ('en', 'vi'):
                window.language_button.set_index(0 if language == 'en' else 1, True)
                settle(app, window)
                name = f'empty-{language}-1600x1000.png'
                window.grab().save(str(destination/name))
                captures.append(name)
            populate(runtime._store, runtime.fingerprint, detailed=True)
            window.refresh()
            settle(app, window)
            window.tabs.setCurrentIndex(1)
            window.table.selectRow(7)
            settle(app, window)
            audit_selected = window.selected_device_ids()
            window.device_order.setCurrentIndex(window.device_order.findData('name'))
            # Include the search debounce in the rendering settle loop.
            settle(app, window)
            window.table.setFocus()
            window.table.verticalScrollBar().setValue(4)
            audit_scroll = window.table.verticalScrollBar().value()
            audit_edit = window.identity_fields['display_name']
            audit_edit.setText('Unsaved render audit')
            audit_edit.textEdited.emit(audit_edit.text())
            window.refresh()
            settle(app, window)
            selection_audit = dict(expected=audit_selected, selected=window.selected_device_ids(),
                                   edit=audit_edit.text(), scroll_before=audit_scroll,
                                   scroll_after=window.table.verticalScrollBar().value())
            if window._detail:
                window._render_detail(window._detail)
            window.device_order.setCurrentIndex(window.device_order.findData('problem'))
            settle(app, window)
            for language in ('en', 'vi'):
                window.language_button.set_index(0 if language == 'en' else 1, True)
                window.resize(1600, 1000)
                window.tabs.setCurrentIndex(0)
                settle(app, window)
                window.fleet_table.selectRow(2)
                settle(app, window)
                for width, height in ((1600, 1000), (1280, 800), (1024, 700), (2560, 1392)):
                    window.resize(width, height)
                    settle(app, window)
                    name = f'overview-{language}-{width}x{height}.png'
                    window.grab().save(str(destination/name))
                    captures.append(name)
                window.resize(1600, 1000)
                for tab, name in ((1, 'devices'), (2, 'administration'), (3, 'this-server')):
                    window.tabs.setCurrentIndex(tab)
                    if tab == 1:
                        window.table.selectRow(0)
                    for width, height in ((1600, 1000), (1280, 800), (1024, 700), (2560, 1392)):
                        window.resize(width, height)
                        settle(app, window)
                        file = f'{name}-{language}-{width}x{height}.png'
                        window.grab().save(str(destination/file))
                        captures.append(file)
                        scroll = window.tabs.widget(tab)
                        if tab == 1:
                            scroll.verticalScrollBar().setValue(window.device_detail_panel.y() - 16 if width < 1200 else scroll.verticalScrollBar().maximum())
                            suffix = 'detail'
                        else:
                            scroll.verticalScrollBar().setValue(window.retention_date.parentWidget().y() - 16 if tab == 2 else 0)
                            suffix = 'settings'
                        settle(app, window)
                        extra = f'{name}-{language}-{width}x{height}-{suffix}.png'
                        window.grab().save(str(destination/extra))
                        captures.append(extra)
                        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                        settle(app, window)
                        extra = f'{name}-{language}-{width}x{height}-bottom.png'
                        window.grab().save(str(destination/extra))
                        captures.append(extra)
                        scroll.verticalScrollBar().setValue(0)
                window.resize(1600, 1000)
                window.add_device()
                settle(app, window)
                file = f'add-device-{language}.png'
                window.add_device_sheet.grab().save(str(destination/file))
                captures.append(file)
                window.add_device_sheet.close()
            window.tabs.setCurrentIndex(0)
            window.close()
            settle(app, window)
    (destination/'capture.json').write_text(json.dumps(dict(platform=app.platformName(), font='Segoe UI',
        data='Synthetic Demo-PC fixtures only; temporary store deleted after capture',
        selection_audit=selection_audit, screenshots=captures), indent=2), encoding='utf-8')
    print('\n'.join(str(destination/name) for name in captures))


if __name__ == '__main__':
    main()
