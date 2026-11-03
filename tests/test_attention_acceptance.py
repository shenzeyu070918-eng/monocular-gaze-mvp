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

class GuideRevisionTests(unittest.TestCase):
    def test_action_timing_does_not_change_preregistered_targets(self):
        from gaze_core.attention.guide import phase_at
        self.assertEqual([phase_at(t)['phase'] for t in (16,20,24,28,32)],['scroll','tab','app','exclude','pause'])
        self.assertEqual(phase_at(35.99),dict(kind='phase',phase='pause'));self.assertEqual(phase_at(36),dict(kind='end'))
        self.assertEqual(phase_at(15.99),dict(kind='target',id=8));self.assertEqual(HARD_THRESHOLD,.6)
    def test_notification_gate_and_temporary_exclusion_copy(self):
        from gaze_core.attention.guide import foreground_block,temporary_exclusions,native_action
        original=('1Password','微信');values,added=temporary_exclusions(original,'备忘录','com.apple.Notes')
        self.assertEqual(original,('1Password','微信'));self.assertIn('com.apple.Notes',added)
        self.assertTrue(foreground_block(dict(app_name='UserNotificationCenter',bundle_id='com.apple.UserNotificationCenter')))
        self.assertIsNone(foreground_block(dict(app_name='Chrome',bundle_id='com.google.Chrome')))
        self.assertTrue(native_action('exclude',dict(pid=3,app_name='Notes',bundle_id='com.apple.Notes'),2,values))
        self.assertFalse(native_action('app',dict(pid=3,app_name='Notes',bundle_id='com.apple.Notes'),2,values))
    def test_no_action_prompts_are_not_execution_evidence(self):
        from gaze_core.attention.acceptance import result_text
        events=[dict(type='acceptance',kind='guide_protocol',time_ns=0,version=2,action_s=4)]
        for i,phase in enumerate(('scroll','tab','app','exclude','pause')):events.append(dict(type='acceptance',kind='phase',phase=phase,time_ns=(16+i*4)*10**9))
        for i,name in enumerate(('scroll','tab','app')):events.append(dict(type='scene',scene_id=str(i),start_ns=(17+i*4)*10**9,dynamic=False,app_name='Finder' if name=='app' else 'Chrome'))
        result=score([],events,[1512,982]);self.assertFalse(result['transition_pass']);self.assertEqual(len(result['steps_not_executed']),5)
        text=result_text(result);self.assertIn('未执行/未观测',text);self.assertIn('自窗口零残留：未验证',text)
    def test_confirmed_actions_and_pause_resume_evidence(self):
        events=[dict(type='acceptance',kind='guide_protocol',time_ns=0,version=2,action_s=4)]
        events.append(dict(type='scene',scene_id='initial',start_ns=0,dynamic=False,app_name='Chrome'))
        for i,phase in enumerate(('scroll','tab','app','exclude','pause')):
            t=(16+i*4)*10**9;events.extend([dict(type='acceptance',kind='phase',phase=phase,time_ns=t),dict(type='acceptance',kind='action',phase=phase,time_ns=t+10**9)])
            if i<3:events.append(dict(type='scene',scene_id=str(i),start_ns=t+10**9,dynamic=False,app_name='Finder' if phase=='app' else 'Chrome'))
        events.extend([dict(type='state',status='excluded_app',time_ns=29*10**9),dict(type='state',status='user_paused',time_ns=33*10**9),dict(type='state',status='recording',time_ns=34*10**9)])
        result=score([],events,[1512,982]);self.assertTrue(result['transition_pass']);self.assertEqual(result['steps_not_executed'],[])
    def test_scheduled_guide_waits_for_popup_and_runs_without_browser_timer(self):
        from overlay.attention_guide import AttentionGuide
        from threading import RLock
        # No socket, native camera or wall-clock sleeps: exercise scheduler.
        guide=AttentionGuide.__new__(AttentionGuide)
        guide.armed=True;guide.finished=False;guide.started_ns=None;guide.browser_pid=None;guide.primary_seen_ns=10**9
        guide._wait_reason='';guide.action_s=4;guide.duration_s=36;guide._stage=None;guide._hold=None;guide.secondary_since=None;guide.recording_cpu=None
        guide._observed=set();guide.excluded_app='备忘录';guide.pause_key='Ctrl+Alt+S';guide.excluded=('com.apple.Notes',);guide.exclude_target=guide.excluded
        events=[]
        from types import SimpleNamespace as NS
        guide.message=NS(emit=events.append)
        front=dict(pid=1,app_name='UserNotificationCenter',bundle_id='com.apple.UserNotificationCenter');guide.foreground=lambda:front
        guide.tick(10**9);self.assertIsNone(guide.started_ns)
        front.update(pid=2,app_name='Chrome',bundle_id='com.google.Chrome')
        with patch('overlay.attention_guide.cpu_snapshot',return_value=dict(time_ns=10**9,tree_cpu_s=0)):
            guide.tick(1100000000);self.assertEqual(guide.started_ns,1100000000)
            # Native timer keeps issuing phases/end even without browser pings.
            guide.tick(guide.started_ns+20*10**9);self.assertEqual(events[-2]['phase'],'tab')
            guide.tick(guide.started_ns+36*10**9);self.assertTrue(guide.finished);self.assertEqual(events[-1]['kind'],'end')
    def test_secondary_visible_must_be_foreground_and_held(self):
        from overlay.attention_guide import AttentionGuide
        from types import SimpleNamespace as NS
        guide=AttentionGuide.__new__(AttentionGuide);guide.started_ns=0;guide.finished=False;guide.action_s=4;guide.browser_pid=2
        guide._observed=set();guide.secondary_since=None;guide.primary_seen_ns=0
        front=dict(pid=9);guide.foreground=lambda:front;events=[];guide.message=NS(emit=events.append)
        guide.observe_browser(dict(page='secondary',visible=True),20*10**9);self.assertFalse(events)
        front['pid']=2
        for t in (20,21,21.6):guide.observe_browser(dict(page='secondary',visible=True),int(t*1e9))
        self.assertEqual(events[0]['phase'],'tab');self.assertEqual(len(events),1)

