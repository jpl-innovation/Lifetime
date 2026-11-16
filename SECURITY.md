# Lifetime Server connection codes

This flow works over Ethernet, Wi-Fi or VPN. There is no Wi-Fi permission,
SSID, hardware address or employee account in enrollment. The technician must
receive the code (or connection line) from the real administrator through a
trusted channel. An address discovered by multicast is only an untrusted hint.

## Code and trust protocol

`LTS-XXXX-XXXX-XXXX-XXXX-XXXX` contains 19 Crockford base32 payload characters
and one Crockford modulo-37 check character. Hyphens, spaces and letter case
are ignored, `O` means `0`, and `I`/`L` mean `1`. The checksum detects ordinary
single-character typing mistakes; it is not an authentication mechanism.

The payload contains a 25-bit random invitation ID, a 30-bit random secret,
and a 40-bit certificate commitment. The commitment is the first 40 bits of
SHA-256 over `Lifetime connection v1\0`, the certificate's full binary SHA-256
fingerprint, and the ASCII invitation ID and secret. It is encoded as eight
base32 characters. The longer code than the illustrative twelve-character
example is deliberate: the certificate commitment alone needs eight characters.

1. The user chooses a discovered server or enters its name/IP. A connection
   line carries the explicit HTTPS origin and the same code. No URL query,
   redirect, proxy lookup, HTTP fallback or remote lookup service is involved.
2. The client opens a TLS 1.2-or-newer handshake to obtain the certificate.
   This bootstrap handshake does not send HTTP, the code, identity, consent,
   polling credentials or tracking data. The hostname/SNI and network-level
   connection metadata are observable. The certificate commitment must match
   the code, using `hmac.compare_digest`, before proceeding.
3. The fetched certificate becomes the normal enrollment bundle. Subsequent
   connections use the existing strict CA/hostname/validity/extension checks
   (TLS 1.2 or newer, the stored certificate as the only trust anchor) and check
   the pin before sending an HTTP request (see "Pinning the server's key" below).
   The name or IP supplied must match the certificate SAN. If a certificate
   names a DNS host, use that name and arrange normal DNS resolution over the
   VPN; an arbitrary IP alias does not bypass hostname validation.
4. Enrollment, durable polling, administrator approval and credential issuance
   follow the existing protocol. A code never bypasses approval. Exact retries
   are idempotent. Re-enrollment preserves the original UUID/sequence namespace,
   server identity and history. Revocation requires a new invitation and approval
   to resume reporting. Certificate renewals and address changes do not need
   re-pairing; only a new server key does.

The commitment still covers the **whole certificate** (unchanged wire format, so
older and newer computers and servers pair with each other). The certificate fixes
its public key, so the key pin taken from it is exactly as authenticated as the
certificate. A code commits to the certificate in use when it was created: after a
renewal, codes created earlier no longer match and must be replaced (they fail
closed with "this code does not match the server"); the server issues new codes
against its current certificate.

The advanced bundle import retains its original full out-of-band trust path.
Legacy bundle codes retain their original server-wide guessing budget.

## Pinning the server's key, renewal and rediscovery

- **What is pinned.** A computer pins the SHA-256 of the public key
  (SubjectPublicKeyInfo, "key pin") of the certificate it paired with. A computer
  paired before key pins existed stored only that certificate; it computes the key
  pin locally from it. That certificate came through the authenticated pairing (code
  commitment or out-of-band bundle), so this trusts nothing new, and such a computer
  keeps working through the server's first renewal. There is no trust on first use
  at any point: a key other than the pinned one is always refused.
- **Renewal.** The server keeps one long-lived private key (`server-key.pem`) and
  issues itself a new self-signed certificate from that same key 30 days before
  expiry and whenever its names change: valid for 397 days, listing the configured
  address first and every extra name and IP added under **Administration > Other
  server names and IPs**. The new certificate must pass the same loopback check as
  at start (strict TLS, its name) before it replaces the old one, which is kept as
  `server-cert.previous.pem`. If renewal fails, the current certificate stays in
  use, the dashboard shows a warning and the hourly check tries again.
