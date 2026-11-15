# Desktop and machine-agent awake-time investigation

Historical investigation from a separate Windows VM on 2026-11-14.
Machine identifiers and user directory names below have been anonymized.
These are not measurements from the computer applying this patch.

Observed on that VM on 2026-11-14. Times below use Vietnam time (UTC+07:00).

## Which records belong to which application

The machine is `EXAMPLE-SERVER-VM`, a Windows Server 2025 VMware VM. The
Administrator desktop runs `C:\Users\ExampleAdmin\Lifetime\lifetime_gui.py`.
Reading that running process's environment established `APPDATA` as
`C:\Users\ExampleAdmin\AppData\Roaming`, with **no `LIFETIME_DATA_DIR`
override**. The GUI uses `lifetime_tracker.data_dir()`, so its ledger is
`C:\Users\ExampleAdmin\AppData\Roaming\Lifetime\ledger.json`.

The reporting process explicitly uses `--directory C:\ProgramData\Lifetime\Agent`.
Its separate SQLite ledger is in `machine.sqlite`, device
`[redacted device ID]`. It is not the Administrator user ledger.
Its import marker is null: no explicit user-ledger transfer is recorded.

The server runtime uses `C:\ProgramData\Lifetime\Server\server.sqlite`.
Its GUI runs from `C:\Users\ExampleUser\Documents\Lifetime`, a separate Git
checkout (HEAD `8d31f7d4961eb41fed6dfdf413f049fc1100f43f` when inspected).
The old ExampleUser user ledger is in that user's AppData, not the Administrator
desktop's directory. The server GUI process has ExampleUser's APPDATA and no data
override, but receives the server directory explicitly. Parent/child Python
venv launchers and packaged-runtime processes explain the paired PIDs; those
pairs alone are not evidence of duplicate accounting workers.

The Administrator application had been extracted from the ZIP at commit
`793c515f878feaad567414bf22d536bc701b8554`, without a `.git` directory. This
investigation initialized Git there from that exact commit using the existing
local checkout's objects. Existing connection-cancel edits were left separate
and uncommitted. The ExampleUser checkout was not modified.

## Saved evidence and timestamp comparison

Consistent read-only SQLite backups and copies of both users' JSON records were
saved under `.qa/awake-investigation/20261114-004142/` before changing code.
These private backups may contain enrollment credentials and are ignored by
Git. Windows boot/time-change and sleep-event query results are saved alongside
them. No live database or user ledger was migrated or edited.

The capture completed at **00:41:42.088442**:

| Source | Observation time | Awake counter / total |
|---|---|---|
| Native `QueryUnbiasedInterruptTime` | 00:41:42.088442 | 8,942.7674546 s |
| Administrator ledger | 00:37:08.606251 | counter = total = 8,669.2902321 s |
| Agent ledger | 00:41:20.750805 | counter 8,921.4304801 s; total 71,047.70849889997 s |
| Server's latest record, seq 2172 | observed 00:40:50.020078; received 00:41:11.990798 | total 71,016.97883489997 s |

The desktop's saved counter is 273.4772225 s behind the native read, while its
timestamp is 273.482191 s earlier. The agent's saved counter is 21.3369745 s
behind, with a timestamp 21.337637 s earlier. These closely matching intervals
support normal awake sampling during this comparison; they do not reconstruct
all earlier usage. The desktop tracker was no longer present in the process
listing at capture time, so its saved value must not be called a current live
total. It was not restarted during this investigation.

The agent has one outstanding outbox record, seq 2173, with delta
30.729663999998593 s. Adding it to the server total gives exactly the agent
total at floating-point precision. The server has 101 retained records,
seq 2072..2172, observed 2026-11-13 23:49:58 through 2026-11-14 00:40:50.
These records cover approximately 3,084.989377 s of deltas. Earlier records
are unavailable in this snapshot. The server is relaying the agent's
cumulative totals; there is no evidence of a sync multiplier.

The agent's current session is 8,908.6836402 s. Its cumulative total minus
current session is **62,139.0248587 s (17h 15m 39s)**. The cumulative total
minus current awake counter is 62,126.2780188 s. These are different measures:
current session was reset at a discontinuity and is not the OS counter itself.
The longest saved session is 42,198.1795931 s. None of these values proves
whether all earlier accounting was valid.

## Boot and clock evidence

The old ExampleUser ledger was last saved on November 11 at 06:18:18.760835, with
counter = total = 25,350.6109307 s. It and the present Administrator/agent
ledgers all carry raw boot GUID `[redacted boot GUID]`.

Retained Kernel-General events include actual OS start/shutdown boundaries:

