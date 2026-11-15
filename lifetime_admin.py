"""Explicit loopback administration client; never discovers or contacts LAN hosts.

On Windows it first uses the server's identity-bound named pipe (the server reads this Windows account from the pipe
and applies its role). Without that pipe (an older server, or Linux), or when this account has no role on it, it uses
the loopback HTTP channel with ``admin-token``, whose holder the server treats as Admin.
"""
import http.client
from pathlib import Path
import sys

from lifetime_protocol import MAX_BYTES, canonical, decode

OPERATIONS = frozenset((
    'live', 'devices', 'device', 'code', 'approve', 'deny', 'revoke', 'delete', 'send_command', 'cancel_command',
    'wake', 'identity', 'retain', 'backup', 'restore', 'configuration', 'set_configuration', 'refresh', 'overview',
    'invitation', 'invitations', 'revoke_invitation', 'fleet', 'server_info', 'pending_enrollments', 'organisation',
    'names', 'server_log', 'presence', 'alert_rules', 'set_alert_rule', 'incidents', 'incident', 'acknowledge',
    'alert_summary', 'schedules', 'set_schedule', 'delete_schedule', 'maintenance', 'add_maintenance',
    'delete_maintenance', 'notifications', 'set_notifications', 'test_notification', 'deliveries', 'time_zones',
    'availability', 'coverage_floor', 'set_coverage_floor', 'checks', 'save_check', 'delete_check', 'check_scopes',
    'set_check_scopes', 'whoami', 'roles', 'set_role', 'delete_role', 'audit', 'verify_audit', 'backup_archive',
    'verify_backup', 'stage_restore'))


class AdminClient:
    def __init__(self, directory, port, timeout=10):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid admin port')
        self.directory = Path(directory)
        self.port = port
        self.timeout = timeout

    def call(self, operation, **arguments):
        if operation not in OPERATIONS:
            raise ValueError('Unknown administrative operation')
        body = canonical(arguments)
        if len(body) > MAX_BYTES:
            raise ValueError('Admin request too large')
        if sys.platform == 'win32':
            import lifetime_pipe
            try:
                raw = lifetime_pipe.call(lifetime_pipe.pipe_name(self.port),
                                         canonical(dict(operation=operation, arguments=arguments)), self.timeout)
            except (FileNotFoundError, PermissionError):
                pass        # no pipe, or no role on it: the token channel decides
            else:
                reply = decode(raw)
                if not isinstance(reply, dict) or reply.get('status') != 200 or 'result' not in reply:
                    raise ValueError('Administrative request failed (HTTP %s)' % (
                        reply.get('status') if isinstance(reply, dict) and type(reply.get('status')) is int else 500))
                return reply['result']
        token = (self.directory / 'admin-token').read_text(encoding='ascii').strip()
        if not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('Invalid administrator token')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            connection.request('POST', '/v1/' + operation, body, {
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('Administrative request failed (HTTP %s)' % response.status)
            raw = response.read(MAX_BYTES + 1)
            return decode(raw)
        finally:
            connection.close()
