# Click-through QA: Lifetime Server and the Lifetime desktop app

Date: 2026-11-15. Code: `latest-main` after the dashboard redesign (ffe148c, 7a3fee4, ea91bac, a6ca7a0).

## How it was run

An automated explorer (a scratch script, not part of the repository) opened each app with demo data and clicked
through it:

- **Lifetime Server:** `ServerRuntime` in a temporary directory on 127.0.0.1 (port 0), `render_server.populate(count=12,
  detailed=True)` plus synthetic presence history, four incidents with timelines and three network checks;
  `lifetime_permissions.windows_acl` patched; `ServerWindow` on the real Windows platform plugin at 1600x1000 and 1024x700.
- **Desktop:** `Window({'completed': True, ...every category on..., 'language': ...})` with `LIFETIME_DATA_DIR`, `APPDATA` and
  `PROGRAMDATA` pointed at temporary directories and 400 days of synthetic ledger history.
- **Nothing reached the machine:** external programs were gated (read-only probes only; every other program was refused
  and recorded), non-loopback network and DNS were refused, registry writes, `QProcess`, shell opens, `os.startfile`,
  `run_agent`, autostart and Connect agent calls were recorded instead of run, open-file dialogs answered Cancel and
  save dialogs a temporary path. Every message box was answered Cancel/No and every other dialog was rejected.
- **On every page and tab:** each visible, enabled button, switch, segmented control, combo box item, tab, table row
  (first three and last), link and pop-up menu was clicked or selected, events processed, dialogs answered, and the
  page restored. Recorded: Python exceptions (`sys.excepthook`, `threading.excepthook`), Qt warnings, critical and fatal
  messages, actions over 100 ms on the GUI thread, clicks that changed nothing visible (page picture, page and dialog
  unchanged), clipped text (font metrics wider than the widget), controls outside the window or cut by their scroll
  area, overlapping sibling controls, empty tables without an empty-state message, untranslated text (in Vietnamese:
  English catalogue strings or English words; in English: Vietnamese letters), and a screenshot per page, per scroll
  position (top, middle, bottom) and per dialog.
- **Combinations:** Glass-dark English and Classic-light Vietnamese fully (every control); the other six style/theme/
  language combinations page by page (screenshots and layout and language checks, no clicks); both window sizes.
- Then the screenshots were read by hand, which found most of the layout and redesign items below.

Screenshots are in the session scratchpad (`qa/out/before/...` and `qa/out/after/...`); they are not committed
because the desktop System page shows the hardware of the machine the explorer ran on.

## Totals

| App, combination | Controls exercised (before / after) | Pages | Dialogs answered | Side effects blocked |
|---|---|---|---|---|
| Server, Glass dark, English (full) | 119 / 117: Overview 10/9, Devices 31/31, Incidents 2/2, Administration 72/71, This Server 0/0 (no controls), header 4/4 | 5 pages x 2 sizes x 3 scroll positions | 14 / 14 | 0 / 0 |
| Server, Classic light, Vietnamese (full) | 119 / 117 (same pages) | same | 14 / 14 | 0 / 0 |
| Server, the other six combinations (pages) | - | same | - | - |
| Desktop, Glass dark, English (full) | 43 / 43: Overview 4, System 14, Activity 12, Reports 1, Health 1, Connect 0 (code field only; Connect is disabled until a code is typed), Settings 11 (language and style segments are covered by the combinations instead) | 7 pages x 2 sizes x 3 scroll positions | 2 / 2 | 7 / 7 (event-log probes, autostart, the history import, all refused) |
| Desktop, Classic light, Vietnamese (full) | 43 / 43 (same pages) | same | 2 / 2 | 7 / 7 |
| Desktop, the other six combinations (pages) | - | same | - | 2 each (start-up event-log probes) |

The two fewer server controls after the fixes are the Recent events and Server log tables, which are now event rails
(nothing to click).

