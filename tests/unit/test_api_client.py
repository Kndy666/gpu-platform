"""An interrupted response must preserve the key needed to retry a creation."""
from http.client import IncompleteRead
import importlib.util
import io
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('platform_api_client',
    Path(__file__).resolve().parents[2] / 'skills/gpu-platform/scripts/client.py')
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)


class APIClientTests(unittest.TestCase):
    def setUp(self):
        self.client = client_module.Client('http://example.test', 'test-token')
        self.client.opener = Mock()

    def test_truncated_success_keeps_the_original_creation_key(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b'{"data":', 200)
        self.client.opener.open.return_value = response
        with self.assertRaises(client_module.ClientError) as caught:
            self.client.request('POST', '/api/v1/tasks', {'purpose': 'development'})
        error = caught.exception.result
        self.assertEqual(error['error'], 'network_error')
        sent = self.client.opener.open.call_args.args[0]
        self.assertEqual(error['idempotency_key'], sent.get_header('Idempotency-key'))
        self.assertTrue(error['idempotency_key'])

    def test_truncated_error_keeps_http_status_and_retry_key(self):
        body = Mock()
        body.read.side_effect = IncompleteRead(b'{"error":', 200)
        self.client.opener.open.side_effect = HTTPError('http://example.test', 503, 'Unavailable', {}, body)
        with self.assertRaises(client_module.ClientError) as caught:
            self.client.request('POST', '/api/v1/tasks', {}, key='retry-this-task')
        self.assertEqual(caught.exception.result['http_status'], 503)
        self.assertEqual(caught.exception.result['idempotency_key'], 'retry-this-task')

    def test_nonobject_error_body_still_returns_http_status(self):
        self.client.opener.open.side_effect = HTTPError('http://example.test', 502, 'Bad Gateway', {}, io.BytesIO(b'[]'))
        with self.assertRaises(client_module.ClientError) as caught:
            self.client.request('GET', '/api/v1/me')
        self.assertEqual(caught.exception.result['http_status'], 502)

    def test_login_can_request_a_permanent_credential(self):
        with patch.dict(client_module.os.environ, {'GPU_PLATFORM_PASSWORD': 'test-only'}), \
             patch.object(client_module.Client, 'request', return_value={'data': {'expires_in': 0}}) as request, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(client_module.main(['--url','http://example.test','login', '--username', 'alice', '--expires-in', '0']), 0)
        self.assertEqual(request.call_args.args, ('POST', '/api/v1/auth/token',
            {'username': 'alice', 'password': 'test-only', 'expires_in': 0, 'name': ''}))

    def test_missing_site_address_does_not_send_a_request(self):
        output=io.StringIO()
        with patch.dict(client_module.os.environ,{},clear=True), \
             patch.object(client_module.Client,'request') as request,redirect_stderr(output):
            self.assertEqual(client_module.main(['request','GET','/api/v1/me']),1)
        request.assert_not_called()
        self.assertIn('url_missing',output.getvalue())
