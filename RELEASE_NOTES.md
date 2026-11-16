## Next release (not yet published)

- **One Linux desktop build for every distribution:** `Lifetime-Linux-x86_64.AppImage` (nothing to install) and the
  same bundle as `Lifetime-Linux-x86_64.tar.gz` with a per-user installer (`~/.local/lib/lifetime`, no root) and
  `uninstall_linux.sh`. It brings its own Python and Qt (X11 and Wayland), built on glibc 2.28, so it runs on Debian
  10+, Ubuntu 20.04+, RHEL/Alma/Rocky 8+, Fedora, openSUSE Leap 15.3+, Arch and their derivatives, on any desktop.
  Without systemd, tracking at login uses XDG autostart; Verify and earlier usage say "Not available" without a
  journal; remote power falls back to `loginctl` or logind over D-Bus and never asks for a password or uses sudo.
  Alpine and other musl distributions are not supported. Replaces the Python-source `Lifetime-Linux.tar.gz`
  (`install_linux.sh` still installs from a source checkout).
- **Lifetime Server for Linux:** `Lifetime-Server-Linux-x86_64.tar.gz` and `-aarch64.tar.gz`, headless, no Qt or
  system Python. `install_server_linux.sh` installs to `/opt/lifetime-server` with data in `/var/lib/lifetime-server`
  (0700, system account `lifetime`), a systemd service (OpenRC or SysV script without systemd), restart and
  uninstall scripts, and prints the `ufw`/`firewalld` rule (8443 from the local subnet; 8444 never). A `Dockerfile`
  builds a non-root container image with a data volume for any Linux with Docker or Podman. Named administrators
  on Linux: a Unix socket with `SO_PEERCRED` maps Unix users and groups to the same roles and audit trail (root and
  `lifetime-admin` are Admin; the socket's group `lifetime-console` lets an account connect but grants no role until
  one is assigned with `set_role`), so remote power from a Linux server now works with a named account. Alert secrets on
  Linux are a 0600 file (permissions, not encryption). Computers pair with it exactly as with a Windows server.
- **The desktop app knows whether its server is on (desktop and agent):** while the computer is connected to a
  Lifetime Server, a **Server: Online / Offline · last reached 14:05 / Checking…** chip sits under the status in the
  sidebar and on the Connect page, with the reason (server not running, no answer or firewall, name not found,
  certificate changed) and that awake time keeps counting and is sent later. The agent checks the server itself every
  15 s (10-25 s while it is away) with one pinned TLS handshake and no data, skipping the check while the live socket
  is up; the window only reads the agent's status file in the background, and stops while it is hidden. A server that
  comes back gets the waiting reports at once. The Connect page now also shows why the last report failed (that line
  was empty before). English and Vietnamese. Timing checked with a simulated clock, not on real machines.
- **Groups ahead of time and one place for a computer's details (server):** **Manage groups** on the Devices page
  creates, renames and removes groups before any computer is in them (empty state when there are none); approving a
  computer can put it in a group straight away; **Edit details…** in a device's menu edits name, asset tag, group,
  location and deployment date together. Renaming a group also moves its computers' energy, after-hours, health and
  sleep-after-hours grouping, its expected hours, maintenance windows, wattage and agentless checks. New Admin
  operations `groups`, `add_group`, `rename_group`, `remove_group` follow the same roles and audit trail (Unix socket
  roles on Linux too). Server database schema 10; backups from schema 9 still restore. English and Vietnamese.
