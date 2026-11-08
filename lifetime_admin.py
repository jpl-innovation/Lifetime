"""Explicit loopback administration client; never discovers or contacts LAN hosts."""
import http.client
from pathlib import Path

from lifetime_protocol import MAX_BYTES, canonical, decode


class AdminClient:
    def __init__(self, directory, port, timeout=10):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid admin port')
        self.directory = Path(directory)
        self.port = port
        self.timeout = timeout

    def call(self, operation, **arguments):
        if operation not in ('devices', 'device', 'code', 'approve', 'deny', 'revoke',
                             'identity', 'retain', 'backup', 'restore', 'configuration', 'set_configuration', 'refresh', 'overview'):
            raise ValueError('Unknown administrative operation')
        token = (self.directory / 'admin-token').read_text(encoding='ascii').strip()
        if not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('Invalid administrator token')
        body = canonical(arguments)
        if len(body) > MAX_BYTES:
            raise ValueError('Admin request too large')
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
