#!/usr/bin/env bash
# Install or upgrade Lifetime Server on Linux (any glibc 2.28+ distribution). Run from the unpacked
# Lifetime-Server-Linux-ARCH folder:  sudo ./install_server_linux.sh --address https://server.school.lan:8443
#
#   program   /opt/lifetime-server (the previous version is kept in /opt/lifetime-server.previous for rollback)
#   data      /var/lib/lifetime-server (0700, system account 'lifetime')
#   settings  /etc/lifetime-server/server.conf
#   service   systemd unit; without systemd an OpenRC or SysV init script (--init to choose)
#   admins    the Unix socket in /run/lifetime-server is 0660, group 'lifetime-console': members may connect, nothing
#             more. Roles come from set_role (default deny); root and members of 'lifetime-admin' are always Admin
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: sudo ./install_server_linux.sh [--address https://NAME:8443] [--host 0.0.0.0] [--port 8443]
                                      [--admin-port 8444] [--init auto|systemd|openrc|sysv|none] [--no-start]
                                      [--admin USER]
  --address     the address computers use to reach this server (default: this server's own IP address)
  --init        service manager; auto picks systemd, then OpenRC, then a SysV script
  --admin USER  also add USER to the lifetime-admin and lifetime-console groups (Admin in the dashboard);
                default: the account that ran sudo
EOF
}

address='' host='' port='' admin_port='' init=auto start=true extra_admin=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --address) address="${2:?}"; shift 2 ;;
        --host) host="${2:?}"; shift 2 ;;
        --port) port="${2:?}"; shift 2 ;;
        --admin-port) admin_port="${2:?}"; shift 2 ;;
        --init) init="${2:?}"; shift 2 ;;
        --no-start) start=false; shift ;;
        --admin) extra_admin="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
program=/opt/lifetime-server
data=/var/lib/lifetime-server
config_dir=/etc/lifetime-server
config="$config_dir/server.conf"

fail() { printf 'Lifetime Server: %s\n' "$1" >&2; exit 1; }

[[ "$(id -u)" == 0 ]] || fail 'run this installer with sudo (or as root).'
[[ -x "$source_dir/LifetimeServerRuntime" ]] || fail 'LifetimeServerRuntime is missing; run this from the unpacked Lifetime-Server-Linux folder.'

# glibc 2.28 or newer (musl distributions such as Alpine are not supported: use the container image there).
glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')" || glibc=''
if [[ -z "$glibc" ]]; then
    fail 'this system does not use glibc (for example Alpine/musl). Use the container image instead (docs/SERVER_OPERATIONS.md).'
fi
if [[ "$(printf '%s\n2.28\n' "$glibc" | sort -V | head -n 1)" != 2.28 ]]; then
    fail "glibc $glibc is older than 2.28 (Debian 10, Ubuntu 20.04, RHEL 8). Use the container image instead."
fi

address_pattern='^https://[][A-Za-z0-9.:-]+$'      # name or [IPv6], optional :port; ']' must come first in [...]
if [[ -n "$address" && ! "$address" =~ $address_pattern ]]; then
    fail 'the address must look like https://server.school.lan:8443'
fi
for value in "$port" "$admin_port"; do
    [[ -z "$value" || "$value" =~ ^[0-9]{1,5}$ ]] || fail 'ports must be numbers'
done
case "$init" in auto|systemd|openrc|sysv|none) ;; *) fail '--init must be auto, systemd, openrc, sysv or none' ;; esac

# ---- account, groups, directories ----
command -v groupadd >/dev/null && command -v useradd >/dev/null || fail 'groupadd/useradd are needed (package shadow or passwd).'
getent group lifetime >/dev/null || groupadd --system lifetime
getent group lifetime-admin >/dev/null || groupadd --system lifetime-admin
console_created=false
getent group lifetime-console >/dev/null || { groupadd --system lifetime-console; console_created=true; }
if ! getent passwd lifetime >/dev/null; then
    nologin="$(command -v nologin || echo /bin/false)"
    useradd --system --gid lifetime --home-dir "$data" --no-create-home --shell "$nologin" \
        --comment 'Lifetime Server' lifetime
fi
# The service account creates the admin socket with group lifetime-console, so it must belong to that group.
usermod -a -G lifetime-admin,lifetime-console lifetime
if [[ "$console_created" == true ]]; then
    # Upgrade from a version whose socket used group lifetime-admin: its members keep their access.
    for member in $(getent group lifetime-admin | cut -d: -f4 | tr ',' ' '); do
        gpasswd -a "$member" lifetime-console >/dev/null
    done