- **Learning a renewed certificate.** When the stored certificate no longer
  verifies (new dates or names), the computer reads the server's certificate in a
  handshake that sends no application data, accepts it only if it carries the
  pinned key, and then connects again with full verification (chain, dates, name,
  strict X.509, TLS 1.2+) trusting only that certificate. A different key is
  refused before any request ("The server's security certificate has changed").
  An expired certificate, or one that does not list the address being used, is
  refused even with the right key.
- **Rediscovery.** If the stored address stops answering (or presents another
  certificate) twice in a row, the computer asks local discovery (mDNS,
  `_lifetime._tcp`) at most every ten minutes. A discovered name or IP, including
  the address the answer came from, is only a hint: it is kept only if a fully
  verified connection to it presents the pinned key and a certificate naming that
  address. Nothing else is sent to a candidate.
- **Key loss or theft** is the only case that needs re-pairing: create a new
  identity on the server (see SERVER_OPERATIONS.md) and pair every computer again.
  A signed hand-over from an old key to a new one is not built.

## Mixed versions

Computers ask for the server's capability list with the request header
`Lifetime-Accept: capabilities`; a server lists the optional report parts it accepts
only to computers that asked, so an older computer never meets an unknown reply
field. A computer sends only advertised parts (a server without a list gets the 1.1
format) and says on its Connect page when some details are not shared because the
server is older. Server validation is unchanged: any field outside its allowed list
still rejects the whole report.

## LAN attacker and guessing limits

A LAN attacker can advertise a fake service/address/fingerprint prefix, observe
public discovery traffic, block or relay traffic, or deny service. They cannot
make the client send enrollment data, or reports, to a different key merely by
spoofing discovery, DNS or the server address. The advertised ten-hex-character
fingerprint prefix is for identification only; it is never the trust decision.
An online MITM with a different certificate is rejected before HTTP is sent.

The compact certificate commitment has **40-bit targeted collision resistance**,
not the full fingerprint's 256-bit strength. A sufficiently resourced attacker
who learns the code could search for a different certificate with the same short
commitment. Prefer the advanced full-certificate bundle when that threat is in
scope. Compromised administrator/client machines, stolen private keys and
replacement of the code through an untrusted channel are outside the LAN-only
protection. Treat codes and connection lines as secrets. A stolen live code can
create pending requests and use up its allowance, but cannot approve itself.

Invitations expire after 24 hours by default, allow 1 to 100 uses, and can be
revoked in Administration. The database stores hashes of the code and secret;
the plaintext is returned only when issued and is not recoverable in the list.
Five failed guesses against a known invitation lock it permanently; create a new
invitation to recover. Failures do not roll back when authentication fails.

Every new-code attempt also uses persistent per-source-IP backoff: after the
third failure, delays start at one second and double to a five-minute ceiling.
The counter resets after an hour without failures. Only a SHA-256 hash of the
source IP is retained, in at most 4,096 throttle entries. For invitation codes
that back-off and the five-failure lock on each invitation are the limit: wrong
codes from one address hold back that address, not every computer. A shared
ceiling of 200 failed enrollments per 60 seconds remains across all addresses.
Legacy 12-character bundle codes, and callers whose address is unknown, keep the
persistent server-wide limit of ten failures per 60 seconds, so a LAN attacker
can still lock those out temporarily; admin approval is still the final gate.

Opening a connection needs no credential, so one remote address may hold at most
8 of the 32 short-request slots at once; further connections from it are closed
before the TLS handshake. Loopback is not counted and live sockets hand their
slot back. Many computers behind one NAT address share that allowance: their
requests are short and are retried, but a large site behind a single address is
not yet measured.

Two limits are known and left for an owner decision. The connection code commits
to the server certificate with 40 bits (above): prefer single-use, short-lived
codes, and treat a multi-use code shared with many people as weaker; a longer
commitment needs a new, longer code format. The agent and server installers
accept a non-default install folder that already exists if it holds a Lifetime
marker file, and a Python found on PATH outside `C:\Users`: use the default
Program Files folders and a machine-wide Python that only administrators can
write.

## Discovery, data and administration

