# Fleet simulator: 50 computers, single run

- Date: 2026-11-15T09:20:00.990692Z
- Machine: Windows 11 (10.0.26200), Intel64 Family 6 Model 198 Stepping 2, GenuineIntel, 24 logical CPUs, 31.4 GB RAM (9.4 GB free at start), Python 3.12.10
- Target: test server started on 127.0.0.1; duration 120 s (measured 130.2 s); 10 agentless checks; drop-outs 0.05 per computer per minute; 2 computers taken offline for good
- Enrollment of all computers (code, enroll, approve, claim): 0.35 s

| Measure | Result |
|---|---|
| Server CPU (% of one core, GetProcessTimes/K32GetProcessMemoryInfo) | mean 1.8, P95 4.7, max 11.6 |
| Server memory (resident MB) | start 45.0, peak 61.0, end 61.0 |
| Simulator CPU (% of one core) | 1.7 |
| SQLite (MB) | server sqlite end 0.45, server wal peak 3.95, monitoring sqlite end 0.08, monitoring wal peak 0.32, access sqlite end 0.0 |
| Live samples sent / reached the dashboard stream | 3041 / 3041 (missing 0, 0.0 %) |
| Live update latency, computer to dashboard | P50 205.1 ms, P95 208.9 ms, P99 224.5 ms, max 302.8 ms (n=3041) |
| Live reconnects / refused | 22 / 0 |
| HTTPS syncs / errors | 194 / none (records not acknowledged at the end: 0) |
| Sync latency | P50 36.9 ms, P95 103.3 ms, P99 147.6 ms, max 176.0 ms (n=194) |
| Dashboard overview + fleet calls | 48 calls, 0 errors, P50 2.6 ms, P95 8.1 ms, P99 52.7 ms, max 52.7 ms (n=48) |
| Random drop-outs | 5 |
| Incident engine lag (offline for 1 min, then opened) | 1 of 2 opened; P50 9.4 s, P95 9.4 s, P99 9.4 s, max 9.4 s (n=1); other incidents: 0 |
| Agentless checks ok | 10 of 10; P50 0.0 ms, P95 0.0 ms, P99 0.0 ms, max 0.0 ms (n=10) |
| Refused requests in the server log | 0 |

Missing live samples are samples a computer sent that never appeared on the dashboard stream: lost when a drop-out cut the socket, refused by the per-computer rate limit, or merged by the stream (it sends the newest sample per computer every 0.2 s). Latency includes that 0.2 s batching. Incident lag is measured from the moment the computer had been offline for the rule's duration; the alert engine evaluates every 15 s.

This is a single run on one machine, with the simulator and the server sharing it. It shows that this size works here; it is not a capacity limit and not a guarantee for other hardware.
Note added after the run: one of the two computers taken offline for good had not opened its incident when the run
ended. An HTTPS report that was already on its way when its socket closed counts as the computer speaking again, so
the server then waits for 30 s of silence before calling it offline; its incident would open about 30 s later than
the other one, just after this 2-minute run stopped. Use a longer --duration to measure incident lag properly.
This run was made before the scaling fixes in the next commit (indexed performance sample trim, one command query per
hub pass); at 50 computers those costs were too small to show.
