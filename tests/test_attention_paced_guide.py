"""Causal evidence and virtual-clock tests; no real camera or screenshots."""
import threading,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from gaze_core.attention.guide import ActionSteps
from gaze_core.attention.acceptance import score,result_text

S=10**9

def guide_harness():
    from overlay.attention_guide import AttentionGuide
    g=AttentionGuide.__new__(AttentionGuide)
    g.armed=True;g.finished=False;g.started_ns=0;g.browser_pid=2;g.requested=True
    g.primary_seen_ns=0;g.recording_cpu=None;g._last_instruction=None;g._wait_reason=''
    g.flow=ActionSteps();g._browser={};g._step_browser_revision=None;g._observed=set()
    g._cpu_before=None;g._cpu_revision=None;g._cpu_reason='';g._primary_scroll_fraction=0.
    g._session=dict(paused=False,recording=True,excluded=False);g._begin_lock=threading.RLock()
    g.excluded_app='备忘录';g.exclude_target=('备忘录','com.apple.Notes');g.excluded=g.exclude_target;g.pause_key='Ctrl+Alt+S'
    g.front=dict(pid=2,app_name='Chrome',bundle_id='com.google.Chrome');g.foreground=lambda:g.front
    g.events=[];g.message=NS(emit=g.events.append);return g


def browser(g,t,page='primary',revision=0,reason='heartbeat',visible=True,target=False,fullscreen=True):
    g.observe_browser(dict(page=page,revision=revision,reason=reason,visible=visible,target_visible=target,fullscreen=fullscreen,scroll_fraction=.6 if target else 0.),int(t*S))


def tick(g,t):
    now=int(t*S)
    with patch('overlay.attention_guide.cpu_snapshot',return_value=dict(time_ns=now,tree_cpu_s=t*.5)):
        g.tick(now)


