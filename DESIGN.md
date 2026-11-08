---
name: Lifetime
description: Native desktop awake-time tracker and local system dashboard.
colors:
  bg: "#0B1426"
  sidebar: "#08101F"
  card: "#12203A"
  text: "#FFFFFF"
  muted: "#9FB0CC"
  border: "#22345A"
  accent: "#FFC93C"
  accent-hover: "#FFD761"
  track: "#1B2A46"
typography:
  display:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "64px"
    fontWeight: 300
  body:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "15px"
  label:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "13px"
  title:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "28px"
    fontWeight: 600
    letterSpacing: "-0.5px"
  stat-value:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "20px"
    fontWeight: 500
  group-title:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "18px"
    fontWeight: 600
rounded:
  signal-card: "10px"
  button: "8px"
  segment: "8px"
  navigation: "8px"
  inset: "10px"
spacing:
  compact: "8px"
  grid: "16px"
  window: "32px"
  sidebar: "232px"
  rail: "64px"
components:
  button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.button}"
    padding: "8px 12px"
  start-tracker:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.button}"
  export-report:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.bg}"
    rounded: "{rounded.button}"
  signal-card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.signal-card}"
    padding: "12px 20px 14px"
  segment-selected:
    backgroundColor: "{colors.track}"
    textColor: "{colors.text}"
    rounded: "{rounded.segment}"
    padding: "5px 12px"
  baseline-input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.button}"
    height: "40px"
  navigation-selected:
    backgroundColor: "{colors.track}"
    textColor: "{colors.text}"
    rounded: "{rounded.navigation}"
    height: "44px"
  inset-panel:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.inset}"
---

# Design System: Lifetime

The server edition follows the same palette and native type, with its separate
fleet layout and verification notes in [docs/design/server.md](docs/design/server.md).
Desktop Overview, System and Insights are outside the server redesign scope.

## Overview

**Creative North Star: "Calm native instrument"**

Lifetime is a native desktop utility with an established navy, yellow, and white identity. Preserve that identity when extending the interface. This document records the implemented system in `lifetime_gui.py`, `lifetime_shell.py`, `lifetime_dashboard.py` and `lifetime_charts.py`; `THEMES['dark']` in `lifetime_theme.py` remains the palette source of truth. The retained legacy light-theme dictionary is not selected by the current window.

The interface is adult, professional, and direct. A prominent lifetime total leads Overview, while labeled local readings and clear unavailable states make System useful without implying more certainty than the OS provides.

**Key Characteristics:**

- Readable type with strong numeric hierarchy.
- Restrained yellow accents on navy surfaces.
- A persistent sidebar, clear page titles, and aligned data rows.
- Native Qt widgets and painted symbols, without external font or image dependencies for dashboard content.

## Colors

### Primary

The warm yellow `accent` is reserved for the Overview ring's endpoint and the
Reports export action, plus the small active-navigation mark and control focus.
The Overview trend uses muted blue bars so the total stays the focal point.
Tracking status is a steady white dot when active and muted when offline.
System and Activity retain their existing chart colors for the next redesign pass.

### Neutral

`bg` is the deep navy window canvas; `card` creates a slightly lighter reading surface. `text` is white for primary values and headings. `muted` softens labels, units, captions, and unavailable readings. `border` defines controls and System card edges; `track` supports meters, pressed controls, and selected navigation.

Preserve these existing roles. A new dashboard section does not authorize a palette redesign.

## Typography

Use Segoe UI on Windows and DejaVu Sans on Linux. Qt selects the platform-specific family explicitly; the frontmatter lists both for portability.

Body and System values use 15px. Captions, status and supporting labels use
13px; page titles use 28px semibold with -0.5px letter spacing. Sidebar branding
uses 23px semibold. Overview's total uses 64px light type, fitting only the number
when necessary; its live duration remains legible beneath it. Grouped statistics
use 20px medium values, fitting translated durations down to 15px before eliding
with a full-value tooltip. Never shrink body or label text to avoid scrolling.

Right-align System values and request tabular numerals where available. Long
device names wrap inside their column. English and Vietnamese relabel in place.

## Layout

This is an Operate surface for people checking how long their PC has been awake.
The direction is a calm native instrument: ENERGY 1 / RHYTHM 2 / MOTION 2. Lifetime's
navy, yellow, white and ring identity remain; familiar native conventions provide
craft without copying an Apple screen.

A 232px sidebar in #08101F is the heavier structural material. The canvas remains
#0B1426 and the inset surfaces #12203A. At widths below 900px the sidebar becomes
a persistent 64px icon rail, including at 390px. Labels remain accessible through
tooltips and accessible names. The Lifetime image is 28px; the supplied JPL
Innovation image and credit sit above the small EN / VI control at the bottom.
Settings is pinned above this bottom group. There is no header action bar or footer.

