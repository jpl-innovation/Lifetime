import importlib.util, pathlib, sys, tempfile, unittest, socket, ssl
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
import lifetime_permissions as permissions
import lifetime_tls as tls
import lifetime_server as server
spec=importlib.util.spec_from_file_location('manual',pathlib.Path(__file__).with_name('manual_server_runtime.py'))
manual=importlib.util.module_from_spec(spec);spec.loader.exec_module(manual)

class CertificateLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
        if sys.platform=='win32': self.enterContext(patch.object(permissions,'windows_acl'))
    def certificate(self,host):
        authority=f'[{host}]' if ':' in host else host
        tls.generate_certificate(self.root,f'https://{authority}:8443')
    def test_ipv4_identity_is_preserved(self):
        self.certificate('127.0.0.1')
        before=(self.root/'server-cert.pem').read_bytes()
        self.assertEqual(manual.certificate_address(self.root,8443),'https://127.0.0.1:8443')
        self.assertEqual(before,(self.root/'server-cert.pem').read_bytes())
    def test_dns_identity_is_preserved(self):
        self.certificate('example-server.test')
        self.assertEqual(manual.certificate_address(self.root,8443),'https://example-server.test:8443')
    def test_ipv6_origin_is_bracketed(self):
        self.certificate('::1')
        self.assertEqual(manual.certificate_address(self.root,8443),'https://[::1]:8443')
    def test_missing_identity_uses_default_without_creating_files(self):
        self.assertEqual(manual.certificate_address(self.root,8443),'https://localhost:8443')
        self.assertEqual(list(self.root.iterdir()),[])
    def test_real_tls_reproduces_mismatch_and_corrected_runtime_starts(self):
        self.certificate('127.0.0.1')
        context,bundle=server._certificate(self.root,'https://localhost:8443')
        with self.assertRaises(ssl.SSLCertVerificationError): server.ServerRuntime._validate_certificate(context,bundle)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        runtime=manual.DiagnosticRuntime(self.root,port=port,host='127.0.0.1',admin_port=0)
        try:
            runtime.start()
            self.assertEqual(runtime.public_address,f'https://127.0.0.1:{port}')
            with socket.create_connection(('127.0.0.1',port),2): pass
        finally: runtime.stop()

if __name__=='__main__': unittest.main()
