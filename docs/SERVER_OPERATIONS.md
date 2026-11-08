# Lifetime Server operations (Windows)

Lifetime Server is a separate, administrator-operated edition for collecting explicitly enrolled machine reports over HTTPS on a trusted LAN. It is not the Lifetime desktop and does not replace it. Existing Windows and Linux desktop installation and support remain as described in the root README.

## Scope and deployment status

The server consists of a reporting runtime and a native Qt dashboard. Its HTTPS endpoint uses a pinned certificate bundle; its separate administration API listens on loopback. Administrators approve pending enrollment in the dashboard and create expiring enrollment bundles. Devices make no company requests until a user explicitly pairs them and enables reporting. Optional history and resource fields are opt-in. The product has no remote-control or remote-command feature.

Install only on a dedicated Windows machine on a trusted, restricted LAN. Use a dedicated local install directory and data directory. The installer applies protected ACLs for Administrators and SYSTEM and refuses linked paths. Do not expose the loopback administration port or publish the HTTPS port to the public internet. Firewall access is not created unless explicitly requested; if needed, restrict remote addresses to the intended LAN.

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

To add an inbound firewall rule deliberately, specify the HTTPS port and an appropriately narrow address scope:

```powershell
.\install_server_windows.ps1 -Address https://lifetime-host.example:8443 -Mode SourceTask -OpenFirewall -FirewallRemoteAddress LocalSubnet
```

The installer creates a dedicated data directory, default `%ProgramData%\Lifetime\Server`, and protected server files. TLS certificate/key and the administrator token are created at first runtime start. Preserve these files and restrict backups. Launch the native dashboard from the installed package on the server machine; it connects to the loopback administration API. Keep that port loopback-only.

For a manual source runtime or dashboard, see the root README commands. The runtime CLI is `lifetime_server.py`; `python lifetime_server.py --help` lists its commands. Service configuration is stored under `HKLM\SYSTEM\CurrentControlSet\Services\LifetimeServer\Parameters`; task mode stores its command in `\Lifetime\Server`. The installer records non-secret selections in `server-install.json`.

## Agent enrollment and operations

The machine agent is separate from the desktop tracker and never imports a per-user ledger implicitly. Install it elevated from the package using `install_agent_windows.ps1`; packaged mode uses `LifetimeAgent.exe`, and source mode creates a standard-library-only Python environment. Reporting requires both `-EnableReporting` and a selected server `-BundleFile`. Optional `-TrackHistory` and `-ResourceReadings` require that explicit enrollment. `-ImportLedger` is a one-time explicit import. `-Start` starts the machine startup task. The agent installer protects its machine data directory and preserves it during uninstall.

The source agent CLI is `lifetime_agent.py`. Useful explicit options are `--help`, `--initialize`, `--run`, `--directory`, `--enroll-bundle-file`, `--policy-history`, `--policy-resources`, and `--import-ledger`. Optional policy flags require an enrollment bundle. Review the bundle fingerprint and address before pairing, then approve the pending device in the server dashboard. No automatic approval or remote administration is provided.

## Backup and restore

Stop the server registration before making a consistent backup. Copy the complete data directory, including `server.sqlite` and its SQLite sidecar files if present, plus `server-cert.pem`, `server-key.pem`, `admin-token`, and `server-install.json` from the install directory. Keep copies access-controlled and separate from the server host. The database contains device reports and enrollment state; TLS identity and token are security-sensitive.

For a database restore, use **Administration > Restore backup** against the running local service. This validates the backup and atomically invalidates device credentials, codes and polling tokens; plan to re-enroll devices. A raw filesystem copy of an old database does **not** perform that invalidation and must not be used as the final restore step. For disaster recovery, first restore the TLS identity/configuration with the service stopped and restrictive ACLs, start a new empty server database, then use the dashboard restore operation on the saved database. Keep the newly generated local administrator token. Verify the dashboard and a test client before resuming collection. Do not merge unrelated histories.

## Retention, certificate rotation, upgrade, rollback

Use the dashboard's retention controls to inspect and set server retention. Confirm the selected policy before applying it; removed server records cannot be recovered without a prior backup. Previously exported files are outside server retention. Disabling a reporting category prevents future collection/transmission and removes its server snapshot at the next permitted sync.

Certificate rotation requires new certificates, not merely a new pairing code. Stop the runtime and preserve its old certificate/key in a restricted offline backup. With the server Python, call `lifetime_tls.generate_certificate(empty_directory, 'https://YOUR-SERVER:8443')` in a separate empty directory; it refuses to overwrite an identity. Move the old identity out of the runtime directory and copy in the new `server-cert.pem` and `server-key.pem`, preserving restrictive ACLs. Restart the service, create new enrollment bundles in the dashboard, and re-pair clients after checking the new fingerprint. Old pins must fail. Do not discard the backup before the new pairing plan is verified.

For an upgrade, take and verify a backup, stop the service/task, run `uninstall_server_windows.ps1`, then install the new package with the same dedicated data directory and settings. Uninstall removes only the service/task registration; program files, data, certificates, token, and backups remain. The installer refuses an existing registration and does not automatically migrate or delete retained data.

For rollback, stop and unregister the current version, restore the previous package and compatible configuration, and retain the current data backup. Restore a compatible saved database through that version's validated restore operation and re-enroll devices. Do not assume a newer schema can be opened by an older runtime. Do not replace the live database with a raw old copy that resurrects revoked credentials.

```powershell
.\uninstall_server_windows.ps1
# Optional: remove only the explicitly named installer-created firewall rule.
.\uninstall_server_windows.ps1 -RemoveFirewallRule
```

Uninstall does not remove server files or retained customer data. Review and remove those separately only under the organization's data-retention process. The desktop and machine-agent task are not changed by the server uninstaller.

## CI

The dedicated server workflow runs unit tests on Linux and Windows with Python 3.10 through 3.13, installs PyQt6 and `cryptography`, and uses Qt's offscreen platform. It does not execute Windows service installation, SCM operations, firewall changes, or reboot tests. CI is validation only: it does not publish a release or automatically deploy.
