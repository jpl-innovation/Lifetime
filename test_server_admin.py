import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lifetime_admin import AdminClient


class AdminClientTests(unittest.TestCase):
    def test_explicit_loopback_and_token(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'admin-token').write_text('a' * 43, encoding='ascii')
            with patch('lifetime_admin.http.client.HTTPConnection') as factory:
                connection = factory.return_value
                response = connection.getresponse.return_value
                response.status = 200
                response.read.return_value = b'{"devices":[]}'
                client = AdminClient(root, 8450)
                self.assertEqual(client.call('devices'), {'devices': []})
                factory.assert_called_once_with('127.0.0.1', 8450, timeout=10)
                self.assertEqual(connection.request.call_args.args[1], '/v1/devices')
                self.assertEqual(connection.request.call_args.args[3]['Authorization'], 'Bearer ' + 'a' * 43)
                connection.close.assert_called_once()

    def test_no_implicit_network_and_invalid_token(self):
        with tempfile.TemporaryDirectory() as root, patch('lifetime_admin.http.client.HTTPConnection') as factory:
            client = AdminClient(root, 8450)
            factory.assert_not_called()
            with self.assertRaises(FileNotFoundError):
                client.call('devices')
            (Path(root) / 'admin-token').write_text('short', encoding='ascii')
            with self.assertRaises(ValueError):
                client.call('devices')
            with self.assertRaises(ValueError):
                client.call('restart')
            factory.assert_not_called()

    def test_errors_do_not_expose_response(self):
        with tempfile.TemporaryDirectory() as root, patch('lifetime_admin.http.client.HTTPConnection') as factory:
            (Path(root) / 'admin-token').write_text('a' * 43, encoding='ascii')
            response = factory.return_value.getresponse.return_value
            response.status = 403
            response.read.return_value = b'secret'
            with self.assertRaisesRegex(ValueError, 'HTTP 403') as caught:
                AdminClient(root, 8450).call('devices')
            self.assertNotIn('secret', str(caught.exception))
            response.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
