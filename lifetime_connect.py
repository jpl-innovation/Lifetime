"""One-code connection page. Certificate search never sends application data."""
import datetime as dt
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import time
from PyQt6.QtCore import Qt, QTimer, QPointF, QPropertyAnimation, QRectF, QParallelAnimationGroup, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (QWidget, QFrame, QLineEdit, QPushButton,
                            QCheckBox, QVBoxLayout, QHBoxLayout, QMessageBox,
                            QGraphicsOpacityEffect)
from lifetime_tasks import Tasks
from lifetime_shell import PageStack, PulseDot
from lifetime_i18n import tr, TABLES, datetime_text
from lifetime_i18n_qt import QLabel, QPushButton
from lifetime_company import read_status
from lifetime_connection import CHECK_WORDS
from lifetime_settings import read_settings
from lifetime_theme import SWITCH_W, tokens
from lifetime_glass import solid

NO_SERVER = 'No server found with this code. Check that the server is on and that this computer is on the same network.'
PERMISSION = 'Windows permission is needed to finish.'
TRIMMED = 'The server runs an older version of Lifetime, so some details are not shared until it is updated.'
UPDATES = 'Get Lifetime updates approved by the server'
UPDATES_DETAIL = 'Downloaded and checked, then installed by your IT team. Off unless you turn it on.'
KEEPS_COUNTING = 'Awake time keeps counting on this computer and is sent when the server is back.'
STALE_STATUS = 150      # the agent rewrites its status at least every 60 s; older means it is not running
UNREACHABLE = ('offline', 'unavailable', 'error', 'retrying', 'backoff', 'unreachable')


def _reached_text(seconds):
    """When the server last answered, short: the time today, else the date and time."""
    try:
        moment = dt.datetime.fromtimestamp(float(seconds)).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        return ''
    return moment.strftime('%H:%M') if moment.date() == dt.date.today() else str(datetime_text(moment))


def reason_text(kind, t):
    """Why the server cannot be reached, in plain words, from the stored category (never a raw error)."""
    if isinstance(kind, str) and kind.startswith('http-') and kind[5:].isdigit():
        return t('The server refused the report (error {code}).', code=kind[5:])
    return t(ConnectPage.REASONS[kind]) if kind in ConnectPage.REASONS else ''


def server_state(status, t, now=None):
    """The Server chip as (kind, short words, full words, explanation), only from the agent's status file.

    A current agent checks the server itself every 15-25 s (lifetime_client.ServerWatch); an older agent is judged
    by its last sync. No network request is made here."""
    status = status or {}
    if not status.get('enabled') or status.get('phase') not in ('pending', 'approved'):
        return 'neutral', t('Not connected'), t('Not connected'), ''
    checking = ('info', t('Server: Checking…'), t('Server: Checking…'),
                t("Waiting for Lifetime's background service on this computer."))
    now = time.time() if now is None else now
    checked = status.get('checked_at')
    if isinstance(checked, (int, float)) and not isinstance(checked, bool):
        if not -STALE_STATUS <= now - checked <= STALE_STATUS:
            return checking
        reachable, problem, seen = status.get('server_reachable') is True, status.get('server_problem'), status.get('server_seen_at')
    else:
        state = status.get('status')
        if state not in ('connected', 'pending', 'approved') + UNREACHABLE:
            return checking
        reachable, problem = state not in UNREACHABLE, status.get('error')
        try:
            seen = dt.datetime.fromisoformat(str(status.get('last_sync')).replace('Z', '+00:00')).timestamp()
        except ValueError:
            seen = None
    if reachable:
        return 'good', t('Server: Online'), t('Server: Online'), ''
    when = _reached_text(seen) if seen else ''
    full = t('Server: Offline · last reached {time}', time=when) if when else t('Server: Offline')
    return 'warning', t('Server: Offline'), full, '\n'.join(part for part in (reason_text(problem, t), t(KEEPS_COUNTING)) if part)


def locate_server(value):
    from lifetime_connection import PREFIX, parse, normalize, bootstrap, find_server, named_origin
    from lifetime_discovery import discover
    if value.strip().startswith(PREFIX):
        origin, code = parse(value)
        try:
            return bootstrap(origin, code)
        except (OSError, ValueError):
            pass        # the line's address may not resolve on this network: look for the server by its code
    else:
        code = normalize(value)
    try:
        candidates = discover()
    except OSError:
        candidates = []
    for server in candidates:
        try:
            return bootstrap(server['address'], code)
        except (OSError, ValueError):
            pass
        # The announced name may not resolve here; the address the answer came from does.
        if server.get('source'):
            try:
                return named_origin(bootstrap(f"https://{server['source']}:{server['port']}", code),
                                    server['source'], server['port'], code)
            except (OSError, ValueError):
                pass
    return find_server(code)


# ---- Diagnose connection: one check per line, each saying its cause and what to do ----

CLOCK_LIMIT = 300       # seconds of clock difference shown as a problem
FIX_SELF_CHECK = 'On the server, open Administration > Self check.'
FIX_CLOCK = "Check the date and time on this computer (set them automatically). If they are right, open Administration > Self check on the server."


def _check(state, text, fix=None, **values):
    """One line: a state (good, warning, danger, info, neutral), catalogue keys for its words and what to do. Values
    lose control characters (a discovery answer cannot start a forged line of its own)."""
    from lifetime_connection import plain
    return dict(state=state, text=text, fix=fix, values={key: plain(value, 300) for key, value in values.items()})


def _matching(servers, code, timeout):
    """The first discovered server whose certificate matches ``code``, as locate_server tries them: only certificates
    are read (no application data); the address the answer came from is tried as well as the announced name."""
    import hashlib
    import hmac
    import lifetime_connection as connection
    import lifetime_tls as tls
    invitation, secret, expected = connection.parts(code)
    for server in servers:
        for host in dict.fromkeys(item for item in (server.get('source'), server['host']) if item):
            try:
                der = tls.peer_certificate(host, server['port'], timeout)
            except (OSError, ValueError):
                continue
            if hmac.compare_digest(connection.commitment(hashlib.sha256(der).hexdigest(), invitation, secret), expected):
                return server
    return None


def windows_profiles(run=None):
    """[(network, category)] of the active Windows networks, or None when they cannot be read. Read only. PowerShell
    writes UTF-8 and undecodable bytes are replaced, so a network named "WLAN (Büro)" never breaks the diagnosis."""
    from lifetime_connection import UTF8_OUTPUT, run_read
    result = run_read(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', UTF8_OUTPUT +
                       "Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + $_.NetworkCategory }"], run, 15)
    if result is None or result[0]:
        return None
    return [tuple(line.strip().rsplit('|', 1)) for line in result[1].splitlines() if '|' in line]


