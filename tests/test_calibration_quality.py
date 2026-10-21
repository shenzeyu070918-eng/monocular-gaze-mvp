from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
import numpy as np

from gaze_core.config import load_settings
from gaze_core.calibration_quality import calibration_quality,validate_quality,quality_text
from gaze_core.recorder import SessionRecorder,validate_payload
from tools.replay import read_session
from test_recorder import metadata
from overlay.tasks import TaskRunner


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.settings=load_settings(use_local=False)
        self.geometry=replace(self.settings.geometry,width_cm=30.,height_cm=20.,measured=True)
        self.samples=[dict(target_id=f'calibration-{i+1}',target=list(p),raw=[np.float64(p[0]+.1),np.float64(p[1]+.2)],sample_count=np.int64(8))
                      for i,p in enumerate(self.settings.evaluation.calibration_points)]
        self.matrix=[[1,0],[0,1],[0,0]]

    def test_measured_cm_and_unmeasured_logical_pixels_numpy_unclipped(self):
        quality=calibration_quality(self.samples,self.matrix,self.geometry)
        self.assertAlmostEqual(quality['mean'],5.)
        self.assertTrue(all(p['redo'] for p in quality['points']))
        self.assertGreater(quality['points'][5]['raw_median'][1],1.)
        pixels=calibration_quality(self.samples,self.matrix,replace(self.geometry,measured=False),screen_size=(1001,801))
        self.assertEqual(pixels['unit'],'logical_px');self.assertIsNone(pixels['threshold'])
        self.assertAlmostEqual(pixels['mean'],np.hypot(100,160))
        self.assertFalse(any(p['redo'] for p in pixels['points']))
        self.assertIn('不使用厘米阈值',quality_text(pixels))
        corrected=calibration_quality(self.samples,[[1,0],[0,1],[-.1,-.2]],self.geometry)
        self.assertLess(corrected['maximum'],1e-10)

    def test_quality_round_trip_and_privacy_whitelist(self):
        quality=calibration_quality(self.samples,self.matrix,self.geometry)
        fields=dict(task_id='synthetic-task',calibration_id='synthetic-calibration',phase='initial',quality=quality,event_ns=100,pipeline='python')
        validate_payload('calibration_quality',fields,'python')
        with tempfile.TemporaryDirectory() as folder:
            recorder=SessionRecorder(metadata(),directory=folder)
            self.assertTrue(recorder.emit('calibration_quality',**fields))
            recorder.close()
            session=read_session(recorder.path)
            event=next(r for r in session.records if r['type']=='calibration_quality')
            self.assertEqual(event['quality'],quality)
            self.assertEqual(session.summary()['event_counts']['calibration_quality'],1)
        for mutate in (lambda q:q.update(image='base64'),lambda q:q['points'][0].update(image=np.zeros((2,2))),
                       lambda q:q.update(round=2),lambda q:q['points'][0].update(residual=float('nan'))):
            bad=deepcopy(quality);mutate(bad)
            with self.assertRaises(ValueError):validate_quality(bad)

    def test_second_redo_and_evaluation_targets_are_refused(self):
        quality=calibration_quality(self.samples,self.matrix,self.geometry)
        first=dict(quality=quality,calibration_id='id',redo_round=0)
        with self.assertRaisesRegex(ValueError,'校准点'):
            TaskRunner('calibrate',self.settings,redo_from=first,redo_target_ids=['edge-1'])
        first['redo_round']=1
        with self.assertRaisesRegex(ValueError,'一轮'):
            TaskRunner('calibrate',self.settings,redo_from=first,redo_target_ids=['calibration-1'])


if __name__=='__main__':unittest.main()
