# Roadmap status (Stages 0-5)

Status of every item of the merged master plan (15 Nov 2026) on `latest-main`. Baseline before the stages: `46bf982`.
Everything marked done here was released in Lifetime 1.2.0 (17 November 2026). The final verification before
that release is in [qa/final-verification.md](qa/final-verification.md).

**Status words**

- **implemented**: the code or document exists.
- **tested synthetic**: covered by automated tests on one Windows 11 PC (servers on 127.0.0.1, simulated computers,
  stubs for mail and webhooks). This is not proof on real networks or real fleets.
- **needs real machines**: the remaining check can only be done on real computers, servers, networks or services.
- **owner action**: needs a decision, money, legal work or people (the owner's job, not code).

An item can carry several words: for example *implemented, tested synthetic; needs real machines* means the code is
done and tested here, and the gate still needs a real-world run.

## Owner decisions recorded

- **Remote power needs a named account.** Sleep, restart, shutdown and Wake-on-LAN from the dashboard are
  accepted only on the identity-bound channel (a named Windows account on the pipe, or since the Linux stage a named
  Unix account on the `SO_PEERCRED` socket) with a role that allows it, never over the anonymous `admin-token`
  channel. Kept deliberately for safety: every power command must name the person who sent it. (Stage 3, `146e6ce`;
  Linux stage.)
- **Supported size = tested size.** No fleet size is claimed beyond what has been measured
  ([benchmarks](benchmarks/README.md)); today 50 simulated computers. (Stage 4, `d780550`.)
- **Agent updates stop at download, check and stage.** The agent never runs a package; the existing installer
  (`install_agent_windows.ps1 -StagedUpdate`) installs it, because a safe self-replacement without prompts and with a
  way back could not be verified on real machines. (Stage 4, `2bc02a0`.)
- **Four roles** (Admin, Operator, Auditor, Viewer) were built instead of the leaner Admin/Viewer pilot subset.
  (Stage 3, `146e6ce`.)

## Stage 0: foundation and trust

| # | Plan item | Status | Commits |
|---|---|---|---|
| 1 | Licensing: Qt route, project license, third-party notices | implemented (GPL-3.0-only, matching PyQt6; notices from package metadata). **owner action**: legal review. | `7e218cf` |
| 2 | Service mode: Windows service not starting; compare modes; reboot/autostart | implemented (service runs the machine-wide interpreter, self-check before registering; installer and uninstaller fixes), tried on one Windows Server 2025 VM. **needs real machines**: reboot and autostart on Windows Server 2022 and 2025. | `f97caee`, `475769a`, `2ff06a4`, `512474d`, `8967675`, `ae0ca88`, `c21959b`, `d5a599e` |
| 3 | Agent/package parity: rebuild packages, new SHA and signature | implemented (packaged agent no longer loses its files after approval; builds write SHA256SUMS). **owner action**: build and publish a release; sign it. | `3985d3e` |
| 4 | Diagnostics: categorized errors on Connect and in a server log | implemented, tested synthetic | `89dc205`, `3cf334e` |
| 5 | Versioning: old agent/new server and new agent/old server | implemented, tested synthetic (capability lists, rolling upgrade tests) | `3fb5498` |
| 6 | Certificate identity: multi-name certificates, renewal without re-pairing, address changes, offline agents | implemented, tested synthetic (key pinning, renewal from the same key, rediscovery that requires the pinned key; back-dated certificates on loopback). **needs real machines**: renewal and address change on a real network. | `c55db0a`, `f1c43fd`, `f02de66`, `95b0b9f` |
| 7 | Signing and reproducibility: signed binaries, checksums, SBOM, no secrets | partly: checksums and third-party notices exist. **owner action**: code-signing certificate and signing; secrets audit before any publish. Not done: SBOM. | `7e218cf` |
| 8 | Accuracy and regression: VM rollback, sleep/hibernate/Fast Startup, isolated ledger, flaky tests | partly: flaky waits replaced, ledger isolation kept by every stage (monitoring never writes the ledger). **needs real machines**: sleep, hibernate, Fast Startup and VM rollback matrix on real hardware. | `2645b9a` |
| 9 | Docs match the code and state what is untested | implemented, kept up to date in every stage | `dad9055`, `95b0b9f`, `7ba110a`, `7eb8313`, `587342e` |
| 10 | Mass deployment: safe silent install and enrollment | implemented, tested synthetic (exit codes, secret on standard input, log without secrets). **needs real machines**: real GPO and Intune. | `17a4c47` |
| Gate | Clean install and reboot on Server 2022/2025, renewal and address change with an offline agent, mixed versions, signed artifact, license approval, **10 real computers for 7 days** | not passed: **needs real machines** and **owner action** (signing, license review) | |

## Stage 1: alerts and incidents

| # | Plan item | Status | Commits |
|---|---|---|---|
| 1 | Monitoring persistence, one writer, retention and quota; server downtime = unknown | implemented, tested synthetic | `4a6c646` |
| 2 | Rule engine: nine rules, sustained thresholds, hysteresis, telemetry missing never fires | implemented, tested synthetic | `9446a8d` |
| 3 | Expected online hours and maintenance windows; laptops not expected by default | implemented, tested synthetic; time zones without a system database | `e8a7a42`, `5cf2fd5` |
| 4 | Incidents: one per problem, states, acknowledge, no duplicates after restart | implemented, tested synthetic | `46274fb` |
| 5 | Email (STARTTLS/TLS only), retries, delivery log, daily summary, webhook; protected secrets | implemented, tested synthetic (local SMTP stub, fake webhook). **needs real machines**: real mail servers and chat services. | `8d88b5e` |
| 6 | Dashboard: Incidents page, badges, device history, Administration sections (EN/VI, Glass/Classic) | implemented, tested synthetic (offscreen Qt) | `edc00a8` |
| 7 | Golden cases: unplug, reconnect, restart, flapping, laptop asleep, SMTP down, consent off | tested synthetic (injectable clock) | `c10beac` |
| 8 | Docs | implemented | `7ba110a` |
| Gate | Unplug a real test device: exactly one incident and email; restart, Wi-Fi loss, sleep, opt-out and SMTP outage cause no floods | **needs real machines** (the same cases pass synthetically) | |

## Stage 2: availability, reports, agentless checks

| # | Plan item | Status | Commits |
|---|---|---|---|
| 1 | Availability engine: good, bad, unknown, excluded; coverage floor; time zones and DST | implemented, tested synthetic (hand-calculated golden cases) | `9e2b3fe` |
| 2 | Reports per computer, group and fleet, PDF and CSV, awake time beside availability (EN/VI) | implemented, tested synthetic. **needs real machines**: printed PDFs. | `2fa3813`, `441ce63` |
| 3 | Agentless ping, TCP, HTTP(S) checks with one bounded worker and an SSRF policy | implemented, tested synthetic (127.0.0.1 only). **needs real machines**: real printers, routers, NAS boxes, websites; ping on Linux. | `095be9b` |
| 4 | Tests: result matrix, concurrency and backpressure, golden cases, report contents | tested synthetic | `384c113` |
| 5 | Docs | implemented | `7eb8313` |
| — | Backups include monitoring history | implemented, tested synthetic | `c083765` |
| Gate | Golden cases pass; capacity exercise 50 devices x 5 checks for **seven days** | golden cases pass. Capacity: only a 2-minute run of 50 simulated computers with 10 checks (Stage 4). **needs real machines** for a seven-day run. | |

## Stage 3: named administration and audit

| # | Plan item | Status | Commits |
|---|---|---|---|
| 1 | Named Windows identities through a local named pipe; token kept for Linux and older dashboards | implemented, tested synthetic (one Windows 11 PC). **needs real machines**: domain accounts and groups, non-elevated dashboards on a real Windows Server. | `146e6ce`, `198aaba` |
| 2 | Roles: Admin, Operator, Viewer, Auditor, enforced on the server, default deny | implemented, tested synthetic (role x action matrix) | `146e6ce`, `b4cb83a` |
| 3 | Audit trail with hash chain, verify, CSV export, bounded retention with archive | implemented, tested synthetic | `146e6ce`, `198aaba` |
| 4 | Remote power hardening: named actor, expiry, exact confirmation, rate limits, consent, revocation wins | implemented, tested synthetic. Owner decision above: not available over the token (Linux). | `146e6ce`, `b4cb83a` |
| 5 | Backup and restore: one protected archive, checksum, verify, staged restore, restore drill | implemented, tested synthetic (round trip on one PC). **needs real machines**: the restore drill on a second machine. | `146e6ce`, `b4cb83a` |
| 6 | Tests | tested synthetic | `146e6ce` |
| 7 | Docs | implemented | `587342e` |
| Gate | Role matrix, viewer changes nothing, power revocation race, audit and restore valid, spoofing tests | passes synthetically; **needs real machines** for domain identities | |

## Stage 4: scaling and controlled updates

| # | Plan item | Status | Commits |
|---|---|---|---|
| 0 | Packaging check: every new module in installers, builds and CI; hidden imports for lazily imported modules; tzdata collected | implemented, tested (static test of import graph against build and install lists; PowerShell parsed). **needs real machines**: a full PyInstaller build and smoke test of the packaged runtime. | `f323da8` |
| 1 | Load-test tool, 50/200/500 computers, server CPU/RAM, SQLite/WAL, live latency, drops, incident lag; one 50-computer run committed | implemented; run once at 50 computers for 2 minutes on one PC. **owner action**: 200 and 500 on a bigger machine (documented). | `fad916b`, `3ce4a10` |
| 2 | Fix scaling problems found | implemented, tested synthetic (query-plan tests): week-old performance samples trimmed per computer through an index, the live hub asks once for waiting commands. The 50-computer run itself showed no problem. | `14ae250` |
| 3 | Staged agent updates: approved package, consent switch (off), pinned download, SHA-256 and signature checks, rollout share, pause, rollback, reports | implemented, tested synthetic (end to end over HTTPS on 127.0.0.1; Authenticode valid/none/tampered). Final install left to `install_agent_windows.ps1 -StagedUpdate`. **needs real machines**: a startup-script install. **owner action**: signed releases (packages are verified by SHA-256 only until then). | `2bc02a0` |
| 4 | Measured limits doc: supported size = tested size | implemented | `d780550` |
| Gate | Supported count equals measured count; no unbounded memory or disk; ledger invariant | supported = 50 (measured). Bounded stores reviewed (performance samples, monitoring quota, audit retention, event and log caps). 200/500: **owner action**. | |

## Unique features: what true awake time makes possible

| # | Item | Status | Commits |
|---|---|---|---|
| 1 | Energy and cost (measured on battery vs estimated, per computer/group/fleet, price and currency, reports) | implemented, tested synthetic (hand-calculated golden cases). **needs real machines**: real discharge readings vs a wall meter; typical wattages are assumptions. **owner action**: the default price (2,500 VND per kWh) and wattages. | `9db3633` |
| 2 | After hours per computer/room/group, weekly trend, sleep-after-hours policy with estimated saving, desktop note | implemented, tested synthetic (midnight, DST, partial hours, overnight hours; policy role, consent, grace, busy, once a night, revocation). **needs real machines**: the policy on real computers and networks; "busy" is processor load only. | `b2b92c5` |
| 3 | Replacement forecast (battery, disk fill, disk health, age, usage) and plan with budget | implemented, tested synthetic (exact dates, not enough data, reached, flat, beyond ten years). **needs real machines**: months of real readings to judge the straight-line forecast. | `c56b1be` |
| 4 | Health score per computer and averaged per group/fleet with breakdown; unknown parts disclosed | implemented, tested synthetic | `aee5368` |

## Stage 5: pilot and proof kit

| # | Plan item | Status | Commits |
|---|---|---|---|
| 5 | Pilot site checklist, consent/data policy template (EN and VI), runbook, evidence log | implemented (documents). **owner action**: Vietnamese text and policy reviewed by a native speaker and counsel. | `804943a` |
| 6 | Zabbix side-by-side protocol: eight tasks mapped to the guide, scoring sheet, fairness rules, results template | implemented (protocol only, no results) | `3204d89` |
| 7 | Interview script (EN and VI) and summary template | implemented | `7763687` |
| 8 | README, release notes, this status file | implemented | this commit |
| — | Run 3-5 pilot sites with 20-50 computers, side-by-side runs with five independent technicians, 10+ interviews, three paying commitments | **owner action** | |
| Gate | No release-blocking incident; scorecard met or reported as unmet; data/security/licensing plan; paying commitments if launching | not started: **owner action** | |

## Linux stage: every Linux, one desktop build

| # | Plan item | Status |
|---|---|---|
| A | One desktop build: PyInstaller bundle built on glibc 2.28 (Rocky Linux 8), AppImage and tar.gz, Qt xcb and Wayland plugins, `--self-check`, per-user install/uninstall, XDG autostart without systemd, "Not available" without journal/tools, remote power via systemctl/loginctl/D-Bus without prompts | implemented; unit tests on Windows with patching; **CI** has built the bundles and passed the container smoke tests on all eleven distributions (row C); **needs real machines**: real X11/Wayland desktops, autostart, tray, remote power. aarch64 desktop not built (PyQt6 aarch64 wheels need a newer glibc). |
| B | Linux server: headless bundle (x86_64, aarch64), `install_server_linux.sh` (systemd, OpenRC, SysV), restart/uninstall, firewall hint, Dockerfile, Unix socket with `SO_PEERCRED` and role mapping, 0600 secrets file, `admin` command line, dashboard on a Linux desktop, SSH socket forwarding | implemented; role mapping and socket tested with patching here, the real socket only in Linux CI; **needs real machines**: systemd unit, OpenRC, firewalls, reboot, SSH forwarding. |
| C | CI: Rocky 8 build, smoke tests in Debian 10/11/12, Ubuntu 20.04/22.04/24.04, Fedora, Rocky 8/9, openSUSE Leap 15, Arch; installer run without systemd (Debian 12, Rocky 9); container image; release files and SHA256SUMS | implemented and run on GitHub. Run `43846999822` (commit `8966f7f`, 16 Nov 2026) passed everything: all six test jobs, both Linux builds, both Windows packages, the container image, all eleven distributions and both installer runs. The release job runs only on a version tag and has not run for this version. |

**Owner decisions (Linux stage)**

- Socket group (approved and built): the admin socket is 0660 with group `lifetime-console`, which may connect but
  grants no role; roles come only from assignments (default deny); root and `lifetime-admin` stay Admin.
- Proposal, not built: a network-reachable admin interface for headless servers (for example the dashboard over
  mutual-TLS). Today: the command line on the server, the dashboard on a Linux desktop, or the socket forwarded over SSH.
- Publishing the container image to a registry (GHCR) and signing the AppImage are not set up.
- The old Python-source `Lifetime-Linux.tar.gz` is no longer a release file (one Linux client version).