- **Lifetime Server has its own icon** (navy and cream swapped against the desktop app's) in the Windows server
  package, its Start menu shortcut and the Linux bundle; on Linux the two windows announce their own app ids
  (`lifetime`, `lifetime-server`) so docks and taskbars show the right icon (the tar.gz installer adds a hidden
  `lifetime-server.desktop` for it; an AppImage run without desktop integration shows the window icon only).
- **Connect:** a server found by the one-code LAN scan is kept only at an address its certificate names; when the
  certificate names only a host name, the computer moves to that name and checks the connection code against it
  again, so pairing no longer fails later with a hostname error. The sharing switches on the Connect page use the
  app's switch and are no longer clipped.
- **Server command line:** `LifetimeServerRuntime admin --operation ... [--arguments JSON] [--field NAME]` (and
  `lifetime_server.py admin`) sends one request with the caller's own role, for headless servers and containers.
- **Energy and cost from true awake time (both apps):** kWh and money per computer, group and fleet for today, 7 and
  30 days, a daily chart and the top consumers on the server's new **Energy** page; an **Energy and cost** panel on the
  desktop Reports page. Power is measured only from a computer's own on-battery readings, otherwise estimated from a
  wattage you set or a typical laptop (20 W) or desktop (80 W), and always labelled. Price (default 2,500 VND per kWh)
  and currency are editable. Reports include an Energy and cost section with the method.
- **After hours:** awake time outside business or office hours per computer, room, group and fleet, with energy, cost,
  an 8-week trend and the estimated monthly saving; an optional per-group **Sleep after hours** that sends the
  ordinary one-computer Sleep command within the existing consent, role, rate-limit and audit rules (never a bulk
  command). The desktop app says how long the computer was on after hours this week.
- **Replacement plan:** battery and disk forecasts from each computer's own readings, with "not enough data" and the
  data span instead of a guess, disks that report a problem, age and awake hours, what to replace this quarter and this
  year, and an optional budget. The desktop Health page shows its own forecast.
- **Health score per computer, group and fleet:** the badge now also counts system disk free space, temperature and the
  agent version; unknown parts are shown and left out of the score.
- **Click-through QA fixes (both apps):** the Server log and the Overview's recent events are event rails (dot, words, time and computer); device cards show the last 24 hours as hourly ticks (loaded off the interface thread, once a minute per computer); the desktop Reports page shows where the saved time comes from as one bar with labelled parts; the desktop Connect page shows the connection as a card; empty Maintenance windows, Delivery log and Roles lists say so instead of showing a blank table; the Pending approvals list is tall enough for its rows; **Open device** on the Overview no longer lands on "No computers match" when a status filter was left on; the server header subtitle ends in "…" with a tooltip instead of being cut mid-word; desktop Overview tiles grow instead of cutting a three-line heading (Vietnamese at 1024 px). Stability: a delayed number roll-in on a removed label, and a background result arriving for a closed window, could stop the app; shutdown now waits up to 3 s for running background tasks. Report: docs/qa/clickthrough-report.md.
- **Two styles:** **Classic** (the original solid look) and **Glass** (translucent panels, glass menu and a blue-to-gold background). Desktop: Settings; Server: Administration. Applies instantly and is remembered.
- **Settings is a page**, not a pop-up, and every tick box is now a sliding switch (green when on).
- **Lifetime Server header:** one-click language (rounded-corner button) and theme (circle button) next to the menu; the menu pill stretches towards the page you choose and pages slide in from that side.
- **Lifetime Server shows what the desktop shows:** per computer Activity (daily hours, hour-of-day, weekdays, session lengths, boots) and Health (stability, battery, sleep), plus a plain empty state when the computer shares nothing.
- **More hardware details** on the desktop System page and the server: CPU temperature and clock, cooling fans (count and speed where the firmware reports them), network cards, memory modules, disks, motherboard and BIOS, graphics driver, displays and OS build. Missing values say "Not available"; nothing is invented; no serial numbers.
- **Livelier dashboards in both apps:** states are chips with a symbol and words (devices, incidents, network checks, invitations, agent updates); a computer's last 24 hours online as hourly ticks; CPU and memory trends beside each device card; specifications and hardware details as cards with icons; disk space as bars that turn amber and red when nearly full; a state bar on the server Overview that opens the matching devices; an incident timeline; clear empty states; and a Health score on the desktop Health page that shows how it is computed and says Unknown when readings are missing. Tab switching stays as smooth as before (measured).
- **Animations:** charts, numbers and the sidebar marker animate in once, skipped under "reduce motion".
- **Server installer and agent fixes** from a trial on one clean Windows Server 2025 virtual machine: Python detection, retry after a partial install, firewall rule follows the chosen port, a Start menu shortcut for the dashboard, cleaner uninstall, and the packaged agent no longer gets stuck at "unreachable" after approval.
- **Accounting and connection improvements** (awake-clock rollback handling, safer upgrades and restarts) and less lag when switching tabs.
- **No re-pairing for certificate renewals or address changes:** computers now pin the server's key instead of its certificate (taken from the certificate they already trust, so nothing new is trusted). The server renews its certificate from the same key 30 days before expiry (397-day certificates) and when you add or remove server names and IPs under **Administration**; **This Server** shows the expiry, and the dashboard warns if a renewal fails. A computer whose stored address stops answering looks for the server on the local network and accepts it only with the same key. Re-pairing is needed only if the server's key is lost. Pairing codes created before a renewal must be replaced.
- **Mixed versions during upgrades:** the server lists what it accepts, computers send only that, and the Connect page says when an older server means some details are not shared. Older computers keep working with the new server unchanged.
- **Silent deployment:** `install_agent_windows.ps1 -Mode Packaged -EnableReporting -ConnectionLine "..."` (or `-PairingCode` with `-Server`) installs and connects a computer from GPO, Intune or a script without prompts, with exit codes and a secret-free `install.log`. Sharing beyond awake time must still be named explicitly. See `docs/SERVER_OPERATIONS.md`.
- **Lifetime Server shows why a computer is not reporting:** each computer's **Last problem** (for example "No answer (timeout)") and version in its detail, and a **Server log** under Administration (starts, stops, renewals, refused reports; no report contents or codes).
- **Connect page explains certificate problems** more precisely: an out-of-date certificate or wrong clock, or an address the server's certificate does not list.
- **Lifetime Server alerts and incidents:** built-in rules for a computer offline during its expected hours, no report, low disk, busy processor, full memory, worn battery, high temperature, repeated crashes and an outdated Lifetime version, each with on/off, threshold and duration under **Administration**. Problems must last before they open and stay away five minutes before they resolve, so spikes and flapping stay quiet. A new **Incidents** page (acknowledge, timeline, filters), an open-alert count in the header and each computer's incident history in its detail. **Expected online hours** per group or computer (laptops are never expected by default, so a sleeping laptop raises nothing) and **maintenance windows**. Rules use only what a computer shares; missing readings show as "telemetry missing".
- **Alert email and webhook:** one message when an incident opens and one when it recovers, over SMTP with STARTTLS or TLS (never plain), with a test button, retries and a delivery log; an optional daily summary and an optional HTTPS webhook for Teams, Slack or Telegram bridges, both off by default. The mail password and webhook address are stored encrypted (DPAPI on Windows) and never shown again. See `docs/SERVER_OPERATIONS.md`.

- **Availability and reports:** reports per computer, group or the whole fleet, for any period such as a month, in PDF or CSV and in English or Vietnamese, now show availability beside awake time (separately: they measure different things) with coverage, unknown and maintenance time, the period's incidents, a health summary and replacement candidates with stated reasons (worn battery, low disk, repeated crashes). Availability counts only expected online hours, never counts unknown time as up or down, and says *insufficient evidence* below a minimum coverage you choose (80 % by default). Days follow each computer's schedule time zone, including DST. Every PDF page names the version, time zone and data source.
- **Network checks** for devices without Lifetime (printers, routers, NAS, websites): ping, TCP port or web page (status range, optional keyword, certificate verified by default, limited redirects and size), under **Administration > Network checks** with each check's last result and latency. Checks reach only the networks you allow (LAN ranges by default); loopback, link-local, cloud metadata addresses and the server's admin port are blocked. A check failing three times in a row opens one incident with the usual notifications.
- **Time zone names on Windows:** `requirements-server.txt` now installs `tzdata`, so schedules can use names such as `Asia/Ho_Chi_Minh`; without it the dashboard explains why a name is refused and offsets keep working.
- **Expected hours and maintenance for one computer** are now chosen from a list of computers (name and group) instead of typing its ID.
- **Backups include monitoring:** **Save backup** also writes `<name>.monitoring.sqlite` (alert history, incidents, rules, schedules, checks, notification settings; not the mail password) and **Restore backup** restores it when it is beside the backup.
- **Named administrators and roles:** on Windows the dashboard signs in as the Windows account that opens it, read by the server from a local named pipe (never typed in). Administrators running as administrator are Admin; give other accounts or groups **Operator** (acknowledge, maintenance, refresh, remote power for one computer where its owner allowed it), **Auditor** (view and audit trail) or **Viewer** (view only) under **Administration > Roles and access**. The server checks every request; the dashboard greys out what a role cannot do and says why. The `admin-token` channel still works for Linux and older dashboards and counts as Admin.
- **Audit trail:** every change and every remote command, with who did it, its target and result, in a hash chain that **Verify audit trail** checks; **Export CSV**. A local administrator can still edit the file: the chain detects tampering, it cannot prevent it.
- **Safer remote power:** needs a named account (a Windows account on the pipe or, on a Linux server, a Unix account on the admin socket; never the shared token), three per computer and ten per account in ten minutes, expires after ten minutes, one computer at a time, and the confirmation shows the computer's name, group and ID. Removing a role cancels the power commands it had sent that are still waiting.
- **One-file backups:** **Save backup** writes one protected `.zip` (all databases, the server key and certificate, configuration) with a `.sha256` file; **Verify backup** checks it; **Restore backup** applies it at the next server start. Also `lifetime_server.py restore | verify-backup | verify-audit` from a prompt, and a restore drill in `docs/SERVER_OPERATIONS.md`.
- **Agent updates (staged):** under **Administration > Agent updates** upload an approved agent package with its published SHA-256 and version, offer it to a share of computers (10 % first), pause or roll back. Only computers whose owner switched on *Get Lifetime updates approved by the server* (off by default) download it over their secure connection, check its SHA-256 and, on Windows, its signature when it has one, keep it ready and report back. They never run it: `install_agent_windows.ps1 -StagedUpdate` installs it, for example from a startup script at the next restart, and keeps the previous program.
- **Faster at fleet size:** a report with readings no longer reads every stored performance sample of every computer, and the live connection's periodic command check asks the database once instead of once per connected computer. A load-test tool (`tools/fleet_simulator.py`, not shipped) measured 50 simulated computers on one PC; see `docs/benchmarks` (supported size = tested size).
- **Smoother page switching (both apps, Glass and Classic):** a page now enters as one picture of itself, so a full page (Devices with a computer open, Administration, Activity, Settings) costs no more per animation frame than an empty one; the server's animations run at 60 frames a second again instead of about 40 (short entrance delays no longer use up Windows' precise timers); sliding switches no longer redraw themselves on every frame while visible (the Settings page kept a processor core busy); and pages are drawn once in idle time after start-up so the first visit is as quick as the next. Measured on one PC at 1600x1000 with synthetic data; slower PCs and high-refresh screens not measured.
- **Pilot kit:** `docs/pilot` has a site checklist, a consent and data policy template in English and Vietnamese, a support runbook, an evidence log, the side-by-side task protocol with Zabbix and an interview script; `docs/ROADMAP_STATUS.md` says what is done, what is tested only synthetically and what still needs real machines.
- **Packaging:** the packaged server runtime, dashboard and service name every module the code imports only when needed (alerts, network checks, the named pipe, reports, updates), the service no longer bundles Qt, and both bundle the full `tzdata` time zone database; a test fails if a module is missing from a build or installer list.
- **Security: names are text, never HTML.** A computer's name and hardware strings, group, check and role names and a server's name were shown in labels that render HTML when the text looks like HTML, so a computer named with an image tag could make the administrator's dashboard fetch a file from another machine. Every label in both apps is now plain text unless it escapes its values, including message boxes and tooltips.
- **Security: Windows installer.** The desktop installer refuses to install through a linked folder and no longer takes ownership of everything under `%LOCALAPPDATA%\Lifetime` while elevated. Connecting to a server now runs the agent setup files from `%ProgramFiles%\LifetimeAgentSetup` (written by the installer, or copied there and SHA-256 checked by the one approved step), never from the per-user folder. After an upgrade, run the installer with administrator approval so those copies are updated.
- **Security: builds.** Release builds use exact, CI-tested package versions and GitHub actions pinned to commits, and a version tag whose release already exists now fails instead of replacing its files. Source installs of the server get the same exact versions from `requirements-server.txt`.
- **Server hardening:** one address can hold at most 8 of the 32 request slots; wrong invitation codes hold back the address that sent them instead of locking enrolment for everyone (a shared ceiling of 200 a minute remains); role SIDs are fully validated, also from a restored backup; Wake no longer makes other requests wait while it sends; the admin command and the Windows service log an error's kind only.
- **Fixed:** the status filter on the server's Devices page is no longer cut off in Vietnamese; the filters are as wide as their longest entry and the row wraps.
- **Fixed:** report PDFs from the dashboard on Windows no longer fail with "PDF generation failed or produced no text" when the report has no emoji (the emoji font had become the text font).
- **Fixed (final verification before this release):**
  - **Connecting on Windows:** the Connect page ran the agent installer without `-ExecutionPolicy Bypass`, so on a PC with Windows' default script policy (every Windows 10/11 client, unless changed) Finish ended with "Agent operation failed"; and connecting again after a **Disconnect** failed because the installer stopped at "an agent is already installed". Finish now passes the policy switch and `-Reinstall`, which replaces the installed agent and keeps its identity, history and unsent reports. `install_agent_windows.ps1 -Mode Packaged -Reinstall -Start` (elevated, no enrollment parameters) is also how an installed agent is upgraded in place without touching its connection. The step that copies the agent setup files no longer needs `Get-FileHash`, which Windows PowerShell cannot find when Lifetime was started from PowerShell 7.
  - **Installing over a running version on Windows:** the installer closes this installation's running Lifetime, tracker and import programs before replacing them (it used to stop with "the file is being used by another process") and starts the tracker again when it starts at login. Uninstalling the machine agent, and `-StagedUpdate`, also end the agent's child process, which stopping the task alone leaves running. A reinstall of the server on another port moves its firewall rule to that port.
  - **Linux desktop installer:** installing the bundle over an earlier source install removes the old program files (never the history), rewrites login entries that still started the old tracker or agent, restarts running user services, refuses Python older than 3.11 with a reason, and the uninstaller also stops a source install's tracker. CI now installs, reinstalls and uninstalls the bundle with `install_linux.sh` on every distribution.
  - **Server dashboard:** one refresh with a computer selected started ten background loads but only eight were accepted, so the detail pane, Energy and Access could keep old data until a later refresh. Devices with nothing selected shows one sentence instead of power buttons and specification cards full of "Not available". The logo in the server window's header is the server's own yellow icon. The price box reads `2500.00` in both apps and languages, and the battery and disk unit prices show the currency.
  - **Consent:** when a computer stops sharing readings (or health), the reading an earlier alert recorded is removed from the incident history as well (the alert stays). One test now follows history, readings and health through every read path and a backup.
  - **Waiting for approval:** a computer whose request nobody approves checks every 2 s for two minutes, then every 15 s, then once a minute, instead of every 2 s for ever.
  - **The remote power switch says what happens:** "Restart and shut down give a one minute warning first. Sleep is immediate." It used to promise the warning for sleep too, which never had one.
  - **Upgrade path checked against the released 1.1.0:** its server database, its computers' databases and its desktop ledger and settings open with this version with nothing lost (schema 7 to 10), a 1.1.0 computer keeps reporting to an upgraded server and a new computer reports to a 1.1.0 server, and a 1.1.0 backup restores; a test now does this from data the 1.1.0 code wrote.
