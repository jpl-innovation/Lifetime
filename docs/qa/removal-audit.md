# Removal audit

Audit of `latest-main` at `a6fa1e6` (224 tracked files, about 65,800 lines) for code, files and catalogue
entries that can go. Method: a reference scan of every tracked file name across the whole tree (installers,
PyInstaller specs, workflow, docs, tests), an AST scan for unused imports and for definitions with no reader
outside tests, an identical-body scan for copy-pasted functions, and an execution check of the translation
tables before and after each catalogue edit. Security checks, protocol validation, consent rules, audit code and
the ledger/store migrations were not treated as candidates.

Low-risk steps were applied, one local commit each, with the relevant test modules run after each step (one
module at a time). Nothing was pushed. Medium and high-risk items are listed with diffs for a decision.

## Result

| | |
| --- | --- |
| Commits | 6 code commits (plus this report) |
| Files removed | 12 tracked files |
| Lines removed | 725 net (761 deleted, 36 written back as shorter lines) |
| Reverted steps | none |
| Test modules run | 41 distinct modules across the steps, every test `OK` (see the note on exit crashes at the end) |

## Findings, ranked by payoff

Status: **applied** (done here), **proposed** (needs your approval), **keep** (checked, not worth changing).

| # | Tag | Finding | Evidence | If removed | Lines | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | delete | Upgrade-branch launch helpers: `scripts/` (7 files), `restart_desktop.ps1`, `restart_server_gui.ps1`, `launch-server.ps1` | Referenced only by each other. `scripts/publish-upgrade.ps1:5` insists `HEAD` is `793c515` and pushes `upgrade/approval-hardware`, a branch already merged. `restart_server_gui.ps1:5` and `scripts/start-server-elevated.ps1:2` hard-code `D:\Dev\lifetime-test-server-live`. In no installer list, spec, workflow or product doc | Nothing shipped changes. The product restart scripts (`restart_server_windows.ps1`, `restart_server_linux.sh`) stay | 387 | applied (`e125027`) |
| 2 | delete | 38 translation keys no code asks for, and 34 catalogue entries that a later or earlier entry of the same key overrides | No string literal anywhere outside `lifetime_i18n.py` equals the key or shares its first/last 14 characters. Mostly the retired "Company server" page (`lifetime_i18n.py:860-887`) and the old paged device list (`:946-969`). Overridden entries include `'Needs attention': 'Cần xử lý'` (`:1737`, loses to `:1395` through `setdefault`) | `EN`, `VI` and `SERVER_DASHBOARD_VI` are equal to the old tables minus the 38 keys; checked by executing the module before and after | 93 | applied (`230fb50`) |
| 3 | delete | `render_insights.py` | Unreferenced by name anywhere. Line 68 reads `window.history.cards[1].toggle`; `ChartCard` has no `toggle` (the page uses one `data_toggle`, `lifetime_charts.py:811`), so it stops at the first capture. It also copies the real `%APPDATA%\Lifetime\ledger.json` | Nothing; README's `render_*.py` line still holds | 104 | applied (`5123a6b`) |
| 4 | delete | Dead code in the UI modules | `lifetime_gui.Icon` (`:166`, no caller; the pages use `lifetime_widgets_dashboard.Icon`), `StatCard.set_hover` plus empty `enterEvent`/`leaveEvent` (`:408-418`, `set_hover` has no caller), `FleetTabBar` (`lifetime_server_widgets.py:67`, replaced by `SegmentedBar`), `admin_decide` (`lifetime_server_gui.py:3118`, the table has per-row buttons), constants `STATUS_GOOD`, `GREEN`, `PRESENCE` | Nothing; no reader in code, tests, docs or strings | 104 | applied (`b5abc60`) |
| 5 | delete | 39 unused imports in 23 files | AST scan; each name has no use in its file and no other file reaches it through the module (`from m import x`, `m.x`, or a patch string) | Nothing | 11 net | applied (`6325eee`) |
| 6 | delete | `test_compat.py` and its 17 imports | Shim adds `TestCase.enterContext` for Python 3.10. Minimum is 3.11: `README.md:55`, `RELEASE_NOTES.md:135`, `install_windows_auto.ps1:18`, CI matrix `windows.yml:14` | Tests on 3.10 would fail, which is already unsupported | 26 | applied (`0a2c4af`) |
| 7 | delete | `lifetime_gui.BarChart`, `history_summary`, `hours_minutes` and the `statistics` import | `lifetime_gui.py:365-459`. Only readers are `test_history_gui.py:54,118` ("the old reusable chart") and `test_history.py:70`. Activity uses `lifetime_charts.Plot` | Two test fragments go with it | ~110 | proposed (M1) |
| 8 | delete | Hidden "Pair a computer" bundle panel in the server window | `lifetime_server_gui.py:1563-1591`: built, then `enrollment.hide()` with "Superseded by Add a computer. Keep the legacy helper for scripted callers". Only `test_server_gui.py:763` clicks it | No visible change. `_create_bundle` stays (used by `test_ws.py`) | ~45 + 6 keys | proposed (M2) |
| 9 | delete | Five more `render_*.py` scripts | `render_dashboard.py`, `render_history.py`: unreferenced. `render_redesign.py`, `render_redesign_r2.py`, `render_health.py`: need a private `.redesign-data` copy that is not in this checkout (`render_health.py:12` points at `D:\Dev\Lifetime-redesign`) | DESIGN.md mentions two of them as provenance; that text would need a past-tense edit | 375 | proposed (M3) |
| 10 | shrink | Six answers to "is motion reduced?" | `lifetime_gui.py:43`, `lifetime_charts.py:600`, `lifetime_shell.py:402`, `lifetime_server_widgets.py:54`, `lifetime_theme.py:102`, `lifetime_connect.py:315`, `lifetime_reveal.py:248`. They disagree: only the server one and the switch honour the Windows "Animation effects" setting | Behaviour change on the desktop app (it would start honouring the Windows setting) | ~30 | proposed (H1) |
| 11 | delete | `RiseEffect` | `lifetime_gui.py:84`: its `offset` was only ever set by the removed `set_hover`, so it is now a plain opacity effect fixed at 1.0 on every stat card | Cards stop rendering through an offscreen pixmap; `test_dashboard.py:206,221` need a two-line edit | ~20 | proposed (M4) |
| 12 | stdlib | `.replace('Z', '+00:00')` before `fromisoformat` (23 places) and the 7-digit fraction rewrite | Python 3.11 parses `Z` and any fraction length itself. `lifetime_import_windows.py:21` says "Python 3.10 only parses 3 or 6" | None on 3.11+. Left alone because several sit in validation and accounting paths | ~25 edits | proposed (M5) |
| 13 | delete | `parse_netsh` | `lifetime_signals.py:93` "Legacy fixture parser only; live Windows queries use the WLAN API". Readers: `test_signals.py:12-44` | Two tests go with it | ~60 | proposed (M6) |
| 14 | yagni | `TelemetryTransport` | `lifetime_ws.py:47`: an interface with one implementation (`lifetime_ws_client.py:25`), kept for a UDP channel that does not exist | `docs/live-channel.md:53` needs one sentence removed | 8 | proposed (M7) |
| 15 | delete | About 80 more catalogue keys with no literal reader | Short keys such as `'Company server'`, `'Pair computer'`, `'Search'`, `'Retention'`. Not applied because short keys can arrive from Qt (`lifetime_i18n_qt.NativeTranslator` translates `&No`, `&Copy`, `Select All`) or from OS state text through `ui_text` (`'Full'`, `'Not charging'`) | Needs a per-key decision | ~80 | proposed (M8) |
| 16 | delete | Stale records | `docs/SERVER_S1_VERIFICATION.md` (128 lines, a dated verification log), `TECHNICIAN_VERIFICATION.md:33` still says "Python 3.10-3.13" and ships in the Windows package, `.gitignore:11` ignores `docs/IMPLEMENTATION_STATUS.md` which no longer exists | Docs only | ~130 | proposed (M9) |
| 17 | keep | Windows pipe and Unix socket admin channels | `lifetime_pipe.py`, `lifetime_unix_admin.py` share only the framing idea; the peer-identity code is platform API on each side and is security code | - | - | keep |
| 18 | keep | Three report modules | `lifetime_reports.py` builds and writes a report, `lifetime_server_reports.py` adds server rows to it, `lifetime_server_export.py` is the dialog. Layers, not copies; the identical-body scan found no shared function | - | - | keep |
| 19 | keep | Module lists repeated in `install_windows_common.ps1:39` and `install_linux.sh:153` | Two shells cannot share a list without a manifest file and a parser in each; `test_installed_modules.py` already fails when either list misses a module | - | - | keep |
| 20 | keep | Capability fallback for older servers | `lifetime_client.py:297,370,413` (`legacy_server_until`), `lifetime_server.py:448`. 1.0.x servers are still in the field | - | - | keep |
| 21 | keep | Identical `_transaction` in `lifetime_monitor.py:311` and `lifetime_server_store.py:335` | 9 lines, two separate databases; a shared base class costs more than it saves | - | - | keep |
| 22 | keep | Source-checkout Linux install path | `install_linux.sh:5`, documented at `README.md:120` and tested by `test_installed_modules.SourceInstallationTests`. Still supported, not a leftover | - | - | keep |
| 23 | keep | `CLIENT_ACCESS` in `lifetime_pipe.py:20` | Unused constant, but it documents the pipe access mask in a security module | - | 1 | keep |

