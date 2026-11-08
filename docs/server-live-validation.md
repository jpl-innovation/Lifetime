# P3 live data validation

Implemented on branch `fleet/specs-control`, without committing.

## Measurements on this Windows PC

- 200 consecutive native full samples: median 3.65 ms, maximum 4.85 ms.
- 100 GUI updates with 200 computer rows in offscreen Qt: median 1.49 ms,
  maximum 6.75 ms, including event processing. This measures the local fixture,
  not a deployed network fleet.
- Inspected the rendered Devices screen. Reserved room for pagination above the
  status bar, added spacing, and sized rows to their content.

## Verification

188 affected-module tests ran in the final two groups, with one existing
platform skip. The full repository suite was not run. The updated cached
battery-capacity assertion was additionally checked by rerunning its affected
agent module successfully.

Core modules: `test_live`, `test_server_runtime`, `test_server_protocol`,
`test_server_store`, `test_server_permissions`, `test_server_admin`,
`test_machine_agent`, `test_sync_client`, `test_installed_modules`.

GUI modules: `test_server_gui`, `test_company_gui`, `test_server_fleet`.

Checks include strict hostile live bodies, native sampling without processes,
Linux procfs fixtures, 300-entry ring bounds, read-only SQLite trace on the live
path, demand expiry and 10/5/1 mapping, revocation of cached credentials and
resource consent, live online status, command wakeups, disposable delivery and
bounded backoff, HTTP framing/size/authentication/rate limits, and 200-card
identity/selection/focus/scroll preservation. GUI polling is tested for no
overlap and suspension while hidden. Capacity caching and unknown battery watts
are checked without another health probe.

The real loopback TLS source-run test covers Add device's generated connection
line through Company server pairing, server approval, credential claim and a
live POST. See [the contract](server-specs-control.md) for exact source-run
requirements. `lifetime_company.py` needed no change. Windows ACL operations are
mocked in the test; real writes require elevation. Pairing alone does not start
the background agent.

## Timing interpretation and limits

The server replies with the requested 10/5/1 demand intervals. The agent caps a
successful wait at four seconds to detect commands within five seconds even
without viewers. Waiting ten seconds with no other server channel would make
that command target impossible. Failure backoff can reach 30 seconds, as
requested. Command delivery assumes a responsive LAN/server.

Linux is fixture-tested here, not validated on real Linux hardware. A real
200-computer LAN and physical power-command timing were not available for
validation. No implementation items are knowingly left unfinished.

## Files changed

- `lifetime_live.py`: native sampler, strict live validation, daemon reporter.
- `lifetime_agent.py`: independent live and sync workers, slow probes outside
  transactions, cached health capacity and unknown-watts handling.
- `lifetime_server_store.py`: memory rings, credential cache, demand leases,
  live receipt/status overlays, synchronous invalidation.
- `lifetime_server.py`: authenticated live route and size enforcement.
- `lifetime_admin.py`: admin live operation.
- `lifetime_server_gui.py`: Tasks polling, moving Live charts and age marker,
  in-place updates and pagination layout.
- `lifetime_server_devices.py`: unchanged-card paint suppression, hidden unknown
  user line and removal of duplicate offline attention text.
- `lifetime_i18n.py`: English/Vietnamese live labels.
- `install_linux.sh`, `install_windows_common.ps1`: include the live module.
- `test_live.py`, `test_machine_agent.py`, `test_server_gui.py`,
  `test_server_runtime.py`, `test_company_gui.py`: affected validation.
- `docs/server-specs-control.md`, `docs/server-live-validation.md`: contract,
  source-run requirements and this report.
