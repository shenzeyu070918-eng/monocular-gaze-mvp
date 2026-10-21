"""Capture-time Python events and distance provenance retain strict JSON schema."""
import tempfile
import unittest

import numpy as np

from gaze_core.config import load_settings
from gaze_core.evaluation import evaluate
from gaze_core.recorder import SessionRecorder, validate_metadata, validate_payload
from tools.replay import read_session


def metadata():
    return dict(source='python', version='test', commit='test', system='test', device_name='test',
                inference_device='cpu', inference_device_name='test', page_clock_id=None, grayscale=False,
                screen=dict(width_px=1000,height_px=800,measured=False),
                camera=dict(width_px=640,height_px=480,intrinsics_source='estimated',
                            matrix=[[640,0,320],[0,640,240],[0,0,1]],distortion=[0]*5))


class PythonEventClockTests(unittest.TestCase):
    def test_event_ns_orders_against_capture_ns_with_old_server_ns_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), directory=folder)
            self.assertTrue(recorder.emit('target', action='appear',task_type='evaluate',task_id='task',
                                          index=0,target=[.2,.3],event_ns=np.int64(20)))
            self.assertTrue(recorder.record_frame(dict(valid=False,status='blink'),
                                                  capture_ns=10,inference_completed_ns=30))
            # Legacy Python event has no event_ns and keeps server_ns ordering.
            self.assertTrue(recorder.emit('target', action='end',task_type='evaluate',task_id='task',
                                          index=0,target=[.2,.3]))
            recorder.close()
            ordered = read_session(recorder.path).records
            self.assertEqual([row['type'] for row in ordered], ['session','frame','target','target','end'])
            self.assertIs(type(ordered[2]['event_ns']), int)
            self.assertNotIn('event_ns',ordered[3])
            self.assertNotIn('browser_time_ms', ordered[2])

    def test_distance_and_profile_provenance_are_optional_strict_numeric_fields(self):
        distance = dict(estimated_distance_cm_median=np.float32(49.5),
                        estimated_distance_sample_count=np.int64(8), conditions_fingerprint='abc123',
                        pipeline='python',profile_file='python/test.json',event_ns=np.int64(77))
        calibration = dict(action='applied',group='B',matrix=[[1,0],[0,1],[0,0]],
                           calibration_id='calibration',coordinate_system='full_screen_normalized',**distance)
        summary = evaluate([], [], load_settings(use_local=False).geometry)
        evaluation = dict(task_id='task',run_id='run',calibration_id='calibration',completed=True,
                          reason='completed',summary=summary,**distance)
        with tempfile.TemporaryDirectory() as folder:
            meta = dict(metadata(),pipeline='python',conditions_fingerprint='abc123',profile_file=None)
            recorder = SessionRecorder(meta,directory=folder)
            self.assertTrue(recorder.emit('calibration',**calibration))
            self.assertTrue(recorder.emit('evaluation_summary',**evaluation))
            recorder.close()
            events = [row for row in read_session(recorder.path).records if row['type'] in {'calibration','evaluation_summary'}]
            for event in events:
                self.assertEqual(event['estimated_distance_cm_median'],49.5)
                self.assertEqual(event['estimated_distance_sample_count'],8)
                self.assertIs(type(event['estimated_distance_sample_count']), int)
                self.assertEqual(event['conditions_fingerprint'],'abc123')
        for value in (True,float('nan'),float('inf'),-1,0,'50'):
            with self.subTest(distance=value),self.assertRaises(ValueError):
                validate_payload('calibration',dict(calibration,estimated_distance_cm_median=value),'python')
        for value in (True,-1,1.2,'8'):
            with self.subTest(count=value),self.assertRaises(ValueError):
                validate_payload('calibration',dict(calibration,estimated_distance_sample_count=value),'python')
        for malicious in ({'conditions':{'image':'data:image/png;base64,AAAA'}},
                          {'profile_file':'data:image/png;base64,AAAA'},
                          {'conditions_fingerprint':{'image':[1,2,3]}}):
            with self.subTest(malicious=malicious),self.assertRaises(ValueError):
                validate_payload('calibration',dict(calibration,**malicious),'python')
        with self.assertRaises(ValueError):
            validate_metadata(dict(metadata(),conditions={'image':[1,2,3]}))


if __name__ == '__main__':
    unittest.main()