class SelfPacedTests(unittest.TestCase):
    def test_no_timeout_and_explicit_skip(self):
        f=ActionSteps();f.start(0);f.drain();f.tick(3600*S)
        self.assertEqual(f.phase,'scroll');self.assertFalse(f.finished)
        self.assertTrue(f.skip(3601*S,0));self.assertFalse(f.skip(3601*S,0))
        self.assertEqual(f.feedback,'skipped');self.assertFalse(any(e['kind']=='action' for e in f.drain()))
        f.tick(3602*S);self.assertEqual(f.phase,'tab');self.assertFalse(f.skip(3603*S,0))
    def test_stability_resets_on_more_scroll_and_requires_full_hold(self):
        f=ActionSteps();f.start(0);f.drain()
        f.observe('scroll',10*S,True,1,'scroll');f.observe('scroll',11*S,True,2,'scroll')
        f.observe('scroll',int(12.49*S),True,2,'scroll');self.assertIsNone(f.feedback)
        f.observe('scroll',int(12.5*S),True,2,'scroll');self.assertEqual(f.feedback,'completed')
        action=next(e for e in f.drain() if e['kind']=='action')
        self.assertEqual(action['time_ns'],12.5*S);self.assertEqual(action['started_ns'],11*S)
    def test_pause_and_resume_each_use_actual_state_and_stability(self):
        f=ActionSteps()
        for i in range(4):
            if i==0:f.start(0)
            f.skip((i*2)*S,i);f.tick((i*2+1)*S)
        self.assertEqual(f.phase,'pause')
        f.pause_state(10*S,True,False);f.pause_state(11*S,False,True);self.assertFalse(f.resume_ready)
        f.pause_state(20*S,True,False);f.pause_state(int(21.5*S),True,False);self.assertTrue(f.resume_ready)
        f.pause_state(22*S,False,False);f.pause_state(24*S,False,False);self.assertIsNone(f.feedback)
        f.pause_state(30*S,False,True);f.pause_state(int(31.5*S),False,True);self.assertEqual(f.feedback,'completed')
    def test_cpu_finishes_before_unlimited_actions_and_never_uses_pause_time(self):
        g=guide_harness()
        for t in (0,16,30):browser(g,t);tick(g,t)
        measurement=next(e for e in g.events if e['kind']=='cpu_recording')['measurement']
        self.assertEqual(measurement['seconds'],30);self.assertTrue(measurement['comparison_valid'])
        self.assertEqual(g.flow.phase,'scroll')
        browser(g,600);tick(g,600);self.assertEqual(g.flow.phase,'scroll');self.assertFalse(g.finished)
        self.assertEqual(len([e for e in g.events if e['kind']=='cpu_recording']),1)
        g.update_session_state(True,False);browser(g,900);tick(g,900)
        self.assertIs(g.recording_cpu.get('seconds'),measurement['seconds'])
    def test_cpu_interrupt_restarts_full_window_without_advancing_actions(self):
        g=guide_harness();browser(g,0);tick(g,0)
        g.front=dict(pid=5,app_name='Finder',bundle_id='com.apple.finder');tick(g,10)
        self.assertIsNone(g._cpu_before);self.assertEqual(g.flow.index,-1)
        g.front=dict(pid=2,app_name='Chrome',bundle_id='com.google.Chrome')
        browser(g,11);tick(g,11);browser(g,30);tick(g,30);self.assertIsNone(g.recording_cpu)
        browser(g,41);tick(g,41);self.assertEqual(g.recording_cpu['seconds'],30);self.assertEqual(g.flow.phase,'scroll')
    def test_scroll_event_goal_stable_and_not_a_heartbeat_inference(self):
        g=guide_harness();g.recording_cpu=dict(seconds=30);g.flow.start(30*S);g._emit_steps()
        browser(g,31,target=True);tick(g,31);browser(g,35,target=True);tick(g,35)
        self.assertIsNone(g.flow.feedback)
        browser(g,40,revision=1,reason='scroll',target=True);tick(g,40)
        browser(g,41.4,revision=1,target=True);tick(g,41.4);self.assertIsNone(g.flow.feedback)
        browser(g,41.5,revision=1,target=True);tick(g,41.5);self.assertEqual(g.flow.feedback,'completed')
    def test_visibilitychange_required_and_background_tab_never_qualifies(self):
        g=guide_harness();g.recording_cpu=dict(seconds=30);g.flow.start(0);g.flow.skip(0,0);g.flow.tick(S);g._emit_steps()
        browser(g,5,page='secondary');tick(g,5);browser(g,8,page='secondary');tick(g,8);self.assertIsNone(g.flow.feedback)
        g.front=dict(pid=3,app_name='Finder',bundle_id='com.apple.finder')
        browser(g,10,page='secondary',revision=1,reason='visibilitychange');tick(g,10)
        browser(g,12,page='secondary',revision=1);tick(g,12);self.assertIsNone(g.flow.feedback)
        g.front=dict(pid=2,app_name='Chrome',bundle_id='com.google.Chrome')
        browser(g,20,page='secondary',revision=1);tick(g,20);browser(g,21.5,page='secondary',revision=1);tick(g,21.5)
        self.assertEqual(g.flow.feedback,'completed')
    def test_out_of_order_browser_heartbeat_cannot_erase_event(self):
        g=guide_harness();browser(g,1,revision=2,reason='scroll',target=True);browser(g,2,revision=1)
        self.assertEqual(g._browser['primary']['change_reason'],'scroll');self.assertEqual(g._browser['primary']['revision'],2)
    def test_system_popup_waits_before_target_clock(self):
        g=guide_harness();g.started_ns=None;browser(g,1)
        g.front=dict(pid=3,app_name='UserNotificationCenter',bundle_id='com.apple.UserNotificationCenter');tick(g,1);self.assertIsNone(g.started_ns)
        g.front=dict(pid=2,app_name='Chrome',bundle_id='com.google.Chrome');browser(g,10);tick(g,10);self.assertEqual(g.started_ns,10*S)
    def test_skip_is_guarded_by_step_id(self):
        g=guide_harness();g.flow.start(0)
        with patch('overlay.attention_guide.time.perf_counter_ns',return_value=S):self.assertTrue(g.skip(0))
        g.flow.tick(2*S);self.assertFalse(g.skip(0));self.assertEqual(g.flow.phase,'tab')