def local_server(port):
    """Whether something listens on this computer's own port: then this computer is (most likely) the server."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            return True
    except OSError:
        return False


def server_time(host, port, pem, timeout):
    """The server's clock, from the Date header of an empty request (HEAD /, which the server refuses) over a
    connection that trusts only ``pem``. Called only after ``pem`` matched the typed code."""
    import email.utils
    import http.client
    import ssl
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.check_hostname = False      # the code proved this certificate, whatever names it carries
    context.load_verify_locations(cadata=pem)
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        connection.request('HEAD', '/', headers={'Connection': 'close'})
        date = connection.getresponse().getheader('Date')
    finally:
        connection.close()
    return email.utils.parsedate_to_datetime(date).timestamp()


def diagnose(value='', saved=None, *, profiles=windows_profiles, here=local_server, now=time.time, timeout=3):
    """The Diagnose connection lines for what was typed (a code or connection line) or the saved server address.

    On the network: a name lookup, TCP connects, one discovery query, TLS handshakes that send no application data,
    and the server's clock only after its certificate matched the typed code. Never raises; no result carries the code.
    """
    import hashlib
    import hmac
    import ipaddress
    import ssl
    import lifetime_connection as connection
    import lifetime_discovery
    import lifetime_tls as tls
    from lifetime_i18n import date_text
    checks, origin, code, source = [], None, None, None
    value = (value or '').strip()
    try:
        if value.startswith(connection.PREFIX):
            origin, code = connection.parse(value)
        elif value:
            code = connection.normalize(value)
    except ValueError:
        checks.append(_check('danger', 'The code or connection line is not complete.',
                             'Type the whole code from the server: LTS and 20 letters and numbers. 0 or O, either works.'))
    if origin:
        checks.append(_check('info', 'Checking {address}, from the connection line.', address=origin))
    elif code:
        checks.append(_check('info', 'Checking: searching the network for the server (only a code was typed).'))
    elif saved:
        try:
            origin = tls._address(saved)[0]
            checks.append(_check('info', 'Checking {address}, the saved connection.', address=origin))
        except ValueError:
            pass
    if not checks:
        checks.append(_check('neutral', 'Nothing to check yet.', 'Type the code from the server, then press Diagnose connection.'))

    try:
        found = lifetime_discovery.discover(2)
    except (OSError, ValueError):
        found = []
    if found:
        def name(server):       # an untrusted hint: one printable line, at most a DNS label long
            return connection.plain(server['instance'], 63)
        checks.append(_check('good', 'A Lifetime Server answered on this network: {servers}.', servers='; '.join(
            f"{name(server)} ({server['address']}, {server.get('source') or server['host']})" for server in found[:4])))
        if not origin and code:
            chosen = _matching(found[:4], code, min(timeout, 2))
            if chosen is None and len(found) > 1:
                checks.append(_check('warning', 'Several Lifetime Servers answered and none has a certificate that matches '
                                     'the code. Checking the first one, {name}.',
                                     'Make sure the code comes from the server this computer should use, or type its '
                                     'connection line (Add device shows it).', name=name(found[0])))
            elif chosen is not None and len(found) > 1:
                checks.append(_check('info', 'Several Lifetime Servers answered. {name} matches the code.', name=name(chosen)))
            chosen = chosen or found[0]
            origin, source = chosen['address'], chosen.get('source')
    else:
        checks.append(_check('warning', 'No Lifetime Server answered the network search (port 5353).',
                             'On the server, allow 5353/udp in the firewall and set its network to Private. Guest and some '
                             'office Wi-Fi block these answers: then type the connection line from the server (Add device) '
                             'instead of only the code.'))

    try:
        networks = sorted({str(ipaddress.IPv4Network(ip + '/24', strict=False)) for ip in connection.local_ipv4()})
    except (OSError, ValueError):
        networks = []
    if networks:
        checks.append(_check('good', 'The network search covers {networks} (port 8443).', networks=', '.join(networks)))
    else:
        checks.append(_check('danger', 'This computer has no local IPv4 network to search.',
                             'Connect this computer to the office network (Wi-Fi or cable). Over a VPN, type the connection '
                             'line from the server instead of only the code.'))
    try:
        named = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        named = []
    if any(ip.startswith('127.') for ip in named):
        checks.append(_check('good', "This computer's name points to {address}, as on Ubuntu. Handled: the search uses "
                             'the addresses of its network connections instead.',
                             address=next(ip for ip in named if ip.startswith('127.'))))

    if not origin:
        checks.append(_check('neutral', 'Name, port and certificate not checked: there is no server address to check.',
                             'Type the connection line from the server (Add device shows it), or make the network search '
                             'work (see above).'))
        return checks + _profile_checks(profiles, here, 8443)

    _, host, port = tls._address(origin)
    target = host
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        checks.append(_check('good', '{host} is an IP address: no name lookup needed.', host=host))
    else:
        try:
            resolved = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
            checks.append(_check('good', '{host} resolves to {addresses} on this network.', host=host, addresses=', '.join(resolved)))
        except OSError:
            if source:
                target = source
                checks.append(_check('warning', '{host} does not resolve on this network. Lifetime uses {source}, the '
                                     'address the server answered from.',
                                     "If connecting fails, ask IT to add {host} to this network's DNS, or add the server's "
                                     'IP under Administration > Other server names and IPs.', host=host, source=source))
            else:
                target = None
                checks.append(_check('danger', '{host} does not resolve on this network.',
                                     "Type the connection line with the server's IP address instead (Administration on the "
                                     "server shows it), or ask IT to add {host} to this network's DNS.", host=host))

    reachable = False
    if target:
        try:
            with socket.create_connection((target, port), timeout=timeout):
                reachable = True
            checks.append(_check('good', 'Port {port} on {host} is reachable.', host=target, port=port))
        except ConnectionRefusedError:
            checks.append(_check('danger', 'Port {port} on {host} refused the connection.',
                                 'The computer answered, but Lifetime Server is not running there or uses another port. '
                                 'Start Lifetime Server, or check its port under Administration.', host=target, port=port))
        except TimeoutError:
            checks.append(_check('danger', 'No answer from {host} on port {port}.',
                                 'The server may be off, or its firewall blocks port {port}. ' + FIX_SELF_CHECK,
                                 host=target, port=port))
        except OSError:
            checks.append(_check('danger', 'No network path to {host}.',
                                 'Check that this computer is on the same network as the server (Wi-Fi or cable, or the VPN).',
                                 host=target))
    else:
        checks.append(_check('neutral', 'Port {port} not checked: the server name did not resolve.', port=port))

    details, der, matched = {}, None, False
    if reachable:
        try:
            der = tls.peer_certificate(target, port, timeout)       # reads the certificate only; sends nothing
        except (OSError, ValueError):
            checks.append(_check('danger', 'The secure connection to {host} failed: it may not be Lifetime Server.',
                                 'Check the address and port. Lifetime Server uses port 8443 unless it was installed with another.',
                                 host=target))
    else:
        checks.append(_check('neutral', 'Certificate not checked: the server could not be reached.'))
    if der:
        pem, problem = ssl.DER_cert_to_PEM_cert(der), None
        try:
            details = connection.certificate_details(target, port, pem, timeout)
        except ssl.SSLCertVerificationError as error:
            problem = {9: 'not-yet', 10: 'expired'}.get(error.verify_code, 'invalid')
        except (OSError, ValueError):
            problem = 'invalid'
        if details:
            names = details.get('subjectAltName', ())
            dns = [name for kind, name in names if kind == 'DNS']
            ips = [name.strip() for kind, name in names if kind == 'IP Address']
            covered = (any(connection._same_ip(name, ip) for name in ips) if ip is not None
                       else host.lower() in {name.lower() for name in dns})
            if covered:
                checks.append(_check('good', 'The server certificate names {host}.', host=host))
            else:
                checks.append(_check('danger', 'The server certificate does not name {host}. It names: {names}.',
                                     'Use one of those names in the connection line, or on the server add {host} under '
                                     'Administration > Other server names and IPs.', host=host, names=', '.join(dns + ips)))
        if code:
            invitation, secret, expected = connection.parts(code)
            matched = hmac.compare_digest(connection.commitment(hashlib.sha256(der).hexdigest(), invitation, secret), expected)
            if matched:
                checks.append(_check('good', 'The server certificate matches the code.'))
            else:
                checks.append(_check('danger', "This server's certificate does not match the code.",
                                     'The code belongs to another server, or the server renewed its certificate after the code '
                                     'was made. Create a new code on the server (Add device).'))
        if details:
            start, end = (ssl.cert_time_to_seconds(details[key]) for key in ('notBefore', 'notAfter'))
            dates = dict(start=date_text(dt.date.fromtimestamp(start)), end=date_text(dt.date.fromtimestamp(end)))
            if end - now() < 30 * 86400:
                checks.append(_check('warning', 'The server certificate is valid from {start} to {end}, less than 30 days left.',
                                     'Lifetime Server renews it 30 days before it ends. ' + FIX_SELF_CHECK, **dates))
            else:
                checks.append(_check('good', 'The server certificate is valid from {start} to {end}.', **dates))
        elif problem == 'expired':
            checks.append(_check('danger', "The server certificate is out of date, or this computer's clock is wrong.", FIX_CLOCK))
        elif problem == 'not-yet':
            checks.append(_check('danger', "The server certificate is not valid yet: this computer's clock may be behind.", FIX_CLOCK))
        else:
            checks.append(_check('danger', 'The server certificate could not be checked.',
                                 'Update Lifetime on this computer and on the server.'))
        if matched and details:
            try:
                difference = server_time(target, port, pem, timeout) - now()
                if abs(difference) <= CLOCK_LIMIT:
                    checks.append(_check('good', "This computer's clock is within {seconds} s of the server's.",
                                         seconds=round(abs(difference))))
                else:
                    checks.append(_check('warning', "This computer's clock differs from the server's by {minutes} minutes.",
                                         'Set the date and time automatically on both computers.',
                                         minutes=round(abs(difference) / 60)))
            except Exception:       # any failure only means the clock is unknown
                checks.append(_check('neutral', "The server's clock could not be read."))
    if not (matched and details):
        checks.append(_check('neutral', "Server clock not compared: Lifetime sends nothing to a server before the typed "
                             'code proves it is the right one.'))
    return checks + _profile_checks(profiles, here, port)


def _profile_checks(profiles, here, port):
    """Windows only: a Public network blocks other computers from reaching a server on this computer."""
    if sys.platform != 'win32':
        return []
    found = profiles()
    if found is None:
        return [_check('neutral', 'Could not read the Windows network profile.')]
    public = ', '.join(name for name, kind in found if kind.lower() == 'public')
    listed = ', '.join(f'{name} ({kind})' for name, kind in found)
    if public and here(port):
        return [_check('danger', 'This computer runs Lifetime Server and its network {networks} is Public: Windows lets no '
                       'other computer reach it.',
                       'Set the network to Private: Settings > Network & internet > {networks} > Network profile type > Private.',
                       networks=public)]
    if public:
        return [_check('info', 'Windows network {networks} is Public. That does not stop this computer from connecting out; '
                       'it would only block computers if this computer were the server.', networks=public)]
    return [_check('good', 'Windows network profile: {networks}. None is Public.', networks=listed or '-')]


def diagnosis_report(checks, t):
    """Copy report: plain text, with any code, token or secret masked."""
    import platform
    from lifetime_connection import check_lines, scrub
    from lifetime_protocol import CLIENT_VERSION
    lines = [t('Lifetime connection diagnosis'),
             f"Lifetime {CLIENT_VERSION} · {platform.platform()} · {dt.datetime.now().astimezone().isoformat(timespec='seconds')}", '']
    return scrub('\n'.join(lines + check_lines(checks, t)))


# Copy diagnostics: only these named fields are copied (identity, codes and credentials never are).
SAFE_SETTINGS = ('language', 'theme', 'style', 'completed', 'autostart', 'history', 'battery', 'network', 'wifi_name',
                 'graphics', 'resources', 'health', 'remote_power')
SAFE_STATUS = ('enabled', 'phase', 'status', 'server', 'server_name', 'reported', 'trimmed', 'updates', 'policy', 'pending',
               'dropped', 'last_sync', 'server_reachable', 'server_problem', 'server_seen_at', 'checked_at', 'error', 'error_at')


def setup_logs():
    """The reporting service's own setup log (install_agent_windows.ps1): in its data folder, or TEMP before that exists.
    The data folder is for administrators only, so it is usually not readable here."""
    if sys.platform != 'win32':
        return []
    from lifetime_agent import machine_directory
    return [machine_directory() / 'install.log', Path(tempfile.gettempdir()) / 'LifetimeAgent-install.log']


def diagnostics_text(t, directory=None, machine_root=None, checks=None, logs=None):
    """Copy diagnostics: version, system, settings that are not secret, the reporting service's state and last problem,
    the last Diagnose connection and the end of the setup log. Named fields only, and the whole text is scrubbed."""
    import platform
    from lifetime_connection import check_lines, log_tail, report
    from lifetime_protocol import CLIENT_VERSION
    settings, status = read_settings(directory), read_status(machine_root)
    edition = t('desktop, installed package') if getattr(sys, 'frozen', False) else t('desktop, from source')
    sections = [(t('Lifetime diagnostics'), [f'Lifetime {CLIENT_VERSION} ({edition})',
                                             f'{platform.platform()} · Python {platform.python_version()}',
                                             dt.datetime.now().astimezone().isoformat(timespec='seconds')]),
                (t('Settings'), [f'{key}: {settings[key]}' for key in SAFE_SETTINGS if key in settings])]
    service = [server_state(status, t)[2]] + [f'{key}: {status[key]}' for key in SAFE_STATUS if status.get(key) is not None]
    if status.get('error'):
        service.append(t('Last problem: {reason}', reason=reason_text(status['error'], t) or status['error']))
    sections.append((t('Reporting service'), service))
    sections.append((t('Connection diagnosis'), check_lines(checks, t) if checks else [t('Not run. Press Diagnose connection first.')]))
    for name, lines in log_tail(setup_logs() if logs is None else logs):
        sections.append((t('Log: {name}, last {count} lines', name=name, count=len(lines)), lines))
    return report(sections)


FINISH_FAILED = 'Could not finish connecting. Check that the server is on and try again.'
# The agent installer's exit codes (install_agent_windows.ps1, Stop-Install; the agent's own 2-4 mean the same).
SETUP_EXIT = {
    2: "Windows could not set up Lifetime's reporting service with these details. Check the code and try again. If the folder "
       "Program Files\\LifetimeAgent holds files that are not from Lifetime, move them away first.",
    3: "Could not reach the server, or it is not the server this code belongs to (its security certificate does not "
       "match). Check that the server is on and on the same network, and that the code is from that server.",
    4: 'The server refused this code: it was already used, has expired or was cancelled. Create a new code on the server.',
    5: "Setting up Lifetime's reporting service needs administrator approval. Sign in with an administrator account and try again.",
    6: "Lifetime's reporting service is already installed on this computer and was not replaced. Ask your IT team to replace it, then try again.",
}


class SetupFailed(RuntimeError):
    """The elevated agent setup ended with this exit code (``detail_code`` crosses the worker bridge)."""
    def __init__(self, code):
        super().__init__('Agent operation failed')
        self.detail_code = 'exit-%d' % code if 0 < code < 2 ** 32 else 'exit-unknown'


def setup_failure_text(error, t):
    """The words for a failed Finish from the worker's error ("SetupFailed:exit-3"): the installer's reason when its
    exit code is known, else the general message with the code."""
    code = error.partition(':exit-')[2]
    if error.startswith('SetupFailed:') and code.isdigit() and int(code) in SETUP_EXIT:
        return t(SETUP_EXIT[int(code)])
    if error.startswith('SetupFailed:') and code.isdigit():
        return t('Could not finish connecting (error {code}). Check that the server is on and try again.', code=code)
    return t(FINISH_FAILED)


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


AGENT_SETUP = ('LifetimeAgent.exe', 'install_agent_windows.ps1', 'uninstall_agent_windows.ps1')
AGENT_SETUP_TARGET = "(Join-Path $env:ProgramFiles 'LifetimeAgentSetup')"      # evaluated by the elevated process


def agent_setup_directory():
    """Where the machine agent's setup files are kept on Windows. Only administrators can write there, so what
    the Connect page asks approval for cannot be replaced by the signed-in user or a program running as them."""
    return Path(os.environ.get('ProgramFiles') or 'C:/Program Files') / 'LifetimeAgentSetup'


def _linked(path):
    """A symbolic link or junction (a Windows reparse point): it leads somewhere other than where it sits."""
    try:
        return os.path.islink(path) or bool(getattr(os.lstat(path), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def _real_file(path):
    return os.path.isfile(path) and not _linked(path)


def stage_script(source, names, target=AGENT_SETUP_TARGET):
    """PowerShell for the elevated process: put the agent setup files where only administrators can write, and
    check that each copy is byte for byte what this app read (its SHA-256) before anything runs. Used only when
    the installer has not already put them there (it was run without administrator approval).
    install_windows_common.ps1 does the same from the package at install time."""
    import hashlib
    files = ', '.join('@(' + _ps_quote(name) + ', ' + _ps_quote(Path(source) / name) + ', ' +
                      _ps_quote(hashlib.sha256((Path(source) / name).read_bytes()).hexdigest()) + ')' for name in names)
    return ("$ErrorActionPreference='Stop'; $target = " + target + "; "
            "function Assert-NotLinked($path) { $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue; "
            "if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Links are refused.' } }; "
            "Assert-NotLinked $target; New-Item -ItemType Directory -Force -Path $target | Out-Null; " + STAGE_ACL +
            "foreach ($file in @(" + files + ")) { $copy = Join-Path $target $file[0]; Assert-NotLinked $copy; "
            "Copy-Item -LiteralPath $file[1] -Destination $copy -Force; "
            # .NET, not Get-FileHash: that is a script function Windows PowerShell cannot find when this app was started
            # from PowerShell 7 (which leaves its own module path in the environment).
            "$hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($copy))).Replace('-', ''); "
            "if ($hash -ne $file[2]) { Remove-Item -LiteralPath $copy -Force; throw 'The agent setup files changed while waiting for approval.' } }; ")


# Administrators and SYSTEM: full control. Users: read and run. Nothing inherited from the parent folder.
STAGE_ACL = ("$security = [Security.AccessControl.DirectorySecurity]::new(); "
             "$security.SetSecurityDescriptorSddlForm('D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)'); "
             "Set-Acl -LiteralPath $target -AclObject $security; ")


def run_agent(action, policy, bundle=None, directory=None, display_name=None):
    """One waited elevation, including the packaged installer when present."""
    from lifetime_agent import reporting_directory
    from lifetime_permissions import protect_directory
    root = Path(directory) if directory is not None else reporting_directory()
    with tempfile.TemporaryDirectory(prefix='Lifetime-connect-') as temporary:
        private = Path(temporary)
        if sys.platform == 'win32':
            import csv
            identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                                      text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            sid = next(csv.reader([identity.stdout.strip()]))[1]
            subprocess.run(['icacls', str(private), '/inheritance:r', '/grant:r',
                            '*' + sid + ':(OI)(CI)(F)', '*S-1-5-32-544:(OI)(CI)(F)', '*S-1-5-18:(OI)(CI)(F)'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            protect_directory(private)
        args = ['--directory', str(root)]
        if action == 'finish':
            path = private / 'connection.txt'
            path.write_text(bundle, encoding='utf-8')
            args += ['--enroll-bundle-file', str(path), '--background', '--display-name', display_name or socket.gethostname()]
        elif action == 'disconnect':
            args += ['--disable-reporting']
        else:
            args += ['--set-policy']
        if action != 'disconnect':
            args += ['--policy-' + key for key, enabled in policy.items() if enabled]
        if getattr(sys, 'frozen', False) and sys.platform != 'win32':      # Linux bundle or AppImage
            from lifetime_settings import bundled_command
            command = bundled_command('LifetimeAgent') + args
        elif getattr(sys, 'frozen', False):
            beside = Path(sys.executable).parent
            if not (beside / 'LifetimeAgent.exe').is_file():
                raise FileNotFoundError('LifetimeAgent.exe is missing beside Lifetime.exe')
            # Never ask for administrator approval to run a file from the per-user folder: anything running as
            # this user can replace it. The copies under Program Files are run instead; if the installer could
            # not put them there, the elevated step copies and verifies them first.
            setup = agent_setup_directory()
            staged = not _linked(setup) and _real_file(setup / 'LifetimeAgent.exe')       # the installer's copies win
            names = [name for name in AGENT_SETUP if _real_file((setup if staged else beside) / name)]
            stage = '' if staged else stage_script(beside, names)
            command = [str(setup / 'LifetimeAgent.exe')] + args
            installer = setup / 'install_agent_windows.ps1'
            if action == 'finish' and installer.name in names:
                # -ExecutionPolicy Bypass: Windows clients refuse every script file by default ("running scripts is
                # disabled on this system"), and the approving administrator's own policy is the one that applies.
                # -Reinstall: connecting again after a Disconnect (or over an older version's agent) replaces the
                # installed agent; without it the installer stops at "already installed" and Finish fails.
                command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(installer), '-Mode', 'Packaged', '-Reinstall',
                           '-EnableReporting', '-BundleFile', str(path), '-Start', '-DisplayName', display_name or socket.gethostname()]
                for key, flag in dict(history='TrackHistory', resources='ResourceReadings', health='HealthReadings', control='PowerControl',
                                      updates='AllowUpdates').items():
                    if policy.get(key):
                        command.append('-' + flag)
            if stage:
                import base64
                run = ("$p = Start-Process -FilePath " + _ps_quote(command[0]) + " -ArgumentList " +
                       _ps_quote(subprocess.list2cmdline(command[1:])) +
                       " -PassThru -WindowStyle Hidden; $null = $p.Handle; $p.WaitForExit(); exit $p.ExitCode")
                command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand',
                           base64.b64encode((stage + run).encode('utf-16-le')).decode('ascii')]
        else:
            command = [sys.executable, str(Path(__file__).with_name('lifetime_agent.py'))] + args
        if sys.platform == 'win32':
            # Start-Process receives one correctly quoted native argument string.
            arguments = subprocess.list2cmdline(command[1:])
            script = ("$ErrorActionPreference='Stop'; try { $p = Start-Process -FilePath " +
                      _ps_quote(command[0]) + ' -ArgumentList ' + _ps_quote(arguments) +
                      # Not -Wait: it also waits for the detached agent, which never exits.
                      ' -Verb RunAs -PassThru -WindowStyle Hidden; $null = $p.Handle; $p.WaitForExit(); exit $p.ExitCode } catch { if ($_.Exception.NativeErrorCode -eq 1223 -or $_.Exception.InnerException.NativeErrorCode -eq 1223) { exit 1223 }; exit 1 }')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                    creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            if result.returncode == 1223:
                raise PermissionError(PERMISSION)
            if result.returncode:
                raise SetupFailed(result.returncode)
        else:
            subprocess.run(command, check=True)
            if action == 'finish':
                try:
                    subprocess.run(['systemctl', '--user', 'enable', '--now', 'lifetime-company-agent.service'],
                                   check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    return read_status(root)


class ConnectButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class SharingSwitch(QCheckBox):
    """Native checkbox keyboard/accessibility behavior. lifetime_theme draws every QCheckBox as the app's
    sliding switch, so this paints nothing of its own: a second switch drawn underneath showed through."""
    def hitButton(self, position):
        return self.rect().contains(position)


class StageMark(QWidget):
    """Drawn, not typed: a green tick when done, a yellow dot for the current step, nothing for later steps."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 22)
        self.state = 0      # 0 later, 1 current, 2 done

    def set_state(self, state):
        self.state = state
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state == 1:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tokens()['accent']))
            p.drawEllipse(QPointF(11, 11), 4, 4)
        elif self.state == 2:
            pen = QPen(QColor(tokens()['good']), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(5, 11.5), QPointF(9.5, 16), QPointF(17, 6.5)])