- **Fixed (VMware test of 1.2.0):**
  - **Remote power refused:** the server dashboard and `lifetime_server.py admin` say why ("This computer does not allow remote sleep, restart, shut down or turn on. Turn on power control on that computer first.", offline, or a command already waiting) instead of a bare "HTTP 400".
  - **Last problem after reconnecting:** connecting again (or being approved again) clears the old connection's error, so the Connect page and the server's "Last problem" no longer show a stale "Server error 403".
  - **Finish on the Connect page:** a failed agent setup says why from the installer's exit code (details refused or a foreign LifetimeAgent folder, server unreachable or certificate not matching the code, code used or expired, administrator approval needed, agent already installed), otherwise the general message with the error number.
  - **Server uninstall:** `uninstall_server_windows.ps1` closes the dashboard windows running from that installation first, so a later reinstall no longer stops on a file they kept in use.
  - **Groups:** renaming a group to the name of another group (any letter case) is refused with "A group with that name already exists." instead of silently merging the two.
  - **Health score:** a computer that shares only awake time shows no score ("Unknown") instead of 100 and is left out of room, group and fleet averages.

Not yet verified on real machines: the server uninstall closing open dashboard windows (its script is checked by tests, not run) and the Connect page's per-exit-code messages from a real elevated installer run, installing over a running version, `-Reinstall`, connecting again after a Disconnect and the uninstallers ending the agent's child process on a real PC (the process handling was tried here only on a stand-in program in a temporary folder; the scheduled-task steps were not run at all), the Linux desktop installer's upgrade steps on a real desktop (run here against a stand-in bundle, and from this release in CI containers), the installer and Connect changes on a standard-user PC with a separate administrator account (tests run the link refusal and the copy-and-verify step unelevated in a temporary folder), the pinned Linux build (PyInstaller 6.11.1 with hooks 2026.9, and PyQt6 6.9.1 with PyQt6-sip 13.13.0, were each tested by CI but not in that combination), named administrators with domain accounts and groups, non-elevated dashboards on a real Windows Server and the restore drill on a second machine (tests run the pipe, roles, audit and backup round trip on one Windows 11 PC), certificate renewal and rediscovery on a real network (tests use loopback servers and back-dated certificates), silent deployment through real GPO or Intune, alert email and webhooks against real mail servers and chat services (tests use a local SMTP stub with STARTTLS and a fake webhook), network checks against real printers, routers, NAS boxes and websites and ping on Linux (tests use servers on 127.0.0.1), installing a staged agent update with `-StagedUpdate` from a real startup script and with a signed release (tests run the download, checks, rollout and rollback on 127.0.0.1), fleets larger than the 50 simulated computers measured on one PC, and printed PDF reports. Linux (tested only in CI containers, which have no display, desktop session, systemd or firewall): the window on real X11 and Wayland desktops, the tray, login autostart (systemd user service and XDG autostart), remote power through systemd, elogind and D-Bus, the server's systemd unit and OpenRC script, `ufw`/`firewalld`, start-up after a reboot, the SSH-forwarded dashboard, and the container on real hosts; see the VM checklist below.

