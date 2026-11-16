# Lifetime Server operations (Windows and Linux)

Lifetime Server is a separate, administrator-operated edition for collecting explicitly enrolled machine reports over HTTPS on a trusted LAN. It is not the Lifetime desktop and does not replace it. Existing Windows and Linux desktop installation and support remain as described in the root README.

## Scope and deployment status

The server consists of a reporting runtime and a native Qt dashboard. Its HTTPS endpoint uses a self-signed certificate whose key every computer pins; its separate administration API listens on loopback. Administrators approve pending enrollment in the dashboard and create expiring enrollment bundles. Devices make no company requests until a user explicitly pairs them and enables reporting. Optional history and resource fields are opt-in. Remote sleep, restart and shutdown exist but stay off on every computer until its owner turns on remote power control (see [server-specs-control.md](server-specs-control.md)); there is no other remote administration.

Install only on a dedicated Windows machine on a trusted, restricted LAN. Use a dedicated local install directory and data directory. The installer applies protected ACLs for Administrators and SYSTEM and refuses linked paths. Do not expose the loopback administration port or publish the HTTPS port to the public internet. The installer always adds one inbound rule, `LifetimeServer-Connect`, for the HTTPS port from `LocalSubnet` on Domain and Private networks only; computers on a Public network cannot reach the server until the network is reclassified or you add a rule on purpose. `-OpenFirewall` adds a second rule with the remote addresses you choose. If needed, restrict remote addresses to the intended LAN.

Development verification has been performed on Windows 11 with Python 3.12, headless Qt, and local simulated clients using real HTTPS/TLS and SQLite. Windows Server service-control-manager behavior, production ACL behavior, firewall behavior, and reboot/startup behavior have not been tested. No Windows Server version is claimed as tested. Treat this package as requiring a supervised pilot and validate it in the intended environment before relying on it operationally.

## Build and package

Run from a Windows checkout. `build_server_windows.ps1` builds a separate timestamped `server-package-*` directory containing `LifetimeServerRuntime.exe`, `LifetimeServerDashboard.exe`, `LifetimeAgent.exe`, source modules, installers, documentation, and `SHA256SUMS.txt`. It installs the server build dependencies into the selected Python environment. The optional `-IncludeServiceWrapper` builds the experimental service wrapper; SCM installation has not been validated. The default build does not include that wrapper. Build artifacts are local; this workflow does not publish releases or deploy servers.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_server_windows.ps1 -Python C:\Path\To\Python312\python.exe
```

Keep the package directory intact and verify its manifest before transfer:

```powershell
Get-Content .\SHA256SUMS.txt
Get-FileHash .\LifetimeServerRuntime.exe -Algorithm SHA256
```

## Install and start

Extract the package outside the intended install directory. From an elevated PowerShell prompt, set the HTTPS origin to the actual LAN DNS name or IP that clients will use. The port in `-Address` must match `-Port`; public HTTPS defaults to 8443 and loopback administration defaults to 8444.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_server_windows.ps1 -Address https://lifetime-host.example:8443 -Mode SourceTask
```

Modes are `SourceService` (default, Windows service wrapper), `SourceTask` (Python runtime as a SYSTEM startup scheduled task), and `PackagedTask` (packaged runtime executable as a SYSTEM startup task). Source modes create a dedicated virtual environment and install `requirements-server.txt` plus PyQt6; pass a machine-wide interpreter with `-Python` if `python` does not resolve outside user profiles. `PackagedTask` requires `LifetimeServerRuntime.exe`. Installation refuses an existing registration: stop and uninstall it before upgrading. Add `-Start` to start the selected registration immediately.

How `SourceService` runs: the service program is the machine-wide `python.exe` itself, started with `-E -s` on `lifetime_server_service.py --service-host <install>\.venv`; the script then loads the environment's packages. (pywin32's default host and a venv's `python.exe`, which is only a launcher for a child process, both fail to start as a SYSTEM service.) The installer runs `lifetime_server_service.py --self-check` first, so a broken environment stops the install with its real error, prints the registered service program, and sets Windows to restart the service after a crash (10 s, 60 s, then every 5 minutes). Start-up errors are written to the Windows Application event log.

To restart or stop the server by hand, in any mode, run `restart_server_windows.ps1` (or `restart_server_windows.ps1 -Stop`) from the install directory in an elevated PowerShell. In task modes do not use `Stop-ScheduledTask` alone: it ends only the first process and leaves the real server running in a child process.

To add an inbound firewall rule deliberately, specify the HTTPS port and an appropriately narrow address scope:

```powershell
.\install_server_windows.ps1 -Address https://lifetime-host.example:8443 -Mode SourceTask -OpenFirewall -FirewallRemoteAddress LocalSubnet
```

The installer creates a dedicated data directory, default `%ProgramData%\Lifetime\Server`, and protected server files. TLS certificate/key and the administrator token are created at first runtime start. Preserve these files and restrict backups. Open **Lifetime Server** from the Start menu on the server machine (the installer adds the shortcut with the data directory and loopback port already filled in, and it asks to run as administrator because the administrator token is protected); the dashboard connects to the loopback administration API. To start it by hand, run `LifetimeServerDashboard.exe --directory <data directory> --admin-port <port>` elevated. Keep that port loopback-only.

For a manual source runtime or dashboard, see the root README commands. The runtime CLI is `lifetime_server.py`; `python lifetime_server.py --help` lists its commands. Service configuration is stored under `HKLM\SYSTEM\CurrentControlSet\Services\LifetimeServer\Parameters`; task mode stores its command in `\Lifetime\Server`. The installer records non-secret selections in `server-install.json`.

