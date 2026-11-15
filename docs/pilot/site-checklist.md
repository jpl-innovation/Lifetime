# Pilot site checklist

For a supervised pilot of Lifetime Server at one school or office. The pilot itself (choosing sites, people, contracts)
is the owner's job; this list is what each site needs. Keep one copy per site, filled in, next to the
[evidence log](evidence-log.md).

**Size.** Start at 20-50 computers per site. The measured size is 50 simulated computers on one PC
([benchmarks](../benchmarks/README.md)); do not promise more than has been measured.

## 1. Before anything is installed

- [ ] Named site contact (name, role, phone or chat) and a named technician who will run the install.
- [ ] Written approval from the person responsible for the computers (head teacher, office manager, IT lead).
- [ ] Consent and data policy agreed and shown to the people who use the computers:
      [template in English and Vietnamese](consent-and-data-policy.md). Decide which optional categories are used
      (history, readings, health, remote power, updates). Default for a pilot: awake time and history only.
- [ ] Inventory: number of Windows and Linux computers, laptops versus desktops, who owns each (school, staff, student).
- [ ] Server machine: Windows 10/11 or Windows Server with Python 3.11+ for all users (or the packaged runtime), 2 CPU
      cores, 4 GB RAM and 10 GB free disk are ample at pilot size; a fixed IP or a DNS name (for example
      `lifetime.school.local`); the server stays on during school/office hours at least.
- [ ] Network: computers reach the server on TCP 8443 (LAN or VPN). No inbound internet access is needed.
- [ ] Mail server details if alert email is wanted (host, port, STARTTLS or TLS, sender, recipients).
- [ ] A place for backups that only administrators can read (USB disk, NAS share), and someone who checks it monthly.
- [ ] The release and its `SHA256SUMS.txt` downloaded; checksums verified (`Get-FileHash` on Windows).
- [ ] Baseline noted in the evidence log: what the site uses today (nothing, Excel, Zabbix, an RMM) and how long a
      typical "is this computer used?" question takes them today.

## 2. Install (record times in the evidence log)

1. **Server.** Follow [Server operations > Install and start](../SERVER_OPERATIONS.md#install-and-start):
   `install_server_windows.ps1 -Address https://<name>:8443 -Start` from an elevated PowerShell. Open the dashboard
   from the Start menu. Note the time from unzipping to a working dashboard.
2. **Organisation.** Administration: organisation name, server names and addresses, reporting interval (keep 30 s).
3. **Roles.** Administration > Roles and access: give the site technician Operator if they should not be Admin.
4. **Alerts.** Check the rules (defaults are sensible), set expected online hours (for example business hours
   07:00-17:00 Monday-Friday, Asia/Ho_Chi_Minh, with school holidays), set up email and press **Send test email**.
5. **Computers.** On the server: **Add device**, allowed uses = number of computers in this batch, valid for the day.
   On each computer either the Connect page (type the code, choose what to share, Finish) or, for many computers,
   the silent install from [Server operations > Silent deployment](../SERVER_OPERATIONS.md#silent-deployment-gpo-intune-scripts).
   Approve them under **Pending approval**. Note the time per computer and for the batch.
6. **Backup.** Administration > Save backup to the backup place. Run **Verify backup**.
7. **Agentless checks (optional).** Printers, the router, the NAS: Administration > Network checks.

## 3. Every day (5 minutes, same time each day)

Write one line per day in the [evidence log](evidence-log.md):

- Computers approved / reporting / offline now (Overview), and how many of the offline ones were expected offline.
- Open incidents, and for each: real problem or false alarm, how it was noticed (email, dashboard), time to acknowledge.
- Any computer whose awake time looks wrong to its user (cross-check: the computer's own Lifetime desktop app, or
  Windows Event Viewer power events, for 2-3 computers a week).
- Server: dashboard opens, the header shows no certificate warning, disk free on the server.
- Support time spent on Lifetime that day (minutes) and what for.
- Anything a user or the contact complained about or asked for.

Weekly: verify the backup file exists and **Verify backup** says it is intact; export one monthly report (PDF) and
check it against what the site knows.

## 4. How to report problems

- **Something is broken** (dashboard does not open, computers stop reporting, wrong numbers, an install fails):
  follow the [runbook](runbook.md) first; if it is not solved in 30 minutes, report it.
- **Report to**: the Lifetime owner (JPL Innovation) through the agreed channel (email or chat named in the pilot
  agreement). For security problems use GitHub's private vulnerability reporting as described in
  [SECURITY.md](../../SECURITY.md#reporting-a-vulnerability), never a public issue.
- **Include**: site, date and time, what you did, what you expected, what happened, the server version
  (Administration > This server), screenshots, and the relevant entries from Administration > Server log and the
  computer's Connect page message. Agent install problems: `%ProgramData%\Lifetime\Agent\install.log`.
- **Never include**: the admin-token file, connection codes, backup archives (they contain the server key),
  `alert-secrets.bin`, or personal data beyond what is needed (blur names on screenshots).
- Record every report in the evidence log with its outcome, including problems that turned out to be user error.

## 5. End of pilot

- [ ] Evidence log complete; side-by-side tasks done if this site takes part ([protocol](zabbix-side-by-side.md)).
- [ ] Interview held with the buyer and the technician ([script](interviews.md)).
- [ ] Decision with the site: keep, remove, or extend. To remove: uninstall agents (`uninstall_agent_windows.ps1`),
      remove computers on the server (this deletes their server data), uninstall the server, and confirm in writing
      that backups made during the pilot were deleted or handed over.
