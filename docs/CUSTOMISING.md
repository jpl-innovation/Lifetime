# Customising Lifetime (for programmers)

Lifetime is a Python program (Python 3.11 or newer, PyQt6 for the windows). The packaged programs that employees
install are built from this same code, so anything changed here can be shipped. This page is the map.

## Run it from source

```sh
python -m pip install PyQt6            # the desktop app needs only PyQt6
python lifetime_gui.py                 # the desktop app
python lifetime_tracker.py --run       # the background tracker on its own
python -m pip install -r requirements-server.txt PyQt6
python lifetime_server.py --help       # the server runtime
python lifetime_server_gui.py          # the server dashboard
```

Use a separate data folder while experimenting, so your own record is never touched:
`set LIFETIME_DATA_DIR=C:\temp\lifetime-dev` (Windows) or `export LIFETIME_DATA_DIR=/tmp/lifetime-dev` (Linux).

## Where things are

| You want to change... | Look in |
|---|---|
| How awake time is counted | `lifetime_tracker.py` (samples every 30 s), `lifetime_ledger.py` (the record file and its checks) |
| Off time, restarts, crashes per day | `lifetime_downtime.py` |
| Reading history from Windows / Linux logs | `lifetime_import_windows.py`, `lifetime_import.py`, `lifetime_signals.py` |
| The desktop window and its pages | `lifetime_gui.py` (window, pages), `lifetime_shell.py` (sidebar), `lifetime_uptime_gui.py` (date picker, Uptime & downtime page), `lifetime_charts.py` (Activity charts), `lifetime_health_gui.py`, `lifetime_system_gui.py`, `lifetime_insights_gui.py` |
| Settings and first-run questions | `lifetime_settings.py`, `lifetime_setup.py` |
| Colours, Classic and Glass styles | `lifetime_theme.py`, `lifetime_glass.py`, animations in `lifetime_reveal.py` |
| Words in English and Vietnamese | `lifetime_i18n.py` (every visible text is translated here) |
| PDF and CSV reports | `lifetime_reports.py` (desktop), `lifetime_server_reports.py` (server) |
| Connecting a computer to a server | `lifetime_connect.py` (Connect page, Diagnose connection), `lifetime_connection.py` (codes, finding the server, certificates), `lifetime_discovery.py`, `lifetime_tls.py` |
| The background agent on a connected computer | `lifetime_agent.py`, `lifetime_client.py` |
| What computers may send the server (security boundary) | `lifetime_protocol.py` |
| The server | `lifetime_server.py` (runtime, Self check), `lifetime_server_store.py` (database), `lifetime_monitor.py` (online/offline, alerts, retention), `lifetime_availability.py`, `lifetime_uptime.py` |
| The server dashboard | `lifetime_server_gui.py` (window), `lifetime_server_uptime.py` (date range, uptime bars), `lifetime_server_*.py` for each section |
| Roles and the audit trail | `lifetime_access.py`, `lifetime_admin.py`, `lifetime_pipe.py` (Windows), `lifetime_unix_admin.py` (Linux) |
| Installers and builds | `install_*.ps1`, `install_*.sh`, `uninstall_*`, `build_windows.ps1`, `build_server_windows.ps1`, `packaging/linux/` |

## Rules that keep it safe

- **Never weaken `lifetime_protocol.py`.** It checks everything a computer sends; a server must refuse anything odd.
- **Every visible text in both languages.** Add the English text where it is shown and the Vietnamese one in
  `lifetime_i18n.py`; `test_i18n` fails if one is missing.
- **Labels are plain text.** Names come from other computers and must never be read as HTML.
- **Unknown is never zero.** If there is no evidence for a day, show it as unknown.
- **Nothing is sent without consent.** Each kind of data has its own switch on the Connect page.
- **New modules** must be added to the install lists (`install_windows_common.ps1`, `install_linux.sh`) and, if
  imported only inside a function, to the build scripts; `test_installed_modules` checks this.
- **Stay in Python.** Rust or other native code only where a measurement shows Python is too slow, with a Python
  fallback and only once releases are code-signed (see `docs/UPGRADE_PLAN.md`).

## Tests

Each `test_*.py` file tests one area. Run one at a time (the full suite needs a lot of memory):

```sh
set QT_QPA_PLATFORM=offscreen
python -m unittest test_downtime
```

GitHub runs every test on Windows Server and Ubuntu, builds every package and installs it on 11 Linux distributions
for each change you push. A release is published only when all of them pass.

## Making a release

1. Change the version in `lifetime_protocol.py` (`CLIENT_VERSION`) and `lifetime_reports.py` (`VERSION`).
2. Add a section at the top of `RELEASE_NOTES.md` (its first section becomes the release text).
3. Push, wait for every check to pass, then push a tag such as `v1.3.0-rc1` (a test version) or `v1.3.0`.
