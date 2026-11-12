import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from gaze_core.reporting.rejected import save_rejected


class RejectedResponseTests(unittest.TestCase):
    def save(self, raw, provider='chat', *, key='custom-test-key-never-write', body='{}'):
        with tempfile.TemporaryDirectory() as directory:
            artifact = SimpleNamespace(directory=Path(directory))
            path = save_rejected(artifact, raw, provider, key, prepared_body=body)
            self.assertEqual(path.name, 'ai-rejected.json')
            self.assertEqual(list(artifact.directory.iterdir()), [path])
            return json.loads(path.read_text(encoding='utf-8'))

    def test_keeps_content_and_numeric_usage_only(self):
        output = {
            'summary': {'text': '区域 3 中观察到 99%。', 'evidence': []},
            'unexpected': 4,
        }
        raw = {
            'choices': [{
                'message': {'content': json.dumps(output), 'reasoning_content': '未公开推理'},
                'finish_reason': 'stop',
            }],
            'usage': {
                'prompt_tokens': 42,
                'completion_tokens': 17,
                'prompt_cache_hit_tokens': 3,
                'prompt_tokens_details': {'cached_tokens': 3, 'headers': '秘密'},
                'total_tokens': True,
                'extra': '秘密',
            },
            'headers': {'Authorization': 'Bearer test-envelope-credential'},
        }
        self.assertEqual(self.save(raw), {
            'output': output,
            'usage': {
                'prompt_tokens': 42, 'completion_tokens': 17,
                'prompt_cache_hit_tokens': 3, 'prompt_tokens_details': {'cached_tokens': 3},
            },
        })

    def test_scrubs_keys_header_fields_and_echoed_input(self):
        secret = 'custom-test-key-never-write'
        input_text = '{"scenes":[{"label":"私有输入片段"}]}'
        image = 'data:image/png;base64,AAECAwQF'
        body = json.dumps({
            'messages': [
                {'role': 'system', 'content': '私有系统提示'},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': input_text},
                    {'type': 'image_url', 'image_url': {'url': image}},
                ]},
            ],
        }, ensure_ascii=False)
        output = {
            'summary': {'text': '正常段落 '+secret+' sk-another-test-secret'},
            secret: '密钥出现在字段名',
            'request_body': body,
            'nested': [
                {'Authorization': 'a credential', 'Content-Type': 'application/json'},
                {'request-headers': {'X-Another': 'header value'}},
                input_text, json.loads(input_text), body, json.loads(body), '私有系统提示', image, 'AAECAwQF',
                'Bearer another-test-secret', 'Cookie: session=header-value',
            ],
        }
        saved = self.save({'choices': [{'message': {'content': json.dumps(output)}}]}, body=body)
        serialized = json.dumps(saved, ensure_ascii=False)
        for term in [secret, 'sk-another-test-secret', 'another-test-secret', 'request_body', 'Authorization',
                     '私有输入片段', '私有系统提示', 'AAECAwQF', 'header value', 'header-value']:
            self.assertNotIn(term, serialized)
        self.assertIn('正常段落', saved['output']['summary']['text'])

    def test_responses_output_and_malformed_json(self):
        raw = {
            'output': [
                {'type': 'reasoning', 'content': [{'type': 'output_text', 'text': '私有推理'}]},
                {'type': 'message', 'content': [
                    {'type': 'output_text', 'text': '{"summary":'},
                    {'type': 'output_text', 'text': ' "中文"'},
                ]},
            ],
            'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12},
        }
        self.assertEqual(self.save(raw, 'responses'), {
            'output': '{"summary": "中文"',
            'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12},
        })
        raw['output'] = [None, {'type': 'message', 'content': '不合规'}, {'type': 'message', 'content': [17]}]
        self.assertIsNone(self.save(raw, 'responses')['output'])

    def test_unparseable_header_dump_is_replaced(self):
        raw = {'choices': [{'message': {'content': '{headers missing quotation: Authorization: test-value'}}]}
        self.assertEqual(self.save(raw)['output'], '[已脱敏]')
