"""The Energy page of the Lifetime Server dashboard: energy and cost from each computer's true awake time.

Everything comes from the admin ``insights`` call (lifetime_insights on the server) in a background Task and reuses
the window's panels, tables, buttons and translations, so Glass/Classic, light/dark and English/Vietnamese follow it.
Estimated figures always say so; a computer that does not share its awake history is counted as unknown, never zero.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QSizePolicy, QTableWidget, QTableWidgetItem, QWidget)

import lifetime_insights as insights
from lifetime_charts import Plot
from lifetime_layout import ControlRow
from lifetime_widgets_dashboard import EmptyState, InfoCard

SOURCE_TEXT, ENERGY_NOTE = insights.SOURCE_TEXT, insights.ENERGY_NOTE


def power_text(t, power):
    return t('{watts} W · {how}', watts=f"{power['watts']:g}", how=t(SOURCE_TEXT[power['source']]))


class InsightsUI:
    """Built into a ServerWindow: the Energy page (``index``) and its settings."""

    def __init__(self, window):
        self.w = window
        self.result = None
        self._busy = self._again = False
        self._make_page()

    def t(self, key, **values):
        return self.w.t(key, **values)

    def _table(self, columns, name):
        table = QTableWidget(0, columns)
        table.setObjectName(name)
        self.w._style_table(table)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, columns):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _make_page(self):
        w = self.w
        layout = w._scroll_page('Energy')
        self.index = w.tabs.count() - 1
        w.tabs.currentChanged.connect(lambda index: self.load() if index == self.index else None)
        title = w.label('Energy and lifecycle')
        title.setStyleSheet('font-size: 28px; font-weight: 600;')
        layout.addWidget(title)
        self.intro = w.label('What each computer’s true awake time costs: energy, money, time on after hours, what to '
                             'replace next and how healthy it is.')
        self.intro.setObjectName('muted')
        layout.addWidget(self.intro)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #FF8A8A;')
        self.error.hide()
        layout.addWidget(self.error)
        # ---- energy and cost ----
        panel, content = w._panel()
        header = ControlRow()
        heading = w.label('Energy and cost')
        heading.setObjectName('sectionTitle')
        header.addWidget(heading, 1)
        self.scope = QComboBox()
        self.scope.addItem('', None)
        self.scope.currentIndexChanged.connect(self.render)
        header.addWidget(self.scope)
        content.addLayout(header)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = {}
        for key in ('today', 'week', 'month'):
            card = InfoCard('event', '')
            value = QLabel()
            card.add_row('', value)
            self.cards[key] = (card, value)
            cards.addWidget(card, 1)
        content.addLayout(cards)
        self.method = QLabel()
        self.method.setWordWrap(True)
        self.method.setObjectName('muted')
        content.addWidget(self.method)
        self.chart_title = w.label('Energy per day, last 30 days (kWh)')
        self.chart_title.setObjectName('muted')
        content.addWidget(self.chart_title)
        self.chart = Plot('bars', 'kWh')
        self.chart.setFixedHeight(160)
        self.chart.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        content.addWidget(self.chart)
        self.top_title = w.label('Top consumers, last 30 days')
        self.top_title.setStyleSheet('font-weight: 600;')
        content.addWidget(self.top_title)
        self.top = self._table(5, 'energyTopTable')
        content.addWidget(self.top)
        self.groups_title = w.label('By group, last 30 days')
        self.groups_title.setStyleSheet('font-weight: 600;')
        content.addWidget(self.groups_title)
        self.groups = self._table(5, 'energyGroupTable')
        content.addWidget(self.groups)
        self.empty = EmptyState('off')
        content.addWidget(self.empty)
        self.note = w.label(ENERGY_NOTE)
        self.note.setObjectName('muted')
        content.addWidget(self.note)
        layout.addWidget(panel)
        # ---- settings ----
        settings, form = w._panel()
        w.access_ui.gate(settings, 'set_insight_settings')
        heading = w.label('Electricity price and typical power')
        heading.setObjectName('sectionTitle')
        form.addWidget(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1e9)
        self.price.setDecimals(2)
        self.currency = QLineEdit()
        self.currency.setMaxLength(3)
        self.currency.setMaximumWidth(90)
        self.laptop = QDoubleSpinBox()
        self.desktop = QDoubleSpinBox()
        for spin in (self.laptop, self.desktop):
            spin.setRange(*insights.WATTS_RANGE)
            spin.setDecimals(0)
            spin.setSuffix(' W')
        self.form_labels = []
        for row, (key, widget) in enumerate((('Price per kWh', self.price), ('Currency', self.currency),
                                             ('Typical laptop', self.laptop), ('Typical desktop', self.desktop))):
            label = w.label(key)
            self.form_labels.append(label)
            grid.addWidget(label, row // 2, (row % 2) * 2)
            grid.addWidget(widget, row // 2, (row % 2) * 2 + 1)
            widget.setAccessibleName(self.t(key))
        form.addLayout(grid)
        self.save = w.button('Save', self._save)
        self.save.setObjectName('primary')
        form.addWidget(self.save, 0, Qt.AlignmentFlag.AlignRight)
        override = ControlRow()
        self.override_label = w.label('Power for a group or computer')
        override.addWidget(self.override_label, 1)
        self.target = QComboBox()
        self.target.setMinimumWidth(220)
        override.addWidget(self.target)
        self.target_watts = QDoubleSpinBox()
        self.target_watts.setRange(*insights.WATTS_RANGE)
        self.target_watts.setDecimals(0)
        self.target_watts.setSuffix(' W')
        self.target_watts.setValue(60)
        override.addWidget(self.target_watts)
        override.addWidget(w.button('Set', lambda: self._override(True)))
        override.addWidget(w.button('Clear', lambda: self._override(False)))
        form.addLayout(override)
        self.overrides = QLabel()
        self.overrides.setWordWrap(True)
        self.overrides.setObjectName('muted')
        form.addWidget(self.overrides)
        layout.addWidget(settings)
        layout.addStretch()

    # ---- loading and saving ----

    def load(self, *unused):
        """Fetched while the page is open (and once before), so a refresh elsewhere does not compute the fleet."""
        if self.w._closed or (self.result is not None and self.w.tabs.currentIndex() != self.index):
            return
        if self._busy:
            self._again = True
            return
        self._busy = True
        client = self.w.client
        if not self.w._submit(lambda: client.call('insights'), self._loaded, quiet=True):
            self._busy = False

    def _loaded(self, result, success):
        self._busy = False
        self.error.setVisible(not success)
        self.error.setText('' if success else self.t('Energy figures are unavailable. Showing the last data received, if any.'))
        if success:
            first = self.result is None
            self.result = result
            if first:
                self._fill_settings()
            self.render()
        if self._again:
            self._again = False
            self.load()

    def _fill_settings(self):
        config = self.result['settings']
        self.price.setValue(config['price'])
        self.currency.setText(config['currency'])
        self.laptop.setValue(config['laptop_watts'])
        self.desktop.setValue(config['desktop_watts'])

    def _send(self, values, button=None):
        client = self.w.client

        def work():
            try:
                return dict(result=client.call('set_insight_settings', values=values))
            except ValueError:
                return dict(refused=True)

        def done(result, success):
            if button is not None:
                button.setEnabled(True)
            ok = success and not result.get('refused')
            self.w.status_label.setText(self.t('Changes saved.' if ok else 'The server did not accept this. Check the values and try again.'))
            self.load()
        if button is not None:
            button.setEnabled(False)
        self.w._submit(work, done, quiet=True)

    def _save(self):
        self._send(dict(price=self.price.value(), currency=self.currency.text().strip().upper(),
                        laptop_watts=self.laptop.value(), desktop_watts=self.desktop.value()), self.save)

    def _override(self, add):
        target = self.target.currentData()
        if not target or not self.result:
            return
        kind, key = target
        field = 'group_watts' if kind == 'group' else 'device_watts'
        table = dict(self.result['settings'][field])
        table.pop(key.casefold(), None)
        if add:
            table[key] = self.target_watts.value()
        self._send({field: table})

    # ---- rendering ----

    def retranslate(self):
        self.scope.setItemText(0, self.t('Whole fleet'))
        self.scope.setAccessibleName(self.t('Show'))
        for key, title in (('today', 'Today'), ('week', 'Last 7 days'), ('month', 'Last 30 days')):
            self.cards[key][0].title.setText(self.t(title))
            self.cards[key][0].pairs[0][0].setText(self.t('Cost'))
        self.top.setHorizontalHeaderLabels([self.t(key) for key in ('Computer', 'Group', 'Power draw', 'Energy', 'Cost')])
        self.groups.setHorizontalHeaderLabels([self.t(key) for key in ('Group', 'Computers', 'Measured', 'Energy', 'Cost')])
        self.top.setAccessibleName(self.t('Top consumers, last 30 days'))
        self.groups.setAccessibleName(self.t('By group, last 30 days'))
        self.chart.setAccessibleName(self.t('Energy per day, last 30 days (kWh)'))
        self.target.setAccessibleName(self.t('Power for a group or computer'))
        self.target_watts.setAccessibleName(self.t('Power for a group or computer'))
        if self.result:
            self.render()

    def _fill(self, table, rows):
        table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(index, column, item)
        self.w._fit_rows(table, len(rows), most=10)
        table.setVisible(bool(rows))

    def render(self, *unused):
        result = self.result
        if not result:
            return
        t, config = self.t, result['settings']
        currency = config['currency']
        groups = sorted(result['groups'])
        chosen = self.scope.currentData()
        self.scope.blockSignals(True)
        while self.scope.count() > 1:
            self.scope.removeItem(1)
        for name in groups:
            self.scope.addItem(t('Group: {name}', name=name or t('No group')), name)
        self.scope.setCurrentIndex(max(0, self.scope.findData(chosen)) if chosen is not None else 0)
        self.scope.blockSignals(False)
        chosen = self.scope.currentData()
        summary = result['fleet'] if chosen is None else result['groups'][chosen]
        computers = [c for c in result['computers'] if chosen is None or c['group'] == chosen]
        for key, (card, value) in self.cards.items():
            card.set_headline(f"{summary[key]['kwh']:,.2f} kWh")
            value.setText(insights.money(summary[key]['cost'], currency))
        self.method.setText(t('{measured} of {known} computers measured on battery; the others are estimated from typical power. '
                              '{unknown} do not share their awake history.', measured=summary['measured'],
                              known=summary['known'], unknown=summary['unknown']))
        days = list(summary['daily'])
        self.chart.set_data([day[5:] for day in days], [summary['daily'][day] for day in days],
                            message='' if summary['known'] else t('No shared awake history yet'))
        by_id = {c['device_id']: c for c in computers}
        ranked = [by_id[i] for i in result['top'] if i in by_id] if chosen is None else \
            sorted((c for c in computers if c['energy']), key=lambda c: -c['energy']['month']['kwh'])[:10]
        self._fill(self.top, [(c['name'], c['group'] or t('No group'), power_text(t, c['power']),
                               f"{c['energy']['month']['kwh']:,.2f} kWh", insights.money(c['energy']['month']['cost'], currency))
                              for c in ranked])
        self._fill(self.groups, [(name or t('No group'), str(result['groups'][name]['computers']),
                                  str(result['groups'][name]['measured']), f"{result['groups'][name]['month']['kwh']:,.2f} kWh",
                                  insights.money(result['groups'][name]['month']['cost'], currency)) for name in groups])
        self.empty.set_state('off', t('No energy figures yet'),
                             t('Energy appears once approved computers share their awake history.'))
        self.empty.setVisible(not summary['known'])
        # The settings form: what is set per group and per computer.
        target = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        for name in groups:
            self.target.addItem(t('Group: {name}', name=name or t('No group')), ('group', name))
        for c in result['computers']:
            self.target.addItem(c['name'], ('computer', c['device_id']))
        if target is not None and self.target.findData(target) >= 0:
            self.target.setCurrentIndex(self.target.findData(target))
        self.target.blockSignals(False)
        names = {c['device_id'].casefold(): c['name'] for c in result['computers']}
        parts = [t('Group: {name}', name=key) + f' {watts:g} W' for key, watts in sorted(config['group_watts'].items())]
        parts += [names.get(key, key) + f' {watts:g} W' for key, watts in sorted(config['device_watts'].items())]
        self.overrides.setText(t('Set: {list}', list=', '.join(parts)) if parts else t('No group or computer has its own power set.'))
