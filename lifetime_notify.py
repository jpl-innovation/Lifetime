"""Alert messages for Lifetime Server: email over SMTP (stdlib smtplib and ssl only) and a generic HTTPS webhook.

The SMTP password and the webhook URL (which often carries its own token) are secrets. They live in
``alert-secrets.bin`` in the server data directory, never in monitoring.sqlite, logs, exports or replies:
on Windows encrypted with DPAPI (the account that runs the server, normally SYSTEM, is the only one that can read
them) and restricted to Administrators and SYSTEM; elsewhere stored as a 0600 file. Messages carry the computer's
name and group, the problem and its times only: no activity history, readings history, addresses or secrets.
"""
import ctypes
import datetime as dt
from email.message import EmailMessage
import json
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

import lifetime_permissions as permissions

SECRET_FILE = 'alert-secrets.bin'
ENTROPY = b'Lifetime Server alert secrets v1'
TIMEOUT = 8         # seconds per attempt; well inside the dashboard's 10-second admin call
EMAIL = re.compile(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*')
HOST = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?')
DEFAULTS = dict(email_enabled=False, smtp_host='', smtp_port=587, security='starttls', username='', sender='',
                recipients=[], webhook_enabled=False, summary_enabled=False, summary_hour=8, language='en')


class NotifyError(ValueError):
    """Invalid notification settings."""


def validate(settings):
    """Complete, checked notification settings (secrets are handled separately)."""
    value = dict(DEFAULTS, **settings)
    if set(value) != set(DEFAULTS):
        raise NotifyError('Unknown notification setting')
    for key in ('email_enabled', 'webhook_enabled', 'summary_enabled'):
        if type(value[key]) is not bool:
            raise NotifyError('Invalid switch')
    if not isinstance(value['smtp_host'], str) or (value['smtp_host'] and not HOST.fullmatch(value['smtp_host'])):
        raise NotifyError('Invalid mail server name')
    if type(value['smtp_port']) is not int or not 1 <= value['smtp_port'] <= 65535:
        raise NotifyError('Invalid mail server port')
    if value['security'] not in ('starttls', 'tls'):
        raise NotifyError('Mail must use STARTTLS or TLS')
    if (not isinstance(value['username'], str) or len(value['username']) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value['username'])):
        raise NotifyError('Invalid user name')
    if not isinstance(value['sender'], str) or (value['sender'] and not EMAIL.fullmatch(value['sender'])):
        raise NotifyError('Invalid sender address')
    recipients = value['recipients']
    if (not isinstance(recipients, list) or len(recipients) > 20
            or any(not isinstance(item, str) or not EMAIL.fullmatch(item) for item in recipients)):
        raise NotifyError('Invalid recipients')
    if type(value['summary_hour']) is not int or not 0 <= value['summary_hour'] <= 23:
        raise NotifyError('Invalid summary hour')
    if value['language'] not in ('en', 'vi'):
        raise NotifyError('Invalid language')
    if value['email_enabled'] and not (value['smtp_host'] and value['sender'] and recipients):
        raise NotifyError('Email needs a mail server, a sender and at least one recipient')
    return dict(value, recipients=list(dict.fromkeys(recipients)))


def check_webhook(url):
    """HTTPS only, a host name, no spaces, no user name or password in the URL."""
    try:
        parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        port = parts and parts.port
    except ValueError:
        parts = port = None
    if (not parts or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or len(url) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in url) or port == 0):
        raise NotifyError('The webhook must be an https:// address')
    return url


def masked(url):
    """What the dashboard may show of a webhook: its scheme and host (the path often holds a token)."""
    return f'https://{urllib.parse.urlsplit(url).hostname}/…' if url else ''


# ---- protected secret storage ----

def _dpapi(data, protect):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    source_buffer, entropy_buffer = ctypes.create_string_buffer(data, len(data)), ctypes.create_string_buffer(ENTROPY, len(ENTROPY))
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.c_void_p))
    entropy = Blob(len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.c_void_p))
    result = Blob()
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(result)):  # UI_FORBIDDEN
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


