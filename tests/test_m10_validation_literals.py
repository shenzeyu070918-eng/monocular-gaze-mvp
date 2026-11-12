import unittest

from gaze_core.reporting.ai import _selected_literals, registry, validate_output


METRIC_ID = 'scene.a.left.hard_s'
METRICS = {
    METRIC_ID: {'value': 1.235, 'unit': 's', 'label': '区域 3 · 停留', 'decimal_places': 3},
    'sample.N_total': {'value': 1, 'unit': 'count', 'label': '本次人数', 'decimal_places': 0},
}
LITERALS = ('区域 3', '场景 2', '报告 2026.png', 'P07', '精度约 1.3–2.5 cm')


def output(*, summary=None, value=1.235, title='区域停留', limitations='仅供设计对照'):
    claim = {
        'text': '停留为{{' + METRIC_ID + '}}。' if summary is None else summary,
        'evidence': [{'metric_id': METRIC_ID, 'value': value, 'unit': 's'}],
    }
    return {
        'summary': claim,
        'findings': [dict(claim, title=title)],
        'recommendations': [claim],
        'limitations': limitations,
    }


class ValidationLiteralTests(unittest.TestCase):
    def validate(self, value):
        return validate_output(value, METRICS, allowed_literals=LITERALS)

    def test_names_and_fixed_explanation_can_keep_their_numbers(self):
        result = self.validate(output(
            summary='P07 在场景 2 的报告 2026.png 中，区域 3 停留为{{' + METRIC_ID + '}}。',
            title='区域 3 的证据',
            limitations='精度约 1.3–2.5 cm，仅供块级对照。',
        ))
        for label in LITERALS[:4]:
            self.assertIn(label, result['summary'])
        self.assertEqual(result['findings'][0]['title'], '区域 3 的证据')
        self.assertIn('精度约 1.3–2.5 cm', result['limitations'])

    def test_identifier_prefix_does_not_exempt_a_different_number(self):
        for title in ('区域 30 的证据', '区域 3.5 的证据', '区域 3% 的证据'):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, '无证据数字'):
                self.validate(output(title=title))

    def test_fabricated_statistics_and_changed_fixed_explanation_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '无证据数字'):
            self.validate(output(summary='区域 3 提升了 99%，停留为{{' + METRIC_ID + '}}。'))
        with self.assertRaisesRegex(ValueError, '无证据数字'):
            self.validate(output(limitations='精度约 1.3–2.6 cm，仅供对照。'))

    def test_measurement_like_label_requires_named_context(self):
        data = {
            'limitation': '只做块级结论。',
            'scenes': [{'id': 'a', 'stats': {'regions': [{'label': '9.10s', 'number': 1}]}}],
        }
        literals = _selected_literals(data, ('a',))
        self.assertIn('区域「9.10s」', validate_output(
            output(title='区域「9.10s」的证据'), METRICS, allowed_literals=literals,
        )['findings'][0]['title'])
        with self.assertRaisesRegex(ValueError, '无证据数字'):
            validate_output(output(title='停留为9.10s'), METRICS, allowed_literals=literals)

    def test_claim_still_requires_its_own_evidence_placeholder(self):
        for summary in ('区域 3 停留较久。', '区域 3 停留为{{sample.N_total}}。'):
            with self.subTest(summary=summary), self.assertRaisesRegex(ValueError, '正文未引用给定证据'):
                self.validate(output(summary=summary))
        for field in ('title', 'limitations'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(output(**{field: '证据{{sample.N_total}}'}))

    def test_evidence_uses_declared_decimal_precision(self):
        for value in (1.2345, 1.2354):
            with self.subTest(value=value):
                self.assertIn('1.235', self.validate(output(value=value))['summary'])
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
