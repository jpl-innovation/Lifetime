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
                            QFileDialog, QFormLayout, QGridLayout,
                            QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                            QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QHeaderView,
                            QSizePolicy, QSpinBox, QMenu, QTableWidget,
                            QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from lifetime_admin import AdminClient
import lifetime_i18n as catalog
from lifetime_protocol import CLIENT_VERSION, IDENTITY_FIELDS, validate_identity
from lifetime_theme import THEMES, stylesheet, server_surfaces, install, configure_high_dpi, T, set_theme, resolve_theme, active_theme, retint_all, CONTROL_RADIUS
import json
import os
from lifetime_layout import ControlRow
from lifetime_tasks import Tasks
from lifetime_tls import encode_bundle
from lifetime_server_widgets import AddDeviceSheet, DailyChart, local_expiry, reduced_motion, status_dot, table_action
from lifetime_reveal import RollLabel, fade_in
from lifetime_server_devices import (glass, Glass, ServerPages, DeviceBox, ATTENTION, problems, byte_text, Skeleton,
                                     LoadingBar, StatusNotice, TileIcon, FleetGauge, RowDelegate)
from lifetime_charts import Plot
from lifetime_shell import PulseDot, install_tray, shut_down, tray_available, tell_running_instance, themed_logo, theme_reveal