class Secrets:
    """The SMTP password and webhook URL, protected at rest (DPAPI on Windows, a 0600 file elsewhere)."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            with self.path.open('rb') as source:
                raw = source.read(65537)
        except FileNotFoundError:
            return {}
        try:
            if raw.startswith(b'DPAPI1\n') and sys.platform == 'win32':
                data = _dpapi(raw[7:], False)
            elif raw.startswith(b'PLAIN1\n') and sys.platform != 'win32':
                data = raw[7:]
            else:
                raise ValueError()
            value = json.loads(data)
            return {key: value[key] for key in ('smtp_password', 'webhook_url') if isinstance(value.get(key), str)}
        except (OSError, ValueError, TypeError):
            return {'unreadable': True}      # written by another account or damaged: enter the secrets again

    def save(self, values):
        payload = json.dumps({key: value for key, value in values.items() if value}).encode('utf-8')
        data = b'DPAPI1\n' + _dpapi(payload, True) if sys.platform == 'win32' else b'PLAIN1\n' + payload
        temporary = self.path.with_name(self.path.name + '.new')
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
                             | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                permissions.protect_private(temporary)      # restricted before the secret is written
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


# ---- transports ----

def send_email(settings, password, subject, body, context=None, timeout=TIMEOUT):
    """One message to every recipient. STARTTLS or implicit TLS is required: never a plain connection."""
    message = EmailMessage()
    message['From'] = settings['sender']
    message['To'] = ', '.join(settings['recipients'])
    message['Subject'] = subject
    message.set_content(body)
    context = context or ssl.create_default_context()
    if settings['security'] == 'tls':
        client = smtplib.SMTP_SSL(settings['smtp_host'], settings['smtp_port'], timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(settings['smtp_host'], settings['smtp_port'], timeout=timeout)
    with client:
        if settings['security'] == 'starttls':
            client.starttls(context=context)     # SMTPNotSupportedError if the server does not offer it
        if settings['username']:
            client.login(settings['username'], password or '')
        client.send_message(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):
        return None     # a redirect is a failure, never a second destination


def post_webhook(url, payload, context=None, timeout=TIMEOUT):
    request = urllib.request.Request(check_webhook(url), data=json.dumps(payload).encode('utf-8'), method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'Lifetime-Server'})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
    with opener.open(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(url, response.status, 'Unexpected status', response.headers, None)


def category(error):
    """A secret-free word for the delivery log."""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'auth'
    if isinstance(error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return 'refused'
    if isinstance(error, (smtplib.SMTPNotSupportedError, ssl.SSLError, ssl.CertificateError)):
        return 'tls'
    if isinstance(error, urllib.error.HTTPError):
        return f'http-{error.code}' if 100 <= error.code <= 599 else 'http'
    if isinstance(error, urllib.error.URLError):
        return category(error.reason) if isinstance(error.reason, BaseException) else 'connect'
    if isinstance(error, (socket.timeout, TimeoutError)):
        return 'timeout'
    if isinstance(error, (smtplib.SMTPServerDisconnected, ConnectionError, socket.gaierror, OSError)):
        return 'connect'
    if isinstance(error, smtplib.SMTPException):
        return 'smtp'
    return 'other'


class Transport:
    """The real senders; tests pass their own with the same two methods."""
    context = None

    def send_email(self, settings, password, subject, body):
        send_email(settings, password, subject, body, self.context)

    def post_webhook(self, url, payload):
        post_webhook(url, payload, self.context)


# ---- message text (English and Vietnamese) ----

RULE_TEXT = {
    'offline': ('Computer offline', 'Máy tính ngoại tuyến'),
    'no_report': ('No report received', 'Không nhận được báo cáo'),
    'disk_low': ('Disk almost full', 'Ổ đĩa gần đầy'),
    'cpu_high': ('Processor busy', 'Bộ xử lý quá tải'),
    'memory_high': ('Memory almost full', 'Bộ nhớ gần đầy'),
    'battery_worn': ('Battery worn', 'Pin đã chai'),
    'temperature_high': ('Running hot', 'Đang quá nóng'),
    'crashes': ('Repeated crashes', 'Lỗi hệ thống lặp lại'),
    'agent_outdated': ('Lifetime version too old', 'Phiên bản Lifetime quá cũ'),
    'check_down': ('Network check failing', 'Kiểm tra mạng không thành công'),
}
UNITS = {'disk_low': '% free', 'cpu_high': '%', 'memory_high': '%', 'battery_worn': '%', 'temperature_high': ' °C',
         'crashes': ''}
TEXT = {
    'open': ('Problem: {problem} on {computer}', 'Sự cố: {problem} trên {computer}'),
    'recovery': ('Resolved: {problem} on {computer}', 'Đã khắc phục: {problem} trên {computer}'),
    'computer': ('Computer', 'Máy tính'), 'group': ('Group', 'Nhóm'), 'problem': ('Problem', 'Sự cố'),
    'value': ('Last reading', 'Số đo gần nhất'), 'since': ('Since', 'Từ'), 'resolved': ('Resolved at', 'Khắc phục lúc'),
    'server': ('Lifetime Server', 'Lifetime Server'),
    'hint': ('Open Incidents on the Lifetime Server dashboard to acknowledge it.',
             'Mở mục Sự cố trên bảng điều khiển Lifetime Server để xác nhận.'),
    'test': ('Lifetime Server test message', 'Tin nhắn thử từ Lifetime Server'),
    'test_body': ('Alerts from {server} reach this address.', 'Cảnh báo từ {server} sẽ được gửi đến địa chỉ này.'),
    'summary': ('Lifetime Server daily summary: {active} open', 'Tóm tắt hằng ngày của Lifetime Server: {active} sự cố đang mở'),
    'summary_counts': ('Open {open} · acknowledged {acknowledged} · paused {suppressed} · opened in the last 24 hours {new} · resolved {resolved}',
                       'Đang mở {open} · đã xác nhận {acknowledged} · tạm dừng {suppressed} · mở trong 24 giờ qua {new} · đã khắc phục {resolved}'),
}


def _text(key, language, **values):
    return TEXT[key][language == 'vi'].format(**values)


def _clean(value):
    return ''.join(c if ord(c) >= 32 else ' ' for c in str(value or ''))[:120]


def _when(stamp):
    return dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if stamp else ''


def compose(kind, incident, server, language='en'):
    """(subject, body, payload) for an opened or resolved incident."""
    problem = RULE_TEXT.get(incident['rule'], (incident['rule'],) * 2)[language == 'vi']
    computer = _clean(incident['device_name']) or incident['device_id']
    subject = f"[{_clean(server)}] " + _text(kind, language, problem=problem, computer=computer)
    lines = [f"{_text('computer', language)}: {computer}"]
    if incident.get('group_name'):
        lines.append(f"{_text('group', language)}: {_clean(incident['group_name'])}")
    lines.append(f"{_text('problem', language)}: {problem}")
    if incident.get('value') is not None and incident['rule'] in UNITS:
        lines.append(f"{_text('value', language)}: {incident['value']:g}{UNITS[incident['rule']]}")
    lines.append(f"{_text('since', language)}: {_when(incident['started_at'])}")
    if kind == 'recovery':
        lines.append(f"{_text('resolved', language)}: {_when(incident['resolved_at'])}")
    else:
        lines += ['', _text('hint', language)]
    payload = dict(source='Lifetime Server', server=_clean(server), event=kind, text=subject + '\n' + '\n'.join(lines),
                   incident={key: incident.get(key) for key in ('id', 'rule', 'state', 'started_at', 'opened_at', 'resolved_at')},
                   computer=computer, group=_clean(incident.get('group_name')))
    return subject, '\n'.join(lines) + '\n', payload


def compose_test(server, language='en'):
    subject = _text('test', language)
    body = _text('test_body', language, server=_clean(server))
    return subject, body + '\n', dict(source='Lifetime Server', server=_clean(server), event='test', text=subject + '\n' + body)


def compose_summary(counts, items, server, language='en'):
    subject = f"[{_clean(server)}] " + _text('summary', language, active=counts['open'] + counts['acknowledged'])
    lines = [_text('summary_counts', language, **counts), '']
    for item in items:
        problem = RULE_TEXT.get(item['rule'], (item['rule'],) * 2)[language == 'vi']
        lines.append(f"- {_clean(item['device_name'])}: {problem} ({_when(item['started_at'])})")
    return subject, '\n'.join(lines) + '\n', dict(source='Lifetime Server', server=_clean(server), event='summary',
                                                  text=subject + '\n' + '\n'.join(lines), counts=counts)