The stdlib-only IPv4 DNS-SD browser/responder uses `_lifetime._tcp.local` at
`224.0.0.251:5353`, with PTR, SRV and TXT records. Replies contain an instance,
host, port, protocol marker and fingerprint prefix, with no device or invitation
data. Only Lifetime questions receive answers. Parsing has bounded packet,
record, compression-depth and result limits. Browsing takes up to three seconds;
responses are limited to five per second. Discovery failures never downgrade
TLS; use a manual address or connection line across routed subnets or VPNs.

The public HTTPS API still accepts only enrollment, claim and sync. New fleet,
invitation, organisation and approval queries use the separate authenticated
loopback-only admin API. Existing request sizes, deadlines, connection limits,
private-file ACLs, offline queue durability and redacted errors are retained.
There are no request/access logs. The **Server log** keeps at most 500 entries
(repeats within a minute are counted): starts and stops, certificate renewals and
refused requests by category only, with no payloads, codes, credentials or IP
addresses. A refused report is attached to a computer (its "Last problem") only
when that computer's own credential came with it. A computer reports the category
of its last failed attempt (the same word its Connect page shows, never error
text) to servers that list the `problem` capability. Restore invalidates both
legacy codes and new invitations, credentials and pending polling tokens.

Daily dashboard values use only currently permitted dated client history.
Missing dates are unavailable, known zeroes stay zero, and fleet subtotals name
their contributing device count. Calendar labels are device-local, not a shared
UTC interval. Connected means a recently received report, not proof that the
computer is still awake. Totals and reporting do not measure human activity.

## Live channel (WebSocket)

Computers keep one WebSocket to the server on the normal port, over the same pinned key and credential as the
HTTPS sync. The credential travels in the upgrade request header and is checked before the connection is accepted; a bad
or missing credential gets a 403 and no data is read. Frames are JSON, at most 8 KiB, and a computer sending more than
40 frames a second is disconnected. Any request that carries an `Origin` header (that is, any browser) is refused, so a
web page cannot open the socket. An unauthenticated upgrade still counts against the short-request connection limit;
only an authenticated socket moves to the separate long-lived pool (at most 1024).

Commands are chosen from a fixed list (sleep, restart, shutdown, refresh), stay behind the owner's remote power consent,
carry the existing one minute on-screen warning, and are deduplicated by id on the computer. The server window uses a
separate stream on 127.0.0.1 only, with the admin bearer token. The admin API also rejects any `Host` header that is not
a loopback name, which blocks DNS rebinding. The live channel itself does not change who may read what the server stores;
what a computer may share is described in the next section.

## What a computer shares, and how the server treats it

A connected computer sends only what its owner allowed, and only after approval. Two consent switches control everything
beyond awake time, and turning one off stops that data at once:

- **History** (on the Settings page and in Connect): the daily awake totals, plus the hour-of-day profile, the list of
  recent sessions (start and length, at most 100), the boot count and the current and longest session.
- **Readings** (computer performance): the specs and live readings, plus the hardware details shown on the System page
  (processor clock and temperature where reported, cooling fans, network cards, memory modules, disk model and health,
  motherboard and BIOS, graphics driver, displays, operating system build) and, with Health allowed, stability counts
  (unexpected shutdowns and crashes), battery wear and cycles and sleep patterns. MAC addresses are part of the specs and
  exist only so Wake-on-LAN can work.

Not collected, ever: serial numbers, user names, file names, process or window lists, keystrokes or screen contents.
Hardware details are read with the operating system's own read-only queries. Lifetime installs no kernel driver and
bundles no third-party sensor library, so a value the system does not expose shows "Not available" with a reason
instead of being guessed. Totals and charts measure computer uptime, not human activity.

The server treats all of this as untrusted input. Every field is validated for type, numeric range, string length and
list size before it is stored (`lifetime_protocol.py`), a report with any field outside the allowed list is refused as a
whole, requests are size-limited, and what is stored per computer is bounded. A computer that has not shared history or readings simply shows an empty state.
Reading the data requires the loopback-only administrator API (its bearer token, or on Windows a named account with a role; see below). Appearance choices
(theme, language, style) are stored locally and are not sent anywhere. If you find a way to make the server accept or
display data it should refuse, please report it as described below.

## Energy, after hours and the sleep-after-hours policy

