#!/usr/bin/env bash
# Remove Lifetime Server from Linux. Keeps the data (/var/lib/lifetime-server), the settings and the 'lifetime'
# account unless --purge is given. Run: sudo ./uninstall_server_linux.sh [--purge]
set -euo pipefail
purge=false
case "${1:-}" in
    '') ;;
    --purge) purge=true ;;
    *) printf 'Usage: sudo ./uninstall_server_linux.sh [--purge]\n'; exit 2 ;;
esac
[[ "$(id -u)" == 0 ]] || { printf 'Run this with sudo (or as root).\n' >&2; exit 1; }

if [[ -f /etc/systemd/system/lifetime-server.service ]]; then
    systemctl disable --now lifetime-server.service 2>/dev/null || true
    rm -f /etc/systemd/system/lifetime-server.service
    systemctl daemon-reload 2>/dev/null || true
fi
if [[ -f /etc/init.d/lifetime-server ]]; then
    if command -v rc-service >/dev/null && head -n 1 /etc/init.d/lifetime-server | grep -q openrc-run; then
        rc-service lifetime-server stop 2>/dev/null || true
        rc-update del lifetime-server default 2>/dev/null || true
    else
        /etc/init.d/lifetime-server stop || true
        if command -v update-rc.d >/dev/null; then update-rc.d -f lifetime-server remove >/dev/null 2>&1 || true
        elif command -v chkconfig >/dev/null; then chkconfig --del lifetime-server >/dev/null 2>&1 || true
        fi
    fi
    rm -f /etc/init.d/lifetime-server
fi
rm -rf /opt/lifetime-server /opt/lifetime-server.previous /opt/lifetime-server.new /run/lifetime-server
if [[ "$purge" == true ]]; then
    rm -rf /var/lib/lifetime-server /etc/lifetime-server /var/log/lifetime-server.log
    getent passwd lifetime >/dev/null && userdel lifetime
    getent group lifetime >/dev/null && groupdel lifetime
    getent group lifetime-admin >/dev/null && groupdel lifetime-admin
    getent group lifetime-console >/dev/null && groupdel lifetime-console
    printf 'Lifetime Server, its data, settings and account were removed.\n'
else
    printf 'Lifetime Server was removed. Data kept in /var/lib/lifetime-server, settings in /etc/lifetime-server (--purge removes them).\n'
fi