## Agent enrollment and operations

The machine agent checks on its own that the server answers (one pinned TLS handshake every 15 s, 10-25 s while it does not; none while the live socket is up or right after a sync) and writes only the result, its time and a category to its status file, for the desktop's **Server: Online / Offline** chip. When the server comes back, waiting reports are sent at once instead of after the sync backoff. The machine agent is separate from the desktop tracker and never imports a per-user ledger implicitly. Install it elevated from the package using `install_agent_windows.ps1`; packaged mode uses `LifetimeAgent.exe`, and source mode creates a standard-library-only Python environment. Reporting requires `-EnableReporting` and exactly one way to reach the server: `-ConnectionLine` (copied from **Add device** on the server), `-PairingCode` with `-Server`, or a `-BundleFile`. Awake time is the only thing shared by default; `-TrackHistory`, `-ResourceReadings`, `-HealthReadings` and `-PowerControl` each have to be named, and only with that explicit enrollment. `-ImportLedger` is a one-time explicit import. `-Start` starts the machine startup task. The agent installer protects its machine data directory and preserves it during uninstall.

### Silent deployment (GPO, Intune, scripts)

The agent installer never prompts, so the same command works from a GPO computer startup script, an Intune Win32 app or a remote shell running as SYSTEM or an administrator. Use the packaged agent and one code with enough uses for the batch (**Add device** > Allowed uses, up to 100, valid up to 24 hours):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_agent_windows.ps1 -Mode Packaged -EnableReporting -ConnectionLine "lifetime-connect-v1:https://lifetime.school.local:8443 LTS-XXXX-XXXX-XXXX-XXXX-XXXX" -TrackHistory -Start
```

- Every parameter is checked before anything is changed, including the agent's own check of the code (checksum), address and display name. The code reaches the agent through standard input, not its command line.
- Exit codes: `0` installed; `1` unexpected failure; `2` invalid parameters; `3` server unreachable, or not the server this code belongs to; `4` code refused by the server (used up, expired or revoked); `5` not run as administrator or not Windows; `6` an agent is already installed (nothing changed; uninstall first to change its settings). After a failure no startup task is registered and existing data is kept.
- A log is appended to `install.log` in the agent data directory (default `%ProgramData%\Lifetime\Agent`, readable by administrators and SYSTEM only). It records the steps, the sharing choices and the exit code, never the code or connection line. A failure before the data directory exists is logged to `%TEMP%\LifetimeAgent-install.log`.
- Each computer still appears under **Pending approval** and reports only after an administrator approves it. Treat the deployment script as a secret while its code is valid, and revoke the invitation when the rollout is done.
- Not yet verified on real GPO or Intune deployments: run a pilot group first and check the exit codes in your tool.

The source agent CLI is `lifetime_agent.py`. Useful explicit options are `--help`, `--initialize`, `--run`, `--directory`, `--enroll-bundle-file`, `--connection-stdin` (a connection line, or a code with `--server`, read from standard input), `--check` (validate only), `--policy-history`, `--policy-resources`, `--policy-updates`, and `--import-ledger`. Optional policy flags require explicit enrollment. Approve the pending device in the server dashboard. No automatic approval or remote administration is provided.

### Agent updates (staged)

**Administration > Agent updates** offers one approved agent package to computers whose owner allowed updates.

1. **Upload package**: choose the file (for example the new `LifetimeAgent.exe` from a release), type its version and the SHA-256 published with the release (`SHA256SUMS.txt`). The server copies the file into `updates\` in its data directory only when its SHA-256 matches what you typed, keeps at most this package and the previous one, and refuses files from its own data directory. Packages are up to 64 MiB. A new package starts offered to **0 %** of computers.
2. **Offered to N % > Save share**: start small (10 %), then raise it. Each computer has a fixed number from 0 to 99 for each package, so the same computers stay in the first share. **Pause** stops new downloads; computers keep what they already downloaded. **Roll back** offers the previous package to every computer again (computers accept the older version only because it is marked as a rollback) or, without a previous package, withdraws the offer; computers then delete a copy they have not installed.
3. A computer takes part only when its owner switched on *Get Lifetime updates approved by the server* on the Connect page (or the installer was given `-AllowUpdates`, or the agent `--policy-updates`). It is off by default, it is not part of what the computer shares, and switching it off deletes a downloaded copy at the next check. Every 15 minutes such a computer asks its server (over the same pinned connection as its reports) whether there is a package for it, downloads it in 512 KiB pieces, checks the size and SHA-256 the server stated and, on Windows, the Authenticode signature when the file carries one (an invalid or untrusted signature is refused; a file without a signature relies on the SHA-256). Only then is the file kept as `updates\package-<name>` in the agent data directory with `updates\staged.json`. An older version than the running one is refused unless it is a rollback. **Nothing is ever run by the agent.**
4. **Installing** is left to the existing installer: `install_agent_windows.ps1 -StagedUpdate` (Packaged installs with an `.exe` package) checks the staged file's SHA-256 again (and its signature if the agent found a valid one), stops the agent task, keeps the old program as `LifetimeAgent.previous.exe`, copies the new one in and starts the task. Run it from a computer startup script (GPO or Intune, as SYSTEM) so the update installs at the next restart without prompts. A replaced agent that reports the offered version is shown as *Installed*. For other packages (a zip, Source installs) use the normal uninstall and install.
5. The list shows each computer's last report: *Ready to install*, *Installed* or *Failed* with the reason (download failed, wrong size, SHA-256 did not match, signature not valid, older version refused, could not be saved).

Not yet verified on real machines: `-StagedUpdate` from a GPO or Intune startup script and a real signed release. The download, checks, rollout, pause and rollback are tested against a server on 127.0.0.1. Packages are not in backup archives: upload the package again after a restore.

## Alerts and notifications

The server evaluates alert rules every 15 seconds from what computers already report (the live connection, reports and their readings). It does not ping or probe computers that run Lifetime; [agentless checks](#agentless-checks) reach only the devices and addresses you add yourself. Everything below is under **Administration** in the dashboard; incidents are on the **Incidents** page.

**Rules.** Each built-in rule has an on/off switch, a threshold where one applies and a duration in minutes: *Computer offline* (default 15 minutes, counted only inside expected online hours), *No report received* (1440 minutes, inside expected hours), *Disk almost full* (free below 10 % for 30 minutes), *Processor busy* (above 95 % for 15 minutes), *Memory almost full* (above 95 % for 15 minutes), *Battery worn* (health below 60 %), *Running hot* (above 90 °C for 5 minutes, only where the computer reports a temperature), *Repeated crashes* (3 or more in 30 days, from Health), *Lifetime version too old* (off until you enter a minimum version) and *Network check failing* (an [agentless check](#agentless-checks) that failed 3 times in a row; the minutes add a minimum time). A reading must stay past the threshold for the whole duration; small movements back inside a hysteresis band (for example 10 points for the processor) do not end the problem; and a problem must stay away 5 minutes before its incident resolves, with the recovery time recorded as when it actually cleared. Rules use only what each computer shares: without Readings consent no reading rule applies, crashes need Health consent, and readings that are missing or older than the overdue window are shown as *Telemetry missing* in the computer's detail without opening or closing anything.

**Expected online hours.** Choose for everyone, a group (the computer's Department, typed) or one computer (picked by name and group from the approved computers): *Automatic* (the default: desktops always, laptops never), *Always*, *Never* or *Business hours* with opening and closing times, weekdays, holidays (YYYY-MM-DD) and a time zone. The time zone is `local` (the server's own), `UTC`, a fixed offset such as `+07:00`, or an IANA name such as `Asia/Ho_Chi_Minh`. Windows Python has no time zone database of its own: `requirements-server.txt` installs `tzdata` (pure data, no native code) into the server's virtual environment and the packaged runtime includes it. Without it an IANA name is refused with a message saying so (the dashboard shows it under the time zone field) while `local`, `UTC` and offsets keep working; a name saved earlier is used as server time instead and marked *time zone unavailable* in the list until `tzdata` is back. A computer that does not share Readings cannot be recognised as a laptop or desktop and is treated like a laptop by *Automatic*: give it a schedule if it should be watched. Offline time is counted from the opening time, so a computer switched off overnight opens an incident N minutes after opening if it is still off; coming back resolves at any hour.

**Maintenance windows** (everyone, a group or one computer, from one minute to 31 days): no alert opens and no message is sent for a problem that starts during the window. If it is still there when the window ends, the incident opens then, with a message; if it clears during the window it is closed quietly. Incidents already open stay open.

**Incidents.** One incident per continuous problem (computer, rule and cause). States: *Starting* (not yet past its duration; not shown as a current problem), *Needs attention*, *Acknowledged* (the dashboard records the Windows account that clicked Acknowledge, and when), *Paused by maintenance* and *Resolved*. A server restart continues the same incident and never sends its message again. After a restart a computer that last reported before the downtime shows as unknown until it has had a full reporting window to come back; the downtime itself is recorded as unknown, never as offline.

**Email.** Turn on *Email alerts* and enter the mail server, port, *STARTTLS* (usually port 587) or *TLS* (usually 465), the user name and password if the server needs them, the sender address and up to 20 recipients. A plain, unencrypted connection is never used: a server that does not offer STARTTLS fails with *Secure connection failed*. The certificate is checked against the system's trusted roots and the server name, so use the name on the mail server's certificate. **Send test email** sends one message now, without creating an incident, and shows the result. Each incident sends one message when it opens and one when it recovers, in the chosen message language. A failed message is retried after 1, 5, 15 and 60 minutes and then marked failed; the **Delivery log** shows queued, sent and failed messages with a category (could not connect, timeout, secure connection failed, sign-in refused, address refused, settings incomplete, interrupted by a server stop, server error NNN), never the message text or the password. A message that was being sent when the server stopped is marked *interrupted* and not sent again, so a restart never sends a duplicate.

**Daily summary** (off by default): one message a day after the chosen hour (server time) with the counts and the open incidents. **Webhook** (off by default): a JSON POST to an `https://` address, with a `text` field for Teams, Slack or Telegram bridges and the event, computer, group, rule and times. Redirects are not followed and only HTTPS is accepted.

