# Upgrade plan: uptime from any date, downtime, a better interface, and no more setup errors

Written 23 November 2026. Status: **proposal**. Nothing in it is built yet; it is the order to build in once approved.
Versions: **1.2.2** (setup errors), **1.3.0** (uptime and downtime), **1.4.0** (interface).

## 1. What people asked for

From the team chat, 22–23 November:

- **Phú:** "Lifetime should show uptime from a chosen day in the past, ideally going back about 5 years."
- **Phú:** "There is only uptime for one month, and no downtime yet."
- **Khải:** "On the server, the Linux AppImage opens the desktop (client) app; the server window needs a command."
- **Team:** keep Python, because it is easy to customise and friendly for programmers.
- **Team:** a full list of what the app can do.
- **Owner:** improve the interface, learn from competitors, and stop employees running into errors.

## 2. What exists today (checked in the code)

| Area | Today | Where |
|---|---|---|
| Desktop date ranges | 30 days, 90 days, 1 year, All. No chosen dates. | `lifetime_charts.py` `RANGES` |
| Server, one computer | "Awake time per day" offers only **14 or 30 days**. This is the "one month" Phú saw. | `lifetime_server_gui.py` `daily_range` |
| Data the server has | Computers already send up to **3,660 days** (10 years) of daily awake totals, and the server keeps them. Per-day *detail* is capped at 400 days. | `lifetime_protocol.py` (3660, `MAX_DETAIL_DAYS = 400`) |
| Downtime, desktop | None. The app shows awake time only. | — |
| Downtime, server | Online/offline intervals (`presence`) and availability inside expected hours exist, but only in reports. There is no per-computer timeline. | `lifetime_monitor.py`, `lifetime_availability.py` |
| History before install | Windows System log (EventLog 6005/6006/6009/6013, Kernel-Power 42/107, Power-Troubleshooter); Linux `journalctl --list-boots` and `last -x`. Health reads 41 and 6008. **Not read:** event 1074 (who restarted) and archived logs. | `lifetime_import*.py`, `lifetime_health.py` |
| Server retention | An administrator can delete data before a date. Nothing is trimmed automatically. | `retained_before` in `lifetime_server_store.py` |

So "uptime from any date" on the server is mostly **interface work**: the data is already there.

## 3. The honest limit on "5 years back"

Measured on the development PC (Windows 11, default settings):

- The Windows System log is capped at **20 MB** and reaches back to **3 July 2026** (about 4.5 months).
- Reliability Monitor history reaches back to **24 October 2026** (one month).

Other PCs will differ, but Windows only keeps what fits in its logs. So:

- The date picker allows **any date**, 5 years back and more.
- Days with no evidence show as **Unknown** (grey), **never as zero**.
- The screen always says where the data starts and why, for example: *"Data from 3 Jul 2026 (Windows log) · recorded by Lifetime since 6 Nov 2026"*.
- **From now on Lifetime keeps everything**, so 5 years of real history builds up from the day it is installed. On a server, that needs a retention design (section 6, 1.3.0).
- More past data where it exists: read **archived** System logs (`Archive-System-*.evtx`, kept when IT sets the log to "archive when full") and rotated Linux `wtmp` files. IT can raise the System log size by group policy; Lifetime documents that but never changes it silently.

## 4. Definitions (decided, so every screen means the same thing)

- **Uptime** = awake time, as today.
- **Off time** is split into: **Asleep**, **Shut down** (clean), **Restarted**, **Unexpected stop** (Kernel-Power 41 / EventLog 6008: crash or power loss), and **Unknown** (no evidence).
- **Downtime (for IT)** = time a computer was *expected* online (its schedule) but was not reachable. This is the existing availability model (desktops expected always, laptops never, unless scheduled). No second model is invented.
- New numbers for a period: awake share of known time, number of restarts, number of unexpected stops, average time between unexpected stops, and the longest outage.

## 5. Phase 0: version 1.2.2, "no more setup errors" (about 1 week)

