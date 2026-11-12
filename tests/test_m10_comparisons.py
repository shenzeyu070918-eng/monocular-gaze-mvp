import json
from pathlib import Path
import unittest

from gaze_core.reporting.ai import _wrong_comparison, validate_output


class ComparisonTests(unittest.TestCase):
    def test_real_reply_only_loses_wrong_recommendation(self):
        fixture=Path(__file__).with_name('fixtures')/'m10_comparison_direction.json'
        record=json.loads(fixture.read_text())
        result=validate_output(record['output'],record['metrics'],aois=record['aois'])
        self.assertIsNone(result['summary_rejection'])
        self.assertEqual(result['summary'],record['output']['summary'])
        self.assertEqual(result['findings'],record['output']['findings'])
        self.assertEqual(result['recommendations'],record['output']['recommendations'][1:])
        self.assertEqual(result['removed'],[
            dict(section='recommendations',index=0,reason='比较方向与统计不符'),
        ])

    def test_direct_comparisons_use_strict_direction(self):
        for word in ('高于','多于','大于','长于','晚于','超过','低于','少于','小于','短于','早于','不及'):
            greater=word in ('高于','多于','大于','长于','晚于','超过')
            for a,b in ((3,1),(1,3),(2,2)):
                evidence={'a':dict(unit='s',value=a),'b':dict(unit='s',value=b)}
                with self.subTest(word=word,a=a,b=b):
                    self.assertEqual(_wrong_comparison('{{a}}，'+word+'{{aoi:other}}的{{b}}。',evidence),
                                     not (a>b if greater else a<b))

    def test_bi_forms_and_unrelated_values(self):
        evidence={'a':dict(unit='s',value=.93),'b':dict(unit='s',value=.286),'c':dict(unit='count',value=3)}
        self.assertTrue(_wrong_comparison('首次为{{a}}比{{b}}更早。',evidence))
        self.assertTrue(_wrong_comparison('首次为{{a}}比{{aoi:other}}更早（{{b}}）。',evidence))
        self.assertFalse(_wrong_comparison('首次为{{a}}比{{b}}更晚。',evidence))
        self.assertTrue(_wrong_comparison('两处停留{{a}}比{{a}}更多。',evidence))
        self.assertFalse(_wrong_comparison('有{{a}}。晚于时的参考值为{{b}}。',evidence))
        self.assertFalse(_wrong_comparison('数值为{{a}}，高于{{c}}。',evidence))
        self.assertFalse(_wrong_comparison('数值为{{a}}，不早于{{b}}。',evidence))
