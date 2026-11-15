# Local upgrade launch helpers

Run from normal PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\try-upgrade.ps1
```

These helpers are for the existing local development setup. They reuse the
server data at `D:\Dev\lifetime-test-server-live` and the desktop data in the
current user's normal Lifetime profile. They are not installers.

Only the server child requests UAC elevation. A running backend is reused;
otherwise it starts on ports 8443/8444. Startup reuses a hostname or IP from the
existing certificate without replacing its identity or disabling TLS checks.
Both GUI restart helpers target Python GUI processes; the backend and machine
reporting agent stay running. Logs go into the ignored `.qa` directory.

The connection GUI now distinguishes an unreachable enrollment attempt from
an acknowledged request awaiting approval. Pair with a fresh code from the
currently running server when replacing an old server connection.

Focused helper checks (temporary data / mocked process control):

```powershell
python scripts/test_manual_launch.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test_restart_helpers.ps1
```

The broader upgrade's focused tests pass. The full suite was run once and
aborted after 556 of 651 result markers (4 skips) with Windows native exit
0xC0000374. Remaining checks passed individually, with additional native Qt
shutdown crashes/hangs in some GUI runs. A clean full-suite pass is not claimed.
