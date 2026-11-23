"""Explicit loopback administration client; never discovers or contacts LAN hosts.

On Windows it first uses the server's identity-bound named pipe (the server reads this Windows account from the pipe
and applies its role); on Linux the identity-bound Unix socket (lifetime_unix_admin, SO_PEERCRED). Without that channel
(an older server), or when this account has no role on it, it uses the loopback HTTP channel with ``admin-token``,
whose holder the server treats as Admin.
"""
import http.client
from pathlib import Path
import sys

from lifetime_protocol import MAX_BYTES, canonical, decode

OPERATIONS = frozenset((
    'live', 'devices', 'device', 'code', 'approve', 'deny', 'revoke', 'delete', 'send_command', 'cancel_command',
    'wake', 'identity', 'retain', 'backup', 'restore', 'configuration', 'set_configuration', 'refresh', 'overview',
    'invitation', 'invitations', 'revoke_invitation', 'fleet', 'server_info', 'self_check', 'pending_enrollments', 'organisation',
    'names', 'server_log', 'presence', 'alert_rules', 'set_alert_rule', 'incidents', 'incident', 'acknowledge',
    'alert_summary', 'schedules', 'set_schedule', 'delete_schedule', 'maintenance', 'add_maintenance',
    'delete_maintenance', 'notifications', 'set_notifications', 'test_notification', 'deliveries', 'time_zones',
    'availability', 'coverage_floor', 'set_coverage_floor', 'checks', 'save_check', 'delete_check', 'check_scopes',
    'set_check_scopes', 'whoami', 'roles', 'set_role', 'delete_role', 'audit', 'verify_audit', 'backup_archive',
    'verify_backup', 'stage_restore', 'updates', 'upload_update', 'set_update', 'rollback_update',
    'insights', 'set_insight_settings', 'set_sleep_policy', 'groups', 'add_group', 'remove_group', 'rename_group',
    'detail_retention', 'set_detail_retention', 'uptime_bars', 'uptime_days'))


class NotAllowed(ValueError):
    """The server refused this operation for the caller's role (HTTP 403)."""


class Refused(ValueError):
    """The server refused this request for a known reason; ``detail_code`` is the key in REFUSALS."""


# Fixed reason codes the server may name with a refusal (lifetime_server_store), in plain words (translated in the GUI).
REFUSALS = {
    'power-consent': 'This computer does not allow remote sleep, restart, shut down or turn on. Turn on power control on that computer first.',
    'not-online': 'This computer needs a recent sync first.',
    'command-pending': 'A command is already pending for this computer.',
    'group-exists': 'A group with that name already exists.',
}


def _failed(status, reason=None):
    if status != 403 and isinstance(reason, str) and reason in REFUSALS:
        error = Refused('Administrative request failed (HTTP %s): %s' % (status, REFUSALS[reason]))
        error.detail_code = reason
        return error
    return (NotAllowed if status == 403 else ValueError)('Administrative request failed (HTTP %s)' % status)


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
        if sys.platform == 'win32' or sys.platform.startswith('linux'):
            request = canonical(dict(operation=operation, arguments=arguments))
            try:
                if sys.platform == 'win32':
                    import lifetime_pipe
                    raw = lifetime_pipe.call(lifetime_pipe.pipe_name(self.port), request, self.timeout)
                else:
                    import lifetime_unix_admin
                    raw = lifetime_unix_admin.call(lifetime_unix_admin.socket_path(self.directory, self.port),
                                                   request, self.timeout)
            except (FileNotFoundError, PermissionError):
                pass        # no pipe, or no role on it: the token channel decides
            else:
                reply = decode(raw)
                if not isinstance(reply, dict) or reply.get('status') != 200 or 'result' not in reply:
                    if not isinstance(reply, dict):
                        raise _failed(500)
                    raise _failed(reply.get('status') if type(reply.get('status')) is int else 500, reply.get('reason'))
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
                reason = None
                if response.status in (400, 409):       # the only refusals that name a reason
                    try:
                        reason = decode(response.read(4096)).get('reason')
                    except Exception:       # no readable body: the status alone
                        pass
                raise _failed(response.status, reason)
            raw = response.read(MAX_BYTES + 1)
            return decode(raw)
        finally:
            connection.close()
