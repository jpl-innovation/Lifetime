"""Native dashboard navigation and interruptible page motion."""
from pathlib import Path
import getpass
import os
import shlex
import sys

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QTimer, QVariantAnimation, QEasingCurve, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QButtonGroup, QHBoxLayout, QMenu, QSystemTrayIcon, QVBoxLayout, QSizePolicy
from lifetime_i18n import tr
from lifetime_i18n_qt import QWidget, QPushButton, QLabel
from lifetime_theme import CONTROL_RADIUS, install


from lifetime_theme import tokens
GREEN = '#4CC38A'   # use tokens()['good'] where the background can be light


def shut_down(app, window):
    """Delete the window and its tray while the application still exists, so Python's exit never tears Qt objects down in a random order."""
    tray = getattr(window, 'tray', None)
    if tray is not None:
        tray.hide()
        tray.deleteLater()
    window.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def tray_available():
    return QSystemTrayIcon.isSystemTrayAvailable()


def _pipe(key):
    return f'{key}-{getpass.getuser()}'


def tell_running_instance(key):
    """True if another copy is already in the tray; that copy then opens its window."""
    socket = QLocalSocket()
    socket.connectToServer(_pipe(key))
    found = socket.waitForConnected(300)
    if found:
        socket.disconnectFromServer()
    return found


class AppTray(QSystemTrayIcon):
    """Notification-area icon. Closing the window keeps the app here; Quit really exits."""

    def __init__(self, window, name, status, translate=tr):
        super().__init__(QApplication.windowIcon(), window)
        self.win, self.name, self.status, self.t = window, name, status, translate
        self.quitting = self.told = False
        install()
        self.menu = QMenu(window)
        self.state = self.menu.addAction('')
        self.state.setEnabled(False)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction('', self.open)
        self.quit_action = self.menu.addAction('', self.quit)
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_state)
        self.timer.start(15000)
        self.update_state()
        self.show()

    def update_state(self):
        state = self.status()
        self.setToolTip(f'{self.name}\n{state}')
        self.state.setText(state)
        self.open_action.setText(self.t('Open'))
        self.quit_action.setText(self.t('Quit'))

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open()

    def open(self):
        if self.win.isMinimized():
            self.win.showNormal()
        else:
            self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def quit(self):
        self.quitting = True
        self.hide()
        self.win.close()
        QApplication.quit()

    def keep(self, event):
        """Called from closeEvent: hide to the tray instead of exiting."""
        if self.quitting:
            return False
        event.ignore()
        self.win.hide()
        if not self.told:
            self.told = True
            self.showMessage(self.name, self.t('Still running here. Right-click the icon to quit.'), self.icon(), 4000)
        return True


def install_tray(window, name, status, key=None, translate=tr):
    """Returns None where the desktop has no tray (the app then closes normally)."""
    if not tray_available():
        return None
    QApplication.setQuitOnLastWindowClosed(False)
    tray = AppTray(window, name, status, translate)
    if key:
        QLocalServer.removeServer(_pipe(key))   # clears a stale Linux socket after a crash
        server = QLocalServer(window)
        server.listen(_pipe(key))
        def repeat():
            server.nextPendingConnection().deleteLater()
            tray.open()
        server.newConnection.connect(repeat)
    return tray


def configure_tray_login(enabled):
    """Put the tray icon back at sign-in. Installed copies only, never a source checkout."""
    here = Path(__file__).resolve().parent
    if not tray_available() or (here / '.git').exists():
        return
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        args = [str(executable), '--tray']
    else:
        if sys.platform == 'win32' and executable.with_name('pythonw.exe').exists():
            executable = executable.with_name('pythonw.exe')
        args = [str(executable), str(here / 'lifetime_gui.py'), '--tray']
    if sys.platform == 'win32':
        import subprocess
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE) as run:
            if enabled:
                winreg.SetValueEx(run, 'Lifetime', 0, winreg.REG_SZ, subprocess.list2cmdline(args))
            else:
                try:
                    winreg.DeleteValue(run, 'Lifetime')
                except FileNotFoundError:
                    pass
    else:
        entry = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'autostart/lifetime-tray.desktop'
        if enabled:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text('[Desktop Entry]\nType=Application\nName=Lifetime\nExec=' + shlex.join(args) + '\n', encoding='utf-8')
        else:
            entry.unlink(missing_ok=True)


