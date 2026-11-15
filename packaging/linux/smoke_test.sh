#!/usr/bin/env bash
# CI smoke tests of the Linux builds.
#   smoke_test.sh ARTIFACTS_DIR              inside a distribution container (no systemd running, like most):
#                                            desktop bundle + AppImage --self-check headless, tracker records, server
#                                            binary on 127.0.0.1, a client enrolls with a pairing code and syncs
#   smoke_test.sh ARTIFACTS_DIR --installer  the same, with the server installed by install_server_linux.sh through its
#                                            SysV fallback and administered by root over the Unix socket (SO_PEERCRED),
#                                            then restarted and uninstalled
#   smoke_test.sh ARTIFACTS_DIR --docker     on the CI host: a client enrolls with the container image's server
#                                            (container 'lifetime-smoke', port 8443 published on 127.0.0.1)
set -euo pipefail
artifacts="$(cd -- "$1" && pwd)"
mode="${2:-direct}"
arch="$(uname -m)"
. /etc/os-release
echo "=== ${PRETTY_NAME:-$ID} ($arch, glibc $(getconf GNU_LIBC_VERSION 2>/dev/null || echo '?'), mode $mode)"

work="$(mktemp -d)"
export QT_QPA_PLATFORM=offscreen LIFETIME_DATA_DIR="$work/desktop-data"
tar -xzf "$artifacts/Lifetime-Linux-$arch.tar.gz" -C "$work"
bundle="$work/Lifetime-Linux-$arch"
address=https://127.0.0.1:8443

if [[ "$mode" != --docker ]]; then
    # Libraries every desktop already has (OpenGL/EGL, fontconfig, xkbcommon, D-Bus, GLib) and the tools used here;
    # the bundle brings Python and Qt itself.
    case " $ID ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*)
            if [[ "$ID" == debian && "${VERSION_ID:-}" == 10 ]]; then      # buster moved to the archive
                sed -i -e 's|deb.debian.org|archive.debian.org|g' -e 's|security.debian.org|archive.debian.org|g' \
                    -e '/buster-updates/d' /etc/apt/sources.list
            fi
            apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libgl1 libegl1 libfontconfig1 libxkbcommon0 \
                libdbus-1-3 libglib2.0-0 tar gzip passwd procps >/dev/null ;;
        *" fedora "*|*" rhel "*|*" centos "*)
            dnf -y -q install mesa-libGL mesa-libEGL fontconfig libxkbcommon dbus-libs glib2 tar gzip shadow-utils \
                util-linux procps-ng >/dev/null ;;
        *" suse "*|*" opensuse "*)
            zypper -n -q install Mesa-libGL1 Mesa-libEGL1 fontconfig libxkbcommon0 libdbus-1-3 libglib-2_0-0 tar \
                gzip shadow util-linux procps >/dev/null ;;
        *" arch "*)
            pacman -Sy --noconfirm --needed --quiet archlinux-keyring >/dev/null
            pacman -S --noconfirm --needed --quiet libglvnd fontconfig libxkbcommon dbus glib2 tar gzip procps-ng >/dev/null ;;
        *) echo "Unknown distribution $ID"; exit 1 ;;
    esac

    echo '--- desktop bundle'
    "$bundle/Lifetime" --self-check
    timeout 8 "$bundle/LifetimeTracker" --run || [[ $? == 124 ]]
    "$bundle/LifetimeTracker" --print

    echo '--- AppImage'
    appimage="$work/Lifetime.AppImage"
    cp "$artifacts/Lifetime-Linux-$arch.AppImage" "$appimage"
    chmod +x "$appimage"
    APPIMAGE_EXTRACT_AND_RUN=1 "$appimage" --self-check
    APPIMAGE_EXTRACT_AND_RUN=1 "$appimage" --run-tool LifetimeAgent --help >/dev/null
fi

echo '--- server'
if [[ "$mode" == --docker ]]; then
    admin() { docker exec lifetime-smoke lifetime-admin "$@"; }
else
    tar -xzf "$artifacts/Lifetime-Server-Linux-$arch.tar.gz" -C "$work"
    package="$work/Lifetime-Server-Linux-$arch"
    if [[ "$mode" == --installer ]]; then
        "$package/install_server_linux.sh" --address "$address" --host 127.0.0.1 --init sysv
        server=/opt/lifetime-server/LifetimeServerRuntime
        data=/var/lib/lifetime-server
        [[ "$(stat -c '%a %U' "$data")" == "700 lifetime" ]] || { echo "data directory permissions"; exit 1; }
    else
        server="$package/LifetimeServerRuntime"
        data="$work/server-data"
        "$server" console --directory "$data" --address "$address" --host 127.0.0.1 --port 8443 --admin-port 8444 \
            > "$work/server.log" 2>&1 &
    fi
    admin() { "$server" admin --directory "$data" --admin-port 8444 "$@"; }
fi
for _ in $(seq 90); do
    admin --operation server_info --field fingerprint >/dev/null 2>&1 && break
    sleep 1
done
admin --operation server_info --field fingerprint
if [[ "$mode" != direct ]]; then
    # root (installer) or the lifetime-admin service account (container) over the Unix socket, not the token
    [[ "$(admin --operation whoami --field channel)" == socket ]] || { echo "admin did not use the Unix socket"; exit 1; }
    [[ "$(admin --operation whoami --field role)" == admin ]]
fi
if [[ "$mode" == --installer ]]; then
    stat -c '%a %G' /run/lifetime-server/admin-8444.sock | grep -qx '660 lifetime-admin' || { echo "socket permissions"; exit 1; }
fi

echo '--- enroll and sync a client'
code="$(admin --operation invitation --field code)"
agent_dir="$work/agent"
printf '%s' "$code" | "$bundle/LifetimeAgent" --directory "$agent_dir" --connection-stdin --server "$address" \
    --display-name "smoke-$ID" || true      # waiting for approval is the expected first state
request="$(admin --operation pending_enrollments --field request_id)"
device="$(admin --operation pending_enrollments --field device_id)"
admin --operation approve --arguments "{\"request_id\": \"$request\"}" >/dev/null
"$bundle/LifetimeAgent" --directory "$agent_dir" --run > "$work/agent.log" 2>&1 &
agent_pid=$!
records=0
for _ in $(seq 90); do
    records="$(admin --operation device --arguments "{\"device_id\": \"$device\"}" --field record_count || echo 0)"
    [[ "$records" =~ ^[1-9] ]] && break
    sleep 2
done
kill "$agent_pid" 2>/dev/null || true
if ! [[ "$records" =~ ^[1-9] ]]; then
    echo "The client did not sync"; tail -n 50 "$work/agent.log" "$work/server.log" 2>/dev/null || true; exit 1
fi
echo "client synced: $records record(s)"
if [[ "$mode" == --installer ]]; then
    "$package/restart_server_linux.sh"
    for _ in $(seq 60); do
        admin --operation server_info --field fingerprint >/dev/null 2>&1 && break
        sleep 1
    done
    admin --operation device --arguments "{\"device_id\": \"$device\"}" --field record_count
    "$package/uninstall_server_linux.sh"
    [[ ! -e /opt/lifetime-server && -d /var/lib/lifetime-server ]]      # data is kept without --purge
fi
echo "=== OK ${PRETTY_NAME:-$ID} ($mode)"