Energy, after-hours, forecast and health figures are computed only from what each computer already shares under its
History, Readings and Health consent; a computer that shares nothing is counted as unknown. Monitoring's daily
disk/battery readings for forecasts are deleted as soon as a computer stops sharing readings. The desktop app keeps its
own on-battery power readings and daily disk reading in `insights.json` beside its settings (Battery and Resources
permissions), never in the awake-time ledger.

The **Sleep after hours** policy adds no new power path: each command is the existing single-computer Sleep, sent
through the same gate as a person's (named Windows account, role re-checked before every command, owner's remote-power
consent, per-computer and per-account rate limits, ten-minute expiry, audit trail). It can be turned on only by an
account with an Admin or Operator role assigned to that account itself, never over the shared token, and it stops when
that role is removed. It never sends a bulk command.

## Text from other computers, and the Windows installers

A computer chooses its own name and reports its own hardware strings; administrators type group, check and role names;
a server tells the desktop app its name. None of it is ever interpreted as HTML. Every label in both apps is a
plain-text label by default (`lifetime_i18n_qt.PlainLabel`); the three that use rich text escape each value with
`html.escape`, as do the detail views and the HTML/PDF report. Labels Qt builds itself (message boxes, input
dialogs) are made plain before they are measured, and a tooltip that looks like HTML is shown as its characters. Email
and webhook messages are plain text. `test_plain_text` fills both windows with markup and fails if any of it renders,
or if a module imports the stock `QLabel` again. Before this, a computer named `<img src="\\host\x.png">` could make
the administrator's console fetch that file (on Windows, an SMB sign-in to the attacker's machine).

The desktop installer runs partly as an administrator inside `%LOCALAPPDATA%\Lifetime`, which the signed-in user
controls. It refuses to install through a link or junction, checks every file it copies, and hands ownership back only
for the files it wrote (it no longer walks the folder). What the Connect page asks approval to run as administrator is
never taken from that folder: the installer copies `LifetimeAgent.exe` and the two agent scripts to
`%ProgramFiles%\LifetimeAgentSetup` (Administrators and SYSTEM write, Users read), and when that copy is missing the
one approved step creates it and checks each file's SHA-256 against what the app read before running anything. That
first copy still trusts the per-user files once; installing with administrator approval avoids even that. Not yet
exercised on a real standard-user PC with a separate administrator account. A source (Python) install still elevates
`lifetime_agent.py` from the per-user folder and is meant for development.

Release builds install exact package versions (`requirements-server.txt`, `requirements-desktop.txt`,
`requirements-build.txt`, and the Linux build script), use GitHub actions pinned to commits, check the AppImage tool
and runtime against recorded SHA-256 values, and never replace the files of a published release. Package hashes
(`--require-hashes`), container base images pinned by digest and signed checksums are not in place.

A role's SID must be a complete SID wherever it is read, including from a restored backup, and is the only thing
written into the admin pipe's access list. The admin command and the Windows service log an error's kind, not its text.

## Agent updates

A computer takes an agent update only when its owner switched updates on (off by default, kept on the computer, not
part of what it shares). Only an Admin can upload a package, and the server accepts the file only when its SHA-256
equals the one the administrator typed (from the release's published checksums); files inside the server's data
directory (key, token, databases) are refused. The computer asks only its pinned server, with its own credential,
downloads in bounded pieces, and keeps the file only if the size and SHA-256 match what the server stated and, on
Windows, an Authenticode signature, when present, verifies (checked offline; an invalid or untrusted signature is
refused). It refuses an older version unless the server marks the offer as a rollback. **The agent never runs the
package.** The administrator's installer (`install_agent_windows.ps1 -StagedUpdate`, as SYSTEM) checks the SHA-256 again
before replacing the program and keeps the previous one. The trust root is the pinned server and the administrator who
approved the checksum: a compromised server administrator could offer a malicious package with a matching checksum,
which the signature check stops only for signed releases. Lifetime releases are not yet code-signed. Until they are,
switching updates on means trusting the server's administrators with SYSTEM on that computer: an unsigned package is
accepted, any publisher Windows trusts counts as signed, and a rollback has no minimum version. Requiring the Lifetime
publisher's signature and a version floor needs a code-signing certificate (an owner decision).

## Alerts, notifications and the mail password

Alert rules run inside the server on data it already holds and apply a rule only to data the computer's policy lets
it share (reading rules need Readings, crash rules need Health). Monitoring writes its own `monitoring.sqlite`, never
`server.sqlite` and never any computer's awake-time ledger. Its admin routes are on the same loopback-only, token
protected administration API, and every setting is validated (ranges, time zones, dates, e-mail addresses, an
`https://` webhook without a user name or password in it).

- **SMTP password and webhook address.** They are secrets (a webhook URL often contains its own token) and are kept
  out of `monitoring.sqlite`, replies, logs and exports, in `alert-secrets.bin` in the data directory. On Windows
  the file is encrypted with DPAPI (`CryptProtectData` through ctypes, for the account that runs the server,
  normally SYSTEM, with an application-specific entropy) and its ACL is reset to Administrators and SYSTEM before
  anything is written. On other systems DPAPI does not exist, so the file is plain JSON protected only by 0600
  permissions on a 0700 data directory: protect the server account and backups accordingly. See "Lifetime Server on Linux" below. The dashboard shows only
  whether a password is set and the webhook's host. A file written by another account (for example after copying
  the data directory to another machine) cannot be decrypted and is reported as unreadable: enter the secrets again.
