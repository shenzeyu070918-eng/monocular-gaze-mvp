"""Web-clock recording contracts using an injected engine and synthetic image."""
import base64
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app as web
from gaze_core.config import load_settings
from gaze_core.web_recording import RecordingConflict, WebRecording
from tools.replay import read_session
from tools.replay import ReplayWarning


class WebRecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = load_settings()
        self.settings = replace(self.settings, recording=replace(self.settings.recording,
                                output_dir=self.directory.name))
        self.manager = WebRecording(self.settings)
        self.addCleanup(lambda: self.manager.stop(reason='test_cleanup'))
        self.result = {'valid': True, 'status': 'tracking', 'raw': [-.25, 1.4],
                       'landmarks': [[.5,.5]], 'origin_m':[0,0,.5], 'gaze_vector':[0,0,-1],
                       'head_degrees':[0,0,0], 'distance_cm':50, 'eye_openness':.3, 'inference_ms':1}
        self.engine = SimpleNamespace(device='cpu', device_name='FAKE CPU',
                                      predict=Mock(return_value=self.result))
        patcher = patch.multiple(web, recording=self.manager, engine=self.engine,
                                state={'ready': True, 'error': None})
        patcher.start(); self.addCleanup(patcher.stop)
        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)
        _, image = cv2.imencode('.jpg', np.zeros((8,8,3), dtype=np.uint8))
        self.image = base64.b64encode(image).decode()

    def start(self):
        response = self.client.post('/api/recording/start', json={
            'page_clock_id':'synthetic-page', 'geometry':asdict(self.settings.geometry),
            'screen':{'width_px':1920, 'height_px':1080, 'fullscreen':True,
                      'device_pixel_ratio':2, 'window_width_px':1920, 'window_height_px':1080},
            'measured':False, 'grayscale':True, 'camera_width':8, 'camera_height':8})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['session_id']

    def request_frame(self, session_id, frame_id=1):
        return self.client.post('/api/frame', json={
            'image':self.image, 'recording_session_id':session_id, 'frame_id':frame_id,
            'page_clock_id':'synthetic-page', 'browser_capture_ms':100.5,
            'browser_request_ms':102, 'video_frame':{'source':'requestVideoFrameCallback_latest',
            'mediaTime':.25, 'callback_ms':99}})

    def stop(self, session_id):
        response = self.client.post('/api/recording/stop',json={
            'session_id':session_id, 'reason':'test_finished','browser_time_ms':150})
        self.assertEqual(response.status_code,200,response.text)
        path = Path(self.directory.name)/response.json()['file']
        return read_session(path)

    def test_frame_receipt_preserves_three_browser_clocks_and_numeric_only_result(self):
        session_id = self.start()
        response = self.request_frame(session_id)
        self.assertEqual(response.json(), self.result)  # Existing prediction response unchanged.
        receipt = self.client.post('/api/recording/receipt',json={
            'session_id':session_id, 'frame_id':1, 'browser_response_ms':120.5})
        self.assertEqual(receipt.status_code,200,receipt.text)
        session = self.stop(session_id)
        frames = [row for row in session.records if row['type']=='frame']
        self.assertEqual(len(frames),1)
        row = frames[0]
        self.assertEqual([row[k] for k in ['browser_capture_ms','browser_request_ms','browser_response_ms']],
                         [100.5,102,120.5])
        self.assertEqual(row['raw'],[-.25,1.4])
        self.assertIsNone(row['capture_ns'])
        self.assertLessEqual(row['request_received_ns'],row['inference_completed_ns'])
        self.assertLessEqual(row['inference_completed_ns'],row['server_ns'])
        contents = session.path.read_text()
        for forbidden in [self.image, 'image', 'landmarks', 'base64']:
            self.assertNotIn(forbidden, contents)
        self.assertEqual(session.metadata['camera']['intrinsics_source'],'estimated')
        self.assertEqual(session.metadata['screen']['device_pixel_ratio'],2)

    def test_events_use_browser_time_even_when_receipt_arrives_later(self):
        session_id = self.start()
        self.request_frame(session_id)
        event = {'session_id':session_id, 'type':'target','action':'switch', 'task_type':'jump',
                 'task_id':'test-task', 'index':1, 'target':[1,0], 'stage_target':None,
                 'browser_time_ms':110}
        self.assertEqual(self.client.post('/api/recording/event',json=event).status_code,200)
        self.client.post('/api/recording/receipt',json={
            'session_id':session_id,'frame_id':1,'browser_response_ms':130})
        session = self.stop(session_id)
        self.assertEqual([r['type'] for r in session.records],['session','frame','target','end'])
        self.assertEqual(session.records[2]['target'],[1,0])
        self.assertEqual(session.records[1]['page_clock_id'],'synthetic-page')

    def test_missing_receipt_flushed_once_with_null_response_and_stop_is_idempotent(self):
        session_id = self.start()
        self.request_frame(session_id)
        session = self.stop(session_id)
        self.assertIsNone(session.records[1]['browser_response_ms'])
        self.assertEqual(self.stop(session_id).records,session.records)
        response = self.client.post('/api/recording/receipt',json={
            'session_id':session_id,'frame_id':1,'browser_response_ms':130})
        self.assertEqual(response.status_code,200)

    def test_single_session_and_page_clock_enforced_and_click_not_enabled(self):
        session_id = self.start()
        with self.assertRaises(RecordingConflict):
            self.manager.start(engine=self.engine,geometry=self.settings.geometry,screen={},
                measured=False,grayscale=False,camera_size=(8,8),page_clock_id='another')
        with self.assertRaises(RecordingConflict):
            self.manager.begin_frame(session_id,'other-page',1,browser_capture_ms=1,browser_request_ms=2)
        response = self.client.post('/api/recording/event',json={
            'session_id':session_id,'type':'click','x':.5,'y':.5,'button':'left','browser_time_ms':1})
        self.assertEqual(response.status_code,422)

    def test_unknown_image_field_in_event_is_rejected_without_writing(self):
        session_id = self.start()
        response = self.client.post('/api/recording/event',json={
            'session_id':session_id, 'type':'calibration','action':'reset','matrix':None,
            'calibration_id':'none','group':'A','browser_time_ms':1,'image':self.image})
        self.assertEqual(response.status_code,422)
        self.assertEqual(len(self.stop(session_id).records),2)

    def test_stop_waits_for_active_inference_and_records_last_result(self):
        session_id = self.start()
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.result
        self.engine.predict.side_effect = blocked
        prediction = threading.Thread(target=lambda: self.request_frame(session_id))
        prediction.start()
        self.assertTrue(entered.wait(3))
        result = []
        closer = threading.Thread(target=lambda: (result.append(self.manager.stop(session_id,'test_finished',150)),stopped.set()))
        closer.start()
        self.assertFalse(stopped.wait(.03))
        release.set(); prediction.join(3); closer.join(3)
        self.assertFalse(prediction.is_alive() or closer.is_alive())
        rows = read_session(Path(self.directory.name)/result[0]['file']).records
        self.assertEqual([r['type'] for r in rows],['session','frame','end'])

    def test_recording_failure_keeps_prediction_running_and_error_visible(self):
        session_id = self.start()
        self.manager._recorder.fail('synthetic disk error')
        response = self.request_frame(session_id)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),self.result)
        status = self.client.get('/api/recording/status').json()
        self.assertFalse(status['active'])
        self.assertIn('synthetic disk error',status['error'])

    def test_full_receipt_queue_preserves_pending_frame_and_final_failure_count(self):
        self.manager.settings = replace(self.settings, recording=replace(self.settings.recording,queue_size=1))
        session_id = self.start()
        self.request_frame(session_id)
        # The second frame cannot acquire a receipt slot; the first is still retained.
        self.assertEqual(self.request_frame(session_id,frame_id=2).status_code,200)
        cleanup = self.manager._failure_cleanup
        self.assertIsNotNone(cleanup)
        cleanup.join(3)
        self.assertFalse(cleanup.is_alive())
        status = self.manager.status()
        with self.assertWarnsRegex(ReplayWarning,'会话录制有失败'):
            session = read_session(Path(self.directory.name)/status['file'])
        self.assertEqual([r['type'] for r in session.records],['session','frame','end'])
        self.assertIsNone(session.records[1]['browser_response_ms'])
        self.assertEqual(session.records[1]['frame_id'],1)
        self.assertEqual(session.records[-1]['failed_records'],status['failed_records'])
        # A late receipt is harmless and cannot modify the final counter.
        self.manager.receipt(session_id,1,130)
        self.assertEqual(self.manager.status()['failed_records'],status['failed_records'])

    def test_full_receipt_queue_waits_for_inflight_prediction_before_end(self):
        self.manager.settings = replace(self.settings, recording=replace(self.settings.recording,queue_size=1))
        session_id = self.start()
        ticket = self.manager.begin_frame(session_id,'synthetic-page',1,
                    browser_capture_ms=100,browser_request_ms=102,request_received_ns=1000)
        self.assertIsNone(self.manager.begin_frame(session_id,'synthetic-page',2,
                    browser_capture_ms=110,browser_request_ms=112,request_received_ns=1100))
        self.manager.complete_frame(ticket,self.result,2000)
        self.manager._failure_cleanup.join(3)
        status = self.manager.status()
        with self.assertWarnsRegex(ReplayWarning,'会话录制有失败'):
            session = read_session(Path(self.directory.name)/status['file'])
        self.assertEqual([r['type'] for r in session.records],['session','frame','end'])
        self.assertEqual(session.records[1]['raw'],self.result['raw'])


if __name__ == '__main__':
    unittest.main()
