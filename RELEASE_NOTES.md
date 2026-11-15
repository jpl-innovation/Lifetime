## Next release (not yet published)

- **Two styles:** **Classic** (the original solid look) and **Glass** (translucent panels, glass menu and a blue-to-gold background). Desktop: Settings; Server: Administration. Applies instantly and is remembered.
- **Settings is a page**, not a pop-up, and every tick box is now a sliding switch (green when on).
- **Lifetime Server header:** one-click language (rounded-corner button) and theme (circle button) next to the menu; the menu pill stretches towards the page you choose and pages slide in from that side.
- **Lifetime Server shows what the desktop shows:** per computer Activity (daily hours, hour-of-day, weekdays, session lengths, boots) and Health (stability, battery, sleep), plus a plain empty state when the computer shares nothing.
- **More hardware details** on the desktop System page and the server: CPU temperature and clock, cooling fans (count and speed where the firmware reports them), network cards, memory modules, disks, motherboard and BIOS, graphics driver, displays and OS build. Missing values say "Not available"; nothing is invented; no serial numbers.
- **Animations:** charts, numbers and the sidebar marker animate in once, skipped under "reduce motion".
- **Server installer and agent fixes** from a full test on a clean Windows Server: Python detection, retry after a partial install, firewall rule follows the chosen port, a Start menu shortcut for the dashboard, cleaner uninstall, and the packaged agent no longer gets stuck at "unreachable" after approval.
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

Not yet verified on real machines: certificate renewal and rediscovery on a real network (tests use loopback servers and back-dated certificates), silent deployment through real GPO or Intune, alert email and webhooks against real mail servers and chat services (tests use a local SMTP stub with STARTTLS and a fake webhook), network checks against real printers, routers, NAS boxes and websites and ping on Linux (tests use servers on 127.0.0.1), and printed PDF reports.

Upgrading: update the computers before you change the server's names. Computers that are never updated from 1.1 pin the whole certificate and stop at the first renewal or name change (they need updating or pairing again). Updated computers take the key pin from the certificate they already store, so they keep working through a renewal even if they have not reported since the update. The server database moves to schema 9 on first start; keep a backup. Alerts start with the default rules and no email: the server creates `monitoring.sqlite` next to its database (schema 2 adds network checks; a backup from schema 1 restores and is upgraded), and nothing is sent until you set up **Notifications**. Run `pip install -r requirements-server.txt` again in the server's environment (source installs) to get `tzdata`.

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