Upgrading: update the computers before you change the server's names. Computers that are never updated from 1.1 pin the whole certificate and stop at the first renewal or name change (they need updating or pairing again). Updated computers take the key pin from the certificate they already store, so they keep working through a renewal even if they have not reported since the update. **Windows computers:** run the new **Install Lifetime.cmd** and approve it; it closes a running Lifetime, replaces the programs and refreshes the agent setup files. That does not replace a machine agent that is already installed (a computer connected to a server): from an elevated PowerShell in the unzipped package run `powershell -ExecutionPolicy Bypass -File .\install_agent_windows.ps1 -Mode Packaged -Reinstall -Start`, which keeps the connection, or press **Disconnect** and **Connect** again with a new code. A 1.1 agent that is not replaced keeps reporting in the 1.1 format. **Linux desktops:** run `install_linux.sh` from the new bundle; it replaces a 1.1 source install. The server database moves from schema 7 (1.1.0) to schema 10 on first start; keep a backup. Alerts start with the default rules and no email: the server creates `monitoring.sqlite` next to its database (schema 3; an older monitoring backup restores and is upgraded), and nothing is sent until you set up **Notifications**. Run `pip install -r requirements-server.txt` again in the server's environment (source installs) to get `tzdata`. The server creates `access.sqlite` (roles and the audit trail) on first start; remote power from the dashboard now needs the identity-bound channel, so run the dashboard on the server itself (as before); on a Linux server it uses the Unix socket (be root or in `lifetime-admin`).

