# Design: certificate renewal, address changes and mixed versions

Status: **approved and implemented** (Stage 0). Baseline: `latest-main` after commit 7e218cf.

Implementation notes (what was decided where the design left room):

- The pairing-code commitment still covers the whole certificate (wire format unchanged, so every version pairs
  with every other). The certificate fixes its key, so the key pin taken from it is equally authenticated; a code
  created before a renewal no longer matches and must be replaced.
- Certificate names are the configured address plus the names and IPs the administrator adds; current IPs are not
  added automatically, because every renewal stops computers still on 1.1 (they pin the whole certificate) and
  invalidates outstanding codes, so renewals stay deliberate (30 days before expiry, or a name change).
- Migration (decided after review): the key pin is computed locally from the certificate the computer already
  stores. That certificate came through the authenticated pairing, so this is as secure as waiting for a live
  match and lets a computer that has not reported since its update keep working through the first renewal.
- Capabilities are announced only to computers that send `Lifetime-Accept: capabilities`, because 1.1 and 1.2
  computers reject unknown reply fields. The one-day 1.1 fallback after a 400 remains as a safety net.
- Server visibility (Stage 0 item 4) added the `problem` capability: a computer's last failure category.
Goal: the server can renew its certificate and change address, and computers and servers of different versions can
run side by side during an upgrade, **without re-pairing every computer and without ever trusting an unknown key**.

## 1. What happens today (observed in code)

- `lifetime_tls.generate_certificate` creates a self-signed RSA-2048 certificate, valid **365 days**, with **one**
  name or IP (`SubjectAlternativeName`) taken from `-Address`, marked as its own CA (`pathlen 0`).
- Each computer stores the server address and the certificate's **full SHA-256 fingerprint**, and
  `request_json` refuses any other certificate (`Server certificate fingerprint mismatch`) before sending data.
- Consequences: after 365 days, or when the server needs a new name or IP (DHCP, rename, new network), every
  computer stops reporting and must be paired again with a new code. Since 89dc205 the computer at least says so
  ("The server's security certificate has changed").
- Versions: computers send `client_version`; the server rejects any message with a field it does not know. A newer
  computer that gets HTTP 400 from an older server resends the 1.1 format for a day (hard-wired to the Activity and
  Health additions).

## 2. Proposal A: pin the server's key, not its certificate

1. **Keep one long-lived server key** (`server-key.pem`, already protected by ACLs) as the server's identity.
2. **Pin the public key** (SHA-256 of the SubjectPublicKeyInfo, "SPKI pin") on computers instead of the whole
   certificate. A renewed certificate made from the same key has the same pin.
3. **Renew automatically:** 30 days before expiry, and whenever the administrator changes addresses, the server
   issues a new self-signed certificate **from the same key**, with a validity of 397 days or less and **every
   configured name and IP** (hostname first, plus current IPs). The old certificate is kept until the new one is
   in use; a failed renewal keeps the old one and raises a dashboard warning.
4. **Validate as before**, except the pin check compares the SPKI. Hostname/SAN validation stays: the address the
   computer uses must appear in the certificate. TLS 1.2+ and strict X.509 checks are unchanged.
5. **Migration without trust-on-first-use:** a computer that still has an old certificate-fingerprint pin connects
   as today; only after the full fingerprint matches does it compute and store the SPKI of that same, already
   trusted certificate, then switch to SPKI pinning. No unknown key is ever accepted.

## 3. Address changes

- Recommend a **DNS name** in `-Address` (for example `lifetime.school.local`), so an IP change needs no action.
- The server certificate lists all configured names and IPs (step 2.3). The administrator can add names on the
  dashboard; the server re-issues the certificate from the same key.
- When a computer cannot reach the stored address, it may look up the server again by the existing local
  discovery (mDNS, `_lifetime._tcp`). A discovered address is only a **hint**: the connection must present the
  **same pinned key**, otherwise it is refused. On success the computer stores the new address.

## 4. Key compromise or loss (the only case that needs re-pairing)

- If the private key is stolen or lost, the administrator creates a **new identity** and every computer pairs
  again with a new code. This stays deliberate and visible.
- Optional later (not in the first step): a **signed hand-over**. The old key signs a statement "replace key X with
  key Y, valid until T". A computer accepts Y only with a valid signature from the pinned key X. This helps planned
  key changes, not theft (a thief holds X too), so it is deferred.

## 5. Mixed versions: capabilities instead of guessing

- Keep strict validation: the server keeps rejecting fields it does not know. Accepting unknown fields would weaken
  input checking and hide mistakes.
- **The server advertises what it accepts.** Every enroll, claim and sync reply gains `capabilities`, a list such as
  `["history.detail", "health.detail", "hardware", "live.v1"]`. A server that sends no list is treated as 1.1.
- **The computer sends only what the server advertised** (generalising today's `without_extended`), and remembers
  the list per server. An upgrade of either side needs no ordering.
- **Never silently**: the computer's status shows "server is older, some details are not shared" when it trims a
  message, and the server's device list shows each computer's `client_version` (already stored).

## 6. Tests required before release (golden cases)

1. Certificate renewed 30 days before expiry: all computers keep reporting, with no new pairing code.
2. Server address changed (IP and name): computers with the DNS name continue; computers with only the old IP
   rediscover the server and continue only with the same key.
3. A different server with a different key at the same address: refused, and the computer says "certificate has
   changed".
4. Computer offline during a renewal, back after expiry of the old certificate: reconnects (same key).
5. Old computer (1.1) with new server; new computer with old server; mixed fleet during a rolling upgrade: no
   rejected reports, trimmed fields disclosed.
6. Pin migration: old fingerprint pin, then SPKI pin, never accepting a key that was not already trusted.

## 7. Open questions

- Validity and renewal window (proposed 397 days, renew at 30 days left).
- Whether the hostname should be mandatory for new installations.
- Whether the signed hand-over (section 4) is worth building for v1.