The scrolling canvas has a 1200px content maximum and 32px margins, reduced to
16px when the available canvas is below 850px. Spacing follows an 8px grid with 16px
between related sections. Every page starts with its own title. Only the current
page determines the scroll height; switching away from Activity or System must
not leave empty scrolling space on Overview or Reports.

Overview uses one grouped stats panel with dividers instead of five separate
cards: these are supporting readings, so a shared surface makes them easy to scan
without competing with the total. Order: Current session, Longest session, Average
per day, Boots observed, Earliest known date. At less than 660px of content width,
the panel becomes an inset list with labels left and values right.

The total and its repeating 100-hour ring lead, with Verify beside the number and
its result immediately below. The compact 14-day daily chart opens Activity by
click, Enter or Space. Unknown dates stay unknown and recorded zeroes stay zero.
All saved-source, coverage, system-name and counting explanations are collected
in one expanding "How this is counted" row. Import starting hours lives inside
that disclosure. The ring's cycle is explained there; it is not a health score.

Reports uses an inset list of Export report, Device identity, Coverage details
each with a description and the original working action.
Only Export report uses yellow fill. Descriptions wrap at narrow widths; below
480px of content width the trailing action moves below its copy. Offline tracker recovery remains available
beneath the active page. Consent continues to hide disabled Activity and System
categories, including the Overview trend.

System uses four full-width grouped panels in reading order: Power, Network,
Graphics, Computer. Live series sit inline beside right-aligned values rather
than occupying separate cards. Activity leads with its calendar and daily chart;
four quieter charts follow in two columns, collapsing to one below 820px of
content. Each page has one Show data toggle. No page introduces outer horizontal
scrolling.

## Elevation & Depth

System cards and controls use flat tonal separation and thin borders. Depth comes from the navy canvas, lighter surfaces, and clear grouping rather than drop shadows. Grouped statistics and charts retain native Qt rendering; there are no decorative shadows, gradients, glow or glass.

## Shapes

`lifetime_theme.controls()` owns control rendering in both editions and all
dialogs. Every control uses `CONTROL_RADIUS` (8px), every panel uses
`PANEL_RADIUS` (10px). Transparent internal editors and borderless viewports have
no visible box. Radio dots and thin meter caps are the only geometric exceptions;
tooltips are square. Popup windows use the application-level translucent,
frameless helper, including parentless tray menus. Qt styles that render CSS
border triangles as bars use a shared painted triangle fallback, without images.
Both application entry points use PassThrough display scaling; controls and
panels retain complete borders at fractional device pixel ratios.

System cards use gently rounded corners; buttons and the baseline input use the button radius. Sidebar selection uses an 8px radius; inset groups use a 10px radius. Thin horizontal progress meters have a small rounded cap. Icons are simple QPainter line symbols with rounded joins.

## Components

### Buttons and input

Standard buttons use a navy surface and thin border, with a yellow border on hover and focus. The tracker start action is a quiet navy recovery button; Reports export uses yellow fill and navy text. Pressed buttons use the track surface. The baseline input matches standard control shape and supports keyboard editing. The import toggle is a quiet text action with a focus underline.

Verify disables itself during a background request and exposes a busy state. Completion restores the action and shows the comparison or an explicit unavailable reason. The action is read-only.

### Navigation

Visual order is Overview, Activity, System, Health, Connect, Reports, with Settings in the bottom
group. Monoline symbols are painted in Qt, not emoji or icon fonts. Hover uses a
quiet navy fill and keyboard focus uses a visible muted outline. Tab and Shift+Tab
reach every row; Up/Down moves focus and Enter or Space activates. Ctrl+1 through
Ctrl+6 follow visual order. For existing integrations the public page IDs stay
0 Overview, 1 System, 2 Activity, 3 Reports; Health uses page ID 4 and Connect uses page ID 5.

The rounded selection surface glides to the selected row over 260ms OutCubic.
An interrupted animation starts from its current painted position. Pages accept
input immediately, fading in while settling upward by 8px over 220ms. Repeated
clicks cancel obsolete transitions, with the last requested page winning.
`LIFETIME_REDUCE_MOTION=1` or `REDUCE_MOTION` makes selection and page switching
instant. The disclosure expands over 200ms, also instant with reduced motion.

### System cards

Six 120x28 System sparklines share the CPU, RAM, battery power, GPU power,
download and upload value rows. Empty samples paint a thin baseline. Five-minute
buffers remain in memory, preserving zeroes and sensor gaps. One page-level Show
data toggle reveals accessible tables with second-resolution timestamps; consent
revocation clears the affected samples and tables. Multiple GPUs use an explicit
total-power row for the aggregate series.

