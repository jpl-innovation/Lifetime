# Final verification before 1.2.0

Date: 2026-11-16. Code: `latest-main` from d74ac32. Version strings were not changed (see part F for the list of
places that change at release time). Tests were run one module at a time on a Windows 11 PC (memory rule); the full
suite runs only on CI.

Each part says what was checked, how, the result, what was fixed and what could not be checked.

## A. CI

**Checked:** the GitHub Actions run for d74ac32 (43809251144), job by job, with `gh run view --json jobs` and the
job logs; the three runs before it; the run history of the branch (30 failures, 15 successes in the last 60 runs).

**Result for d74ac32:** all six test jobs failed, so no package, Linux build, smoke or container job ran.

| Job | Failure | Cause | Fix |
|---|---|---|---|
| test, Ubuntu 3.11 and 3.12 | `test_plain_text.ServerWindowTests`: `ProtocolError: Invalid text reading` | The test put the absolute path of `assets/lifetime.png` into computer names. On the runner (`/home/runner/work/Lifetime/Lifetime/...`) the text passed the protocol's 128 characters and the server (correctly) refused it. | 6c60a71: the path is relative to the working directory. |
| test, Windows 2022 and 2025, 3.11 and 3.12 | `test_windows_install_safety`: `Get-FileHash is not recognized` | `Get-FileHash` is a script function in Windows PowerShell 5.1. A 5.1 process whose parent was started from PowerShell 7 (the runner's shell, or a user's terminal) inherits PowerShell 7's module path and cannot load it. A real product weakness, not only a test problem: the Connect page's staging step used it. | 6c60a71: the step hashes with .NET (`SHA256.ComputeHash`). |
| test, Ubuntu 3.11 (run for a6fa1e6) | `test_slow_handshakes_count_towards_global_32...`: `31 != 32` | Test race: a request slot is taken a moment before the connection is recorded. | 39fdb32: the test waits for both. |
| test, Windows 2022 3.12 (runs for 191cb9e, 220ecd5, d07b30c) | `test_menu_pill_stretches...`: `5.0 not less than 5.0` | One fixed 90 ms wait. | Already fixed in a6fa1e6 (sampled frames); did not fail again. |
| linux-smoke Debian 10; Debian 12 and Rocky 9 `--installer` (run for 97f7e03, the last run that reached them) | `held broken packages`; `Lifetime Server did not start` after restart | Debian 10 security archive moved; restart could not bind during TIME_WAIT. | Already changed in 191cb9e. **Not yet confirmed by CI**: no run since has got past the test jobs. |

**For the lead:** push 39fdb32, 6c60a71 and the later commits of this pass, then check that the run reaches and
passes `windows-package`, `windows-server-package`, `linux-build` (x86_64, aarch64), all 13 `linux-smoke` jobs and
`linux-docker`. The smoke test gained a step (install, reinstall, uninstall the desktop bundle with
`install_linux.sh`), which has only been run here against a stand-in bundle under Git Bash.

**Package contents.** No build of the current code exists (the test jobs failed first), so the artifacts of the last
run that built packages were downloaded and opened (run 43752491079, commit `97f7e03`, 96 commits old):

| Package | Checked | Result |
|---|---|---|
| `Lifetime-Windows.zip` | file list | 4 programs, all 67 `lifetime_*.py`, the five installer scripts and `Install Lifetime.cmd`, assets, README, LICENSE, THIRD_PARTY_NOTICES. Complete for that commit. |
| `Lifetime-Server-Windows.zip` | file list; every file hashed against its `SHA256SUMS.txt` | 3 programs, 67 modules, install, uninstall, restart and agent scripts, docs, LICENSE, notices; 87 of 87 hashes match. No `lifetime-server.ico/png`: the icon was added after that commit. |
| `Lifetime-Linux-x86_64.tar.gz`, `.AppImage` | file list; `SHA256SUMS-x86_64.txt` recomputed | 5 programs, `_internal`, install and uninstall scripts, assets, LICENSE, notices; checksums match. |
| `Lifetime-Server-Linux-x86_64/aarch64.tar.gz` | file list | Runtime, `_internal`, three scripts, systemd/OpenRC/SysV files, `server.conf`, docs, LICENSE, notices. |