class PulseDot(QWidget):
    """Green status dot; while active a soft ring ripples outward every 2.4 s.
    Inactive: plain grey dot, no motion. The text beside it always names the state."""

    def __init__(self, size=26, idle=None, reduce_motion=False):
        super().__init__()
        self.setFixedSize(size, size)
        self.idle, self.reduce_motion = idle, reduce_motion
        self.active, self.wave = False, 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(2400)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._step)

    def _step(self, value):
        self.wave = value
        self.update()

    def set_active(self, active):
        if active == self.active:
            return
        self.active = active
        self._sync_motion()
        self.update()

    def _sync_motion(self):
        if self.active and not self.reduce_motion and self.isVisible():
            self.anim.start()
        else:
            self.anim.stop()
            self.wave = 0.0

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_motion()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.anim.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        core = 5.0
        if self.active and self.anim.state() == QVariantAnimation.State.Running:
            t = min(self.wave / 0.7, 1.0)          # ring travels in the first 70%, then rests
            if t < 1.0:
                eased = 1 - (1 - t) ** 3
                ring = QColor(tokens()['good'])
                ring.setAlphaF(0.55 * (1 - eased))
                p.setPen(QPen(ring, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = core + eased * (self.width() / 2 - core - 1)
                p.drawEllipse(c, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        if self.active:
            halo = QColor(tokens()['good'])
            halo.setAlpha(55)
            p.setBrush(halo)
            p.drawEllipse(c, core + 2, core + 2)
        p.setBrush(QColor(tokens()['good'] if self.active else (self.idle or tokens()['muted'])))
        p.drawEllipse(c, core, core)


def symbol(p, kind):
    """All symbols share a 20 px, round-ended, 1.5 px monoline grid."""
    if kind == 'overview':
        for x, y in ((3, 3), (11, 3), (3, 11), (11, 11)):
            p.drawRoundedRect(QRectF(x, y, 6, 6), 1.5, 1.5)
    elif kind == 'activity':
        points = [(2, 12), (6, 12), (9, 4), (12, 16), (15, 9), (18, 9)]
        for a, b in zip(points, points[1:]):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif kind == 'system':
        p.drawRoundedRect(QRectF(2, 3, 16, 11), 2, 2)
        p.drawLine(10, 14, 10, 18)
        p.drawLine(6, 18, 14, 18)
    elif kind == 'health':
        points = [(3, 3), (10, 1), (17, 3), (17, 11), (14, 16), (10, 19), (6, 16), (3, 11), (3, 3)]
        for a, b in zip(points, points[1:]):
            p.drawLine(QPointF(*a), QPointF(*b))
        for a, b in zip([(5, 10), (8, 10), (10, 6), (12, 13)], [(8, 10), (10, 6), (12, 13), (15, 10)]):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif kind == 'connect':
        p.drawEllipse(QRectF(1, 5, 11, 10))
        p.drawEllipse(QRectF(8, 5, 11, 10))
    elif kind == 'reports':
        p.drawRoundedRect(QRectF(4, 2, 12, 16), 2, 2)
        for y in (7, 10, 13):
            p.drawLine(7, y, 13, y)
    elif kind == 'offline':
        p.drawRoundedRect(QRectF(2, 3, 16, 11), 2, 2)
        p.drawLine(10, 14, 10, 18)
        p.drawLine(6, 18, 14, 18)
        p.drawLine(5, 12, 15, 5)
    elif kind == 'clock':
        p.drawEllipse(QRectF(2, 2, 16, 16))
        p.drawLine(10, 6, 10, 10)
        p.drawLine(10, 10, 13, 12)
    elif kind == 'pending':
        p.drawLine(5, 2, 15, 2)
        p.drawLine(5, 18, 15, 18)
        for a, b in (((6, 2), (6, 6)), ((6, 6), (14, 14)), ((14, 14), (14, 18)),
                     ((14, 2), (14, 6)), ((14, 6), (6, 14)), ((6, 14), (6, 18))):
            p.drawLine(QPointF(*a), QPointF(*b))
    else:
        p.drawEllipse(QRectF(6, 6, 8, 8))
        for a, b in (((10, 1), (10, 4)), ((10, 16), (10, 19)),
                     ((1, 10), (4, 10)), ((16, 10), (19, 10)),
                     ((4, 4), (6, 6)), ((14, 14), (16, 16)),
                     ((4, 16), (6, 14)), ((14, 6), (16, 4))):
            p.drawLine(QPointF(*a), QPointF(*b))


CREDIT_HEIGHT = 40   # the JPL Innovation wordmark stays legible at this height in a 200 px column


def crisp(pixmap, width, height, widget):
    """Scale an image to fit width x height logical pixels, rendered at the screen's pixel density."""
    ratio = widget.devicePixelRatioF() or 1.0
    scaled = pixmap.scaled(int(width * ratio), int(height * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


class NavRow(QPushButton):
    def __init__(self, title, kind, sidebar):
        super().__init__(tr(title), sidebar)
        self.sidebar, self.kind = sidebar, kind
        self.setAccessibleName(tr(title))
        self.setToolTip(tr(title))
        self.setFixedHeight(44)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.keyboard_focus = False

    def focusInEvent(self, event):
        # Show the focus ring only for keyboard navigation; a mouse click or window activation
        # must not leave a second outline that looks like another selection.
        reasons = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)
        self.keyboard_focus = self.keyboard_focus or event.reason() in reasons
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.keyboard_focus = False
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.keyboard_focus = False
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() and not self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tokens()['card']))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), CONTROL_RADIUS, CONTROL_RADIUS)
        ring = self.hasFocus() and self.keyboard_focus
        color = QColor(tokens()['text'] if self.isChecked() or ring else tokens()['muted'])
        p.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.save()
        p.translate((self.width()-20)/2 if self.sidebar.collapsed else 12, 12)
        symbol(p, self.kind)
        p.restore()
        if not self.sidebar.collapsed:
            p.setFont(self.font())
            p.drawText(QRectF(44, 0, self.width()-48, 44), Qt.AlignmentFlag.AlignVCenter, self.text())
        if ring:
            p.setPen(QPen(QColor(tokens()['accent']), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), CONTROL_RADIUS, CONTROL_RADIUS)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            rows = [row for row in self.sidebar.rows if row.isVisible() and row.isEnabled()]
            index = rows.index(self)
            target = rows[(index + (1 if event.key() == Qt.Key.Key_Down else -1)) % len(rows)]
            target.keyboard_focus = True
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, forward):
        rows = [row for row in self.sidebar.rows if row.isVisible() and row.isEnabled()]
        index = rows.index(self)
        target = index + (1 if forward else -1)
        if 0 <= target < len(rows):
            rows[target].setFocus(Qt.FocusReason.TabFocusReason if forward else Qt.FocusReason.BacktabFocusReason)
            return True
        return super().focusNextPrevChild(forward)