1. **Linux server window from the menu.** Finish the uncommitted work from 20 November: installing the server adds a **Lifetime Server** entry to the app menu, the dashboard finds its own settings (no `--directory` or `--admin-port` to type), and the installing user is made an administrator. When the server is installed on the same machine, the AppImage offers the server window first.
2. **"Diagnose connection" button** (computer side). One check per line, in plain words: name lookup, port 8443, discovery on 5353, whether the server's certificate covers the address, clock difference, Windows network profile (Public blocks the server). Ends with **Copy report**.
3. **Server "Self check"** page. Which addresses it listens on, firewall rule and network profile, certificate names (including its IP), discovery running, clock.
4. **Copy diagnostics** in both apps: versions, settings and recent log lines, **without codes, tokens or passwords**, so an error report arrives with its cause.
5. **Windows desktop uninstaller**, listed in Windows' "Installed apps".
6. **Version information in every Windows program** (product name "Lifetime", version). Needed for code signing.
7. **"Code signing policy" section** in the README and release notes; then submit the SignPath Foundation application. Signing ends the Smart App Control blocks.
8. **Tests that would have caught the 1.2.1 bugs:** a Linux client and a Linux server in two containers pairing **without** `--server` (discovery and the network search), with `127.0.1.1 <hostname>` in `/etc/hosts` as on Ubuntu; plus a test that the Windows Server installer works with no Python on the PATH.
9. **Release candidates.** Every version is first tagged `-rc1` and given to the tester for 3 days with a checklist (section 9) before the final tag.

## 6. Phase 1: version 1.3.0, "uptime from any date, and downtime" (2–3 weeks)

### Desktop app

- **Date range picker** on Activity, Reports and the new Uptime page: quick choices (Today, 7 days, 30 days, This month, Last month, This year, 1 year, 5 years, All) plus **From / To** calendars with no lower limit. Every chart, total and report follows it.
- **New "Uptime & downtime" page:**
  - a timeline with one bar per day, coloured Awake / Asleep / Off / Unexpected stop / Unknown;
  - totals for the range, the counts from section 4, and the data coverage line;
  - an event list: started, shut down, restarted (with who or what asked, from event 1074), slept, woke (with the wake reason), unexpected stop.
- **Deeper import:** event 1074, archived System logs, rotated `wtmp` files.
- **Ledger:** new per-day fields for off-time and stop counts, added so older versions still read the file. 5 years is about 1,900 days, which is small.

### Server

- The same range picker on a computer's detail, the Overview fleet totals, Energy and reports, **replacing the 14/30-day box**.
- A per-computer **downtime timeline** from `presence` plus what the computer reports about sleep and off time, and availability in expected hours.
- **Fleet uptime bars:** one row per computer, one small bar per day, coloured by availability (as status pages do).
- **Reports for any period**, with "compare with the previous period".
- **Protocol:** computers send the off-time breakdown only to servers that advertise it (the existing capability mechanism), so old and new versions keep working together.
- **Retention:** daily totals kept for ever by default; raw presence and readings trimmed after a setting (default 400 days), respecting the existing "delete before" date. This is the same idea as Zabbix's history and trends.

## 7. Phase 2: version 1.4.0, "interface" (about 2 weeks)

