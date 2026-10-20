from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import numpy as np
import app as web
from gaze_core.config import load_settings
from gaze_core.recorder import SessionRecorder, session_metadata, validate_payload
from gaze_core.evaluation import evaluate
from gaze_core.web_recording import WebRecording
from tools.replay import read_session

class EvaluationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=load_settings(use_local=False)
        self.geometry=replace(self.settings.geometry,width_cm=100,height_cm=100,measured=True)
        self.settings=replace(self.settings,recording=replace(self.settings.recording,output_dir=self.tmp.name),geometry=self.geometry)
        self.engine=SimpleNamespace(device='cpu',device_name='test',settings=self.settings)
        self.manager=WebRecording(self.settings);self.addCleanup(self.manager.stop)
        self.patch=patch.multiple(web,settings=self.settings,engine=self.engine,recording=self.manager,state={'ready':True,'error':None});self.patch.start();self.addCleanup(self.patch.stop)
        self.client=TestClient(web.app);self.addCleanup(self.client.close)
        self.targets=[dict(target_id='center-1',region='center',target=[.5,.5]),dict(target_id='edge-1',region='edge',target=[.05,.05])]
    def body(self):
        return dict(frames=[dict(target_id='center-1',valid=True,raw=[.51,.52],origin_m=[0,.25,.5])],
                    targets=self.targets,geometry=asdict(self.geometry),matrix=None,session_id=None,
                    task_id='test-task',run_id='test-run',calibration_id='none',completed=True,reason='completed',browser_time_ms=10)
    def test_real_summary_recording_replay_and_privacy(self):
        start=self.client.post('/api/recording/start',json=dict(page_clock_id='page',screen={'fullscreen':True,'width_px':1000,'height_px':800},geometry=asdict(self.geometry),measured=True,grayscale=False,camera_width=640,camera_height=480,browser_camera={'device_id':'browser-id','label':'synthetic camera'}))
        self.assertEqual(start.status_code,200,start.text)
        body=self.body();body['session_id']=start.json()['session_id']
        result=self.client.post('/api/evaluation/summary',json=body)
        self.assertEqual(result.status_code,200,result.text)
        summary=result.json();self.assertFalse(summary['groups']['A']['all']['complete'])
        self.client.post('/api/recording/stop',json=dict(session_id=body['session_id'],reason='test',browser_time_ms=20))
        session=read_session(next(Path(self.tmp.name).glob('*.jsonl')))
        self.assertEqual(session.summary()['event_counts'],{'evaluation_summary':1})
        self.assertEqual(session.records[1]['summary'],summary)
        self.assertEqual(session.metadata['camera']['browser_camera']['device_id'],'browser-id')
        self.assertNotIn('camera_index',session.metadata['camera'])
        self.assertEqual(session.metadata['camera']['configured_camera_index'],0)
        fields=dict(task_id='test',run_id='r',calibration_id='none',completed=True,reason='completed',summary=summary,browser_time_ms=2)
        validate_payload('evaluation_summary',fields,'web')
        summary['groups']['A']['all']['image']='data:image/jpeg;base64,BAD'
        with self.assertRaises(ValueError):validate_payload('evaluation_summary',fields,'web')
    def test_summary_numpy_normalization_and_invalid_payloads(self):
        summary=evaluate([dict(target_id='center-1',valid=True,raw=[np.float64(.5),np.float32(.5)])],self.targets,self.geometry)
        json.dumps(summary,allow_nan=False)
        body=self.body();body['frames'][0]['image']='bad'
        self.assertEqual(self.client.post('/api/evaluation/summary',json=body).status_code,422)
        body=self.body();body['targets'].append(body['targets'][0])
        self.assertEqual(self.client.post('/api/evaluation/summary',json=body).status_code,422)
    def test_calibration_fullscreen_profile_and_save_failure(self):
        body=dict(samples=[dict(raw=[x,y],target=[x,y]) for x in (0,.5,1) for y in (0,.5,1)],fullscreen=False,screen={'fullscreen':True},profile_name='synthetic',calibration_id='id')
        self.assertEqual(self.client.post('/api/calibrate',json=body).status_code,422)
        body['fullscreen']=True
        with patch.object(web,'save_profile',return_value=Path(self.tmp.name)/'synthetic.json') as save:
            result=self.client.post('/api/calibrate',json=body)
            self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['profile_file'],'synthetic.json')
            np.testing.assert_allclose(save.call_args.args[0],[[1,0],[0,1],[0,0]],atol=1e-12)
        with patch.object(web,'save_profile',side_effect=OSError('disk full')):
            result=self.client.post('/api/calibrate',json=body).json()
            self.assertIsNone(result['profile_file']);self.assertIn('disk full',result['profile_error'])

if __name__ == '__main__':unittest.main()
