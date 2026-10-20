import copy
from dataclasses import replace
import unittest
import numpy as np
from gaze_core.config import load_settings,FilterSettings,FixationSettings
from gaze_core.display import DisplayPipeline,DisplaySessions

class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.settings=load_settings(use_local=False)
        self.g=replace(self.settings.geometry,width_cm=30,height_cm=20,measured=True)
        self.s=FixationSettings(angular_threshold_deg_s=1e6,fallback_threshold_norm_s=1e6)
    def test_capture_time_not_server_timing_and_python_ns_origin_precision(self):
        web=DisplayPipeline(fixation_settings=self.s);python=DisplayPipeline(fixation_settings=self.s)
        base=9_000_000_000_000_001
        for time,p in [(0,[0,0]),(30,[.1,.2]),(110,[.2,.4])]:
            data=dict(valid=True,raw=[np.float64(v) for v in p],origin_m=[0,.1,.5],request_received_ns=base+time*9999,inference_completed_ns=base+time*777777)
            a=web.process(data,source='web',geometry=self.g,browser_capture_ms=1000+time)
            b=python.process(data,source='python',geometry=self.g,capture_ns=base+time*1_000_000)
            np.testing.assert_allclose(a['position'],b['position'],atol=1e-14)
            if time:self.assertAlmostEqual(a['dt_s'],b['dt_s'])
        self.assertAlmostEqual(a['dt_s'],.08);self.assertEqual(b['clock_origin_ns'],base)
    def test_missing_time_never_falls_back_to_server(self):
        for source in ('web','python'):
            r=DisplayPipeline().process(dict(valid=True,raw=[0,0],request_received_ns=100,inference_completed_ns=200),source=source,geometry=self.g)
            self.assertEqual(r['state'],'missing_capture_timestamp');self.assertIsNone(r['position'])
    def test_calibrate_then_detect_then_reset_immediate_step(self):
        p=DisplayPipeline(fixation_settings=FixationSettings(angular_threshold_deg_s=1,window_ms=10))
        matrix=[[1,0],[0,2],[0,0]]
        data={'valid':True,'raw':[.2,.2],'origin_m':[0,.1,.5]};before=copy.deepcopy(data)
        p.process(data,source='web',geometry=self.g,matrix=matrix,browser_capture_ms=0)
        data['raw']=[.7,.4]
        r=p.process(data,source='web',geometry=self.g,matrix=matrix,browser_capture_ms=30)
        self.assertEqual(r['state'],'saccade');np.testing.assert_allclose(r['position'],[.7,.8]);self.assertEqual(r['unfiltered'],[.7,.8])
        self.assertEqual(data['raw'],[.7,.4])
    def test_invalid_gap_context_and_backwards_time(self):
        p=DisplayPipeline(fixation_settings=self.s)
        f=lambda t,data,m=None:p.process(data,source='web',geometry=self.g,matrix=m,browser_capture_ms=t)
        f(0,dict(valid=True,raw=[0,0]))
        self.assertEqual(f(0,dict(valid=True,raw=[1,1]))['state'],'nonincreasing_capture_timestamp')
        f(10,dict(valid=False));r=f(20,dict(valid=True,raw=[1,1]));self.assertEqual(r['position'],[1,1])
        r=f(1000,dict(valid=True,raw=[2,2]));self.assertEqual(r['position'],[2,2]);self.assertIsNone(r['dt_s'])
        r=f(1030,dict(valid=True,raw=[2,2]),[[1,0],[0,2],[0,0]])
        self.assertEqual(r['position'],[2,4])
    def test_page_isolation_epochs_expiration_and_capacity(self):
        s=replace(self.settings,display=replace(self.settings.display,max_pages=2,idle_timeout_s=1))
        clock=[0.];registry=DisplaySessions(s,monotonic=lambda:clock[0])
        call=lambda page,gen,t,pos:registry.process(page,gen,0,dict(valid=True,raw=pos),geometry=self.g,browser_capture_ms=t)
        call('a',1,0,[0,0]);self.assertEqual(call('b',1,0,[1,1])['position'],[1,1])
        self.assertEqual(call('a',2,0,[1,1])['position'],[1,1])
        self.assertEqual(call('a',1,100,[0,0])['state'],'stale_display_context')
        call('c',1,0,[0,0]);self.assertEqual(len(registry.pages),2)
        clock[0]=2;call('c',1,30,[1,1]);self.assertEqual(len(registry.pages),1)

if __name__=='__main__':unittest.main()