SERVER_TEXT_VI = catalog.SERVER_DASHBOARD_VI


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
        self.tasks.failed.connect(lambda number, error: self._completed(number, None, False))
        self._jobs = {}
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
        self.setStyleSheet(stylesheet() + server_surfaces() + """
            QMainWindow, QDialog { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2353A0, stop:0.28 #15306A, stop:0.55 #0B1426, stop:0.8 #3A3220, stop:1 #8A6516); }
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
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets/lifetime.png')))
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
        logo.setPixmap(QPixmap(str(Path(__file__).parent / 'assets/lifetime.png')).scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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
        self.network_label = self.label('Local network')
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
        self.language_choice = QComboBox()
        self.language_choice.addItem('English', 'en')
        self.language_choice.addItem('Tiếng Việt', 'vi')
        self.language_choice.setCurrentIndex(0 if language == 'en' else 1)
        self.language_choice.setAccessibleName(self.t('Language'))
        self.language_choice.setMinimumWidth(164)
        self.language_choice.setMaximumWidth(180)
        self.theme_choice = QComboBox()
        for key, name in (('auto', 'Auto'), ('light', 'Light'), ('dark', 'Dark')):
            self.theme_choice.addItem(self.t(name), key)
        saved = read_theme_preference(self.directory)
        self.theme_choice.setCurrentIndex(('auto', 'light', 'dark').index(saved))
        self.theme_choice.setAccessibleName(self.t('Theme'))
        self.theme_choice.setMinimumWidth(164)
        self.theme_choice.setMaximumWidth(180)
        self.theme_choice.currentIndexChanged.connect(self._theme_changed)
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
        self._make_overview()
        self._make_devices()
        self._make_admin()
        self._make_this_server()
        self.local_server = QLabel(self.t('Local server: {name}', name=socket.gethostname()), body)
        self.local_server.hide()
        self._arrange_chrome()
        self._manual_refresh = False
        self._refresh_started = 0.0
        self._make_skeletons()
        self.language_choice.currentIndexChanged.connect(self._language_changed)
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
        self.chart_range.currentIndexChanged.connect(self._fleet_filters_changed)
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
        self.fleet_date = QDateEdit(QDate.currentDate())
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
        self.fleet_empty = self.button('No devices yet. Add device', self.add_device)
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
        self.bind(self.fleet_search, 'Search devices', 'setPlaceholderText')
        self.fleet_search.setMaximumWidth(300)
        filters.addWidget(self.fleet_search)
        self.fleet_group = QComboBox()
        self.fleet_group.setMinimumWidth(150)
        self.fleet_group.addItem(self.t('All groups'), '')
        self.fleet_group.setAccessibleName(self.t('Group'))
        filters.addWidget(self.fleet_group)
        fleet_layout.addLayout(filters)
        self.fleet_table = QTableWidget(0, 5)
        self.fleet_table.setAccessibleName(self.t('Fleet devices'))
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
        self.open_device_button = self.button('Open device', self.open_fleet_device)
        detail_layout.addWidget(self.open_device_button, 0, Qt.AlignmentFlag.AlignRight)
        self.chart_panel, chart_layout = self._panel()
        chart_header = QHBoxLayout()
        title = self.label('Daily awake hours')
        title.setObjectName('sectionTitle')
        chart_header.addWidget(title, 1)
        self.chart_range = QComboBox()
        self.chart_range.addItem('', 7)
        self.chart_range.addItem('', 30)
        self.chart_range.setAccessibleName(self.t('Chart range'))
        chart_header.addWidget(self.chart_range)
        chart_layout.addLayout(chart_header)
        self.daily_chart = DailyChart()
        chart_layout.addWidget(self.daily_chart)
        note = self.label('Known daily contributions only · device-local dates')
        note.setObjectName('muted')
        chart_layout.addWidget(note)
        self.events_panel, events_layout = self._panel()
        title = self.label('Recent events')
        title.setObjectName('sectionTitle')
        events_layout.addWidget(title)
        self.events = QTableWidget(0, 3)
        self.events.setObjectName('fleetEvents')
        self._style_table(self.events)
        self.events.setAccessibleName(self.t('Recent events'))
        self.events.horizontalHeader().hide()
        self.events.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.events.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.events.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.events.verticalHeader().setDefaultSectionSize(40)
        self.events.setMinimumHeight(204)
        self.events.setMaximumHeight(212)
        events_layout.addWidget(self.events)
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
        left.addWidget(self.chart_panel)
        left.addWidget(self.fleet_panel)
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
        # Cumulative source breakdown stays available under Administration.
        self.overview_usage = QWidget()
        usage_layout = QVBoxLayout(self.overview_usage)
        self.counts = {}
        for key in ('All devices', 'Online', 'Offline', 'Pending approval', 'Approved', 'Denied', 'Disconnected', 'Unavailable'):
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
        note = self.label('One latest snapshot per device; snapshots are not added over time.')
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
        self.bind(self.search, 'Search devices', 'setPlaceholderText')
        self.group = QComboBox()
        self.group.addItem(self.t('All groups'), '')
        self.group.setMaximumWidth(170)
        self.group.setAccessibleName(self.t('Group'))
        self.status_filter = QComboBox()
        self.status_filter.setMaximumWidth(170)
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
        for widget in (self.search, self.group, self.status_filter):
            chips.addWidget(widget)
        self.device_order = QComboBox()
        for code, key in (('problem', 'Sort by problem'), ('name', 'Sort by name'), ('last', 'Sort by last seen')):
            self.device_order.addItem(self.t(key), code)
        self.device_order.setAccessibleName(self.t('Sort computers'))
        self.device_order.currentIndexChanged.connect(self._filters_changed)
        chips.addWidget(self.device_order)
        layout.addLayout(chips)
        self.device_grid = QGridLayout()
        self.device_grid.setSpacing(16)
        self.devices_panel, table_layout = self._panel()
        self.empty_label = self.label('No computers match. Choose Add device to connect a computer.')
        self.empty_label.setWordWrap(True)
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
        self.device_detail_panel, detail_layout = self._panel()
        self.detail_name = QLabel()
        self.detail_name.setWordWrap(True)
        self.detail_name.setStyleSheet('font-size: 22px; font-weight: 600;')
        detail_layout.addWidget(self.detail_name)
        status_row = QHBoxLayout()
        self.detail_group, self.detail_status = QLabel(), QLabel()
        self.detail_group.setObjectName('groupBadge')
        self.detail_group.setStyleSheet('background: #22345A; padding: 3px 8px;')
        self.detail_dot = QLabel()
        status_row.addWidget(self.detail_group)
        status_row.addWidget(self.detail_dot)
        status_row.addWidget(self.detail_status)
        status_row.addStretch()
        detail_layout.addLayout(status_row)
        self.spec_title = self.label('Computer specifications')
        self.spec_title.setStyleSheet('font-size:18px; font-weight:600;')
        detail_layout.addWidget(self.spec_title)
        self.spec_values = {}
        spec_form = QFormLayout()
        spec_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        spec_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key in ('Manufacturer', 'Model', 'CPU', 'Cores / threads', 'RAM', 'Graphics', 'Operating system', 'Storage', 'Battery design capacity'):
            value = QLabel()
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            self.spec_values[key] = value
            spec_form.addRow(self._field(key), value)
        detail_layout.addLayout(spec_form)
        self.control_reason = QLabel()
        self.control_reason.setWordWrap(True)
        self.control_reason.setObjectName('muted')
        self.power_buttons = {}
        self.power_grid = power_grid = QGridLayout()
        for i, (action, key) in enumerate((('sleep', 'Sleep'), ('restart', 'Restart'), ('shutdown', 'Shut down'), ('wake', 'Turn on'), ('refresh', 'Refresh'))):
            button = self.button(key, lambda checked=False, a=action: self._device_command(self._detail, a))
            self.power_buttons[action] = button
            power_grid.addWidget(button, i // 3, i % 3)
        detail_layout.addLayout(power_grid)
        detail_layout.addWidget(self.control_reason)
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
        self.daily_range = QComboBox()
        self.daily_range.addItem(self.t('14 days'), 14)
        self.daily_range.addItem(self.t('30 days'), 30)
        self.daily_range.setAccessibleName(self.t('Awake time per day'))
        self.daily_range.currentIndexChanged.connect(lambda: self._render_daily(self._detail) if self._detail else None)
        daily_header.addWidget(self.daily_range)
        detail_layout.addLayout(daily_header)
        self.device_daily = Plot('daily', 'h')      # the desktop's Overview bars, for this computer
        self.device_daily.setFixedHeight(170)
        self.device_daily.setAccessibleName(self.t('Awake time per day'))
        detail_layout.addWidget(self.device_daily)
        self.daily_summary = QLabel()
        self.daily_summary.setObjectName('muted')
        self.daily_summary.setWordWrap(True)
        detail_layout.addWidget(self.daily_summary)
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
        for field, key in (('display_name', 'Display name'), ('asset_tag', 'Asset tag'), ('department', 'Department'), ('location', 'Location'), ('deployment_date', 'Deployment date')):
            edit = QLineEdit()
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
                QTimer.singleShot(120, self._paint_pushed)      # at most about eight repaints a second, however many arrive
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
            self.detail_status.setText(self._fleet_status('reporting'))
            self.detail_dot.setPixmap(status_dot(T('#4CC38A')).pixmap(20, 20))
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
            for action, key in (('restart', 'Restart'), ('sleep', 'Sleep'), ('shutdown', 'Shut down'), ('wake', 'Turn on'), ('refresh', 'Refresh')):
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
            menu.addAction(self.t('Remove computer')).triggered.connect(lambda checked=False: self.delete_device(device))
            menu.setToolTipsVisible(True)
            self.device_menu = menu
            menu.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        self._submit(lambda: client.call('device', device_id=device_id, hours=24), ready)

    def _device_command(self, device, action):
        if self._power_reason(device, action):
            return
        name = (device.get('identity') or {}).get('display_name') or device['device_id']
        key = {'sleep': 'Sleep', 'restart': 'Restart', 'shutdown': 'Shut down', 'wake': 'Turn on', 'refresh': 'Refresh'}[action]
        if not self._confirm(self.t('Confirm {action} for {name}?', action=self.t(key), name=name)):
            return
        arguments = dict(device_id=device['device_id'])
        if action != 'wake':
            arguments['action'] = action
        self._mutate('wake' if action == 'wake' else 'send_command', arguments)

    def _render_specs(self, device):
        missing = self.t('Not available')
        allowed = (device.get('policy') or {}).get('resources') is True
        specs = (device.get('specs') or {}) if allowed else {}
        values = {'Manufacturer': specs.get('manufacturer'), 'Model': specs.get('model'),
                  'CPU': specs.get('cpu_name'), 'RAM': byte_text(specs.get('ram_bytes'), missing),
                  'Cores / threads': f"{specs.get('cpu_cores') if specs.get('cpu_cores') is not None else missing} / {specs.get('cpu_threads') if specs.get('cpu_threads') is not None else missing}",
                  'Graphics': ', '.join(specs.get('gpu_names') or []),
                  'Operating system': ' '.join(str(specs.get(k) or '') for k in ('os_name', 'os_version')).strip(),
                  'Storage': '\n'.join(self.t('{name}: {free} free of {total}', name=d['name'], free=byte_text(d.get('free_bytes'), missing), total=byte_text(d.get('total_bytes'), missing)) for d in specs.get('disks') or []),
                  'Battery design capacity': None if specs.get('battery_design_wh') is None else f"{specs['battery_design_wh']:,.1f} Wh"}
        for key, label in self.spec_values.items():
            label.setText(str(values.get(key) or missing))
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
        for heading, keys in (('Server', ('Server name', 'Listening address', 'Uptime', 'Certificate SHA-256')),
                              ('Hardware', ('CPU', 'RAM', 'Operating system')),
                              ('Right now', ('CPU usage', 'Memory used', 'Last seen')),
                              ('Connected computers', ('All devices', 'Connected', 'Pending approval', 'Database size'))):
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
                  'Database size': byte_text(info.get('database_size'), missing),
                  'All devices': counts.get('total'), 'Connected': counts.get('reporting'), 'Pending approval': counts.get('pending')}
        fingerprint = values['Certificate SHA-256']
        if fingerprint:     # groups of eight wrap inside the row instead of being cut off
            values['Certificate SHA-256'] = ' '.join(fingerprint[i:i+8] for i in range(0, len(fingerprint), 8))
        for key, label in self.host_values.items():
            value = values.get(key)
            label.setText(missing if value is None else str(value))

    def _make_admin(self):
        self.admin_layout = self._scroll_page('Administration')
        self.admin_layout.setContentsMargins(32, 24, 32, 24)
        title = self.label('Administration')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        self.admin_layout.addWidget(title)
        approvals, approvals_layout = self._section('Pending approvals')
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
        self.server_name = QLabel()
        form.addRow(self.label('Server name'), self.server_name)
        self.organisation_edit = QLineEdit()
        self.organisation_edit.setMaxLength(120)
        self.organisation_edit.setAccessibleName(self.t('Organisation'))
        organisation_row = QHBoxLayout()
        organisation_row.addWidget(self.organisation_edit, 1)
        organisation_row.addWidget(self.button('Save organisation', lambda: self._mutate('organisation', dict(name=self.organisation_edit.text()))))
        form.addRow(self.label('Organisation'), organisation_row)
        self.admin_layout.addWidget(server)
        retention, form = self._form_section('Data retention')
        self.retention_boundary = QLabel()
        self.retention_boundary.setWordWrap(True)
        form.addRow(self.retention_boundary)
        self.retention_date = QDateEdit(QDate.currentDate())
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
        access, access_layout = self._section('Device access')
        access_layout.addWidget(self.button('Choose device', lambda: self.tabs.setCurrentIndex(1)), 0, Qt.AlignmentFlag.AlignLeft)
        self.admin_selected = QLabel(self.t('Select a device in Devices.'))
        self.admin_selected.setWordWrap(True)
        access_layout.addWidget(self.admin_selected)
        self.admin_revoke = self.button('Revoke selected device', self.revoke)
        self.admin_reenable = self.button('Re-enable selected device', self.add_device)
        self.admin_revoke.setEnabled(False)
        self.admin_reenable.setEnabled(False)
        access_layout.addWidget(self.admin_revoke, 0, Qt.AlignmentFlag.AlignLeft)
        access_layout.addWidget(self.admin_reenable, 0, Qt.AlignmentFlag.AlignLeft)
        note = self.label('To reconnect, get a new code and approve this computer again. Its history is kept.')
        note.setWordWrap(True)
        access_layout.addWidget(note)
        self.admin_layout.addWidget(access)
        configuration, form = self._form_section('Configuration')
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
        language_panel, language_layout = self._section('Language')
        language_layout.addWidget(self.language_choice, 0, Qt.AlignmentFlag.AlignLeft)
        self.admin_layout.addWidget(language_panel)
        theme_panel, theme_layout = self._section('Theme')
        theme_layout.addWidget(self.theme_choice, 0, Qt.AlignmentFlag.AlignLeft)
        self.admin_layout.addWidget(theme_panel)
        backups, layout = self._section('Backup and restore')
        self.backup_button = self.button('Save backup', self.backup)
        self.restore_button = self.button('Restore backup', self.restore)
        layout.addWidget(self.backup_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignLeft)
        note = self.label('After restoring, computers need a new code and approval to connect again.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.admin_layout.addWidget(backups)
        usage_panel, usage_holder = self._panel()
        self.overview_usage.layout().setContentsMargins(0, 0, 0, 0)
        usage_holder.addWidget(self.overview_usage)
        self.admin_layout.addWidget(usage_panel)
        self.admin_layout.addWidget(self.button('Export fleet', lambda: self.export_requested.emit(None)), 0, Qt.AlignmentFlag.AlignRight)
        self.admin_layout.addStretch()

    def _set_device_actions(self, enabled):
        enabled = enabled and not self._mutation_busy
        for widget in (*self.identity_fields.values(), self.save_identity_button, self.revoke_button, self.delete_button,
                       self.request_refresh_button):
            widget.setEnabled(enabled)
        self.request_refresh_button.setEnabled(bool(enabled and not self._power_reason(self._detail, 'refresh')))
        self.export_button.setEnabled(bool(self.selected_device_ids()) if hasattr(self, 'export_button') else False)
        pending = enabled and self.pending_requests.count() > 0
        self.approve_button.setEnabled(pending)
        self.deny_button.setEnabled(pending)
        if hasattr(self, 'admin_revoke'):
            self.admin_revoke.setEnabled(bool(enabled and self._detail and self._detail.get('status') != 'revoked'))
            self.admin_reenable.setEnabled(bool(enabled and self._detail and self._detail.get('status') == 'revoked'))
            self.admin_selected.setText((self._detail.get('identity', {}).get('display_name') or self._detail['device_id']) if self._detail else self.t('Select a device in Devices.'))
            self._admin_selection_changed()
        self._power_state(self._detail)

    def _mark_identity_dirty(self):
        self._identity_dirty = True

    def _theme_changed(self):
        """Save the choice and change in place: the new theme spreads out from the control."""
        value = self.theme_choice.currentData()
        write_theme_preference(self.directory, value)
        name = resolve_theme(value)
        if name != active_theme():
            origin = self.theme_choice.mapTo(self, self.theme_choice.rect().center())
            theme_reveal(self, origin, lambda: self._apply_live_theme(name))

    def _apply_live_theme(self, name):
        set_theme(name)
        retint_all()
        self.credit.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(150, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.about_logo.setPixmap(themed_logo(Path(__file__).parent / 'assets/jpl_innovation.png').scaled(100, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.refresh()

    def _language_changed(self):
        self.language = self.language_choice.currentData()
        self.retranslate()

    def retranslate(self):
        if hasattr(self, 'performance_range'):
            for index, key in enumerate(('Live', '24 hours')):
                self.performance_range.setItemText(index, self.t(key))
        catalog.set_language(self.language)
        for widget, key, setter in self._bindings:
            getattr(widget, setter)(self.t(key))
            widget.setAccessibleName(self.t(key))
        for index, key in enumerate(('Overview', 'Devices', 'Administration', 'This Server')):
            self.tabs.setTabText(index, self.t(key))
        for index, key in enumerate(('All statuses', 'Pending approval', 'Approved', 'Denied', 'Disconnected', 'Connected', 'Offline', 'Unavailable')):
            self.status_filter.setItemText(index, self.t(key))
        self.table.setHorizontalHeaderLabels([self.t(key) for key in
                                              ('Device', 'Group', 'Status', 'Awake today', 'Last seen')])
        self.table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.group.setItemText(0, self.t('All groups'))
        self.fleet_table.setHorizontalHeaderLabels([self.t(key) for key in ('Device', 'Group', 'Status', 'Awake today', 'Last seen')])
        # The duration column is right-aligned, so its heading must be too.
        self.fleet_table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for index, key in enumerate(('Last 7 days', 'Last 30 days')):
            self.chart_range.setItemText(index, self.t(key))
        self.invitation_table.setHorizontalHeaderLabels([self.t(key) for key in ('Invitation', 'Expires', 'Uses left', 'Wrong guesses', 'Status', 'Actions')])
        self.admin_pending.setHorizontalHeaderLabels([self.t('Device'), self.t('Awaiting approval'), '', ''])
        self.fleet_group.setItemText(0, self.t('All groups'))
        locale = QLocale('vi_VN' if self.language == 'vi' else 'en_GB')
        self.setLocale(locale)
        self.fleet_date.setLocale(locale)
        self.retention_date.setLocale(locale)
        self.today_label.setText(self.t('Today · {date}', date=locale.toString(QDate.currentDate(), 'd MMM yyyy')))
        self.server_status.setText(self.t('Server running' if self._overview_result else 'Server unavailable'))
        if self._server_info:
            self.network_label.setText((self._server_info.get('organisation') or self._server_info['name']) + ' · ' + self.t('Local network'))
        self.local_server.setText(self.t('Local server: {name}', name=(self._server_info or {}).get('name', socket.gethostname())))
        self.events.setHorizontalHeaderLabels([self.t(key) for key in ('Device ID', 'Event', 'Time')])
        if self._overview_result is not None:
            self._render_overview(self._overview_result)
        if self._configuration_result is not None:
            self._render_configuration(self._configuration_result)
        if self._bundle_expiry:
            self.code_expiry.setText(local_expiry(self._bundle_expiry, self.t) if self._bundle_expiry else '')
        for field, key in (('display_name', 'Display name'), ('asset_tag', 'Asset tag'), ('department', 'Department'), ('location', 'Location'), ('deployment_date', 'Deployment date')):
            self.identity_fields[field].setAccessibleName(self.t(key))
        for widget, key in ((self.language_choice, 'Language'), (self.status_filter, 'Status'),
                            (self.table, 'Devices'), (self.detail_text, 'Device details'),
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
        if self._detail:
            self._render_detail(self._detail, preserve_edits=self._identity_dirty)
        else:
            self.detail_text.setPlainText(self.t('Select a device to view its usage, permissions and server-owned identity.'))

    def _submit(self, work, callback, button=None, quiet=False):
        if self._closed or len(self._jobs) >= 8:
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

    def _completed(self, number, result, success):
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
        if not success:
            self._status_failure = True
            self.status_label.setText(self.t(
                "Can't reach the Lifetime server. Make sure it is running."))
        elif not self._status_failure:
            self.status_label.setText(self.t('Working…' if self._jobs else 'Ready'))
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

    def _overview_finished(self, result, success):
        self._overview_busy = False
        self.overview_health.setVisible(not success)
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
        for key, code in (('All devices', 'total'), ('Online', 'reporting'),
                          ('Offline', 'report-overdue'), ('Pending approval', 'pending'),
                          ('Approved', 'approved'), ('Denied', 'denied'), ('Disconnected', 'revoked'), ('Unavailable', 'unavailable')):
            self.counts[key].setText(str(result['counts'][code]))
        totals = result.get('totals') or {}
        for key, field in (('Measured', 'measured_seconds'), ('Imported', 'imported_seconds'), ('Baseline', 'baseline_seconds')):
            value = totals.get(field)
            self.fleet_totals[key].setText(self.t('Not available') if value is None else f'{value / 3600:,.2f}')
        self.snapshot_scope.setText(self.t('{known} devices have snapshots; {unknown} have no snapshot. Totals include known devices only.',
                                            known=result['known_devices'], unknown=result['unknown_devices']))
        self.overview_health.setText(self.t('Service available · schema {version} · server time {time}',
                                            version=result['schema_version'], time=result['server_time']))
        vertical, horizontal = self.events.verticalScrollBar().value(), self.events.horizontalScrollBar().value()
        self.events.setRowCount(len(result['recent_events']))
        for row, event in enumerate(result['recent_events']):
            kind = self.t('Report received' if event['kind'] == 'report-received' else 'Waiting for approval')
            for column, value in enumerate((event['device_id'], kind, event['time'])):
                self.events.setItem(row, column, QTableWidgetItem(value))
        self.events.verticalScrollBar().setValue(vertical)
        self.events.horizontalScrollBar().setValue(horizontal)
        if self._fleet_result is not None:
            self._render_fleet_events(self._fleet_result['recent_events'])

    def _configuration_edited(self):
        self._configuration_dirty = True

    def _load_configuration(self):
        if self._configuration_busy or self._closed:
            return
        client = self.client
        self._configuration_busy = True
        if not self._submit(lambda: client.call('configuration'), self._configuration_finished):
            self._configuration_busy = False

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
            return all_rows[arguments['offset']:arguments['offset'] + arguments['limit']], metadata
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
                    item.setIcon(status_dot(T('#FFC93C') if unstable else T(T('#4CC38A')) if status == 'reporting' else T('#FFC93C')
                                            if status in ('pending', 'report-overdue') else T(T('#9FB0CC'))))
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
            self.table.setRowHeight(row_number, max(178, box.sizeHint().height()))
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
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(self._offset + len(rows) < self._device_matching)
        self.page_label.setText(self.t('Showing {start} to {end} of {total} devices',
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
            self.detail_text.setPlainText(self.t('Select a device to view its usage, permissions and server-owned identity.'))
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
        self.detail_name.setText(self.t('Device details'))
        self.detail_group.clear()
        self.detail_group.hide()
        for edit in self.identity_fields.values():
            edit.clear()
        self.detail_dot.clear()
        self.detail_status.clear()
        self.detail_total.setText(self.t('Not available'))
        self.device_id_label.clear()
        self.copy_device_id.setEnabled(False)
        self.device_pending.hide()
        self._render_specs({})
        self._power_state(None)
        self._fit_detail_text()

    def _render_daily(self, device):
        """Awake hours per local day from the history the computer already reports; missing days stay empty."""
        allowed = (device.get('policy') or {}).get('history') is True
        daily = ((device.get('history') or {}).get('daily') or {}) if allowed else {}
        today = dt.date.today()
        days = [today - dt.timedelta(days=i) for i in reversed(range(self.daily_range.currentData() or 14))]
        values = [daily[d.isoformat()] / 3600 if isinstance(daily.get(d.isoformat()), (int, float)) else None for d in days]
        recorded = [(v, d) for v, d in zip(values, days) if v is not None]
        message = '' if recorded else self.t('No awake time recorded yet' if allowed else 'History is off on this computer')
        self.device_daily.set_data([d.isoformat() for d in days], values, dates=days, message=message)
        if recorded:
            longest, day = max(recorded, key=lambda pair: pair[0])
            average = sum(v for v, _ in recorded) / len(recorded)
            self.daily_summary.setText(self.t('Average {average} a day · longest day {longest} on {day}',
                                              average=self.t('{hours} h', hours=f'{average:.1f}'),
                                              longest=self.t('{hours} h', hours=f'{longest:.1f}'),
                                              day=self._date(day.isoformat(), year=False)))
        self.daily_summary.setVisible(bool(recorded))

    def _render_detail(self, device, preserve_edits=False):
        self._render_specs(device)
        self._render_daily(device)
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
        self.detail_status.setText(self._fleet_status(status))
        self.detail_dot.setPixmap(status_dot(T('#4CC38A') if status == 'reporting' else T('#FFC93C')
            if status in ('pending', 'report-overdue') else T(T('#9FB0CC'))).pixmap(20, 20))
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
                  ('Coverage', coverage),
                  ('Tracking since', self._date(tracking) if tracking else missing)]
        def table(rows):
            return '<table width="100%" cellspacing="0" cellpadding="4">' + ''.join(
                T('<tr><td style="color:#9FB0CC;">') + e(self.t(key)) +
                '</td><td>' + e(value) + '</td></tr>' for key, value in rows) + '</table>'   # no colons, as on Overview
        contents = table(fields)
        health = device.get('health') or {}
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
        self.device_id_label.setText(self.t('Device ID') + ': ' + device['device_id'])
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
        yes = dialog.addButton(self.t('Continue?'), QMessageBox.ButtonRole.AcceptRole)
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
        if self._confirm('Delete older records permanently? Latest totals remain, with their original timestamps.'):
            before = self.retention_date.date().toString('yyyy-MM-dd') + 'T00:00:00+00:00'
            self._mutate('retain', dict(before=before), self.retain_button)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t('Save backup'), 'lifetime-backup.sqlite',
                                             self.t('SQLite backups (*.sqlite *.db);;All files (*)'))
        if path:
            self._mutate('backup', dict(path=path), self.backup_button)

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('Restore backup'), '',
                                             self.t('SQLite backups (*.sqlite *.db);;All files (*)'))
        if path and self._confirm('After restoring, computers need a new code and approval to connect again.'):
            self._mutate('restore', dict(path=path), self.restore_button)

    def export_selection(self):
        selected = self.selected_device_ids()
        if selected:
            self.export_requested.emit(selected)

    def _show_export(self, device_ids):
        try:
            from lifetime_server_export import ServerExportDialog
            ServerExportDialog(self.client, device_ids=device_ids, parent=self, language=self.language).exec()
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
        self.chrome_grid.addWidget(self.chrome_brand, 0, 0)
        self.chrome_grid.addWidget(self.chrome_actions, 0, 2 if wide else 1)
        if wide:
            self.chrome_grid.addWidget(navigation, 0, 1, Qt.AlignmentFlag.AlignCenter)
        else:
            self.chrome_grid.addWidget(navigation, 1, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        self.chrome_grid.setColumnStretch(0, 1)
        self.chrome_grid.setColumnStretch(1, 0 if wide else 1)
        self.chrome_grid.setColumnStretch(2, 1 if wide else 0)
        self.credit.setVisible(self.width() >= 1400)
        self.chrome_states.setVisible(self.width() >= 640)

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
            QTimer.singleShot(max(0, int(450 - (time.monotonic() - self._refresh_started) * 1000)), self._refresh_done)

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

    def _load_fleet(self):
        if self._closed:
            return
        if self._fleet_busy:
            self._fleet_again = True
            return
        header = self.fleet_table.horizontalHeader()
        arguments = dict(day=self.fleet_date.date().toString('yyyy-MM-dd'), days=self.chart_range.currentData(),
                         limit=6, offset=self._fleet_offset, search=self.fleet_search.text(),
                         group=self.fleet_group.currentData() or '',
                         sort=('name', 'group', 'status', 'today', 'last')[max(0, header.sortIndicatorSection())],
                         descending=header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder)
        revision, client = self._fleet_revision, self.client
        self._fleet_busy = True
        if not self._submit(lambda: client.call('fleet', **arguments),
                            lambda result, ok: self._fleet_finished(result, ok, revision)):
            self._fleet_busy = False

    def _fleet_finished(self, result, success, revision):
        self._fleet_busy = False
        self._finish_skeletons('hero', 'offline', 'awake', 'pending', 'fleet', 'events', 'chart')
        if success and revision == self._fleet_revision:
            self._fleet_result = result
            self._render_fleet(result)
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
        self.kpi_notes['Awake today'].setText(self.t('{count} reporting devices', count=result['today']['devices']))
        self.kpis['Pending approval'].setText(str(counts['pending']))
        self._style_kpis()
        self._overview_data = True
        self._arm_overview_entrance()
        self.kpi_notes['Pending approval'].setText(self.t('Administrator review required'))
        self.fleet_empty.setVisible(counts['total'] == 0)
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
            values = (row['name'], row['group'] or self.t('Not available'),
                      self._fleet_status(row['status']) + (' / ' + self.t('Unstable') if row.get('unstable') else ''), self._duration(row['today']), self._relative(row['last']))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row['device_id'])
                item.setToolTip(str(value) if column != 4 else (row['last'] or value))
                if column == 2:
                    # Colour lives on the dot; the word stays in normal text colour for readability.
                    item.setIcon(status_dot(T('#FFC93C') if row.get('unstable') else T(T('#4CC38A')) if row['status'] == 'reporting' else T('#FFC93C')
                                            if row['status'] in ('pending', 'report-overdue') else T(T('#9FB0CC'))))
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
        self.fleet_page_label.setText(self.t('Showing {start} to {end} of {total} devices',
            start=self._fleet_offset + 1 if result['rows'] else 0, end=self._fleet_offset + len(result['rows']), total=result['matching']))
        self.daily_chart.day_label = lambda day: self._date(day, year=False)
        self.daily_chart.set_samples(result['daily'], self.t('No dated reports yet'), self.t('h'))
        self.updated_label.setText(self.t('Updated {time}', time=self._clock_text(result['updated_at'])))
        self._render_fleet_events(result['recent_events'])

    def _date(self, value, year=True):
        """One date style everywhere, in the window's language: '8 Nov 2026' / '8 thg 11, 2026'."""
        day = dt.date.fromisoformat(str(value)[:10])
        return self.t('date.full' if year else 'date.short', day=day.day, month=self.t(f'month.{day.month}'), year=day.year)

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
            self.fleet_detail_text.setText('<p style="font-size:18px; font-weight:600; margin:0 0 4px 0">' + html.escape(self.t('Device details')) + '</p><p>' +
                                          html.escape(self.t('Select a device to view its recorded usage.')) + '</p>')
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
        self.events.setRowCount(len(events))
        self.events.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        kinds = {'report-received': 'Synchronized', 'report-overdue': 'Went offline',
                 'enrollment-pending': 'Awaiting approval', 'revoked': 'Disconnected', 'invitation-used': 'Invitation used',
                 'command-pending': 'Command sent', 'command-delivered': 'Command delivered', 'command-done': 'Command finished',
                 'command-failed': 'Command failed', 'command-cancelled': 'Command cancelled', 'command-expired': 'Command expired',
                 'wake': 'Turn on sent'}
        colors = {'report-received': T(T('#65D782')), 'report-overdue': T('#FFC93C'), 'enrollment-pending': T('#FFC93C')}
        for index, event in enumerate(events):
            values = (event.get('name') or event.get('device_id') or '', self.t(kinds.get(event['kind'], 'Unknown')), self._clock_text(event['time']))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(event['time'] if column == 2 else value)
                if column == 1:
                    item.setIcon(status_dot(colors.get(event['kind'], T(T('#9FB0CC')))))
                if column == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.events.setItem(index, column, item)

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
            self.search.setText(self._fleet_selected)

    def _listed_device_ids(self):
        return [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.table.rowCount())]

    def review_pending(self):
        self.tabs.setCurrentIndex(1)
        self.status_filter.setCurrentIndex(self.status_filter.findData('pending'))

    def _load_server_info(self):
        if self._info_busy:
            self._info_again = True
            return
        self._info_busy = True
        client = self.client
        invitation_offset = self._invitation_offset
        def work():
            return dict(info=client.call('server_info'), invitations=client.call('invitations', limit=100, offset=invitation_offset), pending=client.call('pending_enrollments'))
        if not self._submit(work, self._server_info_finished):
            self._info_busy = False

    def _server_info_finished(self, result, success):
        self._info_busy = False
        self._finish_skeletons(*(f'host{i}' for i in range(len(self.host_panels))))
        if success:
            self._render_server_info(result)
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
        self.server_name.setText(info['name'])
        selections = {}
        for table in (self.invitation_table, self.admin_pending):
            indexes = table.selectionModel().selectedRows()
            selections[table] = table.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole) if len(indexes) == 1 else None
            table.clearSelection()
        self.invitation_table.setRowCount(len(result['invitations']))
        self.invitation_previous.setEnabled(self._invitation_offset > 0)
        self.invitation_next.setEnabled(len(result['invitations']) == 100)
        for table in (self.admin_pending, self.invitation_table):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    wrapper = table.cellWidget(row, column)
                    if wrapper:
                        wrapper.hide()
        self.admin_pending.setRowCount(len(result['pending']))
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
                    item.setIcon(status_dot(T('#65D782') if state == 'Active' else T(T('#9FB0CC'))))
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

    def admin_decide(self, operation):
        indexes = self.admin_pending.selectionModel().selectedRows()
        if len(indexes) == 1:
            request = self.admin_pending.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            self._mutate(operation, dict(request_id=request))

    def _admin_selection_changed(self):
        for button in self._admin_row_actions:
            button.setEnabled(not self._mutation_busy)
        for button in self._invitation_row_actions:
            button.setEnabled(not self._mutation_busy and not button.property('revoked'))

    def _invitation_created(self, result, success):
        self._issuing_invitation = False
        self.invitation_button.setEnabled(True)
        if success:
            self.add_device_sheet = AddDeviceSheet(result['issued'], result['info']['address'], self.t, self, client=self.client)
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


def write_theme_preference(directory, value):
    path = Path(directory) / 'ui.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data['theme'] = value if value in ('auto', 'light', 'dark') else 'auto'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    os.replace(temporary, path)


def create_application(argv=None):
    """The one place the dashboard's Qt application is configured.

    Without the explicit UI font the window fell back to a smaller default font, its panels lost their
    backgrounds and the layout grew wider than a 2560 px screen. Set it before any widget exists.
    """
    existing = QApplication.instance()
    if existing is not None:
        return existing
    configure_high_dpi()
    application = QApplication(list(argv or sys.argv[:1]))
    application.setFont(QFont('Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans', 11))
    application.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets/lifetime.png')))
    return application


def show_fitted(window):
    """Open maximized so the dashboard always fits the screen it starts on."""
    window.showMaximized()
    window.start_stream()
    window.tray = install_tray(window, 'Lifetime Server', window.server_status.text, key='lifetime-server-gui', translate=window.t)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lifetime Server local dashboard')
    parser.add_argument('--directory', required=True)
    parser.add_argument('--admin-port', type=int, required=True)
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