Linux VM checklist for the owner (VMware): Ubuntu 22.04 desktop and Fedora desktop (AppImage and tar.gz, X11 and Wayland sessions, autostart, Verify, pairing, remote power), Debian 12 server and Rocky Linux 9 server without a GUI (install, firewall, reboot, roles over the socket, SSH-forwarded dashboard, backup and restore, upgrade, uninstall).

## Lifetime 1.1.0

Track how long your computer has really been awake, excluding sleep and hibernation. Made by JPL Innovation.

### Download

- **Windows 10/11:** `Lifetime-Windows.zip` -> unzip -> double-click **Install Lifetime.cmd**
- **Linux:** `Lifetime-Linux.tar.gz` -> `tar -xzf Lifetime-Linux.tar.gz && cd Lifetime-Linux && bash install_linux.sh`
- **Lifetime Server (Windows Server):** `Lifetime-Server-Windows.zip` -> follow `docs/SERVER_OPERATIONS.md`

Windows may show a SmartScreen warning because the programs are not code-signed: choose **More info -> Run anyway**.
Check your download against `SHA256SUMS.txt`.

### Updated build of 1.1.0

The files attached to this release were refreshed (same version number, new checksums in `SHA256SUMS.txt`):

- **Light theme** for Lifetime on Windows and Linux and for Lifetime Server. Choose **Auto** (follows your system), **Light** or **Dark** at the bottom of the sidebar (Server: Administration > Theme). The new theme spreads across the window from the control, with no restart.
- **Add earlier usage**: a new computer used to show only the time since its last start. Setup now offers (and pre-selects) adding the time before Lifetime was installed, and the Overview has an **Add earlier usage** button until it is done. The wording no longer talks about "rebuilding" or "logs".
- **Select all** on the "What would you like to track?" step.
- **Verify** counts suspend-then-hibernate correctly on Linux (it showed a false gap of days after a long hibernate).
- Tests no longer fail when the optional `websockets` package is missing.