class WindowEvidenceTests(unittest.TestCase):
    def test_every_component_visible_and_zero_residue_required(self):
        import numpy as np
        from gaze_core.attention.window_check import compare_fixture,COMPONENTS
        expected=np.full((20,100,3),32,dtype=np.uint8);included=expected.copy()
        regions={n:(i*12,0,10,10) for i,n in enumerate(COMPONENTS)}
        for x,y,w,h in regions.values():included[y:y+h,x:x+w]=[255,0,255]
        result=compare_fixture(included,expected,expected,regions);self.assertTrue(result['passed'])
        residue=expected.copy();residue[0,0]=[255,0,255];self.assertFalse(compare_fixture(included,residue,expected,regions)['passed'])
        self.assertFalse(compare_fixture(expected,expected,expected,regions)['passed'])
        quantized=expected.copy();quantized[0,0]+=2;self.assertTrue(compare_fixture(included,quantized,expected,regions)['passed'])
    def test_bare_or_wrong_geometry_success_is_not_evidence(self):
        import tempfile
        from pathlib import Path
        from gaze_core.attention.window_check import load_evidence,COMPONENTS
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'latest.json';p.write_text('{"passed":true}')
            self.assertIsNone(load_evidence(p,ScreenRect(0,0,1512,982)))
            phases=[dict(phase=name,residue_pixels=0,channel_tolerance=3,all_visible=True,visible_pixels={n:100 for n in COMPONENTS}) for name in ('initial','post_new_window')]
            p.write_text(json.dumps(dict(schema_version=1,passed=True,screen_size=[1512,982],component_names=list(COMPONENTS),completed_at='test',phases=phases)))
            self.assertTrue(load_evidence(p,ScreenRect(0,0,1512,982))['passed'])
            self.assertIsNone(load_evidence(p,ScreenRect(0,0,1200,900)))

class GuideActionEvidenceTests(unittest.TestCase):
    def _guide(self):
        from overlay.attention_guide import AttentionGuide
        from types import SimpleNamespace as NS
        g=AttentionGuide.__new__(AttentionGuide);g.started_ns=0;g.finished=False;g.action_s=4;g.browser_pid=2
        g._observed=set();g.secondary_since=None;g.primary_seen_ns=0;g._primary_scroll_fraction=0.;g._scroll_start_fraction=.2
        g.foreground=lambda:dict(pid=2,app_name='Chrome',bundle_id='com.google.Chrome');g.events=[];g.message=NS(emit=g.events.append)
        return g
    def test_scroll_requires_half_screen_during_its_phase(self):
        g=self._guide()
        g.observe_browser(dict(page='primary',visible=True,scroll_fraction=.69),17*10**9);self.assertEqual(g.events,[])
        g.observe_browser(dict(page='primary',visible=True,scroll_fraction=.71),18*10**9);self.assertEqual(g.events[0]['phase'],'scroll')
        g.observe_browser(dict(page='primary',visible=True,scroll_fraction=1.),19*10**9);self.assertEqual(len(g.events),1)
    def test_secondary_hidden_resets_hold_without_false_completion(self):
        g=self._guide()
        g.observe_browser(dict(page='secondary',visible=True),20*10**9)
        g.observe_browser(dict(page='secondary',visible=False),21*10**9)
        g.observe_browser(dict(page='secondary',visible=True),22*10**9)
        g.observe_browser(dict(page='secondary',visible=True),23*10**9);self.assertEqual(g.events,[])
    def test_exclude_prompt_without_real_exclusion_state_is_incomplete(self):
        events=[dict(type='acceptance',kind='guide_protocol',time_ns=0,version=2,action_s=4),dict(type='acceptance',kind='phase',phase='exclude',time_ns=28*10**9),dict(type='acceptance',kind='action',phase='exclude',time_ns=29*10**9)]
        self.assertIn('exclude',score([],events,[1512,982])['steps_not_executed'])
    def test_preparation_dialog_is_not_the_scene_reference_browser(self):
        events=[dict(type='acceptance',kind='guide_protocol',time_ns=2*10**9,version=2,action_s=4,browser_app='Chrome'),dict(type='scene',scene_id='dialog',start_ns=0,dynamic=False,app_name='UserNotificationCenter')]
        for i,phase in enumerate(('scroll','tab','app')):
            t=(16+i*4)*10**9
            events.extend([dict(type='acceptance',kind='phase',phase=phase,time_ns=t),dict(type='acceptance',kind='action',phase=phase,time_ns=t+10**9),dict(type='scene',scene_id=str(i),start_ns=t+10**9,dynamic=False,app_name='Finder' if phase=='app' else 'Chrome')])
        result=score([],events,[1512,982]);self.assertEqual(result['detected_transitions'],3);self.assertTrue(result['transition_pass'])
