# S1 implementation and verification

Implemented in the `design/server` worktree without a commit, push, dependency
installation, real service/firewall change or access to customer Lifetime data.
The desktop UI modules `lifetime_gui.py`, `lifetime_dashboard.py` and
`lifetime_charts.py` were not modified. The supplied JPL image was used without
alteration; it was already untracked when work began.

## Changed files

Connection and reporting:

- Added `lifetime_connection.py`: Crockford encoding/checksum, connection lines,
  TLS-only certificate bootstrap and constant-time commitment verification.
- Added `lifetime_discovery.py`: bounded stdlib DNS-SD responder/browser/parser.
- Updated `lifetime_server_store.py`: schema 5 invitations, lockout, hashed-IP
  backoff, events, organisation, pending queries, fleet projection and sorting.
- Updated `lifetime_server.py` and `lifetime_admin.py`: authenticated loopback
  routes and runtime discovery lifecycle; public endpoint set unchanged.
- Updated `lifetime_client.py`, `lifetime_company.py`, `lifetime_agent.py` and
  `lifetime_protocol.py`: shared connection flow, explicit Linux user-agent path
  and optional bounded client-version metadata.

Server presentation and packaging:

- Updated `lifetime_server_gui.py` and added `lifetime_server_widgets.py`.
- Updated `lifetime_i18n.py` with server/connection English and Vietnamese copy.
- Updated `install_linux.sh` with the default-off, consent-gated user service.
- Updated `install_windows_common.ps1` to copy the new shared modules.
- Updated `install_server_windows.ps1` and `build_server_windows.ps1` to include
  the credit asset and security documentation.
- Added `render_server.py`, a synthetic-store Windows rendering utility.

Tests and documentation:

- Added `test_server_connection.py` and `test_server_fleet.py`.
- Updated `test_company_gui.py` with connection/discovery regressions.
- Updated `test_server_gui.py`'s fake admin with the new routes, preserving the
  existing behavior assertions.
- Updated `test_server_store.py`'s downgrade fixture to remove schema-5 additions
  when constructing an actual old schema for migration tests.
- Added `SECURITY.md`, `docs/design/server.md` and this report; updated `DESIGN.md`
  and `docs/SERVER_PROTOCOL.md`.

## Protocol and threat model

The five groups of four characters carry a 25-bit invitation ID, 30-bit secret,
40-bit certificate commitment and modulo-37 check character. A TLS handshake
fetches only the certificate; a mismatch prevents HTTP, enrollment transmission
and creation of local enrollment state in the dialog. The accepted certificate
then uses the existing strict hostname/CA checks and full SHA-256 pin.

Invitations default to 24 hours and one use, support 1 to 100 uses, and can be
revoked. Five failed guesses lock an invitation. Per-IP backoff and the persistent
global ten-failures-per-minute budget bound guesses; code/secret plaintext and raw
source IPs are not retained. DNS-SD carries only public server metadata. Admin
approval, credential revocation, offline queues and loopback-only administration
remain in place. See [SECURITY.md](../SECURITY.md) for the precise limits, including
the compact commitment's 40-bit targeted collision resistance and the stronger
advanced bundle option.

The optional sync `client_version` field is display metadata. Existing clients
may omit it. The window does not invent a version or attribute cumulative stock
totals to a day. Daily figures need permitted dated history and disclose the
number of known contributors. Device-local dates are not a common UTC interval.

## Executed checks

- Full unittest discovery with `QT_QPA_PLATFORM=offscreen`: **428 tests,
  zero failures, zero errors, four platform skips** (45.257 seconds).
- Final focused connection/fleet/server/company UI run: **48 tests passed**.
- After adding issuance-click coalescing, the five real-store fleet GUI tests
  were run again and passed, including the repeated-click assertion.
- The four full-suite skips require POSIX file modes or `time.tzset`, unavailable
  on Windows. Real loopback TLS enrollment, claim, sync, revocation and mismatch
  rejection ran successfully.
- New tests cover code checksum/aliases, expiry/uses/revocation/restore,
  concurrent invitation limits, persistent wrong-secret lockout and IP/global
  throttles, fake-socket discovery, truthful fleet KPIs, missing dates,
  search/group/sort/pagination, details, native keyboard navigation, reduced
  motion, Add device/copy/revocation and pending approval.
- `bash -n install_linux.sh`, PowerShell parsing of all three changed `.ps1`
  scripts, and `git diff --check` passed.
- No packages were installed. The shell Python lacked the server's already
  declared `cryptography` dependency; tests/rendering used an existing local
  cached installation through `PYTHONPATH`, with bytecode writes disabled.

The verbose unittest result is saved at `shots/server/tests.log`.

Automatic approval review rejected the final cleanup command as “blocked by
policy.” Generated `.server-*.log` files and the early failed-fixture directory
`s1-test-u0j2z5wh/` therefore remain at the worktree root. They contain synthetic
test data only; the renderer's temporary store was removed normally.

## Windows-font captures

`QT_QPA_PLATFORM=windows`, real Segoe UI, exact requested pixel sizes. Synthetic
Demo-PC/Demo-Server names only; loopback runtime/admin API; temporary store inside
the worktree removed afterward. Production ACL code has no bypass: only fixture
ACL calls are mocked by the renderer/tests.

All 16 PNGs are under `shots/server/`:

- `overview-{en,vi}-{1600x1000,1024x700}.png`
- `devices-{en,vi}-{1600x1000,1024x700}.png`
- `administration-{en,vi}-{1600x1000,1024x700}.png`
- `empty-{en,vi}-1600x1000.png`
- `add-device-{en,vi}.png`

`capture.json` records backend, font and filenames. The renders were opened and
inspected against the supplied reference. Fixes included compact Vietnamese
widths, all six rows fitting, the full wide-view chart, readable timestamps,
translated live status/admin rows and responsive stacking. Small windows scroll
to their lower detail/chart sections. There are no demo counters in shipped UI.

## Not verified in deployment

- Actual Windows Server versions, elevated production ACL enforcement, SCM/task
  installation/startup, packaged executables, upgrades and firewall prompts.
- Multicast discovery across real Ethernet/Wi-Fi interfaces, VPNs, subnets or
  firewall configurations. Routed/blocked discovery uses the manual address or
  connection line and retains TLS verification.
- A real Linux systemd user session, sign-out/login behavior, distro packaging
  and POSIX permission execution. The resolver and default-off consent/service
  path are tested, and shell syntax was checked; no service was installed.

Those checks require an isolated deployment environment. No real machine or
customer service was changed to simulate them.
