import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_every_server_operation_is_callable_from_the_dashboard(self):
        import lifetime_server
        with tempfile.TemporaryDirectory() as root:
            client = AdminClient(root, 8450)
            for operation in lifetime_server._ADMIN:
                with self.assertRaises(FileNotFoundError, msg=operation):     # past the allow-list, at the token
                    client.call(operation)

    def test_every_server_operation_has_a_role_rule(self):
        # Default deny: an operation in no class is refused to everyone (Admin included) and fails here first.
        # test_server_access runs the full role x operation matrix over the same list.
        import lifetime_access as access
        import lifetime_server
        from lifetime_admin import OPERATIONS
        for operation in lifetime_server._ADMIN:
            self.assertEqual(sum(operation in group for group in (access.READ, access.AUDIT_READ, access.OPERATE,
                                                                  access.ADMINISTER)), 1, operation)
            self.assertIn(operation, access.PERMISSIONS['admin'], operation)
        self.assertEqual(OPERATIONS, set(lifetime_server._ADMIN))

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

    def test_a_refusal_reason_becomes_plain_words_and_only_known_codes_count(self):
        import lifetime_i18n
        from lifetime_admin import REFUSALS, Refused
        with tempfile.TemporaryDirectory() as root, patch('lifetime_admin.http.client.HTTPConnection') as factory, \
                patch('sys.platform', 'other'):       # the token channel only
            (Path(root) / 'admin-token').write_text('a' * 43, encoding='ascii')
            response = factory.return_value.getresponse.return_value
            response.status = 400
            response.read.return_value = b'{"error":"Request failed","reason":"power-consent"}'
            with self.assertRaises(Refused) as caught:
                AdminClient(root, 8450).call('send_command', device_id='x', action='sleep')
            self.assertEqual(caught.exception.detail_code, 'power-consent')
            self.assertIn('Turn on power control on that computer first.', str(caught.exception))
            for body in (b'{"reason":"<script>"}', b'{"reason":["power-consent"]}', b'not json', b'[1]'):
                response.read.return_value = body
                with self.assertRaises(ValueError) as caught:
                    AdminClient(root, 8450).call('send_command', device_id='x', action='sleep')
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), 'Administrative request failed (HTTP 400)')
        for text in REFUSALS.values():          # every reason has its Vietnamese words
            self.assertIn(text, lifetime_i18n.VI)

    def test_the_worker_bridge_passes_a_fixed_code_but_never_the_message(self):
        from lifetime_tasks import _Task
        from lifetime_admin import _failed
        errors = []
        for failure in (_failed(400, 'power-consent'), ValueError('secret text'), type('E', (Exception,), {'detail_code': 'Bad Code!'})()):
            def boom(failure=failure):
                raise failure
            task = _Task(1, boom)
            task.signals.finished.connect(lambda number, result, error: errors.append(error))
            task.run()
        self.assertEqual(errors, ['Refused:power-consent', 'ValueError', 'E'])


if __name__ == '__main__':
    unittest.main()
