import io
import json
import socket
import ssl
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from gaze_core.reporting.ai import (
    ConsentGate, MissingCredentials, ProviderFailure, _http_child,
    interpret, prepare, request_json,
)
from gaze_core.reporting.build import build
from gaze_core.reporting.data import load_session
from gaze_core.reporting.settings import ReportSettings
from report_fixtures import session
from tools import check_ai_provider

KEY = 'sk-SENTINEL-diagnostics-never-output'
URL = 'https://api.deepseek.com/chat/completions'


class ProviderDiagnosticTests(unittest.TestCase):
    def test_read_only_probe_and_safe_console_output(self):
        request = Mock(side_effect=[
            (200, {'data': [{'id': 'deepseek-flash'}, {'id': KEY}]}, 'certifi'),
            (200, {'is_available': True, 'balance_infos': [KEY]}, 'system'),
        ])
        options = ReportSettings()
        results = check_ai_provider.check(options, request=request, key_reader=lambda _: KEY)
        self.assertEqual([c.args[0] for c in request.call_args_list], [
            'https://api.deepseek.com/models', 'https://api.deepseek.com/user/balance',
        ])
        self.assertTrue(all(not c.kwargs for c in request.call_args_list))
        self.assertEqual(results['models']['tls'], '正常（certifi）')
        self.assertTrue(results['models']['value'])
        self.assertTrue(results['balance']['value'])
        self.assertNotIn(KEY, json.dumps(results))
        with patch.object(check_ai_provider, 'check', return_value=results), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(check_ai_provider.main([]), 0)
        self.assertIn('HTTP 200', out.getvalue())
        self.assertNotIn(KEY, out.getvalue())

    def test_get_and_tls_fallback_before_sending(self):
        first, second = Mock(), Mock()
        first.connect.side_effect = ssl.SSLCertVerificationError('invalid trust chain')
        second.getresponse.return_value.status = 200
        second.getresponse.return_value.read1.side_effect = [b'{"data":[]}', b'']
        with patch('gaze_core.reporting.ai.http.client.HTTPSConnection', side_effect=[first, second]) as factory:
            status, data, ca = request_json('https://api.deepseek.com/models', KEY, 10, lambda: 0)
        self.assertEqual((status, data, ca), (200, {'data': []}, 'certifi'))
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
        self.assertEqual(factory.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
        first.request.assert_not_called()
        self.assertEqual(second.request.call_args.args, ('GET', '/models'))
        self.assertIsNone(second.request.call_args.kwargs['body'])

    def test_child_only_transmits_safe_category_and_status(self):
        for raised, expected in [
            (ssl.SSLError(KEY), ('error', ('tls', None, ''))),
            (socket.gaierror(KEY), ('error', ('dns', None, ''))),
            (ConnectionError(KEY), ('error', ('network', None, ''))),
            (TimeoutError(KEY), ('timeout', None)),
        ]:
            with self.subTest(expected=expected):
                connection, pipe = Mock(), Mock()
                connection.connect.side_effect = raised
                with patch('gaze_core.reporting.ai.http.client.HTTPSConnection', return_value=connection):
                    _http_child(pipe, URL, '{}', KEY, float('inf'))
                pipe.send.assert_called_once_with(expected)
                connection.request.assert_not_called()
        connection, pipe = Mock(), Mock()
        connection.getresponse.return_value.status = 402
        with patch('gaze_core.reporting.ai.http.client.HTTPSConnection', return_value=connection):
            _http_child(pipe, URL, '{}', KEY, float('inf'))
        pipe.send.assert_called_once_with(('error', ('http', 402, '')))

    def test_classified_failures_reach_report_without_echoes(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = build(load_session(session(root)))
            prepared = prepare(artifact, ['a'])
            gate = ConsentGate()
            cases = [(401, '密钥无效'), (402, '余额不足'), (400, 'Invalid max_tokens'),
                     (429, '请求受限'), (503, '服务端错误')]
            for status, expected in cases:
                with self.subTest(status=status):
                    connection = Mock()
                    response = connection.getresponse.return_value
                    response.status = status
                    response.read.return_value = json.dumps({'error': {'message': 'Invalid max_tokens '+KEY+' 合成标题区'}}).encode()
                    def send(p, key, deadline, clock):
                        return request_json(p.endpoint, key, deadline, clock, body=p.body)[1]
                    with patch('gaze_core.reporting.ai.http.client.HTTPSConnection', return_value=connection):
                        result = interpret(artifact, prepared, gate.approve(prepared), gate, transport=send, key_reader=lambda _: KEY)
                    self.assertIn(f'HTTP {status}', result['reason'])
                    self.assertIn(expected, result['reason'])
                    self.assertIn(expected, artifact.html.read_text())
                    self.assertNotIn(KEY, artifact.html.read_text())
                    self.assertNotIn('合成标题区', result['reason'])
            for reader, expected in [(Mock(side_effect=MissingCredentials()), '未从环境变量'), (lambda _: 'short', '格式异常')]:
                send = Mock()
                result = interpret(artifact, prepared, gate.approve(prepared), gate, transport=send, key_reader=reader)
                self.assertIn(expected, result['reason'])
                self.assertIn(expected, artifact.html.read_text())
                send.assert_not_called()
