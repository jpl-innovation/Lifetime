# Technician and server edition verification

Based on upstream `58d37a92a7c3551e8713551258d940363e572973` in
`jpl-innovation/Lifetime`, preserving its desktop tracker, consent, System,
Insights, Verify, EN/VI catalogue and Windows/Linux installation conventions.

## Executed checks

Development platform: Windows 11 x64, Python 3.12, PyQt6.

- Baseline: 135 tests passed. Final combined command:
  `.venv-server/Scripts/python.exe -m unittest discover -q`:
  **405 tests run, 401 passed, four platform-specific skips**. Skips cover POSIX
  permissions and timezone APIs unavailable on Windows. Tests use synthetic
  ledgers, temporary SQLite databases and simulated clients, not customer data.
- Real localhost HTTPS tests exercise strict certificates, hostname verification,
  pin mismatches, enrollment, lost acknowledgments, revocation, restarts,
  concurrent clients, malformed requests, bounded queues and retention.
- Recovery, migration, interrupted saves, preserved consent, import idempotency,
  clock/boot boundaries, missing history, Unicode, CSV formula protection, fresh
  preview permissions and unknown-versus-zero reports have regressions.
- Windows ACL APIs are explicitly mocked, not production installation checks.
  Atomic DACL replacement precedes secondary ACL enforcement.
- Headless desktop, Company panel and Server screens were inspected in English
  and Vietnamese, narrow/normal windows, at 100% and 200% scaling.
- A synthetic three-page Vietnamese fleet PDF was reopened in Chrome and visually
  inspected. Diacritics and embedded fonts rendered correctly. CSV was reopened
  with Python's CSV reader. Exports leave source data and tracking unchanged.
- PyInstaller built desktop, tracker/importer, agent, server runtime and dashboard
  executables. Runtime and agent `--help` smoke checks passed. No installer,
  service, startup task or firewall rule was run during development.
- All PowerShell files passed the parser; whitespace checks passed. CI defines
  Windows/Linux Python 3.10–3.13 tests and a separate server artifact build.
- The packaged report smoke harness passed with real consent gates, EN/VI,
  embedded-font PDF/CSV and unchanged synthetic settings/ledger. Git Bash
  `bash -n install_linux.sh` passed; Bash was not on the default PATH.

Intermediate HTTP response, TLS shutdown, unbounded history-query, Windows
consent-lock and Qt blank-PDF regressions were corrected before final validation.

## Data and design tradeoffs

- Awake time does not prove human work, hardware health or remaining lifespan.
  Measured, reconstructed and undated baseline counters remain distinct.
  Continuous coverage is never inferred from first and last timestamps.
- Daily records use saved device-local date labels, not one common fleet UTC day.
  PDF/preview show at most 90 daily rows; CSV includes all selected retained rows.
  Server queries are paginated. Fleet stocks use one snapshot per distinct device,
  with unavailable contributions disclosed rather than replaced by zero.
- Settings preserve unknown extensions. A previous non-text `location` extension
  moves to an unused `_legacy_location` key; the optional text field starts empty.
  Invalid settings fail closed, and edits refuse to overwrite malformed files.
  Migration/export never grants consent.
- Per-user and machine ledgers remain separate. Explicit one-time imports require
  reliable boot boundaries and are idempotent. Offline outbox loss is counted;
  latest source totals are retained. Reporting requires explicit enrollment.
- Disabled optional categories are never sampled/sent. Stopping local reporting
  does not delete existing server data or previously exported customer files.
- Restore invalidates credentials and pending enrollment material. TLS certificate
  and configuration backup is a separate restricted operational step.

## Remaining manual deployment checks

Actual Windows Server operation, SCM/packaged-service installation, production
ACLs, firewall rules, startup before login/after sign-out, real reboots, sleep,
hibernation, Fast Startup and restricted-user logs remain unverified. No Windows
Server version is claimed as tested. Linux runtime has CI coverage defined; it
was not executed locally in this Windows environment.

Follow [SERVER_OPERATIONS.md](docs/SERVER_OPERATIONS.md) in an isolated deployment:
check LAN certificate identity, pairing/approval/revocation, offline reconnect,
startup tasks, read-only public status, backup/restore, retention, upgrades and
uninstall with retained data. Preserve backups before upgrade testing.

Launch the desktop with `python lifetime_gui.py`. Separate server launch/build/
install commands are in the README and operations guide. The work is on
`feature/technician-server-editions`; no release tag or deployment is included.