Checked and clean: every file in `assets/` and `docs/screenshots/` is referenced; every `lifetime_*.py` is
imported or is an entry point; the identical-body scan over all Python found only the test-local `Paints`
counter (three copies of six lines) and the `_transaction` pair above.

## Diffs of the applied steps

Step 1 (`0a2c4af`), repeated in 17 test modules, plus `test_compat.py` deleted:

```diff
--- a/test_ws.py
+++ b/test_ws.py
@@ -5,7 +5,6 @@
 import threading
 import time
 import unittest
-import test_compat  # noqa: F401  (enterContext on Python 3.10)
```

Step 2 (`e125027`) and step 3 (`5123a6b`) are whole-file deletions:

```diff
 delete mode 100644 launch-server.ps1
 delete mode 100644 restart_desktop.ps1
 delete mode 100644 restart_server_gui.ps1
 delete mode 100644 scripts/README.md
 delete mode 100644 scripts/manual_server_runtime.py
 delete mode 100644 scripts/publish-upgrade.ps1
 delete mode 100644 scripts/start-server-elevated.ps1
 delete mode 100644 scripts/test_manual_launch.py
 delete mode 100644 scripts/test_restart_helpers.ps1
 delete mode 100644 scripts/try-upgrade.ps1
 delete mode 100644 render_insights.py
```

