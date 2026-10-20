"""Device provenance and self-check events cross the real recording/replay boundary."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import app as web
from gaze_core.camera import CameraAdapter
from gaze_core.config import load_settings
from gaze_core.devices import parse_device
from gaze_core.recorder import SessionRecorder, session_metadata
from gaze_core.web_recording import WebRecording
from test_devices import synthetic_device
from tools.replay import read_session


class DeviceRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        settings = load_settings(use_local=False)
        self.profile = parse_device(synthetic_device(), 'synthetic-test.yaml')
        self.settings = replace(settings, device_profile=self.profile,
                                geometry=replace(settings.geometry,width_cm=30,height_cm=20,measured=True),
                                recording=replace(settings.recording,output_dir=self.temporary.name))
        self.adapter = CameraAdapter(self.profile)
        self.engine = SimpleNamespace(device='cpu',device_name='synthetic CPU',settings=self.settings,
                                      camera_metadata=lambda size=(640,480), geometry=None:self.adapter.prepare(size).metadata,
                                      current_camera=self.adapter.prepare((640,480)).metadata)

    def test_calibrated_session_and_numpy_frame_roundtrip(self):
        metadata = session_metadata(source='python', engine=self.engine, geometry=self.settings.geometry,
                                    camera_size=(640,480),grayscale=False,measured=True)
        self.assertTrue(metadata['camera']['undistort_applied'])
        self.assertEqual(metadata['screen']['width_px'],1440)
        recorder = SessionRecorder(metadata,directory=self.temporary.name)
        self.addCleanup(recorder.close)
        recorder.record_frame(dict(valid=True,status='tracking',raw=[np.float64(-.1),np.float32(.2)],
                                   camera=self.engine.current_camera),capture_ns=100,inference_completed_ns=200)
        recorder.close()
        session = read_session(recorder.path)
        self.assertEqual(session.metadata['camera']['device_file'],'synthetic-test.yaml')
        self.assertEqual(session.records[1]['camera'], {k:v for k,v in session.metadata['camera'].items() if k not in {'camera_index','configured_camera_index'}})
        self.assertEqual(session.metadata['camera']['camera_index'], self.settings.camera.index)
        self.assertEqual(session.records[1]['raw'][0],-.1)

    def test_aspect_fallback_marked_in_session_and_frame(self):
        with self.assertWarnsRegex(UserWarning,'aspect_ratio_mismatch'):
            metadata = session_metadata(source='python',engine=self.engine,geometry=self.settings.geometry,
                                        camera_size=(640,360),grayscale=False)
        recorder = SessionRecorder(metadata,directory=self.temporary.name)
        self.addCleanup(recorder.close)
        recorder.record_frame(dict(valid=False,status='no_face',camera=metadata['camera']),capture_ns=10,inference_completed_ns=20)
        recorder.close()
        session = read_session(recorder.path)
        for camera in (session.metadata['camera'],session.records[1]['camera']):
            self.assertEqual(camera['intrinsics_source'],'estimated')
            self.assertEqual(camera['intrinsics_reason'],'aspect_ratio_mismatch')
            self.assertFalse(camera['undistort_applied'])

    def test_distance_event_api_without_lifespan_or_hardware(self):
        manager = WebRecording(self.settings)
        self.addCleanup(manager.stop)
        with patch.multiple(web,settings=self.settings,engine=self.engine,recording=manager,
                            state={'ready':True,'error':None}):
            client = TestClient(web.app)
            self.addCleanup(client.close)
            config = client.get('/api/recording/config').json()
            self.assertEqual(config['distance_check']['distances_cm'],[40,50,60])
            self.assertEqual(client.get('/api/status').json()['camera']['device_file'],'synthetic-test.yaml')
            start = client.post('/api/recording/start',json=dict(page_clock_id='test-page',
                                geometry=asdict(self.settings.geometry),screen=dict(width_px=1440,height_px=900),
                                measured=True,grayscale=False,camera_width=640,camera_height=480))
            self.assertEqual(start.status_code,200,start.text)
            sid = start.json()['session_id']
            event = dict(session_id=sid,type='distance_check',measured_cm=40,estimated_cm=43,bias_cm=3,
                         bias_percent=7.5,sample_count=3,sample_start_ms=10,sample_end_ms=20,
                         browser_time_ms=30,warning=False,camera=self.engine.current_camera)
            self.assertEqual(client.post('/api/recording/event',json=dict(event,image='forbidden')).status_code,422)
            bad = dict(event,camera=dict(self.engine.current_camera,pixels=[1,2,3]))
            self.assertEqual(client.post('/api/recording/event',json=bad).status_code,422)
            self.assertEqual(client.post('/api/recording/event',json=event).status_code,200)
            stop = client.post('/api/recording/stop',json=dict(session_id=sid,reason='test',browser_time_ms=40))
            session = read_session(Path(self.temporary.name)/stop.json()['file'])
            self.assertEqual(session.summary()['event_counts'],{'distance_check':1})
            recorded = session.records[1]
            self.assertEqual((recorded['measured_cm'],recorded['bias_cm'],recorded['browser_time_ms']),(40,3,30))
            self.assertIsInstance(recorded['server_ns'],int)
            self.assertEqual(recorded['camera']['intrinsics_source'],'calibrated')


if __name__ == '__main__':
    unittest.main()
