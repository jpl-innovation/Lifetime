"""Small bounded DNS-SD responder/browser for _lifetime._tcp.local.

No device data, secrets, DNS installation or third-party dependencies. Packets
are untrusted hints; the connection code authenticates the actual certificate.
"""
import re
import socket
import struct
import threading
import time

SERVICE = '_lifetime._tcp.local'
GROUP = ('224.0.0.251', 5353)
MAX_PACKET = 4096


def _name(value):
    labels = value.rstrip('.').split('.')
    result = b''
    for label in labels:
        data = label.encode('utf-8')
        if not 1 <= len(data) <= 63:
            raise ValueError('Invalid DNS name')
        result += bytes([len(data)]) + data
    if len(result) > 254:
        raise ValueError('Invalid DNS name')
    return result + b'\0'


def _read_name(packet, offset):
    labels, visited, end = [], set(), None
    for _ in range(64):
        if offset >= len(packet) or offset in visited:
            raise ValueError('Invalid DNS name')
        visited.add(offset)
        size = packet[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError('Invalid DNS pointer')
            end = end if end is not None else offset + 2
            offset = ((size & 63) << 8) | packet[offset + 1]
            continue
        if size & 0xC0 or size > 63 or offset + size + 1 > len(packet):
            raise ValueError('Invalid DNS label')
        offset += 1
        if not size:
            return '.'.join(labels), end if end is not None else offset
        labels.append(packet[offset:offset+size].decode('utf-8'))
        offset += size
    raise ValueError('DNS name too deep')


def query():
    return struct.pack('!6H', 0, 0, 1, 0, 0, 0) + _name(SERVICE) + struct.pack('!HH', 12, 1)


def lifetime_query(packet, instance=None, host=None):
    try:
        if not 12 <= len(packet) <= MAX_PACKET:
            return False
        _, flags, count, _, _, _ = struct.unpack('!6H', packet[:12])
        if flags & 0x8000 or not 1 <= count <= 32:
            return False
        offset, matched = 12, False
        allowed = {SERVICE.casefold(), (instance or '').casefold()}
        for _ in range(count):
            name, offset = _read_name(packet, offset)
            kind, cls = struct.unpack_from('!HH', packet, offset)
            offset += 4
            matched |= name.casefold() in allowed and kind in (12, 16, 33, 255) and cls & 0x7FFF == 1
        return matched
    except (ValueError, UnicodeError, struct.error):
        return False


def announcement(instance, host, port, fingerprint):
    if not 1 <= port <= 65535 or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ValueError('Invalid service data')
    full = instance + '.' + SERVICE
    def rr(name, kind, data):
        return _name(name) + struct.pack('!HHIH', kind, 1, 120, len(data)) + data
    txt = [b'v=1', ('fp=' + fingerprint[:10]).encode('ascii')]
    records = [rr(SERVICE, 12, _name(full)),
               rr(full, 33, struct.pack('!HHH', 0, 0, port) + _name(host)),
               rr(full, 16, b''.join(bytes([len(v)]) + v for v in txt))]
    return struct.pack('!6H', 0, 0x8400, 0, len(records), 0, 0) + b''.join(records)


def parse(packet):
    """Parse only complete Lifetime PTR/SRV/TXT advertisements from one packet."""
    try:
        if not 12 <= len(packet) <= MAX_PACKET:
            return []
        _, flags, qd, an, ns, ar = struct.unpack('!6H', packet[:12])
        if not flags & 0x8000 or qd > 32 or an + ns + ar > 64:
            return []
        offset, pointers, services, texts = 12, set(), {}, {}
        for _ in range(qd):
            _, offset = _read_name(packet, offset)
            offset += 4
        for _ in range(an + ns + ar):
            name, offset = _read_name(packet, offset)
            kind, cls, ttl, size = struct.unpack_from('!HHIH', packet, offset)
            offset += 10
            end = offset + size
            if end > len(packet):
                raise ValueError('Truncated DNS record')
            if cls & 0x7FFF == 1 and ttl:
                if kind == 12 and name.casefold() == SERVICE:
                    target, _ = _read_name(packet, offset)
                    if target.casefold().endswith('.' + SERVICE):
                        pointers.add(target)
                elif kind == 33 and size >= 7:
                    port = struct.unpack_from('!H', packet, offset + 4)[0]
                    host, _ = _read_name(packet, offset + 6)
                    services[name] = (host, port)
                elif kind == 16:
                    pos, fields = offset, {}
                    while pos < end:
                        length = packet[pos]
                        pos += 1
                        if pos + length > end:
                            raise ValueError('Truncated TXT')
                        key, _, value = packet[pos:pos+length].decode('ascii').partition('=')
                        fields[key] = value
                        pos += length
                    texts[name] = fields
            offset = end
        result = []
        from lifetime_connection import address
        for name in sorted(pointers):
            if name not in services or name not in texts:
                continue
            host, port = services[name]
            prefix = texts[name].get('fp', '')
            if not port or not re.fullmatch('[0-9a-f]{10}', prefix) or texts[name].get('v') != '1':
                continue
            authority = f'[{host}]' if ':' in host else host
            result.append(dict(instance=name[:-(len(SERVICE)+1)], host=host, port=port,
                               address=address(f'https://{authority}:{port}'), fingerprint_prefix=prefix))
        return result[:32]
    except (ValueError, UnicodeError, struct.error):
        return []


def _socket(factory, listener=False):
    sock = factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if listener:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', GROUP[1]))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(GROUP[0]) + socket.inet_aton('0.0.0.0'))
        else:
            sock.bind(('', 0))
        sock.settimeout(0.2)
        return sock
    except BaseException:
        sock.close()
        raise


def discover(timeout=3, socket_factory=socket.socket):
    if isinstance(timeout, bool) or not 0 < timeout <= 3:
        raise ValueError('Invalid discovery timeout')
    found = {}
    try:
        with _socket(socket_factory) as sock:
            # Legacy unicast reply requested by the ephemeral source port.
            sock.sendto(query(), GROUP)
            deadline = time.monotonic() + timeout
            received = 0
            while time.monotonic() < deadline and received < 128:
                try:
                    packet, _ = sock.recvfrom(MAX_PACKET + 1)
                except socket.timeout:
                    continue
                received += 1
                for server in parse(packet):
                    found[(server['address'], server['instance'])] = server
                    if len(found) >= 32:
                        return list(found.values())
    except OSError:
        pass
    return list(found.values())


class Responder:
    def __init__(self, instance, host, port, fingerprint, socket_factory=socket.socket):
        self.instance, self.host = instance, host
        self.packet = announcement(instance, host, port, fingerprint)
        self.factory, self.stopped = socket_factory, threading.Event()
        self.thread, self.sock = None, None

    def start(self):
        try:
            self.sock = _socket(self.factory, listener=True)
        except OSError:
            return False  # Manual address and connection line still work.
        self.thread = threading.Thread(target=self._run, name='Lifetime-discovery', daemon=True)
        self.thread.start()
        return True

    def answer(self, packet, peer):
        if lifetime_query(packet, self.instance + '.' + SERVICE, self.host):
            self.sock.sendto(self.packet, peer if peer[1] != 5353 else GROUP)

    def _run(self):
        # At most five answers per second even under a multicast query flood.
        next_answer = 0
        while not self.stopped.is_set():
            try:
                packet, peer = self.sock.recvfrom(MAX_PACKET + 1)
                if time.monotonic() >= next_answer:
                    self.answer(packet, peer)
                    next_answer = time.monotonic() + 0.2
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self.stopped.set()
        if self.sock:
            self.sock.close()
        if self.thread:
            self.thread.join(timeout=0.5)