For the current code the same was checked from the build scripts: `build_server_windows.ps1` copies both server icon
files and now also puts `lifetime-server.png` inside the dashboard program (it was missing, so the packaged header
logo would have fallen back to the desktop icon); `test_installed_modules` asserts it. The release job's
`SHA256SUMS.txt` covers the six release files. **Not checked: a package built from the current code.** Open the
artifacts of the first green run the same way before tagging.

**Found in the packages:** the Linux packages contain about 95 shared libraries copied from the Rocky Linux 8 build
container, including OpenSSL 1.1.1, GNU Readline, GnuTLS and GLib. None was listed in THIRD_PARTY_NOTICES.md; they
are now (part F). OpenSSL 1.1.1 has the old OpenSSL/SSLeay license, in one process with GPL-3.0 code (Lifetime and
PyQt6): **owner action, legal review**. These copies never get the distribution's security updates.

## B. Click-through of both apps

**How:** the explorer (a scratch script, method in clickthrough-report.md), demo data in temporary folders, real
Windows platform plugin, 1600x1000 and 1024x700. Full runs (every control clicked): Glass dark English and Classic
light Vietnamese, both apps. Page runs (screenshots, layout and language checks): the other six combinations of
each app. 16 runs, one process at a time. Screenshots read by hand: Devices with and without a selection, Energy
tables, Manage groups, headers in both styles, and the guide images (Connect with the Server chip online and
offline).

| App | Controls exercised (full runs) | Dialogs | Exceptions, Qt warnings, overlaps, controls outside the window |
|---|---|---|---|
| Server | 145 (EN), 144 (VI): Overview 9, Devices 33, Incidents 2, Energy 25, Administration 71, header 4-5 | 15 each | none |
| Desktop | 44 each: Overview 4, System 14, Activity 12, Reports 2, Health 1, Settings 11 | 2 each | none |

Explorer findings (12 unique): 9 are the known false positives of the earlier report (hardware names in Vietnamese,
two tile values measured wider than they are drawn, Verify backup answered with Cancel). 3 are "slow": selecting a
row in the Devices list or the Overview table took 100 to 123 ms on the interface thread once per run. Measured
separately (12 selections, 4 runs): 30 to 95 ms, the same with and without this pass's changes. **Left**: close to
the 100 ms line and worth trimming after the release, not a regression.

**Found and fixed** (abaef3e, 27b3ea5):

| Severity | Finding | Fix |
|---|---|---|
| bug | One refresh with a computer selected starts ten background loads; the limit was 8, so the last ones were silently dropped. The detail pane, Energy and Access could keep old data until a later refresh (it showed as a test that failed one run in five). The limit dates from 1.1.0, when a refresh started fewer loads. | Limit 24; `test_a_refresh_with_a_computer_selected_drops_none_of_its_loads` fails with the old limit. |
| UX (R4) | Devices with nothing selected showed power buttons and specification cards full of "Not available". | One sentence ("Select a computer to see its details.") and nothing else; test extended, also in Vietnamese. |
| consistency | The logo inside the server window was the desktop icon. | The server's yellow icon, also inside the packaged dashboard. |
| consistency | Price box `2500,00` on the server in Vietnamese, `2500.00` on the desktop. | `2500.00` in both apps and languages, as every figure the apps print. |
| polish | Battery and disk unit prices had no currency. | They carry the saved currency. |
| wrong text | The remote power switch on Connect promised "a one minute warning first" for sleep too. Sleep has never had a warning (Windows `SetSuspendState`, Linux `systemctl suspend`); restart and shut down have. | "Restart and shut down give a one minute warning first. Sleep is immediate." (EN, VI), and the same correction in README, SECURITY, live-channel, the consent template and the guide. **Owner decision** whether sleep should get a real warning. |