class SelfPacedScoreTests(unittest.TestCase):
    def events(self):
        return [dict(type='acceptance',kind='guide_protocol',version=3,time_ns=0,browser_app='Chrome'),
                dict(type='scene',scene_id='initial',start_ns=0,app_name='Chrome',dynamic=False)]
    def test_actions_many_minutes_after_prompt_match_completion_not_deadline(self):
        events=self.events()
        for i,p in enumerate(('scroll','tab','app')):
            start=30+i*300;done=start+200
            events.extend([dict(type='acceptance',kind='phase',phase=p,time_ns=start*S),
                dict(type='acceptance',kind='action',phase=p,time_ns=done*S,started_ns=int((done-1.5)*S),app_name='Finder' if p=='app' else 'Chrome'),
                dict(type='scene',scene_id=p,start_ns=int((done-1.3)*S),confirmed_ns=done*S,app_name='Finder' if p=='app' else 'Chrome',dynamic=False)])
        result=score([],events,[1512,982]);self.assertTrue(result['transition_pass']);self.assertEqual(result['detected_transitions'],3)
    def test_completed_but_no_scene_is_different_from_skip(self):
        events=self.events()+[dict(type='acceptance',kind='phase',phase='scroll',time_ns=30*S),
            dict(type='acceptance',kind='action',phase='scroll',time_ns=400*S,started_ns=int(398.5*S),app_name='Chrome'),
            dict(type='acceptance',kind='phase',phase='tab',time_ns=401*S),
            dict(type='acceptance',kind='step_result',phase='tab',time_ns=600*S,result='skipped')]
        result=score([],events,[1512,982]);self.assertNotIn('scroll',result['steps_not_executed']);self.assertIn('tab',result['steps_not_executed'])
        self.assertEqual(result['executed_but_not_detected'],['scroll']);self.assertIn('已执行但切换未检出：',result_text(result))
    def test_4point5_second_detection_allowance_is_not_relaxed(self):
        events=self.events()+[dict(type='acceptance',kind='phase',phase='scroll',time_ns=30*S),
            dict(type='acceptance',kind='action',phase='scroll',time_ns=200*S,started_ns=int(198.5*S),app_name='Chrome'),
            dict(type='scene',scene_id='late',start_ns=int(204.6*S),app_name='Chrome',dynamic=False)]
        self.assertEqual(score([],events,[1512,982])['executed_but_not_detected'],['scroll'])
    def test_missing_or_interrupted_cpu_not_automatically_passed(self):
        events=self.events()+[dict(type='acceptance',kind='cpu_baseline',measurement=dict(process_tree_percent_one_core=50)),
            dict(type='acceptance',kind='cpu_recording',measurement=dict(process_tree_percent_one_core=40))]
        self.assertIsNone(score([],events,[1512,982])['cpu_pass'])
        events[-1]['measurement']['comparison_valid']=True;self.assertTrue(score([],events,[1512,982])['cpu_pass'])
    def test_baseline_is_30_seconds_still_foreground_and_restarts_on_action(self):
        g=guide_harness();g.requested=False;g.second_ready=True;g._baseline_ready=threading.Event()
        g._baseline_before=None;g._baseline_revision=None;g._baseline_elapsed_s=0.
        def baseline(t,revision=0,reason='heartbeat'):
            browser(g,t,revision=revision,reason=reason)
            with patch('overlay.attention_guide.cpu_snapshot',return_value=dict(time_ns=int(t*S),tree_cpu_s=t*.5)):g.baseline_tick(int(t*S))
        baseline(0);baseline(20,1,'scroll');baseline(30,1);self.assertFalse(g._baseline_ready.is_set())
        baseline(50,1);self.assertTrue(g._baseline_ready.is_set());self.assertEqual(g.baseline['seconds'],30)
    def test_cpu_window_not_resampled_as_actions_take_longer(self):
        g=guide_harness()
        for t in (0,16,30):browser(g,t);tick(g,t)
        saved=dict(g.recording_cpu)
        g.flow.skip(31*S,0);g.flow.tick(32*S);g._emit_steps()
        g.front=dict(pid=3,app_name='Finder',bundle_id='com.apple.finder');tick(g,3600)
        self.assertEqual(g.recording_cpu,saved)

class TabFullscreenRecoveryTests(unittest.TestCase):
    def test_completed_tab_retains_timestamp_and_waits_for_primary_fullscreen(self):
        g=guide_harness();g.recording_cpu=dict(seconds=30);g.flow.start(30*S);g.flow.skip(30*S,0);g.flow.tick(31*S);g._emit_steps()
        browser(g,35,page='secondary',revision=1,reason='visibilitychange',fullscreen=False);tick(g,35)
        browser(g,36.5,page='secondary',revision=1,fullscreen=False);tick(g,36.5)
        action=next(e for e in g.events if e['kind']=='action');self.assertEqual(action['time_ns'],int(36.5*S))
        browser(g,50,page='secondary',revision=1,fullscreen=False);tick(g,50)
        self.assertEqual(g.flow.phase,'tab')
        browser(g,51,page='primary',revision=1,reason='visibilitychange',fullscreen=False);tick(g,51)
        self.assertEqual(g.flow.phase,'tab')
        browser(g,52,page='primary',revision=1,fullscreen=True);tick(g,52)
        self.assertEqual(g.flow.phase,'app');self.assertEqual(len([e for e in g.events if e['kind']=='action']),1)
        self.assertEqual(action['time_ns'],int(36.5*S))
    def test_app_exclude_pause_steps_do_not_require_html_fullscreen(self):
        g=guide_harness();g.recording_cpu=dict(seconds=30)
        g.flow.start(30*S);g.flow.skip(30*S,0);g.flow.tick(31*S);g.flow.skip(31*S,1);g.flow.tick(32*S);g._emit_steps()
        g.front=dict(pid=3,app_name='Finder',bundle_id='com.apple.finder')
        browser(g,40,fullscreen=False);tick(g,40);browser(g,41.5,fullscreen=False);tick(g,41.5)
        self.assertEqual(g.flow.feedback,'completed')
        tick(g,43);self.assertEqual(g.flow.phase,'exclude')
        g.front=dict(pid=4,app_name='备忘录',bundle_id='com.apple.Notes');g.update_session_state(False,False,True)
        tick(g,50);tick(g,51.5);self.assertEqual(g.flow.feedback,'completed')
        tick(g,53);self.assertEqual(g.flow.phase,'pause')
        g.update_session_state(True,False);tick(g,54);tick(g,55.5)
        g.update_session_state(False,True);tick(g,60);tick(g,61.5);self.assertEqual(g.flow.feedback,'completed')
