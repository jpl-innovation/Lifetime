#!/usr/bin/env bash
# Linux builds of Lifetime.
#
#   build_linux.sh build [--server-only]   inside a Rocky Linux 8 container (glibc 2.28): PyInstaller bundles in
#                                          build-linux/dist (Lifetime/ and LifetimeServer/). Run as root. With
#                                          LIFETIME_RUST=1 (CI) the server runtime gets the Rust accelerator, or fails.
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
    # Exact versions (see requirements-server.txt). PyInstaller 6.11.1 and Qt 6.9 are the ones this glibc 2.28
    # build has always used: newer PyQt6 wheels need glibc 2.34.
    python3.12 -m pip install --disable-pip-version-check 'pyinstaller==6.11.1' 'pyinstaller-hooks-contrib==2026.9' \
        'altgraph==0.17.5' 'packaging==26.3' -r "$root/requirements-server.txt"
    if [[ "$server_only" == false ]]; then
        python3.12 -m pip install --disable-pip-version-check 'PyQt6==6.9.1' 'PyQt6-Qt6==6.9.2' 'PyQt6-sip==13.13.0'
    fi
    if [[ "${LIFETIME_RUST:-0}" == 1 ]]; then
        rust_accelerator
    fi
    export LIFETIME_SERVER_ONLY=$([[ "$server_only" == true ]] && echo 1 || echo 0)
    python3.12 -m PyInstaller --noconfirm --clean --distpath "$work/dist" --workpath "$work/pyinstaller" \
        "$root/packaging/linux/lifetime-linux.spec"
    if [[ "${LIFETIME_RUST:-0}" == 1 ]] && ! compgen -G "$work/dist/LifetimeServer/_internal/lifetime_fast*.so" >/dev/null; then
        echo "The server bundle was built without the Rust accelerator (lifetime_fast)" >&2
        exit 1
    fi
    ldd --version | sed -n 1p > "$work/dist/GLIBC-BASELINE.txt"   # sed reads to the end: head would SIGPIPE ldd under pipefail (exit 141)
    if [[ -n "${HOST_UID:-}" ]]; then
        chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$work"     # the CI host packages these files next
    fi
}

rust_accelerator() {
    # rust/lifetime_fast (docs/notes/1.3.0-rust.md) for the server runtime: Rust 1.91.0 through rustup-init 1.28.2,
    # checked against its recorded sha256, then maturin builds it here on glibc 2.28 and pip installs it for PyInstaller.
    local init_sum
    case "$arch" in
        x86_64) init_sum=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c ;;
        aarch64) init_sum=e3853c5a252fca15252d07cb23a1bdd9377a8c6f3efa01531109281ae47f841c ;;
        *) echo "No pinned rustup-init for $arch" >&2; exit 1 ;;
    esac
    dnf -y install gcc curl      # Rust links with the C compiler's linker
    mkdir -p "$work"
    local init="$work/rustup-init-1.28.2-$arch"
    curl -fsSL -o "$init" "https://static.rust-lang.org/rustup/archive/1.28.2/$arch-unknown-linux-gnu/rustup-init"
    printf '%s  %s\n' "$init_sum" "$init" | sha256sum -c -
    chmod +x "$init"
    "$init" -y --no-modify-path --profile minimal --default-toolchain 1.91.0
    export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR="$work/cargo-target"
    python3.12 -m pip install --disable-pip-version-check 'maturin==1.10.2'
    python3.12 -m maturin build --release --locked --interpreter python3.12 -m "$root/rust/lifetime_fast/Cargo.toml" \
        --out "$work/wheels"
    python3.12 -m pip install --disable-pip-version-check --no-index --find-links="$work/wheels" 'lifetime_fast==0.1.0'
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
        install -m 644 "$root/assets/lifetime.png" "$root/assets/lifetime-server.png" "$client/assets/"
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
        # Fixed releases with checksums (not "continuous"): appimagetool 1.9.1 and the type2 runtime it embeds.
        local tool_sum runtime_sum
        case "$arch" in
            x86_64) tool_sum=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
                    runtime_sum=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d ;;
            aarch64) tool_sum=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158
                     runtime_sum=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444 ;;
            *) echo "No pinned appimagetool for $arch" >&2; exit 1 ;;
        esac
        local tool="$work/appimagetool-1.9.1-$arch.AppImage" runtime="$work/runtime-20251108-$arch"
        [[ -f "$tool" ]] || curl -fsSL -o "$tool" "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-$arch.AppImage"
        [[ -f "$runtime" ]] || curl -fsSL -o "$runtime" "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-$arch"
        printf '%s  %s\n%s  %s\n' "$tool_sum" "$tool" "$runtime_sum" "$runtime" | sha256sum -c -
        chmod +x "$tool"
        ARCH="$arch" APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream --runtime-file "$runtime" "$appdir" \
            "$out/Lifetime-Linux-$arch.AppImage"
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
