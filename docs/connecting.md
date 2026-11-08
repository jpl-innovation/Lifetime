# Connecting computers

On the server, choose Add device. On the computer, choose Connect and enter the
code. Review sharing and finish, then approve the computer in the server's open
Add a computer window. No address, server list or certificate details are required.
For a VPN or another network, Other network? exposes a line to paste into the same
code input. The address in that line must be reachable from the computer.

Search runs off the Qt thread. A pasted line uses its origin; otherwise DNS-SD is
tried before a bounded scan of local IPv4 /24 networks. The scan skips loopback and
link-local addresses and uses TCP 8443, 0.4-second connections and at most 64
threads. Discovery is untrusted. Every candidate goes through the existing TLS
certificate commitment check before any application data is sent. A matching
certificate only opens the sharing review; Finish authorizes the first request.

Awake time is required. Daily history and computer specifications/performance
start on. Health starts on only when local Health consent is on. Power control
starts off. The machine agent applies those exact choices. Pending approval is
read from local status every two seconds; a background agent performs the network
polling and reporting. Policy and phase in public status contain no credentials.

Windows uses one waited RunAs process per Finish, policy change or disconnect.
Temporary connection material is protected for its owner, Administrators and
SYSTEM, then removed in finally, including on UAC cancellation. Source runs spawn
a detached hidden agent after connecting. A frozen build needs LifetimeAgent.exe
beside the desktop executable. If install_agent_windows.ps1 is also beside it,
Finish calls Packaged mode with the selected policy and display name. The installer
still refuses an existing registered task rather than silently replacing it;
upgrades need the documented uninstall/install process with data preserved.

Linux uses reporting_directory() and starts a detached user agent, then tries
systemctl --user enable --now lifetime-company-agent.service. The agent lock
refuses a second capture loop. The Windows server installer creates an inbound
TCP 8443 LocalSubnet rule for Domain and Private profiles if its rule is absent.

Run affected modules during development. `python -m unittest discover` is the final
suite. `python render_connect.py` creates ten deterministic 1280x800 EN/VI captures
under the ignored shots/connect directory without network calls or agent changes.
Real UAC interaction, packaged binaries, service startup and firewall mutation
need a Windows installation check; unit tests fake these operating-system calls.