| Record ID | Vietnam time | Event |
|---|---|---|
| 843 | Nov 10 23:15:53.993 | OS start, declared start 23:15:52.500 |
| 1403 | Nov 11 06:18:36.241 | OS shutdown |
| 1404 | Nov 11 08:01:27.314 | OS start |
| 1753 / 1754 | Nov 11 09:27:22 / 09:28:11 | shutdown / start |
| 2037 | Nov 11 13:27:50.217 | OS start |
| 2865 / 2866 | Nov 13 22:12:33 / 22:12:40 | shutdown / start |

WMI reports the current boot at November 13 22:12:39.5. Thus the GUID has
not distinguished the OS starts represented by these ledgers. The current
registry `PrefetchParameters\BootId` is DWORD 8, providing an additional local
boot sequence for future sampling. We did not reboot this VM to test whether
that sequence advances here; registry availability is best effort, not proof
against VM snapshot restoration.

No matching retained Kernel-Power 42/107 or Power-Troubleshooter 1 sleep/resume
events were found. Absence of those events does not prove complete sleep
coverage. Kernel-General time-change events identify VMware Tools advancing
wall time by approximately 73, 75 and 80 thousand seconds on Nov 11..13.
These demonstrate wall-clock corrections, **not** a measured rollback of
`QueryUnbiasedInterruptTime`. Snapshot restoration remains a hypothesis.

The agent has `clock_note_count = 1`, no retained coverage gaps, and history
starting November 14 at about 00:16. `_scrub_ledger()` clears daily history,
gaps and recording-start provenance when history is disabled, while retaining
core accounting. Therefore null `recording_started` and short daily history
cannot establish when machine accounting began. The retained data cannot
uniquely explain the old 62,139 s or justify subtracting it.

## Confirmed accounting defects and the fix

1. Windows boot GUID alone was insufficient on this VM. On unchanged GUID
   plus a decreasing counter, `sample()` did not increment boots or reset
   `boot_accounted_seconds`; it treated the event as a discontinuity and then
   resumed accumulating into the old boot. This makes a value much larger
   than the current counter possible without proving an inflated lifetime
   total. Windows identity now includes the registry boot sequence when
   available. Legacy GUID-only identities are adopted without fresh boot
   credit; temporary registry-read failure does not erase a stored sequence.
2. With an unchanged identity and no independent evidence of a new boot,
   the old sampler counted positive deltas through a previously observed
   counter range. Deterministic reproduction `100 -> 50 -> 80 -> 110`
   produced total 160 under the original code. The fixed code preserves
   total 100 until the counter crosses 100, then adds only the 10-second
   tail, producing total 110. This establishes a repeatable overlap defect;
   it does **not** establish that the real agent's entire extra total came
   from that path.

The fix persists an optional per-boot `awake_clock_high_water` and
`awake_clock_rollbacks`. These survive desktop reload, agent SQLite restart,
and disabled history. Validation treats them as accounting metadata; damaged
values cannot be silently salvaged away. Explicit agent imports retain them
and recognize legacy/extended boot aliases. A verified changed boot identity
resets the watermark and credits that new boot once. Sleep adds no awake
delta. Forward gaps above MAX_DELTA remain excluded.

This is deliberately conservative: if a real reboot or snapshot restore also
reuses the full boot identity, new usage below the old watermark is withheld
until disambiguated/caught up. No observation can distinguish those cases
using only the old GUID and a counter rewind. Likewise, restoration of both
the VM and its saved ledger cannot be detected using ledger state alone.
An entire older period cannot be reconstructed from the remaining records.

No historical total, baseline, boot count, or daily allocation was reset,
subtracted, merged or fabricated. The larger server number should be described
as the agent's retained cumulative accounting, with unresolved earlier
provenance; the desktop number approximately measures this current boot and
belongs to another ledger. Neither establishes the machine's true lifetime.

## Validation and rollout

Regression tests cover unchanged-ID rollback and repeated rollback, partial
crossing, history disabled, JSON and SQLite restart, duplicate credit, normal
changed-ID reboot, sleep/resume, forward gaps, legacy inconsistent saved
totals, explicit import, registry-read failure, and boot-sequence upgrade.

Targeted validation: 157 tests ran, 2 platform-dependent tests skipped; all
executed tests passed. The original-source rollback reproduction was also
run and compared with the fixed sampler. Another 45 compatibility,
sleep-verification, protocol and rollback checks passed; this second run
includes some of the same rollback tests. `git diff --check` passed.

Only source/tests/documentation are committed on a separate local branch.
Existing running GUI/agent/runtime processes were not restarted, nor was the
packaged server rebuilt. Restart the source-based desktop/tracker and agent
to load the new sampler; the server needs no accounting migration. Existing
totals will remain as saved. The backup above precedes any future adoption of
new accounting fields.