Findings by severity (unique, all combinations): explorer before: 13 (layout 3, translation 6, UX 4), of which 3 real (#6, #10 in both languages) and 10 false positives;
explorer after: 10, all false positives (listed below). With the findings from reading the screenshots and from the test
runs, 12 real findings: crash 4, bug 1, layout 3, translation 0, UX 4. Fixed 11, left 1 (#12, a redesign item).

## Findings

Severity: crash, bug, layout, translation, UX. "Fixed" commits are local on `latest-main`.

| # | Severity | Where | Finding | Steps to reproduce | Screenshot (before) | Status |
|---|---|---|---|---|---|---|
| 1 | crash | test_dashboard | The test process aborted (0xC0000409) at `test_reduced_motion_cards_keep_full_contrast`, also before the redesign. Windows from earlier tests were only closed, so Python's cycle collector destroyed them at a random allocation, sometimes inside one of their own background-task slots (the Connect page's 2 s status poll, the Health load); that slot then ran on deleted widgets, the `RuntimeError` reached Qt and PyQt called `qFatal`. | `python -m unittest test_dashboard` (5 of 6 runs aborted) | - | Fixed (4afcd27): tests delete their window with `shut_down()`; the Connect poll now stops when the window closes. |
| 2 | crash | test_server_fleet | Access violation at interpreter exit: 13 closed but never deleted `ServerWindow`s were torn down during Python finalisation in random order. Not app code: the app deletes its window with `shut_down()` before exit. | `python -m unittest test_server_fleet`, exit code 0xC0000005 | - | Fixed (4afcd27): the tests delete each window. |
| 3 | crash | both apps (`lifetime_reveal.later`) | A delayed callback ran on a deleted widget: `RollLabel.roll_in` starts its animation 160 ms later; if the label was deleted first (window closed, device list rebuilt), the `RuntimeError` aborted the process. Found once the test windows were deleted. | Delete a `RollLabel` within 160 ms of `roll_in()` | - | Fixed (4afcd27): `later()` ignores "has been deleted"; regression test in test_reveal. |
| 4 | crash | both apps (`lifetime_tasks`) | A background task finishing after its owner was deleted (or during exit) emitted on a deleted signal object in a pool thread: abort at exit (0xC0000409). | Close the window while a Health load or status read runs | - | Fixed (4afcd27): the result is dropped; `shut_down()` waits up to 3 s for running tasks; regression test in test_tasks. |
| 5 | bug | Server, Overview > Open device | After **Review pending** (status filter "Pending approval"), selecting an approved computer on the Overview and pressing **Open device** went to Devices with the computer's ID in the search box and "No computers match": the status filter still hid it. | Overview > Review pending; back to Overview; select a connected computer; Open device | server-glass-dark-en/1600x1000-01-Devices-0.png (trial run) | Fixed: Open device clears the status, group and chip filters before narrowing to the computer; regression test. |
| 6 | layout | Server header, Vietnamese, 1600 px | The subtitle under "Lifetime Server" was cut mid-word ("Demo-Server · Mạng nộ"): it only gets the width the brand column has left beside the page menu. | VI, 1600x1000, any page | server-classic-light-vi/1600x1000-00-T-ng-quan-0.png | Fixed: it ends in "…" and the whole text is the tooltip; regression test. |
| 7 | layout | Server, Administration > Pending approvals | The table was 140 px tall with 56 px rows, so the second request was cut in half behind a scroll bar. | Two computers waiting for approval; Administration | server-glass-dark-en/1600x1000-03-Administration-0.png | Fixed: tall enough for up to four requests, then it scrolls (the Invitations table, which cut its third row, the same); regression test. |
| 8 | layout | Desktop Overview tiles, Vietnamese, 1024 px | "Hoạt động trung bình / ngày" wrapped to three lines in a fixed 104 px tile; the third line was cut off. | VI, 1024x700, Overview | desktop-classic-light-vi/1024x700-00-T-ng-quan-1.png | Fixed: a tile grows when its heading needs a third line; regression test. |
| 9 | UX | Server, Administration > Maintenance windows | Empty table with column headings and a blank area, no message. | Administration with no maintenance windows | server-glass-dark-en/1600x1000-03-Administration-1.png | Fixed: "No maintenance windows. Alerts open as usual." |
| 10 | UX | Server, Administration > Notifications > Delivery log | Same: blank table when nothing was sent. | Administration, scroll to Notifications | server-glass-dark-en/1600x1000-03-Administration-2.png | Fixed: "No alert messages sent yet." |
| 11 | UX | Server, Administration > Roles and access | Same: a 180 px blank table when no account has a role. | Administration (top) | server-glass-dark-en/1600x1000-03-Administration-0.png | Fixed: "No other account or group has a role yet." |
| 12 | UX | Server, Devices, nothing selected | The detail pane shows the remote power buttons and four spec cards full of "Not available" before any computer is chosen. | Devices, clear the selection | server-glass-dark-en/1600x1000-01-Devices-0.png | Left: redesign list item R4. |
| - | (false positive) | Desktop System, Vietnamese | Network adapter and device names (as reported by Windows) flagged as English. | - | - | Not a bug: hardware names are not translated. |
| - | (false positive) | Desktop Overview tile values, Vietnamese 1024 px | "Chưa có dữ liệu" and a date measured wider than their label; on screen they are whole. | - | - | Not a bug. |
| - | (false positive) | Server, Administration > Verify backup | No visible change: the explorer answers the file dialog with Cancel. | - | - | Not a bug. |

No Python exception, Qt critical/fatal message, overlapping control, control outside the window or action over
100 ms on the GUI thread was recorded in any combination, before or after. Every dialog opened by the click-through
(Add a computer, Export report, the confirmation boxes, Delete collected history) was translated in Vietnamese.

## Redesign built in this pass (existing widgets only)

- **Event rail** (`EventRail`): the Server log under Administration (newest first, five rows then it scrolls; green for
  starts, renewals and restores, amber for refused reports and connections, red for failed renewals and restores) and
  the Overview's Recent events (the latest five: the event in words, then the time and the computer).
- **Heartbeat ticks** (`HeartbeatBar`) on each device card in the Devices list: the last 24 hours, one tick an hour,
  the same as the device detail. Presence is loaded in the list's worker thread together with the page of
  computers (only the listed, connected ones, at most once a minute each), never on the GUI thread; the fake admin
  client in test_server_gui fails any call made on the GUI thread.
- **Capacity bar** (`CapacityBar` + `StatusChip`) on the desktop Reports page: where the saved time comes from
  (measured, OS logs, manual baseline) as one bar, with a chip per part so colour is never the only key.
- **Info card** (`InfoCard`) for the desktop Connect page's connection details: the server's name as the title, then
  Server address, Last report and Shared with the server; the "older server" note sits under the rows. `InfoCard.text()`
  now returns the card as "Key: value" lines.

## Redesign still needed

Screens that still read as plain text or look unlike the redesigned ones, with a proposal each. R1-R6 only reuse
existing widgets; R7-R9 need new data or new screens and were not built.

| # | Screen | Now | Proposal |
|---|---|---|---|
| R1 | Server, This Server | Four panels of label/value text rows (Server, Hardware, Right now, Connected computers); the certificate's names are appended under the expiry date. | `InfoCard`s with icons (os, cpu, memory, network); Right now: memory used as a `CapacityBar` and CPU as a `MiniSpark`; Connected computers as a `SummaryStrip` (connected / offline / pending, clickable to the filtered Devices list); certificate names as their own row. |
| R2 | Server, Administration > Latest cumulative snapshots | "Measured 3,665.00 / Imported 0.00 / Baseline 0.00" as text. | The same source bar as the desktop Reports page (`CapacityBar` + three chips), with the snapshot coverage sentence under it. |
| R3 | Server, Overview > Device details | Rich-text label/value table for the selected computer. | `InfoCard` (asset tag, last seen, version, coverage, tracking since) and a `HealthBadge` row for crashes, shutdowns and battery health, as in the device detail. |
| R4 | Server, Devices detail with nothing selected | Power buttons and spec cards full of "Not available". | One `EmptyState` ("Select a computer to see its details") and nothing else until a computer is selected. |
| R5 | Server, Devices list cards | CPU and RAM meters are yellow `QProgressBar`s styled in a stylesheet. | Thin `CapacityBar`s with `capacity_kind` colours (amber under 15 % free, red under 5 %), matching the spec cards. |
| R6 | Server, Incidents with nothing selected; desktop Coverage details dialog | A one-line hint in an empty pane; the coverage dialog is a `QTextBrowser` of "Key: value" lines and gap dates. | `EmptyState` for the pane; for Coverage, an `InfoCard` of the evidence and an `EventRail` of the recorded gaps. |
| R7 | Server, Network checks | Each check shows only its latest result. | **Needs a schema change** (a per-check result history table with retention), then a `HeartbeatBar` per check (last N results, worst-in-bucket) and the latency as a `MiniSpark`. Not built. |
| R8 | Server, Availability | Availability exists only in reports (PDF/CSV) and per-computer schedules. | **A new screen**: per computer or group, a 30/90-day heat strip of availability against expected hours, the coverage floor marked, and a list of the worst. Not built. |
| R9 | Desktop Connect and server Add device | Connect is a code field and a status sentence per step; Add device is one sheet with the code. | **A stepper** (research P12): Get a code > Enter it > Approve > Details sent, with the current step ringed and the failure on its own step, shared by both apps. Not built. |

## Tab switching

`measure_tabs.py` (scratchpad) re-run after the changes, Glass, 1600x1000, both rounds of every page:

| Glass, 1600x1000 | Mean click-to-first-frame | Worst | Frames in 520 ms | Slow frames (> 25 ms) |
|---|---|---|---|---|
| Server, after the redesign | 16.2 ms | 34.0 ms (Administration) | 32-33 | 0 |
| Server, after this pass | 18.8 ms | 40.6 ms (Administration) | 30-33 | 11 in 10 switches |
| Desktop, after the redesign | 15.5 ms | 30.4 ms | 29-33 | 0 |
| Desktop, after this pass | 15.6 ms | 33.9 ms | 29-33 | 17 in 14 switches |

Still smooth (frame rate unchanged), but the slow frames also appear in the idle baseline this time (one per 520 ms
with no transition at all), so the machine was busier than during the earlier measurement; Administration's first
frame is 6.6 ms slower and is worth a re-measure on a quiet machine.

## Tests run (one module at a time)

After the crash fixes (4afcd27): test_dashboard (5 runs, exit 0 each), test_server_fleet (3 runs, exit 0), test_tasks,
test_reveal, test_gui_startup. After the QA fixes: test_dashboard (15 OK), test_company_gui (16 OK),
test_widgets_dashboard (18 OK), test_server_gui (65 OK), test_server_fleet (13 OK), test_switches (19 OK),
test_i18n (8 OK), test_redesign (27 OK), test_server_access (20 OK), test_gui_startup (1 OK), test_theme_modes (8 OK).
Not run: the full suite (memory rule). Not verified on a second machine or with real computers connected.

## New features: energy and cost, after hours, replacement plan, health score

Date: 2026-11-15. Code: `latest-main` after 9db3633, b2b92c5, c56b1be, aee5368, 697198f, c4f0fe7.

**Scope:** only the new screens. Server: the Energy page (Energy and cost, After hours with the Sleep after hours
control, Replacement plan, Health score, and the settings forms for price and currency, typical laptop/desktop power,
power per group or computer, battery/disk replacement levels and unit prices, office hours). Desktop: Reports (Energy and
cost panel with the after-hours sentence and its price/power form) and Health (badge and Replacement forecast panel).
Same explorer as above, limited to those pages, with `render_server.populate_monitoring` (disk and battery trends) on the
server and 60 days of hourly ledger buckets, on-battery readings, a disk trend and a battery history on the desktop.
Every control clicked in Glass-dark English and Classic-light Vietnamese; screenshots at every scroll step (not only
top/middle/bottom) at 1600x1000 and 1024x700, read by hand; the server also in Glass-light English. The server now
opens in light: the explorer writes the theme preference (`write_theme_preference`) and calls `set_theme` before
`ServerWindow` is built, as `lifetime_server_gui.main` does (the earlier pass only wrote the style).

**Automatic checks:** 25 server controls x 2 combinations and the desktop pages: no exceptions, no Qt warnings, no action
over 100 ms, no clipped text, no untranslated strings, no empty table without a message.

### Found and fixed

| Severity | Where | Finding | Fix |
|---|---|---|---|
| Wrong label | Server, computer actions (VI) | The power button and menu item Sleep read **Giấc ngủ** (the Health page's noun) instead of **Ngủ**: the server's `text()` takes the shared catalogue first, where `'Sleep'` is the Health noun. | Own key `power.sleep` (EN Sleep, VI Ngủ) for the button, the menu and the confirmation. Test: `test_i18n.CatalogueTests.test_server_sleep_action_is_the_verb_not_the_health_noun`. |
| Misleading number | Server and desktop after-hours chart | Weeks before a computer's first hourly record were drawn as 0 h, so a new install looked like eight weeks with nothing on after hours. | `lifetime_insights.weekly` leaves out weeks before the first recorded day (unknown, not zero). Test: `test_insights` (after-hours week series). |
| Misleading number | Server health table | A part was printed rounded (79.6 % as "80%") but classed on the raw value, so it read "Needs a look · 80%" against an "80 % or more is good" rule. | Battery, disk and temperature are shown rounded down, like the thresholds. Test: `test_server_gui.test_health_part_values_round_down_like_their_thresholds`. |
| Raw number | Replacement plan "When" | "now (79.3333 %, limit 80 %)". | Whole percent, as the Why column already did. Test: `test_insights.test_reached_text_shows_a_whole_percent`. |
| Wrong translation | Replacement plan "Why" (VI) | "Độ chai pin hiện 79 %" says the battery is 79 % worn; the number is its health. | "Sức khỏe pin hiện 79 %…". |
| Layout | Server settings at 1024 px | The lifecycle and hours rows wrapped between a label and its field ("Giá pin" at the end of one line, its box on the next). | Each label and its field wrap together. |

### Seen and left

- Disk health in the plan shows the status Windows reports, untranslated ("ổ đĩa báo Warning"). It is the device's own
  word, like a model name; translating Warning/Unhealthy would need the value translated in three places.
- Money uses `1,234 VND` (English grouping) in Vietnamese, and the server price box shows `2500,00` while the desktop
  shows `2500.00` (Qt locale). Cosmetic.
- The after-hours chart's first week can be partial (history starts mid-week) and then looks low.
- The kWh headlines carry no "est." mark themselves; the line under them says how many computers are measured and the
  table's power column says "measured on battery" or "estimated: typical …". Even "measured" applies an on-battery draw
  to plugged-in time, which the note states.
- Server header at 1600 px logical with the alert badge in Vietnamese: the tab strip scrolls ("Quản tr…"). Not new.
- Once, a desktop Reports capture at 1600 px (Vietnamese) caught the Energy panel mid-layout (squashed); it did not repeat
  in two later runs.
- Battery and disk unit prices have no currency suffix in the form.

### Tests run (one module at a time)

test_i18n (9 OK), test_insights (26 OK), test_server_insights (5 OK), test_server_reports (8 OK), test_server_gui (70 OK).
Not verified with real computers (real on-battery readings, real hourly ledgers or a real Sleep after hours night).
