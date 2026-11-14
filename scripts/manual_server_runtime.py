"""Local launch diagnostics; retain the normal server lifecycle and protections."""
from pathlib import Path
import sys
import traceback
import ipaddress
import socket

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lifetime_server as server


def certificate_address(directory, port):
    """Reuse a supported SAN from the existing public certificate, without rotation."""
    from cryptography import x509
    certificate = Path(directory) / 'server-cert.pem'
    if not certificate.exists():
        return f'https://localhost:{port}'
    with certificate.open('rb') as source:
        pem = source.read(server.tls.MAX_CERTIFICATE_BYTES + 1)
    if len(pem) > server.tls.MAX_CERTIFICATE_BYTES:
        raise ValueError('Existing server certificate exceeds the size limit')
    cert = x509.load_pem_x509_certificate(pem)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        raise ValueError('Existing server certificate has no Subject Alternative Name') from None
    names = san.get_values_for_type(x509.DNSName)
    names += [str(value) for value in san.get_values_for_type(x509.IPAddress)]
    preferred = ['127.0.0.1', 'localhost', socket.gethostname(), socket.getfqdn()]
    names = sorted(names, key=lambda value: preferred.index(value) if value in preferred else len(preferred))
    for name in names:
        try:
            host = server.tls._hostname(name)
        except ValueError:
            continue  # Wildcards and invalid names are not explicit enrollment origins.
        authority = f'[{host}]' if ':' in host else host
        return server.tls._address(f'https://{authority}:{port}')[0]
    raise ValueError('Existing certificate has no supported explicit DNS name or IP address')


class DiagnosticRuntime(server.ServerRuntime):
    def __init__(self, directory, **configuration):
        configuration['address'] = certificate_address(directory, configuration.get('port', 8443))
        host = server.tls._address(configuration['address'])[1]
        try:
            if ipaddress.ip_address(host).version == 6:
                configuration['host'] = '::'
        except ValueError:
            pass
        print('Reusing server certificate address: ' + configuration['address'], flush=True)
        super().__init__(directory, **configuration)

    def start(self):
        try:
            return super().start()
        except Exception:
            # The normal CLI deliberately hides exception details. This local
            # helper writes the cause to its redirected log, never to clients.
            traceback.print_exc()
            raise


if __name__ == '__main__':
    server.ServerRuntime = DiagnosticRuntime
    raise SystemExit(server.main())
