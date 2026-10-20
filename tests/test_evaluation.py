from dataclasses import replace
import math
import tempfile
from pathlib import Path
import json
import unittest
from unittest.mock import patch
import numpy as np
from gaze_core.config import load_settings
from gaze_core.evaluation import evaluate, ray_errors, screen_to_camera
from gaze_core.geometry import intersect_screen
from gaze_core.profiles import save_profile

class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.g = replace(load_settings(use_local=False).geometry,width_cm=100.,height_cm=100.,measured=True)
        self.targets = [dict(target_id='c',region='center',target=[.5,.5]),dict(target_id='e',region='edge',target=[.9,.1])]
    def test_known_3d_rays_yaw_pitch_and_off_center_origin(self):
        g = replace(self.g,top_cm=-50)
        a = ray_errors([0,0,.5],[1,.5],[.5,.5],g)
        self.assertAlmostEqual(a['angle_deg'],45);self.assertAlmostEqual(a['yaw_deg'],45);self.assertAlmostEqual(a['pitch_deg'],0)
        a = ray_errors([0,0,.5],[.5,1],[.5,.5],g)
        self.assertAlmostEqual(a['angle_deg'],45);self.assertAlmostEqual(a['pitch_deg'],45)
        # Off-axis origin: rays are X=-.5,Z=-.5 and X=-1,Z=-.5.
        a = ray_errors([.5,0,.5],[1,.5],[.5,.5],g)
        self.assertAlmostEqual(a['angle_deg'],math.degrees(math.atan(2))-45)
        both = ray_errors(np.array([0,0,.5]),[1,1],[.5,.5],g)
        self.assertAlmostEqual(both['angle_deg'],math.degrees(math.acos(1/math.sqrt(3))))
        self.assertAlmostEqual(both['pitch_deg'],math.degrees(math.atan(1/math.sqrt(2))))
    def test_screen_camera_intersection_roundtrip_offsets(self):
        g = replace(self.g,horizontal_cm=2,top_cm=.3)
        origin=np.array([.03,.1,.5]);target=[np.float64(-.1),np.float64(1.2)]
        point=screen_to_camera(target,g)
        raw=intersect_screen(origin,point-origin,g.width_cm,g.height_cm,g.top_cm,g.horizontal_cm)
        np.testing.assert_allclose(raw,target)
    def test_equal_target_medians_bias_slopes_and_missing_origins(self):
        rows=[dict(target_id='c',valid=True,raw=[np.float64(.51),.52],origin_m=[0,.3,.5])]*10
        rows += [dict(target_id='e',valid=True,raw=[.93,.14],origin_m=None)]
        result=evaluate(rows,self.targets,self.g,[[1,0],[0,1],[0,0]])
        a=result['groups']['A']['all']
        self.assertAlmostEqual(a['mean_cm'],(math.sqrt(5)+5)/2)
        self.assertAlmostEqual(a['horizontal_cm'],2);self.assertAlmostEqual(a['vertical_cm'],3)
        self.assertEqual(a['missing_origin_frames'],1);self.assertEqual(a['angle_frame_count'],10)
        self.assertAlmostEqual(a['horizontal_scale'],1.05);self.assertAlmostEqual(a['vertical_scale'],.95)
        self.assertEqual(result['groups']['A'],result['groups']['B'])
    def test_missing_targets_invalid_frames_and_origin_keep_position_stats(self):
        rows=[dict(target_id='c',valid=True,raw=[.5,.5],origin_m=[0,0,-1]),dict(target_id='e',valid=False)]
        result=evaluate(rows,self.targets,self.g)['groups']['A']
        self.assertFalse(result['all']['complete']);self.assertEqual(result['edge']['valid_targets'],0)
        self.assertEqual(result['all']['invalid_origin_frames'],1)
        self.assertIsNone(result['all']['angle_deg']);self.assertEqual(result['all']['mean_cm'],0)
    def test_no_measured_size_no_cm_or_angles_but_scale_available(self):
        rows=[dict(target_id=t['target_id'],valid=True,raw=t['target'],origin_m=[0,0,.5]) for t in self.targets]
        a=evaluate(rows,self.targets,replace(self.g,measured=False))['groups']['A']['all']
        self.assertIsNone(a['mean_cm']);self.assertIsNone(a['angle_deg']);self.assertEqual(a['horizontal_scale'],1)
    def test_thresholds_and_extreme_raw_are_not_clipped(self):
        rows=[dict(target_id=t['target_id'],valid=True,raw=[t['target'][0]+.015,t['target'][1]]) for t in self.targets]
        a=evaluate(rows,self.targets,self.g)['groups']['A']['all']
        # Floating representation of a threshold does not justify rewriting real errors.
        self.assertEqual(a['within2'],1)
        rows[0]['raw']=[3,4]
        self.assertGreater(evaluate(rows,self.targets,self.g)['groups']['A']['all']['mean_cm'],100)
    def test_reject_unknown_target_and_nonfinite_origin(self):
        with self.assertRaises(ValueError): evaluate([dict(target_id='bad',valid=False)],self.targets,self.g)
        with self.assertRaises(ValueError): ray_errors([0,float('nan'),.5],[.5,.5],[.5,.5],self.g)
    def test_profile_atomic_fullscreen_matrix_and_failed_save(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments=dict(name='synthetic',device_file='synthetic.yaml',screen={'fullscreen':True},calibration_id='test',directory=directory)
            matrix=[[np.float64(1),0],[0,1],[0,0]]
            path=save_profile(matrix,**arguments);before=path.read_bytes();data=json.loads(before)
            self.assertEqual(data['matrix'],matrix);self.assertFalse(data['coordinates']['clip'])
            with patch('gaze_core.profiles.os.replace',side_effect=OSError('disk full')),self.assertRaises(OSError):save_profile(matrix,**arguments)
            self.assertEqual(path.read_bytes(),before);self.assertEqual(len(list(Path(directory).iterdir())),1)
            with self.assertRaisesRegex(ValueError,'全屏'):save_profile(matrix,**dict(arguments,screen={'fullscreen':False}))
            with self.assertRaises(ValueError):save_profile(matrix,**dict(arguments,name='../unsafe'))

if __name__ == '__main__':unittest.main()
