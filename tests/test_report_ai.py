from dataclasses import replace
import json,tempfile,unittest
from unittest.mock import Mock,patch
from pathlib import Path
from report_fixtures import session
from gaze_core.reporting.ai import prepare,ConsentGate,interpret,validate_output,registry,usage_cost,endpoint
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build
from gaze_core.reporting.settings import ReportSettings

KEY='sk-SENTINEL-secret-DoNotWrite-987654321'


def answer(p,*,title='区域停留证据'):
    metrics=json.loads(p.metrics_json);key=next(k for k in metrics if k.endswith('.valid_s'));known=metrics[key]
    ref=dict(metric_id=key,value=known['value'],unit=known['unit']);claim=dict(text='有效注视时长为{{'+key+'}}，建议提高入口对比度后再做对照。',evidence=[ref])
    return dict(summary=claim,findings=[dict(claim,title=title)],recommendations=[claim],limitations='停留不等于理解；只做块级分析，后续仍需对照验证。')


def response(p,value=None):
    text=json.dumps(value if value is not None else answer(p),ensure_ascii=False)
    if p.provider=='responses':return dict(output=[dict(type='message',content=[dict(type='output_text',text=text)])],usage=dict(input_tokens=1500,output_tokens=300,input_tokens_details=dict(cached_tokens=0)))
    return dict(choices=[dict(message=dict(content=text))],usage=dict(prompt_tokens=1500,completion_tokens=300,prompt_tokens_details=dict(cached_tokens=0)))


