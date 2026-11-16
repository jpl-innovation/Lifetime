# Incident and support runbook (pilot)

For the site technician and the Lifetime owner during a pilot. Work top to bottom; stop when it is fixed. Record each
case in the [evidence log](evidence-log.md) (what happened, time to detect, time to fix, cause), including user error.
Details behind each step are in [Server operations](../SERVER_OPERATIONS.md).

Severity: **S1** server down or data wrong for everyone; **S2** one or more computers not reporting, alerts not
arriving; **S3** cosmetic, question, wish. S1: tell the site contact and the owner at once. S2: same day. S3: weekly.

## A computer stopped reporting

1. Dashboard: open the computer. Read **Last problem** and its version. Then on the computer itself, open Lifetime >
   **Connect**: it says why in plain words. Match the category:

| The computer or server says | Usual cause | Fix |
|---|---|---|
| No answer (timeout) / not reachable | Computer off or asleep, cable or Wi-Fi, firewall, VPN down | Check it is on and on the network; `Test-NetConnection <server> -Port 8443` from the computer. |
| Connection refused | Server not running, or wrong port | Start the server (`restart_server_windows.ps1`); check the port on the **This Server** page. |
| Name not found | DNS name of the server not resolvable | Fix DNS or use the address the certificate lists; the computer also looks for the server on the local network. |
| The server's certificate is out of date / clock wrong | The computer's clock is wrong, or the server certificate expired | Fix the computer's date and time; on the server check the certificate expiry on the **This Server** page (it renews itself 30 days before expiry). |
| Address not in the server's certificate | The computer uses an address the certificate does not list | Administration > Other server names and IPs: add the name or IP; the certificate renews from the same key, no re-pairing. |
| The server's security certificate has changed | A different server, or the server's key was replaced | Stop. If the key was really lost (new server without a restored backup), pair the computer again. Otherwise someone else answers at that address: investigate. |
| Removed by the server / revoked | The computer was removed or revoked on the server | Pair it again with a new code if it should report. |
| Rejected (invalid) or conflict on the server | A report the server refused (version mix, damaged data) | Note the time, check Administration > Server log, update the computer to the server's version; report it with the log entry. |

2. Still not reporting: restart the agent (restart the computer; ending the *Lifetime\Agent* scheduled task in Task
   Scheduler stops only its first process, so prefer a restart, or reinstall as in step 3).
3. Agent installation problems: read `%ProgramData%\Lifetime\Agent\install.log` and the exit code (2 invalid input,
   3 server unreachable or not this server, 4 code used or expired, 5 not administrator, 6 already installed: add
   `-Reinstall` to replace it and keep its data).

## Many computers stopped at once

1. Is the server running? On the server: `Get-Service LifetimeServer` (or the *Lifetime\Server* task); open the
   dashboard. If not, `restart_server_windows.ps1` from an elevated prompt and read the dashboard's Server log.
2. Server disk full? Check free space on the data drive (`%ProgramData%\Lifetime\Server`).
3. Network change (new IP, DHCP)? Computers using a DNS name continue; computers that stored an IP rediscover the
   server on the local network and accept it only with the same key. Add the new IP under server names if needed.
4. After a restore from backup every computer must be paired again (credentials are invalidated on purpose).

## The dashboard does not open or shows "unavailable"

- Open it from the Start menu shortcut (it runs as administrator) on the server itself; the admin port is loopback only.
- "This Windows account has no role": an Admin gives the account a role under Administration > Roles and access.
- Numbers greyed out: the dashboard cannot reach the server; check that the server runs (above).

## Alerts did not arrive

1. Incidents page: is the incident there? If not, check the rule is on, the computer's expected online hours and
   whether a maintenance window covers it (Administration). A laptop is not expected online by default.
2. If the incident is there: Administration > Notifications > **Delivery log** shows queued, sent or failed with a
   reason (could not connect, timeout, secure connection failed, sign-in refused, address refused, settings incomplete).
   Fix and press **Send test email**. A plain (unencrypted) mail server is never used.

## Too many alerts (noise)

Raise the duration of the noisy rule, give laptops or labs their own expected hours, or add a maintenance window for
planned work. Record every false alarm in the evidence log: it is a pilot result.

## Awake time looks wrong

1. Compare with the computer's own Lifetime desktop app (same numbers expected for the same period) and Windows Event
   Viewer power events. Note the period, both values and the difference in the evidence log.
2. Check the computer's clock and time zone, and whether it was asleep or hibernating (not counted as awake).
3. Report with the evidence; do not edit data on the server.

## Restore drill or server rebuild

Follow [Server operations > Restore drill](../SERVER_OPERATIONS.md#restore-drill). Expected: the dashboard comes back
with all history; computers need a new code and approval; roles and the audit trail are kept on the server where the
restore runs.

## Agent update failed

Administration > Agent updates lists the reason per computer (download failed, wrong size, SHA-256 did not match,
signature not valid, older version refused, could not be saved). SHA-256 or signature failures: stop the rollout
(**Pause**), check the uploaded file against the release's `SHA256SUMS.txt`, and report it. **Roll back** offers the
previous package again.

## Escalate to the Lifetime owner

After 30 minutes without a fix (S1: at once). Send what the [site checklist](site-checklist.md#4-how-to-report-problems)
lists, never tokens, codes, backups or `alert-secrets.bin`.
