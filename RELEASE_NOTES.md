## Next release (not yet published)

- **Two styles:** **Classic** (the original solid look) and **Glass** (translucent panels, glass menu and a blue-to-gold background). Desktop: Settings; Server: Administration. Applies instantly and is remembered.
- **Settings is a page**, not a pop-up, and every tick box is now a sliding switch (green when on).
- **Lifetime Server header:** one-click language (rounded-corner button) and theme (circle button) next to the menu; the menu pill stretches towards the page you choose and pages slide in from that side.
- **Lifetime Server shows what the desktop shows:** per computer Activity (daily hours, hour-of-day, weekdays, session lengths, boots) and Health (stability, battery, sleep), plus a plain empty state when the computer shares nothing.
- **More hardware details** on the desktop System page and the server: CPU temperature and clock, cooling fans (count and speed where the firmware reports them), network cards, memory modules, disks, motherboard and BIOS, graphics driver, displays and OS build. Missing values say "Not available"; nothing is invented; no serial numbers.
- **Animations:** charts, numbers and the sidebar marker animate in once, skipped under "reduce motion".
- **Server installer and agent fixes** from a full test on a clean Windows Server: Python detection, retry after a partial install, firewall rule follows the chosen port, a Start menu shortcut for the dashboard, cleaner uninstall, and the packaged agent no longer gets stuck at "unreachable" after approval.
- **Accounting and connection improvements** (awake-clock rollback handling, safer upgrades and restarts) and less lag when switching tabs.

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
