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
source IP is retained, in at most 4,096 throttle entries. The existing persistent
server-wide limit of ten failed enrollments per 60 seconds also applies across
IPs and Store instances. These intentionally conservative limits permit a LAN
attacker to cause temporary lockout; admin approval is still the final gate.

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
Reading the data requires the loopback-only administrator API with its bearer token, as before. Appearance choices
(theme, language, style) are stored locally and are not sent anywhere. If you find a way to make the server accept or
display data it should refuse, please report it as described below.

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
  permissions on a 0700 data directory: protect the server account and backups accordingly. The dashboard shows only
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

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on this repository
(Security tab, Report a vulnerability).

## Linux reporting

The Windows machine agent keeps its restricted machine-wide path. On Linux,
the shared Company server dialog uses a separate user ledger at
`${XDG_STATE_HOME:-~/.local/state}/lifetime-agent`. The existing privileged
`/var/lib/lifetime-agent` CLI default is unchanged. `install_linux.sh
--company-agent` offers a separate, default-No consent prompt before initializing
the user ledger and enabling `lifetime-company-agent.service`. The unit has
`UMask=0077`, `NoNewPrivileges=true`, and no credentials in its arguments. It
records/retries while the user manager is running; no lingering is enabled.
Enrollment and optional history/system information require their own explicit
dialog consent. The desktop tracker ledger is never implicitly imported.
