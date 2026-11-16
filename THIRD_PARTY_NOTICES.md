# Third-party notices

Lifetime is free software, Copyright (C) 2026 JPL Innovation, licensed under the **GNU General Public License,
version 3 only** (GPL-3.0-only). The full text is in [LICENSE](LICENSE). You may copy, change and redistribute it,
including for money, as long as the people who receive it get the same freedoms and the source code.

**Additional permission for OpenSSL (GPL version 3, section 7).** JPL Innovation gives permission to link or combine
Lifetime's own code with the OpenSSL library, or a modified version of it that uses the same licenses as OpenSSL,
and to distribute the result. This permission covers only the code JPL Innovation holds the copyright to; it cannot
change the licenses of the other components listed below. The Linux packages contain OpenSSL 1.1.1 (see below).

**Source code.** The complete source for every release is in the public repository
<https://github.com/jpl-innovation/Lifetime>: use the release tag that matches the version you received. Every
downloadable package is built from that tagged source by the published build scripts (`build_windows.ps1`,
`build_server_windows.ps1`, `packaging/linux/build_linux.sh`) in the published workflow
(`.github/workflows/windows.yml`).

## Components used by Lifetime

Licenses below are taken from each package's own published metadata (checked 15 and 16 November 2026 against the
pinned versions). Each component keeps its own copyright and license. The exact versions a release was built with are
in `requirements-server.txt`, `requirements-desktop.txt` and `requirements-build.txt` at that release's tag, and for
the Linux packages (which use an older Qt) in `packaging/linux/build_linux.sh`.

| Component | Used for | License | Project |
|---|---|---|---|
| PyQt6 | Desktop and server windows | GPL-3.0-only (Riverbank Computing; a commercial license also exists) | <https://www.riverbankcomputing.com/software/pyqt/> |
| Qt 6 (PyQt6-Qt6) | User interface libraries | LGPL-3.0 (The Qt Company) | <https://www.qt.io/> |
| PyQt6-sip | PyQt6 runtime support | BSD-2-Clause | <https://pypi.org/project/PyQt6-sip/> |
| cryptography | Server certificates and TLS identity | Apache-2.0 OR BSD-3-Clause (used under BSD-3-Clause) | <https://cryptography.io/> |
| cffi, pycparser | Needed by cryptography | MIT-0; BSD-3-Clause | <https://cffi.readthedocs.io/> |
| websockets | Live connection between computers and the server | BSD-3-Clause | <https://websockets.readthedocs.io/> |
| tzdata (server) | IANA time zone data for schedules and availability | Apache-2.0 (data: public domain) | <https://github.com/python/tzdata> |
| pywin32 (server on Windows) | Windows service support | PSF-2.0 | <https://github.com/mhammond/pywin32> |
| Python | Runtime | PSF-2.0 | <https://www.python.org/> |
| PyInstaller (build tool; its start-up loader is part of every packaged program) | Packaging the Windows and Linux programs | GPL-2.0-or-later with an exception that leaves bundled programs under their own license | <https://pyinstaller.org/> |

No fonts are bundled; Lifetime uses the operating system's own fonts.

## Also inside the packaged programs

The packaged programs carry the Python runtime, and Python brings libraries of its own.

- **Windows programs (`.exe`)**: the Python runtime from python.org with the libraries it ships: OpenSSL
  (Apache-2.0), SQLite (public domain), zlib, bzip2, liblzma (xz), libffi, Expat and mpdecimal (permissive licenses,
  listed in Python's own license file).
- **Linux packages (AppImage and `.tar.gz`)**: Python 3.12 and the shared libraries it and Qt need, copied by the
  build from **Rocky Linux 8** (the build container; glibc itself is not included). The main ones: OpenSSL 1.1.1
  (`libssl`, `libcrypto`; OpenSSL and SSLeay licenses), SQLite (public domain), zlib, bzip2, xz, zstd, lz4, libffi,
  Expat, mpdecimal, PCRE, libpng, FreeType, Fontconfig, the X11, XCB, Wayland and xkbcommon client libraries (MIT
  and similar permissive licenses), ICU (Unicode license), D-Bus (AFL-2.1 or GPL-2.0-or-later), MIT Kerberos, and
  under the LGPL (2.1 or later, some 3.0 or later): GLib, GnuTLS, Nettle, GMP, libgcrypt, libgpg-error, libtasn1,
  libidn2, libunistring, util-linux's `libmount`, `libblkid` and `libuuid`, and systemd's `libsystemd`; under the
  GPL-3.0-or-later: GNU Readline; and the GCC runtime (`libstdc++`, `libgcc`; GPL-3.0 with the GCC Runtime Library
  Exception). The server package contains only Python's own share of that list (OpenSSL, SQLite, zlib, bzip2, xz,
  libffi, Expat, mpdecimal, Readline with ncurses, `libuuid` and `libgcc`; no Qt and no graphics libraries). Their
  source code is Rocky Linux 8's: <https://dl.rockylinux.org/> (source packages of the same names). Each file is an
  ordinary shared library under `_internal/` in the package and can be replaced with your own compatible build.
- These libraries are fixed when a release is built: your distribution's security updates do not reach the copies
  inside a Lifetime package. A fix arrives with a new Lifetime release.

**Qt libraries (LGPL-3.0).** You may replace the Qt libraries used by Lifetime with your own compatible build. With
the Python installation route, Qt is an ordinary installed package (`PyQt6-Qt6`) that can be swapped. The packaged
Windows programs include Qt's libraries; to use different ones, run Lifetime from source with your own Qt. In the
Linux packages Qt's libraries are ordinary shared libraries (`libQt6*.so.6`) under `_internal/` and can be replaced
there.

This notice is a good-faith summary for users, not legal advice.