**Tab switching** (scratch `measure_tabs.py`, Glass, 1600x1000, two rounds): server mean frame 15.6 ms, worst
35.1 ms (Devices), 25-33 frames per 520 ms; desktop mean 16.2 ms, worst 46.0 ms (Activity, first visit), 27-33
frames. The idle baseline again has one slow frame per window, as in the earlier measurement. Unchanged: about 16 ms.

**Left:** number formatting stays English in Vietnamese (`1,234 VND`): 44 places format numbers and changing two of
them would be less consistent, not more. The 1600 px Vietnamese header still scrolls its tab strip ("Quản tr…").
Desktop Server chip: seen online and offline in the guide screenshots and covered by `test_company_gui`; the
"Checking" state was not looked at on screen. Edit details and group on approval were exercised by the explorer
(dialogs opened and rejected), but their screenshots were not read one by one.

## C. Code review since v1.1.0

**Read end to end:** `lifetime_server_store.py`, `lifetime_monitor.py`, `lifetime_server.py`, `lifetime_access.py`
(roles table), `lifetime_client.py`, `lifetime_ws_server.py`, `lifetime_updates.py`, `lifetime_server_backup.py`,
`lifetime_availability.py`, the agent's policy, sampling and run loop, the tracker's `sample`. **Not read line by
line:** `lifetime_checks.py`, `lifetime_notify.py`, `lifetime_pipe.py`, `lifetime_unix_admin.py`,
`lifetime_tls.py`, the GUI modules, `lifetime_insights.py` beyond its constants (the security audit of the same
day covers the first five; their tests were not re-run here).

| Area | Result |
|---|---|
| Roles | All 74 admin operations are in exactly one permission class (checked by script; `test_server_access` asserts it). State-changing and refused requests are audited; a request with malformed fields is refused before the audit (nothing changed). |
| Consent on every data path | New test: history, readings and health followed through the raw rows, device detail, device list, fleet, insight inputs, alert inputs, performance samples and a backup; each is gone after its switch goes off, and Wake-on-LAN loses its MAC addresses with readings. **Gap fixed** (6c332d9): an incident kept the reading that opened it after the computer stopped sharing readings; the value is now removed (the alert stays). **Left, by design:** data stays on the server until the computer's next report says the switch is off; a revoked or disconnected computer's data stays until an administrator removes it; backups and exports made earlier keep what they had. |
| Sleep after hours | Never without consent: candidates are filtered on the owner's remote-power switch and the command path checks it again; the account's role is re-checked before every command; one per computer per 12 hours. |
| Agent backoff | **Fixed** (6c332d9): a computer waiting for approval asked every 2 s for ever (one TLS handshake each). Now 2 s for two minutes, then 15 s, then 60 s. Sync backoff (1 s doubling to 5 minutes) has no jitter, but the 15 s probe wakes each agent on its own phase when the server returns. |
| 15 s probe, limits behind NAT | One handshake per computer per 15 s only while its live socket is down; at most 8 of 32 request slots per address; a refused connection is retried. Not measured beyond 50 simulated computers. |
| Locking | **Fixed** (6c332d9): the computer's own Disconnect read the shared connection outside the store lock. No lock-order inversion found between the store, the live hub and the monitor. |
| Schema migrations | Server 7 to 10 verified with real 1.1.0 data (part D). |
| Time zones, DST, midnight | Availability and expected hours read; windows are built per local day with the schedule's zone, tests cover DST days. With the server's own zone, a business-hours window uses the offset of "now", so on the two DST days its start can be an hour off for alerts (availability reports are right). **Left.** |
| Unbounded growth | **Found, not fixed (owner decision):** `server.sqlite` keeps one usage record per 30 s per awake computer for ever: 591 bytes each (measured), 28 MB a day for 50 computers awake 8 hours, 7 GB a school year. Retention is a manual action. A backup validates every record and a restore loads the database into memory, so both degrade with size. Documented in SERVER_OPERATIONS with the numbers. Everything else is bounded (samples 7 days, monitoring 180 days/64 MiB, log 500, audit 200,000, deliveries 5,000, events 1,000). |
| Resource leaks | A staged restore that fails between its two steps leaves a `restore-*` folder (with a key copy) in the protected data directory. **Left**, low. |

