"""Native, loopback-only Lifetime Server dashboard. All I/O runs in Tasks.

The window consumes AdminClient results and never opens SQLite. All English/Vietnamese text uses the shared application catalogue.
"""
import argparse
import datetime as dt
import html
import socket
from pathlib import Path
import sys
import time

from PyQt6.QtCore import QDate, QEvent, QItemSelectionModel, QLocale, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap
from PyQt6 import sip
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit,
                            QFileDialog, QFormLayout, QGridLayout, QFrame,
                            QHBoxLayout, QLineEdit, QMainWindow,
                            QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QHeaderView,
                            QSizePolicy, QSpinBox, QMenu, QTableWidget,
                            QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)
from lifetime_i18n_qt import PlainLabel as QLabel

from lifetime_admin import AdminClient, REFUSALS
from lifetime_server_access_ui import AccessUI
import lifetime_i18n as catalog
from lifetime_protocol import CLIENT_VERSION, IDENTITY_FIELDS, validate_identity
from lifetime_theme import THEMES, stylesheet, server_surfaces, install, configure_high_dpi, T, tokens, set_theme, resolve_theme, active_theme, retint_all, CONTROL_RADIUS
import json
import os
from lifetime_layout import ControlRow
from lifetime_tasks import Tasks
from lifetime_tls import encode_bundle
from lifetime_connection import CHECK_WORDS, check_lines, log_tail, report
from lifetime_server_insights import InsightsUI
from lifetime_server_alerts import AlertsUI
from lifetime_server_uptime import UptimeUI
from lifetime_server_widgets import CycleButton, AddDeviceSheet, DailyChart, GroupField, DeviceDetailsDialog, GroupsDialog, local_expiry, reduced_motion, table_action
from lifetime_reveal import RollLabel, fade_in, later, warm_pages
from lifetime_server_devices import (glass, Glass, STYLES, set_style, active_style, ServerPages, DeviceBox, problems, byte_text, Skeleton,
                                     LoadingBar, StatusNotice, TileIcon, FleetGauge, RowDelegate)
from lifetime_charts import Plot, Insights
from lifetime_health_gui import HealthPage
from lifetime_system_gui import HardwarePanel
from lifetime_widgets_dashboard import VISUAL_ROLE, CapacityBar, EmptyState, EventRail, HeartbeatBar, InfoCard, StatusChip, SummaryStrip, interval_ticks
from lifetime_shell import ModeSwitch, draw_mode_icon, PulseDot, install_tray, shut_down, tray_available, tell_running_instance, themed_logo, theme_reveal

SERVER_TEXT_VI = catalog.SERVER_DASHBOARD_VI
RETAIN_CONFIRM = ('Delete everything recorded before this date permanently? This removes, from before the date: usage '
                  'records, daily awake and off-time totals, readings, monitoring history, resolved incidents, sent '
                  'alerts, maintenance windows and daily readings. It keeps each computer\'s latest totals (with their '
                  'original time), open incidents and everything from the date on. This cannot be undone.')
THEME_KEYS = ('auto', 'light', 'dark')
# Why a computer's last report failed (its own category, or what the server saw), for the device detail.
PROBLEM_TEXT = {'certificate': 'Server certificate not trusted', 'expired': 'Certificate out of date or wrong clock',
                'hostname': 'Address not in the certificate', 'tls': 'Secure connection failed',
                'name': 'Server name not found', 'refused': 'Connection refused', 'timeout': 'No answer (timeout)',
                'dropped': 'Connection interrupted', 'network': 'No network path', 'protocol': 'Reply not understood',
                'internal': 'Internal error on the computer', 'rejected': 'Report rejected by the server',
                'conflict': 'Report conflicts with stored data'}
LOG_TEXT = {('server-started', None): 'Server started', ('server-stopped', None): 'Server stopped',
            ('names-changed', None): 'Server names changed',
            ('certificate-renewed', 'expiry'): 'Certificate renewed (before expiry)',
            ('certificate-renewed', 'names'): 'Certificate renewed (new names)',
            ('certificate-renewal-failed', None): 'Certificate renewal failed',
            ('report-rejected', 'invalid'): 'Report refused: invalid data',
            ('report-rejected', 'credential'): 'Report refused: not a connected computer',
            ('report-rejected', 'conflict'): 'Report refused: conflicts with stored data',
            ('report-rejected', 'throttled'): 'Report refused: too many attempts',
            ('enrollment-refused', 'invalid'): 'Connection refused: invalid request',
            ('enrollment-refused', 'code'): 'Connection refused: wrong or used code',
            ('enrollment-refused', 'conflict'): 'Connection refused: conflicting request',
            ('enrollment-refused', 'throttled'): 'Connection refused: too many attempts',
            ('admin-pipe-unavailable', None): 'Named administration channel unavailable (token only)',
            ('restore-applied', None): 'Backup restored at start',
            ('restore-failed', None): 'Staged backup refused at start (kept as restore-failed.zip)'}

# The rail dot of a server log event: green for a healthy step, amber for a refused request, red for a failure.
LOG_KIND = {'server-started': 'good', 'certificate-renewed': 'good', 'restore-applied': 'good',
            'server-stopped': 'neutral', 'names-changed': 'info',
            'report-rejected': 'warning', 'enrollment-refused': 'warning', 'admin-pipe-unavailable': 'warning',
            'certificate-renewal-failed': 'danger', 'restore-failed': 'danger'}


def _create_bundle(client, directory, address, ttl, uses):
    # Read/validate public trust material before consuming a new pairing code.
    with (directory / 'server-cert.pem').open('rb') as source:
        certificate = source.read(16385).decode('ascii')
    encode_bundle(address, 'validate-address', certificate)
    issued = client.call('code', ttl=ttl, uses=uses)
    return dict(bundle=encode_bundle(address, issued['code'], certificate), expires_at=issued['expires_at'])


def text(key, language='en', **values):
    if key in catalog.EN:
        value = catalog.TABLES[language][key]
    else:
        value = SERVER_TEXT_VI.get(key, key) if language == 'vi' else key
    return value.format(**values)


# Copy diagnostics: only these named fields of server_info (never the admin port, hardware serials or alert settings).
SAFE_INFO = ('server_version', 'os', 'name', 'organisation', 'address', 'host', 'port', 'started_at', 'uptime',
             'reporting_interval', 'device_counts', 'discovery', 'database_size', 'fingerprint', 'server_names')


# Why Self check gave no lines: (cause, what to do). Only a server that does not know the operation is too old.
SELF_CHECK_PROBLEMS = {
    'unsupported': ('Self check could not run.', 'Update Lifetime Server: versions before 1.3.0 have no self check. Then try again.'),
    'timeout': ('Self check did not finish in time.',
                'Try again in a minute. If it keeps happening, the server may be very busy, or a name lookup or the firewall '
                'tools on it answer very slowly.'),
    'denied': ('Your role does not allow Self check.', 'Ask an administrator of this server for a role (Viewer or higher).'),
    'failed': ('Self check could not run.', 'Check that Lifetime Server is running, then try again. The server log may say more.'),
}


def self_check_problem(error):
    """The SELF_CHECK_PROBLEMS key for an error from AdminClient.call('self_check')."""
    from lifetime_admin import NotAllowed
    if isinstance(error, NotAllowed):
        return 'denied'
    if isinstance(error, TimeoutError):
        return 'timeout'
    if type(error) is ValueError and 'HTTP 404' in str(error):      # the operation is unknown to this server
        return 'unsupported'
    return 'failed'


def server_logs():
    """The Linux server's own log file, used only when it exists (the systemd service logs to the journal)."""
    return ['/var/log/lifetime-server.log'] if sys.platform.startswith('linux') else []


def diagnostics_text(t, info, check=None, log=(), logs=None):
    """Copy diagnostics for the server: versions, system, settings that are not secret, Self check and the last server
    log events without computer names (and log file lines). Named fields only, and the whole text is scrubbed. Reads
    files: call it from a worker."""
    import platform
    certificate = info.get('certificate') or {}
    sections = [(t('Lifetime diagnostics'), [f'Lifetime Server {CLIENT_VERSION} ({t("server")})',
                                             f'{platform.platform()} · Python {platform.python_version()}',
                                             dt.datetime.now().astimezone().isoformat(timespec='seconds')]),
                (t('Server'), [f'{key}: {info[key]}' for key in SAFE_INFO if key in info] +
                 [f'certificate {key}: {certificate[key]}' for key in ('expires', 'names', 'warning', 'renewed_at') if certificate.get(key)]),
                (t('Self check'), check_lines(check['checks'], t) if check else [t('Self check could not run.')]),
                (t('Server log'), [' '.join(str(event[key]) for key in ('time', 'kind', 'category', 'count') if event.get(key))
                                   for event in log or []])]
    for name, lines in log_tail(server_logs() if logs is None else logs):
        sections.append((t('Log: {name}, last {count} lines', name=name, count=len(lines)), lines))
    return report(sections)


class ElidedLabel(QLabel):
    """One line that ends in '…' when it has less room than its text, with the whole text as the tooltip. The header
    subtitle gets only what the brand column has left: 'Demo-Server · Mạng nội bộ' was cut to 'Mạng nộ'."""
    def __init__(self):
        super().__init__()
        self.full = ''

    def setText(self, text):
        self.full = str(text)
        self._elide()

    def text(self):
        return self.full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(shown)
        self.setToolTip(self.full if shown != self.full else '')


class _Item(QTableWidgetItem):
    def __lt__(self, other):
        left, right = self.data(Qt.ItemDataRole.UserRole + 1), other.data(Qt.ItemDataRole.UserRole + 1)
        return (left is None, left) < (right is None, right)