Messages name the computer and its group, the problem, the last reading and the times. They never contain activity history, readings history, addresses, codes or credentials. Not yet verified against a real mail server or webhook service: send a test message after configuring and watch the delivery log during a pilot.

## Agentless checks

For printers, routers, NAS boxes and websites that do not run Lifetime, add checks under **Administration > Network checks**:

- **Ping**: one echo request with the operating system's `ping` (no shell, a fixed argument list, on an address that has already passed the network policy). Windows runs `ping -n 1 -w <timeout ms> <address>` and counts a reply only when it contains `TTL=` (Windows also exits successfully for some "destination unreachable" replies); Linux and other systems run `ping -c 1 -W <timeout s> <address>` and count exit status 0. Some networks and hosts drop ping: use a TCP check there.
- **TCP port**: a connection to a host and port (for example a printer on 9100, a NAS on 445 or 5000).
- **Web page (HTTP or HTTPS)**: GET or HEAD, an expected status range (200 to 399 by default), an optional keyword that must appear in the page (GET only), certificate verification on by default (turn it off only for a device with its own self-signed certificate), up to 3 redirects by default (0 to 5) and a response size cap (256 KiB by default; a larger response fails as *Response too large*).

Each check has a name, an optional group (maintenance windows for that group and *everyone* pause it), an interval (30 seconds to a day, 60 by default), a timeout (1 to 30 seconds, 5 by default) and an on/off switch. The list shows each check's last result (*Up*, *No reply*, *Connection refused*, *No answer (timeout)*, *Name not found*, *Secure connection failed*, *Unexpected status 404*, *Keyword not found*, *Response too large*, *Blocked by the allowed networks*), how many times in a row it has failed, its latency and when it ran. Changing what a check targets starts its results again.