fi
# Admin in the dashboard. gpasswd, unlike usermod, also adds directory accounts (AD/LDAP through sssd) that are not
# in /etc/passwd.
admin_groups() { gpasswd -a "$1" lifetime-admin >/dev/null && gpasswd -a "$1" lifetime-console >/dev/null; }
if [[ -n "$extra_admin" ]]; then
    getent passwd "$extra_admin" >/dev/null || fail "no such user: $extra_admin"
    admin_groups "$extra_admin" || fail "could not add $extra_admin to the groups lifetime-admin and lifetime-console"
elif [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    # The person installing with sudo administers it without another step; never a reason to stop the install.
    if admin_groups "$SUDO_USER"; then
        extra_admin="$SUDO_USER"
    else
        printf 'Lifetime Server: %s could not be made Admin. Run: sudo gpasswd -a %s lifetime-admin && sudo gpasswd -a %s lifetime-console\n' \
            "$SUDO_USER" "$SUDO_USER" "$SUDO_USER" >&2
    fi
fi
install -d -m 0700 -o lifetime -g lifetime "$data"
chmod 0700 "$data"
install -d -m 0755 -o root -g root "$config_dir"

# ---- settings ----
if [[ ! -f "$config" ]]; then
    if [[ -z "$address" ]]; then
        # No --address: this server's own IPv4 address, which every computer on the network can reach.
        ip_address="$(ip -o -f inet addr show scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')"
        [[ -n "$ip_address" ]] || fail 'first install: give --address https://NAME:8443 (the name computers use for this server).'
        address="https://$ip_address:${port:-8443}"
        printf 'Server address: %s\n' "$address"
    fi
    install -m 0640 -o root -g lifetime "$source_dir/linux/server.conf" "$config"
fi
set_value() {   # KEY VALUE: replace the line in server.conf
    local key="$1" value="$2" temporary
    temporary="$(mktemp "$config_dir/.server.conf.XXXXXX")"
    grep -v "^$key=" "$config" > "$temporary" || true
    printf '%s=%s\n' "$key" "$value" >> "$temporary"
    chown root:lifetime "$temporary"
    chmod 0640 "$temporary"
    mv -f "$temporary" "$config"
}
[[ -z "$address" ]] || set_value LIFETIME_ADDRESS "$address"
[[ -z "$host" ]] || set_value LIFETIME_HOST "$host"
[[ -z "$port" ]] || set_value LIFETIME_PORT "$port"
[[ -z "$admin_port" ]] || set_value LIFETIME_ADMIN_PORT "$admin_port"
# shellcheck disable=SC1090
. "$config"
[[ "$LIFETIME_PORT" != "$LIFETIME_ADMIN_PORT" ]] || fail 'public and loopback administration ports must differ.'

# ---- service manager ----
if [[ "$init" == auto ]]; then
    if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; then init=systemd
    elif command -v openrc-run >/dev/null || [[ -x /sbin/openrc-run ]]; then init=openrc
    elif [[ -d /etc/init.d ]]; then init=sysv
    else init=none
    fi
fi
service_stop() {
    case "$init" in
        systemd) systemctl stop lifetime-server.service 2>/dev/null || true ;;
        openrc) rc-service lifetime-server stop 2>/dev/null || true ;;
        sysv) [[ -x /etc/init.d/lifetime-server ]] && /etc/init.d/lifetime-server stop || true ;;
    esac
}
service_stop

# ---- program (atomic swap; previous version kept for rollback) ----
rm -rf "$program.new"
mkdir -p "$program.new/linux" "$program.new/docs"
cp -a "$source_dir/LifetimeServerRuntime" "$source_dir/_internal" "$program.new/"
cp -a "$source_dir/linux/." "$program.new/linux/"
[[ -d "$source_dir/docs" ]] && cp -a "$source_dir/docs/." "$program.new/docs/"
for script in install_server_linux.sh uninstall_server_linux.sh restart_server_linux.sh; do
    install -m 0755 "$source_dir/$script" "$program.new/$script"
done
chown -R root:root "$program.new"
chmod -R go-w "$program.new"
if [[ -d "$program" ]]; then
    rm -rf "$program.previous"
    mv "$program" "$program.previous"
fi
mv "$program.new" "$program"