- **Transport.** Mail uses only the standard library (`smtplib`, `ssl`) with STARTTLS or implicit TLS, certificate
  and host name checks from the system's trusted roots, and never falls back to a plain connection: a server that
  does not offer STARTTLS receives nothing, not even the user name. Webhooks are HTTPS POSTs that follow no
  redirects.
- **Message content.** Messages name the computer and its group, the problem, its last reading and times. They never
  contain activity or readings history, IP or MAC addresses, codes, credentials or the server's certificate details.
  The delivery log stores a status and an error category, never message text or server replies.
- A test message never creates an incident. Retries are bounded (five attempts), and a message interrupted by a
  server stop is never sent twice.

## Agentless checks and the network policy

Checks of printers, routers, NAS boxes and websites run from the server, so they must not become a way to reach
places the administrator did not intend (server-side request forgery):

- **Allowed networks.** A check may reach only addresses inside the configured scopes: `private` (RFC 1918, CGNAT
  100.64.0.0/10 and IPv6 unique local; the default), `public` (globally routable addresses) or explicit CIDR ranges.
  Loopback (127.0.0.0/8, ::1, IPv4-mapped forms) and link-local (169.254.0.0/16, fe80::/10) addresses are refused
  unless an explicit range names them. Cloud metadata addresses (169.254.169.254, fd00:ec2::254, 100.100.100.200),
  this server's loopback admin port, unspecified, multicast, broadcast and reserved addresses are always refused.
- **Every attempt and every redirect** resolves the name again, refuses the check if any address it resolves to is
  outside the policy, and connects to that checked address (no second lookup between the check and the connection),
  so DNS rebinding and redirects to internal or metadata addresses fail as *Blocked*. Redirects are limited (0 to 5)
  and may only go to `http://` or `https://` addresses without a user name or password.
- **No credentials.** URLs with a user name or password are refused when saved and when met in a redirect. Requests
  are plain GET or HEAD with a fixed user agent and no cookies, authorization headers or body. TLS (1.2 or newer) is
  verified against the system's trusted roots and the host name by default; turning verification off is a per-check
  administrator choice for devices with their own certificates.
- **Bounded work.** One worker, at most 16 attempts at a time, a minimum spacing per target, jitter, a deadline on
  every attempt, a response size cap (at most 1 MiB) and a bounded result queue; at most 500 checks with intervals of
  at least 30 seconds. Ping runs the operating system's `ping` with a fixed argument list, no shell, and only an IP
  address that already passed the policy, so no host name or option reaches its command line.
- **Storage.** Results go only to the monitor, the single writer of `monitoring.sqlite`; checks never touch
  `server.sqlite` or any awake-time ledger. Check settings and results are part of the dashboard's monitoring backup.
  The dashboard reaches all of this only through the loopback-only, token-protected administration API, and every
  field is validated (kinds, host names, URLs, ports, status ranges, sizes, intervals, scopes).

## Named administrators, roles and the audit trail

