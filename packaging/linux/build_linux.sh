#!/usr/bin/env bash
# Linux builds of Lifetime.
#
#   build_linux.sh build [--server-only]   inside a Rocky Linux 8 container (glibc 2.28): PyInstaller bundles in
#                                          build-linux/dist (Lifetime/ and LifetimeServer/). Run as root.
#   build_linux.sh package                 on any Linux host: the release files in build-linux/out
#                                          (Lifetime-Linux-ARCH.AppImage and .tar.gz, Lifetime-Server-Linux-ARCH.tar.gz).
#
# Building on glibc 2.28 is what makes the bundles run on Debian 10+, Ubuntu 20.04+, RHEL/Alma/Rocky 8+, Fedora,
# openSUSE Leap 15.3+, Arch and their derivatives: a binary runs on its build system's glibc and every newer one.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
arch="$(uname -m)"
work="$root/build-linux"
stage="${1:-}"
server_only=false
[[ "${2:-}" == "--server-only" ]] && server_only=true

build() {
    dnf -y install epel-release
    dnf -y install python3.12 python3.12-pip python3.12-devel binutils file findutils \
        fontconfig freetype dbus-libs glib2 libX11-xcb libxkbcommon libxkbcommon-x11 mesa-libEGL mesa-libGL \
        xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-cursor \
        libwayland-client libwayland-cursor libwayland-egl
    python3.12 -m pip install --disable-pip-version-check 'pyinstaller==6.11.1' -r "$root/requirements-server.txt"
    if [[ "$server_only" == false ]]; then
        python3.12 -m pip install --disable-pip-version-check 'PyQt6>=6.7,<6.10'
    fi
    export LIFETIME_SERVER_ONLY=$([[ "$server_only" == true ]] && echo 1 || echo 0)
    python3.12 -m PyInstaller --noconfirm --clean --distpath "$work/dist" --workpath "$work/pyinstaller" \
        "$root/packaging/linux/lifetime-linux.spec"
    ldd --version | head -n 1 > "$work/dist/GLIBC-BASELINE.txt"
    if [[ -n "${HOST_UID:-}" ]]; then
        chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$work"     # the CI host packages these files next
    fi
}

copy_docs() {
    install -m 644 "$root/README.md" "$root/SECURITY.md" "$root/LICENSE" "$root/THIRD_PARTY_NOTICES.md" "$1/"
}

package() {
    local out="$work/out"
    mkdir -p "$out"
    if [[ -d "$work/dist/Lifetime" ]]; then
        # tar.gz: the bundle plus a per-user installer (no root, no system Python)
        local client="$work/Lifetime-Linux-$arch"
        rm -rf "$client"
        mkdir -p "$client/assets"
        cp -a "$work/dist/Lifetime/." "$client/"
        install -m 755 "$root/install_linux.sh" "$root/uninstall_linux.sh" "$client/"
        install -m 644 "$root/assets/lifetime.png" "$client/assets/"
        copy_docs "$client"
        tar -C "$work" -czf "$out/Lifetime-Linux-$arch.tar.gz" "Lifetime-Linux-$arch"
        # AppImage: the same bundle under usr/lib/lifetime
        local appdir="$work/AppDir"
        rm -rf "$appdir"
        mkdir -p "$appdir/usr/lib/lifetime"
        cp -a "$work/dist/Lifetime/." "$appdir/usr/lib/lifetime/"
        install -m 755 "$root/packaging/linux/AppRun" "$appdir/AppRun"
        install -m 644 "$root/packaging/linux/lifetime.desktop" "$appdir/lifetime.desktop"
        install -m 644 "$root/assets/lifetime.png" "$appdir/lifetime.png"
        cp "$appdir/lifetime.png" "$appdir/.DirIcon"
        local tool="$work/appimagetool-$arch.AppImage"
        if [[ ! -x "$tool" ]]; then
            curl -fsSL -o "$tool" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$arch.AppImage"
            chmod +x "$tool"
        fi
        ARCH="$arch" APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$appdir" "$out/Lifetime-Linux-$arch.AppImage"
    fi
    local server="$work/Lifetime-Server-Linux-$arch"
    rm -rf "$server"
    mkdir -p "$server/linux" "$server/docs"
    cp -a "$work/dist/LifetimeServer/." "$server/"
    install -m 755 "$root/install_server_linux.sh" "$root/uninstall_server_linux.sh" "$root/restart_server_linux.sh" "$server/"
    install -m 644 "$root/packaging/linux/lifetime-server.service" "$root/packaging/linux/server.conf" "$server/linux/"
    install -m 755 "$root/packaging/linux/lifetime-server.openrc" "$root/packaging/linux/lifetime-server.sysv" "$server/linux/"
    install -m 644 "$root/docs/SERVER_OPERATIONS.md" "$root/docs/SERVER_PROTOCOL.md" "$server/docs/"
    copy_docs "$server"
    tar -C "$work" -czf "$out/Lifetime-Server-Linux-$arch.tar.gz" "Lifetime-Server-Linux-$arch"
    (cd "$out" && sha256sum Lifetime-*"$arch"* > "SHA256SUMS-$arch.txt")
    ls -l "$out"
}

case "$stage" in
    build) build ;;
    package) package ;;
    *) echo "Usage: build_linux.sh build [--server-only] | package" >&2; exit 2 ;;
esac
