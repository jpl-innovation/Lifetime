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
   and compare the **full** SHA-256 fingerprint before sending an HTTP request.
   The name or IP supplied must match the certificate SAN. If a certificate
   names a DNS host, use that name and arrange normal DNS resolution over the
   VPN; an arbitrary IP alias does not bypass hostname validation.
4. Enrollment, durable polling, administrator approval and credential issuance
   follow the existing protocol. A code never bypasses approval. Exact retries
   are idempotent. Re-enrollment preserves the original UUID/sequence namespace,
   server identity and history. Revocation requires a new invitation and approval
   to resume reporting. Certificate rotation still requires explicit re-pairing.

The advanced bundle import retains its original full out-of-band trust path.
Legacy bundle codes retain their original server-wide guessing budget.

## LAN attacker and guessing limits

A LAN attacker can advertise a fake service/address/fingerprint prefix, observe
public discovery traffic, block or relay traffic, or deny service. They cannot
make the client send enrollment data to a different certificate merely by
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
There are no request/access logs. Restore invalidates both legacy codes and
new invitations, credentials and pending polling tokens.

Daily dashboard values use only currently permitted dated client history.
Missing dates are unavailable, known zeroes stay zero, and fleet subtotals name
their contributing device count. Calendar labels are device-local, not a shared
UTC interval. Connected means a recently received report, not proof that the
computer is still awake. Totals and reporting do not measure human activity.

## Live channel (WebSocket)

Computers keep one WebSocket to the server on the normal port, over the same pinned certificate and credential as the
HTTPS sync. The credential travels in the upgrade request header and is checked before the connection is accepted; a bad
or missing credential gets a 403 and no data is read. Frames are JSON, at most 8 KiB, and a computer sending more than
40 frames a second is disconnected. Any request that carries an `Origin` header (that is, any browser) is refused, so a
web page cannot open the socket. An unauthenticated upgrade still counts against the short-request connection limit;
only an authenticated socket moves to the separate long-lived pool (at most 1024).

Commands are chosen from a fixed list (sleep, restart, shutdown, refresh), stay behind the owner's remote power consent,
carry the existing one minute on-screen warning, and are deduplicated by id on the computer. The server window uses a
separate stream on 127.0.0.1 only, with the admin bearer token. The admin API also rejects any `Host` header that is not
a loopback name, which blocks DNS rebinding. Nothing here changes what the server stores or who may read it.

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