**Allowed networks.** Checks reach only the networks listed under *Allowed networks*: `private` (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 and fc00::/7; the default), `public` (internet addresses; add it to watch websites) and explicit ranges such as `192.168.10.0/24`. Loopback and link-local addresses are refused unless an explicit range names them; cloud metadata addresses (169.254.169.254, fd00:ec2::254), this server's admin port, unspecified, multicast and reserved addresses are always refused. A name is resolved again for every attempt and every redirect, every address it resolves to must be allowed, and the check connects to that checked address. See [SECURITY.md](../SECURITY.md#agentless-checks-and-the-network-policy).

**Load.** All checks run in one worker with at most 16 attempts at a time, at least 2 seconds between two attempts to the same host and port, a little random jitter on every interval, a deadline on every attempt and a queue of at most 1,000 results that the monitor writes every 15 seconds (scheduling waits while it is full). At most 500 checks. A failing check opens one incident through the *Network check failing* rule, named after the check and its group, with the usual notifications; a success starts its recovery.

Not yet verified against real printers, routers, NAS boxes or websites, nor with ping on Linux: tests use servers on 127.0.0.1 that they explicitly allow.

## Availability and reports

**Availability** comes from the presence history the server records (online, offline, unknown) and counts only each computer's *expected online hours*. Every second of those hours is *observed online*, *observed offline*, *unknown* (the server was not running, the computer had not reported yet, or monitoring had not started) or *maintenance* (excluded). Observed availability = online / (online + offline), shown only when something was observed; coverage = (online + offline) / (online + offline + unknown). Below the **minimum coverage for availability** (80 % by default, under *Expected online hours*) a report says *Insufficient evidence* and shows the observed figure beside it; a computer with no expected hours (a laptop under *Automatic*) is *Not expected online*. Unknown time is never counted as up or down. Days and periods use each computer's calendar: the time zone of its expected-hours schedule (computers do not report their own zone), so a day is 23 or 25 hours across a DST change; reports name the zone and whether it came from the computer's, its group's or the default schedule. Groups and the fleet add up seconds before dividing. Monitoring keeps 180 days, so a longer period has unknown time at its start.

**Reports** (**Export report** on the header, **Export fleet** under Administration, or a selection of computers): choose all available data or a date range (for example one month), all computers or one group, then preview and save PDF or CSV. Next to the existing awake time (from each computer's own record, unchanged) a report adds, in separate sections: availability with coverage and the online, offline, unknown and maintenance seconds (per day for one computer), the incidents in the period, a health summary from what the computer shares (battery health and charge cycles, crashes and unexpected shutdowns in 30 days, system disk free) and whether it is a **replacement candidate**, with each reason stated against the alert thresholds (battery health below the *Battery worn* threshold, less free disk than *Disk almost full*, at least the *Repeated crashes* count). Every report names its data source and calendar time zone, and every PDF page carries a footer with the Lifetime Server version, time zone, source and page number. Reports are in the dashboard's language (English or Vietnamese). The availability figures are measured up to the moment of the preview, so the saved file matches what was reviewed.

Monitoring data lives in `monitoring.sqlite` in the data directory, written only by the running server: presence history (online, offline or unknown, with a reason), agentless checks with their last results, and incidents for 180 days, at most 64 MiB (the oldest closed history goes first), and the last 5,000 delivery log entries. Removing a computer also removes its monitoring history.

## Energy, after hours, replacements and health

The **Energy** page (admin operation `insights`, readable by every role) computes everything when it is opened, from
what each computer shares; nothing is stored except the settings and one disk/battery reading per computer and day.

- **Energy** = awake hours (the computer's daily record) x power. Power is the mean of the computer's on-battery
  discharge readings when it has at least 3 in the last week (the server now keeps a battery reading as a power sample
  only while discharging; a charging battery reports its charge rate), else the wattage set for the computer or its
  group, else the typical laptop (20 W) or desktop (80 W) wattage; a computer of unknown kind is treated as a desktop.
  The price (default 2,500 VND per kWh) and currency are settings (`set_insight_settings`, Admin, audited).
- **After hours** uses the computer's hourly awake record (its own wall-clock hours; a night is split at midnight, a
  daylight-saving day has an empty or double hour) against its business hours, or the **office hours** setting when its
  schedule is Automatic, Always or Never. An hour cut by the opening or closing time is shared by minutes. Older
  computers that do not send hourly history count as unknown.
- **Groups** are a computer's Group (department) field. **Manage groups** on the Devices page (operations `groups`,
  readable by every role; `add_group`, `rename_group`, `remove_group`, Admin, audited with the group name) creates
  groups before any computer is in them, so they can be picked when approving a computer or in **Edit details** from a
  device's menu. Renaming a group moves its computers and, in monitoring, its expected hours, maintenance windows,
  after-hours sleep policy, wattage and agentless checks. Removing forgets a created group; a group stays listed while
  a computer is in it. A backup taken before this release still restores (its copy gains the new table first).