case "$init" in
    systemd)
        install -m 0644 "$program/linux/lifetime-server.service" /etc/systemd/system/lifetime-server.service
        systemctl daemon-reload
        systemctl enable lifetime-server.service >/dev/null
        [[ "$start" == false ]] || systemctl restart lifetime-server.service ;;
    openrc)
        install -m 0755 "$program/linux/lifetime-server.openrc" /etc/init.d/lifetime-server
        rc-update add lifetime-server default >/dev/null
        [[ "$start" == false ]] || rc-service lifetime-server restart ;;
    sysv)
        mkdir -p /etc/init.d
        install -m 0755 "$program/linux/lifetime-server.sysv" /etc/init.d/lifetime-server
        if command -v update-rc.d >/dev/null; then update-rc.d lifetime-server defaults >/dev/null 2>&1 || true
        elif command -v chkconfig >/dev/null; then chkconfig --add lifetime-server >/dev/null 2>&1 || true
        else printf 'Add /etc/init.d/lifetime-server to your boot sequence with your init system'"'"'s tool.\n'
        fi
        [[ "$start" == false ]] || /etc/init.d/lifetime-server start ;;
    none)
        printf 'No service manager found. Start the server with:\n  sudo -u lifetime %s/LifetimeServerRuntime console --directory %s --address %s --host %s --port %s --admin-port %s\n' \
            "$program" "$data" "$LIFETIME_ADDRESS" "$LIFETIME_HOST" "$LIFETIME_PORT" "$LIFETIME_ADMIN_PORT" ;;
esac

# ---- firewall hint (never applied automatically; 8444 is loopback only and must never be opened) ----
subnet=''
if command -v ip >/dev/null; then
    cidr="$(ip -o -f inet addr show scope global 2>/dev/null | awk '{print $4; exit}')"
    if [[ "$cidr" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)\.([0-9]+)/([0-9]+)$ ]]; then
        bits="${BASH_REMATCH[5]}"
        value=$(( (BASH_REMATCH[1] << 24) | (BASH_REMATCH[2] << 16) | (BASH_REMATCH[3] << 8) | BASH_REMATCH[4] ))
        mask=$(( bits == 0 ? 0 : (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
        value=$(( value & mask ))
        subnet="$(( (value >> 24) & 255 )).$(( (value >> 16) & 255 )).$(( (value >> 8) & 255 )).$(( value & 255 ))/$bits"
    fi
fi
subnet="${subnet:-192.168.1.0/24}"
printf '\nLifetime Server is installed (%s). Data: %s. Settings: %s.\n' "$init" "$data" "$config"
printf 'Computers connect to %s. Allow port %s from your local network only, for example:\n' "$LIFETIME_ADDRESS" "$LIFETIME_PORT"
if command -v ufw >/dev/null; then
    printf '  sudo ufw allow from %s to any port %s proto tcp\n' "$subnet" "$LIFETIME_PORT"
    printf '  sudo ufw allow from %s to any port 5353 proto udp    # so computers can find the server on their own\n' "$subnet"
fi
if command -v firewall-cmd >/dev/null; then
    printf "  sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=%s port port=%s protocol=tcp accept' && sudo firewall-cmd --reload\n" "$subnet" "$LIFETIME_PORT"
    printf '  sudo firewall-cmd --permanent --add-service=mdns && sudo firewall-cmd --reload    # so computers can find the server on their own\n'
fi
printf 'Never open port %s: administration stays on this server.\n' "$LIFETIME_ADMIN_PORT"
printf 'Administer: sudo %s/LifetimeServerRuntime admin --directory %s --admin-port %s --operation invitation\n' "$program" "$data" "$LIFETIME_ADMIN_PORT"
printf 'Other accounts: sudo usermod -a -G lifetime-console USER, then give a role (set_role); membership alone grants nothing.\n'
# Lifetime for Linux already installed for that person: its hidden Lifetime Server entry goes into the app menu.
if [[ -n "$extra_admin" ]]; then
    entry="$(getent passwd "$extra_admin" | cut -d: -f6)/.local/share/applications/lifetime-server.desktop"
    # Edited as that person, never as root in their home folder (where a link could point anywhere).
    if [[ -f "$entry" && ! -L "$entry" ]] && command -v runuser >/dev/null; then
        runuser -u "$extra_admin" -- sed -i '/^NoDisplay=true$/d' "$entry" || true
        printf 'Dashboard: %s signs out and back in once, then opens Lifetime Server from the app menu.\n' "$extra_admin"
    else
        printf 'Dashboard: install Lifetime for Linux as %s (install_linux.sh); it adds Lifetime Server to the app menu.\n' "$extra_admin"
    fi
fi
printf 'Group membership takes effect at your next sign-in.\n'
