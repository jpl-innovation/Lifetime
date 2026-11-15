"""The Energy page of the Lifetime Server dashboard: energy and cost from each computer's true awake time.

Everything comes from the admin ``insights`` call (lifetime_insights on the server) in a background Task and reuses
the window's panels, tables, buttons and translations, so Glass/Classic, light/dark and English/Vietnamese follow it.
Estimated figures always say so; a computer that does not share its awake history is counted as unknown, never zero.
"""
from PyQt6.QtCore import QTime, Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QSizePolicy, QTableWidget, QTableWidgetItem, QTimeEdit, QWidget)

import lifetime_insights as insights
from lifetime_charts import Plot
from lifetime_layout import ControlRow
from lifetime_widgets_dashboard import EmptyState, InfoCard

SOURCE_TEXT, ENERGY_NOTE = insights.SOURCE_TEXT, insights.ENERGY_NOTE
POLICY_NOTE = ('Sleep after hours sends the ordinary single-computer Sleep command, as the account that turned it on, '
               'to each computer of the group whose owner allows remote power: once a night, 30 minutes after its '
               'hours close, within the usual rate limits, and recorded in the audit trail. It skips a computer that '
               'is offline, already has a command waiting or looks busy (Lifetime cannot see a keyboard or mouse, so '
               'busy means the processor is working), and does not try again that night if the computer is woken or '
               'its owner declines. Turning it on needs an Admin or Operator role given to your own account.')