- **Sleep after hours** (`set_sleep_policy`, Admin): only a named Windows account with an Admin or Operator role
  assigned to the account itself can turn it on (not the token, and not membership of Administrators alone, which the
  server cannot re-check later). Every 15 seconds the server looks at that group's computers whose owner allows remote
  power and, 30 minutes after their hours close, sends one ordinary Sleep command per computer per 12 hours through the
  same path as a person: the account's role is checked again, the per-computer (3 / 10 min) and per-account (10 / 10 min)
  limits apply, and the audit trail records it as "<account> (after-hours policy)". It skips computers that are offline,
  already have a command waiting, or whose latest processor reading is 25 % or more (Lifetime cannot see a keyboard or
  mouse). Removing the account's role stops it at once. The estimated monthly saving is this week's after-hours energy
  of computers that allow remote power x 30/7. Not tested on real machines.
- **Replacement plan**: battery health comes from the computer's battery report history (Health with Readings);
  system disk free space from one reading per computer and day that monitoring keeps in `monitoring.sqlite` (table
  `trends`, two years, included in backups, removed with the computer and as soon as it stops sharing readings). A
  least-squares line gives the date the battery falls below the replacement level (default 80 %) or the disk below the
  free-space level (default 10 %), only with at least 4 readings over 30 days. Unit prices for batteries and disks are
  optional; parts without a price are counted, not priced.
- **Health score**: the badge's score (good 100, needs a look 50, poor 0, averaged) over stability, battery, system disk
  free, temperature and agent version (against the newer of this server's version and the approved update package).
  Unknown parts are listed and left out; group and fleet scores average the known computers and say how many are
  unknown.

Reports add the same sections (Energy and cost with the method, after hours, forecasts and the plan, health score).

## Named administrators and roles

On Windows the dashboard signs in as the Windows account that opens it. The server reads the account from its local
named pipe (`\\.\pipe\LifetimeServer-admin-<admin port>`); the dashboard cannot claim a name. **Administration >
Roles and access** shows who is signed in and with which role.

- **Admin**: everything. Members of Administrators running the dashboard *as administrator* and SYSTEM are always
  Admin; an administrator who opens it without elevation counts only through a role given to their account or a group.
- **Operator**: view everything; acknowledge incidents; add and remove maintenance windows; ask for a fresh report;
  sleep, restart, shut down or turn on one computer at a time where its owner allowed remote power.
- **Auditor**: view everything and the audit trail.
- **Viewer**: view only.

To give a role, an Admin types a Windows account or group (`SCHOOL\helpdesk`, `helpdesk`, a local group, or a SID)
under **Roles and access**, chooses the role and clicks **Give role**; the server resolves it to a SID. A group role
applies to its members at their next sign-in (Windows puts group membership in the logon token). Removing a role
takes effect at that account's next request; removing or lowering a role that allowed remote power also cancels
every power command still waiting. People with a role other than Admin run the dashboard without elevation:
`LifetimeServerDashboard.exe --directory <data directory> --admin-port <port>` (or the source command); the Start menu
shortcut asks for elevation, which only Administrators need.

The older loopback channel with `admin-token` still works (Linux, older dashboards, an account the pipe does not
admit). Whoever can read that file is Admin and appears as `admin-token` in the audit trail. Remote power is not
available through it, because it cannot say who is asking; on Linux, send remote power from a Windows dashboard or
not at all. If the server log shows *Named administration channel unavailable (token only)*, another program held the
pipe name at start: find it, then restart the server.

Remote power limits: at most three power commands (sleep, restart, shutdown, Turn on) per computer and ten per
account in ten minutes; a command expires ten minutes after it was issued; one computer per request.

## Audit trail

**Administration > Audit trail** (Admin and Auditor) lists the latest entries: every change made through the
dashboard or API and every remote command, plus every refused request, with the Windows account, role, action,
target, result and time. **Verify audit trail** walks the hash chain and reports the first broken entry (changed,
removed, reordered or newest entries removed); from a prompt on the server, `lifetime_server.py verify-audit
--directory <data directory>` does the same without the dashboard (exit code 0 when intact). **Export CSV** saves the
whole trail with each entry's hashes: keep exports outside the server, because a local administrator can rewrite the
file and recompute the chain. The trail lives in `access.sqlite` with the roles. After 200,000 entries the oldest
20,000 are written to `audit-archive\audit-<first>-<last>.jsonl` in the data directory (Administrators and SYSTEM
only) and then removed; keep or copy those files under your retention policy.

## Backup and restore

**Save backup** (Admin) writes one archive, for example `lifetime-server-backup.zip`, and `lifetime-server-backup.zip.sha256`
beside it. The archive holds consistent copies (SQLite backup API, taken while the server runs) of `server.sqlite`
(computers, reports, settings, groups created ahead of time), `monitoring.sqlite` (alert history, incidents, rules, schedules, maintenance windows,
checks, notification settings) and `access.sqlite` (roles and the audit trail), the TLS identity (`server-key.pem`,
`server-cert.pem`, `server-cert.previous.pem` when present), the server's configuration (address, ports, version and
`server-install.json` when found), and a manifest with each file's SHA-256 and size. It never contains `admin-token`
or `alert-secrets.bin` (the mail password and webhook address, which only the account that wrote them on that machine
can read: enter them again after restoring on another machine). The archive is created readable only by
Administrators and SYSTEM because it holds the private key every computer pins: store copies just as restricted, and
separate from the server host. Keep the two files together.

