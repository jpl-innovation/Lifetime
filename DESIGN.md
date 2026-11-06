---
name: Lifetime
description: Native desktop awake-time tracker and local system dashboard.
colors:
  bg: "#0B1426"
  card: "#12203A"
  text: "#FFFFFF"
  muted: "#9FB0CC"
  border: "#22345A"
  accent: "#FFC93C"
  accent-hover: "#FFD761"
  track: "#1B2A46"
typography:
  body:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "15px"
  label:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "13px"
  title:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "23px"
    fontWeight: 600
  group-title:
    fontFamily: "Segoe UI, DejaVu Sans"
    fontSize: "18px"
    fontWeight: 600
rounded:
  signal-card: "6px"
  button: "8px"
  segment: "5px"
spacing:
  compact: "8px"
  grid: "16px"
  window: "24px"
components:
  button:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.button}"
    padding: "8px 12px"
  start-tracker:
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
---

# Design System: Lifetime

## Overview

Lifetime is a native desktop utility with an established navy, yellow, and white identity. Preserve that identity when extending the interface. This document records the implemented system in `lifetime_gui.py` and `lifetime_dashboard.py`; `THEMES['dark']` remains the palette source of truth. The retained legacy light-theme dictionary is not selected by the current window.

The interface is adult, professional, and direct. A prominent lifetime total leads Overview, while labeled local readings and clear unavailable states make System useful without implying more certainty than the OS provides.

**Key Characteristics:**

- Readable type with strong numeric hierarchy.
- Restrained yellow accents on navy surfaces.
- Plain tab navigation and aligned data rows.
- Native Qt widgets and painted symbols, without external font or image dependencies for dashboard content.

## Colors

### Primary

The warm yellow `accent` identifies the primary tracker action, active status, history marks, and percentage meters. `accent-hover` brightens the primary action on hover or focus.

### Neutral

`bg` is the deep navy window canvas; `card` creates a slightly lighter reading surface. `text` is white for primary values and headings. `muted` softens labels, units, captions, and unavailable readings. `border` defines controls and System card edges; `track` supports meters, pressed controls, and selected navigation.

Preserve these existing roles. A new dashboard section does not authorize a palette redesign.

## Typography

Use Segoe UI on Windows and DejaVu Sans on Linux. Qt selects the platform-specific family explicitly; the frontmatter lists both for portability.

Body and System values use the body role. Labels, captions, status, and the footer use the smaller label role. Brand and page titles use the title role; System group headings use the group-title role. Overview statistic values are medium weight (25px). Its lifetime total uses a light weight at a large size (60px, or 52px in compact height), shrinking further only when necessary to fit the number.

Right-align System values and request tabular numerals where the Qt font API supports them. Keep units muted and adjacent to their values. Long device names wrap inside the value column.

## Layout

The content sits in a vertically scrolling canvas with a maximum body width of 1400px. Default window margins use the window spacing token; compact windows reduce them to 16px. The brand and tracker status precede plain Overview, System, and Insights navigation. A separate credit footer remains below the scroll area.

System places Power and Network in its first row, Graphics and Computer in its second. At window widths below 700px, the cards form a single column in that order. Columns share available width, and cards in a row share the row height. The grid uses the grid spacing token. System cards have distinct heading and data areas; their label and value columns stretch in a 2:3 ratio.

Overview adjusts its statistic grid and total height for smaller windows. System and Insights retain vertical scrolling where their content exceeds the viewport. Do not force every telemetry row into a fixed-height window by shrinking text.

## Elevation & Depth

System cards and controls use flat tonal separation and thin borders. Depth comes from the navy canvas, lighter surfaces, and clear grouping rather than drop shadows. Existing Overview statistics and painted charts retain their native rendering.

## Shapes

System cards use gently rounded corners; buttons and the baseline input use the button radius. Navigation segments use the smaller segment radius. Thin horizontal progress meters have a small rounded cap. Icons are simple QPainter line symbols with rounded joins.

## Components

### Buttons and input

Standard buttons use a navy surface and thin border, with a yellow border on hover and focus. The tracker start action uses yellow fill and navy text. Pressed buttons use the track surface. The baseline input matches standard control shape and supports keyboard editing. The import toggle is a quiet text action with a focus underline.

Verify disables itself during a background request and exposes a busy state. Completion restores the action and shows the comparison or an explicit unavailable reason. The action is read-only.

### Navigation

Overview, System, and Insights use plain text segments. The selected segment has a track-colored fill and white text; unselected text is muted. The same navigation stays visible across pages.

### System cards

Six small System charts precede the detailed cards: CPU, RAM, battery watts, GPU watts, download, and upload. Each has one axis and one series, a five-minute in-memory ring buffer, hover crosshairs, and a Table toggle. Initial empty time stays empty; unavailable sensors break the series. Revoking a category clears its samples and table.

Each detailed card has a group title followed by left-aligned field labels and right-aligned values. Percentage meters appear only for available percentage readings. Thin yellow meters supplement the displayed value; they never stand in for missing data. Unavailable values use the same row structure and a muted textual state.

### Total, charts, and motion

Overview gives the lifetime total the strongest type hierarchy. Insights uses native QPainter charts with one shared 30-day, 90-day, calendar-year, or All filter. Six titled cards cover the calendar, daily hours with a white 7-day average, cumulative lifetime hours, local hour profile, weekday averages, and session lengths. The wide grid uses six equal cards in two columns; compact views use one column. Longer calendars scroll horizontally inside their card, preserving legible day cells. Cards retain a consistent height, sparse muted axes, thin marks, generous hover lanes, and a keyboard-accessible Table toggle. Missing dates remain unknown, and recorded zeroes remain zero. The five-step calendar scale increases in lightness toward brand yellow and saturates at 24 hours, preserving larger boot credits in tooltips and tables. Cumulative totals retain the opening balance and mark the original live tracking boundary. Short-range hourly profiles use retained imported awake intervals and explicitly describe partial coverage; undated all-time hourly totals are never presented as a complete filtered profile. Existing entrance and numeric transitions are brief; `LIFETIME_REDUCE_MOTION=1` disables motion. Busy feedback must remain understandable without animation.

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
