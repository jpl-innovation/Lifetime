# Dashboard and monitoring UI research for Lifetime

Date: 2026-11-15. Purpose: find proven, open-source dashboard patterns that turn Lifetime's text-heavy screens (label/value lists, plain forms, long detail text) into scannable visuals, for a PyQt6 desktop app (Overview, Activity, System, Health, Connect, Reports, Settings) and Lifetime Server (fleet Overview, Devices, device detail, Incidents, Availability, Network checks, Administration, server log).

Rule of this document: inspiration only. Lifetime is GPL-3.0 and PyQt-native. No code, CSS, icons, logos, or screenshots from any repo below are copied. Every pattern is reimplemented with QPainter and our own tokens.

Verification method: each repo below was confirmed to exist through the GitHub REST API on 2026-11-15 (not archived unless noted; star counts are rounded from that day's response). Licenses are the SPDX id GitHub reported, with notes where GitHub could not detect one. Demo links are noted only where a fetch succeeded or the README states them; live demos are often flaky or blocked, so treat them as "try it", and rely on README screenshots.

---

## 1. Verified repositories (33)

Legend: license is the SPDX id from GitHub unless noted. "Borrow" lists concrete UI patterns.

### A. Uptime and fleet monitoring (closest to Lifetime Server)

**1. Uptime Kuma** - https://github.com/louislam/uptime-kuma - MIT - ~94k stars
- Shows: per-monitor up/down state, ping charts, cert expiry, status pages.
- Borrow: (a) heartbeat bar, a row of small colored ticks, one per check, newest on the right, with hover tooltip of time and message; (b) big colored status pill (Up / Down / Pending / Maintenance) at the top of a monitor; (c) small summary figures (24h, 30d, 1y uptime %) in a row of tiles, not a sentence; (d) ping chart with down periods shaded red.
- Screenshots: README images (dashboard https://user-images.githubusercontent.com/1336778/212262296-e6205815-ad62-488c-83ec-a5b0d0689f7c.jpg, status page https://user-images.githubusercontent.com/1336778/134628766-a3fe0981-0926-4285-ab46-891a21c3e4cb.png). Demo: https://demo.kuma.pet/start-demo (README; temporary data, my fetch got 403).
- Trademark: name and logo are theirs; do not reuse.

**2. Beszel** - https://github.com/henrygd/beszel - MIT - ~27k stars
- Shows: multi-server CPU, RAM, disk, network, temperatures, Docker stats, alerts.
- Borrow: (a) compact per-system card with 3 to 4 mini area charts and a current value beside each; (b) one list/grid view that sorts and filters across all systems with a status color edge; (c) system page that stacks time-range charts with a single shared range selector; (d) alert setup as "metric, threshold, duration" rows, not prose.
- Screenshots: https://beszel.dev/image/dashboard.png , https://beszel.dev/image/system-full.png (README). No public live demo listed.

**3. Gatus** - https://github.com/TwiN/gatus - Apache-2.0 - ~12.8k stars
- Shows: endpoint health, condition results, uptime, incidents.
- Borrow: (a) endpoints grouped under collapsible group headers with a worst-status color on the header; (b) a row of last-N result ticks per endpoint plus response time on the right; (c) per-condition pass/fail list (each condition a line with a check or cross icon); (d) status badges as compact SVG-like pills (uptime %, response time).
- Demo: https://status.twin.sh (page loads; I could not read its rendering).

**4. Healthchecks** - https://github.com/healthchecks/healthchecks - BSD-3-Clause - ~10.5k stars
- Shows: cron and background job pings.
- Borrow: (a) status dot plus "last ping 3 min ago" relative time as the primary row content; (b) "period and grace" expressed as two small steppers/labels instead of a form paragraph; (c) ping log as a vertical event list with type icons (success, start, fail, late); (d) public badge page.
- Screenshots (README, raw paths): static/img/my_checks.png , check_details.png , badges.png under https://github.com/healthchecks/healthchecks/tree/master/static/img

**5. Statping-ng** - https://github.com/statping-ng/statping-ng - GPL-3.0 - ~2k stars (last push 2025-06, low activity but exists)
- Shows: service status page with graphs.
- Borrow: (a) per-service card with a compact uptime area graph beneath the name; (b) "all systems operational" banner that collapses to a red banner on any failure. Use as secondary reference only; project is slow-moving.

**6. Checkmk** - https://github.com/Checkmk/checkmk - GPL-2.0 (GitHub mirror) - ~2.6k stars on the mirror
- Shows: host/service state at enterprise scale.
- Borrow: (a) "tactical overview" tiles: counts of hosts and services by state (OK, WARN, CRIT, UNKNOWN) as large colored numbers, each clickable to a filtered list; (b) service rows with state-colored left cell and a short plugin output; (c) host state as a 4-color convention (green, yellow, red, grey for unknown). Note: GPL-2.0-only code cannot be copied into GPL-3.0 anyway, another reason to reimplement.

**7. Zabbix** - https://github.com/zabbix/zabbix - AGPL-3.0 - ~6.5k stars (GitHub mirror)
- Shows: hosts, problems, triggers, maps, graphs.
- Borrow: (a) problem list with severity color bands (Not classified, Information, Warning, Average, High, Disaster) as a 5 or 6 step scale; (b) dashboard of configurable widgets, each a titled card; (c) severity count strip across the top. AGPL means do not reuse source; concepts are fine.

**8. LibreNMS** - https://github.com/librenms/librenms - GPL-3.0-or-later (README/repo; GitHub reports NOASSERTION) - ~5k stars
- Shows: network device status, ports, bandwidth graphs, alerts.
- Borrow: (a) device list with tiny inline traffic sparkline and up/down badge per row; (b) port list with speed/utilization bar per row; (c) alert log with severity and "since" duration.

### B. Host and system-info monitors (closest to Lifetime desktop System / Health)

**9. Netdata** - https://github.com/netdata/netdata - GPL-3.0 (agent) - ~81k stars
- Shows: per-second system metrics, anomaly scoring, alerts.
- Borrow: (a) every chart is a small titled card with a live line and the current value in the corner; (b) a table of contents sidebar that groups hundreds of charts into sections (CPU, Memory, Disks, Network); (c) alert status chips (Clear, Warning, Critical) inside the header; (d) a thin anomaly ribbon under charts.
- LICENSE WARNING: the agent is GPL-3.0+, but the Netdata dashboard UI is closed source under the NCUL1 license (per the README). Do not copy it or imitate it pixel for pixel. Take the idea of "chart plus value in the corner" only.
- Demo: app.netdata.cloud/spaces/netdata-demo (README).

**10. Glances** - https://github.com/nicolargo/glances - LGPL-3.0 (README; GitHub says NOASSERTION) - ~34k stars
- Shows: CPU, RAM, swap, load, network, disk IO, sensors, processes, in TUI and web UI.
- Borrow: (a) every metric cell is color-coded by thresholds (careful, warning, critical) so a screenful of numbers becomes scannable at a glance; (b) quicklook strip: CPU, MEM, LOAD as three thin horizontal bars at the top; (c) alert summary list at the bottom with duration.
- Screenshots: https://github.com/nicolargo/glances/raw/develop/docs/_static/glances-webui-with-alert.png , webui-cpu-chart.png , glances-responsive-webdesign.png

**11. btop** - https://github.com/aristocratos/btop - Apache-2.0 - ~35k stars
- Shows: CPU per core, memory, disks, network, processes in a terminal.
- Borrow: (a) dense usage meters: thin bars with a percentage label, many per screen; (b) per-core mini graphs in a grid; (c) disk rows with used/free bar and IO speed; (d) selectable themes via a small token set. Braille/block graph resolution tradeoffs are interesting but irrelevant to Qt.
- Screenshots: https://github.com/aristocratos/btop/raw/main/Img/normal.png , alt.png

**12. Scrutiny** - https://github.com/AnalogJ/scrutiny - MIT - ~8.4k stars
- Shows: S.M.A.R.T. drive health with real-world failure thresholds.
- Borrow: (a) one card per disk: model, capacity, temperature, power-on hours, with a Passed/Failed status chip; (b) attribute table where each row has a failure-risk indicator and a tiny history; (c) temperature history as a line chart in the drive page. This maps directly to Lifetime Health and System hardware.
- Screenshot: https://github.com/AnalogJ/scrutiny/raw/master/docs/dashboard.png (README).

**13. Cockpit** - https://github.com/cockpit-project/cockpit - mixed (LGPL-2.1-or-later main, plus GPL-3.0+, BSD-3, MIT, CC-BY-SA-3.0 parts; GitHub shows null) - ~15k stars
- Shows: Linux server overview, services, storage, networking, logs.
- Borrow: (a) Overview page: a "Health" card, "System information" card with icon-labeled rows, and "Usage" card with CPU and memory progress bars; (b) logs page with severity filter chips and a time-grouped list; (c) service list with status text color. Uses PatternFly, which is MIT/Apache licensed.

**14. Pi-hole web** - https://github.com/pi-hole/web - EUPL-1.2 (not machine-detected by GitHub; confirm in the repo LICENSE before citing it anywhere) - ~2.6k stars
- Shows: DNS query stats.
- Borrow: (a) row of 4 colored KPI tiles with a big number, a label, and an icon; (b) donut for blocked vs allowed share; (c) a single time-series bar chart with two stacked series.
- Trademark: Pi-hole name and logo are theirs.

### C. Self-hosted homelab and ops dashboards

**15. Homepage** - https://github.com/gethomepage/homepage - GPL-3.0 - ~33.7k stars
- Shows: app tiles with live service stats and system widgets.
- Borrow: (a) service tile = icon + name + status dot + 2 to 3 small stat values; (b) top info widgets: resources bar (CPU, RAM, disk as thin bars with %), clock, search; (c) strong consistency: every tile is the same height and rhythm.
- Screenshot: https://github.com/gethomepage/homepage/raw/dev/images/1.png?v=2 (README). GPL-3.0, so licensing is compatible, but we still reimplement.

**16. Glance** - https://github.com/glanceapp/glance - AGPL-3.0 - ~38k stars
- Shows: feeds plus Docker and server stats widgets.
- Borrow: (a) widget cards with a quiet header and tightly aligned content; (b) server stats widget with CPU/RAM/disk as small rings or bars with numeric labels; (c) strong typographic hierarchy with a small set of colors. AGPL: concept only.
- Screenshots: https://github.com/glanceapp/glance/raw/main/docs/images/readme-main-image.png , themes-example.png

**17. Homarr** - https://github.com/homarr-labs/homarr - Apache-2.0 - ~5k stars on the new org repo (the older ajnart/homarr history may be what other sources cite)
- Shows: app boards with integration widgets and edit mode.
- Borrow: (a) edit mode where cards can be resized and rearranged; (b) integration status shown as a small colored dot; (c) widgets that show one number with an icon, rather than a text paragraph.
- Demo: https://demo.homarr.dev (loaded; it showed a login page with demo credentials).

**18. Dashy** - https://github.com/Lissy93/dashy - MIT - ~27k stars
- Shows: link dashboard with status checks and widgets.
- Borrow: (a) per-item availability dot and response time shown on hover; (b) theme picker with live preview swatches; (c) clean empty state ("no items yet, add one"). Lower priority; UI is more decorative than analytical.

**19. Coolify** - https://github.com/coollabsio/coolify - Apache-2.0 - ~66k stars
- Shows: servers, projects, deployments, logs.
- Borrow: (a) resource cards with a small status dot and "Running / Exited / Degraded" text; (b) deployment page with a live log pane with colored levels and a sticky status header; (c) step-by-step onboarding (connect server, validate, deploy) with checkmarks.

**20. Portainer** - https://github.com/portainer/portainer - Zlib - ~39k stars
- Shows: containers, stacks, environments, resource usage.
- Borrow: (a) home page of environment cards, each with count chips (3 running, 1 stopped, 2 unhealthy) and a status badge; (b) container table with state pill and quick action icons on the row; (c) dashboard summary tiles with an icon and a number. Name and logo are trademarks of Portainer.io.

### D. Observability and analytics (chart craft, KPI layout)

**21. Grafana** - https://github.com/grafana/grafana - AGPL-3.0 - ~78k stars
- Shows: everything, via panels.
- Borrow: (a) stat panel with big value, unit, and sparkline in the background; (b) gauge and bar-gauge panels with threshold bands (green, amber, red) drawn on the track; (c) state-timeline panel: horizontal colored segments over time per entity; (d) value mappings (0 = Down, 1 = Up) shown as colored text.
- Demo: https://play.grafana.org (my fetch showed "Error loading Grafana"; may be intermittent). Source is AGPL, concept only.

**22. SigNoz** - https://github.com/SigNoz/signoz - mostly MIT, with an enterprise `ee` directory under a separate license (GitHub shows NOASSERTION; check LICENSE before depending on that) - ~32.6k stars
- Borrow: (a) service list with p99 latency, error rate, ops/sec as columns with a mini spark bar; (b) trace/waterfall rows (relevant to a timeline rail); (c) alert rule builder as a visual "query, condition, notify" flow.

**23. OpenObserve** - https://github.com/openobserve/openobserve - AGPL-3.0 - ~23k stars
- Borrow: (a) log viewer with a histogram bar chart above the list, linked to the selected time range; (b) field sidebar with top values and counts as tiny bars. Relevant to the Lifetime Server log screen.

**24. Umami** - https://github.com/umami-software/umami - MIT - ~40k stars
- Borrow: (a) KPI row: value, delta vs previous period shown as a small colored arrow chip; (b) clean ranked lists with a horizontal bar behind each row (top pages, countries); (c) weekly heatmap by weekday and hour.

**25. Plausible** - https://github.com/plausible/analytics - AGPL-3.0 - ~29.8k stars
- Borrow: (a) single-page, no-nav layout: top stats strip that doubles as the chart selector (click a stat to switch the main chart); (b) ranked lists with proportional bars behind labels; (c) very restrained palette (one accent, grays).
- Demo: https://plausible.io/plausible.io (loaded). Name and logo are theirs.

### E. Design systems and templates (layout, cards, type)

**26. Tabler** - https://github.com/tabler/tabler - MIT - ~42k stars
- Borrow: (a) card library: stat card with icon badge, progress card, activity timeline, avatar list; (b) status "dot + text" badges and "pill" chips; (c) steps/stepper component.
- Demo: https://preview.tabler.io (loaded).

**27. Tremor** - https://github.com/tremorlabs/tremor - Apache-2.0 - ~3.7k stars (last push 2025-10; repo exists, still referenced widely)
- Borrow: (a) KPI card with delta badge; (b) Tracker component: a row of colored blocks = heartbeat/uptime bar, clean API for a colored tick row with tooltips; (c) CategoryBar / ProgressBar / BarList for segmented bars; (d) SparkChart inside table cells.

**28. shadcn/ui** - https://github.com/shadcn-ui/ui - MIT - ~128k stars
- Borrow: (a) dashboard example: section cards (value, delta badge, one-line caption), then area chart, then table; (b) badges, tabs, toggles; (c) restraint: one border color, one muted-foreground, tight type scale.
- Demo: https://ui.shadcn.com/examples/dashboard (loaded).

**29. next-shadcn-dashboard-starter** - https://github.com/Kiranism/next-shadcn-dashboard-starter - MIT - ~7.3k stars
- Borrow: (a) overview layout with 4 KPI cards over a main chart plus recent-activity list; (b) data tables with status badges and column filters; (c) empty-state and skeleton loading placeholders.

**30. Square UI** - https://github.com/zerostaticthemes/square-ui - license shown as "Other" (GitHub NOASSERTION); treat as look-only, do not even structurally copy - ~6.2k stars
- Borrow: general layout rhythm of dense list/board screens. Weak license clarity, so lowest priority.

### Verification notes

All 30 entries above were confirmed to exist via the GitHub API. Lookups that failed and were discarded: `mdsadiqueinam/statping-ng` (404; the real repo is `statping-ng/statping-ng`, entry 5) and `ln-dev7/square-ui` (404; it moved to `zerostaticthemes/square-ui`, entry 30). Star counts for Checkmk, Zabbix and Pi-hole web are for their GitHub repos (Checkmk and Zabbix are mirrors of self-hosted upstreams), so they understate real usage.

---

## 2. License and trademark notes that matter

- Lifetime is GPL-3.0. Reading any repo for ideas is fine. Copying source from AGPL (Grafana, Glance, Zabbix, Plausible, OpenObserve) or GPL-2.0-only (Checkmk) projects into Lifetime would be a license problem, and these are web stacks anyway. We reimplement in QPainter.
- Netdata: the agent is GPL-3.0+, but its dashboard UI is closed source (NCUL1). Do not imitate it closely and never embed or reuse its assets.
- Square UI has an unclear "Other" license. Use only as a general mood reference, if at all.
- Trademarks and logos (Uptime Kuma frog, Grafana, Netdata, Pi-hole, Portainer, Plausible, Checkmk, Zabbix) must not appear in Lifetime. Do not name them in the UI or in marketing copy as endorsement. Naming them in internal research docs, as here, is fine.
- Icons: do not ship third-party icon fonts or images from these repos. Use our own painter-drawn glyphs or an icon set whose license we already track in THIRD_PARTY_NOTICES.md.
- Color conventions (green OK, amber warning, red critical, grey unknown) are universal and not protectable.

---

## 3. What Lifetime already has (reuse before building)

From the current code (files in D:\Dev\Lifetime-merge): `lifetime_charts.py` has `Plot`, `Sparkline`, `LiveSparklines`, `Calendar` (a calendar heat map), `HeatScale`, `RangeSegments`, `ChartCard`, `Insights`, `motion_reduced()`, `bounded_tooltip()`; `lifetime_glass.py` has `paint_glass`, `paint_classic`, `paint_track`, `glass()`; `lifetime_server_widgets.py` has `status_dot()`, `reduced_motion()`, `DailyChart`, `FleetTabBar`; `lifetime_system_gui.py` has `HardwarePanel`; `lifetime_health_gui.py` has `GoodDot`, `HealthPlot`. New widgets below should sit next to these, share the `motion_reduced()` check, and use the same `Plot` base for hover and tooltip behavior. Confirm exact signatures in code before building; this research did not modify or run anything.

---

## 4. Pattern catalog for Lifetime

Common build rules for all patterns:
- Subclass `QWidget`, override `paintEvent`, draw with `QPainter` using theme tokens (navy surfaces, amber `#FFC93C` accent, plus semantic ok/warn/bad/unknown colors that already exist or are added once in the theme module). No hardcoded colors in widgets.
- Card backgrounds go through `paint_glass` (Glass style) or `paint_classic` (Classic) so both styles look right.
- Never use color alone: pair every state color with a glyph or text (check, bang, cross, dash) for color-blind safety and for the Windows high-contrast case.
- All visible strings go through `lifetime_i18n` (EN and VI). Vietnamese strings are often longer; size chips and tiles by `QFontMetrics` of the translated text, elide with tooltip. Format numbers and dates by locale.
- Animation (value tween, tick fade-in, ring sweep) only if `motion_reduced()` is False; the reduced path must draw the final state instantly.
- Expose `setAccessibleName`/`accessibleDescription` with the full sentence (for example "Disk C: 62 percent used, 118 GB free"), since painted widgets are invisible to screen readers.
- Keep paint cheap: cache static geometry and pixmaps, repaint on data change only (fleet lists can hold 500 rows; use a delegate-painted row in a `QListView`/`QTableView` rather than 500 child widgets).

### P1. Status chip / pill
- What: small rounded label with a dot or glyph plus a short word: Online, Offline, Warning, Idle, Unknown, Maintenance, Passed, Failed.
- Repos: Uptime Kuma, Gatus, Portainer, Tabler, shadcn/ui, Scrutiny, Coolify, Healthchecks.
- Replaces: server Devices list "status" text column; device detail header status text; Incidents severity and state words; Network checks result text; Administration user role/state text; desktop Health "Good/Warning" lines; Connect page "Connected / Not connected" sentence.
- Why: the technician filters by state first. A fixed set of 5 to 6 colors with glyphs turns a column of words into a scannable column of shapes.
- Build: one `StatusChip(QWidget)` and a matching painter function `paint_chip(painter, rect, kind, text)` usable inside item delegates. Rounded-pill rect, tinted fill (state color at about 15 percent alpha), 1px state border, glyph then text. Kinds enumerated in one dict so table and card share it.

### P2. Heartbeat / uptime tick bar
- What: a row of 30 to 90 narrow vertical ticks, one per check or per hour or day, colored by state; the newest on the right; tooltip on hover shows the time and result.
- Repos: Uptime Kuma, Gatus, Tremor Tracker, Statping-ng, Healthchecks.
- Replaces: server Availability screen (currently text/percent lists); Network checks "last results" text; Devices list "last seen" column on hover; device detail "online history" text. Desktop: Connect page "connection history".
- Why: shows flapping vs steady outage vs one blip, which a single "99.2%" cannot. This is the single most decision-relevant visual for a fleet.
- Build: `HeartbeatBar(QWidget)` taking a list of `(ts, state, note)`; paint with `fillRect` rounded 1px ticks, width computed to fit, gap 1 to 2 px; bucket when there are more points than pixels (worst state wins per bucket). Hover: find tick under cursor, use existing `bounded_tooltip`. Provide a mini variant (height 14, no tooltip) for table delegates.

### P3. Mini sparkline beside a value
- What: tiny line or area chart, no axes, next to the number it explains (CPU %, RAM %, temp, network rate, latency).
- Repos: Beszel, Netdata, Grafana stat panel, LibreNMS, Tremor SparkChart, SigNoz.
- Replaces: desktop System hardware label/value rows (CPU load, temperature, fans, GPU); server Devices list CPU/RAM columns; device detail live metrics text; Network checks latency text.
- Why: a value without trend hides whether 78 percent is a spike or a plateau. Adjacent trend answers that without opening a chart.
- Build: reuse `Sparkline` and `SampleBuffer` from `lifetime_charts.py`. Add a compact mode (fixed 64x20, no tooltip, last-point dot, optional threshold line). In table rows, draw through a delegate that reads a short array from the model role. Skip the sweep animation under reduced motion.

### P4. Ring / arc gauge for percentages
- What: 270-degree or full ring with the percent in the center, colored by threshold.
- Repos: Glance, Grafana gauge, Pi-hole donut, Homepage resources, Cockpit usage, Glances quicklook.
- Replaces: desktop System CPU/RAM/disk/battery as plain "CPU: 34 %" lines; Health score and battery health text; server Overview "fleet health" sentence; Overview "devices online 87 of 100" text.
- Why: a percentage with a known ceiling is exactly what a ring communicates; the center holds the exact figure for precision and the arc gives instant magnitude.
- Build: `RingGauge(QWidget)` with `drawArc` using `Qt.PenCapStyle.RoundCap`; track via `paint_track`; value arc color from thresholds (default 70 / 90, configurable per metric); center text two lines (value, caption). Animate arc over about 250 ms only when motion is allowed. Limit to 3 to 5 rings per screen, otherwise use P5 bars.

### P5. Segmented capacity bar (disk / RAM / storage)
- What: a horizontal bar split into labeled segments (system, apps, user data, free), with a legend of values below or on hover.
- Repos: btop (disk and memory meters), Cockpit, Homepage, Glances, Scrutiny, Grafana bar-gauge.
- Replaces: desktop System disk list ("C: 931 GB, 512 GB used, 419 GB free" as text) and RAM line; server device detail storage rows; server Overview "storage across fleet"; Reports storage section.
- Why: used vs free vs reserved is a proportion question. One bar replaces three numbers and shows which drive is nearly full.
- Build: `CapacityBar(QWidget)` taking `[(label, value, token_color)]`; rounded track, clip to rounded rect then `fillRect` segments in order; 2px gap lines between segments; free space drawn as the track color. Text left (name), right (used / total) in tabular figures. Color the whole bar amber/red when free < 15/5 percent. Also provide a thin 6 px variant for rows.

### P6. Icon + value KPI tile
- What: card with a small icon in a tinted rounded square, large number with unit, label, and an optional delta chip versus the previous period.
- Repos: Tabler, shadcn/ui, Pi-hole, Umami, Plausible, Portainer, Checkmk tactical overview, next-shadcn-dashboard-starter.
- Replaces: desktop Overview total-hours area if shown as a text block (today, week, month, all time); Activity summary sentences; server Overview KPI row (devices, online, offline, open incidents, alerts today); Reports header numbers.
- Why: lets a manager read 4 to 6 headline numbers in one second. Clickable tiles also serve as filters (click "Offline 7" to open the filtered device list).
- Build: `KpiTile(QFrame)` with `glass()` styling; painted icon from our own glyph set; value font large and bold, unit smaller; delta chip uses P1 painter with up/down arrow, green only if "up is good" for that metric (hours up good; incidents up bad). `clicked` signal. Fixed min width from translated label metrics.

### P7. Compact device card with status color edge
- What: card per device: 4 px left edge in the state color, name, group/room, IP/OS in muted text, status chip, 2 to 3 tiny metrics (P3 sparklines or P5 slim bars), last seen relative time.
- Repos: Beszel systems grid, Portainer environments, Homepage service tiles, Homarr, Coolify resource cards.
- Replaces: server Devices list as a plain table (offer a Cards toggle beside Table; keep the table for 100+ devices and sorting); Overview "problem devices" text list; Connect page peer list on desktop.
- Why: for 20 to 80 PCs (a school lab), cards show room-level health at a glance; edges make outliers jump out. For 500, default to Table and use cards only for filtered subsets.
- Build: item delegate on a `QListView` in `IconMode`, or a flow-layout of `DeviceCard` widgets for under 100 items. Edge drawn with `fillRect` inside a clipped rounded rect. Selected card gets amber outline. Elide long names with tooltip, and support VI diacritics in font metrics.

### P8. Dense sortable table with row-level visuals
- What: the table stays (technicians need sorting and bulk actions), but cells carry visuals: status chip, inline slim bar for CPU/RAM/disk, tiny tick bar for uptime, relative time with full timestamp in tooltip, a sticky header, zebra off, row hover highlight.
- Repos: Portainer, LibreNMS, Zabbix, SigNoz service list, Beszel, Umami ranked lists.
- Replaces: server Devices list, Incidents list, Network checks list, Administration users/tokens lists, server log table.
- Why: a good table is the right tool at 500 rows; making cells visual removes the "wall of text" without losing density.
- Build: `QStyledItemDelegate` subclasses for chip, bar, spark cell types, selected by a role. Model supplies raw values. Keep sort on raw values, not display text. Column visibility menu persisted in settings.

### P9. Timeline / event rail
- What: vertical rail with dots (colored by type) and entries grouped by day: "Offline 09:12 - back online 09:40 (28 min)", with expandable detail.
- Repos: Healthchecks ping log, Coolify deployment logs, Cockpit logs, Tabler activity feed, SigNoz traces, Grafana state timeline.
- Replaces: server Incidents screen text rows; device detail "recent events" text; server log screen (as an alternative view with severity filter chips); desktop Activity "sessions" lists; Reports incident section.
- Why: incidents are sequences with durations, and overlaps matter (three PCs went offline together = network, not hardware). A rail with duration bars exposes that.
- Build: `EventRail(QWidget)` inside a `QScrollArea` or, better, a delegate-painted `QListView` for long logs. Left gutter: line plus dot via `drawLine`/`drawEllipse`; day header rows; duration shown as a small proportional bar. Severity filter chips (P1) above. For overlapping incidents, a swimlane variant (Grafana state-timeline idea): one row per device, colored segments over time.

### P10. Health score badge with breakdown
- What: one large score (0 to 100 or A to D) in a ring or badge, with 3 to 5 contributing factors beneath as small chips or bars (disk SMART, battery, temperature, updates, uptime/crashes).
- Repos: Scrutiny pass/fail plus risk indicators, Checkmk host state, Cockpit Health card, Glances quicklook.
- Replaces: desktop Health page's long finding lists; System page top summary; server device detail "health" paragraph; server Overview fleet-health sentence.
- Why: the user wants one answer ("is this PC OK?") with the reason one click away. The breakdown prevents an opaque score; each factor says why it counted.
- Build: reuse P4 `RingGauge` for the score and P1 chips for factors; each factor row = glyph + name + slim P5 bar + short verdict string. The scoring rules stay in `lifetime_health.py`; the widget only displays. Document the thresholds in a tooltip so the number is explainable.

### P11. Key-value cards grouped with icons (replaces long label/value lists)
- What: instead of one list of 30 rows, group into 4 to 6 cards (Processor, Memory, Storage, Graphics, Network, Operating system), each with an icon header, 3 to 6 rows in two columns, and the single most important value promoted (large) at the top of the card.
- Repos: Cockpit system information, Beszel system page, Homepage info widgets, Tabler cards, Netdata sections.
- Replaces: desktop System hardware readings (`HardwarePanel`); server device detail hardware/specs text; desktop Settings "About" and diagnostics blocks; Connect details (address, port, certificate fingerprint, version).
- Why: grouping by what the technician is troubleshooting (storage, network) is the main fix for "boring text". Promoted value gives each card a headline; the rest is reference.
- Build: `InfoCard(QFrame)` with `glass()`, a header painted with a glyph and title, then a grid of `QLabel` pairs (muted key, strong value, tabular figures) with copy-on-click for values like serial, MAC, IP (a technician need). Flow cards in a responsive grid that goes from 3 columns to 1 under a width threshold. Hide rows with no data instead of printing "N/A" lines.

### P12. Progress stepper for setup and long operations
- What: horizontal numbered steps (Choose server, Enter token, Verify, Done) with done/current/pending states and a message for the current step.
- Repos: Coolify onboarding, Tabler steps, Portainer environment wizard.
- Replaces: desktop Connect page and first-run setup; server AddDeviceSheet text instructions; server install/enrollment flow; Administration "create user" and token flows; update checks.
- Why: setup failures are the most common support burden; a stepper shows where the user is and what failed, and lets us put the error on the specific step.
- Build: `Stepper(QWidget)` paint: circles joined by lines, check glyph for done, amber ring for current, red cross for failed step with message under it. Keyboard navigation where interactive; `accessibleName` "Step 2 of 4: Verify". Fade only when motion is on.

### P13. Calendar heat strip / heat map
- What: GitHub-style grid or a single-row strip of days colored by intensity (hours awake; availability; incident count).
- Repos: Umami weekday/hour heatmap, Healthchecks timelines, Plausible weekly heat, Tremor Tracker (strip form).
- Replaces: desktop Activity "daily totals" text and long lists; Overview "this month" figures; server Availability monthly text (a 30 or 90 day strip per device); Reports weekly summary.
- Why: habits and anomalies (a missed backup week, a PC that is off every Friday) pop out in a grid, not in lists. School managers can read it without training.
- Build: reuse `Calendar` and `HeatScale` in `lifetime_charts.py` (already present); extend with a compact one-row strip mode (cell 10 to 14 px) and a weekday-by-hour grid. Use a single-hue ramp (navy to amber) for intensity and a distinct grey for "no data" so missing is not confused with low.

### P14. Ranked list with proportional bars
- What: each row has a label, a value, and a horizontal bar behind or under the label scaled to the max.
- Repos: Plausible, Umami, OpenObserve field values, Pi-hole top domains, Glances processes.
- Replaces: desktop Activity "top apps" text lists; Reports "most active devices" and "devices with most incidents"; server Overview "top offenders" lists; Network checks "slowest targets".
- Why: ranks and magnitude in one glance; no legend or axis needed.
- Build: `BarList(QWidget)` painting rounded bar at 20 percent alpha behind a text row; width = value / max; keyboard selectable; values via locale-aware formatting; clicking filters.

### P15. State summary strip (segmented count bar)
- What: one wide bar across the top of Devices or Overview, split into Online / Warning / Offline / Unknown proportional segments with counts; clicking a segment filters the list.
- Repos: Checkmk tactical overview, Zabbix severity strip, Portainer environment chips, Uptime Kuma's top counters.
- Replaces: server Overview "87 online, 5 offline..." text; Devices list header counts; Incidents summary text.
- Why: answers "how bad is it right now" before anything else, and works as the primary filter control.
- Build: reuse P5 `CapacityBar` with state colors plus clickable segments (hit test on x). Always show counts as text beside the bar, never only color.

### P16. Empty-state panel
- What: a simple painted icon/line illustration, a one-sentence explanation of why it is empty, and a primary action ("Add first device", "Run a check now").
- Repos: next-shadcn-dashboard-starter, Tabler, Dashy, Coolify.
- Replaces: blank tables/lists on server Devices (none yet), Incidents ("no incidents" is good news and should say so), Network checks (none configured), Availability (no data yet), desktop Health/Activity before first data, Reports with no range data.
- Why: distinguishes "nothing is wrong" from "nothing is set up" from "failed to load", which a blank area cannot. This is a decision aid for technicians, not decoration.
- Build: `EmptyState(QWidget)` with three kinds (all-clear, not-configured, error/retry) each with its own glyph (simple line icon drawn with `QPainterPath` in muted + one accent stroke). One action button. Strings in EN/VI. No mascots, no large illustrations.

### P17. Threshold bands and color-coded values (Glances / Grafana style)
- What: values and bar fills change color by thresholds (ok, caution, critical), and a thin marker on gauges shows where the thresholds are.
- Repos: Glances, Grafana, btop, Netdata, Checkmk.
- Replaces: plain black/white values on desktop System (temperature, load, disk usage) and server device detail; Network checks latency numbers; Settings of alert thresholds (a form of 8 numeric boxes).
- Why: one metric's number means nothing without a threshold. Showing the band makes the number a verdict. In Settings, a slider on a banded track makes threshold choice understandable.
- Build: a single `threshold_color(metric, value)` helper reading thresholds from settings; reuse it in P3, P4, P5, P8. Threshold editor: `QSlider`-like painted handle(s) on a three-band track instead of spin boxes. Cap at one warning and one critical level per metric.

### P18. Segmented range selector and time-linked charts
- What: one control (1h, 24h, 7d, 30d, custom) that sets the range for every chart on the screen.
- Repos: Beszel system page, Grafana time picker, Umami, Plausible, Netdata.
- Replaces: separate per-chart range controls on Activity, System history, device detail charts, Availability, Reports.
- Why: comparing CPU, RAM, and network on the same window is how technicians diagnose; independent ranges cause mismatches.
- Build: `RangeSegments` already exists; connect it to all `Plot` instances in a screen through one signal. Add optional crosshair sync (hover shows the same time on sibling charts) via a shared hover time in a small coordinator object.

### P19. Grouped, collapsible sections with worst-state header
- What: sections (by room, group, site) with a header showing name, count, and worst-state chip; collapsed by default when all OK, expanded when not.
- Repos: Gatus endpoint groups, Checkmk host groups, Uptime Kuma groups, Portainer environments.
- Replaces: server Devices list for fleets (group by lab/room), Network checks grouped by target type, Administration settings pages with 30 fields in one scroll (group into collapsible cards), desktop Settings long forms.
- Why: with 20 to 500 PCs, a manager thinks in rooms. Auto-expanding only the unhealthy groups surfaces the problem without a search.
- Build: `QTreeView`/`QAbstractItemModel` with group header delegate, or a vertical stack of `Section` widgets with animated height only when motion is allowed. Persist expand state per user.

### P20. Form cards with inline validation and presets (replaces plain forms)
- What: forms split into titled cards; related fields placed in a row; live validation icon (check, warning) next to the field; presets as chips (for example "Every 5 min", "Every 15 min") rather than an empty number box; a summary sentence ("Alert when CPU over 90 percent for 5 minutes").
- Repos: Uptime Kuma add-monitor page, Healthchecks period/grace dialog, Beszel alert settings, Grafana alert rule builder, shadcn/ui forms.
- Replaces: server Administration forms (users, tokens, backup, update channel), Network check editor, Alert rules; desktop Settings pages; AddDeviceSheet.
- Why: reduces input errors and makes it obvious which fields matter. The summary sentence confirms what will happen before saving.
- Build: standard Qt widgets styled through the theme (no custom paint needed for inputs), plus a small `ChipGroup` (exclusive checkable pills, reusing P1 paint) and a `ValidationHint` label with glyph. Keep the "Save" button disabled until valid, with the reason in a tooltip.

---

## 5. Top 10 to build first

Ranked by value for technicians and managers divided by build effort, using existing code where possible.

1. **P1 Status chip** (all server tables, device header, Health) - tiny to build, used by everything else.
2. **P2 Heartbeat / uptime tick bar** (server Availability, Network checks, Devices hover) - highest decision value for a fleet; cheap `paintEvent`.
3. **P6 + P15 KPI tile row with state summary strip** (server Overview; desktop Overview headline numbers) - gives the first screen a dashboard feel and doubles as filter.
4. **P11 Key-value cards grouped with icons** (desktop System hardware panel; server device detail hardware/specs; Connect details) - direct cure for "boring text" lists.
5. **P5 Segmented capacity bar** (desktop System disks and RAM; device detail storage; Reports) - replaces the most text-heavy numbers.
6. **P3 Mini sparkline beside value** (desktop System rows; Devices list CPU/RAM columns) - reuses `Sparkline`, so low effort.
7. **P8 Dense table with visual cells** (Devices list, Incidents, Network checks) - keeps 500-row scalability, brings in P1/P2/P3/P5 as delegates.
8. **P10 Health score badge with breakdown** (desktop Health, System summary; device detail health) - single answer with reasons; uses P4 ring and P1 chips.
9. **P9 Timeline / event rail** (Incidents, device detail events, server log alternate view) - makes incidents readable as sequences with durations.
10. **P16 Empty states** (Devices, Incidents, Network checks, Availability, Health before data) - low effort, high clarity, fixes the "blank screen" case; follow with P12 stepper for Connect/Add device and P13 heat strip for Availability/Activity as the next batch (they would be #11 and #12).

Suggested sequence: build P1, P2, P3, P5 as a shared widget module first (they are paint primitives reused everywhere), then assemble P6/P15, P11, P8 on top, then P10, P9, P16.

---

## 6. What NOT to do

- No decorative gradients, glow, blur beyond what `paint_glass` already does, or drop shadows stacked on shadows. Surfaces stay flat navy; amber only for the accent and the "current/selected" signal.
- No fake 3D, skeuomorphic gauges (needles, bezels, chrome), or isometric illustrations.
- No more than 5 semantic colors on screen (ok, warn, bad, unknown/idle, accent). Do not give every metric its own hue; use one hue per state, plus neutral greys.
- Never use color alone for state; always add glyph or text. Check contrast on both Glass and Classic styles.
- No animated counters, endless pulsing dots, or continuous marquee. Motion is only for state change, short (under 250 ms), and fully skipped under reduced motion.
- No rings or sparklines for data that has no ceiling or no trend (a hardware serial number needs no chart).
- No more than 3 to 5 rings per screen; beyond that use bars or a table. No pie charts with more than 4 slices.
- Do not replace a table with cards when the user needs sorting, bulk actions, or 100+ rows. Keep both and let the user switch.
- Do not hide the exact number: every visual keeps the precise value in text or a tooltip (technicians copy numbers into tickets).
- Do not copy layouts, icons, or logos from the repos above, and do not name them in the UI. No Netdata UI imitation (closed UI license).
- No AI-style filler: no emojis in the UI, no motivational copy, no "insights" text that the data does not support. Every new widget must answer a question the user actually asks ("which PCs are down?", "is this disk filling up?", "did it flap last night?").
- No new third-party dependency (chart library, icon font, QML) for these; PyQt6 painting is enough and keeps the GPL-3.0 and bundle story clean.
- Do not paint 500 child widgets for a large list; use delegates and virtualized views.
- Do not forget Vietnamese: test every chip, tile, and card with VI strings and with the largest system font scale before shipping.
