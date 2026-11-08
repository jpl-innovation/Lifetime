# Lifetime company reporting protocol (version 1)

This is an optional LAN service. The standalone desktop performs no company
requests. Enrollment is an explicit technician action; no employee account is
required. There are no remote command, restart, installation or control messages.

## Trust and administrators

The enrollment string contains the explicit HTTPS origin, expiring pairing code,
full self-signed certificate, and SHA-256 certificate fingerprint. Including the
certificate allows normal hostname and strict chain verification on the very first
connection, without an insecure certificate download. Clients compare the actual
peer fingerprint **before sending credentials or a request body**. Redirects and
environment proxy discovery are not used. Changing the certificate requires
re-pairing; do not replace a certificate silently.

The public HTTPS listener accepts enrollment, enrollment polling and authenticated
sync. Administration uses a separate loopback listener and protected bearer token.
Windows Administrators and SYSTEM are the server administrators. A local account
without access to that token is not an administrator. Keep that token, database,
private key and client credential out of exports and application logs.

## Enrollment and immutable records

Create a short-lived, bounded-use code in the dashboard. The client first persists
a random device UUID, request UUID and random polling secret, then submits an
enrollment request. Repeating the same request consumes no additional code uses.
The administrator approves or denies the pending request. Approval permits the
polling client to obtain its own random credential. Only its hash is stored on the
server. A lost credential response can be retried until the first authenticated
sync completes enrollment. Revocation blocks both sync and outstanding claims.
Re-enrollment preserves the device's sequence namespace and server metadata.

Each usage record contains a monotonically increasing device-local `seq`, an aware
ISO-8601 event timestamp, measured and imported duration increments in seconds,
their combined `awake_seconds`, and cumulative source `totals`. Baseline is an
undated estimate in the stock totals; it is never turned into a measured increment.
The primary key is `(device_id, seq)`. Exact repeats are acknowledged. A changed
record at an existing sequence is a conflict and rolls back the whole batch.
Out-of-order records do not replace a newer cumulative snapshot.

**Never sum cumulative snapshots.** A current device total is its latest source
snapshot; a fleet total sums one such snapshot per distinct device. Period totals
need attributable dated records. Unknown values are not zero. Event time and
server receipt time are stored separately; neither proves that a person worked.

## Optional information and limits

The core policy contains independent `history` and `resources` permissions and a
monotonic policy version. Optional dated history and whitelisted system readings
are separate from immutable usage records. Disabled categories are rejected on
input and absent from server results. A newer revocation clears their stored
snapshot; stale policy versions cannot enable them again. Permission changes cannot
erase a report that a technician previously saved outside the application.

Identity fields are optional display name, asset tag, department, location and
deployment date. The enrollment values seed the server record. Thereafter server
edits own that metadata; usage sync does not overwrite it. No serial number,
username, IP address, MAC address or Wi-Fi name is accepted by this protocol.

JSON is UTF-8, versioned, with a 1 MiB limit and at most 100 records per sync.
Duplicate JSON object keys, nonfinite numbers, invalid dates, unrecognized fields
and unrecognized protocol versions are rejected. Durations are nonnegative
seconds. Timestamps include an offset; daily labels, when supplied, represent
the recording computer's calendar dates rather than an inferred UTC day.

## Storage and recovery

SQLite uses WAL, full synchronization and transactional schema migrations. A
backup uses SQLite's backup API, including committed WAL data. Restore validates
database integrity and the supported schema before replacement. Restoring a
backup invalidates credentials, polling secrets and pairing codes: devices must
be re-enrolled. This prevents an older backup from resurrecting revoked access.
Post-backup history can be missing; no continuous coverage is inferred.

Retention removes records strictly before a configured event-time boundary and
keeps a retired sequence floor. Late resubmission below that floor is acknowledged
as retired, not reinserted or counted again. The latest cumulative snapshot remains
available with its original timestamp. Clock errors can affect event-time
retention; receipt timestamps remain separate evidence. Database backup does not
by itself include the TLS key/certificate or runtime configuration: keep a separate
restricted offline copy of those files when planning disaster recovery.

The Windows service wrapper, installer ACLs, firewall configuration and actual
Windows Server operation need separate installation checks. Simulated local HTTPS
tests are not proof that those deployment steps succeeded.