- **One shared range picker** used everywhere, so it looks and behaves the same in both apps.
- **Drag across a chart to zoom** into those dates; double-click to go back.
- **Compare with the previous period** shown as a faint line behind the current one.
- **Overview, rethought:** what changed since yesterday, which computers are down now, the longest-running ones, open problems first.
- **Vietnamese-first review** with Phú and Khải: short labels, nothing cut off at 1024 px, the same word for the same thing everywhere.
- **Speed at size:** load only the chosen range; measure 500 simulated computers with 5 years of daily data each (today's measured size is 50).
- Accessibility: keyboard use and screen-reader names for every new control.

## 8. Ideas taken from competitors

| Tool | What it does well | What Lifetime takes from it | Source |
|---|---|---|---|
| NirSoft TurnedOnTimesView | An on/off list from the Windows event log: events 41, 42, Power-Troubleshooter 1 (resume), 1074 (who restarted), 6005/6006; can read archived logs and other disks' logs | The Uptime & downtime event list; reading 1074 and archived logs | read on nirsoft.net, 23 Nov 2026 |
| Zabbix | Keeps raw "history" briefly and hourly "trends" (min, avg, max, count) for years; graphs use trends for old periods | Raw data trimmed, daily rollups kept for years | read on zabbix.com, 23 Nov 2026 |
| Uptime Kuma | Status pages, 20-second checks, 90+ notification channels | Lifetime already has checks, email and webhooks; a read-only status page is a later idea | read on github.com, 23 Nov 2026 |
| Grafana | Time picker with quick choices, exact dates and drag-to-zoom | The shared range picker and zoom | from knowledge |
| Status-page tools | A row of small daily bars coloured by uptime | Fleet uptime bars | from knowledge |
| Microsoft Intune Endpoint Analytics | Restart frequency and boot performance per device | Restarts per week and unexpected stops in Health | from knowledge |

## 9. How errors stop reaching employees

- **Before every release:** all automatic tests, the new pairing tests, then a release candidate with this checklist on real machines:
  1. Install on Windows 11 with Smart App Control on, following only the README.
  2. Install the server on Windows Server (no Python) and on Ubuntu Server.
  3. Connect a Windows and a Linux computer to each server **by typing the code only**.
  4. Open the server window from the menu; approve; see the computer's data.
  5. Make a report for a chosen month.
  6. Uninstall everything.
- **In the apps:** Diagnose connection, Self check and Copy diagnostics, so a problem says its cause instead of "No server found".
- **Code signing** removes the Smart App Control class of problems.
- **Known issues** listed in every release's notes.

## 10. Python stays

Lifetime stays a Python program. The packaged programs are for employees; the source route (`python lifetime_gui.py`) keeps working. A new `docs/CUSTOMISING.md` will explain the module map, how to change a page, how to run the tests and how to build the packages.

### Rust: only where it is measured to help

The owner allows Rust for parts where it clearly helps, as long as most of Lifetime stays Python. Measured on the
development PC (23 November 2026), the two obvious candidates do not need it:

| Candidate | Measured | Decision |
|---|---|---|
| The always-running tracker | 6.5 MB of memory (14 MB private) | stays Python |
| Reading past power events from the Windows log | 502 events covering 4.5 months in 0.2 s (filtered); the whole log, 40,412 events, in 12.3 s | stays Python; filter by event ID, also for archived logs |

The one place to watch is the **server at fleet size**: availability and daily rollups for 500 computers × 5 years.
`lifetime_availability._intersect` is O(n×m) today. The 1.4.0 speed test decides; the first step is a faster
algorithm in Python (one sweep, O(n+m)), and Rust only if that is still too slow.

Rules for any Rust part:

1. **A measurement first**: Python must be shown to be too slow or too heavy, after trying a better algorithm.
2. **Small and separate**: one Python extension module (PyO3, built with maturin) behind a plain Python function, with
   the same tests run against both versions.
3. **A pure-Python fallback is always kept**, so `python lifetime_gui.py` still runs without a Rust compiler and the
   team can read and change the logic in Python.
4. **Only after releases are code-signed.** A new, unsigned native file (`.pyd`, `.dll`, `.exe`) is blocked by Smart App
   Control, which would break the Python route that today gets around it.
5. **CI builds it for every target** (Windows x64 and ARM64; Linux x86_64 and aarch64 on glibc 2.28) or it is not used.

## 11. Feature list

A full feature list in Vietnamese and English (`docs/FEATURES.md`), built from the README and the beginner's guide, updated with every release.

## 12. Order and size

| Version | Contents | Rough size |
|---|---|---|
| 1.2.2 | Section 5 | about 1 week, plus 3 days with the tester |
| 1.3.0 | Section 6, with `docs/FEATURES.md` and `docs/CUSTOMISING.md` | 2–3 weeks, plus 3 days with the tester |
| 1.4.0 | Section 7 | about 2 weeks, plus 3 days with the tester |

Sizes are estimates for planning, not promises.

## 13. Not planned, and why

- **Inventing history where no logs exist.** Unknown stays Unknown.
- **Changing Windows log sizes automatically.** It changes system settings, so it stays an IT choice, documented.
- **A cloud service.** Lifetime stays self-hosted; nothing goes to the developers.
