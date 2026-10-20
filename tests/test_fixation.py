from dataclasses import replace
import unittest
import numpy as np
from gaze_core.config import load_settings, FixationSettings
from gaze_core.fixation import FixationDetector

class FixationTests(unittest.TestCase):
    def setUp(self):
        self.g=replace(load_settings(use_local=False).geometry,width_cm=100,height_cm=100,top_cm=-50,measured=True)
    def test_known_ray_angular_speed(self):
        detector=FixationDetector(FixationSettings(window_ms=20,max_gap_ms=2000,angular_threshold_deg_s=40))
        detector.update([.5,.5],0,origin_m=[0,0,.5],geometry=self.g)
        r=detector.update(np.array([1.,.5]),1,origin_m=np.array([0.,0.,.5]),geometry=self.g)
        self.assertAlmostEqual(r['velocity'],45)
        self.assertEqual(r['velocity_unit'],'deg/s');self.assertEqual(r['state'],'saccade')
    def test_fallback_unit_change_and_finished_fixations(self):
        d=FixationDetector()
        for t in [0,.06,.12]:r=d.update([.5,.5],t,origin_m=[0,0,.5],geometry=self.g)
        self.assertEqual(r['state'],'fixation')
        r=d.update([.5,.5],.15,geometry=self.g)
        self.assertEqual(r['fallback_reason'],'missing_origin');self.assertEqual(r['velocity_unit'],'screen_norm/s')
        self.assertEqual(len(r['finished_fixations']),1)
        self.assertAlmostEqual(r['finished_fixations'][0]['duration_ms'],120)
        np.testing.assert_allclose(r['finished_fixations'][0]['center'],[.5,.5])
        r=d.update([.7,.5],.18,geometry=self.g)
        self.assertEqual(r['state'],'saccade')
    def test_window_uses_timestamps_and_gap_resets(self):
        d=FixationDetector(FixationSettings(window_ms=80,fallback_threshold_norm_s=100))
        for t in (0,.01,.03,.09):r=d.update([t,0],t,geometry=self.g)
        self.assertAlmostEqual(r['velocity'],1)
        r=d.update([5,5],1,geometry=self.g)
        self.assertIsNone(r['velocity']);self.assertEqual(r['state'],'unknown')
        with self.assertRaises(ValueError):d.update([5,5],1,geometry=self.g)
    def test_invalid_origin_and_unmeasured_geometry_marked(self):
        for origin,g,reason in [([0,0,-1],self.g,'invalid_origin'),([0,0,.5],replace(self.g,measured=False),'unmeasured_geometry')]:
            r=FixationDetector().update([.5,.5],0,origin_m=origin,geometry=g)
            self.assertEqual(r['fallback_reason'],reason)

if __name__=='__main__':unittest.main()