## D. Upgrade from 1.1.0

A git worktree of tag v1.1.0 was created in the scratchpad (removed afterwards) and its own code run with temporary
data folders on 127.0.0.1.

| Check | Result |
|---|---|
| 1.1.0 server with a 1.1.0 computer and a current computer: enroll, approve, sync | pass; the current computer sends the 1.1 format and reports no error |
| The same data directory opened by the current server | schema 7 to 10; computers, record counts, totals, history and readings identical |
| The 1.1.0 computer keeps syncing with the upgraded server (same credential and pin) | pass |
| The current computer learns the capabilities and stops trimming | pass |
| The 1.1.0 computer's own database opened by the current agent | pass, total continues |
| A database backup made by 1.1.0 restored into the current server | pass (computers revoked, as every restore) |
| The upgraded server writes and verifies a backup archive | pass |
| Desktop: 44 days of ledger, a baseline and Vietnamese settings written by the 1.1.0 tracker, opened by the current code | totals, days, baseline and settings identical; the daily repair changes no total; two more samples add exactly their time; the window opens without an exception |

12 of 12 server and agent checks passed. **Automated:** `test_upgrade_from_1_1_0.py` with `fixtures/upgrade_1_1_0`
(written by the 1.1.0 code; the credential replaced by a fixed fake value). Not covered: the Windows installers
over a real 1.1.0 installation (part E), and the 1.1.0 GUI.

## E. Installers and scripts

All 11 PowerShell scripts parse (`Parser::ParseFile`), all shell scripts pass `bash -n` / `sh -n`, and every
script, unit file and the Dockerfile was read line by line. No reference to the scripts removed in e125027 and
5123a6b remains.

| Finding | Evidence | Fix |
|---|---|---|
| **Connecting failed on a default Windows client.** The Connect page ran `powershell -File install_agent_windows.ps1` without `-ExecutionPolicy Bypass`. | Reproduced: with the default policy the script is refused, exit 1 ("Agent operation failed"). This PC has RemoteSigned for its user, which hid it. Present in 1.1.0. | 6c60a71 |
| **Connecting again after a Disconnect failed** on packaged Windows installs: the installer stops at "already installed". | Read from the code; not reproducible here without touching the scheduled tasks. Present in 1.1.0. | 27b3ea5: `-Reinstall`, used by Connect; also the way to upgrade an installed agent. |
| **Installing over a running version failed** (Windows desktop, .exe package): a running program cannot be replaced. | Reproduced with a stand-in program in a temporary folder. | 41f826f: closes this installation's programs, restarts the tracker. |
| Agent uninstall and `-StagedUpdate` left the agent's child process running (the server scripts already handled this). With `-StagedUpdate` the update would never install. | From the server scripts' own finding on the Windows Server VM. | 41f826f |
| Server reinstall on another port kept the old firewall rule. | read | 41f826f |
| Linux desktop: bundle over a 1.1 source install left the old program files and login entries starting the old tracker; uninstall did not stop a source install's tracker; Python older than 3.11 was not refused. | Run against a stand-in bundle in a temporary home under Git Bash. | 41f826f; CI smoke test now installs, reinstalls and uninstalls the bundle. |
| Container image had no LICENSE. | read | 41f826f |

