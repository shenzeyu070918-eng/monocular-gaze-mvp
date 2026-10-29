import json,time,unittest,urllib.request,urllib.error
from unittest.mock import patch
from overlay.mapping import ScreenRect
from gaze_core.attention.acceptance import layout,score,parse_cpu_time,cpu_difference,HARD_THRESHOLD

class AcceptanceTests(unittest.TestCase):
    def test_preregistered_geometry_and_threshold(self):
        rows=layout(ScreenRect(0,0,1512,982));self.assertEqual(len(rows),8);self.assertEqual(HARD_THRESHOLD,.60)
        for r in rows:
            a,b,c,d=r['rect'];self.assertAlmostEqual((c-a)*1511,684);self.assertAlmostEqual((d-b)*981,194)
    def test_correctness_uses_all_valid_time_not_only_hits(self):
        r=layout(None)[0];a,b,c,d=r['rect'];events=[dict(type='acceptance',kind='target',time_ns=0,rect=r['rect'])]
        frames=[dict(start_ns=500000000,end_ns=1000000000,center=[(a+c)/2,(b+d)/2],scene_id='a',excluded_reason=None),dict(start_ns=1000000000,end_ns=2000000000,center=[.99,.99],scene_id='a',excluded_reason=None)]
        result=score(frames,events,[1512,982]);self.assertAlmostEqual(result['hard_fraction'],1/3);self.assertFalse(result['hard_pass']);self.assertIsNone(result['overall_pass'])
    def test_cpu_units_and_missing_data_do_not_pass(self):
        self.assertAlmostEqual(parse_cpu_time('1-02:03:04.50'),93784.5)
        a=dict(time_ns=0,tree_cpu_s=1.,windowserver_cpu_s=None);b=dict(time_ns=10000000000,tree_cpu_s=2.,windowserver_cpu_s=None)
        result=cpu_difference(a,b);self.assertEqual(result['process_tree_percent_one_core'],10.);self.assertIsNone(result['windowserver_percent_one_core'])
        self.assertIsNone(score([],[],[1512,982])['cpu_pass'])

class GuideServerTests(unittest.TestCase):
    def test_loopback_token_host_whitelist_and_shutdown(self):
        from overlay.attention_guide import AttentionGuide
        with patch('overlay.attention_guide.cpu_snapshot',return_value=dict(time_ns=1,tree_cpu_s=0,windowserver_cpu_s=None)):
            guide=AttentionGuide(ScreenRect(0,0,1512,982,2))
            try:
                self.assertEqual(guide.server.server_address[0],'127.0.0.1')
                with urllib.request.urlopen(guide.origin+'/') as r:self.assertIn(b'30',r.read())
                for path in ('/clock','/session.json'):
                    with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(guide.origin+path)
                req=urllib.request.Request(guide.origin+'/clock',headers={'X-Attention-Token':guide.token})
                with urllib.request.urlopen(req) as r:self.assertIn('baseline_ready',json.load(r))
                bad=urllib.request.Request(guide.origin+'/event',data=b'{"kind":"frame","image":"secret"}',headers={'X-Attention-Token':guide.token,'Origin':guide.origin})
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bad)
                wrong=urllib.request.Request(guide.origin+'/',headers={'Host':'example.com'})
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(wrong)
            finally:guide.close()
            self.assertFalse(guide.thread.is_alive());self.assertFalse(guide.baseline_thread.is_alive())
