# Persistent live channel (WebSocket)

Live data and commands now travel over one WebSocket per computer instead of one HTTPS request per sample.
The HTTPS sync (`/v1/sync`) is unchanged and still carries the durable history ledger, so exactly-once
accounting is untouched.

## What it does

| Before | Now |
| --- | --- |
| A fresh TLS handshake and a POST for every sample | One TLS handshake when the computer connects |
| The computer learned "someone is watching" from the reply to its next POST (up to 10 s) | The server pushes the rate the moment a viewer opens a computer |
| Commands waited for the computer's next request | Commands are pushed instantly; the computer answers with an `ack` |
| The server window polled the admin API every second | The server window receives pushed samples; polling stays as a fallback |
| Offline after 30 s of silence | A closed socket is offline at once; silence is still caught by a 45 s receive timeout and 15 s pings |

Rates: 500 ms for the computer being watched, 2 s for the rest while any window is open, 10 s when nobody
is watching. The floor is the sampler (CPU percentage needs a measurement window), not the network.

## Protocol

`GET /v1/ws?device=<id>` on the normal port (8443), same pinned certificate, `Authorization: Bearer <credential>`.
A bad credential gets 403 during the upgrade, before any data is accepted. No new port, no new firewall rule.

Every frame is JSON: `{"type": "hello|telemetry|command|ack|status", "id": "...", "ts": <ms>, "data": ...}`.

- `telemetry` (computer to server): one live sample.
- `status` (server to computer): `{"rate_ms": n}` desired sample interval, 250 to 60000.
- `command` (server to computer): `{"action", "issued_at"}`, with the command id as the envelope id.
- `ack` (computer to server): `{"id", "status": "executed|failed|unsupported|declined"}`.

Commands stay in the database. A command is marked delivered when pushed, and done or failed when acknowledged.
The computer remembers the last command id it handled, so a re-delivery after a reconnect is acknowledged and never
run twice. Power commands keep the existing one minute warning and the `control` consent.

## Reliability

- Heartbeat: websocket ping every 15 s with a 15 s timeout on both ends.
- Reconnect: exponential backoff from 2 s to a 60 s cap with jitter; a clean drop retries within a second.
- Fallback: if the socket cannot open (an older server answers 404, a proxy, a closed network) the computer keeps
  using the HTTPS live POST exactly as before and asks again later (every 5 minutes after a 404).
- A server without the `websockets` package simply has no channel, and computers fall back.

## Server window stream

`GET /v1/stream` on the loopback admin port (127.0.0.1 only, same bearer token). The server pushes samples
(coalesced to the latest per computer every 200 ms), online and offline changes and command acknowledgements. The
window sends `{"watch": "<device id>"}` so the open computer is sampled faster. If the stream is down the window
polls the admin API as before.

## Adding UDP later

`lifetime_ws.TelemetryTransport` is the only thing the live sender needs (`send_sample`). A UDP channel can implement
it while commands, acks and presence stay on the socket. Not built.

## What you need to do

- `pip install websockets` (it is in `requirements-server.txt`). The Windows build scripts add the hidden imports;
  both `LifetimeServerRuntime.exe` and `LifetimeAgent.exe` were built and the modules are inside them.
- No firewall change: it is the same port as before.
- Computers running an older agent keep working over HTTPS until they are reconnected with the new code. An agent
  left running from an earlier connect must be stopped once; new connects replace it automatically.
- The source (non-packaged) agent install uses a clean virtual environment with no packages, so it uses the HTTPS
  fallback unless `websockets` is installed there.

## Activity and Health detail (client 1.2)

Durable HTTPS sync adds optional history fields: `hourly` (24 buckets),
`dated_hourly` (120 days), `sessions` (100 rows), `boots`, and current/longest
session seconds. Existing daily/imported history remains capped at 3,660 days.
These fields require the existing history consent.

Under existing resources consent, `readings.health` carries the version-1 health
summary plus optional `detail`: stability events (400), battery packs (4), capacity
history/usage (400 days each), sleep days/nights (400 each). OS log reads run in a
background worker and refresh every six hours. Failed readings are omitted.
No new consent flags are introduced; the older independent health summary is
unchanged. Revoking resources clears the extended readings locally and remotely.

The server validates nested field names, types, finite ranges, dates and list caps;
the complete sync remains limited to 1 MiB. Optional JSON is stored in existing
snapshot columns, so existing databases need no DDL migration. The loopback,
token-protected device detail API returns the same snapshots. Old clients work
unchanged. Since older servers strictly reject unknown fields, a 400 response to
an extended batch makes the client retry the old format for 24 hours.

### Optional hardware inventory

`readings.specs.hardware` uses the shared `lifetime_system` schema. It requires
resources consent and is collected off the sampler/UI thread, cached for six hours.
There are at most 16 hardware network interfaces and 8 rows per other list
(fans, RAM modules, physical disks, GPUs, active displays). Strings are capped at
160 characters; numbers have per-field finite bounds, and counts/byte sizes are
integers. Unknown keys, control characters, bools-as-numbers and oversized lists
are rejected. Old-server fallback strips this field too. Existing JSON snapshot
storage/API and consent revocation apply without schema migration.

Windows probes use CIM and standard OS APIs. No sensor
drivers/libraries are installed. Unidentified ACPI thermal zones are never presented
as CPU temperatures; Windows CPU temperature stays unavailable without an identified sensor.
Optional bounded `unavailable` fields explain domain failures, including access denied.
Network inventory includes virtual adapters; missing driver/link speed is explicit.
Win32_Fan DesiredSpeed is a requested speed and is
never labeled as measured RPM. Win32_VideoController's 32-bit AdapterRAM is not
trusted for VRAM; a driver registry QWORD or existing NVIDIA reading is preferred.
Access-denied or unexposed readings remain unavailable. Linux CPU sensors/fans
use thermal/coretemp/k10temp/hwmon; other inventory is best-effort sysfs data.