SCHEDULE_TEXT = {'schedule': 'its own business hours', 'office-hours': 'office hours'}


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
        self._make_after(layout)
        self._make_plan(layout)
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
        lifecycle = ControlRow()
        self.battery_level, self.disk_level = QDoubleSpinBox(), QDoubleSpinBox()
        self.battery_price, self.disk_price = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, low, high, suffix in ((self.battery_level, 50, 99, ' %'), (self.disk_level, 1, 50, ' %'),
                                        (self.battery_price, 0, 1e12, ''), (self.disk_price, 0, 1e12, '')):
            spin.setRange(low, high)
            spin.setDecimals(0)
            spin.setSuffix(suffix)
        for key, spin in (('Replace battery below', self.battery_level), ('Act when disk free below', self.disk_level),
                          ('Battery price', self.battery_price), ('Disk price', self.disk_price)):
            lifecycle.addWidget(w.label(key))
            lifecycle.addWidget(spin)
            spin.setAccessibleName(self.t(key))
        self.save_lifecycle = w.button('Save', self._save_lifecycle)
        lifecycle.addWidget(self.save_lifecycle)
        form.addLayout(lifecycle)
        hours_title = w.label('Office hours (for computers without business hours of their own)')
        hours_title.setStyleSheet('font-weight: 600;')
        form.addWidget(hours_title)
        hours = ControlRow()
        self.opens, self.closes = QTimeEdit(QTime(8, 0)), QTimeEdit(QTime(18, 0))
        for control, key in ((self.opens, 'Opens'), (self.closes, 'Closes')):
            control.setDisplayFormat('HH:mm')
            hours.addWidget(w.label(key))
            hours.addWidget(control)
        self.weekday_boxes = []
        for day in range(7):
            box = QCheckBox()
            box.setChecked(day < 5)
            self.weekday_boxes.append(box)
            hours.addWidget(box)
        self.save_hours = w.button('Save hours', self._save_hours)
        hours.addWidget(self.save_hours)
        form.addLayout(hours)
        layout.addWidget(settings)
        layout.addStretch()

    def _make_plan(self, layout):
        w = self.w
        panel, content = w._panel()
        heading = w.label('Replacement plan')
        heading.setObjectName('sectionTitle')
        content.addWidget(heading)
        self.plan_intro = w.label('Forecasts from each computer’s own history: when its battery falls below the '
                                  'replacement level and when its system disk runs out of room, plus disks that report a '
                                  'problem. A date is given only with at least 4 readings over 30 days; otherwise it '
                                  'says not enough data, with what it has.')
        self.plan_intro.setObjectName('muted')
        content.addWidget(self.plan_intro)
        self.plan_titles, self.plan_tables, self.budgets = {}, {}, {}
        for key, title in (('quarter', 'Now and next quarter'), ('year', 'Later this year')):
            label = w.label(title)
            label.setStyleSheet('font-weight: 600;')
            content.addWidget(label)
            self.plan_titles[key] = label
            table = self._table(5, f'plan_{key}')
            content.addWidget(table)
            self.plan_tables[key] = table
            budget = QLabel()
            budget.setWordWrap(True)
            budget.setObjectName('muted')
            content.addWidget(budget)
            self.budgets[key] = budget
        label = w.label('Forecast per computer')
        label.setStyleSheet('font-weight: 600;')
        content.addWidget(label)
        self.forecasts = self._table(5, 'forecastTable')
        content.addWidget(self.forecasts)
        layout.addWidget(panel)

    def _make_after(self, layout):
        w = self.w
        panel, content = w._panel()
        heading = w.label('After hours')
        heading.setObjectName('sectionTitle')
        content.addWidget(heading)
        self.after_summary = QLabel()
        self.after_summary.setWordWrap(True)
        content.addWidget(self.after_summary)
        self.after_chart_title = w.label('Hours on after hours per week (all computers)')
        self.after_chart_title.setObjectName('muted')
        content.addWidget(self.after_chart_title)
        self.after_chart = Plot('bars', 'h')
        self.after_chart.setFixedHeight(140)
        self.after_chart.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        content.addWidget(self.after_chart)
        self.after_top_title = w.label('Most on after hours, last 7 days')
        self.after_top_title.setStyleSheet('font-weight: 600;')
        content.addWidget(self.after_top_title)
        self.after_top = self._table(6, 'afterTopTable')
        content.addWidget(self.after_top)
        self.after_groups_title = w.label('By group, last 7 days')
        self.after_groups_title.setStyleSheet('font-weight: 600;')
        content.addWidget(self.after_groups_title)
        self.after_groups = self._table(6, 'afterGroupTable')
        content.addWidget(self.after_groups)
        self.after_rooms_title = w.label('By room, last 7 days')
        self.after_rooms_title.setStyleSheet('font-weight: 600;')
        content.addWidget(self.after_rooms_title)
        self.after_rooms = self._table(4, 'afterRoomTable')
        content.addWidget(self.after_rooms)
        policy = ControlRow()
        policy.addWidget(w.label('Sleep after hours for'), 1)
        self.policy_group = QComboBox()
        self.policy_group.setMinimumWidth(200)
        self.policy_group.currentIndexChanged.connect(self._policy_button)
        policy.addWidget(self.policy_group)
        self.policy_toggle = w.button('Turn on for this group', self._toggle_policy)
        policy.addWidget(self.policy_toggle)
        holder = QWidget()
        holder.setLayout(policy)
        w.access_ui.gate(holder, 'set_sleep_policy')
        content.addWidget(holder)
        self.policies = QLabel()
        self.policies.setWordWrap(True)
        content.addWidget(self.policies)
        self.policy_note = w.label(POLICY_NOTE)
        self.policy_note.setObjectName('muted')
        content.addWidget(self.policy_note)
        layout.addWidget(panel)

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
        hours = config['hours']
        self.opens.setTime(QTime.fromString(hours['start'], 'HH:mm'))
        self.closes.setTime(QTime.fromString(hours['end'], 'HH:mm'))
        for day, box in enumerate(self.weekday_boxes):
            box.setChecked(day in hours['weekdays'])
        self.price.setValue(config['price'])
        self.battery_level.setValue(config['battery_replace'])
        self.disk_level.setValue(config['disk_replace'])
        self.battery_price.setValue(config['unit_prices']['battery'])
        self.disk_price.setValue(config['unit_prices']['disk'])
        self.currency.setText(config['currency'])
        self.laptop.setValue(config['laptop_watts'])
        self.desktop.setValue(config['desktop_watts'])

    def _send(self, values, button=None, operation='set_insight_settings',
              refused='The server did not accept this. Check the values and try again.'):
        client = self.w.client

        def work():
            try:
                return dict(result=client.call(operation, **values))
            except ValueError:
                return dict(refused=True)

        def done(result, success):
            if button is not None:
                button.setEnabled(True)
            ok = success and not result.get('refused')
            self.w.status_label.setText(self.t('Changes saved.' if ok else refused))
            self.load()
        if button is not None:
            button.setEnabled(False)
        self.w._submit(work, done, quiet=True)

    def _save(self):
        self._send(dict(values=dict(price=self.price.value(), currency=self.currency.text().strip().upper(),
                                    laptop_watts=self.laptop.value(), desktop_watts=self.desktop.value())), self.save)

    def _save_lifecycle(self):
        self._send(dict(values=dict(battery_replace=self.battery_level.value(), disk_replace=self.disk_level.value(),
                                    unit_prices=dict(battery=self.battery_price.value(), disk=self.disk_price.value()))),
                   self.save_lifecycle)

    def _save_hours(self):
        hours = dict(self.result['settings']['hours'] if self.result else insights.OFFICE_HOURS,
                     start=self.opens.time().toString('HH:mm'), end=self.closes.time().toString('HH:mm'),
                     weekdays=[day for day, box in enumerate(self.weekday_boxes) if box.isChecked()])
        self._send(dict(values=dict(hours=hours)), self.save_hours)

    def _policy_on(self):
        group = self.policy_group.currentData()
        return group is not None and self.result is not None and group.casefold() in self.result.get('sleep_policies', {})

    def _policy_button(self, *unused):
        self.policy_toggle.setText(self.t('Turn off for this group' if self._policy_on() else 'Turn on for this group'))
        self.policy_toggle.setEnabled(self.policy_group.currentData() is not None)

    def _toggle_policy(self):
        group = self.policy_group.currentData()
        if group is None:
            return
        self._send(dict(group=group, enabled=not self._policy_on()), self.policy_toggle, 'set_sleep_policy',
                   'Sleep after hours needs an Admin or Operator role given to your own Windows account.')

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
        self._send(dict(values={field: table}))

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
        for day, box in enumerate(self.weekday_boxes):
            box.setText(self.t(f'weekday.{day}'))
        self.opens.setAccessibleName(self.t('Opens'))
        self.closes.setAccessibleName(self.t('Closes'))
        self.after_top.setHorizontalHeaderLabels([self.t(key) for key in (
            'Computer', 'Group', 'After hours', 'Energy', 'Cost', 'Hours used')])
        self.after_groups.setHorizontalHeaderLabels([self.t(key) for key in (
            'Group', 'After hours', 'Energy', 'Cost', 'Estimated saving per month', 'Sleep after hours')])
        self.after_rooms.setHorizontalHeaderLabels([self.t(key) for key in ('Room', 'After hours', 'Energy', 'Cost')])
        self.after_chart.setAccessibleName(self.t('Hours on after hours per week (all computers)'))
        self.policy_group.setAccessibleName(self.t('Sleep after hours for'))
        for table in self.plan_tables.values():
            table.setHorizontalHeaderLabels([self.t(key) for key in ('Computer', 'Group', 'Part', 'When', 'Why')])
        self.forecasts.setHorizontalHeaderLabels([self.t(key) for key in ('Computer', 'Battery', 'System disk space', 'Age', 'Awake so far')])
        self._policy_button()
        self.target_watts.setAccessibleName(self.t('Power for a group or computer'))
        if self.result:
            self.render()

    def _fill(self, table, rows, numbers=True):
        table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if numbers and column >= 2:
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
        self.render_after(groups)

    def render_after(self, groups):
        result, t = self.result, self.t
        currency = result['settings']['currency']
        after = result['fleet']['after']
        hours = result['settings']['hours']
        self.after_summary.setText(t('Last 7 days: {hours} h awake outside hours on {known} computers, {kwh} kWh, {cost}. '
                                     '{unknown} do not share their hourly history.', hours=f"{after['hours']:,.1f}",
                                     known=after['known'], kwh=f"{after['kwh']:,.2f}",
                                     cost=insights.money(after['cost'], currency), unknown=after['unknown']))
        self.after_chart.set_data([start[5:] for start, _ in after['weeks']], [value for _, value in after['weeks']],
                                  message='' if after['known'] else t('No shared hourly history yet'))
        by_id = {c['device_id']: c for c in result['computers']}

        def used(c):
            span = f"{c['after']['hours']['start']}–{c['after']['hours']['end']}"
            return t(SCHEDULE_TEXT[c['after']['source']]) + ' ' + span
        self._fill(self.after_top, [
            (c['name'], c['group'] or t('No group'), f"{c['after']['week']['hours']:,.1f} h",
             f"{c['after']['week']['kwh']:,.2f} kWh", insights.money(c['after']['week']['cost'], currency), used(c))
            for c in (by_id[i] for i in result.get('after_top', []) if i in by_id)])
        policies = result.get('sleep_policies', {})
        self._fill(self.after_groups, [
            (name or t('No group'), f"{g['after']['hours']:,.1f} h", f"{g['after']['kwh']:,.2f} kWh",
             insights.money(g['after']['cost'], currency),
             t('{cost} (est., {allowed} allow remote power)', cost=insights.money(g['after']['saving_month']['cost'], currency),
               allowed=g['after']['allowed']),
             t('On') if name.casefold() in policies else t('Off'))
            for name, g in ((name, result['groups'][name]) for name in groups)])
        rooms = result.get('rooms', {})
        self._fill(self.after_rooms, [(name, f"{r['after']['hours']:,.1f} h", f"{r['after']['kwh']:,.2f} kWh",
                                       insights.money(r['after']['cost'], currency)) for name, r in rooms.items()])
        self.after_rooms_title.setVisible(bool(rooms))
        chosen = self.policy_group.currentData()
        self.policy_group.blockSignals(True)
        self.policy_group.clear()
        for name in groups:
            if name:
                self.policy_group.addItem(t('Group: {name}', name=name), name)
        if chosen is not None and self.policy_group.findData(chosen) >= 0:
            self.policy_group.setCurrentIndex(self.policy_group.findData(chosen))
        self.policy_group.blockSignals(False)
        self._policy_button()
        self.policies.setText('\n'.join(t('{group}: on since {since}, sent as {name}', group=p['group'], name=p['name'],
                                            since=self.w._timestamp(p['since'])) for p in policies.values())
                              or t('Sleep after hours is off for every group.'))
        self.render_plan()

    def _says(self, pair):
        key, values = pair
        return self.t(key, **values)

    def render_plan(self):
        result, t = self.result, self.t
        plan, currency = result['plan'], result['settings']['currency']
        for key, table in self.plan_tables.items():
            rows = plan[key]
            self._fill(table, [(row['name'], row['group'] or t('No group'), t(insights.PART_TEXT[row['part']]),
                                self._says(insights.when_text(row)), self._says(insights.reason_text(row))) for row in rows], numbers=False)
            budget = plan[key + '_budget']
            self.budgets[key].setText(t('Nothing due.') if not rows else
                                      t('{count} parts · budget {total} for {priced} priced, {unpriced} without a price',
                                        count=len(rows), total=insights.money(budget['total'], currency),
                                        priced=budget['priced'], unpriced=budget['unpriced']))

        def cell(c, part):
            found = next((p for p in c['replace'] if p['part'] == part), None)
            if found:
                return self._says(insights.when_text(found))
            return t('No battery in this computer') if part == 'battery' and c.get('kind') == 'desktop' else t('Not available')
        self._fill(self.forecasts, [
            (c['name'], cell(c, 'battery'), cell(c, 'disk'),
             t('{years} years', years=c['age']['years']) if c['age']['years'] is not None else t('Not available'),
             t('{hours} h', hours=f"{c['age']['awake_hours']:,}") if c['age']['awake_hours'] is not None else t('Not available'))
            for c in result['computers']], numbers=False)
