# Measured limits

**Supported size = tested size.** Lifetime Server is claimed to handle a fleet only as large as a fleet that has
actually been measured, on hardware like the server it will run on. Today that is:

| Tested | Where | Duration | Result |
|---|---|---|---|
| **50 computers** (simulated), 10 agentless checks, 5 % drop-outs per minute, 2 permanent outages | One Windows 11 PC (24 logical CPUs, 31.4 GB RAM, 9.4 GB free), server and simulator on the same machine, Python 3.12 | 2 minutes, **single run**, 2026-11-15 | Server CPU 1.8 % of one core on average (11.6 % peak), 61 MB resident memory, no sync errors, no lost live samples, live updates P99 225 ms computer to dashboard (0.2 s of it is the dashboard stream's batching). [Report](2026-11-15-50-computers-single-run/report.md) |

So the supported size is **50 computers**. Nothing larger is claimed. 200 and 500 have not been run: this PC is short
of memory and the simulator shares the machine with the server, which would distort the numbers.

That run was made before the two scaling fixes that followed it (a report no longer reads every stored performance
sample of every computer, and the live connection's periodic command check is one query instead of one per connected
computer). Both costs grow with fleet size and history, so they were invisible at 50 computers for 2 minutes; they are
covered by query-plan tests instead.

## Running a larger test

`tools/fleet_simulator.py` (developer tool, not shipped) starts a test server in a temporary directory on 127.0.0.1,
enrolls N simulated computers through the real pairing path, runs them like real computers with a dashboard open
(HTTPS reports at the server's interval, live telemetry at the rate the server asks for, random drop-outs, a few
permanent outages to open incidents, optional agentless checks) and writes `report.json` and `report.md`.

```powershell
python tools\fleet_simulator.py                                              # 50 computers, 2 minutes
python tools\fleet_simulator.py --devices 200 --duration 600 --checks 20 --output fleet-200
python tools\fleet_simulator.py --devices 500 --duration 900 --checks 50 --output fleet-500
```

- Use a machine with at least 8 GB free for 500 computers, ideally two machines: the measurement is cleaner when the
  simulator does not compete with the server. To load a server on another machine, start it there and pass
  `--attach-directory`, `--attach-address`, `--attach-admin-port` (and `--attach-pid` to measure its CPU and memory)
  with `--i-understand`; the simulated computers are enrolled there and removed at the end. Without that flag the tool
  refuses any server it did not start itself.
- Run at least 10 minutes (`--duration 600`) so that incident lag is measured for every outage (the offline rule is
  set to 1 minute and the alert engine evaluates every 15 s) and the WAL reaches a steady state.
- Commit the report under `docs/benchmarks/<date>-<N>-computers-single-run/` and update the table above. Only then
  does the supported size change.

## What the numbers mean

- **Server CPU / memory**: the server process only, sampled every 2 s (psutil when it is installed, otherwise the
  operating system's own counters). CPU is a percentage of one core.
- **SQLite and WAL**: sizes of `server.sqlite`, `monitoring.sqlite` and their WAL files; the WAL peak shows whether
  checkpoints keep up.
- **Live update latency**: from a computer sending a sample to the dashboard's live stream receiving it, on the same
  clock. It includes the stream's 0.2 s batching.
- **Missing live samples**: sent by a computer but never seen on the dashboard stream (a drop-out cut the socket, the
  per-computer rate limit, or the stream merging two samples within 0.2 s).
- **Incident lag**: how long after a computer had been offline for the rule's duration its incident opened.
- **Sync latency and errors**: each HTTPS report, including the TLS handshake; records not acknowledged at the end.
