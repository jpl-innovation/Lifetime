# Side-by-side protocol: Lifetime Server and Zabbix

The eight equivalent tasks from the master plan (section 14.3), each mapped to the section of the Vietnamese Zabbix
deployment guide (Zabbix 8.0 LTS on Ubuntu 22.04) that a technician would follow. This document is the protocol to
register **before** any run; it contains no results. Results go in the template at the end, losses as well as wins.

## Fairness rules (fixed before the first run)

1. **Same conditions.** The same endpoints, network, server hardware class and permissions for both products. Declare
   versions, hardware, OS, network and every prerequisite that was installed beforehand (for Zabbix: Ubuntu, MySQL,
   Apache, PHP; for Lifetime: Windows, Python or the packaged runtime, PyQt6 for the dashboard).
2. **Independent technicians.** At least five technicians who have used neither product, not employed by JPL
   Innovation. Each runs all eight tasks on both products; the order of the products is randomised per technician
   (coin toss recorded in the sheet). They may use only the product's own documentation: the Zabbix guide below and
   Lifetime's README and `docs/`.
3. **Pre-registered scoring.** The tasks, the success criteria and the scoring sheet below do not change after the
   first run. A task that needs an extension, a script or a third-party add-on to be possible is scored as such,
   never as "equal".
4. **Different metrics are not the same metric.** Zabbix's availability (ICMP ping, `system.uptime`, SLA from triggers)
   and Lifetime's awake time (time the computer was on and not asleep, from the computer's own accounting) measure
   different things. Task 4 asks for awake time with defined semantics; if a product cannot give it, say so and record
   what it gives instead. Task 6 asks for availability with coverage disclosure, where Zabbix's SLA is the natural fit.
5. **Recorded evidence.** Screen recording with the technician's permission (blur device and employee names), the
   commands typed, the logs. Raw sheets are published with the summary.
6. **Stopping.** A task is stopped as *not completed* after 3 times the slower product's median for that task or
   90 minutes, whichever is shorter; the technician may also give up.
7. **Publish everything.** Losses, ties, tasks a product could not do, and technician comments are published with the
   wins. A narrow win is claimed only with its declared scope.

## The eight tasks

| # | Task (same for both products) | Success criterion | Zabbix guide sections | Lifetime documentation |
|---|---|---|---|---|
| 1 | Install the server and reach a working dashboard over HTTPS, with administrator accounts secured. | Dashboard opens over HTTPS; default/shared credentials changed or not present. | §2 Preparation; §3 Install Zabbix Server (3.1 repository and packages, 3.2 database, 3.3 server configuration, 3.4 start services, 3.5 firewall, 3.6 web UI, 3.7 account security, 3.8 HTTPS) | README *Lifetime Server*; SERVER_OPERATIONS *Install and start* |
| 2 | Add one Windows and one Linux computer with encrypted communication. | Both report data to the server; traffic encrypted (PSK or TLS); verified in the UI. | §4 Agent 2 on Linux; §5.1 Agent 2 on Windows (installer); §6 check the server–agent connection; §7 PSK encryption (7.1 Linux, 7.2 Windows, 7.3 server side); §8 add the host | README *Connecting a computer*; SERVER_OPERATIONS *Agent enrollment* |
| 3 | Add 20 more computers in bulk, where supported. | 20 computers reporting; method scriptable without per-computer manual steps. | §5.2 Windows silent MSI install from the command line (plus host creation and template linking per host, §8, §10.4) | SERVER_OPERATIONS *Silent deployment (GPO, Intune, scripts)* |
| 4 | See how long each computer was on (awake) during the previous month, with the metric's meaning defined. | A per-computer number for the month and a written definition of what it counts (sleep, hibernation, gaps). | §10 Uptime/Downtime (10.1 prerequisites, 10.2 Uptime Tracking template, 10.3 trigger, 10.4 assign template, 10.5 laptops and accuracy); §11 SLA; §12 a service per computer; §13 SLA report | README *What it shows*, *How it counts*; dashboard computer detail and reports |
| 5 | Receive one meaningful email when a computer goes offline or its disk is nearly full. | One email for the problem and, where the product does it, one for the recovery; no flood. | §17 Email alerts (media type, user media, trigger action, test); Appendix A trigger test; triggers/templates from §10.3 | SERVER_OPERATIONS *Alerts and notifications* |
| 6 | Produce a monthly manager report with availability and a disclosure of coverage (how much of the month had data). | A shareable file or page per computer or group with availability and coverage for the month. | §13 SLA report; §14 pushing uptime/downtime by script and API (14.1-14.6); §15 central dashboard | README *Availability and reports*; dashboard Reports |
| 7 | Back up the server and restore it on a new server. | The new server shows the old history and works; what had to be redone is noted. | §18 Backup and maintenance (18.1 database backup, 18.2 retention, 18.3 updates) | SERVER_OPERATIONS *Backup and restore*, *Restore drill* |
| 8 | Diagnose and fix a computer that stopped reporting (the facilitator breaks it in a pre-agreed way: firewall rule, stopped agent service, or wrong server address). | The cause named and fixed, the computer reporting again. | §19 Common errors; §6 connection check; §20 pre-production checklist | [Runbook](runbook.md); the computer's Connect page; Server log |

Lifetime gaps to state up front, so they are scored honestly: no Linux server package (the server is Windows-first;
Linux runs from source), task 3 relies on a script or GPO/Intune that the site provides, and remote power, agent
updates and roles have no equivalent task here. Zabbix gaps to state up front: no awake-time accounting from the
computer itself (task 4), and SLA reporting needs the template, service and script steps of §10-§14.

## Scoring sheet (one row per technician, product and task)

| Field | How it is measured |
|---|---|
| Technician ID, product, task #, product order (first/second) | Recorded by the facilitator. |
| Time (minutes) | From reading the task card to the success criterion verified by the facilitator; excludes waiting for downloads over a declared bandwidth. |
| Completed | Yes / partly (what is missing) / no (stopped: why). |
| Errors | Count of actions the technician had to undo or redo, and wrong results accepted as right. |
| Manual lookups | Count of times the technician left the product's own documentation (web search, forum, asking the facilitator). |
| Interventions | Count of facilitator interventions (each one noted with its reason). |
| Confidence | The technician's answer, 1-5, to "How sure are you that this is set up correctly?" asked right after the task. |
| Recovery time (task 8) | From the break to the computer reporting again. |
| Notes | Free text: what was confusing, what was good. |

## Results template

Copy this section for each run. Do not summarise until every technician has finished.

**Run:** date, place, facilitator, product versions, server hardware, network, prerequisites installed before the run.

| Task | Lifetime median time (range) | Zabbix median time (range) | Lifetime completed (n/5) | Zabbix completed (n/5) | Errors L / Z | Lookups L / Z | Confidence median L / Z | Faster or better | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 Install | | | | | | | | | |
| 2 Add two computers | | | | | | | | | |
| 3 Add 20 | | | | | | | | | |
| 4 Monthly awake time | | | | | | | | | |
| 5 Alert email | | | | | | | | | |
| 6 Manager report | | | | | | | | | |
| 7 Backup and restore | | | | | | | | | |
| 8 Diagnose and fix | | | | | | | | | |

- Tasks Zabbix did better or Lifetime could not do:
- Tasks Lifetime did better or Zabbix could not do without extensions:
- Tasks where the products are not comparable (and why):
- Technician comments (verbatim, anonymised):
- Deviations from this protocol (any change after registration, with the reason):