**Not verified anywhere:** every scheduled-task, service, firewall and elevation step (they need administrator
rights and would touch this PC's own Lifetime tasks), paths with spaces in a real profile (quoting was read, and
this PC's profile has a space), non-English Windows (`-UserId 'SYSTEM'` and the SDDL aliases are locale-neutral by
documentation only).

## F. Docs against the product

Read: README, SECURITY.md, SERVER_OPERATIONS.md, RELEASE_NOTES.md, ROADMAP_STATUS.md, THIRD_PARTY_NOTICES.md,
docs/pilot (runbook, checklist, consent template), the customer guide (English in full, Vietnamese by label and
changed lines). 328 bold labels of the English guide and 331 of the Vietnamese one were checked against the
programs' strings by script; the ones not found are prose, plus three Vietnamese labels that were corrected.
Defaults checked against constants: ports, alert rule defaults, 397/30 days, health score thresholds, 20 W/80 W,
2,500 VND, 4 readings over 30 days, 25 % busy, queue of 10,000 reports or 16 MB, retention figures.

Corrected (a2c8531): the sleep warning everywhere; SECURITY listed three public routes, there are seven;
"No Windows Server version tested" against the release notes' "full test on a clean Windows Server" (now: one
trial on one Windows Server 2025 VM); "remote power not on Linux" in two places; schema 9 (now 10) and monitoring
schema 2 (now 3); "CI does not publish a release"; a Linux backup example path the service account cannot write;
"Administration > This server" (it is its own page); ROADMAP_STATUS "nothing has been pushed" and "CI not yet
run"; third-party notices (build script name, pinned versions, the libraries inside the packages); upgrade and
uninstall steps. Licenses of the pinned packages were re-read from their metadata and match.

