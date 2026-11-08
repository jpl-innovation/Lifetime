## Lifetime 1.1.0

Track how long your computer has really been awake, excluding sleep and hibernation. Made by JPL Innovation.

### Download

- **Windows 10/11:** `Lifetime-Windows.zip` -> unzip -> double-click **Install Lifetime.cmd**
- **Linux:** `Lifetime-Linux.tar.gz` -> `tar -xzf Lifetime-Linux.tar.gz && cd Lifetime-Linux && bash install_linux.sh`
- **Lifetime Server (Windows Server):** `Lifetime-Server-Windows.zip` -> follow `docs/SERVER_OPERATIONS.md`

Windows may show a SmartScreen warning because the programs are not code-signed: choose **More info -> Run anyway**.
Check your download against `SHA256SUMS.txt`.

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
