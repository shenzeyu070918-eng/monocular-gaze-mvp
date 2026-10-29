import unittest,tempfile,json
from pathlib import Path
from dataclasses import replace
import numpy as np
from gaze_core.attention.settings import AttentionSettings
from gaze_core.attention.contracts import ScreenSample
from gaze_core.attention.scenes import SceneDetector,scroll_offset
from gaze_core.attention.binding import Timeline,frame_record,extract_fixations
from gaze_core.attention.aoi import AOIStatistics,validate_regions
from gaze_core.attention.storage import SessionStore,read_jsonl,delete_session
from gaze_core.attention.stimulus import StimulusMapping
from gaze_core.config import load_settings,FixationSettings,ScreenGeometry
from gaze_core.demo_coordinates import DemoCoordinates
from overlay.mapping import ScreenRect
from types import SimpleNamespace

class AttentionTests(unittest.TestCase):
    def sample(self,t,value=0,pixels=None,app='browser'):
        return ScreenSample(np.full((200,320,3),value,np.uint8) if pixels is None else pixels,int(t*1e9),int(t*1e9)+100,app)
    def stable(self,d,t=0,value=0):
        d.observe(self.sample(t,value));d.observe(self.sample(t+.5,value));return d.observe(self.sample(t+1,value))
    def test_defaults_and_validation(self):
        self.assertTrue(load_settings(use_local=False).attention.hide_ring)
        for kwargs in ({'capture_hz':True},{'min_changed_area':float('nan')},{'repeat_count':8},{'hide_ring':0}):
            with self.assertRaises(ValueError):AttentionSettings(**kwargs)
    def test_backtrace_first_matching_and_only_old_new_gap(self):
        d=SceneDetector(AttentionSettings());self.stable(d)
        d.observe(self.sample(1.5));d.observe(self.sample(2,220));d.observe(self.sample(2.5,220));row=d.observe(self.sample(3,220))
        self.assertEqual(row['start_ns'],2000000050);self.assertEqual(row['transition'],[1500000050,2000000050])
    def test_small_flicker_does_not_hold_unstable(self):
        d=SceneDetector(AttentionSettings());self.stable(d)
        for i in range(2,30):
            pixels=np.zeros((200,320,3),np.uint8);pixels[:8,:8]=255 if i%2 else 0
            self.assertIsNone(d.observe(self.sample(i,pixels=pixels)))
        self.assertEqual(d.number,1);self.assertIsNone(d.pending_since)
    def test_repeating_video_region_ignored(self):
        d=SceneDetector(AttentionSettings());self.stable(d)
        for i in range(3,30):
            pixels=np.zeros((200,320,3),np.uint8);pixels[:64,:128]=255 if i%2 else 40
            d.observe(self.sample(i*.5,pixels=pixels))
        self.assertEqual(d.number,1);self.assertIsNone(d.pending_since)
    def test_dynamic_forced_at_configured_limit(self):
        d=SceneDetector(AttentionSettings());self.stable(d);rows=[]
        for i in range(3,16):
            row=d.observe(self.sample(i*.5,80 if i%2 else 200))
            if row:rows.append(row)
        self.assertTrue(any(r['dynamic'] for r in rows))
    def test_scroll_phase_has_direction_and_confidence(self):
        rng=np.random.default_rng(9);a=rng.integers(0,255,(200,320,3),dtype=np.uint8);b=np.roll(a,-20,axis=0)
        row=scroll_offset(a,b,AttentionSettings());self.assertTrue(row['accepted']);self.assertAlmostEqual(row['vertical_px'],-20,delta=1)
    def test_binding_splits_uncertain_and_pause(self):
        events=[dict(type='scene',scene_id='a',start_ns=0),dict(type='scene',scene_id='b',start_ns=3,transition=[2,3]),dict(type='state',time_ns=4,status='user_paused')]
        t=Timeline(events,0,6);parts=list(t.split(dict(start_ns=1,end_ns=6,center=[.5,.5])))
        self.assertEqual([(r['scene_id'],r['excluded_reason']) for r in parts],[('a',None),(None,'transition_uncertain'),('b',None),(None,'user_paused')]);self.assertEqual(sum(r['end_ns']-r['start_ns'] for r in parts),5)
        self.assertEqual(t.summary()['uncertain_fraction'],1/6)
    def test_soft_mass_conservation_with_overlap_and_background(self):
        regions=[dict(id='wide',rect=[0,0,.8,1]),dict(id='manual',rect=[.2,.2,.7,.7],source='manual')]
        stats=AOIStatistics(regions,dict(sigma_x=.1,sigma_y=.1),80)
        stats.add(dict(start_ns=0,end_ns=1000000000,center=[np.float64(.5),np.float64(.5)]));s=stats.snapshot()
        self.assertEqual(s['regions'][1]['hard_s'],1)
        self.assertAlmostEqual(sum(r['soft_s'] for r in s['regions'])+s['background_soft_s']+s['outside_probability_s'],1,places=12)
    def test_stimulus_notch_and_letterbox_mapping(self):
        c=DemoCoordinates(ScreenRect(0,0,1512,982,2),window_origin=(0,32),window_size=(1512,950),fullscreen=True)
        m=StimulusMapping(c,(1000,1000));center=c.window_to_screen((756,475))
        self.assertAlmostEqual(m.map(center)[0],.5,delta=.002);self.assertIsNone(m.map((0,.5)))
        self.assertGreater(m.normalized_rect()[1],0)
    def test_numeric_only_and_capture_clock_not_completion(self):
        row=frame_record(SimpleNamespace(captured_ns=123,completed_ns=900,data=dict(valid=True,status='tracking',raw=[np.float64(.2),np.float32(.4)],image='secret',base64='secret')))
        self.assertEqual(row['capture_ns'],123);self.assertNotIn('image',row);self.assertNotIn('base64',row)
    def test_store_truncation_delete_quota_and_no_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            s=SessionStore(directory,{},20000);s.append('gaze.jsonl',{'raw':[np.float64(.5)]});path=s.path;s.close()
            with (path/'gaze.jsonl').open('a') as f:f.write('{')
            warnings=[];self.assertEqual(len(list(read_jsonl(path/'gaze.jsonl',warnings))),1);self.assertTrue(warnings)
            with self.assertRaises(ValueError):s.destination('../escape')
            with self.assertRaises(OSError):s.write_bytes('large',b'0'*20000)
            delete_session(directory,path);self.assertFalse(path.exists())
    def test_fixation_uses_capture_clock_and_excludes_outside(self):
        frames=[dict(capture_ns=i*100000000,completed_ns=i*100000000+1000000000,raw=[.5,.5],valid=True,status='tracking') for i in range(8)]
        frames.append(dict(capture_ns=800000000,raw=[1.2,.5],valid=True,status='tracking'))
        result=list(extract_fixations(frames,FixationSettings(),ScreenGeometry(30,20,0,1,measured=True),[[1,0],[0,1],[0,0]],0))
        f=[v for k,v in result if k=='fixation'];self.assertEqual(f[0]['end_ns'],700000000)