### What is new in 1.1.0

- **Three editions:** Lifetime for Windows, Lifetime for Linux, and **Lifetime Server**, a fleet dashboard for IT teams that sees every connected computer's specs, performance and health, and can restart, sleep, shut down or wake them.
- **Live data over one persistent socket:** a WebSocket on the same pinned-certificate connection replaces one request per sample. The server pushes the update rate the moment someone opens a computer, commands arrive instantly with an acknowledgement and are never run twice, and a closed connection shows the computer offline at once. Older servers and agents keep working over HTTPS. See `docs/live-channel.md`.
- **Simple connecting:** one code. Connect has its own page, shows a loading screen while the computer and server talk, and then asks only what to share.
- **Lifetime Server redesign:** a top navigation bar, a card for every computer with problems first, a detail panel with specs and live charts, search, filters, Refresh, Remove computer, and loading placeholders.
- **Clear status words:** Online, Offline and Disconnected instead of "report overdue".
- **Tray icon** and an animated running indicator in the desktop and server apps.
- **Consistent corners and controls** across both apps.
- **Security hardening:** browser requests (any `Origin`) are refused on both sockets, the local admin API checks the `Host` header, a computer that floods the socket is disconnected, and unauthenticated upgrades cannot use the long-lived connection pool. Dependencies were audited with no known issues.

### Upgrading

- **Python 3.11 or newer is now required** for the Python route (3.10 reached end of life in October 2026). The Windows installer package needs no Python.

- Computers connected by an older version keep reporting over HTTPS. To use the live socket, press **Disconnect** then **Connect** with a new code. Connecting now replaces a background reporter left over from an earlier connect.
- Servers need `pip install websockets` when run from source (it is in `requirements-server.txt`); the packaged server already includes it. No new port or firewall rule is needed.

### Known issues

- On some Linux laptops Verify can show a gap of about the length of a suspend. Your total is not affected. See the README for how to help diagnose it.
- A reporter installed from source runs in a clean environment without extra packages, so it uses the HTTPS path unless `websockets` is installed there.
- Real remote Sleep, Restart, Shut down and Wake-on-LAN have been verified in tests and on loopback only.

### Highlights

- Live awake-time counter with seconds, plus a Verify button that cross-checks against the operating system's records
- System dashboard: power, network, graphics, CPU, memory, disks, live sparklines
- Insights: calendar heatmap, daily trend, cumulative hours, hour and weekday profiles, session lengths
- First-run consent: you choose what is tracked, and everything stays on your computer
- English and Tieng Viet, switchable at any time
