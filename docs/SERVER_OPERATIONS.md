# Lifetime Server operations (Windows)

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

The machine agent is separate from the desktop tracker and never imports a per-user ledger implicitly. Install it elevated from the package using `install_agent_windows.ps1`; packaged mode uses `LifetimeAgent.exe`, and source mode creates a standard-library-only Python environment. Reporting requires `-EnableReporting` and exactly one way to reach the server: `-ConnectionLine` (copied from **Add device** on the server), `-PairingCode` with `-Server`, or a `-BundleFile`. Awake time is the only thing shared by default; `-TrackHistory`, `-ResourceReadings`, `-HealthReadings` and `-PowerControl` each have to be named, and only with that explicit enrollment. `-ImportLedger` is a one-time explicit import. `-Start` starts the machine startup task. The agent installer protects its machine data directory and preserves it during uninstall.

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

The source agent CLI is `lifetime_agent.py`. Useful explicit options are `--help`, `--initialize`, `--run`, `--directory`, `--enroll-bundle-file`, `--connection-stdin` (a connection line, or a code with `--server`, read from standard input), `--check` (validate only), `--policy-history`, `--policy-resources`, and `--import-ledger`. Optional policy flags require explicit enrollment. Approve the pending device in the server dashboard. No automatic approval or remote administration is provided.

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

## Backup and restore

Stop the server registration before making a consistent backup. Copy the complete data directory, including `server.sqlite` and its SQLite sidecar files if present, `monitoring.sqlite` (alert history and settings) with its sidecar files and `alert-secrets.bin`, plus `server-key.pem` (the identity every computer pins), `server-cert.pem`, `admin-token`, and `server-install.json` from the install directory. `alert-secrets.bin` can be read only by the account that wrote it on that machine (normally SYSTEM): after moving to another machine, enter the mail password and webhook address again under **Notifications**. The dashboard's **Save backup** writes the database backup and, beside it, `<name>.monitoring.sqlite` with monitoring history, incidents, rules, schedules, maintenance windows, checks and notification settings (never `alert-secrets.bin`). **Restore backup** checks both files first and then restores both; history that was open when the backup was taken ends there and the time after it is unknown, and messages that were still waiting are marked interrupted rather than sent. Keep the two files together: without the monitoring file beside it, a restore brings back the database only and keeps the current monitoring data, and the dashboard says so. After restoring on another machine, enter the mail password and webhook address again. Keep copies access-controlled and separate from the server host. The database contains device reports and enrollment state; TLS identity and token are security-sensitive.

For a database restore, use **Administration > Restore backup** against the running local service. This validates the backup and atomically invalidates device credentials, codes and polling tokens; plan to re-enroll devices. A raw filesystem copy of an old database does **not** perform that invalidation and must not be used as the final restore step. For disaster recovery, first restore the TLS identity/configuration with the service stopped and restrictive ACLs, start a new empty server database, then use the dashboard restore operation on the saved database. Keep the newly generated local administrator token. Verify the dashboard and a test client before resuming collection. Do not merge unrelated histories.

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

## CI

The dedicated server workflow runs unit tests on Linux and Windows with Python 3.11 and 3.12, installs PyQt6 and `cryptography`, and uses Qt's offscreen platform. It does not execute Windows service installation, SCM operations, firewall changes, or reboot tests. CI is validation only: it does not publish a release or automatically deploy.