Step 4 (`b5abc60`), the part that touches a live class:

```diff
--- a/lifetime_gui.py
+++ b/lifetime_gui.py
@@ -307,7 +265,6 @@ class StatCard(QFrame):
         super().__init__()
         self.setObjectName('statCell')
-        self.hover = 0.0
         self.number = 0.0
@@ -405,19 +362,6 @@ class StatCard(QFrame):
                 p.drawLine(self.width()-1, 16, self.width()-1, self.height()-16)
 
-    def set_hover(self, value):
-        self.hover = value
-        self.effect.offset = 0 if REDUCE_MOTION else -2 * value
-        self.effect.update()
-        self.update()
-
-    def enterEvent(self, event):
-        super().enterEvent(event)
-
-    def leaveEvent(self, event):
-        super().leaveEvent(event)
-
-
 def hours_minutes(seconds):
--- a/lifetime_server_gui.py
+++ b/lifetime_server_gui.py
-    def admin_decide(self, operation):
-        indexes = self.admin_pending.selectionModel().selectedRows()
-        if len(indexes) == 1:
-            request = self.admin_pending.item(indexes[0].row(), 0).data(Qt.ItemDataRole.UserRole)
-            self._mutate(operation, dict(request_id=request))
```

Step 5 (`6325eee`), typical hunks:

```diff
--- a/lifetime_dashboard.py
+++ b/lifetime_dashboard.py
-from lifetime_i18n import tr, language, set_language, on_language_change, date_text, datetime_text, month, weekdays
+from lifetime_i18n import tr, language, datetime_text
--- a/lifetime_server_widgets.py
+++ b/lifetime_server_widgets.py
-from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QVariantAnimation, pyqtSignal
-from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
+from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, pyqtSignal
+from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
```

Step 6 (`230fb50`), typical hunks:

```diff
--- a/lifetime_i18n.py
+++ b/lifetime_i18n.py
 _ALERTS_VI = {
     'Current problems': 'Sự cố hiện tại',
-    'Needs attention': 'Cần xử lý',
     'Acknowledged': 'Đã xác nhận',
 _COMPANY_VI = {
-    'Company reporting is optional. Standalone Lifetime sends no company-server requests.': '...',
-    'Paste an enrollment bundle or open its file.': '...',
-    'Send dated awake-time history': '...',
```

## Cleanup plan, low risk first

| Step | Risk | What | Removed | Tests run after it | State |
| --- | --- | --- | --- | --- | --- |
| 1 | low | Python 3.10 test shim | 1 file, 26 lines | the 17 edited modules + `test_installed_modules` | done |
| 2 | low | Upgrade-branch helpers | 10 files, 387 lines | `test_installed_modules`, `test_linux_desktop` | done |
| 3 | low | `render_insights.py` | 1 file, 104 lines | `test_installed_modules` | done |
| 4 | low | Dead classes, methods, constants | 104 lines | `test_dashboard`, `test_redesign`, `test_gui_startup`, `test_history_gui`, `test_theme`, `test_monitor`, `test_server_gui`, `test_server_fleet`, `test_switches`, `test_widgets_dashboard`, `test_installed_modules` | done |
| 5 | low | Unused imports | 39 names, 11 lines net | 29 modules, see the hand-back report | done |
| 6 | low | Dead and overridden catalogue entries | 72 entries, 93 lines | 19 modules, see the hand-back report | done |
| M9 | low-medium | Stale docs and one `.gitignore` line | ~130 lines | none needed | proposed |
| M3 | medium | Five more render scripts | 5 files, 375 lines | `test_installed_modules`, `test_server_fleet` | proposed |
| M1 | medium | `BarChart`, `history_summary` | ~110 lines + 2 test fragments | `test_history`, `test_history_gui`, `test_dashboard` | proposed |
| M6 | medium | `parse_netsh` | ~60 lines with its tests | `test_signals`, `test_compatibility` | proposed |
| M4 | medium | `RiseEffect` | ~20 lines | `test_dashboard`, `test_redesign` + a look at the cards | proposed |
| M7 | medium | `TelemetryTransport` | 8 lines | `test_ws`, `test_live` | proposed |
| M8 | medium | Remaining unused catalogue keys | up to ~80 lines | `test_i18n` + every GUI module | proposed |
| M2 | medium | Hidden bundle panel | ~45 lines + 6 keys | `test_server_gui`, `test_ws`, `test_i18n` | proposed |
| M5 | medium | `fromisoformat` shims | ~25 one-line edits | `test_server_protocol`, `test_ledger_recovery`, `test_import`, `test_monitor`, `test_server_store` | proposed |
| H1 | high | One reduced-motion function | ~30 lines | every GUI module; visible behaviour change | proposed |

## Diffs for the proposed steps

M1, `lifetime_gui.py` (the whole block from `hours_minutes` to the end of `BarChart` goes):