class ServerWindow(QMainWindow):
    export_requested = pyqtSignal(object)
    tray = None
    CONTENT_WIDTH = 1920     # wide screens get a wider column; narrower windows simply fill it

    def __init__(self, directory, admin_port, language='en', parent=None):
        super().__init__(parent)
        if language not in ('en', 'vi'):
            raise ValueError('Unsupported language')
        self.directory, self.language = Path(directory), language
        self.client = AdminClient(directory, admin_port)
        self.tasks = Tasks(self)
        self.tasks.completed.connect(lambda number, result: self._completed(number, result, True))
        self.tasks.failed.connect(lambda number, error: self._completed(number, None, False, error))
        self._jobs = {}
        self._presence_cache = {}       # device id -> (monotonic time, presence rows); written by the list worker only
        self._quiet_jobs = set()
        self._closed = False
        self._revision, self._list_busy, self._list_again = 0, False, False
        self._offset, self._rows, self._detail = 0, [], None
        self._detail_revision = 0
        self._identity_dirty = False
        self._mutation_busy = False
        self._configuration_busy = False
        self._configuration_loaded = False
        self._configuration_dirty = False
        self._overview_busy, self._overview_again, self._overview_result = False, False, None
        self._configuration_result = None
        self._bundle_expiry = None
        self._status_failure = False
        self._bindings = []
        self._fleet_busy, self._fleet_again, self._fleet_revision = False, False, 0
        self._fleet_result, self._fleet_offset, self._fleet_selected = None, 0, None
        self._server_info = None
        self._admin_result = None
        self._invitation_offset = 0
        self._issuing_invitation = False
        self._info_busy = False
        self._info_again = False
        self._device_sort = None
        self._device_matching = 0
        self._device_cache = {}
        self._readings_busy = False
        self._live_busy = False
        self.stream = None
        self._entrance_played, self._overview_data = False, False
        self.tile_icons = []
        self._paint_pending = False
        self._last_poll, self._poll_focus = 0.0, None
        self._live_items = {}
        self._live_ring = []
        self._live_focus = None
        self.setWindowTitle('Lifetime Server')
        self.resize(1600, 1000)
        self.setMinimumSize(420, 400)
        install()
        QApplication.instance().installEventFilter(self)
        set_style(read_style_preference(self.directory))
        self._apply_window_style()
        self.setWindowIcon(QIcon(str(server_icon_path())))
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.chrome = QWidget()
        self.chrome.setObjectName('chrome')
        self.chrome.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.chrome_grid = QGridLayout(self.chrome)
        self.chrome_grid.setContentsMargins(32, 14, 32, 6)
        self.chrome_grid.setHorizontalSpacing(16)
        self.chrome_grid.setVerticalSpacing(8)
        self.chrome_brand = QWidget()
        brand = QHBoxLayout(self.chrome_brand)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(server_icon_path('png'))).scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel('Lifetime Server')
        font = title.font()
        font.setPointSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        names.addWidget(title)
        self.network_label = self.bind(ElidedLabel(), 'Local network')
        self.network_label.setObjectName('muted')
        self.network_label.setWordWrap(False)
        self.network_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        names.addWidget(self.network_label)
        brand.addLayout(names, 1)
        self.chrome_actions = QWidget()
        actions = QHBoxLayout(self.chrome_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        actions.addStretch()
        self.server_pulse = PulseDot(size=24)
        actions.addWidget(self.server_pulse)
        states = QVBoxLayout()
        states.setSpacing(0)
        self.server_status = QLabel(self.t('Server unavailable'))
        self.server_status.setWordWrap(False)
        states.addWidget(self.server_status)
        self.updated_label = QLabel()
        self.updated_label.setObjectName('muted')
        states.addWidget(self.updated_label)
        self.chrome_states = QWidget()
        self.chrome_states.setLayout(states)
        states.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.chrome_states)
        self.refresh_button = self.button('Refresh', self.manual_refresh)
        self.refresh_button.setMinimumWidth(132)
        glass(self.refresh_button, CONTROL_RADIUS, pill=True)
        actions.addWidget(self.refresh_button)
        self.credit = QLabel()
        self.credit.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(150, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.bind(self.credit, 'Made by JPL Innovation', 'setToolTip')
        actions.addWidget(self.credit)
        # Language and theme: one compact glass button each in the header. A click moves to the next value.
        self.chrome_switches = QWidget()
        switches = QHBoxLayout(self.chrome_switches)
        switches.setContentsMargins(0, 0, 0, 0)
        switches.setSpacing(8)
        self.language_button = CycleButton(2, self._draw_language, 0 if language == 'en' else 1, (52, 40), 16)   # a soft rounded rectangle
        self.language_button.cycled.connect(self._language_cycled)
        saved = read_theme_preference(self.directory)
        self.theme_button = CycleButton(3, self._draw_theme, THEME_KEYS.index(saved), (40, 40), 20)      # a circle
        self.theme_button.cycled.connect(self._theme_cycled)
        for button in (self.language_button, self.theme_button):
            glass(button, button.radius, pill=True)
            switches.addWidget(button)
        actions.insertWidget(1, self.chrome_switches)
        self._describe_switches()
        self.style_control = ModeSwitch([self.t('Glass'), self.t('Classic')], lambda index: self._style_changed(STYLES[index]))
        self.style_control.setFixedWidth(260)
        self.style_control.setAccessibleName(self.t('Style'))
        for segment in self.style_control.group.buttons():
            segment.setProperty('glass', True)        # the segments paint themselves; the window must not put glass under them
        self.style_control.group.button(STYLES.index(active_style())).setChecked(True)
        self.tabs = ServerPages()
        layout.addWidget(self.chrome)
        self.loading_bar = LoadingBar()
        layout.addWidget(self.loading_bar)
        self.notice = QWidget()
        self.notice.setObjectName('notice')
        self.notice.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        glass(self.notice, 12, accent='#FFC93C')
        self.notice.setMaximumWidth(self.CONTENT_WIDTH - 64)
        notice_row = QHBoxLayout(self.notice)
        notice_row.setContentsMargins(24, 12, 18, 12)
        self.status_label = StatusNotice(lambda value: value in (self.t('Ready'), self.t('Working…'), self.t('Changes saved.')))
        self.bind(self.status_label, 'Ready')
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName(self.t('Local administration'))
        self.status_label.setObjectName('statusText')
        notice_row.addWidget(self.status_label)
        self.notice.hide()
        notice_holder = QHBoxLayout()
        notice_holder.setContentsMargins(32, 10, 32, 0)
        notice_holder.addWidget(self.notice)
        layout.addLayout(notice_holder)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(body)
        self._kpi_alerts = set()
        self._open_after_load = None
        self.access_ui = AccessUI(self)      # what the signed-in Windows account's role may do
        self.uptime = UptimeUI(self)        # date ranges, uptime and downtime, fleet uptime bars, detail retention
        self._make_overview()
        self._make_devices()
        self.alerts = AlertsUI(self)        # the Incidents page, the header alert badge and the alert settings
        self.insights = InsightsUI(self)    # the Energy page: energy and cost, after hours, replacements, health
        self._make_admin()
        self._make_this_server()
        self.local_server = QLabel(self.t('Local server: {name}', name=socket.gethostname()), body)
        self.local_server.hide()
        self._arrange_chrome()
        self._manual_refresh = False
        self._refresh_started = 0.0
        self._make_skeletons()
        self.export_requested.connect(self._show_export)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._load_devices)
        self._readings_timer = QTimer(self)
        self._readings_timer.setSingleShot(True)
        self._readings_timer.setInterval(120)
        self._readings_timer.timeout.connect(self._load_visible_readings)
        self.table.verticalScrollBar().valueChanged.connect(lambda: self._readings_timer.start())
        self.table.verticalScrollBar().valueChanged.connect(lambda: self._paint_live())
        self.tabs.currentChanged.connect(lambda: self._readings_timer.start())
        self.tabs.currentChanged.connect(self._arm_overview_entrance)
        self._fleet_timer = QTimer(self)
        self._fleet_timer.setSingleShot(True)
        self._fleet_timer.setInterval(250)
        self._fleet_timer.timeout.connect(self._load_fleet)
        self.fleet_search.textChanged.connect(self._fleet_filters_changed)
        self.fleet_group.currentIndexChanged.connect(self._fleet_filters_changed)
        self.fleet_date.dateChanged.connect(self._fleet_filters_changed)
        self.fleet_range.changed.connect(self._fleet_filters_changed)
        self.fleet_table.horizontalHeader().sortIndicatorChanged.connect(self._fleet_filters_changed)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._device_sort_changed)
        self.search.textChanged.connect(self._filters_changed)
        self.group.currentIndexChanged.connect(self._filters_changed)
        self.status_filter.currentIndexChanged.connect(self._filters_changed)
        self.page_size.currentIndexChanged.connect(self._filters_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(1000)
        self.live_timer.timeout.connect(self._load_live)
        self.live_timer.start()
        for button in self.findChildren(QPushButton):     # buttons polished before they joined the window
            self._glass_button(button)
        self.retranslate()
        QTimer.singleShot(0, self.refresh)
        warm_pages([self.tabs.widget(index) for index in range(1, self.tabs.count())], lambda: self.tabs.entrance is not None)
        # Open with nothing focused: a yellow focus outline at launch reads as a second selected state.
        body.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        QTimer.singleShot(0, lambda: body.setFocus(Qt.FocusReason.OtherFocusReason))

    def t(self, key, **values):
        return text(key, self.language, **values)

    def eventFilter(self, watched, event):
        # Three button kinds: amber primary (#primary), quiet text links (#link) and glass for everything else.
        # Every other push button in this window or its dialogs becomes glass when it is first polished.
        if (event.type() == QEvent.Type.Polish and isinstance(watched, QPushButton) and not watched.property('glass')
                and watched.objectName() != 'deviceMenu'):
            parent = watched
            while parent is not None and parent is not self:
                parent = parent.parentWidget()
            if parent is self:
                self._glass_button(watched)
        return False

    @staticmethod
    def _glass_button(button):
        if not button.property('glass') and button.objectName() != 'deviceMenu':
            glass(button, CONTROL_RADIUS, pill=True)

    def bind(self, widget, key, setter='setText'):
        self._bindings.append((widget, key, setter))
        getattr(widget, setter)(self.t(key))
        widget.setAccessibleName(self.t(key))
        return widget

    def label(self, key):
        label = self.bind(QLabel(), key)
        label.setWordWrap(True)
        return label

    def button(self, key, callback):
        widget = self.bind(QPushButton(), key)
        widget.clicked.connect(callback)
        return widget

    def _scroll_page(self, key):
        page, scroll = QWidget(), QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        page.setMaximumWidth(self.CONTENT_WIDTH)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self.tabs.addTab(scroll, self.t(key))
        return layout

    def _make_overview(self):
        layout = self._scroll_page('Overview')
        layout.setContentsMargins(32, 24, 32, 24)
        title_row = ControlRow()
        title = self.label('Fleet overview')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        title_row.addWidget(title)
        self.today_label = QLabel()
        self.today_label.setObjectName('muted')
        title_row.addWidget(self.today_label, 1)
        self._today = QDate.currentDate()
        self.fleet_date = QDateEdit(self._today)
        self.fleet_date.setCalendarPopup(True)
        self.fleet_date.setDisplayFormat('d MMM yyyy')
        self.fleet_date.setMinimumWidth(180)
        self.fleet_date.setAccessibleName(self.t('Report date'))
        title_row.addWidget(self.fleet_date)
        title_row.addWidget(glass(self.button('Export report', lambda: self.export_requested.emit(None)), CONTROL_RADIUS, pill=True))
        self.add_device_button = self.button('Add device', self.add_device)
        self.add_device_button.setObjectName('primary')
        title_row.addWidget(self.add_device_button)
        layout.addLayout(title_row)
        self.tile_grid = QGridLayout()
        self.tile_grid.setSpacing(16)
        self.kpis, self.kpi_notes, self.kpi_titles = {}, {}, {}
        self.tiles = []
        for key, icon in (('Connected', 'system'), ('Offline', 'offline'), ('Awake today', 'clock'), ('Pending approval', 'pending')):
            tile, tile_layout = self._panel()
            tile_layout.setSpacing(4)
            top_row = QWidget()
            top_row.setFixedHeight(44)      # the Review button must not push one tile's numbers out of line
            top = QHBoxLayout(top_row)
            top.setContentsMargins(0, 0, 0, 0)
            tile_icon = TileIcon(icon, delay=len(self.tile_icons) * 90)
            self.tile_icons.append(tile_icon)
            top.addWidget(tile_icon)
            top.addStretch()
            if key == 'Pending approval':
                review = self.button('Review', self.review_pending)
                review.setObjectName('link')
                top.addWidget(review)
            tile_layout.addWidget(top_row)
            tile_layout.addSpacing(8)
            value = RollLabel(self.t('Not available'))
            value.setObjectName('metric')
            value.setMinimumHeight(38)
            self.kpis[key] = value
            tile_layout.addWidget(value)
            heading = self.label(key)
            self.kpi_titles[key] = heading
            heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            tile_layout.addWidget(heading)
            note = QLabel()
            note.setWordWrap(True)
            note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            note.setObjectName('muted')
            self.kpi_notes[key] = note
            tile_layout.addWidget(note)
            self.tiles.append(tile)
        self.fleet_empty = EmptyState('setup')
        self.fleet_empty.clicked.connect(self.add_device)
        layout.addWidget(self.fleet_empty)
        self.fleet_empty.hide()
        self.fleet_grid = QGridLayout()
        self.fleet_grid.setSpacing(16)
        self.fleet_panel, fleet_layout = self._panel()
        filters = ControlRow()
        title = self.label('Devices')
        title.setObjectName('sectionTitle')
        filters.addWidget(title, 1)
        self.fleet_search = QLineEdit()
        self.bind(self.fleet_search, 'Search computers', 'setPlaceholderText')
        self.fleet_search.setMaximumWidth(300)
        filters.addWidget(self.fleet_search)
        self.fleet_group = QComboBox()
        self.fleet_group.setMinimumWidth(150)
        self.fleet_group.addItem(self.t('All groups'), '')
        self.fleet_group.setAccessibleName(self.t('Group'))
        filters.addWidget(self.fleet_group)
        fleet_layout.addLayout(filters)
        self.fleet_table = QTableWidget(0, 5)
        self.fleet_table.setAccessibleName(self.t('Computer list'))
        self._style_table(self.fleet_table)
        self.fleet_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fleet_table.setSortingEnabled(False)  # Sorting happens across the entire store.
        self.fleet_table.horizontalHeader().setSectionsClickable(True)
        self.fleet_table.horizontalHeader().setSortIndicatorShown(True)
        self.fleet_table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.fleet_table.horizontalHeader().sectionClicked.connect(self._fleet_sort_clicked)
        self.fleet_table.itemSelectionChanged.connect(self._fleet_selection_changed)
        self.fleet_table.doubleClicked.connect(self.open_fleet_device)
        self.fleet_table.setMinimumHeight(280)
        fleet_layout.addWidget(self.fleet_table, 1)     # grows so the panel matches the detail panel beside it
        pagination = ControlRow()
        self.fleet_page_label = QLabel()
        self.fleet_page_label.setObjectName('muted')
        pagination.addWidget(self.fleet_page_label, 1)
        self.fleet_previous = self.button('Previous', lambda: self._fleet_page(-1))
        self.fleet_next = self.button('Next', lambda: self._fleet_page(1))
        pagination.addWidget(self.fleet_previous)
        self.fleet_page_number = QLabel('1')
        pagination.addWidget(self.fleet_page_number)
        pagination.addWidget(self.fleet_next)
        fleet_layout.addLayout(pagination)
        self.fleet_detail, detail_layout = self._panel()
        self.fleet_detail_text = QLabel()
        self.fleet_detail_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.fleet_detail_text.setTextFormat(Qt.TextFormat.RichText)
        self.fleet_detail_text.setWordWrap(True)
        self.fleet_detail_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.fleet_detail_text, 0, Qt.AlignmentFlag.AlignTop)
        detail_layout.addStretch()
        self.today_bar = QProgressBar()
        self.today_bar.setRange(0, 86400)
        self.today_bar.setTextVisible(False)
        self.today_bar.setAccessibleName(self.t('Selected date awake time, out of 24 hours'))
        detail_layout.addWidget(self.today_bar)
        self.open_device_button = self.button('Open computer', self.open_fleet_device)
        detail_layout.addWidget(self.open_device_button, 0, Qt.AlignmentFlag.AlignRight)
        self.chart_panel, chart_layout = self._panel()
        chart_header = QHBoxLayout()
        title = self.label('Daily awake hours')
        title.setObjectName('sectionTitle')
        chart_header.addWidget(title, 1)
        self.fleet_range = self.uptime.range_control('7d')      # the period of the fleet totals (lifetime_server_uptime)
        chart_header.addWidget(self.fleet_range)
        chart_layout.addLayout(chart_header)
        self.daily_chart = DailyChart()
        chart_layout.addWidget(self.daily_chart)
        self.fleet_range_total = QLabel()
        self.fleet_range_total.setWordWrap(True)
        chart_layout.addWidget(self.fleet_range_total)
        note = self.label("Known daily contributions only · each computer's local dates")
        note.setObjectName('muted')
        chart_layout.addWidget(note)
        self.events_panel, events_layout = self._panel()
        title = self.label('Recent events')
        title.setObjectName('sectionTitle')
        events_layout.addWidget(title)
        # The latest five events as a rail, newest at the top: what happened in words, then when and which computer.
        self.events = EventRail()
        self.events.setObjectName('fleetEvents')
        self.events.setAccessibleName(self.t('Recent events'))
        self.events.setFixedHeight(5 * EventRail.ROW)       # room for the loading skeleton until the first answer
        events_layout.addWidget(self.events)
        self.events_empty = self.label('No events yet')
        self.events_empty.setObjectName('muted')
        self.events_empty.hide()
        events_layout.addWidget(self.events_empty)
        self.gauge_panel, gauge_layout = self._panel()
        title = self.label('Online now')
        title.setObjectName('sectionTitle')
        gauge_layout.addWidget(title)
        self.fleet_gauge = FleetGauge()
        gauge_layout.addWidget(self.fleet_gauge)
        self.gauge_note = QLabel()
        self.gauge_note.setObjectName('muted')
        self.gauge_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gauge_note.setWordWrap(True)
        gauge_layout.addWidget(self.gauge_note)
        self.overview_left, self.overview_right = QWidget(), QWidget()
        left = QVBoxLayout(self.overview_left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(16)
        left.addLayout(self.tile_grid)
        # The fleet's state in one bar, with a chip per state that opens the matching list.
        self.state_strip = SummaryStrip()
        self.state_strip.picked.connect(self._state_picked)
        self.state_strip.hide()
        left.addWidget(self.state_strip)
        left.addWidget(self.chart_panel)
        left.addWidget(self.fleet_panel)
        left.addWidget(self.uptime.fleet_bars_panel())       # one row of daily availability bars per computer
        left.addStretch()
        right = QVBoxLayout(self.overview_right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)
        for panel in (self.gauge_panel, self.fleet_detail, self.events_panel):
            right.addWidget(panel)
        right.addStretch()
        layout.addLayout(self.fleet_grid)
        self.overview_health = QLabel(self.t('Loading…'))
        self.overview_health.setObjectName('muted')
        self.overview_health.setWordWrap(True)
        self.overview_health.hide()     # only a failure is worth a line here
        layout.insertWidget(1, self.overview_health)     # in the column, under the title, not after the page
        self.certificate_warning = QLabel()
        self.certificate_warning.setWordWrap(True)
        self.certificate_warning.setStyleSheet('color: #FF8A8A; font-weight: 600;')     # the danger colour, re-tinted in light mode
        self.certificate_warning.hide()     # shown only when a renewal failed
        layout.insertWidget(1, self.certificate_warning)
        # Cumulative source breakdown stays available under Administration.
        self.overview_usage = QWidget()
        usage_layout = QVBoxLayout(self.overview_usage)
        self.counts = {}
        for key in ('All computers', 'Online', 'Offline', 'Pending approval', 'Approved', 'Denied', 'Disconnected', 'Unavailable'):
            value = QLabel(self.t('Not available'))
            self.counts[key] = value
            value.setParent(self.overview_usage)
            value.hide()
        usage_layout.addWidget(self.label('Latest cumulative snapshots (hours)'))
        totals_form = QFormLayout()
        self.fleet_totals = {}
        for key in ('Measured', 'Imported', 'Baseline'):
            value = QLabel(self.t('Not available'))
            self.fleet_totals[key] = value
            totals_form.addRow(self.label(key), value)
        usage_layout.addLayout(totals_form)
        note = self.label('One latest snapshot per computer; snapshots are not added over time.')
        note.setWordWrap(True)
        usage_layout.addWidget(note)
        self.snapshot_scope = QLabel()
        self.snapshot_scope.setWordWrap(True)
        usage_layout.addWidget(self.snapshot_scope)
        usage_layout.addStretch()
        layout.addStretch()
        self._arrange_overview()

    def _field(self, key):
        label = self.label(key)
        label.setObjectName('fieldLabel')
        return label

    def _panel(self):
        panel = QWidget()
        panel.setObjectName('panel')
        glass(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        return panel, layout

    def _style_table(self, table):
        table.setProperty('lifetimeBorderless', True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setMinimumSectionSize(32)
        table.verticalHeader().setDefaultSectionSize(44)
        table.horizontalHeader().setMinimumHeight(40)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setMinimumSectionSize(95)
        table.setAlternatingRowColors(False)
        if not table.property('lifetimeCards'):
            table.setItemDelegate(RowDelegate(table))

    def _make_devices(self):
        layout = self._scroll_page('Devices')
        layout.setContentsMargins(32, 24, 32, 24)
        title_row = ControlRow()
        title = self.label('Devices')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        title_row.addWidget(title, 1)
        self.devices_add_button = self.button('Add device', self.add_device)
        self.devices_add_button.setObjectName('primary')
        title_row.addWidget(self.devices_add_button)
        self.export_button = glass(self.button('Export selected', self.export_selection), CONTROL_RADIUS, pill=True)
        title_row.addWidget(self.export_button)
        layout.addLayout(title_row)
        self.search = QLineEdit()
        self.search.setMinimumWidth(220)
        self.bind(self.search, 'Search computers', 'setPlaceholderText')
        self.group = QComboBox()
        self.group.addItem(self.t('All groups'), '')
        self.group.setMaximumWidth(260)       # group names are typed by people and can be long; the list shows them whole
        self.group.setAccessibleName(self.t('Group'))
        self.status_filter = QComboBox()
        for code in ('', 'pending', 'approved', 'denied', 'revoked', 'reporting', 'report-overdue', 'unavailable'):
            self.status_filter.addItem('', code)
        chips = ControlRow()
        self.device_chips = {}
        self._chip = 'all'
        for code, key in (('all', 'All'), ('attention', 'Needs attention'), ('online', 'Online'), ('offline', 'Offline')):
            button = self.button(key, lambda checked=False, c=code: self._set_chip(c))
            button.setCheckable(True)
            button.setChecked(code == 'all')
            self.device_chips[code] = button
            chips.addWidget(button)
        chips.addStretch()
        self.manage_groups_button = self.button('Manage groups', self.manage_groups)
        for widget in (self.search, self.group, self.manage_groups_button, self.status_filter):
            chips.addWidget(widget)
        self.device_order = QComboBox()
        for code, key in (('problem', 'Sort by problem'), ('name', 'Sort by name'), ('last', 'Sort by last seen')):
            self.device_order.addItem(self.t(key), code)
        self.device_order.setAccessibleName(self.t('Sort computers'))
        self.device_order.currentIndexChanged.connect(self._filters_changed)
        chips.addWidget(self.device_order)
        for combo in (self.group, self.status_filter, self.device_order):
            # Wide enough for its longest entry in the current language. The default keeps the width of the first
            # show, so after switching to Vietnamese the status filter read "Tất cả trạng thá"; the row wraps instead.
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        layout.addLayout(chips)
        self.device_grid = QGridLayout()
        self.device_grid.setSpacing(16)
        self.devices_panel, table_layout = self._panel()
        self.empty_label = EmptyState('off', self.t('No computers match'), self.t('No computers match. Choose Add device to connect a computer.'),
                                      self.t('Add device'))
        self.empty_label.clicked.connect(self.add_device)
        table_layout.addWidget(self.empty_label)
        self.table = QTableWidget(0, 5)
        self.table.setProperty('lifetimeCards', True)
        self._style_table(self.table)
        self.table.setAccessibleName(self.t('Devices'))
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.horizontalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setStyleSheet('QTableWidget {border:0; background:transparent;} QTableWidget::item, QTableWidget::item:selected {padding:0; border:0; background:transparent;}')
        for column in range(1, 5):
            self.table.hideColumn(column)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(False)
        table_layout.setContentsMargins(10, 10, 10, 14)
        table_layout.addWidget(self.table, 1)
        table_layout.addSpacing(12)
        pagination = ControlRow()
        pagination.setSpacing(8)
        self.page_label = QLabel()
        self.page_label.setObjectName('muted')
        pagination.addWidget(self.page_label, 1)
        self.previous_button = self.button('Previous', lambda: self._page(-1))
        self.next_button = self.button('Next', lambda: self._page(1))
        pagination.addWidget(self.previous_button)
        self.device_pages = QHBoxLayout()
        self.device_pages.setSpacing(4)
        pagination.addLayout(self.device_pages)
        pagination.addWidget(self.next_button)
        self.page_size = QComboBox()
        for number in (25, 50, 100, 250, 500):
            self.page_size.addItem(str(number), number)
        self.page_size.setMinimumWidth(90)
        pagination.addWidget(self.page_size)
        table_layout.addLayout(pagination)
        self.device_detail_panel, detail_outer = self._panel()
        # Nothing selected: one sentence and nothing else, instead of power buttons and cards full of "Not available".
        self.detail_hint = EmptyState('off')
        detail_outer.addWidget(self.detail_hint)
        self.detail_body = QWidget()
        detail_layout = QVBoxLayout(self.detail_body)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(detail_outer.spacing())
        detail_outer.addWidget(self.detail_body)
        detail_outer.addStretch(1)
        self.detail_name = QLabel()
        self.detail_name.setWordWrap(True)
        self.detail_name.setStyleSheet('font-size: 22px; font-weight: 600;')
        detail_layout.addWidget(self.detail_name)
        status_row = QHBoxLayout()
        self.detail_group, self.detail_status = QLabel(), StatusChip()
        self.detail_group.setObjectName('groupBadge')
        self.detail_group.setStyleSheet('background: #22345A; padding: 3px 8px;')
        status_row.addWidget(self.detail_group)
        status_row.addWidget(self.detail_status)
        status_row.addStretch()
        detail_layout.addLayout(status_row)
        # Online history from monitoring: one tick per hour, newest on the right, so a flapping computer shows.
        self.presence_title = self.label('Last 24 hours')
        self.presence_title.setObjectName('muted')
        self.presence_bar = HeartbeatBar()
        presence_row = QHBoxLayout()
        presence_row.setSpacing(12)
        presence_row.addWidget(self.presence_title)
        presence_row.addWidget(self.presence_bar, 1)
        self.presence_box = QWidget()
        self.presence_box.setLayout(presence_row)
        presence_row.setContentsMargins(0, 0, 0, 0)
        self.presence_box.hide()
        detail_layout.addWidget(self.presence_box)
        # The actions and the reason a button is disabled sit at the top: below the specifications they scrolled out of sight.
        self.control_reason = QLabel()
        self.control_reason.setWordWrap(True)
        self.control_reason.setObjectName('muted')
        self.power_buttons = {}
        self.power_grid = power_grid = QGridLayout()
        for i, (action, key) in enumerate((('sleep', 'power.sleep'), ('restart', 'Restart'), ('shutdown', 'Shut down'), ('wake', 'Turn on'), ('refresh', 'Refresh'))):
            button = self.button(key, lambda checked=False, a=action: self._device_command(self._detail, a))
            self.power_buttons[action] = button
            power_grid.addWidget(button, i // 3, i % 3)
        detail_layout.addLayout(power_grid)
        detail_layout.addWidget(self.control_reason)
        self.spec_title = self.label('Computer specifications')
        self.spec_title.setStyleSheet('font-size:18px; font-weight:600;')
        detail_layout.addWidget(self.spec_title)
        # Specifications as small cards grouped by what a technician looks for, instead of one long list.
        self.spec_values, self.spec_cards, self.spec_keys = {}, {}, {}
        spec_box = QWidget()
        spec_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)     # no spare height into the cards
        spec_grid = QGridLayout(spec_box)
        spec_grid.setContentsMargins(0, 0, 0, 0)
        spec_grid.setSpacing(12)
        groups = (('system', 'Computer', ('Manufacturer', 'Model', 'Operating system')),
                  ('cpu', 'CPU', ('CPU', 'Cores / threads')),
                  ('memory', 'Memory & graphics', ('RAM', 'Graphics', 'Battery design capacity')),
                  ('storage', 'Storage', ('Storage',)))
        for index, (icon, title, keys) in enumerate(groups):
            card = InfoCard(icon, self.t(title))
            self.bind(card.title, title)        # follows the language switch
            for key in keys:
                value = QLabel()
                self.spec_values[key] = value
                self.spec_keys[key] = self.bind(card.add_row(self.t(key), value), key)
            self.spec_cards[title] = card
            spec_grid.addWidget(card, index // 2, index % 2)
        self.storage_bars = []
        detail_layout.addWidget(spec_box)
        live_header = QHBoxLayout()
        self.live_marker = QLabel()
        live_header.addWidget(self.live_marker, 1)
        self.performance_range = QComboBox()
        self.performance_range.addItem(self.t('Live'), 'live')
        self.performance_range.addItem(self.t('24 hours'), 'history')
        self.performance_range.setAccessibleName(self.t('Performance range'))
        self.performance_range.currentIndexChanged.connect(lambda: self._render_specs(self._detail) if self._detail else None)
        live_header.addWidget(self.performance_range)
        detail_layout.addLayout(live_header)
        self.performance_plots = {}
        for field, key, unit in (('cpu_percent', 'CPU', '%'), ('ram_percent', 'RAM', '%'), ('disk_free_percent', 'Disk free', '%'),
                                 ('battery_percent', 'Battery', '%'), ('temperature_c', 'Temperature', '°C')):
            detail_layout.addWidget(self.label(key))
            plot = Plot('line', unit)
            plot.bar_color = None
            plot.setFixedHeight(155)
            self.performance_plots[field] = plot
            detail_layout.addWidget(plot)
        self.command_status = QLabel()
        self.command_status.setWordWrap(True)
        detail_layout.addWidget(self.command_status)
        self.detail_total = QLabel()
        self.detail_total.setStyleSheet('font-size: 32px; font-weight: 600;')
        detail_layout.addWidget(self.detail_total)
        detail_layout.addWidget(self.label('Recorded awake time'))
        daily_header = QHBoxLayout()
        daily_title = self.label('Awake time per day')
        daily_title.setStyleSheet('font-size:18px; font-weight:600;')
        daily_header.addWidget(daily_title, 1)
        self.detail_range = self.uptime.range_control('30d')     # this computer's period (lifetime_server_uptime)
        self.detail_range.changed.connect(lambda: self._render_daily(self._detail) if self._detail else None)
        daily_header.addWidget(self.detail_range)
        detail_layout.addLayout(daily_header)
        self.device_daily = Plot('daily', 'h')      # the desktop's Overview bars, for this computer
        self.device_daily.setFixedHeight(170)
        self.device_daily.setAccessibleName(self.t('Awake time per day'))
        detail_layout.addWidget(self.device_daily)
        self.daily_summary = QLabel()
        self.daily_summary.setObjectName('muted')
        self.daily_summary.setWordWrap(True)
        detail_layout.addWidget(self.daily_summary)
        self.uptime.device_section(detail_layout)     # uptime and downtime for the same period
        # The desktop's own Activity and Health pages, fed with what this computer reported.
        self.hardware_detail = HardwarePanel(remote=True)
        detail_layout.addWidget(self.hardware_detail)
        self.activity_state = self.label('History is off on this computer')
        self.activity_state.setObjectName('muted')
        self.activity_stats = QLabel()
        self.activity_stats.setObjectName('muted')
        self.activity_stats.setWordWrap(True)
        self.remote_activity = Insights()
        # The server's split detail pane is narrower than the desktop page.
        # Keep the same controls/charts, with ranges on their own row.
        activity_box = self.remote_activity.layout()
        activity_header = activity_box.itemAt(0).layout()
        activity_header.removeWidget(self.remote_activity.ranges)
        range_row = QHBoxLayout()
        range_row.addWidget(self.remote_activity.ranges, alignment=Qt.AlignmentFlag.AlignLeft)
        range_row.addStretch()
        activity_box.insertLayout(1, range_row)
        activity_box.itemAt(2).layout().setSpacing(12)
        for value in self.remote_activity.figures:
            value.setStyleSheet('font-size:16px; font-weight:600;')
        self.remote_health = HealthPage({}, lambda: None, remote=True)
        for card in self.remote_activity.cards:
            glass(card)
        for panel in self.remote_health.content.findChildren(QFrame):
            if panel.objectName() == 'insetPanel':
                glass(panel)
        for page in (self.remote_activity, self.remote_health):
            page.setStyleSheet('QLabel#pageTitle {font-size:22px; font-weight:600;} QLabel#caption {font-size:13px;} QLabel#groupTitle {font-size:17px; font-weight:600;}')
        self.health_state = self.label('System readings are off on this computer')
        self.health_state.setObjectName('muted')
        for widget in (self.activity_stats, self.activity_state, self.remote_activity, self.health_state, self.remote_health):
            detail_layout.addWidget(widget)
        self.detail_text = QTextBrowser()
        self.detail_text.setOpenExternalLinks(False)
        self.detail_text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.detail_text.setStyleSheet('QTextBrowser { border: none; background: transparent; padding: 0; }')
        self.detail_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_text.document().setDocumentMargin(0)
        detail_layout.addWidget(self.detail_text)
        id_row = QHBoxLayout()
        self.device_id_label = QLabel()
        self.device_id_label.setObjectName('muted')
        self.device_id_label.setStyleSheet('font-size: 12px; color: #9FB0CC;')
        self.device_id_label.setWordWrap(True)
        id_row.addWidget(self.device_id_label, 1)
        self.copy_device_id = self.button('Copy', lambda: QApplication.clipboard().setText(self._detail['device_id']) if self._detail else None)
        id_row.addWidget(self.copy_device_id)
        detail_layout.addLayout(id_row)
        detail_layout.addSpacing(10)
        identity_title = self.label('Server-owned identity')
        identity_title.setStyleSheet('font-size: 18px; font-weight: 600;')
        detail_layout.addWidget(identity_title)
        identity_form = QFormLayout()
        identity_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        identity_form.setSpacing(10)
        self.identity_fields = {}
        for field, key in (('display_name', 'Display name'), ('asset_tag', 'Asset tag'), ('department', 'Group (department)'), ('location', 'Location'), ('deployment_date', 'Deployment date')):
            edit = GroupField() if field == 'department' else QLineEdit()
            edit.setMinimumWidth(120)
            edit.setMaxLength(4096)
            label = self.label(key)
            label.setBuddy(edit)
            edit.setAccessibleName(self.t(key))
            edit.textEdited.connect(self._mark_identity_dirty)
            self.identity_fields[field] = edit
            identity_form.addRow(label, edit)
        self.identity_fields['deployment_date'].setPlaceholderText('d MMM yyyy')
        detail_layout.addLayout(identity_form)
        self.save_identity_button = self.button('Save identity', self.save_identity)
        self.save_identity_button.setObjectName('primary')
        detail_layout.addWidget(self.save_identity_button, 0, Qt.AlignmentFlag.AlignRight)
        actions = QVBoxLayout()
        self.revoke_button = self.button('Disconnect computer', self.revoke)
        self.revoke_button.setStyleSheet('color: #FF8A8A;')
        self.delete_button = self.button('Remove computer', self.delete_device)
        self.delete_button.setStyleSheet('color: #FF8A8A;')
        self.request_refresh_button = self.button('Request fresh report', self.request_fresh_report)
        actions.addWidget(self.revoke_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(self.request_refresh_button)
        detail_layout.addLayout(actions)
        refresh_note = self.label('Commands are delivered on the next sync. Turn on uses Wake-on-LAN.')
        refresh_note.setObjectName('muted')
        refresh_note.setWordWrap(True)
        detail_layout.addWidget(refresh_note)
        self.device_pending = QWidget()
        pending_layout = QVBoxLayout(self.device_pending)
        pending_layout.setContentsMargins(0, 0, 0, 0)
        pending_layout.addWidget(self.label('Connection requests'))
        self.pending_requests = QComboBox()
        pending_layout.addWidget(self.pending_requests)
        decisions = QHBoxLayout()
        self.approve_button = self.button('Approve', lambda: self.decide('approve'))
        self.deny_button = self.button('Deny', lambda: self.decide('deny'))
        decisions.addWidget(self.approve_button)
        decisions.addWidget(self.deny_button)
        pending_layout.addLayout(decisions)
        detail_layout.addWidget(self.device_pending)
        self.device_detail_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.device_grid.addWidget(self.devices_panel, 0, 0)
        self.device_detail_scroll = QScrollArea()
        self.device_detail_scroll.setObjectName('detailScroll')
        for layer in self.device_detail_panel.findChildren(Glass, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self.device_detail_panel.removeEventFilter(layer)       # one strong pane, not glass inside glass
        glass(self.device_detail_scroll, strong=True)
        self.device_detail_panel.setProperty('lifetimeScrollContent', True)
        self.device_detail_scroll.setWidgetResizable(True)
        self.device_detail_scroll.setWidget(self.device_detail_panel)
        self.device_detail_scroll.setMinimumWidth(330)
        self.device_grid.addWidget(self.device_detail_scroll, 0, 1)
        layout.addLayout(self.device_grid)
        layout.addStretch()
        self._arrange_devices()
        self._set_device_actions(False)

    def _arrange_devices(self):
        wide = self.width() >= 900
        self.device_grid.removeWidget(self.device_detail_scroll)
        self.device_grid.addWidget(self.device_detail_scroll, 0 if wide else 1, 1 if wide else 0)
        self.device_grid.setColumnStretch(0, 6)
        self.device_grid.setColumnStretch(1, 4 if wide else 0)
        height = max(self.devices_panel.minimumSizeHint().height(), 280, self.height() - 262)
        self.devices_panel.setFixedHeight(height)
        self.table.setMinimumHeight(180)
        self.device_detail_scroll.setFixedHeight(height if wide else 520)
        columns = 3 if self.width() >= 1200 else 2
        for i, button in enumerate(self.power_buttons.values()):
            self.power_grid.removeWidget(button)
            self.power_grid.addWidget(button, i // columns, i % columns)
        self.device_grid.setAlignment(self.devices_panel, Qt.AlignmentFlag.AlignTop)
        self.device_grid.setAlignment(self.device_detail_scroll, Qt.AlignmentFlag.AlignTop)
        self._fit_detail_text()

    def _detail_shown(self, shown):
        """The detail pane shows either one computer or, with none (or several) selected, only the sentence saying so."""
        self.detail_hint.setVisible(not shown)
        self.detail_body.setVisible(shown)
        self._fit_detail_text()

    def _fit_detail_text(self):
        width = max(280, self.device_detail_panel.width() - 40)
        self.detail_text.document().setTextWidth(width)
        self.detail_text.setFixedHeight(int(self.detail_text.document().size().height()) + 8)
        self.device_detail_panel.setMinimumHeight(self.device_detail_panel.sizeHint().height())

    def _section(self, key):
        panel, layout = self._panel()
        title = self.label(key)
        title.setStyleSheet('font-size: 18px; font-weight: 600;')
        layout.addWidget(title)
        return panel, layout

    def _form_section(self, key):
        panel, layout = self._section(key)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return panel, form

    def _set_chip(self, code):
        self._chip = code
        if code == 'all' and self.status_filter.currentIndex() > 0:
            # Review sets the status box; "All" has to mean every computer, not "all that are still pending".
            self.status_filter.setCurrentIndex(0)
        for key, button in self.device_chips.items():
            button.setChecked(key == code)
        self._filters_changed()

    def _cached_device(self, device):
        # Permission revocation immediately hides even previously cached readings.
        if (device.get('policy') or {}).get('resources') is not True:
            return {}
        cached = self._device_cache.get(device['device_id'])
        detail = cached[2] if cached else {}
        live = self._live_items.get(device['device_id'])
        if live and live[0].get('sample'):
            detail = dict(detail, performance=dict(detail.get('performance') or {}, **live[0]['sample']))
        return detail

    def _load_live(self):
        if self._closed or not self.isVisible() or self.isMinimized():
            return
        self._paint_live()
        if self._live_busy:
            return
        focus = self._detail['device_id'] if self.tabs.currentIndex() == 1 and self._detail else None
        if self.stream is not None:
            self.stream.watch(focus)
            # While the stream is up it delivers every sample; the admin call only refreshes the 5 minute chart history.
            if self.stream.connected and focus == self._poll_focus and time.monotonic() - self._last_poll < 15:
                return
        self._live_busy = True
        def ready(result, success):
            self._live_busy = False
            if success:
                now = time.monotonic()
                self._last_poll, self._poll_focus = now, result['device_id']
                self._live_items = {item['device_id']: (item, now) for item in result['devices']}
                self._live_ring = result['ring']
                self._live_focus = result['device_id']
                self._paint_live()
        if not self._submit(lambda: self.client.call('live', **({'device_id': focus} if focus else {})), ready, quiet=True):
            self._live_busy = False

    def _arm_overview_entrance(self, *unused):
        """The Overview plays its entrance once, when its first real numbers are on screen: the gauge sweeps, the
        tile icons draw and the counts land one after another. Tab changes and refreshes never replay it."""
        if self._closed or self._entrance_played or not self._overview_data or self.tabs.currentIndex() != 0:
            return
        self._entrance_played = True
        reduced = reduced_motion()
        for index, key in enumerate(('Connected', 'Offline', 'Awake today', 'Pending approval')):
            for icon in self.tile_icons[index:index + 1]:
                icon.intro.ready()
            self.kpis[key].roll_in(delay=160 + index * 90, reduced=reduced)
            for caption in (self.kpi_titles[key], self.kpi_notes[key]):   # the words settle in just after their number starts
                fade_in(caption, delay=420 + index * 90, reduced=reduced)

    def start_stream(self):
        """Subscribe to pushed live data. Without it the window polls, as before."""
        from lifetime_server_stream import AdminStream
        self.stream = AdminStream(self.directory, self.client.port, self)
        self.stream.received.connect(self._stream_event)
        self.stream.start()

    def _stream_event(self, event):
        if self._closed:
            return
        device_id = event.get('device_id')
        if event.get('type') == 'telemetry' and device_id and isinstance(event.get('sample'), dict):
            sample = event['sample']
            self._live_items[device_id] = (dict(device_id=device_id, sample=sample, age_seconds=0), time.monotonic())
            if device_id == self._live_focus and sample.get('observed_at'):
                self._live_ring.append(sample)
                del self._live_ring[:-300]
            if not self._paint_pending:
                self._paint_pending = True
                later(120, self._paint_pushed)      # at most about eight repaints a second, however many arrive
        elif event.get('type') in ('status', 'ack'):
            if event.get('type') == 'status' and event.get('online') is False:
                self._live_items.pop(device_id, None)
            self._fleet_timer.start()
            self._load_devices()

    def _paint_pushed(self):
        self._paint_pending = False
        if not self._closed:
            self._paint_live()

    def _paint_live(self):
        # No table mutations: selection, editor focus and scroll remain untouched.
        rows = {d['device_id']: d for d in self._rows}
        viewport = self.table.viewport().rect()
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if not box:
                continue
            device = rows.get(box.device_id)
            live = self._live_items.get(box.device_id)
            if device and live:
                item, received = live
                age = item['age_seconds']
                if item['sample'] and age is not None:
                    age += time.monotonic()-received
                    if age < 20:
                        device['last_received_at'] = item['sample']['observed_at']
                        device['reporting_status'] = 'reporting'
                        device['attention'] = [c for c in device.get('attention', []) if c != 'offline']
                # Qt clips offscreen painting; only update visible card widgets.
                if box.geometry().intersects(viewport):
                    box.set_device(device, self)
        self._paint_live_detail()

    def _paint_live_detail(self):
        if not self._detail:
            self.live_marker.clear()
            if self.performance_range.currentData() == 'live':
                for field in ('cpu_percent', 'ram_percent'):
                    self.performance_plots[field].set_data([], [], message=self.t('No recorded data in this range'))
            return
        live = self._live_items.get(self._detail['device_id'])
        item, received = live if live else ({}, time.monotonic())
        age = item.get('age_seconds')
        age = age + time.monotonic()-received if age is not None else None
        fresh = age is not None and age < 20
        if fresh and self._detail.get('last_received_at') != item['sample']['observed_at']:
            self._detail['last_received_at'] = item['sample']['observed_at']
            self._detail['reporting_status'] = 'reporting'
            self.detail_status.set_state('good', self._fleet_status('reporting'))
            self._power_state(self._detail)
        marker = self.t('Live: {seconds}s ago' if fresh else 'Last seen: {seconds}s ago', seconds=int(age)) if age is not None else self.t('Last seen')
        if self.live_marker.text() != marker:
            self.live_marker.setText(marker)
            self.live_marker.setStyleSheet('color:#4CC38A;' if fresh else 'color:#9FB0CC;')
        if self.performance_range.currentData() != 'live':
            return
        allowed = (self._detail.get('policy') or {}).get('resources') is True
        now = int(time.time())
        samples = {}
        if allowed and self._live_focus == self._detail['device_id']:
            for sample in self._live_ring:
                stamp = int(dt.datetime.fromisoformat(sample['observed_at'].replace('Z', '+00:00')).timestamp())
                samples[stamp] = sample
        keys = [dt.datetime.fromtimestamp(second).strftime('%H:%M:%S') for second in range(now-299, now+1)]
        for field in ('cpu_percent', 'ram_percent'):
            values = [samples.get(second, {}).get(field) for second in range(now-299, now+1)]
            plot = self.performance_plots[field]
            plot.set_data(keys, values, message='' if samples else self.t('No recorded data in this range' if allowed else 'Not permitted'))

    def _load_visible_readings(self):
        """Fetch only visible cards, with a small cache, never one request per fleet member."""
        if self._closed or self._readings_busy or self.tabs.currentIndex() != 1:
            return
        viewport = self.table.viewport()
        visible = []
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if box and box.geometry().intersects(viewport.rect()):
                device = next((d for d in self._rows if d['device_id'] == box.device_id), None)
                if device and (device.get('policy') or {}).get('resources') is True:
                    cached = self._device_cache.get(device['device_id'])
                    if not cached or time.monotonic() - cached[0] >= 30:
                        visible.append((device['device_id'], device.get('last_received_at')))
        if not visible:
            return
        self._readings_busy = True
        client = self.client
        def work():
            return [(identifier, stamp, client.call('device', device_id=identifier, hours=24)) for identifier, stamp in visible]
        def ready(results, success):
            self._readings_busy = False
            if success:
                for identifier, stamp, detail in results:
                    self._device_cache[identifier] = (time.monotonic(), stamp, detail)
                while len(self._device_cache) > 32:
                    del self._device_cache[next(iter(self._device_cache))]
                for row in range(self.table.rowCount()):
                    box = self.table.cellWidget(row, 0)
                    device = next((d for d in self._rows if d['device_id'] == box.device_id), None) if box else None
                    if device:
                        box.set_device(device, self)
                # A scroll during the request may have exposed a different set of cards.
                self._readings_timer.start()
        if not self._submit(work, ready):
            self._readings_busy = False

    def _select_box(self, device_id, multiple=False):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == device_id:
                flags = QItemSelectionModel.SelectionFlag.Rows | (
                    QItemSelectionModel.SelectionFlag.Toggle if multiple else QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.table.setCurrentCell(row, 0, QItemSelectionModel.SelectionFlag.NoUpdate)   # before select: moving current can drop the selection
                self.table.selectionModel().select(self.table.model().index(row, 0), flags)
                return

    def _power_reason(self, device, action):
        if not device:
            return self.t('Select a computer first.')
        refused = self.access_ui.power_reason(action)
        if refused:
            return refused
        if self._mutation_busy:
            return self.t('A request is in progress.')
        if action != 'refresh' and (device.get('policy') or {}).get('control') is not True:
            return self.t('The owner has not allowed remote power control.')
        if device.get('status') != 'approved':
            return self.t('This computer must be approved first.')
        if any(c.get('status') in ('pending', 'delivered') for c in device.get('commands', [])):
            return self.t('A command is already pending for this computer.')
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(device['last_received_at'].replace('Z', '+00:00'))).total_seconds()
            online = age <= max(600, self.interval.value() * 3)
        except (KeyError, ValueError, TypeError, AttributeError):
            online = False
        if action == 'wake':
            if online:
                return self.t('Turn on is available when the computer is offline.')
            if not (device.get('specs') or {}).get('mac_addresses'):
                return self.t('No network adapter address has been recorded.')
        elif not online:
            return self.t('This computer needs a recent sync first.')
        return ''

    def _power_state(self, device):
        if not hasattr(self, 'power_buttons'):
            return
        for action, button in self.power_buttons.items():
            reason = self._power_reason(device, action)
            button.setEnabled(not bool(reason))
            button.setToolTip(reason)
        self.control_reason.setText(self._power_reason(device, 'restart'))

    def _device_menu(self, device_id, anchor):
        # Load authoritative consent and command state in a worker before offering actions.
        client = self.client
        def ready(device, success):
            if not success or sip.isdeleted(anchor) or not anchor.isVisible() or anchor.parentWidget().device_id != device_id:
                return
            menu = QMenu(self)
            edit = menu.addAction(self.t('Edit details…'))
            edit.setEnabled(self.access_ui.can('identity'))
            edit.setToolTip('' if edit.isEnabled() else self.access_ui.reason())
            edit.triggered.connect(lambda checked=False: self.edit_details(device))
            menu.addSeparator()
            for action, key in (('restart', 'Restart'), ('sleep', 'power.sleep'), ('shutdown', 'Shut down'), ('wake', 'Turn on'), ('refresh', 'Refresh')):
                item = menu.addAction(self.t(key))
                reason = self._power_reason(device, action)
                item.setEnabled(not bool(reason))
                item.setToolTip(reason)
                item.triggered.connect(lambda checked=False, a=action: self._device_command(device, a))
            reason = self._power_reason(device, 'restart')
            if reason:
                menu.addSeparator()
                menu.addAction(reason).setEnabled(False)
            menu.addSeparator()
            remove = menu.addAction(self.t('Remove computer'))
            remove.setEnabled(self.access_ui.can('delete'))
            remove.setToolTip('' if remove.isEnabled() else self.access_ui.reason())
            remove.triggered.connect(lambda checked=False: self.delete_device(device))
            menu.setToolTipsVisible(True)
            self.device_menu = menu
            menu.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        self._submit(lambda: client.call('device', device_id=device_id, hours=24), ready)

    def _device_command(self, device, action):
        if self._power_reason(device, action):
            return
        identity = device.get('identity') or {}
        key = {'sleep': 'power.sleep', 'restart': 'Restart', 'shutdown': 'Shut down', 'wake': 'Turn on', 'refresh': 'Refresh'}[action]
        # Names the exact computer: its name, group and ID, so two computers with the same name cannot be confused.
        if not self._confirm(self.t('{action} this computer?\n\n{name}\nGroup: {group}\nID: {device_id}', action=self.t(key),
                                    name=identity.get('display_name') or self.t('Unnamed computer'),
                                    group=identity.get('department') or self.t('No group'), device_id=device['device_id'])):
            return
        arguments = dict(device_id=device['device_id'])
        if action != 'wake':
            arguments['action'] = action
        self._mutate('wake' if action == 'wake' else 'send_command', arguments)

    def _render_specs(self, device):
        missing = self.t('Not available')
        allowed = (device.get('policy') or {}).get('resources') is True
        specs = (device.get('specs') or {}) if allowed else {}
        if hasattr(self, 'hardware_detail'):
            self.hardware_detail.setVisible(allowed)
            self.hardware_detail.set_data(specs.get('hardware'))
        values = {'Manufacturer': specs.get('manufacturer'), 'Model': specs.get('model'),
                  'CPU': specs.get('cpu_name'), 'RAM': byte_text(specs.get('ram_bytes'), missing),
                  'Cores / threads': f"{specs.get('cpu_cores') if specs.get('cpu_cores') is not None else missing} / {specs.get('cpu_threads') if specs.get('cpu_threads') is not None else missing}",
                  'Graphics': ', '.join(specs.get('gpu_names') or []),
                  'Operating system': ' '.join(str(specs.get(k) or '') for k in ('os_name', 'os_version')).strip(),
                  'Storage': '\n'.join(self.t('{name}: {free} free of {total}', name=d['name'], free=byte_text(d.get('free_bytes'), missing), total=byte_text(d.get('total_bytes'), missing)) for d in specs.get('disks') or []),
                  'Battery design capacity': None if specs.get('battery_design_wh') is None else f"{specs['battery_design_wh']:,.1f} Wh"}
        for key, label in self.spec_values.items():
            label.setText(str(values.get(key) or missing))
            label.setAccessibleName(self.t(key))
        # One capacity bar per disk; the words stay in the Storage row for copying into a ticket.
        disks = [d for d in specs.get('disks') or [] if d.get('total_bytes') and d.get('free_bytes') is not None]
        card = self.spec_cards['Storage']
        while len(self.storage_bars) < len(disks):
            bar = CapacityBar()
            card.extra.addWidget(bar)
            self.storage_bars.append(bar)
        for bar, disk in zip(self.storage_bars, disks):
            used = disk['total_bytes'] - disk['free_bytes']
            detail = self.t('{free} free of {total}', free=byte_text(disk['free_bytes'], missing), total=byte_text(disk['total_bytes'], missing))
            bar.set_usage(used, disk['total_bytes'], disk['name'], detail, f"{disk['name']} {detail}")
        for index, bar in enumerate(self.storage_bars):
            bar.setVisible(index < len(disks))
        for widget in (self.spec_values['Storage'], self.spec_keys['Storage']):
            widget.setVisible(not disks)
        history = (device.get('performance_history') or []) if allowed else []
        for field, plot in self.performance_plots.items():
            if field in ('cpu_percent', 'ram_percent') and self.performance_range.currentData() == 'live':
                continue
            plot.set_data([dt.datetime.fromisoformat(r['observed_at'].replace('Z', '+00:00')).astimezone().strftime('%H:%M') for r in history], [r.get(field) for r in history],
                          message='' if history and allowed else self.t('No recorded data in this range' if allowed else 'System readings are off on this computer'))
            plot.setAccessibleName(self.t({'cpu_percent': 'CPU', 'ram_percent': 'RAM', 'disk_free_percent': 'Disk free', 'battery_percent': 'Battery', 'temperature_c': 'Temperature'}[field]))
        self._paint_live_detail()

    def _make_this_server(self):
        layout = self._scroll_page('This Server')
        title = self.label('This Server')
        title.setStyleSheet('font-size:28px; font-weight:600;')
        layout.addWidget(title)
        self.host_values = {}
        self.host_panels = []
        self.host_grid = QGridLayout()
        self.host_grid.setSpacing(16)
        for heading, keys in (('Server', ('Server name', 'Listening address', 'Uptime', 'Certificate expires',
                                          'Server key SHA-256', 'Certificate SHA-256')),
                              ('Hardware', ('CPU', 'RAM', 'Operating system')),
                              ('Right now', ('CPU usage', 'Memory used', 'Last seen')),
                              ('Connected computers', ('All computers', 'Connected', 'Pending approval', 'Database size'))):
            panel, content = self._section(heading)
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(12)
            for key in keys:
                label = QLabel(self.t('Not available'))
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setMinimumWidth(0)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
                self.host_values[key] = label
                form.addRow(self._field(key), label)
            content.addLayout(form)
            content.addStretch()
            self.host_panels.append(panel)
        layout.addLayout(self.host_grid)
        layout.addStretch()

    def _render_this_server(self, info):
        missing = self.t('Not available')
        specs, perf, counts = info.get('specs') or {}, info.get('performance') or {}, info.get('device_counts') or {}
        values = {'Server name': info.get('name'), 'Listening address': info.get('address'),
                  'Uptime': self._duration(info.get('uptime')), 'Last seen': self.t('Just now'),
                  'CPU': specs.get('cpu_name'), 'RAM': byte_text(specs.get('ram_bytes'), missing),
                  'Operating system': ' '.join(str(specs.get(k) or '') for k in ('os_name', 'os_version')).strip() or info.get('os'),
                  'CPU usage': None if perf.get('cpu_percent') is None else f"{perf['cpu_percent']:.0f}%",
                  'Memory used': byte_text(perf.get('ram_used_bytes'), missing),
                  'Certificate SHA-256': info.get('certificate_fingerprint') or info.get('fingerprint'),
                  'Server key SHA-256': info.get('key_fingerprint'),
                  'Certificate expires': self._certificate_text(info.get('certificate') or {}),
                  'Database size': byte_text(info.get('database_size'), missing),
                  'All computers': counts.get('total'), 'Connected': counts.get('reporting'), 'Pending approval': counts.get('pending')}
        for key in ('Certificate SHA-256', 'Server key SHA-256'):
            fingerprint = values[key]
            if fingerprint:     # groups of eight wrap inside the row instead of being cut off
                values[key] = ' '.join(fingerprint[i:i+8] for i in range(0, len(fingerprint), 8))
        for key, label in self.host_values.items():
            value = values.get(key)
            label.setText(missing if value is None else str(value))

    # Self check (1.3.0 diagnostics): what computers need from this server, one line each with what to do.
    def _make_self_check(self):
        panel, layout = self._section('Self check')
        note = self.label('Checks the addresses, certificate, discovery, firewall and time that computers need. It only reads; nothing is changed.')
        note.setObjectName('muted')
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.self_check_button = self.access_ui.gate(self.button('Run self check', self.run_self_check), 'self_check')
        # Needs only server_info and server_log, so it also works with a server older than Self check.
        self.diagnostics_button = self.access_ui.gate(self.button('Copy diagnostics', self.copy_diagnostics), 'server_info')
        actions.addWidget(self.self_check_button)
        actions.addWidget(self.diagnostics_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.self_check_rows = QVBoxLayout()
        self.self_check_rows.setSpacing(10)
        layout.addLayout(self.self_check_rows)
        self._self_check = None
        self.admin_layout.addWidget(panel)

    def run_self_check(self):
        client = self.client

        def work():
            try:
                return client.call('self_check')
            except Exception as error:      # said in the Self check panel itself, by its cause
                return dict(problem=self_check_problem(error))
        self._submit(work, self._self_check_finished, self.self_check_button)

    def copy_diagnostics(self):
        client = self.client

        def work():
            try:
                check = client.call('self_check')
            except Exception:       # an older server: the rest is still worth copying
                check = None
            # Built here, in the worker: reading the log file and scrubbing 256 KB must not hold up the window.
            return diagnostics_text(self.t, client.call('server_info'), check, client.call('server_log', limit=200))

        def finished(result, success):
            if success:
                QApplication.clipboard().setText(result)
                self.status_label.setText(self.t('Diagnostics copied. Codes, tokens and passwords are left out.'))
        self._submit(work, finished, self.diagnostics_button)

    def _self_check_finished(self, result, success):
        self._self_check = result if success else None        # shown again in another language
        problem = (result or {}).get('problem') if success else 'failed'
        if problem:
            cause, fix = SELF_CHECK_PROBLEMS[problem]
            checks = [dict(state='danger', text=cause, fix=fix, values={})]
        else:
            checks = result.get('checks') or []
        while self.self_check_rows.count():
            item = self.self_check_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for check in checks:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(StatusChip(check['state'], self.t(CHECK_WORDS.get(check['state'], 'Info'))), 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(2)
            for key, muted in ((check['text'], False), (check.get('fix'), True)):
                if key:
                    label = QLabel(self.t(key, **check['values']) if not muted else
                                   self.t('What to do: {fix}', fix=self.t(key, **check['values'])))
                    label.setWordWrap(True)
                    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    if muted:
                        label.setObjectName('muted')
                    words.addWidget(label)
            line.addLayout(words, 1)
            self.self_check_rows.addWidget(row)

    def _make_admin(self):
        self.admin_layout = self._scroll_page('Administration')
        self.admin_layout.setContentsMargins(32, 24, 32, 24)
        title = self.label('Administration')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        self.admin_layout.addWidget(title)
        gate = self.access_ui.gate
        self.access_ui.roles_section(self.admin_layout)
        approvals, approvals_layout = self._section('Pending approvals')
        gate(approvals, 'approve')
        self.admin_pending = QTableWidget(0, 4)
        self.admin_pending.setObjectName('approvalTable')
        self._style_table(self.admin_pending)
        self.admin_pending.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.admin_pending.setFixedHeight(140)
        self.admin_pending.verticalHeader().setDefaultSectionSize(56)
        self.admin_pending.setAccessibleName(self.t('Pending approvals'))
        self.admin_pending.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.admin_pending.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._admin_row_actions = []
        self._invitation_row_actions = []
        approvals_layout.addWidget(self.admin_pending)
        self.approvals_empty = self.label('No pending approvals')
        self.approvals_empty.setObjectName('muted')
        approvals_layout.addWidget(self.approvals_empty)
        self.admin_layout.addWidget(approvals)
        invitations, invitation_layout = self._section('Invitations')
        gate(invitations, 'invitation')
        self.invitation_button = self.button('Add device', self.add_device)
        self.invitation_button.setObjectName('primary')
        options = ControlRow()
        options.setSpacing(12)
        self.invitation_hours, self.invitation_uses = QSpinBox(), QSpinBox()
        self.invitation_hours.setRange(1, 24)
        self.invitation_hours.setValue(24)
        self.invitation_uses.setRange(1, 100)
        for key, control in (('Expires in (hours)', self.invitation_hours), ('Allowed uses', self.invitation_uses)):
            label = self.label(key)
            label.setBuddy(control)
            options.addWidget(label)
            control.setAccessibleName(self.t(key))
            control.setFixedWidth(108)
            options.addWidget(control)
        options.addStretch()
        options.addWidget(self.invitation_button)
        invitation_layout.addLayout(options)
        self.invitation_table = QTableWidget(0, 6)
        self.invitation_table.setObjectName('invitationTable')
        self._style_table(self.invitation_table)
        self.invitation_table.setFixedHeight(184)
        self.invitation_table.verticalHeader().setDefaultSectionSize(56)
        self.invitation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for column in (0, 2, 3, 4, 5):
            self.invitation_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        invitation_layout.addWidget(self.invitation_table)
        invitation_pages = QHBoxLayout()
        self.invitation_previous = self.button('Previous', lambda: self._invitation_page(-1))
        self.invitation_next = self.button('Next', lambda: self._invitation_page(1))
        invitation_pages.addStretch()
        invitation_pages.addWidget(self.invitation_previous)
        invitation_pages.addWidget(self.invitation_next)
        invitation_layout.addLayout(invitation_pages)
        self.admin_layout.addWidget(invitations)
        server, form = self._form_section('Server')
        self.server_information = QLabel(self.t('Server unavailable'))
        self.server_information.setWordWrap(True)
        self.server_information.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.label('Listening address'), self.server_information)
        self.fingerprint = QLineEdit()
        self.fingerprint.setReadOnly(True)
        self.fingerprint.setStyleSheet('font-family: Consolas; font-size: 13px;')
        self.fingerprint.setAccessibleName(self.t('Certificate SHA-256'))
        fingerprint_row = QHBoxLayout()
        fingerprint_row.addWidget(self.fingerprint, 1)
        fingerprint_row.addWidget(self.button('Copy', lambda: QApplication.clipboard().setText(self.fingerprint.text())))
        form.addRow(self.label('Certificate SHA-256'), fingerprint_row)
        self.certificate_expiry = QLabel()
        self.certificate_expiry.setWordWrap(True)
        self.certificate_expiry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.label('Certificate expires'), self.certificate_expiry)
        self.names_edit = gate(QLineEdit(), 'names')
        self.names_edit.setMaxLength(2000)
        names_row = QHBoxLayout()
        names_row.addWidget(self.names_edit, 1)
        self.names_button = gate(self.button('Save names', self.save_names), 'names')
        names_row.addWidget(self.names_button)
        form.addRow(self.label('Other server names and IPs'), names_row)
        names_note = self.label('Separate them with commas. Lifetime renews the certificate with the same key, so connected computers keep working without a new code.')
        names_note.setObjectName('muted')
        form.addRow(names_note)
        self.server_name = QLabel()
        form.addRow(self.label('Server name'), self.server_name)
        self.organisation_edit = gate(QLineEdit(), 'organisation')
        self.organisation_edit.setMaxLength(120)
        self.organisation_edit.setAccessibleName(self.t('Organisation'))
        organisation_row = QHBoxLayout()
        organisation_row.addWidget(self.organisation_edit, 1)
        organisation_row.addWidget(gate(self.button('Save organisation', lambda: self._mutate('organisation', dict(name=self.organisation_edit.text()))), 'organisation'))
        form.addRow(self.label('Organisation'), organisation_row)
        self.admin_layout.addWidget(server)
        self._make_self_check()
        log_panel, log_layout = self._section('Server log')
        log_note = self.label('Recent server events: starts and stops, certificate renewals and refused requests. No report contents or codes are kept.')
        log_note.setObjectName('muted')
        log_layout.addWidget(log_note)
        self.server_log = EventRail()
        self.server_log.setObjectName('serverLog')
        self.server_log.setAccessibleName(self.t('Server log'))
        self.server_log_scroll = QScrollArea()      # five rows tall; the rest of the 50 scrolls
        self.server_log_scroll.setWidget(self.server_log)
        self.server_log_scroll.setWidgetResizable(True)
        self.server_log_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.server_log_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.server_log_scroll.setFixedHeight(5 * EventRail.ROW)
        self.server_log_scroll.viewport().setAutoFillBackground(False)
        self.server_log.setAutoFillBackground(False)
        log_layout.addWidget(self.server_log_scroll)
        self.server_log_empty = self.label('No server events yet')
        self.server_log_empty.setObjectName('muted')
        log_layout.addWidget(self.server_log_empty)
        self.server_log_scroll.hide()
        self.admin_layout.addWidget(log_panel)
        self.access_ui.audit_section(self.admin_layout)
        self.alerts.admin(self.admin_layout)
        retention, form = self._form_section('Data retention')
        gate(retention, 'retain')
        self.retention_boundary = QLabel()
        self.retention_boundary.setWordWrap(True)
        form.addRow(self.retention_boundary)
        self.retention_date = QDateEdit(QDate.currentDate())
        self.retention_date.setMaximumDate(QDate.currentDate())     # the server refuses a boundary in the future
        self.retention_date.setMinimumWidth(190)
        self.retention_date.setCalendarPopup(True)
        self.retention_date.setDisplayFormat('d MMM yyyy')
        self.retention_date.setAccessibleName(self.t('Delete records before (UTC)'))
        retention_row = QHBoxLayout()
        retention_row.addWidget(self.retention_date)
        self.retain_button = self.button('Apply', self.apply_retention)
        retention_row.addWidget(self.retain_button)
        retention_row.addStretch()
        form.addRow(self.label('Delete records before (UTC)'), retention_row)
        self.uptime.retention_rows(form)
        self.admin_layout.addWidget(retention)
        about, about_layout = self._section('About')
        credit = QHBoxLayout()
        self.about_logo = QLabel()
        self.about_logo.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(100, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        credit.addWidget(self.about_logo)
        credit.addWidget(self.label('Made by JPL Innovation'), 1)
        credit.addWidget(QLabel(CLIENT_VERSION))
        about_layout.addLayout(credit)
        self.admin_layout.addWidget(about)
        access, access_layout = self._section('Computer access')
        gate(access, 'revoke')
        access_layout.addWidget(self.button('Choose computer', lambda: self.tabs.setCurrentIndex(1)), 0, Qt.AlignmentFlag.AlignLeft)
        self.admin_selected = QLabel(self.t('Select a computer in Devices.'))
        self.admin_selected.setWordWrap(True)
        access_layout.addWidget(self.admin_selected)
        self.admin_revoke = self.button('Revoke selected computer', self.revoke)
        self.admin_reenable = self.button('Re-enable selected computer', self.add_device)
        self.admin_revoke.setEnabled(False)
        self.admin_reenable.setEnabled(False)
        access_layout.addWidget(self.admin_revoke, 0, Qt.AlignmentFlag.AlignLeft)
        access_layout.addWidget(self.admin_reenable, 0, Qt.AlignmentFlag.AlignLeft)
        note = self.label('To reconnect, get a new code and approve this computer again. Its history is kept.')
        note.setWordWrap(True)
        access_layout.addWidget(note)
        self.admin_layout.addWidget(access)
        configuration, form = self._form_section('Configuration')
        gate(configuration, 'set_configuration')
        self.interval = QSpinBox()
        self.interval.setMaximumWidth(150)
        self.interval.setRange(5, 3600)
        self.interval.setEnabled(False)
        self.interval.setAccessibleName(self.t('Reporting interval (seconds)'))
        self.interval.valueChanged.connect(self._configuration_edited)
        form.addRow(self.label('Reporting interval (seconds)'), self.interval)
        self.interval_button = self.button('Save reporting interval', self.save_configuration)
        self.interval_button.setEnabled(False)
        interval_actions = QHBoxLayout()
        interval_actions.addWidget(self.interval_button)
        interval_actions.addStretch()
        form.addRow(interval_actions)
        self.admin_layout.addWidget(configuration)
        enrollment, form = self._form_section('Pair a computer')
        self.server_address = QLineEdit('https://localhost:8443')
        self.server_address.setAccessibleName(self.t('Server HTTPS address'))
        self.code_ttl, self.code_uses = QSpinBox(), QSpinBox()
        self.code_ttl.setRange(1, 86400)
        self.code_ttl.setValue(600)
        self.code_uses.setRange(1, 100)
        for key, widget in (('Server HTTPS address', self.server_address),
                            ('Code lifetime (seconds)', self.code_ttl), ('Allowed uses', self.code_uses)):
            label = self.label(key)
            label.setBuddy(widget)
            widget.setAccessibleName(self.t(key))
            form.addRow(label, widget)
        self.code_button = self.button('Create enrollment bundle', self.create_code)
        form.addRow(self.code_button)
        self.bundle_output = QPlainTextEdit()
        self.bundle_output.setReadOnly(True)
        self.bundle_output.setMaximumHeight(130)
        self.bundle_output.setAccessibleName(self.t('Enrollment bundle'))
        form.addRow(self.bundle_output)
        self.code_expiry = QLabel()
        self.code_expiry.setWordWrap(True)
        form.addRow(self.code_expiry)
        note = self.label('Share this bundle only with the technician pairing the computer. Approval is still required.')
        note.setWordWrap(True)
        form.addRow(note)
        # Superseded by Add a computer. Keep the legacy helper for scripted callers.
        enrollment.setParent(self)
        enrollment.hide()
        style_panel, style_layout = self._section('Style')
        style_layout.addWidget(self.style_control, 0, Qt.AlignmentFlag.AlignLeft)
        self.admin_layout.addWidget(style_panel)
        backups, layout = self._section('Backup and restore')
        gate(backups, 'backup_archive')
        self.backup_button = self.button('Save backup', self.backup)
        self.verify_backup_button = self.button('Verify backup', self.verify_backup)
        self.restore_button = self.button('Restore backup', self.restore)
        backup_row = QHBoxLayout()
        for button in (self.backup_button, self.verify_backup_button, self.restore_button):
            backup_row.addWidget(button)
        backup_row.addStretch()
        layout.addLayout(backup_row)
        note = self.label('A backup is one protected .zip archive with a .sha256 file beside it: the databases (computers, monitoring, roles and the audit trail), the server key and certificate, and the configuration. It holds the private key, so keep it where only administrators can read it. The mail password, webhook address and administrator token are not included.')
        note.setWordWrap(True)
        layout.addWidget(note)
        note = self.label('Restore checks the archive and applies it the next time the server starts (restart it with restart_server_windows.ps1). Computers then need a new code and approval to connect again; the current roles and audit trail are kept.')
        note.setObjectName('muted')
        layout.addWidget(note)
        self.admin_layout.addWidget(backups)
        usage_panel, usage_holder = self._panel()
        self.overview_usage.layout().setContentsMargins(0, 0, 0, 0)
        usage_holder.addWidget(self.overview_usage)
        self.admin_layout.addWidget(usage_panel)
        self.admin_layout.addWidget(self.button('Export fleet', lambda: self.export_requested.emit(None)), 0, Qt.AlignmentFlag.AlignRight)
        self.admin_layout.addStretch()
        self.access_ui.retranslate()

    def _set_device_actions(self, enabled):
        enabled = enabled and not self._mutation_busy
        can = self.access_ui.can
        for widget, operation in ([(field, 'identity') for field in self.identity_fields.values()]
                                  + [(self.save_identity_button, 'identity'), (self.revoke_button, 'revoke'),
                                     (self.delete_button, 'delete'), (self.request_refresh_button, 'refresh')]):
            widget.setEnabled(enabled and can(operation))
            widget.setToolTip('' if can(operation) else self.access_ui.reason())
        self.request_refresh_button.setEnabled(bool(enabled and not self._power_reason(self._detail, 'refresh')))
        self.export_button.setEnabled(bool(self.selected_device_ids()) if hasattr(self, 'export_button') else False)
        pending = enabled and self.pending_requests.count() > 0 and can('approve')
        self.approve_button.setEnabled(pending)
        self.deny_button.setEnabled(pending)
        if hasattr(self, 'admin_revoke'):
            self.admin_revoke.setEnabled(bool(enabled and self._detail and self._detail.get('status') != 'revoked'))
            self.admin_reenable.setEnabled(bool(enabled and self._detail and self._detail.get('status') == 'revoked'))
            self.admin_selected.setText((self._detail.get('identity', {}).get('display_name') or self._detail['device_id']) if self._detail else self.t('Select a computer in Devices.'))
            self._admin_selection_changed()
        self._power_state(self._detail)

    def _mark_identity_dirty(self):
        self._identity_dirty = True

    WINDOW_BACKGROUND = {
        'glass': 'qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2353A0, stop:0.28 #15306A, stop:0.55 #0B1426, stop:0.8 #3A3220, stop:1 #8A6516)',
        'classic': '#0B1426'}

    def _apply_window_style(self):
        """The window stylesheet; only the background differs between Glass and Classic."""
        self.setStyleSheet(stylesheet() + server_surfaces() + """
            QMainWindow, QDialog { background: %s; }
""" % self.WINDOW_BACKGROUND[active_style()] + """
            QWidget#chrome { background: transparent; }
            QWidget#panel[glass="true"], QWidget#notice, QScrollArea#detailScroll { background: transparent; border: none; }
            QPushButton[glass="true"] { background: transparent; border: 1px solid transparent; padding: 8px 16px; }
            QPushButton[glass="true"]:focus { border-color: #FFC93C; }
            QPushButton[glass="true"]:checked, QPushButton[glass="true"]:pressed { background: transparent; }
            QPushButton[glass="true"]:disabled { background: transparent; border-color: transparent; }
            QPushButton#link { background: transparent; border: 1px solid transparent; color: #FFC93C; padding: 4px 6px; font-weight: 600; }
            QPushButton#link:hover { text-decoration: underline; }
            QPushButton#link:focus { border-color: #FFC93C; }
            QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0F1A30, stop:1 #12203A); border-color: #2A3E66; }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDateEdit:hover { border-color: #3A5688; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: #FFC93C; }
            QLabel#statusText { font-size: 14px; }
            QLabel#muted { color: #9FB0CC; font-size: 13px; }
            QLabel#fieldLabel { color: #9FB0CC; }
            QLabel#sectionTitle { font-size: 18px; font-weight: 600; }
            QLabel#metric { font-size: 26px; font-weight: 600; }
            QPushButton#deviceMenu { background: transparent; border: 1px solid transparent; }
            QPushButton#deviceMenu:hover { border-color: #34445E; }
            QPushButton#deviceMenu:focus { border-color: #FFC93C; }
            QTableWidget { background: transparent; border: none; padding: 0; gridline-color: #22345A; }
            QTableWidget::item { padding: 7px 10px; border-bottom: 1px solid #1B2A46; }
            QTableWidget#approvalTable::item, QTableWidget#invitationTable::item { padding: 0; }
            QTableWidget::item:selected { background: transparent; }
            QHeaderView::section { background: transparent; font-weight: 600; font-size: 12px; color: #9FB0CC; border: none; border-bottom: 1px solid #2A3E66; }
        """)

    def _draw_language(self, p, rect, index):
        font = p.font()
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(tokens()['text']))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, ('EN', 'VI')[index])

    def _draw_theme(self, p, rect, index):
        draw_mode_icon(p, THEME_KEYS[index], rect.center(), QColor(tokens()['accent']), 1.0)

    def _describe_switches(self):
        """Tooltip and accessible name: the current value, and what a click does."""
        names = (('English', 'Tiếng Việt'), tuple(self.t(name) for name in ('Auto', 'Light', 'Dark')))
        for button, key, choices in ((self.language_button, 'Language: {current}. Click to switch to {next}.', names[0]),
                                     (self.theme_button, 'Theme: {current}. Click to switch to {next}.', names[1])):
            sentence = self.t(key, current=choices[button.index], next=choices[(button.index + 1) % button.count])
            button.setToolTip(sentence)
            button.setAccessibleName(sentence)

    def _language_cycled(self, index):
        self.language = ('en', 'vi')[index]
        self.retranslate()

    def _theme_cycled(self, index):
        """Save the choice and change in place: the new theme spreads out from the button."""
        value = THEME_KEYS[index]
        write_theme_preference(self.directory, value)
        self._describe_switches()
        name = resolve_theme(value)
        if name != active_theme():
            origin = self.theme_button.mapTo(self, self.theme_button.rect().center())
            theme_reveal(self, origin, lambda: self._apply_live_theme(name))

    def _style_changed(self, value):
        """Save the choice and swap the surfaces in place, spreading out from the control like the theme does."""
        write_style_preference(self.directory, value)
        if value != active_style():
            origin = self.style_control.mapTo(self, self.style_control.rect().center())
            theme_reveal(self, origin, lambda: self._apply_live_style(value))

    def _apply_live_style(self, name):
        set_style(name)
        self._apply_window_style()
        retint_all()

    def _apply_live_theme(self, name):
        set_theme(name)
        retint_all()
        self.credit.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(150, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.about_logo.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(100, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.refresh()

    def retranslate(self):
        if hasattr(self, 'performance_range'):
            for index, key in enumerate(('Live', '24 hours')):
                self.performance_range.setItemText(index, self.t(key))
        catalog.set_language(self.language)
        for widget, key, setter in self._bindings:
            getattr(widget, setter)(self.t(key))
            widget.setAccessibleName(self.t(key))
        for index, key in enumerate(('Overview', 'Devices', 'Incidents', 'Energy', 'Administration', 'This Server')):
            self.tabs.setTabText(index, self.t(key))
        self.alerts.retranslate()
        self.insights.retranslate()
        self.uptime.retranslate()
        self.access_ui.retranslate()
        for index, key in enumerate(('All statuses', 'Pending approval', 'Approved', 'Denied', 'Disconnected', 'Connected', 'Offline', 'Unavailable')):
            self.status_filter.setItemText(index, self.t(key))
        self.table.setHorizontalHeaderLabels([self.t(key) for key in
                                              ('Device', 'Group', 'Status', 'Awake today', 'Last seen')])
        self.table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.group.setItemText(0, self.t('All groups'))
        self.fleet_table.setHorizontalHeaderLabels([self.t(key) for key in ('Device', 'Group', 'Status', 'Awake today', 'Last seen')])
        # The duration column is right-aligned, so its heading must be too.
        self.fleet_table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.invitation_table.setHorizontalHeaderLabels([self.t(key) for key in ('Invitation', 'Expires', 'Uses left', 'Wrong guesses', 'Status', 'Actions')])
        self.admin_pending.setHorizontalHeaderLabels([self.t('Device'), self.t('Awaiting approval'), '', ''])
        self.server_log.setAccessibleName(self.t('Server log'))
        self.events.setAccessibleName(self.t('Recent events'))
        if getattr(self, '_admin_result', None) is not None:
            self._render_server_log(self._admin_result.get('log') or [])
        self.fleet_group.setItemText(0, self.t('All groups'))
        locale = QLocale('vi_VN' if self.language == 'vi' else 'en_GB')
        self.setLocale(locale)
        self.fleet_date.setLocale(locale)
        self.retention_date.setLocale(locale)
        self._show_today()
        self.server_status.setText(self.t('Server running' if self._overview_result else 'Server unavailable'))
        if self._server_info:
            self.network_label.setText((self._server_info.get('organisation') or self._server_info['name']) + ' · ' + self.t('Local network'))
        self.local_server.setText(self.t('Local server: {name}', name=(self._server_info or {}).get('name', socket.gethostname())))
        if self._overview_result is not None:
            self._render_overview(self._overview_result)
        if self._configuration_result is not None:
            self._render_configuration(self._configuration_result)
        if self._bundle_expiry:
            self.code_expiry.setText(local_expiry(self._bundle_expiry, self.t) if self._bundle_expiry else '')
        for field, key in (('display_name', 'Display name'), ('asset_tag', 'Asset tag'), ('department', 'Group (department)'), ('location', 'Location'), ('deployment_date', 'Deployment date')):
            self.identity_fields[field].setAccessibleName(self.t(key))
        self._describe_switches()
        for segment, key in zip(self.style_control.group.buttons(), ('Glass', 'Classic')):
            segment.setText(self.t(key))
        self.style_control.setAccessibleName(self.t('Style'))
        for widget, key in ((self.status_filter, 'Status'),
                            (self.table, 'Devices'), (self.detail_text, 'Computer details'),
                            (self.page_size, 'Page size'), (self.pending_requests, 'Pending requests'),
                            (self.interval, 'Reporting interval (seconds)'), (self.events, 'Recent events'),
                            (self.bundle_output, 'Enrollment bundle'), (self.retention_date, 'Delete records before (UTC)')):
            widget.setAccessibleName(self.t(key))
        for i, key in enumerate(('Sort by problem', 'Sort by name', 'Sort by last seen')):
            self.device_order.setItemText(i, self.t(key))
        self.device_order.setAccessibleName(self.t('Sort computers'))
        self._render_rows(self._rows)
        if self._fleet_result:
            self._render_fleet(self._fleet_result)
        else:
            self._render_fleet_detail(None)
        if self._admin_result:
            self._render_server_info(self._admin_result)
        if getattr(self, '_self_check', None):
            self._self_check_finished(self._self_check, True)
        self.detail_hint.set_state('off', self.t('Select a computer to see its details.'))
        self._detail_shown(bool(self._detail) or len(self.selected_device_ids()) == 1)
        if self._detail:
            self._render_detail(self._detail, preserve_edits=self._identity_dirty)
        else:
            self.detail_text.setPlainText(self.t('Select a computer to view its usage, permissions and server-owned identity.'))

    # One refresh with a computer selected starts ten loads (the pages, alerts, energy, roles, and the computer's detail
    # and presence), and the device list asks for the detail again when it arrives. With the old limit of 8 the last
    # ones were silently dropped: the detail pane and the Energy and Access pages kept old data until a later refresh.
    MAX_JOBS = 24

    def _submit(self, work, callback, button=None, quiet=False):
        if self._closed or len(self._jobs) >= self.MAX_JOBS:
            return False
        number = self.tasks.submit(work)
        self._jobs[number] = (callback, button)
        if quiet:
            self._quiet_jobs.add(number)
        if button is not None:
            button.setEnabled(False)
        if not quiet and not self._status_failure:
            self.status_label.setText(self.t('Working…'))
        self._update_busy()
        return True

    def _completed(self, number, result, success, error=None):
        callback, button = self._jobs.pop(number)
        quiet = number in self._quiet_jobs
        self._quiet_jobs.discard(number)
        if self._closed:
            return
        if quiet:
            callback(result, success)
            return
        if button is not None:
            button.setEnabled(True)
        if error == 'NotAllowed':        # the server refused this for the signed-in role (HTTP 403)
            self.status_label.setText(self.t('Your role does not allow this.'))
        elif error and error[8:] in REFUSALS and error.startswith('Refused:'):      # the server said why (a fixed code)
            self.status_label.setText(self.t(REFUSALS[error[8:]]))
        elif error == 'PermissionError':
            self._status_failure = True
            self.status_label.setText(self.t('This Windows account has no Lifetime Server role. Ask an administrator for one, or open the dashboard as administrator.'))
        elif not success:
            self._status_failure = True
            self.status_label.setText(self.t(
                "Can't reach the Lifetime server. Make sure it is running."))
        elif not self._status_failure:
            self.status_label.setText(self.t('Working…' if any(job not in self._quiet_jobs for job in self._jobs) else 'Ready'))
        callback(result, success)
        self._update_busy()

    def refresh(self):
        self._status_failure = False
        if self._detail:
            self._selection_changed()
        self._load_devices()
        self._load_configuration()
        self._load_overview()
        self._load_fleet()
        self._load_server_info()
        self.alerts.load()
        self.insights.load()
        self.access_ui.load()

    def _load_overview(self):
        if self._closed:
            return
        if self._overview_busy:
            self._overview_again = True
            return
        self._overview_busy = True
        client = self.client
        if not self._submit(lambda: client.call('overview'), self._overview_finished):
            self._overview_busy = False

    def _set_stale(self, stale):
        """While the server cannot be reached the numbers are only the last ones received. Dim them so they do not look live."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        if stale and self.tabs.graphicsEffect() is None:
            effect = QGraphicsOpacityEffect(self.tabs)
            effect.setOpacity(0.45)
            self.tabs.setGraphicsEffect(effect)
        elif not stale and self.tabs.graphicsEffect() is not None:
            self.tabs.setGraphicsEffect(None)

    def _overview_finished(self, result, success):
        self._overview_busy = False
        self.overview_health.setVisible(not success)
        self._set_stale(not success)
        if success:
            self._overview_result = result
            self._render_overview(result)
            self.server_status.setText(self.t('Server running'))
            self.server_status.setStyleSheet('color: #65D782;')
            self.server_pulse.set_active(True)
        else:
            self.server_status.setText(self.t('Server unavailable'))
            self.server_status.setStyleSheet('color: #9FB0CC;')
            self.server_pulse.set_active(False)
            self.overview_health.setText(self.t('Overview unavailable. Showing the last successful data, if any.'))
        if self._overview_again:
            self._overview_again = False
            self._load_overview()

    def _render_overview(self, result):
        self.alerts.overview(result.get('alerts'))
        for key, code in (('All computers', 'total'), ('Online', 'reporting'),
                          ('Offline', 'report-overdue'), ('Pending approval', 'pending'),
                          ('Approved', 'approved'), ('Denied', 'denied'), ('Disconnected', 'revoked'), ('Unavailable', 'unavailable')):
            self.counts[key].setText(str(result['counts'][code]))
        totals = result.get('totals') or {}
        for key, field in (('Measured', 'measured_seconds'), ('Imported', 'imported_seconds'), ('Baseline', 'baseline_seconds')):
            value = totals.get(field)
            self.fleet_totals[key].setText(self.t('Not available') if value is None else f'{value / 3600:,.2f}')
        self.snapshot_scope.setText(self.t('{known} computers have snapshots; {unknown} have none. Totals include only computers with a snapshot.',
                                            known=result['known_devices'], unknown=result['unknown_devices']))
        self.overview_health.setText(self.t('Service available · schema {version} · server time {time}',
                                            version=result['schema_version'], time=result['server_time']))
        self._render_fleet_events((self._fleet_result or result)['recent_events'])

    def _configuration_edited(self):
        self._configuration_dirty = True

    def _load_configuration(self):
        if self._configuration_busy or self._closed:
            return
        client = self.client
        self._configuration_busy = True
        if not self._submit(lambda: client.call('configuration'), self._configuration_finished):
            self._configuration_busy = False
        else:
            self.uptime.load_retention()

    def _configuration_finished(self, result, success):
        self._configuration_busy = False
        if success:
            self._configuration_result = result
            self._configuration_loaded = True
            if not self._configuration_dirty:
                self.interval.blockSignals(True)
                self.interval.setValue(result['reporting_interval'])
                self.interval.blockSignals(False)
            self._render_configuration(result)
        self.interval.setEnabled(self._configuration_loaded)
        self.interval_button.setEnabled(self._configuration_loaded)

    def _render_configuration(self, result):
        self.retention_boundary.setText(self.t('Current boundary (UTC): {time}',
                                              time=self._timestamp(result.get('retained_before'))))

    def _filters_changed(self):
        self._revision += 1
        self._offset = 0
        self._search_timer.start()

    def _page(self, direction):
        self._offset = max(0, self._offset + direction * self.page_size.currentData())
        self._revision += 1
        self._load_devices()

    def _load_devices(self):
        if self._closed:
            return
        if self._list_busy:
            self._list_again = True
            return
        arguments = dict(limit=self.page_size.currentData(), offset=self._offset,
                         search=self.search.text(), group=self.group.currentData() or '', status=self.status_filter.currentData())
        if self._device_sort:
            arguments.update(sort=self._device_sort[0], descending=self._device_sort[1])
        revision, client = self._revision, self.client
        self._list_busy = True
        chip, order = self._chip, self.device_order.currentData()
        def work():
            all_rows = []
            offset = 0
            while True:
                batch = client.call('devices', **{**arguments, 'offset': offset, 'limit': 500})
                all_rows.extend(batch)
                if len(batch) < 500:
                    break
                offset += 500
            metadata = client.call('fleet', limit=1, search=arguments['search'], group=arguments['group'], status=arguments['status'])
            if chip == 'attention':
                all_rows = [d for d in all_rows if problems(d)]
            elif chip == 'online':
                all_rows = [d for d in all_rows if d.get('reporting_status') == 'reporting']
            elif chip == 'offline':
                all_rows = [d for d in all_rows if d.get('reporting_status') != 'reporting']
            def key(d):
                name = (d.get('identity', {}).get('display_name') or d['device_id']).casefold()
                secondary = name if order == 'name' else (d.get('last_received_at') or '') if order == 'last' else -len(problems(d))
                return (not bool(problems(d)), secondary, name)
            all_rows.sort(key=key)
            metadata['matching'] = len(all_rows)
            page = all_rows[arguments['offset']:arguments['offset'] + arguments['limit']]
            for device in page:
                if device.get('status') == 'approved':
                    device['presence'] = self._listed_presence(client, device['device_id'])
            return page, metadata
        submitted = self._submit(work, lambda result, ok: self._devices_finished(result, ok, revision))
        if not submitted:
            self._list_busy = False

    def _devices_finished(self, rows, success, revision):
        self._list_busy = False
        self._finish_skeletons('devices')
        if success and revision == self._revision:
            rows, metadata = rows
            self._device_matching = metadata['matching']
            chosen = self.group.currentData()
            self.group.blockSignals(True)
            self.group.clear()
            self.group.addItem(self.t('All groups'), '')
            for group in metadata['groups']:
                self.group.addItem(group, group)
            self.identity_fields['department'].set_groups(metadata['groups'])
            self.group.setCurrentIndex(max(0, self.group.findData(chosen)))
            self.group.blockSignals(False)
            self._rows = rows
            for device in rows:
                if (device.get('policy') or {}).get('resources') is not True:
                    self._device_cache.pop(device['device_id'], None)
                if self._detail and self._detail['device_id'] == device['device_id']:
                    self._detail['policy'] = device.get('policy') or {}
                    self._power_state(self._detail)
                    self._render_specs(self._detail)
            self._render_rows(rows)
            if self._open_after_load in self._listed_device_ids():
                self._select_box(self._open_after_load)
                self._open_after_load = None
            # A computer's specs arrive after its first report; do not leave the open panel blank.
            if (self._detail and not self._detail.get('specs') and (self._detail.get('policy') or {}).get('resources') is True
                    and self.selected_device_ids() == [self._detail['device_id']]):
                self._selection_changed()
        if self._list_again or revision != self._revision:
            self._list_again = False
            self._load_devices()

    def selected_device_ids(self):
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def _render_rows(self, rows):
        selected = set(self.selected_device_ids())
        focused = QApplication.focusWidget()
        vertical = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        current = self.table.currentItem()
        current_uuid = current.data(Qt.ItemDataRole.UserRole) if current else None
        current_column = self.table.currentColumn()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_number, device in enumerate(rows):
            identity = device.get('identity', {})
            status = device.get('reporting_status') if device.get('status') == 'approved' else device.get('status')
            unstable = device.get('unstable', False)
            values = [identity.get('display_name') or device['device_id'], identity.get('department', ''),
                      self._fleet_status(status) + (' / ' + self.t('Unstable') if unstable else ''), self._duration(device.get('today')),
                      self._relative(device.get('last_received_at'))]
            for column, value in enumerate(values):
                item = self.table.item(row_number, column)
                if item is None:
                    item = _Item()
                    self.table.setItem(row_number, column, item)
                item.setText(str(value))
                item.setData(Qt.ItemDataRole.UserRole, device['device_id'])
                item.setData(Qt.ItemDataRole.UserRole + 1, device.get('today') if column == 3 else str(value).casefold())
                if column == 2:
                    item.setData(VISUAL_ROLE, ('chip', self._status_kind(status, unstable)))
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 4:
                    item.setToolTip(self._timestamp(device.get('last_received_at')))
                    item.setData(Qt.ItemDataRole.UserRole + 1, device.get('last_received_at') or '')
            box = self.table.cellWidget(row_number, 0)
            if box is None:
                box = DeviceBox()
                box.intro.delay = min(row_number, 8) * 45
                box.activated.connect(self._select_box)
                box.menu_requested.connect(self._device_menu)
                self.table.setCellWidget(row_number, 0, box)
            box.set_device(device, self)
            # 178 px fits a card's usual lines; its row of hourly ticks needs 20 more (a card with a problem line was squeezed).
            self.table.setRowHeight(row_number, max(178 + (20 if not box.presence.isHidden() else 0), box.sizeHint().height()))
        self.table.horizontalHeader().blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().blockSignals(False)
        identifiers = [self.table.item(row_number, 0).data(Qt.ItemDataRole.UserRole) for row_number in range(self.table.rowCount())]
        if current_uuid in identifiers:     # before select: moving current can drop the selection
            self.table.setCurrentCell(identifiers.index(current_uuid), max(0, current_column), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.clearSelection()
        for row_number, identifier in enumerate(identifiers):
            if identifier in selected:
                self.table.selectionModel().select(self.table.model().index(row_number, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(vertical)
        self.table.horizontalScrollBar().setValue(horizontal)
        if focused is not None and not sip.isdeleted(focused) and focused.isVisible():
            focused.setFocus(Qt.FocusReason.OtherFocusReason)
        self.empty_label.setVisible(not rows)
        self.empty_label.set_state('off', self.t('No computers match'),
                                   self.t('No computers match. Choose Add device to connect a computer.'), self.t('Add device'))
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(self._offset + len(rows) < self._device_matching)
        self.page_label.setText(self.t('Showing {start} to {end} of {total} computers',
            start=self._offset + 1 if rows else 0, end=self._offset + len(rows), total=self._device_matching))
        page_size = self.page_size.currentData()
        current_page = self._offset // page_size
        count = max(1, (self._device_matching + page_size - 1) // page_size)
        start = min(max(0, current_page - 1), max(0, count - 3))
        numbers = list(range(start, min(count, start + 3)))
        if getattr(self, '_page_numbers', None) != numbers:
            while self.device_pages.count():
                old = self.device_pages.takeAt(0).widget()
                old.hide()
                old.deleteLater()
            self._page_numbers = numbers
            for number in numbers:
                button = QPushButton(str(number + 1))
                button.setMinimumWidth(max(40, button.sizeHint().width()))
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, page=number: self._go_device_page(page))
                self.device_pages.addWidget(button)
        for i, number in enumerate(numbers):
            button = self.device_pages.itemAt(i).widget()
            button.setChecked(number == current_page)
            button.setAccessibleName(self.t('Page {page}', page=number + 1))
        self._selection_changed()
        if hasattr(self, '_readings_timer'):
            self._readings_timer.start()

    def _go_device_page(self, page):
        self._offset = page * self.page_size.currentData()
        self._revision += 1
        self._load_devices()

    def _timestamp(self, stamp):
        if not stamp:
            return self.t('Not available')
        try:
            moment = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone()
            return self._date(moment.date().isoformat()) + ' · ' + moment.strftime('%H:%M:%S %Z')
        except (ValueError, TypeError, AttributeError):
            return self.t('Not available')

    @staticmethod
    def _status_kind(status, unstable=False):
        """Online is good; offline, waiting for a report and unstable need a look; waiting for approval is
        information; disconnected or unknown is neutral."""
        if unstable or status in ('report-overdue',):
            return 'warning'
        return {'reporting': 'good', 'pending': 'info', 'denied': 'danger'}.get(status, 'neutral')

    def _status_text(self, value):
        return self.t({'pending': 'Pending approval', 'approved': 'Approved', 'denied': 'Denied',
                       'revoked': 'Disconnected', 'reporting': 'Online', 'report-overdue': 'Offline',
                       'unavailable': 'Unavailable'}.get(value, 'Unknown'))

    def _selection_changed(self):
        selected_ids = set(self.selected_device_ids())
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if box:
                box.set_selected(box.device_id in selected_ids)
        selected = self.selected_device_ids()
        self._detail_shown(len(selected) == 1)
        self.export_button.setEnabled(bool(selected))
        self.export_button.setObjectName('primary' if selected else '')
        self.devices_add_button.setObjectName('' if selected else 'primary')     # one amber action at a time
        for button in (self.export_button, self.devices_add_button):
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        if len(selected) != 1:
            self._finish_skeletons('detail')
            self._detail_revision += 1
            self._detail = None
            self._clear_device_detail()
            self.detail_text.setPlainText(self.t('Select a computer to view its usage, permissions and server-owned identity.'))
            self._set_device_actions(False)
            return
        device_id = selected[0]
        if not self._detail or self._detail['device_id'] != device_id:
            self._detail = None
            self._clear_device_detail()
            self._identity_dirty = False
            self._set_device_actions(False)
            self.detail_text.setPlainText(self.t('Loading…'))
            self.skeletons['detail'].start(120)
        self._detail_revision += 1
        revision, client = self._detail_revision, self.client
        self._submit(lambda: client.call('device', device_id=device_id, limit=25, after_seq=0),
                     lambda result, ok: self._detail_finished(result, ok, revision, device_id))
        self._submit(lambda: client.call('presence', device_id=device_id, limit=200),
                     lambda result, ok: self._render_presence(result if ok else None, revision), quiet=True)

    def _listed_presence(self, client, device_id):
        """Runs in the device list's worker thread. One small query per listed computer, at most once a minute each."""
        cached = self._presence_cache.get(device_id)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        try:
            rows = client.call('presence', device_id=device_id, limit=48)
        except Exception:       # monitoring unavailable: the card simply shows no ticks
            rows = None
        self._presence_cache[device_id] = (time.monotonic(), rows)
        return rows

    def _render_presence(self, rows, revision):
        """Hourly ticks for the last 24 hours from the monitoring presence history (online, offline, unknown); an hour
        with no record stays an outline. Hidden when monitoring has nothing for this computer."""
        if revision != self._detail_revision:
            return
        ticks, summary = self.presence_ticks(rows)
        self.presence_box.setVisible(bool(ticks))
        self.presence_bar.set_ticks(ticks, summary)

    def presence_ticks(self, rows):
        """([(kind, tooltip)] for the last 24 hours, one per hour, and the sentence that sums them up); ([], '') when
        monitoring has no record. Shared by the device detail and the cards in the device list."""
        now = dt.datetime.now(dt.timezone.utc)
        start = now - dt.timedelta(hours=24)
        kinds = {'online': 'good', 'offline': 'warning', 'unknown': 'neutral'}
        words = {'good': 'Online', 'warning': 'Offline', 'neutral': 'Unknown', None: 'No record'}
        def moment(stamp):
            return dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')) if stamp else None
        intervals = [(kinds.get(row.get('state'), 'neutral'), moment(row['start_at']), moment(row.get('end_at')))
                     for row in rows or [] if row.get('start_at')]
        if not intervals:
            return [], ''
        states = interval_ticks(intervals, start, now, 24)
        ticks = []
        for index, kind in enumerate(states):
            begin = (start + dt.timedelta(hours=index)).astimezone()
            ticks.append((kind, f"{begin.strftime('%H:%M')}–{(begin + dt.timedelta(hours=1)).strftime('%H:%M')} · {self.t(words[kind])}"))
        online = sum(kind == 'good' for kind in states)
        return ticks, self.t('Last 24 hours: online in {online} of 24 hours', online=online)

    def _detail_finished(self, result, success, revision, device_id):
        if revision != self._detail_revision or self.selected_device_ids() != [device_id]:
            return
        self._finish_skeletons('detail')
        if success:
            same = self._detail and self._detail['device_id'] == device_id
            self._detail = result
            if (result.get('policy') or {}).get('resources') is True:
                self._device_cache[device_id] = (time.monotonic(), result.get('last_received_at'), result)
                while len(self._device_cache) > 32:
                    del self._device_cache[next(iter(self._device_cache))]
            self._render_detail(result, preserve_edits=bool(same and self._identity_dirty))
        else:
            self._set_device_actions(False)

    def _clear_device_detail(self):
        self.detail_name.setText(self.t('Computer details'))
        self.detail_group.clear()
        self.detail_group.hide()
        for edit in self.identity_fields.values():
            edit.clear()
        self.detail_status.hide()
        self.presence_box.hide()
        self.detail_total.setText(self.t('Not available'))
        self.device_id_label.clear()
        self.copy_device_id.setEnabled(False)
        self.device_pending.hide()
        self._render_specs({})
        self._render_remote({})
        self._power_state(None)
        self._fit_detail_text()

    def _render_daily(self, device):
        """Awake hours per local day for the chosen period; missing days stay empty."""
        self.uptime.render_awake(device)

    def _render_remote(self, device):
        """Rebuild the inputs the desktop pages read from its local ledger and probes."""
        policy = device.get('policy') or {}
        history = device.get('history') if policy.get('history') is True else None
        totals = device.get('totals') or {}
        have = bool(history and history.get('daily'))
        self.activity_state.setVisible(not have)
        self.activity_state.setText(self.t('No activity history reported yet' if history is not None or policy.get('history') is True
                                           else 'History is off on this computer') if not have else '')
        if policy.get('history') is not True:
            self.activity_state.setText(self.t('History is off on this computer'))
        self.remote_activity.setVisible(have)
        self.activity_stats.setVisible(have and history.get('boots') is not None)
        if have:
            imported = totals.get('imported_seconds', 0)
            self.remote_activity.set_ledger(dict(
                daily=history.get('daily') or {}, hourly=history.get('hourly', []), sessions=history.get('sessions', []),
                dated_hourly=history.get('dated_hourly', {}), first_seen=(history.get('first_seen') or '')[:10] or None,
                recording_started=history.get('recording_started'),
                total_seconds=totals.get('measured_seconds', 0) + imported, baseline_seconds=totals.get('baseline_seconds', 0),
                imported=dict(seconds=imported, daily=history.get('imported_daily') or {}),
                **({'daily_allocation': {'estimated': True}} if history.get('daily_estimated') else {})))
            if history.get('boots') is not None:
                from lifetime_charts import hm
                self.activity_stats.setText(self.t('Started {boots} times · current session {current} · longest session {longest}',
                    boots=f"{history['boots']:,}", current=hm(history.get('current_session_seconds') or 0),
                    longest=hm(history.get('longest_session_seconds') or 0)))
        else:
            self.remote_activity.set_ledger({})
            self.activity_stats.clear()
        health = (device.get('readings') or {}).get('health') if policy.get('resources') is True else None
        detail = (health or {}).get('detail')
        self.remote_health.setVisible(bool(detail))
        self.health_state.setVisible(not detail)
        self.health_state.setText(self.t('System readings are off on this computer' if policy.get('resources') is not True
                                         else 'No health details reported yet'))
        self.remote_health.set_remote(detail or {})

    def _render_detail(self, device, preserve_edits=False):
        self._render_specs(device)
        self._render_daily(device)
        self._render_remote(device)
        self._power_state(device)
        commands = device.get('commands') or []
        self.command_status.setText(self.t('Command status: {status}', status=self.t('command.' + commands[0]['status'])) if commands else '')
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if box and box.device_id == device['device_id']:
                box.set_device(next((d for d in self._rows if d['device_id'] == box.device_id), device), self)
        missing = self.t('Not available')
        totals = device.get('totals') or {}
        def hours(key):
            value = totals.get(key)
            return missing if value is None else f'{value / 3600:,.2f}'
        identity = device.get('identity', {})
        e = lambda value: html.escape(str(value))
        status = device.get('reporting_status') if device.get('status') == 'approved' else device.get('status')
        self.detail_name.setText(identity.get('display_name') or device['device_id'])
        self.detail_group.show()
        self.detail_group.setText(identity.get('department') or self.t('No group'))
        self.detail_status.set_state(self._status_kind(status), self._fleet_status(status))
        self.detail_status.show()
        total = None if not totals else totals.get('measured_seconds', 0) + totals.get('imported_seconds', 0)
        self.detail_total.setText(missing if total is None else self.t('{hours} h', hours=f'{total / 3600:,.1f}'))
        history_data = device.get('history') if (device.get('policy') or {}).get('history') is True else None
        tracking = (history_data or {}).get('recording_started')
        coverage = self.t('Partial') if (history_data or {}).get('daily') else self.t('Unknown')
        fields = [('Asset tag', identity.get('asset_tag') or missing),
                  ('Department', identity.get('department') or missing),
                  ('Location', identity.get('location') or missing),
                  ('Deployment date', self._date(identity['deployment_date']) if identity.get('deployment_date') else missing),
                  ('Last seen', self._relative(device.get('last_received_at'))),
                  ('Client version', device.get('client_version') or missing),
                  ('Last problem', self._problem_text(device.get('last_problem'))),
                  ('Coverage', coverage),
                  ('Tracking since', self._date(tracking) if tracking else missing)]
        def table(rows):
            return '<table width="100%" cellspacing="0" cellpadding="4">' + ''.join(
                T('<tr><td style="color:#9FB0CC;">') + e(self.t(key)) +
                '</td><td>' + e(value) + '</td></tr>' for key, value in rows) + '</table>'   # no colons, as on Overview
        contents = table(fields) + self.alerts.device_html(device)
        health = (device.get('readings') or {}).get('health') or device.get('health') or {}
        contents += '<p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Health')) + '</p>'
        contents += table([(title, missing if health.get(field) is None else (self._timestamp(health[field]) if field == 'last_crash' else str(health[field]) + ('%' if field == 'battery_health' else ''))) for title, field in (('Crashes in 30 days', 'crashes_30d'), ('Unexpected shutdowns in 30 days', 'shutdowns_30d'), ('Battery health', 'battery_health'), ('Cycle count', 'cycle_count'), ('Last crash', 'last_crash'))])
        contents += '<p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Usage snapshots (hours; never added together)')) + '</p>'
        contents += table([(key, hours(field)) for key, field in
                          (('Measured', 'measured_seconds'), ('Imported', 'imported_seconds'), ('Baseline', 'baseline_seconds'))])
        lines = []
        policy = device.get('policy') or {}
        readings = device.get('readings') if policy.get('resources') is True else None
        if policy.get('resources') is not True:
            lines.append(self.t('Not permitted'))
        elif not readings:
            lines.append(missing)
        else:
            observed = readings.get('observed_at')
            try:
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(observed.replace('Z', '+00:00'))).total_seconds()
                freshness = self.t('Stale report') if age > max(600, self.interval.value() * 3) or device.get('status') != 'approved' else (
                    self.t('Current report') if age >= -300 else self.t('Reading age is unknown.'))
            except (ValueError, TypeError, AttributeError):
                freshness = self.t('Reading age is unknown.')
            lines += [freshness, f"{self.t('Observed at')}: {self._timestamp(observed)}",
                      f"{self.t('Received at')}: {self._timestamp(device.get('readings_received_at'))}"]
            for field, key in (('system', 'System'), ('release', 'Release'), ('machine', 'Machine'), ('python_version', 'Python version')):
                if readings.get(field):
                    lines.append(f'{self.t(key)}: {readings[field]}')
        history = self.t('Not permitted') if policy.get('history') is not True else (
            self.t('Available') if device.get('history') is not None else missing)
        lines += ['', f"{self.t('Optional history')}: {history}"]
        scroll = self.detail_text.verticalScrollBar().value()
        contents += '<p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Optional system readings')) + '</p>'
        contents += '<p style="margin:4px 0">' + '<br>'.join(e(line) for line in lines if line) + '</p>'
        self.detail_text.setHtml(contents)
        self.detail_text.setToolTip(self._timestamp(device.get('last_received_at')))
        self.device_id_label.setText(self.t('Computer ID') + ': ' + device['device_id'])
        self.copy_device_id.setEnabled(True)
        self.detail_text.verticalScrollBar().setValue(scroll)
        if not preserve_edits:
            for field, edit in self.identity_fields.items():
                value = device.get('identity', {}).get(field, '')
                if field == 'deployment_date' and value:
                    value = self.locale().toString(QDate.fromString(value, 'yyyy-MM-dd'), 'd MMM yyyy')
                edit.setText(value)
            self._identity_dirty = False
        selected_request = self.pending_requests.currentData()
        self.pending_requests.clear()
        for request in device.get('pending_requests', []):
            self.pending_requests.addItem(request['request_id'], request['request_id'])
        if self.pending_requests.findData(selected_request) >= 0:
            self.pending_requests.setCurrentIndex(self.pending_requests.findData(selected_request))
        self.device_pending.setVisible(self.pending_requests.count() > 0)
        self._fit_detail_text()
        self._set_device_actions(True)

    def save_identity(self):
        if not self._detail or self.selected_device_ids() != [self._detail['device_id']]:
            return
        identity = {field: edit.text() for field, edit in self.identity_fields.items()}
        deployment = identity['deployment_date']
        if deployment:
            localized = self.locale().toDate(deployment, 'd MMM yyyy')
            if localized.isValid():
                identity['deployment_date'] = localized.toString('yyyy-MM-dd')
        try:
            validate_identity(identity)
        except ValueError:
            self.status_label.setText(self.t('Identity is invalid. Use an optional YYYY-MM-DD deployment date.'))
            return
        self._mutate('identity', dict(device_id=self._detail['device_id'], identity=identity))

    def _known_groups(self):
        return [self.group.itemData(index) for index in range(self.group.count()) if self.group.itemData(index)]

    def edit_details(self, device):
        dialog = DeviceDetailsDialog(device.get('identity') or {}, self._known_groups(), self.t, self)
        if dialog.exec() and not self._closed:
            self._mutate('identity', dict(device_id=device['device_id'], identity=dialog.identity()))
        dialog.deleteLater()

    def manage_groups(self):
        client = self.client

        def run(operation, arguments, done):
            if not self._submit(lambda: client.call(operation, **arguments), done):
                done(None, False)
        dialog = GroupsDialog(self.t, run, self, can_edit=self.access_ui.can('add_group'))
        dialog.exec()
        dialog.deleteLater()        # a late answer is dropped by the dialog's own deleted check
        if dialog.changed and not self._closed:
            self._revision += 1
            self._load_devices()
            self._load_fleet()

    def revoke(self):
        if self._detail and self._confirm('Disconnect this computer? Connecting again needs approval.'):
            self._mutate('revoke', dict(device_id=self._detail['device_id']))

    def delete_device(self, device=None):
        device = device if isinstance(device, dict) else self._detail
        if device and self._confirm('Remove this computer and everything saved about it? This cannot be undone.'):
            self._mutate('delete', dict(device_id=device['device_id']))

    def decide(self, operation):
        request = self.pending_requests.currentData()
        if request and operation in ('approve', 'deny'):
            self._mutate(operation, dict(request_id=request))

    def request_fresh_report(self):
        if self._detail:
            self._device_command(self._detail, 'refresh')

    def _mutate(self, operation, arguments, button=None):
        if self._mutation_busy:
            return
        self._mutation_busy = True
        self._set_device_actions(False)
        client = self.client
        if not self._submit(lambda: client.call(operation, **arguments),
                            lambda result, ok: self._mutation_finished(operation, result, ok), button):
            self._mutation_busy = False
            self._set_device_actions(bool(self._detail))

    def _mutation_finished(self, operation, result, success):
        self._mutation_busy = False
        if success:
            if operation == 'identity':
                self._identity_dirty = False
            if operation == 'set_configuration':
                self._configuration_dirty = False
            self.status_label.setText(self.t('Changes saved.'))
            if operation == 'backup' and isinstance(result, dict) and result.get('monitoring'):
                self.status_label.setText(self.t('Backup saved. Monitoring history is in {name} beside it: keep both files together.',
                                                 name=Path(result['monitoring']).name))
            elif operation in ('backup_archive', 'verify_backup', 'stage_restore'):
                # Nothing on screen changed, so no reload: it would replace this message with "Ready".
                self.status_label.setText(self.access_ui.backup_message(operation, result) or self.t('Changes saved.'))
                self._set_device_actions(bool(self._detail))
                return
            elif operation == 'restore' and isinstance(result, dict):
                self.status_label.setText(self.t(
                    'Restored, including monitoring history and alert settings.' if result.get('monitoring') == 'restored' else
                    'Restored. No monitoring file was beside this backup, so the current alert history and settings were kept.'))
            self._revision += 1
            self._load_devices()
            self._load_overview()
            self._load_fleet()
            self._load_server_info()
            if operation == 'set_configuration':
                self._load_configuration()
            if operation in ('retain', 'restore'):
                self._load_configuration()
        self._set_device_actions(bool(self._detail))

    def _confirm(self, message):
        dialog = QMessageBox(QMessageBox.Icon.Warning, self.t('Continue?'), self.t(message), parent=self)
        yes = dialog.addButton(self.t('Continue'), QMessageBox.ButtonRole.AcceptRole)
        no = dialog.addButton(self.t('Cancel'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(no)
        dialog.setEscapeButton(no)
        dialog.exec()
        return dialog.clickedButton() is yes

    def create_code(self):
        client, directory = self.client, self.directory
        address, ttl, uses = self.server_address.text(), self.code_ttl.value(), self.code_uses.value()
        self.bundle_output.clear()
        self._bundle_expiry = None
        self.code_expiry.clear()
        self._submit(lambda: _create_bundle(client, directory, address, ttl, uses), self._code_finished, self.code_button)

    def _code_finished(self, result, success):
        if success:
            self._bundle_expiry = result['expires_at']
            self.bundle_output.setPlainText(result['bundle'])
            self.code_expiry.setText(local_expiry(result['expires_at'], self.t))

    def save_configuration(self):
        if self._configuration_loaded:
            self._mutate('set_configuration', dict(reporting_interval=self.interval.value()), self.interval_button)

    def apply_retention(self):
        self.retention_date.setMaximumDate(QDate.currentDate())
        if self._confirm(RETAIN_CONFIRM):
            before = self.retention_date.date().toString('yyyy-MM-dd') + 'T00:00:00+00:00'
            self._mutate('retain', dict(before=before), self.retain_button)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save backup'), 'lifetime-server-backup.zip',
                                             self.t('Backup archives (*.zip)'))
        if path:
            self._mutate('backup_archive', dict(path=path if path.lower().endswith('.zip') else path + '.zip'), self.backup_button)

    def verify_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Verify backup'), '', self.t('Backup archives (*.zip)'))
        if path:
            self._mutate('verify_backup', dict(path=path), self.verify_backup_button)

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Restore backup'), '', self.t('Backup archives (*.zip)'))
        if path and self._confirm('Restore this backup when the server next starts? Computers will need a new code and approval to connect again.'):
            self._mutate('stage_restore', dict(path=path), self.restore_button)

    def export_selection(self):
        selected = self.selected_device_ids()
        if selected:
            self.export_requested.emit(selected)

    def _show_export(self, device_ids):
        try:
            from lifetime_server_export import ServerExportDialog
            groups = [self.group.itemData(index) for index in range(1, self.group.count())]
            ServerExportDialog(self.client, device_ids=device_ids, parent=self, language=self.language, groups=groups).exec()
        except Exception:
            self.status_label.setText(self.t('Export could not open. Check the reporting components and try again.'))

    def closeEvent(self, event):
        if self.tray and self.tray.keep(event):
            return
        self._closed = True
        if self.stream is not None:
            self.stream.stop()
        self.timer.stop()
        self.live_timer.stop()
        self._search_timer.stop()
        self._fleet_timer.stop()
        self._readings_timer.stop()
        super().closeEvent(event)

    def _arrange_chrome(self):
        if not hasattr(self, 'chrome_grid'):
            return
        wide = self.width() >= 1180
        navigation = self.tabs.tabBar()
        navigation.set_compact(self.width() < 720)
        compact = self.width() < 600        # too narrow for the switches beside the status: they join the menu's row
        self.chrome_grid.addWidget(self.chrome_brand, 0, 0)
        self.chrome_grid.addWidget(self.chrome_actions, 0, 2 if wide else 1)
        if wide:
            self.chrome_grid.addWidget(navigation, 0, 1, Qt.AlignmentFlag.AlignCenter)
        elif compact:
            self.chrome_grid.addWidget(navigation, 1, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self.chrome_grid.addWidget(navigation, 1, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        # Language and theme sit beside the status and Refresh; on a narrow window they move to the menu's row.
        if compact:
            self.chrome_grid.addWidget(self.chrome_switches, 1, 1, Qt.AlignmentFlag.AlignRight)
        elif self.chrome_actions.layout().indexOf(self.chrome_switches) < 0:
            self.chrome_actions.layout().insertWidget(1, self.chrome_switches)
        self.chrome_grid.setColumnStretch(0, 1)
        self.chrome_grid.setColumnStretch(1, 0 if wide or compact else 1)
        self.chrome_grid.setColumnStretch(2, 1 if wide else 0)
        self.credit.setVisible(self.width() >= 1400)
        self.chrome_states.setVisible(self.width() >= 720)

    def _style_kpis(self):
        for key, value in self.kpis.items():
            if value.text() == self.t('Not available'):     # a placeholder must not read like a number
                value.setStyleSheet('font-size: 15px; font-weight: 400; color: #9FB0CC;')
                continue
            color = '#FFC93C' if key in self._kpi_alerts else '#FFFFFF'
            value.setStyleSheet(f'font-size: 28px; font-weight: 600; color: {color};')

    def _make_skeletons(self):
        self.skeletons = dict(
            hero=Skeleton(self.kpis['Connected'], 'value'),
            offline=Skeleton(self.kpis['Offline'], 'value'),
            awake=Skeleton(self.kpis['Awake today'], 'value'),
            pending=Skeleton(self.kpis['Pending approval'], 'value'),
            fleet=Skeleton(self.fleet_table, 'rows', 6),
            events=Skeleton(self.events, 'lines', 5),
            chart=Skeleton(self.daily_chart, 'chart'),
            devices=Skeleton(self.table, 'cards', 6),
            detail=Skeleton(self.device_detail_scroll, 'lines', 14, inset=2),
            **{f'host{i}': Skeleton(panel, 'lines', 4, inset=2) for i, panel in enumerate(self.host_panels)})
        self.empty_label.hide()     # not knowable until the first answer
        for key, skeleton in self.skeletons.items():
            if key != 'detail':
                skeleton.start()

    def _finish_skeletons(self, *keys):
        for key in keys:
            self.skeletons[key].finish()

    def _update_busy(self):
        busy = any(number not in self._quiet_jobs for number in self._jobs)
        self.loading_bar.set_busy(busy)
        if self._manual_refresh and not busy:
            self._manual_refresh = False
            # Loopback answers in milliseconds; hold the state long enough to be seen.
            later(450 - (time.monotonic() - self._refresh_started) * 1000, self._refresh_done)

    def _refresh_done(self):
        if not self._closed:
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText(self.t('Refresh'))

    def manual_refresh(self):
        self._manual_refresh, self._refresh_started = True, time.monotonic()
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText(self.t('Refreshing…'))
        self.refresh()
        self._update_busy()

    def _arrange_overview(self):
        wide = self.width() >= 1200
        self.fleet_grid.addWidget(self.overview_left, 0, 0)
        self.fleet_grid.addWidget(self.overview_right, 0 if wide else 1, 1 if wide else 0)
        self.fleet_grid.setColumnStretch(0, 7)
        self.fleet_grid.setColumnStretch(1, 3 if wide else 0)
        self.fleet_grid.setAlignment(self.overview_left, Qt.AlignmentFlag.AlignTop)
        self.fleet_grid.setAlignment(self.overview_right, Qt.AlignmentFlag.AlignTop)
        across = 4 if self.width() >= 900 else 2 if self.width() >= 560 else 1
        for index, tile in enumerate(self.tiles):
            self.tile_grid.addWidget(tile, index // across, index % across)
        self._style_kpis()
        self.today_label.setVisible(wide)
        columns = 2 if self.width() >= 900 else 1
        for index, panel in enumerate(getattr(self, 'host_panels', ())):
            self.host_grid.addWidget(panel, index // columns, index % columns)
        self._arrange_chrome()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'fleet_grid'):
            self._arrange_overview()
        if hasattr(self, 'device_grid'):
            self._arrange_devices()

    def _fleet_sort_clicked(self, column):
        header = self.fleet_table.horizontalHeader()
        order = Qt.SortOrder.DescendingOrder if header.sortIndicatorSection() == column and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        header.setSortIndicator(column, order)

    def _fleet_filters_changed(self, *unused):
        self._fleet_offset = 0
        self._fleet_revision += 1
        self._fleet_timer.start()

    def _fleet_page(self, direction):
        self._fleet_offset = max(0, self._fleet_offset + direction * 6)
        self._fleet_revision += 1
        self._load_fleet()

    def _show_today(self):
        locale = QLocale('vi_VN' if self.language == 'vi' else 'en_GB')
        self.today_label.setText(self.t('Today · {date}', date=locale.toString(QDate.currentDate(), 'd MMM yyyy')))

    def _roll_to_today(self):
        """Left open past midnight, "today" moves on. Follow it, unless someone picked another date."""
        today = QDate.currentDate()
        if today == self._today:
            return
        follows = self.fleet_date.date() == self._today
        self._today = today
        self._show_today()
        if follows:
            self.fleet_date.blockSignals(True)      # the load below already uses the new date
            self.fleet_date.setDate(today)
            self.fleet_date.blockSignals(False)

    def _load_fleet(self):
        if self._closed:
            return
        if self._fleet_busy:
            self._fleet_again = True
            return
        self._roll_to_today()
        header = self.fleet_table.horizontalHeader()
        arguments = dict(day=self.fleet_date.date().toString('yyyy-MM-dd'), **self.uptime.fleet_arguments(),
                         limit=6, offset=self._fleet_offset, search=self.fleet_search.text(),
                         group=self.fleet_group.currentData() or '',
                         sort=('name', 'group', 'status', 'today', 'last')[max(0, header.sortIndicatorSection())],
                         descending=header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder)
        revision, client = self._fleet_revision, self.client
        self._fleet_busy = True
        if not self._submit(lambda: self.uptime.call_fleet(client, arguments),
                            lambda result, ok: self._fleet_finished(result, ok, revision)):
            self._fleet_busy = False

    def _fleet_finished(self, result, success, revision):
        self._fleet_busy = False
        self._finish_skeletons('hero', 'offline', 'awake', 'pending', 'fleet', 'events', 'chart')
        if success and revision == self._fleet_revision:
            self._fleet_result = result
            self._render_fleet(result)
        elif revision == self._fleet_revision:
            self.uptime.fleet_failed()       # no figures from another period under this period's name
        if self._fleet_again or revision != self._fleet_revision:
            self._fleet_again = False
            self._load_fleet()

    def _duration(self, seconds):
        if seconds is None:
            return self.t('Not available')
        minutes = int(seconds // 60)
        return self.t('{hours} h {minutes:02} m', hours=minutes // 60, minutes=minutes % 60)

    def _relative(self, stamp):
        if not stamp:
            return self.t('Not available')
        try:
            elapsed = max(0, int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds()))
            if elapsed < 60:
                return self.t('{n} s ago', n=elapsed)
            if elapsed < 3600:
                return self.t('{n} min ago', n=elapsed // 60)
            if elapsed < 86400:
                return self.t('{n} h ago', n=elapsed // 3600)
            return self._date(dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().date().isoformat())
        except (ValueError, TypeError):
            return self.t('Not available')

    def _render_fleet(self, result):
        counts = result['counts']
        is_today = result.get('date') == QDate.currentDate().toString('yyyy-MM-dd')
        self.kpi_titles['Awake today'].setText(self.t('Awake today' if is_today else 'Awake on selected date'))
        self.fleet_table.horizontalHeaderItem(3).setText(self.t('Awake today' if is_today else 'Awake time'))
        self.kpis['Connected'].setText(str(counts['connected']))
        self.kpi_notes['Connected'].setText(self.t('of {count} computers', count=counts['enrolled']))
        summary = self.t('{connected} of {total} computers online', connected=counts['connected'], total=counts['enrolled'])
        self.gauge_note.setText(summary)
        self.fleet_gauge.set_counts(counts['connected'], counts['enrolled'], summary)
        self.kpis['Offline'].setText(str(counts['overdue']))
        self.kpi_notes['Offline'].setText(self.t('Not heard from recently'))
        self._kpi_alerts = {key for key, count in (('Offline', counts['overdue']), ('Pending approval', counts['pending'])) if count}
        self._style_kpis()
        self.kpis['Awake today'].setText(self._duration(result['today']['seconds']))
        self.kpi_notes['Awake today'].setText(self.t('{count} reporting computers', count=result['today']['devices']))
        self.kpis['Pending approval'].setText(str(counts['pending']))
        self._style_kpis()
        self._overview_data = True
        self._arm_overview_entrance()
        self.kpi_notes['Pending approval'].setText(self.t('Administrator review required'))
        self.fleet_empty.setVisible(counts['total'] == 0)
        self.fleet_empty.set_state('setup', self.t('No computers yet'),
                                   self.t('Add a computer with a one-time code; it shows here once you approve it.'), self.t('Add device'))
        waiting = max(0, counts['enrolled'] - counts['connected'] - counts['overdue'])
        self.state_strip.set_counts([(key, kind, self.t(words), number) for key, kind, words, number in (
            ('online', 'good', 'Online', counts['connected']), ('offline', 'warning', 'Offline', counts['overdue']),
            ('waiting', 'neutral', 'No report yet', waiting), ('pending', 'info', 'Pending approval', counts['pending'])) if number],
            self.t('{connected} of {total} computers online', connected=counts['connected'], total=counts['enrolled']))
        self.state_strip.setVisible(counts['total'] > 0)
        selected_group = self.fleet_group.currentData()
        self.fleet_group.blockSignals(True)
        self.fleet_group.clear()
        self.fleet_group.addItem(self.t('All groups'), '')
        for group in result['groups']:
            self.fleet_group.addItem(group, group)
        index = self.fleet_group.findData(selected_group)
        self.fleet_group.setCurrentIndex(max(0, index))
        self.fleet_group.blockSignals(False)
        table = self.fleet_table
        table.blockSignals(True)
        table.setRowCount(len(result['rows']))
        table.setFixedHeight(table.horizontalHeader().height() +
                             max(1, len(result['rows'])) * table.verticalHeader().defaultSectionSize() + 16)
        for row_index, row in enumerate(result['rows']):
            values = (row['name'], row['group'] or self.t('No group'),
                      self._fleet_status(row['status']) + (' / ' + self.t('Unstable') if row.get('unstable') else ''), self._duration(row['today']), self._relative(row['last']))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row['device_id'])
                item.setToolTip(str(value) if column != 4 else (row['last'] or value))
                if column == 2:     # a chip: glyph and words, the colour is never the only signal
                    item.setData(VISUAL_ROLE, ('chip', self._status_kind(row['status'], row.get('unstable'))))
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row_index, column, item)
            if row['device_id'] == self._fleet_selected:
                table.selectRow(row_index)
        table.blockSignals(False)
        self._fleet_selection_changed()
        self.fleet_previous.setEnabled(self._fleet_offset > 0)
        self.fleet_next.setEnabled(self._fleet_offset + len(result['rows']) < result['matching'])
        self.fleet_page_number.setText(str(self._fleet_offset // 6 + 1))
        self.fleet_page_label.setText(self.t('Showing {start} to {end} of {total} computers',
            start=self._fleet_offset + 1 if result['rows'] else 0, end=self._fleet_offset + len(result['rows']), total=result['matching']))
        self.uptime.render_fleet_chart(result)
        self.updated_label.setText(self.t('Updated {time}', time=self._clock_text(result['updated_at'])))
        self._render_fleet_events(result['recent_events'])

    def _date(self, value, year=True):
        """One date style everywhere, in the window's language: '8 Nov 2026' / '8 thg 11, 2026'."""
        day = dt.date.fromisoformat(str(value)[:10])
        return self.t('date.full' if year else 'date.short', day=day.day, month=self.t(f'month.{day.month}'), year=day.year)

    def _state_picked(self, key):
        """A state chip on Overview opens Devices showing just those computers."""
        if key == 'pending':
            return self.review_pending()
        self.tabs.setCurrentIndex(1)
        self._set_chip(key if key in ('online', 'offline') else 'all')

    def _fleet_status(self, status):
        return self.t('Connected') if status == 'reporting' else self._status_text(status)

    def _fleet_selection_changed(self):
        indexes = self.fleet_table.selectionModel().selectedRows()
        identifier = self.fleet_table.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole) if indexes else None
        self._fleet_selected = identifier
        row = next((row for row in (self._fleet_result or {}).get('rows', []) if row['device_id'] == identifier), None)
        self._render_fleet_detail(row)

    def _render_fleet_detail(self, row):
        self.open_device_button.setEnabled(bool(row))
        self.today_bar.setVisible(bool(row and row['today'] is not None))
        if not row:
            self.fleet_detail_text.setText('<p style="font-size:18px; font-weight:600; margin:0 0 4px 0">' + html.escape(self.t('Computer details')) + '</p><p>' +
                                          html.escape(self.t('Select a computer to view its recorded usage.')) + '</p>')
            return
        e = lambda value: html.escape(str(value))
        totals = row['totals']
        total = None if totals is None else totals['measured_seconds'] + totals['imported_seconds']
        hours = self.t('Not available') if total is None else self.t('{hours} h', hours=f'{total / 3600:,.1f}')
        fields = (('Asset tag', row['identity'].get('asset_tag') or self.t('Not available')),
                  ('Last seen', self._relative(row['last'])),
                  ('Client version', row.get('client_version') or self.t('Not available')),
                  ('Coverage', self.t('Partial') if row['coverage'] == 'partial' else self.t('Unknown')),
                  ('Tracking since', self._date(row['tracking_since']) if row.get('tracking_since') else self.t('Not available')))
        contents = '<p style="font-size:18px; font-weight:600; margin:0 0 4px 0">' + e(row['name']) + '</p>'
        contents += T('<p style="color:#9FB0CC;margin:4px 0">') + e(row['group'] or self.t('No group')) + ' · ' + e(self._fleet_status(row['status'])) + '</p>'
        contents += '<p style="font-size:32px;font-weight:600;margin:8px 0 2px">' + e(hours) + T('</p><p style="color:#9FB0CC;margin:2px 0 8px">') + e(self.t('Recorded awake time')) + '</p>'
        contents += '<table width="100%" cellspacing="0" cellpadding="3">'
        for key, value in fields:
            contents += T('<tr><td style="color:#9FB0CC">') + e(self.t(key)) + '</td><td>' + e(value) + '</td></tr>'
        contents += '</table><p style="font-weight:600;margin:12px 0 4px">' + e(self.t('Health')) + '</p><table width="100%" cellspacing="0" cellpadding="3">'
        health = row.get('health') or {}
        for title, field in (('Crashes in 30 days', 'crashes_30d'), ('Unexpected shutdowns in 30 days', 'shutdowns_30d'),
                             ('Battery health', 'battery_health'), ('Cycle count', 'cycle_count'), ('Last crash', 'last_crash')):
            value = health.get(field)
            value = self.t('Not available') if value is None else (self._timestamp(value) if field == 'last_crash' else str(value) + ('%' if field == 'battery_health' else ''))
            contents += T('<tr><td style="color:#9FB0CC">') + e(self.t(title)) + '</td><td>' + e(value) + '</td></tr>'
        contents += '</table><p style="margin:8px 0 0">' + e(self.t('Selected date')) + ' · ' + e(self._duration(row['today'])) + '</p>'
        self.fleet_detail_text.setText(contents)
        self.today_bar.setValue(int(row['today'] or 0))

    def _render_fleet_events(self, events):
        # Recent means recent: the latest five fit the panel without a scrollbar; the full log lives in Administration.
        events = list(events)[:5]
        kinds = {'report-received': 'Synchronized', 'report-overdue': 'Went offline',
                 'enrollment-pending': 'Awaiting approval', 'revoked': 'Disconnected', 'invitation-used': 'Invitation used',
                 'command-pending': 'Command sent', 'command-delivered': 'Command delivered', 'command-done': 'Command finished',
                 'command-failed': 'Command failed', 'command-cancelled': 'Command cancelled', 'command-expired': 'Command expired',
                 'wake': 'Turn on sent'}
        chips = {'report-received': 'good', 'report-overdue': 'warning', 'enrollment-pending': 'info', 'command-failed': 'danger',
                 'revoked': 'neutral'}
        self.events.set_events([(chips.get(event['kind'], 'neutral'), self.t(kinds.get(event['kind'], 'Unknown')),
                                 self._clock_text(event['time'])[:5], event.get('name') or event.get('device_id') or '')
                                for event in events], self.t('Recent events'))      # HH:MM; the tooltip has the full stamps
        self.events.setToolTip('\n'.join(f"{self._timestamp(event['time'])} · {event.get('name') or event.get('device_id') or ''}" for event in events))
        self.events.setVisible(bool(events))
        self.events_empty.setVisible(not events)

    @staticmethod
    def _clock_text(stamp):
        return dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%H:%M:%S')

    def open_fleet_device(self, *unused):
        if not self._fleet_selected:
            return
        self.tabs.setCurrentIndex(1)
        if self._fleet_selected in self._listed_device_ids():
            self._select_box(self._fleet_selected)
        else:   # on another page of the list or filtered out: narrow the list to it, then select
            self._open_after_load = self._fleet_selected
            # A status, group or chip filter left on (e.g. by Review pending) would hide it: "No computers match".
            self.group.setCurrentIndex(0)
            self._set_chip('all')
            self.search.setText(self._fleet_selected)

    def _listed_device_ids(self):
        return [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.table.rowCount())]

    def review_pending(self):
        # Requests can belong to an already approved computer reconnecting.
        # Device-status filters cannot represent that queue; show the actual decisions.
        self.tabs.setCurrentIndex(2)
        self._load_server_info()
        self._review_approvals = True
        self.tabs.widget(2).ensureWidgetVisible(self.admin_pending)

    def _load_server_info(self):
        if self._info_busy:
            self._info_again = True
            return
        self._info_busy = True
        client = self.client
        invitation_offset = self._invitation_offset
        def work():
            return dict(info=client.call('server_info'), invitations=client.call('invitations', limit=100, offset=invitation_offset),
                        pending=client.call('pending_enrollments'), log=client.call('server_log', limit=50))
        if not self._submit(work, self._server_info_finished):
            self._info_busy = False

    def _server_info_finished(self, result, success):
        self._info_busy = False
        self._finish_skeletons(*(f'host{i}' for i in range(len(self.host_panels))))
        if success:
            self._render_server_info(result)
            if getattr(self, "_review_approvals", False) and self.tabs.currentIndex() == 2:
                self.tabs.widget(2).ensureWidgetVisible(self.admin_pending)
                self._review_approvals = False
        if self._info_again:
            self._info_again = False
            self._load_server_info()

    def _render_server_info(self, result):
        self._admin_result = result
        self._server_info = info = result['info']
        self._render_this_server(info)
        if not self.organisation_edit.hasFocus():
            self.organisation_edit.setText(info.get('organisation', ''))
        self.server_address.setText(info['address'])
        self.network_label.setText((info.get('organisation') or info['name']) + ' · ' + self.t('Local network'))
        self.local_server.setText(self.t('Local server: {name}', name=info['name']))
        self.server_information.setText(f"{info['host']}:{info['port']} · {info['address']}")
        self.fingerprint.setText(info['fingerprint'])
        certificate = info.get('certificate') or {}
        self.certificate_expiry.setText(self._certificate_text(certificate))
        if not self.names_edit.hasFocus():
            self.names_edit.setText(', '.join(info.get('server_names') or []))
        self.names_edit.setAccessibleName(self.t('Other server names and IPs'))
        failed = certificate.get('warning') == 'renewal-failed'
        self.certificate_warning.setText(self.t('Lifetime Server could not renew its security certificate. The current one works until {date}. Check the server names under Administration.',
                                                date=self._date(certificate['expires'])) if failed and certificate.get('expires') else '')
        self.certificate_warning.setVisible(bool(self.certificate_warning.text()))
        self._render_server_log(result.get('log') or [])
        self.server_name.setText(info['name'])
        selections = {}
        for table in (self.invitation_table, self.admin_pending):
            indexes = table.selectionModel().selectedRows()
            selections[table] = table.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole) if len(indexes) == 1 else None
            table.clearSelection()
        self.invitation_table.setRowCount(len(result['invitations']))
        self._fit_rows(self.invitation_table, len(result['invitations']))
        self.invitation_previous.setEnabled(self._invitation_offset > 0)
        self.invitation_next.setEnabled(len(result['invitations']) == 100)
        for table in (self.admin_pending, self.invitation_table):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    wrapper = table.cellWidget(row, column)
                    if wrapper:
                        wrapper.hide()
        self.admin_pending.setRowCount(len(result['pending']))
        self._fit_rows(self.admin_pending, len(result['pending']))
        self.approvals_empty.setVisible(not result['pending'])
        self.admin_pending.setVisible(bool(result['pending']))
        self._admin_row_actions = []
        for index, request in enumerate(result['pending']):
            for column, value in enumerate((request['name'], self._relative(request['created_at']))):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, request['request_id'])
                item.setToolTip(self._timestamp(request['created_at']) if column == 1 else value)
                self.admin_pending.setItem(index, column, item)
            for column, operation, key in ((2, 'approve', 'Approve'), (3, 'deny', 'Deny')):
                button = QPushButton(self.t(key))
                button.setAccessibleName(self.t(key) + ': ' + request['name'])
                if operation == 'approve':
                    button.setObjectName('primary')
                button.clicked.connect(lambda checked=False, op=operation, identifier=request['request_id']:
                                       self._mutate(op, dict(request_id=identifier)))
                self.admin_pending.setCellWidget(index, column, table_action(button))
                self._admin_row_actions.append(button)
        self._invitation_row_actions = []
        for index, invitation in enumerate(result['invitations']):
            state = 'Revoked' if invitation['revoked'] else 'Locked' if invitation['failures'] >= 5 else 'Used' if not invitation['remaining'] else 'Expired' if dt.datetime.fromisoformat(invitation['expires_at'].replace('Z', '+00:00')) < dt.datetime.now(dt.timezone.utc) else 'Active'
            # The column is already headed "Expires", so cells show only the date and time.
            expires = dt.datetime.fromisoformat(invitation['expires_at'].replace('Z', '+00:00')).astimezone()
            expires_text = self._date(expires.date()) + ' · ' + expires.strftime('%H:%M')
            for column, value in enumerate((invitation['invitation_id'], expires_text,
                                            invitation['remaining'], invitation['failures'], self.t(state))):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, invitation['invitation_id'])
                if column == 1:
                    item.setToolTip(self._timestamp(invitation['expires_at']))
                if column == 4:
                    item.setData(VISUAL_ROLE, ('chip', 'good' if state == 'Active' else 'neutral'))
                self.invitation_table.setItem(index, column, item)
            button = QPushButton(self.t('Revoke'))
            button.setAccessibleName(self.t('Revoke invitation') + ': ' + invitation['invitation_id'])
            button.setStyleSheet('color: #FF8A8A;')
            button.clicked.connect(lambda checked=False, identifier=invitation['invitation_id']:
                                   self._mutate('revoke_invitation', dict(invitation_id=identifier)))
            button.setProperty('revoked', invitation['revoked'])
            # Only a live invitation can be revoked; used, expired, locked or revoked ones have nothing to stop.
            if state != 'Active':
                button.setEnabled(False)
                button.setStyleSheet('')
            self.invitation_table.setCellWidget(index, 5, table_action(button))
            self._invitation_row_actions.append(button)
        for table, identifier in selections.items():
            for index in range(table.rowCount()):
                if table.item(index, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                    table.selectRow(index)
                    break
        self._admin_selection_changed()

    def _problem_text(self, problem):
        """'No answer (timeout) · 3 h ago': a category the computer or server recorded, never error text."""
        if not problem:
            return self.t('None reported')
        kind = str(problem.get('kind'))
        label = (self.t('Server error {code}', code=kind[5:]) if kind.startswith('http-')
                 else self.t(PROBLEM_TEXT.get(kind, 'Unknown')))
        return label + ' · ' + self._relative(problem.get('at'))

    @staticmethod
    def _fit_rows(table, rows, most=4):
        """Tall enough for every row up to `most`, then it scrolls. A fixed height cut rows in half (Pending approvals
        was 140 px for 56 px rows)."""
        table.setFixedHeight(table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth() + 2 +
                             table.verticalHeader().defaultSectionSize() * min(most, max(1, rows)))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if rows > most else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _render_server_log(self, log):
        """The server log as an event rail, newest at the top: the event in words, then when and which computer."""
        rows = []
        for entry in log:
            event = self.t(LOG_TEXT.get((entry.get('kind'), entry.get('category')), 'Unknown'))
            if entry.get('count', 1) > 1:
                event = self.t('{event} ({count} times)', event=event, count=entry['count'])
            rows.append((LOG_KIND.get(entry.get('kind'), 'neutral'), event, self._timestamp(entry.get('time')),
                         entry.get('name') or entry.get('device_id') or ''))
        self.server_log.set_events(rows, self.t('Server log'))
        self.server_log_scroll.setVisible(bool(log))
        self.server_log_empty.setVisible(not log)

    def _certificate_text(self, certificate):
        """'8 Dec 2027 · renewed automatically' plus the names it covers; None when unknown."""
        if not certificate.get('expires'):
            return None
        names = ', '.join(certificate.get('names') or [])
        return self.t('{date} · renewed automatically', date=self._date(certificate['expires'])) + (f'\n{names}' if names else '')

    def save_names(self):
        names = [part.strip() for part in self.names_edit.text().replace(';', ',').split(',') if part.strip()]
        self._mutate('names', dict(names=names), self.names_button)

    def add_device(self):
        if self._issuing_invitation:
            return
        client = self.client
        ttl, uses = self.invitation_hours.value() * 3600, self.invitation_uses.value()
        def work():
            info = client.call('server_info')
            issued = client.call('invitation', ttl=ttl, uses=uses)
            return dict(info=info, issued=issued)
        self._issuing_invitation = True
        self.invitation_button.setEnabled(False)
        if not self._submit(work, self._invitation_created, self.add_device_button):
            self._issuing_invitation = False
            self.invitation_button.setEnabled(True)

    def _admin_selection_changed(self):
        for button in self._admin_row_actions:
            button.setEnabled(not self._mutation_busy)
        for button in self._invitation_row_actions:
            button.setEnabled(not self._mutation_busy and not button.property('revoked'))

    def _invitation_created(self, result, success):
        self._issuing_invitation = False
        self.invitation_button.setEnabled(True)
        if success:
            groups = self._known_groups()
            self.add_device_sheet = AddDeviceSheet(result['issued'], result['info']['address'], self.t, self, client=self.client,
                                                   groups=groups)
            self.add_device_sheet.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.add_device_sheet.show()
            self._invitation_offset = 0
            self._load_server_info()

    def _invitation_page(self, direction):
        if not self._info_busy:
            self._invitation_offset = max(0, self._invitation_offset + direction * 100)
            self._load_server_info()

    def revoke_invitation(self):
        indexes = self.invitation_table.selectionModel().selectedRows()
        if len(indexes) == 1:
            identifier = self.invitation_table.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            self._mutate('revoke_invitation', dict(invitation_id=identifier))

    def _device_sort_changed(self, column, order):
        self._device_sort = (('name', 'group', 'status', 'today', 'last')[max(0, column)], order == Qt.SortOrder.DescendingOrder)
        if hasattr(self, '_search_timer'):
            self._filters_changed()


def read_theme_preference(directory):
    try:
        value = json.loads((Path(directory) / 'ui.json').read_text(encoding='utf-8')).get('theme')
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in ('auto', 'light', 'dark') else 'auto'


def read_style_preference(directory):
    try:
        value = json.loads((Path(directory) / 'ui.json').read_text(encoding='utf-8')).get('style')
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in STYLES else 'glass'


def write_style_preference(directory, value):
    _write_ui(directory, 'style', value if value in STYLES else 'glass')


def write_theme_preference(directory, value):
    _write_ui(directory, 'theme', value if value in ('auto', 'light', 'dark') else 'auto')


def _write_ui(directory, key, value):
    path = Path(directory) / 'ui.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data[key] = value
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(data), encoding='utf-8')
        os.replace(temporary, path)
    except OSError:
        pass        # a dashboard without write access to the data folder (a Linux lifetime-admin member) forgets it


def server_icon_path(extension=None):
    """The server's own icon (navy and cream swapped against the desktop app's), or the shared one if not shipped.
    The window and taskbar take the platform's format; the header logo inside the window asks for the PNG."""
    assets = Path(__file__).resolve().parent / 'assets'
    extension = extension or ('ico' if sys.platform == 'win32' else 'png')
    own = assets / f'lifetime-server.{extension}'
    return own if own.exists() else assets / f'lifetime.{extension}'


def create_application(argv=None):
    """The one place the dashboard's Qt application is configured.

    Without the explicit UI font the window fell back to a smaller default font, its panels lost their
    backgrounds and the layout grew wider than a 2560 px screen. Set it before any widget exists.
    """
    existing = QApplication.instance()
    if existing is not None:
        return existing
    if sys.platform == 'win32':
        # Source launches use pythonw.exe; give the dashboard its own taskbar group.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Lifetime.Server')
        except (AttributeError, OSError):
            pass
    configure_high_dpi()
    application = QApplication(list(argv or sys.argv[:1]))
    application.setDesktopFileName('lifetime-server')
    application.setFont(QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans', 11))
    application.setWindowIcon(QIcon(str(server_icon_path())))
    return application


def show_fitted(window):
    """Open maximized so the dashboard always fits the screen it starts on."""
    window.showMaximized()
    window.start_stream()
    window.tray = install_tray(window, 'Lifetime Server', window.server_status.text, key='lifetime-server-gui', translate=window.t)


LINUX_DATA = '/var/lib/lifetime-server'
LINUX_CONFIG = '/etc/lifetime-server/server.conf'


def linux_admin_port(config=LINUX_CONFIG, sockets='/run/lifetime-server'):
    """The loopback administration port install_server_linux.sh wrote, so the menu entry needs no arguments.
    server.conf is readable only by root and the service account; for anyone else the running server's one socket
    (/run/lifetime-server/admin-PORT.sock, a searchable folder) names the port."""
    try:
        for line in Path(config).read_text(encoding='utf-8').splitlines():
            key, _, value = line.strip().partition('=')
            if key == 'LIFETIME_ADMIN_PORT' and value.isdigit():
                return int(value)
    except OSError:
        pass
    try:
        ports = [name[6:-5] for name in os.listdir(sockets) if name.startswith('admin-') and name.endswith('.sock')]
    except OSError:
        ports = []
    return int(ports[0]) if len(ports) == 1 and ports[0].isdigit() else 8444


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lifetime Server local dashboard')
    linux = sys.platform.startswith('linux')     # a server on this computer: its standard folder and settings
    parser.add_argument('--directory', required=not linux, default=LINUX_DATA if linux else None)
    parser.add_argument('--admin-port', type=int, required=not linux, default=linux_admin_port() if linux else None)
    parser.add_argument('--language', choices=('en', 'vi'), default='en')
    parser.add_argument('--theme', choices=('auto', 'light', 'dark'), default=None)
    args = parser.parse_args(argv)
    application = create_application()
    set_theme(resolve_theme(args.theme or read_theme_preference(args.directory)))
    if tray_available() and tell_running_instance('lifetime-server-gui'):
        return 0
    window = ServerWindow(args.directory, args.admin_port, args.language)
    show_fitted(window)
    code = application.exec()
    shut_down(application, window)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