class DisclosureButton(QPushButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(tokens()['muted']), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = self.width()-20, self.height()/2
        delta = -3 if self.isChecked() else 3
        p.drawLine(QPointF(x-4, y-delta/2), QPointF(x, y+delta/2))
        p.drawLine(QPointF(x, y+delta/2), QPointF(x+4, y-delta/2))


def themed_logo(path):
    """The JPL Innovation logo has white lettering for dark surfaces; on the light theme turn that near-white ink dark."""
    from PyQt6.QtGui import QImage
    from lifetime_theme import active_theme
    image = QImage(str(path))
    if active_theme() == 'light' and not image.isNull():
        image = image.convertToFormat(QImage.Format.Format_ARGB32)
        ink = QColor(37, 45, 45)
        for y in range(image.height()):
            for x in range(image.width()):
                c = image.pixelColor(x, y)
                if c.alpha() and min(c.red(), c.green(), c.blue()) >= 150 and max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 45:
                    ink.setAlpha(c.alpha())
                    image.setPixelColor(x, y, ink)
    return QPixmap.fromImage(image)


class Sidebar(QWidget):
    def __init__(self, select, settings, motion_reduced, language_control, status, dot, theme_control=None):
        super().__init__()
        self.setObjectName('sidebar')
        self.collapsed = False
        self.motion_reduced = motion_reduced
        self.indicator_y = 0.0
        self.indicator_animation = None
        self.setFixedWidth(232)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 24, 16, 16)
        box.setSpacing(8)
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        self.logo = QLabel()
        self.logo.setObjectName('lifetimeLogo')
        self.logo.setAccessibleName(tr('Lifetime'))
        self.logo.setFixedSize(28, 28)
        self.logo.setPixmap(crisp(QPixmap(str(root/'assets/lifetime.png')), 28, 28, self))
        self.brand = QLabel(tr('Lifetime'))
        self.brand.setObjectName('brand')
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(self.logo)
        brand.addWidget(self.brand, 1)
        box.addLayout(brand)
        state = QHBoxLayout()
        state.setSpacing(8)
        state.addWidget(dot)
        state.addWidget(status, 1)
        self.status = status
        box.addLayout(state)
        box.addSpacing(24)
        self.group = QButtonGroup(self)
        self.rows = []
        # Keep the original public page IDs for renderers/integrations.
        # Visual order and Ctrl shortcuts are Overview, Activity, System, Reports.
        for key, title, kind in ((0, 'Overview', 'overview'), (2, 'Activity', 'activity'),
                                 (1, 'System', 'system'), (4, 'Health', 'health'), (5, 'Connect', 'connect'), (3, 'Reports', 'reports')):
            row = NavRow(title, kind, self)
            row.setCheckable(True)
            self.group.addButton(row, key)
            self.rows.append(row)
            box.addWidget(row)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(select)
        box.addStretch(1)
        self.settings_button = NavRow('Settings', 'settings', self)
        self.settings_button.clicked.connect(settings)
        self.rows.append(self.settings_button)
        box.addWidget(self.settings_button)
        box.addSpacing(16)
        self.credit_logo = QLabel()
        self.credit_logo.setObjectName('jplLogo')
        self.credit_logo.setAccessibleName(tr('Made by JPL Innovation'))
        self.credit_pixmap = themed_logo(root/'assets/jpl_innovation.png')
        self.credit_logo.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.credit_logo.setFixedHeight(CREDIT_HEIGHT)
        self.credit_logo.setPixmap(crisp(self.credit_pixmap, 200, CREDIT_HEIGHT, self))
        box.addWidget(self.credit_logo)
        self.credit = QLabel(tr('Made by JPL Innovation'))
        self.credit.setObjectName('caption')
        self.credit.setWordWrap(True)
        box.addWidget(self.credit)
        self.language_control = language_control
        self.theme_control = theme_control
        box.addWidget(language_control)
        if theme_control is not None:
            box.addWidget(theme_control)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(tokens()['sidebar']))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = self.group.checkedButton()
        if row and not row.isHidden():
            r = QRectF(row.x(), self.indicator_y, row.width(), row.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tokens()['track']))
            p.drawRoundedRect(r, CONTROL_RADIUS, CONTROL_RADIUS)
            p.setBrush(QColor(tokens()['accent']))
            p.drawRoundedRect(QRectF(r.left()+2, r.center().y()-8, 2, 16), 1, 1)

    def move_indicator(self, animated=True):
        row = self.group.checkedButton()
        if row is None:
            return
        if self.indicator_animation:
            self.indicator_animation.stop()
            self.indicator_animation.deleteLater()
            self.indicator_animation = None
        target = float(row.y())
        if not animated or self.motion_reduced():
            self.indicator_y = target
            self.update()
            return
        animation = QVariantAnimation(self)
        animation.setDuration(260)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(self.indicator_y)
        animation.setEndValue(target)
        animation.valueChanged.connect(self._indicator_frame)
        self.indicator_animation = animation
        animation.start()

    def _indicator_frame(self, value):
        self.indicator_y = value
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self.move_indicator(False))

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, 'group'):
            self.layout().activate()
            self.move_indicator(False)
        return result

    def set_collapsed(self, collapsed):
        if self.collapsed == collapsed:
            return
        self.collapsed = collapsed
        self.setFixedWidth(64 if collapsed else 232)
        self.layout().setContentsMargins(10 if collapsed else 16, 24, 10 if collapsed else 16, 16)
        self.brand.setVisible(not collapsed)
        self.status.setVisible(not collapsed)
        self.credit.setVisible(not collapsed)
        self.credit_logo.setPixmap(crisp(self.credit_pixmap, 44 if collapsed else 200, CREDIT_HEIGHT, self))
        self.credit_logo.setToolTip(tr('Made by JPL Innovation'))
        for control in (self.language_control, self.theme_control):
            if control is not None:
                control.layout().setDirection(QHBoxLayout.Direction.TopToBottom if collapsed else QHBoxLayout.Direction.LeftToRight)
        self.move_indicator(False)
        for row in self.rows:
            row.update()


class PageStack(QWidget):
    """Only the selected page sets the scroll height, even after visiting a long page."""
    def __init__(self):
        super().__init__()
        self._pages = []
        self._index = -1
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)

    def addWidget(self, page):
        self._pages.append(page)
        self.layout().addWidget(page)
        page.hide()
        if self._index < 0:
            self.setCurrentIndex(0)

    def count(self):
        return len(self._pages)

    def currentIndex(self):
        return self._index

    def currentWidget(self):
        return self._pages[self._index] if self._index >= 0 else None

    def setCurrentIndex(self, index):
        self._index = index
        for i, page in enumerate(self._pages):
            page.setVisible(i == index)
        self.layout().invalidate()
        self.updateGeometry()

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page else QSize(0, 0)

    def minimumSizeHint(self):
        page = self.currentWidget()
        return QSize(0, page.minimumSizeHint().height()) if page else QSize(0, 0)