```diff
-import statistics
@@
-def hours_minutes(seconds):
-    return duration(seconds, days=False)
-
-
-def history_summary(daily, sessions, today):
-    ...
-
-
-class BarChart(QWidget):
-    ...
-
-
 def segments(items, callback):
--- a/test_history_gui.py
+++ b/test_history_gui.py
-        legacy = gui.BarChart()
-        legacy.show()
-        assert legacy.entrance.duration() == 750
-        for _ in range(60):
-            QTest.qWait(50)
-            if legacy.progress == 1:
-                break
@@
-        reduced = gui.BarChart()
-        reduced.show()
-        assert reduced.progress == 1 and reduced.entrance is None
-        reduced.close()
--- a/test_history.py
+++ b/test_history.py
-    def test_summaries(self):
-        from lifetime_gui import history_summary
-        ...
```

M2, `lifetime_server_gui.py`:

```diff
-        enrollment, form = self._form_section('Pair a computer')
         self.server_address = QLineEdit('https://localhost:8443')
-        self.code_ttl, self.code_uses = QSpinBox(), QSpinBox()
-        ...
-        self.code_button = self.button('Create enrollment bundle', self.create_code)
-        self.bundle_output = QPlainTextEdit()
-        self.code_expiry = QLabel()
-        ...
-        # Superseded by Add a computer. Keep the legacy helper for scripted callers.
-        enrollment.setParent(self)
-        enrollment.hide()
@@
-    def create_code(self):
-        ...
-    def _code_finished(self, result, success):
-        ...
```

`self.server_address` is read elsewhere and has to stay as a plain attribute. The server's `code` admin
operation stays; only the hidden window controls go.

M3:

```diff
 delete mode 100644 render_dashboard.py
 delete mode 100644 render_history.py
 delete mode 100644 render_redesign.py
 delete mode 100644 render_redesign_r2.py
 delete mode 100644 render_health.py
--- a/DESIGN.md
+++ b/DESIGN.md
-`render_redesign.py` uses `QT_QPA_PLATFORM=windows`, installed Segoe UI and only
+The redesign captures used `QT_QPA_PLATFORM=windows`, installed Segoe UI and only
```

`render_server.py` (imported by `test_server_fleet.py:23`), `render_connect.py`, `render_setup.py`,
`render_technician.py` (README:280) and `render_logo_light.py` (rebuilds a shipped asset) stay.

M4, `lifetime_gui.py`:

```diff
-class RiseEffect(QGraphicsOpacityEffect):
-    """Move the rendered card without fighting its layout geometry."""
-    offset = 0.0
-    ...
@@ class StatCard(QFrame):
-        self.effect = RiseEffect(self)
-        self.setGraphicsEffect(self.effect)
--- a/test_dashboard.py
+++ b/test_dashboard.py
-        with patch.object(gui, 'motion_reduced', return_value=False), patch.object(gui, 'RiseEffect') as effect:
+        with patch.object(gui, 'motion_reduced', return_value=False):
             self.window.select_page(1)
-            effect.assert_not_called()
@@
-                self.assertEqual(card.effect.opacity(), 1)
+                self.assertIsNone(card.graphicsEffect())
```

M5, the same edit in 23 places, for example:

```diff
--- a/lifetime_health.py
+++ b/lifetime_health.py
-    parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
+    parsed = dt.datetime.fromisoformat(str(value))
--- a/lifetime_import_windows.py
+++ b/lifetime_import_windows.py
-    import re
-    text = value.strip().replace('Z', '+00:00')
-    # Event logs write 7 fractional digits; Python 3.10 only parses 3 or 6, so normalise to microseconds.
-    text = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], text, count=1)
-    value = dt.datetime.fromisoformat(text)
+    value = dt.datetime.fromisoformat(value.strip())
```

