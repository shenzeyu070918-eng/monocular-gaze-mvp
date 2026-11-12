import json
from pathlib import Path
import unittest

from gaze_core.reporting.ai import _selected_literals, registry, validate_output


METRIC_ID = 'scene.a.left.hard_s'
METRICS = {
    METRIC_ID: {'value': 1.235, 'unit': 's', 'label': '区域 3 · 停留', 'decimal_places': 3},
    'sample.N_total': {'value': 1, 'unit': 'count', 'label': '本次人数', 'decimal_places': 0},
}
LITERALS = ('区域 3', '场景 2', '报告 2026.png', 'P07')


def output(*, summary=None, value=1.235, title='区域停留', limitations='仅供设计对照'):
    claim = {
        'text': '停留为{{' + METRIC_ID + '}}。' if summary is None else summary,
        'evidence': [{'metric_id': METRIC_ID, 'value': value, 'unit': 's'}],
    }
    return {
        'summary': claim,
        'findings': [dict(claim, title=title)],
        'recommendations': [dict(claim)],
        'limitations': limitations,
    }


class ValidationLiteralTests(unittest.TestCase):
    def validate(self, value):
        return validate_output(value, METRICS, allowed_literals=LITERALS)

    def test_names_and_known_counts_can_keep_their_numbers(self):
        result = self.validate(output(
            summary='P07 在场景 2 的报告 2026.png 中，1名参与者在第3个区域停留为{{' + METRIC_ID + '}}。',
            title='区域 3 的证据',
            limitations='精度约 1.3–2.5 cm，仅供块级对照。',
        ))
        for label in LITERALS[1:]:
            self.assertIn(label, result['summary']['text'])
        self.assertEqual(result['findings'][0]['title'], '区域 3 的证据')
        self.assertIn('经验精度约1.3–2.5 cm', result['limitations'])
        self.assertIn('1 名参与者', result['limitations'])
        self.assertNotIn('仅供块级对照', result['limitations'])

    def test_identifier_prefix_does_not_exempt_a_different_number(self):
        for title in ('区域 30 的证据', '区域 3.5 的证据', '区域 3% 的证据'):
            with self.subTest(title=title):
                self.assertEqual(self.validate(output(title=title))['removed'][0]['reason'], 'AI包含无证据数字')

    def test_unreferenced_measurements_are_rejected_even_if_integer_is_known(self):
        for text in ('停留了5.3秒', '占比59%', '停留了1秒', '长度1 cm', '1千米', '等待1 minute', '比例.1', '停留为-1'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError,'无证据数字'):
                self.validate(output(summary=text+'，另有{{'+METRIC_ID+'}}。'))

    def test_invalid_items_are_removed_until_nothing_remains(self):
        value = output()
        value['findings'][0]['text'] += '停留了5.3秒。'
        result = self.validate(value)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['removed'], [dict(section='findings',index=0,reason='AI包含无证据数字')])
        value['recommendations'][0]['text'] += '占比59%。'
        with self.assertRaisesRegex(ValueError,'没有通过校验的发现或建议'):
            self.validate(value)

    def test_measurement_like_label_requires_named_context(self):
        data = {
            'limitation': '只做块级结论。',
            'scenes': [{'id': 'a', 'stats': {'regions': [{'label': '9.10s', 'number': 1}]}}],
        }
        literals = _selected_literals(data, ('a',))
        self.assertIn('区域「9.10s」', validate_output(
            output(title='区域「9.10s」的证据'), METRICS, allowed_literals=literals,
        )['findings'][0]['title'])
        result = validate_output(output(title='停留为9.10s'), METRICS, allowed_literals=literals)
        self.assertEqual(result['removed'][0]['reason'], 'AI包含无证据数字')

    def test_claim_still_requires_its_own_evidence_placeholder(self):
        for summary in ('区域 3 停留较久。', '区域 3 停留为{{sample.N_total}}。'):
            with self.subTest(summary=summary), self.assertRaisesRegex(ValueError, '正文未引用给定证据'):
                self.validate(output(summary=summary))
        result=self.validate(output(title='证据{{sample.N_total}}'))
        self.assertEqual(result['removed'][0]['reason'],'AI占位符格式或位置无效')

    def test_evidence_uses_declared_decimal_precision(self):
        for value in (1.2345, 1.2354):
            with self.subTest(value=value):
                self.assertEqual(self.validate(output(value=value))['summary']['evidence'][0]['value'],1.235)
        for value in (1.2344, 1.2356, True, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(output(value=value))

    def test_registry_declares_precision_by_unit(self):
        data = {
            'kind': 'session',
            'metadata': {'kernel': {'sigma_x': .1234567, 'sigma_y': .2345678}},
            'scenes': [{'id': 'a', 'stats': {
                'valid_fixation_s': 1.2354,
                'regions': [{'id': 'left', 'hard_fraction': .2345678, 'entries': 2}],
            }}],
        }
        metrics = registry(data, ('a',))
        expected = {
            'scene.a.valid_s': 3,
            'scene.a.left.hard_fraction': 6,
            'scene.a.left.entries': 0,
            'calibration.sigma_x': 6,
            'sample.N_total': 0,
        }
        self.assertEqual({key: metrics[key]['decimal_places'] for key in expected}, expected)


class SinglePersonDisclaimerTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name('fixtures') / 'm10_single_person_disclaimer.json'
        fixture = json.loads(path.read_text())
        self.answer = fixture['output']
        self.metrics = fixture['metrics']
        self.literals = fixture['allowed_literals']

    def validate(self):
        return validate_output(self.answer, self.metrics, allowed_literals=self.literals)

    def test_complete_real_reply_with_sanitized_aoi_names(self):
        result = self.validate()
        self.assertEqual(len(result['findings']), 3)
        self.assertEqual(len(result['recommendations']), 2)
        self.assertIn('单人数据不能用于推断群体规律', result['limitations'])
        self.assertEqual(result['summary']['evidence'][0]['value'],8.997)

    def test_negated_group_inference_in_claims_is_allowed(self):
        self.answer['summary']['text'] += '不能据此作群体结论，无法推广到多数用户，也不代表群体偏好。'
        self.answer['findings'][0]['title'] = '不应从本次会话推断群体偏好'
        self.validate()

    def test_group_assertions_are_rejected_in_all_claim_sections(self):
        for claim in (self.answer['summary'], self.answer['findings'][0], self.answer['recommendations'][0]):
            previous = claim['text']
            with self.subTest(claim=previous):
                claim['text'] = '本次多数用户更关注这个区块。' + previous
                if claim is self.answer['summary']:
                    with self.assertRaisesRegex(ValueError, '单人数据禁止群体推断'):
                        self.validate()
                else:
                    self.assertEqual(self.validate()['removed'][0]['reason'],'单人数据禁止群体推断')
                claim['text'] = previous
        self.answer['summary']['text'] += '不能代表群体偏好，但多数用户更关注这个区块。'
        with self.assertRaisesRegex(ValueError, '单人数据禁止群体推断'):
            self.validate()

    def test_legacy_limitations_are_ignored(self):
        expected=self.validate()['limitations']
        self.answer['limitations']={'text':'另有99%未记录。'}
        self.assertEqual(self.validate()['limitations'],expected)
        del self.answer['limitations']
        self.assertEqual(self.validate()['limitations'],expected)

    def test_third_real_reply_is_accepted_in_full(self):
        path = Path(__file__).with_name('fixtures') / 'm10_single_participant_count.json'
        fixture = json.loads(path.read_text())
        result = validate_output(fixture['output'], fixture['metrics'], allowed_literals=fixture['allowed_literals'])
        self.assertEqual((len(result['findings']),len(result['recommendations']),result['removed']), (3,2,[]))
        self.assertIn('1 名参与者', result['limitations'])