**Verify backup** checks an archive without changing anything: the `.sha256` file, the list of files, each file's
hash, every database (it must be one this server version can restore), the audit chain inside it and that the key
matches the certificate. `lifetime_server.py verify-backup --directory <data directory> --archive <file>` does the
same from a prompt.

**Restore backup** checks the archive the same way and stages it (`restore-staged.zip` in the data directory); it is
applied the next time the server starts, before any computer or dashboard can connect: the key and certificate first
(the replaced ones are kept as `*.before-restore.pem`), then the databases through the same validated restore as
before, which invalidates every computer credential, pairing code and polling token (computers need a new code and
approval). The current roles and audit trail are kept; the archive's `access.sqlite` is used only when the server has
none (a new machine). Monitoring history that was open in the backup ends there and the time after it is unknown. If
the staged archive no longer checks out at start, the server starts with its current data, renames it
`restore-failed.zip` and logs *Staged backup refused at start*. To cancel a staged restore before the restart, delete
`restore-staged.zip` from the data directory. Never copy an old `server.sqlite` over the live one by hand: that skips
the credential invalidation.

The older single-database **backup** and **restore** admin operations (and `<name>.monitoring.sqlite` files from
earlier versions) still work for scripts, Admin only.

### Restore drill

Practise this on a spare machine or VM before you need it, and after each upgrade:

1. On the live server, **Save backup** to removable or network storage that only administrators can read, then
   **Verify backup** on the copy you will keep.
2. On the drill machine, install the same Lifetime Server version (`install_server_windows.ps1`, same address if
   possible) and do not pair any computer.
3. From an elevated prompt in the install directory, stage the archive while the server is stopped:
   `python lifetime_server.py restore --directory C:\ProgramData\Lifetime\Server --archive D:\backup\lifetime-server-backup.zip`
   (packaged: `LifetimeServerRuntime.exe restore ...`). It prints a JSON report and exits 0 when staged.
   Alternatively start the empty server, open the dashboard as administrator and use **Restore backup**, then
   restart it with `restart_server_windows.ps1`.
4. Start the server. **Administration > Server log** shows *Backup restored at start*; the **Audit trail** shows
   `restore-applied`; **This Server** shows the restored key fingerprint; Devices lists the computers as
   disconnected.
5. Check **Verify audit trail**, the alert rules, schedules and checks, and re-enter the mail password and webhook
   address under **Notifications**.
6. Pair one test computer with a new code to prove the restored identity works, then discard the drill machine (or,
   in a real recovery, re-pair the remaining computers).

Not yet verified on a real Windows Server or with a real disaster: the drill above is covered by automated tests that
back up, restart and restore a server on one Windows 11 PC.

## Retention, certificate and addresses, upgrade, rollback

Use the dashboard's retention controls to inspect and set server retention. Confirm the selected policy before applying it; removed server records cannot be recovered without a prior backup. Previously exported files are outside server retention. Disabling a reporting category prevents future collection/transmission and removes its server snapshot at the next permitted sync.

**Certificate renewal needs no action and no re-pairing.** The server's identity is its private key, `server-key.pem`. The certificate made from it is valid for 397 days; the server issues a new one from the same key 30 days before expiry (checked at start and every hour) and keeps the previous one as `server-cert.previous.pem`. Computers pin the key, so they keep reporting. **This Server** shows when the certificate expires and its key fingerprint; if a renewal fails, Overview and Administration show a warning and the old certificate stays in use until it expires, so check disk space and the server names. Pairing codes created before a renewal stop matching; create new ones.

**Address changes need no re-pairing either.** Prefer a DNS name in `-Address` (for example `lifetime.school.local`), so an IP change needs nothing. Under **Administration > Other server names and IPs** add or remove names and IPs (comma separated, up to 16) that computers may use; saving renews the certificate at once from the same key with every name. A computer that knows only an old address that no longer answers looks for the server with local discovery (same subnet only) and accepts the new address only if it presents the same key; across routed networks or VPNs, keep the old name working or add the new name before moving. Computers that are never updated from Lifetime 1.1 pin the whole certificate: they stop at the first renewal or name change and need updating (or pairing again), so update them before you change names. An updated computer takes the key pin from the certificate it already stores, so it keeps working through a renewal even if it has not reported since its update.

**Server log and last problem.** **Administration > Server log** lists the most recent 500 server events: starts and stops, certificate renewals and failures, name changes, and refused reports or connection attempts by category (repeats within a minute are counted). Each computer's detail shows its **Client version** and **Last problem**: why its last attempt failed, as reported by the computer once it reaches the server again (for example "No answer (timeout)"), or a report the server itself refused. Neither keeps report contents, codes, credentials or IP addresses.

