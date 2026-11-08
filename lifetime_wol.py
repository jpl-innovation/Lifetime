"""Wake-on-LAN broadcasts only; never connects to a device."""
import ipaddress
import re
import socket
import sys


def local_broadcasts():
    """Best effort IPv4 interface broadcasts, without assuming a /24 subnet."""
    result = {'255.255.255.255'}
    try:
        if sys.platform == 'win32':
            import lifetime_signals as signals
            rows = signals.objects(signals.parse_json(signals.powershell(
                'Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress')))
            for row in rows:
                network = ipaddress.IPv4Network(f"{row['IPAddress']}/{row['PrefixLength']}", strict=False)
                if not network.is_loopback and network.prefixlen < 31:
                    result.add(str(network.broadcast_address))
        else:
            import fcntl
            import struct
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for _, name in socket.if_nameindex():
                    try:
                        request = struct.pack('256s', name.encode()[:15])
                        flags = struct.unpack('H', fcntl.ioctl(sock, 0x8913, request)[16:18])[0]
                        if flags & 2:  # IFF_BROADCAST
                            result.add(socket.inet_ntoa(fcntl.ioctl(sock, 0x8919, request)[20:24]))
                    except OSError:
                        continue
    except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError):
        pass
    return sorted(result)


def send_magic_packet(mac, sock_factory=socket.socket):
    if not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac):
        raise ValueError('Invalid MAC address')
    packet = b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16
    sock = sock_factory(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2)
        for broadcast in local_broadcasts():
            sock.sendto(packet, (broadcast, 9))
    finally:
        sock.close()
    return len(packet)