class PacketFlight(QWidget):
    """Parcels leave this computer, cross to the server, and a green reply comes back."""
    PARCELS = 4

    def __init__(self, reduce_motion=False):
        super().__init__()
        self.setFixedHeight(120)
        self.reduce_motion = reduce_motion
        self.phase = 0.35
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(2400)
        self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._step)

    def _step(self, value):
        self.phase = value
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.reduce_motion:
            self.anim.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.anim.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle, left, right = self.height() / 2, 74.0, self.width() - 74.0
        line = QPen(QColor(tokens()['line']), 2, Qt.PenStyle.DashLine)
        p.setPen(line)
        p.drawLine(QPointF(left, middle), QPointF(right, middle))
        outline = QPen(QColor(tokens()['text']), 2)
        outline.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(outline)
        p.setBrush(QColor(tokens()['bg']))
        # This computer
        p.drawRoundedRect(QRectF(24, middle - 24, 46, 32), 4, 4)
        p.drawLine(QPointF(16, middle + 16), QPointF(78, middle + 16))
        # The server: three stacked units with a live light on each
        x = self.width() - 70.0
        for row in range(3):
            y = middle - 28 + row * 19
            p.setPen(outline)
            p.setBrush(QColor(tokens()['bg']))
            p.drawRoundedRect(QRectF(x, y, 46, 16), 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            lit = int(self.phase * 6) % 3 == row
            p.setBrush(QColor(tokens()['good'] if lit else tokens()['line']))
            p.drawEllipse(QPointF(x + 37, y + 8), 2.5, 2.5)
        span = right - left
        p.setPen(Qt.PenStyle.NoPen)
        for index in range(self.PARCELS):
            t = (self.phase + index / self.PARCELS) % 1.0
            eased = t * t * (3 - 2 * t)
            p.setOpacity(min(1.0, min(t, 1 - t) * 8))
            p.setBrush(QColor(tokens()['accent']))
            cx = left + span * eased
            hop = 10 * (4 * eased * (1 - eased))
            p.drawRoundedRect(QRectF(cx - 7, middle - 7 - hop, 14, 14), 3, 3)
            p.setBrush(QColor(tokens()['bg']))
            p.drawRect(QRectF(cx - 1, middle - 7 - hop, 2, 14))     # the parcel's tape
        reply = (self.phase * 2) % 1.0
        p.setOpacity(min(1.0, min(reply, 1 - reply) * 8))
        p.setBrush(QColor(tokens()['good']))
        p.drawEllipse(QPointF(right - span * reply, middle + 13), 4, 4)
        p.setOpacity(1.0)


class ConnectPage(QWidget):
    serverState = pyqtSignal(str, str, str, str)     # kind, short words, full words, explanation: only on a change
    def restyle_glass(self):
        self.setStyleSheet('QWidget#connectPage {background: ' + solid(tokens()['bg']) + ';}')

    def __init__(self, parent=None, directory=None, language=None, machine_root=None):
        super().__init__(parent)
        from lifetime_theme import install
        install()
        self.setObjectName('connectPage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.restyle_glass()
        self.directory, self.machine_root, self.language = directory, machine_root, language
        self._busy = False
        self._operation = None
        self._cancel_requested = False
        self.bundle = None
        self._origin = None
        self._line_code = None
        self.server_name = ''
        self.server_address = ''
        self.status = {}
        self.editing = False
        self.reduce_motion = os.environ.get('LIFETIME_REDUCE_MOTION') == '1' or bool(os.environ.get('REDUCE_MOTION'))
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self.completed)
        self.tasks.failed.connect(self.failed)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 24, 0, 24)
        outer.addStretch()
        self.column = QWidget()
        self.column.setMaximumWidth(560)
        self.column.setMinimumWidth(0)
        box = QVBoxLayout(self.column)
        box.setContentsMargins(0, 0, 0, 0)
        self.steps = PageStack()
        box.addWidget(self.steps)
        self.diagnosis = QFrame()       # below the steps: shown after Diagnose connection, on the code and connected steps
        self.diagnosis.setObjectName('insetPanel')
        self.diagnosis_layout = QVBoxLayout(self.diagnosis)
        self.diagnosis_layout.setContentsMargins(24, 24, 24, 24)
        self.diagnosis_layout.setSpacing(12)
        self.note(self.diagnosis_layout, 'Connection diagnosis', 'pageTitle')
        self.diagnosis_rows = QVBoxLayout()
        self.diagnosis_rows.setSpacing(10)
        self.diagnosis_layout.addLayout(self.diagnosis_rows)
        self.button(self.diagnosis_layout, 'Copy report', self.copy_report)
        self.diagnosis.hide()
        self.diagnosis_checks = []
        self._diagnosing = False
        self.diagnose_tasks = Tasks(self)      # its own worker: a diagnosis never blocks or answers the status poll
        self.diagnose_tasks.completed.connect(lambda number, result: self._diagnosed(result))
        self.diagnose_tasks.failed.connect(lambda number, error: self._diagnosed(
            [_check('danger', 'The diagnosis could not finish.', 'Try again. If it keeps failing, tell your IT team.')]))
        self.copy_tasks = Tasks(self)       # Copy diagnostics reads files: never on the interface thread
        self.copy_tasks.completed.connect(lambda number, text: self._copied(text))
        self.copy_tasks.failed.connect(lambda number, error: self._copied(None))
        box.addWidget(self.diagnosis)
        box.addStretch()
        outer.addWidget(self.column, 1)
        outer.addStretch()
        self.forms = []
        for _ in range(5):
            panel = QFrame()
            panel.setObjectName('insetPanel')
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(16)
            self.forms.append(layout)
            self.steps.addWidget(panel)
        f = self.forms[0]
        self.note(f, 'Connect to a server', 'pageTitle')
        self.note(f, "For workplace computers: connect to your IT team's Lifetime Server. Not needed for personal use — Lifetime works fully on its own.")
        f.addWidget(PacketFlight(self.reduce_motion))
        # The tutorial is captioned onto the drawing: each end says what to do at that computer.
        ends = QHBoxLayout()
        # The drawing puts this computer on the left, but the order of work is server first, so the words say so.
        for heading, detail, side in (('Then, on this computer', 'Type the code below. 0 or O, either works.', Qt.AlignmentFlag.AlignLeft),
                                      ('First, on the server', 'Press Add device to get a code.', Qt.AlignmentFlag.AlignRight)):
            end = QVBoxLayout()
            end.setSpacing(0)
            self.note(end, heading).setAlignment(side)
            self.note(end, detail, 'caption').setAlignment(side)
            ends.addLayout(end, 1)
        f.addLayout(ends)
        self.code_error = self.note(f, '')      # above the field, where it is seen without scrolling
        self.code_error.hide()
        self.code = QLineEdit()
        self.code.setMaxLength(2300)
        self.code.setPlaceholderText('LTS-XXXX-XXXX-XXXX-XXXX-XXXX')
        self.code.setAccessibleName(self.t('Connection code'))
        self.code.setStyleSheet('QLineEdit {font-family: Consolas, monospace; font-size: 26px; color: ' + tokens()['accent'] + '; background: ' + tokens()['card'] + '; border: 1px solid ' + tokens()['line'] + '; padding: 14px 8px;}')
        f.addWidget(self.code)
        self.connect_button = self.button(f, 'Connect', self.connect_server, True)
        self.connect_button.setEnabled(False)
        self.code.textChanged.connect(self.format_input)
        self.code.returnPressed.connect(self.connect_server)
        self.note(f, 'Next you choose what to share. You can change it or disconnect any time.', 'caption')
        self.diagnose_buttons = [self.button(f, 'Diagnose connection', self.diagnose)]
        self.copy_buttons = [self.button(f, 'Copy diagnostics', self.copy_diagnostics)]
        self.pulse(self.forms[1], 'Looking for your server...', True)
        self.forms[1].addWidget(PacketFlight(self.reduce_motion))
        f = self.forms[2]
        self.review_title = self.pulse(f, '', True)
        self.review_address = self.note(f, '', 'caption')
        self.note(f, 'Choose what this server can see.')
        self.awake = self.choice(f, 'Awake time', 'Required to connect.', True)
        self.awake.setEnabled(False)
        self.history = self.choice(f, 'Awake history by day', 'Daily totals of awake time.', True)
        self.resources = self.choice(f, 'Computer specs and live performance', 'Processor, memory, disk, battery and network.', True)
        settings = read_settings(directory)
        self.health_allowed = bool(settings.get('completed') and settings.get('health'))
        self.health = self.choice(f, 'Health and crashes', 'Health summaries and crash counts.', self.health_allowed)
        self.health.setEnabled(self.health_allowed)
        self.control = self.choice(f, 'Let the server sleep or restart this computer',
                                   'Restart and shut down give a one minute warning first. Sleep is immediate.', False)
        self.updates = self.choice(f, UPDATES, UPDATES_DETAIL, False)
        self.review_error = self.note(f, '')
        self.review_error.hide()
        self.finish_button = self.button(f, 'Finish', self.finish, True)
        self.cancel_button = self.button(f, 'Cancel', self.cancel)
        self.cancel_button.setObjectName('quietAction')
        f = self.forms[3]
        self.loading_title = self.note(f, 'Connecting securely', 'pageTitle')
        self.waiting_text = self.note(f, '')
        f.addWidget(PacketFlight(self.reduce_motion))
        self.stage_rows = []
        for key in ('Server found', 'Connecting securely', 'Waiting for approval', "Sending this computer's details"):
            row = QHBoxLayout()
            mark = StageMark()
            text = QLabel(self.t(key))
            row.addWidget(mark)
            row.addWidget(text, 1)
            f.addLayout(row)
            self.stage_rows.append((mark, text))
        self.waiting_cancel = self.button(f, 'Cancel connection', self.cancel_waiting)
        self.waiting_cancel.setObjectName('quietAction')
        self.waiting_cancel.hide()
        self.stage = 1
        self._report_started = None
        f = self.forms[4]
        self.connected_title = self.pulse(f, 'Connected', True)
        from lifetime_widgets_dashboard import StatusChip
        self.server_chip = StatusChip()
        self.server_chip.hide()     # shown only while this computer is connected to a server
        f.addWidget(self.server_chip, 0, Qt.AlignmentFlag.AlignLeft)
        self._server_state = None
        # The connection as a card: the server's name, then its address, the last report and what is shared.
        from lifetime_widgets_dashboard import InfoCard
        self.summary = InfoCard('network', '')
        self.summary_values = {}
        for key in ('Server address', 'Last report', 'Shared with the server'):
            value = QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText)
            self.summary_values[key] = (self.summary.add_row(self.t(key), value), value)
        self.summary_trimmed = QLabel()
        self.summary_trimmed.setObjectName('caption')
        self.summary_trimmed.setWordWrap(True)
        self.summary_trimmed.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.extra.addWidget(self.summary_trimmed)
        f.addWidget(self.summary)
        self.unreachable = self.note(f, "Can't reach the server right now. Lifetime will keep trying.")
        self.unreachable.hide()
        self.button(f, 'Change what is shared', self.change_policy)
        self.button(f, 'Disconnect', self.disconnect)
        self.diagnose_buttons.append(self.button(f, 'Diagnose connection', self.diagnose))
        self.copy_buttons.append(self.button(f, 'Copy diagnostics', self.copy_diagnostics))
        for button in self.diagnose_buttons + self.copy_buttons:
            button.setObjectName('quietAction')
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.show_step(1)
        self.poll()

    def update_health(self, settings):
        self.health_allowed = bool(settings.get('completed') and settings.get('health'))
        self.health.setEnabled(self.health_allowed)
        if not self.health_allowed:
            self.health.setChecked(False)

    def t(self, key, **fmt):
        if self.language:
            return TABLES[self.language][key].format(**fmt)
        return tr(key, **fmt)

    def note(self, layout, key, kind=''):
        label = QLabel(self.t(key) if key else '')
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setMinimumWidth(0)
        if kind:
            label.setObjectName(kind)
        layout.addWidget(label)
        return label

    def button(self, layout, key, action, primary=False):
        button = ConnectButton(self.t(key))
        button.setMinimumHeight(44)
        if primary:
            button.setObjectName('primaryAction')
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def pulse(self, layout, key, active):
        row = QHBoxLayout()
        dot = PulseDot(reduce_motion=self.reduce_motion)
        dot.set_active(active)
        row.addWidget(dot)
        label = QLabel(self.t(key) if key else '')
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName('pageTitle')
        label.setWordWrap(True)
        label.dot = dot
        row.addWidget(label, 1)
        layout.addLayout(row)
        return label

    def choice(self, layout, title, detail, checked):
        row = QHBoxLayout()
        copy = QVBoxLayout()
        self.note(copy, title)
        self.note(copy, detail, 'caption')
        row.addLayout(copy, 1)
        switch = SharingSwitch()
        switch.setAccessibleName(self.t(title))
        switch.setChecked(checked)
        switch.setFixedSize(SWITCH_W, 44)     # the theme's switch is SWITCH_W wide; narrower clipped its end
        row.addWidget(switch)
        layout.addLayout(row)
        return switch

    def show_step(self, step):
        if getattr(self, "step", None) == step:
            return
        if hasattr(self, 'animation'):
            self.animation.stop()
            self.animation.deleteLater()
            del self.animation
        if hasattr(self, '_outgoing') and self._outgoing is not None:
            self._outgoing.deleteLater()
            self._outgoing = None
        previous = self.steps.currentWidget()
        snapshot = previous.grab() if previous and self.isVisible() and not self.reduce_motion else None
        old_geometry = previous.geometry() if previous else None
        if previous:
            previous.setGraphicsEffect(None)
        self.step = step
        self.steps.setCurrentIndex(step - 1)
        self.diagnosis.setVisible(step in (1, 5) and bool(self.diagnosis_checks))
        incoming = self.steps.currentWidget()
        if snapshot is not None:
            self.animation = QParallelAnimationGroup(self)
            effect = QGraphicsOpacityEffect(incoming)
            incoming.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b'opacity', self.animation)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setDuration(180)
            self.animation.addAnimation(fade)
            overlay = QLabel(self.steps)
            overlay.setPixmap(snapshot)
            overlay.setGeometry(old_geometry)
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            overlay.show()
            overlay.raise_()
            self._outgoing = overlay
            outgoing = QGraphicsOpacityEffect(overlay)
            overlay.setGraphicsEffect(outgoing)
            fade_out = QPropertyAnimation(outgoing, b'opacity', self.animation)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setDuration(180)
            self.animation.addAnimation(fade_out)
            def finished():
                incoming.setGraphicsEffect(None)
                overlay.deleteLater()
                if self._outgoing is overlay:
                    self._outgoing = None
            self.animation.finished.connect(finished)
            self.animation.start()
        if step == 1:
            self.code.setFocus()
        elif step == 3:
            self.finish_button.setFocus()

    def set_stage(self, stage):
        """1 connecting, 2 approval, 3 sending. Every row always names its own state in words."""
        self.stage = stage
        self.waiting_cancel.setVisible(stage in (1, 2))
        self.waiting_cancel.setEnabled(not self._cancel_requested)
        titles = {1: 'Connecting securely', 2: 'Waiting for approval', 3: "Sending this computer's details"}
        self.loading_title.setText(self.t(titles[stage]))
        for index, (mark, text) in enumerate(self.stage_rows):
            done, current = index < stage, index == stage
            mark.set_state(2 if done else 1 if current else 0)
            text.setStyleSheet('color: ' + (tokens()['text'] if current else tokens()['muted']) + ';')

    def format_input(self):
        from lifetime_connection import PREFIX, parse, normalize, format_code
        value = self.code.text()
        if value.strip().startswith(PREFIX):
            try:
                self._origin, self._line_code = parse(value)
                self.code.blockSignals(True)
                self.code.setText(self._line_code)
                self.code.blockSignals(False)
                valid = True
            except ValueError:
                valid = False
        else:
            cursor = self.code.cursorPosition()
            formatted = format_code(value)
            if formatted != value:
                self.code.blockSignals(True)
                self.code.setText(formatted)
                self.code.setCursorPosition(min(len(formatted), cursor + len(formatted) - len(value)))
                self.code.blockSignals(False)
            if formatted != self._line_code:
                self._origin = None
                self._line_code = None
            try:
                normalize(formatted)
                valid = True
            except ValueError:
                valid = False
        self.connect_button.setEnabled(valid and not self._busy)

    def submit(self, operation, function):
        if self._busy:
            return
        self._busy, self._operation = True, operation
        self.finish_button.setEnabled(False)
        self.cancel_button.setEnabled(False)    # a running step cannot be cancelled, so do not offer it
        self.waiting_cancel.setEnabled(operation == 'status' and not self._cancel_requested)
        self.tasks.submit(function)

    def connect_server(self):
        if not self.connect_button.isEnabled() or self._busy:
            return
        value = self.code.text()
        if self._origin:
            from lifetime_connection import connection_line
            value = connection_line(self._origin, value)
        self.code_error.hide()
        self.show_step(2)
        self.submit('find', lambda: locate_server(value))

    def diagnose(self):
        if self._diagnosing:
            return
        value = self.code.text() if self.step == 1 else ''
        if value and self._origin:
            from lifetime_connection import connection_line
            try:
                value = connection_line(self._origin, value)
            except ValueError:
                pass
        saved = (self.status.get('server') or None) if self.status.get('enabled') else None
        self._diagnosing = True
        for button in self.diagnose_buttons:
            button.setEnabled(False)
            button.setText(self.t('Checking the connection…'))
        self.diagnose_tasks.submit(lambda: diagnose(value, saved))

    def _diagnosed(self, checks):
        self._diagnosing = False
        for button in self.diagnose_buttons:
            button.setEnabled(True)
            button.setText(self.t('Diagnose connection'))
        self.diagnosis_checks = checks or []
        from lifetime_widgets_dashboard import StatusChip
        while self.diagnosis_rows.count():
            item = self.diagnosis_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for check in self.diagnosis_checks:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(StatusChip(check['state'], self.t(CHECK_WORDS[check['state']])), 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(2)
            self.note(words, '').setText(self.t(check['text'], **check['values']))
            if check['fix']:
                self.note(words, '', 'caption').setText(self.t('What to do: {fix}', fix=self.t(check['fix'], **check['values'])))
            line.addLayout(words, 1)
            self.diagnosis_rows.addWidget(row)
        self.diagnosis.setVisible(self.step in (1, 5) and bool(self.diagnosis_checks))

    def copy_report(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(diagnosis_report(self.diagnosis_checks, self.t))

    def copy_diagnostics(self):
        directory, root, checks = self.directory, self.machine_root, list(self.diagnosis_checks)
        for button in self.copy_buttons:
            button.setEnabled(False)
        self.copy_tasks.submit(lambda: diagnostics_text(self.t, directory, root, checks))

    def _copied(self, text):
        from PyQt6.QtWidgets import QApplication
        for button in self.copy_buttons:
            button.setEnabled(True)
        if text:
            QApplication.clipboard().setText(text)

    def policy(self):
        return dict(history=self.history.isChecked(), resources=self.resources.isChecked(),
                    health=self.health.isChecked() and self.health_allowed, control=self.control.isChecked(),
                    updates=self.updates.isChecked())       # kept on this computer; not part of what is shared

    def finish(self):
        if self._busy:
            return
        self.review_error.hide()
        action = 'policy' if self.editing else 'finish'
        policy, bundle, root = self.policy(), self.bundle, self.machine_root
        if action == 'finish':
            self.waiting_text.setText('')
            self.set_stage(1)
            self.show_step(4)
        self.submit(action, lambda: run_agent(action, policy, bundle, root, socket.gethostname()))

    def cancel(self):
        if self._busy:
            return
        self.show_step(5 if self.editing else 1)
        self.editing = False
        self.bundle = None

    def change_policy(self):
        if self._busy:
            return
        policy = self.status.get('policy') or {}
        for key in ('history', 'resources', 'health', 'control'):
            getattr(self, key).setChecked(bool(policy.get(key)) and (key != 'health' or self.health_allowed))
        self.updates.setChecked(bool(self.status.get('updates')))
        self.editing = True
        self.review_title.setText(self.t('Connected to {server}', server=self.server_name))
        self.review_address.setText(self.server_address)
        self.show_step(3)

    def cancel_waiting(self):
        if self.step != 4 or self.stage not in (1, 2) or self._cancel_requested:
            return
        if self._busy and self._operation != 'status':
            return
        self._cancel_requested = True
        self.waiting_cancel.setEnabled(False)
        if not self._busy:
            self._cancel_waiting()

    def _cancel_waiting(self):
        self.submit('cancel-waiting', lambda: run_agent('disconnect', {}, directory=self.machine_root))

    def disconnect(self):
        if self._busy:
            return
        if QMessageBox.question(self, self.t('Disconnect'), self.t('Disconnect from this server?')) == QMessageBox.StandardButton.Yes:
            self.submit('disconnect', lambda: run_agent('disconnect', {}, directory=self.machine_root))

    def poll(self):
        if not self._busy and self.step in (1, 4, 5):
            self.submit('status', lambda: read_status(self.machine_root))

    def completed(self, number, result):
        operation = self._operation
        self._busy = False
        self.finish_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.format_input()
        if operation == 'status' and self._cancel_requested:
            self._cancel_waiting()
            return
        if operation == 'find':
            from lifetime_tls import decode_bundle
            from urllib.parse import urlsplit
            self.bundle = result
            trust = decode_bundle(result)
            self.server_address = trust['address']
            self.server_name = urlsplit(self.server_address).hostname
            self.review_title.setText(self.t('Found {server}', server=self.server_name))      # found, not connected yet
            self.review_address.setText(self.server_address)
            self.show_step(3)
        elif operation in ('disconnect', 'cancel-waiting'):
            self._cancel_requested = False
            self.status = {}
            self.bundle = None
            self._report_started = None
            self.editing = False
            self.code.clear()
            self.code_error.hide()
            self.show_step(1)
        elif operation in ('status', 'finish', 'policy'):
            self.status = result or {}
            if self.status.get('status') in ('denied', 'revoked', 'invitation-used', 'invalid-code'):
                self._code_rejected(self.status.get('status'))
            elif self.status.get('enabled'):
                self.server_address = self.status.get('server') or self.server_address
                from urllib.parse import urlsplit
                self.server_name = self.status.get('server_name') or urlsplit(self.server_address).hostname or ''
                state = self.status.get('status')
                if state in ('denied', 'revoked', 'invitation-used', 'invalid-code'):
                    self._code_rejected(state)
                elif self.status.get('phase') == 'enrolling' or state == 'enrolling':
                    # An enrollment attempt is not an acknowledged server request.
                    # In particular an unreachable old address has no approval
                    # to show on the currently running server.
                    message = ("Can't reach the server right now. Lifetime will keep trying."
                               if state in ('unreachable', 'offline', 'error', 'retrying', 'backoff')
                               else 'Connecting securely')
                    self.waiting_text.setText(self.server_address + '\n' + self.t(message))
                    self.set_stage(1)
                    self.show_step(4)
                elif self.status.get('phase') == 'pending' or state == 'pending':
                    message = (self.t("Can't reach the server right now. Lifetime will keep trying.")
                               if state in ('unreachable', 'offline', 'error', 'retrying', 'backoff')
                               else self.t('{server} needs to approve this computer.', server=self.server_name))
                    self.waiting_text.setText(self.server_address + '\n' + message)
                    self.set_stage(2)
                    self.show_step(4)
                elif self.step == 4 and state == 'connected' and not self.status.get('reported') and not self._report_timed_out():
                    # Approved is not finished: stay here until the server has this computer's details.
                    self.waiting_text.setText(self.t('Almost done'))
                    self.set_stage(3)
                else:
                    self._report_started = None
                    self.render_status()
                    self.show_step(5)
            elif self.step in (4, 5) or operation != 'status':
                self.show_step(1)
            self.editing = False
        self.show_server_state()        # every page step; the sidebar chip follows through serverState

    def _report_timed_out(self):
        if self._report_started is None:
            self._report_started = time.monotonic()
        return time.monotonic() - self._report_started > 60     # never trap someone on this screen

    def render_status(self):
        names = ['Awake time']
        for key, title in (('history', 'Awake history by day'), ('resources', 'Computer specs and live performance'),
                           ('health', 'Health and crashes'), ('control', 'Let the server sleep or restart this computer')):
            if (self.status.get('policy') or {}).get(key):
                names.append(title)
        if self.status.get('updates'):
            names.append(UPDATES)
        self.summary.title.setText(self.server_name)
        values = {'Server address': self.server_address,
                  'Last report': self._local_time(self.status.get('last_sync')) or self.t('Not available'),
                  'Shared with the server': '\n'.join(self.t(name) for name in names)}
        for key, (title, value) in self.summary_values.items():
            title.setText(self.t(key))      # the language may have changed since the card was built
            value.setText(values[key])
        self.summary_trimmed.setText(self.t(TRIMMED) if self.status.get('trimmed') else '')
        self.summary_trimmed.setVisible(bool(self.status.get('trimmed')))
        self._set_reachable(self.status.get('status') not in UNREACHABLE)

    def _code_rejected(self, state):
        """Back to code entry, saying why. The old code is of no use, so it is cleared once (never while a new one is typed)."""
        if self.step != 1:
            self.code.clear()
        self.show_step(1)
        self.code_error.setText(self.t('The server removed this computer. Ask for a new code on the server to connect again.'
                                       if state == 'revoked' else 'This code cannot be used. Ask for a new code on the server.'))
        self.code_error.show()

    @staticmethod
    def _local_time(stamp):
        """'2026-11-10T17:03:17Z' means little to a person: show their own local date and time."""
        if not stamp:
            return ''
        try:
            moment = dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).astimezone()
        except ValueError:
            return str(stamp)
        return str(datetime_text(moment))

    REASONS = {
        'certificate': "The server's security certificate has changed. Connect again with a new code from the server.",
        'expired': "The server's security certificate is out of date, or this computer's clock is wrong. Check the date and time, or ask the administrator to check Lifetime Server.",
        'hostname': "This address is not one of the server's names. Ask the administrator to add it under Administration on Lifetime Server.",
        'tls': 'The secure connection to the server failed.',
        'name': "The server's name could not be found on this network.",
        'refused': 'The server computer answered, but Lifetime Server is not running on it.',
        'timeout': 'No answer from the server. It may be switched off, or a firewall may be blocking it.',
        'dropped': 'The connection to the server was interrupted.',
        'network': 'This computer has no network path to the server.',
        'protocol': 'The server sent a reply this version does not understand. Update Lifetime on both computers.',
        'internal': 'Lifetime hit an internal error while reporting. It will keep trying.',
    }

    def _reason(self):
        """Why the last attempt failed, in plain words; only a category is ever stored, never the raw error."""
        return reason_text(self.status.get('error'), self.t)

    def show_server_state(self, now=None):
        """The Server chip from the last status read (a file read in the background); it repaints only on a change."""
        state = server_state(self.status, self.t, now)
        if state != self._server_state:
            self._server_state = state
            self.server_chip.set_state(state[0], state[2], state[3])
            self.serverState.emit(*state)
        self.server_chip.setVisible(state[0] != 'neutral')     # a computer that never connects shows no server status
        return state

    def _set_reachable(self, reachable):
        """The title and dot must agree with the line below them: never 'Connected' beside 'Can't reach the server'."""
        state = self.show_server_state()
        reachable = reachable and state[0] != 'warning'
        reason = '' if reachable else (state[3] or self._reason())
        self.unreachable.setText(self.t("Can't reach the server right now. Lifetime will keep trying.") + ('\n' + reason if reason else ''))
        self.unreachable.setVisible(not reachable)
        self.connected_title.setText(self.t('Connected' if reachable else 'Waiting for the server'))
        self.connected_title.dot.set_active(reachable)

    def failed(self, number, error):
        operation = self._operation
        self._busy = False
        self.finish_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.format_input()
        if operation == 'status' and self._cancel_requested:
            self._cancel_waiting()
            return
        if operation == 'cancel-waiting':
            self._cancel_requested = False
            self.waiting_cancel.setEnabled(True)
            self.waiting_text.setText(self.t(PERMISSION if error == 'PermissionError' else
                                           'Could not cancel the connection. Try again.'))
            return
        if operation == 'find':
            self.show_step(1)
            self.code_error.setText(self.t(NO_SERVER))
            self.code_error.show()
        elif operation in ('finish', 'policy', 'disconnect'):
            if operation == 'disconnect':
                self.change_policy()
            self.show_step(3)
            self.review_error.setText(self.t(PERMISSION) if error == 'PermissionError' else setup_failure_text(error, self.t))
            self.review_error.show()
        elif self.step == 5:
            self._set_reachable(False)
