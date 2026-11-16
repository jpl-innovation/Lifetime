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
