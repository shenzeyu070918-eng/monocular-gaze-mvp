import json
from pathlib import Path
import unittest

from gaze_core.reporting.ai import _selected_literals, registry, validate_output
from gaze_core.reporting.references import region_references


METRIC_ID = 'scene.a.left.hard_s'
METRICS = {
    METRIC_ID: {'value': 1.235, 'unit': 's', 'label': '区域 3 · 停留', 'decimal_places': 3},
    'sample.N_total': {'value': 1, 'unit': 'count', 'label': '本次人数', 'decimal_places': 0},
}
LITERALS = ('区域 3', '场景 2', '报告 2026.png', 'P07')


def output(*, summary=None, value=1.235, title='区域停留', limitations='仅供设计对照'):
    claim = {
        'text': '停留为{{' + METRIC_ID + '}}。',
        'evidence': [{'metric_id': METRIC_ID, 'value': value, 'unit': 's'}],
    }
    return {
        'summary': dict(claim,text=claim['text'] if summary is None else summary),
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
            with self.subTest(text=text):
                result=self.validate(output(summary=text+'，另有{{'+METRIC_ID+'}}。'))
                self.assertEqual(result['summary_rejection'],'AI包含无证据数字')

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
            'scenes': [{'id': 'a', 'stats': {'regions': [{'id':'left','label': '9.10s', 'number': 1}]}}],
        }
        literals = _selected_literals(data, ('a',))
        self.assertIn('{{aoi:left}}', validate_output(
            output(title='区域「9.10s」的证据'), METRICS, allowed_literals=literals, aois={'left':'9.10s'},
        )['findings'][0]['title'])
        result = validate_output(output(title='停留为9.10s'), METRICS, allowed_literals=literals)
        self.assertEqual(result['removed'][0]['reason'], 'AI包含无证据数字')

    def test_claim_still_requires_its_own_evidence_placeholder(self):
        for summary in ('区域 3 停留较久。', '区域 3 停留为{{sample.N_total}}。'):
            with self.subTest(summary=summary):
                self.assertEqual(self.validate(output(summary=summary))['summary_rejection'],'AI正文未引用给定证据')
        result=self.validate(output(title='证据{{sample.N_total}}'))
        self.assertEqual(result['removed'][0]['reason'],'AI占位符格式或位置无效')

    def test_evidence_uses_declared_decimal_precision(self):
        for value in (1.2345, 1.2354):
            with self.subTest(value=value):
                self.assertEqual(self.validate(output(value=value))['summary']['evidence'][0]['value'],1.235)
        for value in (1.2344, 1.2356, True, float('nan')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,'数字、单位或所选场景与统计不一致'):
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
        self.aois=dict(zip(('group-5','group-4'),self.literals))

    def validate(self):
        return validate_output(self.answer, self.metrics, aois=self.aois)

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
                    self.assertEqual(self.validate()['summary_rejection'],'单人数据禁止群体推断')
                else:
                    self.assertEqual(self.validate()['removed'][0]['reason'],'单人数据禁止群体推断')
                claim['text'] = previous
        self.answer['summary']['text'] += '不能代表群体偏好，但多数用户更关注这个区块。'
        self.assertEqual(self.validate()['summary_rejection'],'单人数据禁止群体推断')

    def test_legacy_limitations_are_ignored(self):
        expected=self.validate()['limitations']
        self.answer['limitations']={'text':'另有99%未记录。'}
        self.assertEqual(self.validate()['limitations'],expected)
        del self.answer['limitations']
        self.assertEqual(self.validate()['limitations'],expected)

    def test_third_real_reply_is_accepted_in_full(self):
        path = Path(__file__).with_name('fixtures') / 'm10_single_participant_count.json'
        fixture = json.loads(path.read_text())
        result = validate_output(fixture['output'], fixture['metrics'], aois=dict(zip(('group-5','group-4'),fixture['allowed_literals'])))
        self.assertEqual((len(result['findings']),len(result['recommendations']),result['removed']), (3,2,[]))
        self.assertIn('1 名参与者', result['limitations'])

    def test_fourth_real_reply_keeps_summary_and_removes_bare_historical_range(self):
        path=Path(__file__).with_name('fixtures')/'m10_region_prefix_collision.json'
        f=json.loads(path.read_text())
        result=validate_output(f['output'],f['metrics'],allowed_literals=f['allowed_literals'],aois=f['aois'])
        self.assertIsNone(result['summary_rejection'])
        self.assertIn('{{aoi:group-4}}',result['summary']['text'])
        self.assertEqual((len(result['findings']),len(result['recommendations'])),(2,3))
        self.assertEqual(result['removed'],[dict(section='findings',index=2,reason='AI包含无证据数字')])
        f['output']['findings'][2]['text']=f['output']['findings'][2]['text'].replace(
            '硬归属占比区间约66.7%–85.3%', '参考{{const.m9_hard_range}}')
        accepted=validate_output(f['output'],f['metrics'],allowed_literals=f['allowed_literals'],aois=f['aois'])
        self.assertFalse(accepted['removed'])
        self.assertIn('历史硬归属范围',accepted['references']['const.m9_hard_range'])

    def test_normalized_known_region_is_replaced_but_unlisted_numbers_still_fail(self):
        region='区域 2：产品规格/40…'
        value=output(summary='“区域　２:产品规格／４０...”停留{{'+METRIC_ID+'}}。',
                     title='{{aoi:left}} · {{const.precision_range}}')
        result=validate_output(value,METRICS,aois={'left':region},allowed_literals=('区域 2',))
        self.assertEqual(result['summary']['text'],'{{aoi:left}}停留{{'+METRIC_ID+'}}。')
        self.assertFalse(result['removed'])
        value['summary']['text']+=' 停留了５.３秒。'
        result=validate_output(value,METRICS,aois={'left':region})
        self.assertEqual(result['summary_rejection'],'AI包含无证据数字')
        self.assertIn('参与人数',result['summary']['text'])

    def test_placeholders_must_be_in_scope_and_cannot_replace_statistical_evidence(self):
        for title in ('{{aoi:other}}的发现','{{const.unknown}}的发现'):
            result=validate_output(output(title=title),METRICS,aois={'left':'产品图'})
            self.assertEqual(result['removed'][0]['reason'],'AI区域或固定说明引用无效')
        result=validate_output(output(summary='{{aoi:left}}样本量{{const.sample_n}}。'),
                               METRICS,aois={'left':'产品图'})
        self.assertEqual(result['summary_rejection'],'AI正文未引用给定证据')

    def test_repeated_region_id_is_scoped_by_scene(self):
        data={'scenes':[dict(id=i,stats={'regions':[dict(id='left',label=i)]}) for i in ('s1','s2')]}
        self.assertEqual(dict(region_references(data,('s1','s2')).values()),{'s1/left':'s1','s2/left':'s2'})
        self.assertEqual(dict(region_references(data,('s1',)).values()),{'left':'s1'})
