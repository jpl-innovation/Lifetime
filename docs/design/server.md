# Lifetime Server surface

Mode: Operate. The audience is the administrator reviewing fleet reports and
approving computers on a Windows Server console. The first question is which
devices need attention; the primary action is Add device.

## Dashboard pass

Reading this as: a fleet console for one administrator, in the calm native
instrument language of the desktop edition, ENERGY 2 / RHYTHM 2 / MOTION 2.
The owner supplied a dashboard reference as inspiration for structure only.

Why each major decision exists:
- Colour: the desktop's navy and white, with yellow only for what needs a look now.
- Layout: left column answers "what happened", right column "what is true now".
- Four equal tiles: these are peer readings, scanned as one row; the gauge is the focal point.
- Gauge: extends Lifetime's ring into a fleet reading, and is its identity motif here.
- Type: Segoe UI and DejaVu Sans, matching each platform and the desktop edition.
- Icons: the shell's own painted symbols, one family across both editions.
- Motion: placeholders fade so content does not pop in; the page fade marks the change of page.

Reasons the desktop read as more finished, and what replaced them: one structural
band in the sidebar colour (#08101F) holds brand, pill navigation, status and
Refresh, replacing a header, a floating bar and a footer. Language moved to
Administration. The status strip appears only for a message worth reading.
Overview is two columns: four icon tiles, the daily chart and the device list on
the left; the Online now gauge, selected computer and recent events on the right.
The server uses SOFT_RADIUS (16) for its larger surfaces. Only the newest chart bar
carries yellow. Offline and Pending turn yellow only when above zero.

Loading: Skeleton placeholders cover each surface until its first answer and fade
over 180 ms; the detail panel waits 120 ms so a fast answer never flashes. A 2 px
LoadingBar under the band appears after 200 ms of pending work. Refresh reads
Refreshing… for at least 450 ms. Reduced motion gives static placeholders and a
static bar, so the state is still readable without animation.

## P2a shell and Devices

The P2 user notes supersede the earlier sidebar and three-tab composition below.
The shell now uses a centered segmented navigation bar above a QStackedWidget:
Overview, Devices, Administration, This Server. Its interrupted highlight resumes
from the current geometry over 260 ms OutCubic. Native arrow navigation, Tab,
and Ctrl+1 through Ctrl+4 remain available. LIFETIME_REDUCE_MOTION snaps selection.
Server pulse, tray integration, create_application and show_fitted are retained.

Devices uses spaced rounded computer cards, searchable and filterable by group,
approval/report status, and All / Needs attention / Online / Offline chips.
Attention stays first across the complete filtered fleet before local pagination;
name, last seen and problem ordering apply within those groups. Table selection
is retained internally for UUID-based multi-selection/export and identity edits.
Cards and pagination buttons are reused on polls. Visible card readings load
through Tasks with a 32-computer cache and 30-second freshness limit. Scrolling
loads newly visible computers; it never fetches all 200 detail histories at once.
Resources revocation hides cached data immediately.

The detail pane scrolls independently beside the list from 900 logical pixels.
It contains native spec labels, yellow lifetime_charts.Plot history charts,
existing health/usage details, identity editing, enrollment and power controls.
Every power or refresh command requires a confirmation naming its computer.
The GUI checks consent, approval, recent sync and pending commands; the backend
remains authoritative. Wake additionally requires offline state and recorded MACs.
Consent failures have a visible explanation. Refresh uses send_command, and
Turn on uses wake. No backend or installer changes belong to this GUI task.

This Server shows server inventory, live readings, address, uptime, certificate,
database size and device counts from server_info. Unknown data stays unavailable.
User identity is not supplied by the current backend and is shown as unavailable.
All new copy is in the shared English/Vietnamese catalogue.

P2a captures use synthetic resources-enabled fixtures in shots/server-p2a with
real Windows Qt and Segoe UI, at 1024x700, 1280x800, 1600x1000 and 2560x1392.
Overview and Administration retain their existing presentation for the next job.

Verification ran only test_server_gui, test_server_fleet and test_i18n, twice.
The last run passed 43/44 checks and found selection restoration after sorting.
Restoration now uses row selection flags across hidden metadata columns. A later
real-store Windows rendering audit preserved selected UUID, scroll position and
unsaved identity text; its evidence is in capture.json. The suites were not run
again after that fix, respecting the user's two-run limit. New checks cover
200-computer reuse, bounded visible-reading requests, consent and command routes,
named confirmations, unknown/zero chart samples, pending commands, and navigation.

## Direction contract

THESIS: make the recorded fleet state and approval boundary visible together.
The supplied `.redesign-inspiration.png` governs the structure.

OWN-WORLD: Lifetime navy `#0B1426`, flat panels `#12203A`, white text, muted
`#9FB0CC`, restrained borders `#22345A` and yellow `#FFC93C`. Segoe UI on Windows.
The existing ring logo and JPL credit use supplied assets. No gradient or glow.

STORY: scan the four metrics, find a computer, inspect its recorded contribution,
and open its report. Enrollment remains visibly subject to approval.

FIRST VIEWPORT: brand/status/actions above three tabs; title/date/Add device;
four metrics; six-row searchable fleet table with details at right; daily bars
and recent events below. Below 1200 logical pixels, details and the lower panels
stack in the scrolling page. Typography stays readable at 1024 by 700.

FORM: native Qt widgets and a single painted bar chart, supplied reference as
composition authority. No random direction was needed. seed: none (unavailable).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish
review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Behavior and truth

The admin API supplies every figure; the window never opens SQLite. Filters and
sorts are applied before bounded pagination. UUID selection survives refresh.
The selected date and 7/30-day chart range share one fleet query. Known daily
contributions carry a contributor count; missing data remains unavailable.
Source stock totals are kept separately in Administration and the device report.
Coverage is partial or unknown, never inferred to be complete.

The tab underline uses one interruptible 180 ms OutCubic animation. Keyboard
navigation, Windows Animation effects and `LIFETIME_REDUCE_MOTION=1` can snap it
instantly. Controls retain native focus, selection, keyboard and scrolling.
The Add device sheet provides selectable code/line, separate working copy actions,
expiry and uses. Administration owns invitation revocation and pending decisions.

## Render review

`render_server.py` uses `QT_QPA_PLATFORM=windows`, real Segoe UI and an isolated
temporary SQLite/TLS fixture under the worktree. All names are synthetic Demo
names. The fixture is deleted afterward. No demo values are shipped in the UI.
The PNG inputs are the existing supplied assets; no generated artwork was added.

Captures are in `shots/server/`, with backend/font details in `capture.json`.
English and Vietnamese were inspected at 1600 by 1000 and 1024 by 700, plus
Devices, Administration and Add device. Review fixes included Vietnamese metric
widths, readable date labels, timestamp truncation, status translation, compact
stacking, and mismatched Location/Deployment date field labels.