class AITests(unittest.TestCase):
    def artifact(self,root,**options):return build(load_session(session(root)),options=ReportSettings(**options))
    def test_no_confirmation_zero_dns_http_or_credentials(self):
        with tempfile.TemporaryDirectory() as root,patch('socket.getaddrinfo',side_effect=AssertionError('network forbidden')):
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate();send=Mock();key=Mock()
            result=interpret(artifact,p,None,gate,transport=send,key_reader=key)
            self.assertFalse(result['ok']);send.assert_not_called();key.assert_not_called()
            gate.revoke();self.assertIn('零发送',result['reason'])
    def test_success_single_use_chinese_evidence_and_secret_never_written(self):
        for provider in ('responses','chat'):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as root:
                artifact=self.artifact(root,provider=provider);p=prepare(artifact,['a']);gate=ConsentGate();ticket=gate.approve(p)
                value=answer(p,title='区域'+KEY+'证据');send=Mock(return_value=response(p,value))
                result=interpret(artifact,p,ticket,gate,transport=send,key_reader=lambda _:KEY)
                self.assertTrue(result['ok'],result);self.assertIn('computed_usd',result['result']['provenance']);self.assertEqual(send.call_count,1)
                again=interpret(artifact,p,ticket,gate,transport=send,key_reader=lambda _:KEY);self.assertFalse(again['ok']);self.assertEqual(send.call_count,1)
                for path in artifact.directory.iterdir():
                    content=path.read_text();self.assertNotIn(KEY,content);self.assertNotIn('Authorization',content)
                self.assertIn('AI中文',json.dumps(dict(msg='AI中文'),ensure_ascii=False))
    def test_changed_price_endpoint_model_body_or_content_requires_new_consent(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate();send=Mock()
            for options in [replace(artifact.options,model='changed'),replace(artifact.options,base_url='https://example.invalid/v1'),replace(artifact.options,input_usd_per_million=4)]:
                original=artifact.options;ticket=gate.approve(p);artifact.options=options
                result=interpret(artifact,p,ticket,gate,transport=send,key_reader=lambda _:KEY);self.assertFalse(result['ok']);artifact.options=original
            forged=replace(p,body=p.body+' ');result=interpret(artifact,forged,gate.approve(p),gate,transport=send,key_reader=lambda _:KEY);self.assertFalse(result['ok']);send.assert_not_called()
    def test_cancelled_scene_not_in_payload_and_ocr_default_off(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);data=artifact.data;import copy
            second=copy.deepcopy(data['scenes'][0]);second['id']='excluded';second['regions'][0]['text']='PRIVATE-OCR-UNSELECTED';second['stats']['regions'][0]['label']='PRIVATE-LABEL'
            data['scenes'].append(second);data['scenes'][0]['regions'][0]['text']='OPTIONAL-OCR'
            p=prepare(artifact,['a']);self.assertNotIn('PRIVATE',p.body);self.assertNotIn('OPTIONAL-OCR',p.body);self.assertNotIn('participant_id',p.body);self.assertEqual(len(p.thumbnails),1)
            self.assertIn('OPTIONAL-OCR',prepare(artifact,['a'],include_ocr=True).body)
            with self.assertRaises(ValueError):prepare(artifact,[])
    def test_wrong_numbers_units_keys_non_chinese_and_single_person_claims_reject(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);metrics=json.loads(p.metrics_json)
            for mutation in ('number','unit','key','extra','english','group','free_number'):
                value=answer(p)
                if mutation=='number':value['summary']['evidence'][0]['value']+=1
                elif mutation=='unit':value['summary']['evidence'][0]['unit']='cm'
                elif mutation=='key':value['summary']['evidence'][0]['metric_id']='scene.not-selected.valid_s'
                elif mutation=='extra':value['unexpected']=True
                elif mutation=='english':value['limitations']='English only'
                elif mutation=='group':value['limitations']='大多数用户都喜欢这个区域。'
                else:value['summary']['text']+='提升99%'
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_output(value,metrics)
    def test_failure_timeout_total_budget_and_no_late_html(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate();original=artifact.html.read_bytes();now=[0.]
            def transport(*args):now[0]=30.01;return response(p)
            result=interpret(artifact,p,gate.approve(p),gate,transport=transport,key_reader=lambda _:KEY,clock=lambda:now[0],started=0)
            self.assertFalse(result['ok']);self.assertIn('AI总预算超时',artifact.html.read_text());self.assertFalse((artifact.directory/'ai.json').exists())
            bad=Mock(side_effect=RuntimeError(KEY));result=interpret(artifact,p,gate.approve(p),gate,transport=bad,key_reader=lambda _:KEY)
            self.assertFalse(result['ok']);self.assertNotIn(KEY,str(result));self.assertEqual(bad.call_count,1)
            # Rendering and file flush are included, not just HTTP latency.
            import gaze_core.reporting.build as module
            real=module.render_html
            def slow(*args,**kwargs):value=real(*args,**kwargs);now[0]=31.;return value
            now[0]=0
            with patch.object(module,'render_html',side_effect=slow):
                result=interpret(artifact,p,gate.approve(p),gate,transport=lambda *a:response(p),key_reader=lambda _:KEY,clock=lambda:now[0],started=0)
            self.assertFalse(result['ok']);self.assertIn('统计报告',artifact.html.read_text());self.assertFalse((artifact.directory/'ai.json').exists())
    def test_unknown_usage_and_prices_not_zero(self):
        self.assertIsNone(usage_cost(dict(input_tokens=None,output_tokens=None,cached_tokens=None),dict(input=1,output=5,cached=.05)))
        self.assertIsNone(usage_cost(dict(input_tokens=100,output_tokens=10,cached_tokens=None),dict(input=1,output=5,cached=.05)))
        self.assertAlmostEqual(usage_cost(dict(input_tokens=100,output_tokens=10,cached_tokens=0),dict(input=1,output=5,cached=.05)),.00015)
        with self.assertRaises(ValueError):endpoint(ReportSettings(base_url='https://secret:password@example.com'))

    def test_missing_key_and_redirect_are_safe_fallbacks_without_retry(self):
        from gaze_core.reporting.ai import MissingCredentials,http_transport
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate();send=Mock()
            result=interpret(artifact,p,gate.approve(p),gate,transport=send,key_reader=Mock(side_effect=MissingCredentials()))
            self.assertIn('未配置AI密钥',result['reason']);send.assert_not_called()
            conn=Mock();conn.getresponse.return_value.status=302
            with patch('gaze_core.reporting.ai.http.client.HTTPSConnection',return_value=conn) as factory:
                with self.assertRaises(RuntimeError):http_transport(p,KEY,999,clock=lambda:0)
            factory.assert_called_once();conn.request.assert_called_once();conn.close.assert_called_once()
    def test_deadline_write_rollback(self):
        import gaze_core.reporting.build as module
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate();clock=[0.];atomic=module.atomic
            def delayed(path,raw):
                atomic(path,raw)
                if Path(path)==artifact.html and b'AI' in raw:clock[0]=31.
            with patch.object(module,'atomic',side_effect=delayed):
                result=interpret(artifact,p,gate.approve(p),gate,transport=lambda *a:response(p),key_reader=lambda _:KEY,clock=lambda:clock[0],started=0)
            self.assertFalse(result['ok']);self.assertFalse((artifact.directory/'ai.json').exists());self.assertIn('AI总预算超时',artifact.html.read_text());self.assertNotIn(KEY,artifact.html.read_text())

    def test_failed_retry_preserves_previous_success(self):
        import gaze_core.reporting.build as module
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a']);gate=ConsentGate()
            first=interpret(artifact,p,gate.approve(p),gate,transport=lambda *a:response(p),key_reader=lambda _:KEY)
            self.assertTrue(first['ok']);before_html=artifact.html.read_bytes();before_ai=(artifact.directory/'ai.json').read_bytes();now=[0.];atomic=module.atomic
            def expire(path,raw):
                atomic(path,raw)
                if Path(path)==artifact.html:now[0]=31.
            with patch.object(module,'atomic',side_effect=expire):
                result=interpret(artifact,p,gate.approve(p),gate,transport=lambda *a:response(p),key_reader=lambda _:KEY,clock=lambda:now[0],started=0)
            self.assertFalse(result['ok']);self.assertEqual(artifact.html.read_bytes(),before_html);self.assertEqual((artifact.directory/'ai.json').read_bytes(),before_ai)
