# Specs and remote power backend

All operations use the existing loopback admin API and authenticated HTTPS sync.
No inbound agent listener is added. `AdminClient.call` supports:

* `devices`: compact `summary` and `attention` codes alongside existing fields.
* `device(device_id, hours=24)`: `specs`, `performance`, `performance_history`, and
  the last 20 commands. `hours` accepts integers 1 through 168.
* `send_command(device_id, action)`: `sleep`, `restart`, `shutdown`, or `refresh`.
* `cancel_command(command_id)`: cancels a pending or delivered command.
* `wake(device_id)`: sends WoL broadcasts for the latest recorded MAC addresses.
* `server_info`: server inventory, live readings, runtime and storage metadata.
* `live(device_id=None)`: latest live sample and `age_seconds` for every approved
  computer, plus up to 300 samples for the focused computer. A viewer lease lasts
  20 seconds. The GUI refreshes this lease once per second while visible.

`POST /v1/live` uses the existing device bearer credential and pinned TLS. Its
strict body is `{device_id, sample}` (at most 4096 bytes); all sample fields are
required and unavailable values are null. The server timestamps receipt, limits
each authenticated computer to two samples per second, and stores only a latest
sample and a 300-entry memory ring. Authentication caches expire after five
seconds and are invalidated synchronously by revoke, reapproval, policy sync and
restore. Neither live ingestion nor the admin live read writes SQLite. A fresh
live receipt (less than 20 seconds old) also counts as online and last seen.

The response is `{interval, sync_now}`. Demand selects 10 seconds without viewers,
5 seconds with a dashboard viewer, and 1 second for the focused computer. To meet
the separate command delivery target, the agent caps successful waits at four
seconds even for an idle dashboard. Network failures use exponential waits up
to 30 seconds. Samples are disposable and never enter the offline outbox.
The live daemon has its own read-only state connection and shares the reporting
file lock with consent changes. Slow hardware probes run outside the writer
transaction; a separate normal-sync worker handles command wakeups and the
existing durable acknowledgment before power execution.

CPU, memory, system-disk free space, battery state and network rates use native
Windows APIs or Linux procfs/sysfs. CPU and network rates need two samples.
Temperature, watts and Wi-Fi signal reuse the latest slow reading; absent data
stays null. Battery health and design capacity reuse the cached health pack
data when that category is enabled, without adding probes to the fast path.
The detail panel defaults to Live CPU/memory charts with a moving five-minute
window. The 24 hours choice continues to use the existing five-minute history.

Source runs support Add device followed by Connect without an installation.
Connect verifies the certificate before showing sharing choices, then Windows
Finish asks for administrator permission once and launches the agent. GUI state
tests use fake workers and operating-system calls. Connection tests exercise a
real loopback TLS server, approval and reporting, plus fake LAN sockets that
reject non-matching certificates. See [connecting.md](connecting.md) for scan
limits, sharing defaults, packaged requirements and screenshots.

Remote power requires an approved device, a recent sync, and owner permission
`control=true`. Refresh only requires approval and a recent sync. Recent means
within the greater of 10 minutes or three reporting intervals. Wake requires
an approved offline device with control permission and recorded MAC addresses.
WoL still depends on the target firmware, adapter and network configuration.

The owner API is `MachineAgent.set_control_consent(bool)`. The settings category
`remote_power` defaults off; Connect provides separate power sharing consent.
Missing control permission always means false.

Commands expire 10 minutes after creation. There is at most one pending or
delivered command per device. The agent persists a result and sends it on the
next sync. A strict optional `command_ack: {id, status}` in the sync response
confirms the server recorded it. Power executes only after a matching `done`
acknowledgment, while permission remains enabled and the command is unexpired.
This also allows cancellation between delivery and the result sync.

`done` means accepted for execution, since power actions must be acknowledged
before they can disconnect the agent. An OS invocation failure is reported as
`failed` on the next sync. The agent consumes its pending action durably before
calling the OS, preventing repeats after a crash; a crash in that small window
can leave an accepted command unexecuted. Cancellation cannot abort an OS
shutdown already scheduled after acknowledgment.

Inventory is cached in the agent state for six hours. Every permitted sample
collects live performance. History inserts are separated by at least five
minutes per device and pruned to seven days on insertion. Revoking resources
scrubs inventory and performance history. Physical MAC addresses are included
only under resources permission. No serial numbers or user identity are probed.

CPU percent needs two samples. Temperature currently uses an available GPU
sensor; a CPU temperature is not provided by the reused probes. Battery health,
power and design capacity may be unavailable depending on firmware/drivers.
Missing measurements are null. Hardware probes do not elevate privileges.

The existing Windows installer already registers the agent at system startup;
no additional restart registration is needed.