**Re-pairing is needed only after the key is lost or stolen.** Then create a new identity: stop the runtime, move `server-key.pem`, `server-cert.pem` and `server-cert.previous.pem` out of the data directory into a restricted offline place, and start the runtime again; it creates a new key and certificate. Create new codes and pair every computer again (Disconnect, then Connect). Old pins must fail. Keep the old files until the new pairing is verified, then destroy a stolen key's copies.

For an upgrade, take and verify a backup, stop the service/task, run `uninstall_server_windows.ps1`, then install the new package with the same dedicated data directory and settings. Uninstall removes only the service/task registration; program files, data, certificates, token, and backups remain. The installer refuses an existing registration and does not automatically migrate or delete retained data.

For rollback, stop and unregister the current version, restore the previous package and compatible configuration, and retain the current data backup. Restore a compatible saved database through that version's validated restore operation and re-enroll devices. Do not assume a newer schema can be opened by an older runtime. Do not replace the live database with a raw old copy that resurrects revoked credentials.

```powershell
.\uninstall_server_windows.ps1
# Optional: also remove the installer-created inbound rules (LifetimeServer-Connect and, if you added it, LifetimeServer-HTTPS).
.\uninstall_server_windows.ps1 -RemoveFirewallRule
```

Uninstall does not remove server files or retained customer data. Review and remove those separately only under the organization's data-retention process. The desktop and machine-agent task are not changed by the server uninstaller.

## Linux server

Lifetime Server also runs on Linux: the same runtime, protocol, pairing, pinning, renewal, alerts, checks, reports, backup and insights, headless, as a self-contained binary built on glibc 2.28 (Debian 10+, Ubuntu 20.04+, RHEL/AlmaLinux/Rocky 8+, Fedora, openSUSE Leap 15.3+, Arch; x86_64 and aarch64). No desktop, Qt or system Python is needed. Musl distributions (Alpine) use the container image instead. Windows and Linux computers pair with a Linux server exactly as with a Windows server.

**Verification status.** CI builds the bundles in a Rocky Linux 8 container. In Debian 12 and Rocky Linux 9 containers it installs the server with `install_server_linux.sh` (SysV fallback, because containers run no systemd), administers it as root over the Unix socket, enrolls a client with a pairing code, syncs, restarts and uninstalls it. The bare binary is started and paired on all eleven distributions in the README, and the container image is built, run as a non-root user and paired. The systemd unit, the OpenRC script, `ufw`/`firewalld`, start-up after a reboot and real servers have **not** been tested: check them in a pilot (VM checklist in the release notes).

### Install, upgrade, remove

```sh
tar -xzf Lifetime-Server-Linux-x86_64.tar.gz && cd Lifetime-Server-Linux-x86_64
sudo ./install_server_linux.sh --address https://lifetime.school.lan:8443 --admin alice
```

| What | Where |
|---|---|
| Program | `/opt/lifetime-server` (an upgrade keeps the previous version in `/opt/lifetime-server.previous`) |
| Data, key, certificate, token, databases, `alert-secrets.bin` | `/var/lib/lifetime-server`, mode 0700, owned by the system account `lifetime` |
| Settings (address, host, ports) | `/etc/lifetime-server/server.conf` (root:lifetime 0640); change it, then `sudo ./restart_server_linux.sh` |
| Admin socket | `/run/lifetime-server/admin-8444.sock`, mode 0660, group `lifetime-console` (may connect; no role by itself) |
| Log | `journalctl -u lifetime-server` (systemd) or `/var/log/lifetime-server.log` (OpenRC, SysV) |

`--init auto` (the default) uses systemd when it is running (`lifetime-server.service`, `Restart=on-failure`, hardened with `NoNewPrivileges`, `ProtectSystem=full`, `UMask=0077`), otherwise OpenRC (`supervise-daemon` restarts it), otherwise a SysV script (`update-rc.d` or `chkconfig`; SysV does not restart a crashed server). `--init none` installs the files and prints the command line. Running the installer again upgrades in place, stopping and starting the service; the data stays. Rollback: `sudo mv /opt/lifetime-server /opt/lifetime-server.bad && sudo mv /opt/lifetime-server.previous /opt/lifetime-server && sudo ./restart_server_linux.sh` (same schema rules as on Windows). `sudo ./uninstall_server_linux.sh` removes the program and service and keeps data and settings; `--purge` also removes the data, settings, account and groups.

**Firewall.** The installer prints, never applies, the rule for `ufw` or `firewalld`: allow the HTTPS port (8443) from your local subnet only, for example `sudo ufw allow from 192.168.1.0/24 to any port 8443 proto tcp`. The administration port (8444) listens on 127.0.0.1 only and must never be opened or forwarded.

### Administrators and roles on Linux

The Windows named pipe is replaced by a Unix domain socket. The kernel tells the server which Unix account connected (`SO_PEERCRED`); a client cannot name itself. `root` and members of the `lifetime-admin` group are always Admin (like SYSTEM and elevated Administrators); every other user or group has no access until given a role (Admin, Operator, Auditor, Viewer: same permissions, default deny, same tamper-evident audit trail, with actors recorded as `unix:uid:N` and their user name). A caller's groups are read from the user database at each request, so adding someone to a group or removing them applies at once. A caller without any role is disconnected before its request is read.