Each detailed card has a group title followed by left-aligned field labels and right-aligned values. Percentage meters appear only for available percentage readings. Thin yellow meters supplement the displayed value; they never stand in for missing data. Unavailable values use the same row structure and a muted textual state.

### Total, charts, and motion

Overview gives the lifetime total the strongest type hierarchy. Activity uses native QPainter charts with one shared 30-day, 90-day, calendar-year, or All filter. Six titled cards cover the calendar, daily hours with a white 7-day average, cumulative lifetime hours, local hour profile, weekday averages, and session lengths. The leading calendar/daily pair uses 330px panels; four supporting charts use
280px panels and quieter blue marks in two columns. Compact views use one column. Longer calendars scroll horizontally inside their card, preserving legible day cells. Cards retain a consistent height, sparse muted axes, thin marks, generous hover lanes, and one keyboard-accessible page-level Show data toggle. Missing dates remain unknown, and recorded zeroes remain zero. The five-step calendar scale increases in lightness toward brand yellow and saturates at 24 hours, preserving larger boot credits in tooltips and tables. Cumulative totals retain the opening balance and mark the original live tracking boundary. Short-range hourly profiles use retained imported awake intervals and explicitly describe partial coverage; undated all-time hourly totals are never presented as a complete filtered profile. Existing entrance and numeric transitions are brief; `LIFETIME_REDUCE_MOTION=1` disables motion. Busy feedback must remain understandable without animation.

## Do's and Don'ts

- **Do** preserve the incumbent palette and platform typography.
- **Do** keep units secondary and numeric columns aligned.
- **Do** show truthful local readings and explicit unavailable states.
- **Do** retain keyboard focus feedback and reduced-motion support.
- **Don't** invent a percentage, hardware reading, or historical sample to fill space.
- **Don't** introduce a new palette, decorative imagery, or an unrelated component style when extending the dashboard.
- **Don't** reduce the established body and label sizes to avoid scrolling.

## Setup and Settings

This is an Operate surface. A four-page interview asks about tracking, log rebuild,
and login tracking, then shows a summary. Keep the privacy introduction on the first
page. Optional choices start off, including the separate Wi-Fi-name permission.
Progress and Back/Next stay outside the scrolling page; the last primary action is
Start (Save settings when reopened). Closing before Start records no consent.

The dialog uses the existing navy palette, Segoe UI on Windows and DejaVu Sans on
Linux, 26px question headings, 15px controls and 13px explanations. Checked boxes
retain a drawn check mark and fixed indicator dimensions at every DPI. Radio
choices remain mutually exclusive. Descriptions wrap beneath their controls;
small screens scroll without shrinking text. Settings exposes confirmed history
deletion with a retained local backup. No optional telemetry appears in setup.

`render_setup.py` captures the four pages and Settings with installed Windows font
files. The root shots use 100%; `shots/setup-200` records 200% captures. Both include
a compact view to check scrolling and navigation access.

## R1 verification

`render_redesign.py` uses `QT_QPA_PLATFORM=windows`, installed Segoe UI and only
the existing `.redesign-data` private copy. It records the four pages at 1280x800
and 860x640, Vietnamese Overview, the 760x560 rail, 390px Overview, expanded
counting details and baseline import, 390px Vietnamese Reports at the top and bottom,
and three mid-transition frames under `shots/redesign/`.
The audit records platform, font, geometry and scroll bounds. No render uses
AppData, fabricated counters or a fabricated tracking heartbeat.

`test_redesign.py` checks native input navigation, interruptible indicator motion,
last-click selection, both reduced-motion flags, persistent rail and 390px scroll
bounds, logos, report dispatch, Settings, consent, live translation and truthful
trend gaps. Existing keyboard-export and language-placement tests now follow the
new Reports page and sidebar locations.


## R2 verification

`render_redesign_r2.py` captures System and Activity at exact 1280x800 and
860x640 pixel sizes using native Windows Qt and installed Segoe UI. It reads only
`.redesign-data`, waits for actual local probe samples, and verifies the private
ledger remains unchanged. Captures include both page data toggles, Vietnamese
Activity, counting details, and scrolled views of all six charts.

Activity's segmented ranges use the sidebar's interruptible 260ms OutCubic
selection motion. Counting details expand over 200ms. Both reduced-motion flags
make these instant. Chart tooltips are clamped within the window, axes reserve
their measured label width, and tables align numeric cells with tabular figures.


## Connect

Connect is page ID 5, directly above Reports. It uses the existing navy palette,
28px page title, insetPanel and yellow primaryAction tokens. One step occupies a
centered column up to 560px: code, certificate search, sharing, approval, status.
Transitions crossfade for 180ms and are instant with reduced motion. Sharing uses
keyboard-accessible switches; power starts off. Reports has three rows and two
dividers. `render_connect.py` renders five states in EN and VI to `shots/connect`.