One caution: 3.11's parser also accepts forms the old one refused (`20261106T100000`, a space separator), so
in `lifetime_protocol.py:48`, `lifetime_ledger.py:41` and `lifetime_server_store.py:76` the surrounding
checks should be read before touching them. Recommendation: do the display-only call sites, leave the three
validators.

M6, `lifetime_signals.py` and `test_signals.py`:

```diff
-def parse_netsh(text):
-    """Legacy fixture parser only; live Windows queries use the WLAN API."""
-    ...
--- a/test_signals.py
+++ b/test_signals.py
-    def test_netsh_connected(self):
-        ...
-    def test_netsh_disconnected_and_partial(self):
-        ...
```

M7:

```diff
--- a/lifetime_ws.py
+++ b/lifetime_ws.py
-class TelemetryTransport:
-    """What the live sender needs from a transport. ..."""
-    def send_sample(self, sample):
-        raise NotImplementedError
--- a/lifetime_ws_client.py
+++ b/lifetime_ws_client.py
-class WebSocketTransport(ws.TelemetryTransport):
+class WebSocketTransport:
```

M9:

```diff
--- a/.gitignore
+++ b/.gitignore
-/docs/IMPLEMENTATION_STATUS.md
+.job-commits/
+.lifetime-local-commits/
+*.bundle
+s1-gui-*/
--- a/TECHNICIAN_VERIFICATION.md
+++ b/TECHNICIAN_VERIFICATION.md
-  Windows/Linux Python 3.10–3.13 tests and a separate server artifact build.
+  Windows/Linux Python 3.11 and 3.12 tests and a separate server artifact build.
```

H1, one function in `lifetime_theme.py` (already imported by every UI module) and the six local copies
replaced by an import:

```diff
--- a/lifetime_shell.py
+++ b/lifetime_shell.py
-def reduced_motion():
-    return os.environ.get('LIFETIME_REDUCE_MOTION') == '1'
+from lifetime_theme import motion_off as reduced_motion
```

This is the only item that changes what a user sees: the desktop window would begin to follow the Windows
"Animation effects" setting, as the server window and the switches already do. Tests patch several of these
names by module (`patch('lifetime_server_devices.reduced_motion')`, `patch.object(gui, 'REDUCE_MOTION')`), so
the names must survive as aliases.

## Untracked files in the working tree (not touched)

| Path | What it is | `.gitignore` |
| --- | --- | --- |
| `.job-commits/` | a bare Git repository with earlier job commits | not covered; add `.job-commits/` |
| `.lifetime-local-commits/` | a full copy of the source tree from an earlier job | not covered; add `.lifetime-local-commits/` |
| `lifetime-jobs.bundle` | a Git bundle | not covered; add `*.bundle` |
| `s1-gui-*/` (4 folders) | leftover server data from a GUI run: `server-key.pem`, `admin-token`, SQLite files | each file is ignored by pattern, the folders are not named; they hold a private key and an admin token for a throwaway server and can be deleted |
| `.qa/`, `shots/`, `job1-*.log` | QA captures and logs | already ignored |

## Not sure about

- `render_dashboard.py` and `render_history.py` were not run (the first opens the real data folder), so "still
  works" is unverified for both. They are listed under M3 rather than deleted.
- The 38 removed keys were chosen by absence of any matching literal. A key that an older agent or server sends
  as text over the wire would no longer be translated to Vietnamese; it would show in English, not fail,
  because every such path falls back to the text itself (`lifetime_reports.py:52`, `lifetime_technician.py:72`,
  `lifetime_i18n.ui_text`). None of the 38 looks like wire text (they are sentences from retired pages).
- Four GUI test modules report every test `OK` and then the interpreter exits with a segmentation fault (exit
  code 139) during Qt teardown: `test_health_gui` and `test_technician_gui` every run, `test_server_export` and
  `test_technician_release` on some runs. The same happens on an untouched checkout of `a6fa1e6` (4 of 4, 3 of 4
  and 1 of 4 runs for the three modules tried there), so this cleanup did not cause it, but a CI job that reads
  the exit code would see those modules fail.