**Identity.** On Windows the server serves its admin API on a local named pipe
(`\\.\pipe\LifetimeServer-admin-<admin port>`) as well as on the loopback HTTP port. After a request arrives the
server impersonates the client at *identification* level (the client opens the pipe with
`SECURITY_IDENTIFICATION`, so the server can read the token but never act as the client), reads the token's user SID
and enabled groups, and reverts. A request cannot name its own caller: anything but `operation` and `arguments` is
refused, and the incident *acknowledged by* name is replaced with the Windows account. Groups that are deny-only are
ignored, so an administrator who has not elevated (UAC) is not Admin through the Administrators group. The pipe is
created first-instance (another program that took the name first makes the server log
*Named administration channel unavailable* and run on the token channel only), refuses remote clients, denies
network logons, and admits only SYSTEM, Administrators, the server's own account and the accounts and groups that
hold a role; those get read/write access without the right to create pipe instances. The client refuses a pipe that
is not owned by SYSTEM, Administrators or its own account before sending anything. Standard library only (ctypes).

**Roles.** Admin (everything), Operator (view; acknowledge incidents; add and remove maintenance windows; refresh;
sleep, restart, shutdown and Turn on for one computer at a time, still only where its owner turned on remote power),
Auditor (view and the audit trail) and Viewer (view only). SYSTEM and elevated Administrators are always Admin and
cannot be lowered, so the server cannot be locked out of its own administrators. Roles are enforced by the server for
every request, on both channels, before the request's fields are even checked. Every admin operation belongs to
exactly one permission class in `lifetime_access.py`; an operation in no class is refused to everyone, Admin
included, and the tests fail until it is classified (default deny). The dashboard only greys out what the server
would refuse.

**The token channel.** The loopback HTTP API with `admin-token` stays for older dashboards and for callers
without a role on the named channel. It cannot tell who is calling, so whoever can read the token
(Administrators and SYSTEM by the file's ACL; root and the `lifetime` account on Linux) is Admin and is
recorded as `admin-token`. Remote power is refused on it: it needs a named account, which on Linux comes
from the Unix socket (see "Lifetime Server on Linux").

**Remote power.** Besides the owner's consent on the computer: a named, authorised Windows account; the role is
checked again under the same lock as role changes, so removing a role always wins against a command in flight, and
removing or lowering a role that allowed power cancels every power command still waiting. Each command has an id and
expires ten minutes after it was issued (the computer refuses it after that too). At most three power requests per
computer and ten per account in ten minutes. One computer per request; there are no bulk power actions. The
confirmation names the computer's name, group and ID.

**Audit trail.** `access.sqlite` keeps an append-only record of every state-changing request and every refused
request: the account's SID and name, role, action, target (identifier fields only, never passwords, webhook
addresses or other values), result, UTC time and a correlation id (the command id for remote commands). Each row
holds the SHA-256 of the previous row, so an edited, removed, reordered or truncated entry is reported by **Verify
audit trail** (or `lifetime_server.py verify-audit --directory <data directory>`) with the first broken entry. At
200,000 entries the oldest 20,000 are first written to a protected `audit-archive/audit-<first>-<last>.jsonl` and
then removed; the chain continues from the last archived hash. Limits, honestly: a local administrator (or anything
running as SYSTEM) can still rewrite `access.sqlite` and recompute every hash, or delete the file. The chain makes
tampering detectable, not impossible; export the trail (CSV with hashes) and keep copies elsewhere for evidence that
does not live on the server.

**Backups.** The backup archive contains the server's private key, so it is created with an ACL for Administrators
and SYSTEM only (0600 elsewhere); keep copies just as restricted. It never contains `admin-token` or
`alert-secrets.bin`. Restore checks the archive's checksum, every file's hash, every database and that the key matches
the certificate, and applies it only at the next start, before any listener opens; it keeps the current roles and
audit trail, so a restore can never bring back an older trail or a role that was removed, and it invalidates every
computer credential, code and polling token as before.

## Lifetime Server on Linux

