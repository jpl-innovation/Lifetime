#!/usr/bin/env bash
# Restart Lifetime Server with whichever service manager the installer used. Run: sudo ./restart_server_linux.sh
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { printf 'Run this with sudo (or as root).\n' >&2; exit 1; }
if [[ -f /etc/systemd/system/lifetime-server.service && -d /run/systemd/system ]]; then
    systemctl restart lifetime-server.service
    systemctl --no-pager --lines=0 status lifetime-server.service || true
elif [[ -f /etc/init.d/lifetime-server ]] && head -n 1 /etc/init.d/lifetime-server | grep -q openrc-run; then
    rc-service lifetime-server restart
elif [[ -x /etc/init.d/lifetime-server ]]; then
    /etc/init.d/lifetime-server restart
else
    printf 'Lifetime Server is not installed as a service here.\n' >&2
    exit 1
fi
