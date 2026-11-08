"""The persistent live channel: message envelope shared by the server and the computer.

One WebSocket over pinned TLS carries live samples, rate changes, commands and acknowledgements.
Durable history and health still travel on the HTTPS sync, so the exactly-once ledger is untouched.
"""
import json
import time
import uuid

PATH = '/v1/ws'
VERSION = 1
MAX_FRAME = 8192            # a telemetry frame is about 400 bytes; this is generous and still bounded
MIN_RATE_MS, MAX_RATE_MS = 250, 60000
TYPES = ('hello', 'telemetry', 'command', 'ack', 'status')
ACKS = ('executed', 'failed', 'unsupported', 'declined')


class EnvelopeError(ValueError):
    pass


def envelope(kind, data=None, id=None):
    return json.dumps({'type': kind, 'id': id or str(uuid.uuid4()), 'ts': int(time.time()*1000), 'data': data},
                      separators=(',', ':'), allow_nan=False)


def parse(raw):
    """Strict decode: one JSON object with a known type. Anything else ends the connection."""
    if isinstance(raw, bytes) or len(raw) > MAX_FRAME:
        raise EnvelopeError('Unsupported frame')
    try:
        value = json.loads(raw)
    except ValueError:
        raise EnvelopeError('Invalid JSON') from None
    if (not isinstance(value, dict) or set(value) - {'type', 'id', 'ts', 'data'}
            or value.get('type') not in TYPES or not isinstance(value.get('id', ''), str)):
        raise EnvelopeError('Invalid envelope')
    return value


def clamp_rate(value):
    if type(value) is not int:
        raise EnvelopeError('Invalid rate')
    return max(MIN_RATE_MS, min(MAX_RATE_MS, value))


class TelemetryTransport:
    """What the live sender needs from a transport.

    Telemetry is the only part a faster, lossy channel (UDP) could carry later: it would implement
    send_sample and nothing else, while commands, acknowledgements and presence stay on the socket.
    """
    def send_sample(self, sample):
        raise NotImplementedError
