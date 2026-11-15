# Third-party notices

Lifetime is free software, Copyright (C) 2026 JPL Innovation, licensed under the **GNU General Public License,
version 3 only** (GPL-3.0-only). The full text is in [LICENSE](LICENSE). You may copy, change and redistribute it,
including for money, as long as the people who receive it get the same freedoms and the source code.

**Source code.** The complete source for every release is in the public repository
<https://github.com/jpl-innovation/Lifetime>: use the release tag that matches the version you received. Every
downloadable package is built from that tagged source by the published build scripts (`build_windows.ps1`,
`build_server_windows.ps1`, `install_linux.sh`).

## Components used by Lifetime

Licenses below are taken from each package's own published metadata (checked 15 November 2026). Each component keeps
its own copyright and license; versions in a given release may be newer.

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
| PyInstaller (build tool) | Packaging the Windows programs | GPL-2.0-or-later with an exception that leaves bundled programs under their own license | <https://pyinstaller.org/> |

No fonts are bundled; Lifetime uses the operating system's own fonts.

**Qt libraries (LGPL-3.0).** You may replace the Qt libraries used by Lifetime with your own compatible build. With
the Python installation route, Qt is an ordinary installed package (`PyQt6-Qt6`) that can be swapped. The packaged
Windows programs include Qt's libraries; to use different ones, run Lifetime from source with your own Qt.

This notice is a good-faith summary for users, not legal advice.