**Guide v5:** `C:\LifetimeTest\guide_final_v5\` (four PDFs, 28 and 4 pages). Changed: the switch label and the
sleep-after-hours sentence, how to update the desktop app and the agent (a former open question), removal steps,
the approval wait, exit codes for the server installer and `-StagedUpdate` (from the scripts), `-Reinstall`, three
Vietnamese labels; all 20 screenshots re-shot (yellow server logo, new Devices pane). Pages 6 and 18 viewed.
13 `[CHECK]` items remain for the owner. **Owner decision:** the guide says Linux comes in 1.3.0, while the
release notes and the release workflow ship the Linux packages with the next release.

**Claims that depend on CI:** README's table ("Debian 10 ... CI containers") and SERVER_OPERATIONS ("installs,
restarts and uninstalls in Debian 12 and Rocky 9 containers") are true only once a run is green; the last run that
reached those jobs failed exactly there. The release job cannot run without them, so a release cannot carry the
claim falsely, but do not tag on a red run.

**Version strings. Not changed. To change at release time:**

- `lifetime_reports.py`: `VERSION = '1.1.0'` (report footers; `lifetime_protocol.CLIENT_VERSION` already says 1.2.0, so today the two disagree).
- `RELEASE_NOTES.md`: the heading "Next release (not yet published)" becomes "Lifetime 1.2.0" with a Download list of the six files (the 1.1.0 list names `Lifetime-Linux.tar.gz`, which no longer exists). The release job publishes this whole file as the release text.
- Guide: `PUBLIC 1.1.0 | 1.2.0 UPCOMING` headers and every "Coming in version 1.2.0" box, file names `_v1.2.0_v5`.
- `docs/ROADMAP_STATUS.md` first paragraph; README's "version tag such as `v1.0.0`" example.
- Any test that asserts the report version (search the tests for `1.1.0`).

## G. Security re-check

A scratch script against a real server on 127.0.0.1: 35 of 35 checks pass.

| Audit item | Re-check | Holds |
|---|---|---|
| H1 text as HTML | `test_plain_text` (6 tests, both windows filled with markup); a name with markup is stored and returned as data | yes |
| M1 elevated installer | `test_windows_install_safety` (9 tests, junction refusal run for real) | yes |
| M2 agent updates | unchanged by design; documented as trusting the server's administrators until releases are signed | owner decision |
| M3 pinned builds | `test_build_pins`; installed package versions equal the pins. The Rocky Linux 8 image and its `dnf` packages are not pinned. | partly, as documented |
| L3 slots per address | 9th to 13th connection of one address closed, another address still served (driven through the accounting, because loopback is exempt) | yes |
| L4 wrong codes | twelve wrong codes throttle that address only; another address enrolls | yes |
| L5, L6 | documented limits, unchanged | owner decision |
| L7 role SIDs | five crafted principals refused, none reaches the roles table | yes |
| B1, B3 | read in the code (wake sends after the transaction; error kind only) | yes |
| Untrusted reports | control characters, 5,000 characters, unknown fields, NaN, 201 %, a path as a date: all refused, nothing stored | yes |
| Admin API | no token 401, wrong token 401, rebound Host 403, bound to 127.0.0.1, not served on the public port; live socket refuses an Origin header; backups refused onto the token, key, database and secrets file | yes |

Secret scanner over `d74ac32..HEAD`: 37 hits, all in the upgrade fixture (a public certificate of a deleted test
key, hashes, the placeholder address `02:00:00:00:00:01`) and loopback addresses. No secret. Author and committer
of every new commit are the noreply identity; no trailers. The scratch file `public.patch` was deleted.

## Tests run (one module at a time, Windows 11, Python 3.12)

test_server_runtime (39), test_windows_install_safety (9), test_linux_desktop (9), test_installed_modules (6),
test_build_pins (4), test_plain_text (6), test_sync_client (35), test_monitor (40), test_server_store (58),
test_upgrade_from_1_1_0 (4), test_server_fleet (13), test_server_gui (81; run six times, one failure in the first
five runs led to the job-limit fix), test_insights and test_server_insights (32), test_i18n (9),
test_company_gui (27), test_ws (14). All pass. The full suite was not run locally (memory rule): CI must.

## Only on real machines

1. Windows: install over a running 1.1.0, Connect on a default-policy PC with a separate administrator account, Disconnect then Connect, `-Reinstall`, agent uninstall leaving no process, `-StagedUpdate`, non-English Windows.
2. Remote Sleep, Restart, Shut down and Wake-on-LAN on real hardware (on Windows, sleep may hibernate where hibernation is on), and Sleep after hours for a night.
3. Windows Server 2022/2025: service mode across reboots, firewall profiles, domain accounts and groups, non-elevated dashboards.
4. Linux: the window on X11 and Wayland, tray, autostart, the systemd unit, OpenRC, firewalls, reboot, SSH-forwarded dashboard, the installer's upgrade path from a 1.1 source install.
5. Certificate renewal and address change on a real network; rediscovery.
6. Mail servers, webhooks, real printers and routers for checks; printed PDFs.
7. Fleets above 50 computers, and a server database after months of records.
8. GPO or Intune deployment.

## Release readiness

Not ready to tag today. The code is in better shape than at d74ac32: the two CI failures and a flaky test are
fixed, and this pass removed three faults that would have met the first real customers (pairing failing on a
default Windows client, reconnecting after a Disconnect, installing over a running version). But no CI run has
been green for 96 commits, so no package of the current code has ever been built or smoke-tested, and the Debian
10 and Linux installer fixes are unconfirmed. The installer changes of this pass, like the faults they fix, could
not be run with real scheduled tasks here. Before a tag: push, get one fully green run, open its artifacts, and
try install-over-1.1.0, Connect, Disconnect and Connect again on one real Windows PC with default settings. Three
decisions are the owner's: automatic retention for usage records, the OpenSSL 1.1.1 license question in the Linux
packages, and whether Linux ships in 1.2.0 (repository) or 1.3.0 (guide).
