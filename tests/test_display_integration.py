import base64
import copy
from dataclasses import asdict,replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import cv2
import numpy as np
from fastapi.testclient import TestClient
import app as web
from gaze_core.config import load_settings,FixationSettings
from gaze_core.display import DisplaySessions,DisplayPipeline
from gaze_core.capture import CameraPipeline
from gaze_core.profiles import save_profile,load_profile
from test_capture import FakeCamera,FakeEngine

class DisplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings=load_settings(use_local=False)
        self.geometry=replace(self.settings.geometry,width_cm=30,height_cm=20,measured=True)
    def test_web_capture_clock_and_raw_engine_result_untouched(self):
        settings=replace(self.settings,fixation=FixationSettings(angular_threshold_deg_s=1e6))
        data={'valid':True,'status':'tracking','raw':[.2,.3],'origin_m':[0,.1,.5],'inference_ms':1}
        original=copy.deepcopy(data)
        engine=SimpleNamespace(predict=Mock(side_effect=lambda *a:copy.deepcopy(data)))
        _,encoded=cv2.imencode('.jpg',np.zeros((8,8,3),np.uint8))
        body=dict(image=base64.b64encode(encoded).decode(),geometry=asdict(self.geometry),page_clock_id='test-page',display_context=dict(generation=1,revision=0,matrix=[[1,0],[0,2],[0,0]]))
        registry=DisplaySessions(settings)
        recorder=Mock();recorder.begin_frame.return_value=None
        with patch.multiple(web,engine=engine,state={'ready':True,'error':None},display_sessions=registry,recording=recorder):
            client=TestClient(web.app)
            with patch.object(web.time,'perf_counter_ns',side_effect=[100,1000,1500,20000,9000000,9900000]):
                a=client.post('/api/frame',json=dict(body,browser_capture_ms=1000)).json()
                data['raw']=[.3,.4]
                b=client.post('/api/frame',json=dict(body,browser_capture_ms=1040)).json()
            self.assertEqual(a['raw'],original['raw']);self.assertEqual(a['display']['unfiltered'],[.2,.6])
            self.assertAlmostEqual(b['display']['dt_s'],.04)
            self.assertNotEqual(b['display']['position'],b['display']['unfiltered'])
            numeric=recorder.complete_frame.call_args.args[1]
            self.assertNotIn('display',numeric);self.assertEqual(numeric['raw'],[.3,.4])
            no_clock=client.post('/api/frame',json=body).json()
            self.assertEqual(no_clock['display']['state'],'missing_capture_timestamp')
            bad=dict(body);bad.pop('page_clock_id');self.assertEqual(client.post('/api/frame',json=bad).status_code,422)
            client.close()
    def test_python_pipeline_passes_read_return_capture_ns_not_compute_clock(self):
        settings=replace(self.settings,camera=replace(self.settings.camera,warmup_timeout_s=1))
        pipe=DisplayPipeline(fixation_settings=FixationSettings(fallback_threshold_norm_s=1e6),matrix=[[1,0],[0,2],[0,0]])
        wrapped=Mock(wraps=pipe)
        camera=FakeCamera(stop_requested=lambda:pipeline._stop.is_set())
        camera.send(100)
        engine=FakeEngine();engine.predict=Mock(return_value=dict(valid=True,status='tracking',raw=[.5,.5],inference_ms=0))
        pipeline=CameraPipeline(settings,camera_factory=lambda _:camera,engine_factory=lambda:engine,display_pipeline=wrapped)
        self.addCleanup(pipeline.close);pipeline.start()
        first=pipeline.wait_for_result(timeout=3);self.assertIsNotNone(first)
        camera.send(110);second=pipeline.wait_for_result(first.sequence,timeout=3)
        args=wrapped.process.call_args.kwargs
        self.assertEqual(args['capture_ns'],second.captured_ns);self.assertNotIn('inference_completed_ns',args)
        self.assertEqual(args['source'],'python');self.assertEqual(second.data['display']['unfiltered'],[.5,1.])
        self.assertAlmostEqual(second.data['display']['dt_s'],(second.captured_ns-first.captured_ns)/1e9)
        self.assertNotEqual(second.captured_ns,second.completed_ns)
        pipeline.pause();self.assertEqual(camera.release_threads,['gaze-capture']);self.assertFalse(camera.release_during_read[0])
    def test_explicit_profile_device_coordinates_and_geometry_checks(self):
        screen=dict(fullscreen=True,width_px=1000,height_px=800,width_cm=30,height_cm=20,measured=True,horizontal_cm=0,top_cm=0)
        with TemporaryDirectory() as folder:
            save_profile([[1,0],[0,1],[0,0]],name='synthetic',device_file='device.yaml',screen=screen,calibration_id='id',directory=folder)
            args=dict(name='synthetic',device_file='device.yaml',geometry=self.geometry,screen=screen,directory=folder)
            result=load_profile(**args);self.assertEqual(result['file'],'synthetic.json')
            for change in [dict(device_file='other.yaml'),dict(geometry=replace(self.geometry,width_cm=31)),dict(screen=dict(screen,width_px=900))]:
                with self.assertRaises(ValueError):load_profile(**dict(args,**change))
            path=Path(folder)/'synthetic.json';text=path.read_text();path.write_text(text.replace('corrected_fullscreen_normalized','stage_normalized'))
            with self.assertRaises(ValueError):load_profile(**args)

if __name__=='__main__':unittest.main()
