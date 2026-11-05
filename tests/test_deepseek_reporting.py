from dataclasses import replace
from datetime import datetime,timezone
import json,tempfile,unittest
from unittest.mock import Mock,patch
from report_fixtures import session
from test_report_ai import answer
from gaze_core.config import load_settings
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build
from gaze_core.reporting.ai import prepare,interpret,ConsentGate,parse_response
from gaze_core.reporting.pricing import snapshot
from gaze_core.reporting.settings import ReportSettings

KEY='sk-deepseek-SENTINEL-do-not-persist'
NOW=datetime(2026,11,7,12,tzinfo=timezone.utc)


class DeepSeekTests(unittest.TestCase):
    def artifact(self,root,**options):return build(load_session(session(root)),options=ReportSettings(**options))
    def response(self,p):return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(answer(p),ensure_ascii=False),reasoning_content='discarded '+KEY))],usage=dict(prompt_tokens=1000,completion_tokens=100,prompt_cache_hit_tokens=100,prompt_cache_miss_tokens=900))
    def test_official_defaults_json_mode_and_disabled_thinking(self):
        settings=load_settings(use_local=False).report
        self.assertEqual((settings.provider,settings.base_url,settings.model,settings.key_env,settings.keychain_account),('chat','https://api.deepseek.com','deepseek-flash','DEEPSEEK_API_KEY','deepseek'))
        with tempfile.TemporaryDirectory() as root,patch('socket.getaddrinfo',side_effect=AssertionError('no network')):
            p=prepare(self.artifact(root),['a'],now_utc=NOW);body=json.loads(p.body)
            self.assertEqual(body['thinking'],{'type':'disabled'});self.assertEqual(body['response_format'],{'type':'json_object'});self.assertIn('Schema:',body['messages'][0]['content']);self.assertIn('json示例',body['messages'][0]['content'])
            self.assertNotIn('store',body);self.assertNotIn('max_completion_tokens',body);self.assertEqual(body['max_tokens'],3000);self.assertEqual(p.provider_name,'DeepSeek');self.assertFalse(p.text_only);self.assertEqual(len(p.thumbnails),1)
    def test_pure_text_has_only_stats_labels_no_image_or_full_ocr(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root,ai_text_only=True)
            with patch('gaze_core.reporting.ai.scene_image',side_effect=AssertionError('pure mode must not render an image')):p=prepare(artifact,['a'],include_ocr=True,now_utc=NOW)
            self.assertEqual(p.thumbnails,());self.assertTrue(p.text_only);self.assertFalse(p.include_ocr);self.assertNotIn('image_url',p.body);self.assertNotIn('data:image',p.body);self.assertNotIn('ocr_text',p.body)
            content=json.loads(json.loads(p.body)['messages'][1]['content']);self.assertIn('合成标题区',[r['label'] for r in content['scenes'][0]['regions']]);self.assertEqual(content['input_mode'],'text_only')
            unknown=replace(artifact.options,model='deepseek-v4-pro',ai_text_only=False);artifact.options=unknown;p=prepare(artifact,['a'],now_utc=NOW);self.assertTrue(p.text_only);self.assertIn('自动',p.mode_reason);self.assertEqual(json.loads(p.prices_json)['input'],.66)
    def test_mock_deepseek_usage_cost_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a'],now_utc=NOW);gate=ConsentGate();send=Mock(return_value=self.response(p))
            denied=interpret(artifact,p,None,gate,transport=send,key_reader=lambda _:KEY,utc_clock=lambda:NOW);self.assertFalse(denied['ok']);send.assert_not_called()
            result=interpret(artifact,p,gate.approve(p),gate,transport=send,key_reader=lambda _:KEY,utc_clock=lambda:NOW)
            self.assertTrue(result['ok'],result);prov=result['result']['provenance'];self.assertAlmostEqual(prov['computed_usd'],(900*.15+100*.003+100*.6)/1e6);self.assertEqual(prov['cost_kind'],'estimate');self.assertEqual(prov['images_sent'],1)
            for f in artifact.directory.iterdir():self.assertNotIn(KEY,f.read_text())
    def test_time_bands_weekend_and_uncertain_holidays(self):
        settings=ReportSettings()
        def at(day,hour,minute=0):return snapshot(settings,datetime(2026,11,day,hour,minute,tzinfo=timezone.utc))
        for hour,minute,period in [(0,59,'offpeak'),(1,0,'peak'),(3,59,'peak'),(4,0,'offpeak'),(5,59,'offpeak'),(6,0,'peak'),(9,59,'peak'),(10,0,'offpeak')]:self.assertEqual(at(5,hour,minute)['period'],period)
        self.assertEqual(at(7,2)['period'],'offpeak');self.assertEqual(at(8,7)['period'],'offpeak');self.assertTrue(at(5,2)['holiday_uncertain'])
        known=snapshot(replace(settings,price_holiday_years=(2026,)),datetime(2026,11,5,2,tzinfo=timezone.utc));self.assertFalse(known['holiday_uncertain']);self.assertEqual(known['input'],.3)
        # Synthetic holiday fixture, not a claim about the actual calendar.
        holiday=snapshot(replace(settings,price_holidays=('2026-11-05',)),datetime(2026,11,5,2,tzinfo=timezone.utc));self.assertEqual(holiday['period'],'offpeak');self.assertEqual(holiday['input'],.15)
    def test_cross_band_cost_range_and_change_before_send_reconfirmation(self):
        before=datetime(2026,11,5,5,59,59,tzinfo=timezone.utc);after=datetime(2026,11,5,6,0,2,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a'],now_utc=before);gate=ConsentGate();ticks=iter([before,before,after]);send=Mock(return_value=self.response(p))
            result=interpret(artifact,p,gate.approve(p),gate,transport=send,key_reader=lambda _:KEY,utc_clock=lambda:next(ticks))
            self.assertTrue(result['ok'],result);self.assertTrue(result['result']['provenance']['crossed_price_period']);low,high=result['result']['provenance']['estimated_usd_range'];self.assertLess(low,high)
            ticks=iter([before,after]);send.reset_mock();result=interpret(artifact,p,gate.approve(p),gate,transport=send,key_reader=lambda _:KEY,utc_clock=lambda:next(ticks));self.assertFalse(result['ok']);send.assert_not_called()
    def test_empty_json_truncation_and_inconsistent_usage_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=self.artifact(root);p=prepare(artifact,['a'],now_utc=NOW)
            for change in ('blank','length','cache'):
                raw=self.response(p)
                if change=='blank':raw['choices'][0]['message']['content']='   '
                elif change=='length':raw['choices'][0]['finish_reason']='length'
                else:raw['usage']['prompt_cache_miss_tokens']=901
                gate=ConsentGate();send=Mock(return_value=raw);result=interpret(artifact,p,gate.approve(p),gate,transport=send,key_reader=lambda _:KEY,utc_clock=lambda:NOW);self.assertFalse(result['ok']);self.assertEqual(send.call_count,1)