**Identity: a Unix domain socket with SO_PEERCRED.** On Linux the named pipe is replaced by
`/run/lifetime-server/admin-<admin port>.sock` (a console run without that folder uses the data directory). The
kernel reports the uid and gid of the connecting process (`SO_PEERCRED`); the server turns them into an identity of
the same shape as a Windows token: `unix:uid:N` with the user name, and the primary plus supplementary groups from
the user database (`getgrouplist`) as `unix:gid:N`. A request cannot name its caller (the same `operation` /
`arguments` rule as the pipe). `root` and members of `lifetime-admin` (or `LIFETIME_ADMIN_GROUP`) are always Admin and
`root` cannot be given a lower role; every other account has no access until a role is assigned to its uid or one
of its groups; the role matrix, power gate, rate limits and audit chain are the same code as on Windows. A caller
without any role is disconnected before its request is read, as the pipe's DACL refuses it on Windows.

The socket is created under a temporary name (reachable only by its owner whatever the umask), set to 0660 with
group `lifetime-console` (or `LIFETIME_CONSOLE_GROUP`; the admin group when no console group exists), then renamed into
place, so it is never briefly open to others. Membership of `lifetime-console` only allows connecting: it grants no
role, and a member without an assigned role is disconnected unread. To give an account a role, add it to
`lifetime-console` and assign the role with `set_role`. `root` and `lifetime-admin` remain Admin. Its folder is 0755 and owned
by the `lifetime` service account. A stale socket left by a crash is replaced only if nothing answers on it; a live
socket or any other file at that path stops the channel (the server then runs on the token channel and logs
*Named administration channel unavailable*). The client checks with `SO_PEERCRED` that the listening process belongs
to `root`, the `lifetime` account or itself before it sends anything.

Limits: membership is read from the user database at each request, not from the calling process, so a user removed
from a group loses that role immediately, and one just added gains it before signing in again. Forwarding the socket
over SSH (documented for headless servers) makes the server see the SSH account, which is the intended identity. The
admin token on the loopback HTTP port still makes its reader Admin; only root and the `lifetime` account can read it.

**Secrets file.** On Linux `alert-secrets.bin` is plain JSON in a 0600 file owned by `lifetime` inside the 0700 data
directory: file-permission protection, not encryption (there is no DPAPI). Root, the service account and anyone with
the disk, a disk image or a raw copy of the data directory can read the SMTP password and the webhook URL. Dashboard
backups exclude it, as on Windows. Use dedicated, revocable credentials for alerts.

**Service and container.** The systemd unit runs as `lifetime` with `NoNewPrivileges`, `ProtectSystem=full`,
`ProtectHome=read-only`, `PrivateTmp` and `UMask=0077`; the OpenRC and SysV scripts drop to `lifetime` with
`supervise-daemon`, `setpriv` or `runuser` and the same umask. The container image runs as the non-root `lifetime`
account, publishes only 8443 and keeps 8444 and the admin socket inside the container. The administration port is
never exposed by any of them, and there is no network-reachable administration interface.

**Linux clients.** Remote power on a Linux computer uses `systemctl --no-ask-password` / `shutdown +1` with systemd,
otherwise `loginctl --no-ask-password` or logind over D-Bus with `interactive=false`: polkit decides, nothing ever
asks for a password, and Lifetime never runs sudo. If none is available the command is reported as failed ("not
supported"). The AppImage passes its own path (`$APPIMAGE`), never a path inside its temporary mount, to autostart
entries and background processes.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on this repository
(Security tab, Report a vulnerability).

## Linux reporting

The Windows machine agent keeps its restricted machine-wide path. On Linux,
the shared Company server dialog uses a separate user ledger at
`${XDG_STATE_HOME:-~/.local/state}/lifetime-agent`. The existing privileged
`/var/lib/lifetime-agent` CLI default is unchanged. `install_linux.sh
--company-agent` offers a separate, default-No consent prompt before initializing
the user ledger and enabling `lifetime-company-agent.service`.
Without a systemd user manager it writes an XDG autostart entry instead
(`~/.config/autostart/lifetime-company-agent.desktop`) and starts the agent once. The unit has
`UMask=0077`, `NoNewPrivileges=true`, and no credentials in its arguments. It
records/retries while the user manager is running; no lingering is enabled.
Enrollment and optional history/system information require their own explicit
dialog consent. The desktop tracker ledger is never implicitly imported.