The socket is 0660 with group `lifetime-console`. Membership of `lifetime-console` only lets an account **connect**; it grants no role. The installer creates both groups and puts the `lifetime` service account in them (`--admin USER` puts USER in both). To give someone a lower role:

```sh
sudo usermod -a -G lifetime-console alice          # may reach the socket (takes effect at alice's next sign-in)
sudo /opt/lifetime-server/LifetimeServerRuntime admin --directory /var/lib/lifetime-server --admin-port 8444 \
    --operation set_role --arguments '{"principal": "alice", "role": "operator"}'
```

Until the second step alice is disconnected unread, like anyone without a role. Members of `lifetime-admin` who should use the socket need `lifetime-console` too (the installer adds the existing `lifetime-admin` members when it first creates `lifetime-console`, so an upgrade keeps their access). Other group names: set `LIFETIME_CONSOLE_GROUP` / `LIFETIME_ADMIN_GROUP` in the service's environment (systemd: `sudo systemctl edit lifetime-server`, `[Service]` `Environment=LIFETIME_CONSOLE_GROUP=helpdesk`); without a console group the socket falls back to the admin group, as before. Principals for `set_role`: a user name, `group:NAME`, or `unix:uid:N` / `unix:gid:N`. Remote power still needs a named account (never the token), rate limits and the owner's consent.

Whoever can read `/var/lib/lifetime-server/admin-token` (root and the `lifetime` account) is Admin over the loopback HTTP channel, as on Windows.

### Administering a server

On the server: `sudo /opt/lifetime-server/LifetimeServerRuntime admin --directory /var/lib/lifetime-server --admin-port 8444 --operation OPERATION [--arguments JSON] [--field NAME]`, for example `--operation invitation --field code` for a pairing code, `--operation pending_enrollments`, `--operation approve --arguments '{"request_id": "..."}'` or `--operation backup_archive --arguments '{"path": "/var/backups/lifetime.zip"}'`. It goes through the same roles, validation and audit trail as the dashboard (this command line also works on Windows).

**Dashboard on a Linux desktop** (a server on the same computer): `Lifetime-Linux-x86_64.AppImage --run-tool LifetimeServerDashboard --directory /var/lib/lifetime-server --admin-port 8444` (or `~/.local/lib/lifetime/LifetimeServerDashboard ...` after `install_linux.sh`), as a member of `lifetime-console` with a role, or of `lifetime-admin` and `lifetime-console` (sign in again after being added). It uses the socket. Without access to the data folder it does not remember its theme and cannot create "Other network?" bundles; use invitations.

**Headless server, dashboard on an admin's Linux computer**: forward the socket over SSH. The server sees the SSH account and applies its role.

```sh
mkdir -p ~/.lifetime-remote && chmod 700 ~/.lifetime-remote
ssh -N -L ~/.lifetime-remote/admin-8444.sock:/run/lifetime-server/admin-8444.sock admin@lifetime.school.lan &
LIFETIME_ADMIN_SOCKET_DIR=~/.lifetime-remote ./Lifetime-Linux-x86_64.AppImage --run-tool LifetimeServerDashboard \
    --directory ~/.lifetime-remote --admin-port 8444
```

From Windows there is no supported remote path yet (the Windows dashboard uses the pipe or the token): use SSH and the command line, or a Linux desktop or VM. A network-reachable admin interface is deliberately not provided; see the proposal in docs/ROADMAP_STATUS.md.

### Secrets on Linux

The SMTP password and webhook URL are stored in `/var/lib/lifetime-server/alert-secrets.bin`, a 0600 file owned by `lifetime` inside the 0700 data folder. This is file-permission protection, **not** encryption like DPAPI on Windows: root, the `lifetime` account, anyone who can read the disk or a disk image, and any copy of the data folder can read them. Backups made by the dashboard never include this file (as on Windows); enter the secrets again after a restore. Use a dedicated mail account and webhook token for alerts.

### Container image (any Linux with Docker or Podman)

```sh
docker build -t lifetime-server .            # in a source checkout
docker run -d --name lifetime-server --restart unless-stopped -p 8443:8443 \
    -e LIFETIME_ADDRESS=https://lifetime.school.lan:8443 -v lifetime-data:/var/lib/lifetime-server lifetime-server
docker exec lifetime-server lifetime-admin --operation invitation --field code
```

The image (Debian slim, Python 3.12, `requirements-server.txt`, no Qt) runs as the non-root `lifetime` account, which is in `lifetime-admin` and `lifetime-console` inside the container, so `docker exec` commands are Admin over the socket. Data lives in the volume (`/var/lib/lifetime-server`, 0700); back it up with `--operation backup_archive` into the volume and copy it out. With a bind mount instead of a volume, create the folder owned by the container's `lifetime` uid first. Only 8443 is published; 8444 stays inside the container. Local network discovery (mDNS) does not cross Docker's bridge network: pair computers with the connection line ("Other network?" / the invitation and the address) or run with `--network host`. Podman works with the same commands. The image is not published to a registry (owner decision).

## CI

The dedicated server workflow runs unit tests on Linux and Windows with Python 3.11 and 3.12, installs PyQt6 and `cryptography`, and uses Qt's offscreen platform. It does not execute Windows service installation, SCM operations, firewall changes, or reboot tests. The Linux jobs build the server and desktop bundles in Rocky Linux 8 and run the container smoke tests described under [Linux server](#linux-server); they do not run systemd, OpenRC, firewalls or reboots. CI is validation only: it does not publish a release or automatically deploy.
