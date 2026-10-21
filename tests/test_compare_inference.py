from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from gaze_core.config import load_settings
from tools.compare_inference import measured_call,difference,compare


class CompareTests(unittest.TestCase):
    def test_synchronization_wraps_timer_and_differences_are_actual_numbers(self):
        calls=[];clock=iter([10,2_000_010])
        result,elapsed=measured_call(lambda:calls.append('call') or np.float64(3),synchronize=lambda:calls.append('sync'),clock=lambda:next(clock))
        self.assertEqual(calls,['sync','call','sync'])
        self.assertEqual((result,elapsed),(3,2.))
        report=difference([[1,2],[3,4]],[[1,3],[5,4]])
        self.assertEqual(report,dict(pairs=2,max_abs=2.,mean_abs=.75))
        self.assertIsNone(difference([],[])['max_abs'])

    def test_comparison_reuses_identical_frames_and_patches_and_closes_every_engine(self):
        import torch
        engines=[]
        class FakeEngine:
            def __init__(self,settings):
                self.device=settings.inference.device;self.closed=False;self.images=[];self.patches=[]
                self.model=SimpleNamespace(_gaze_estimation_model=torch.nn.Identity(),_run_face_model=self.forward)
                engines.append(self)
            def forward(self,image):
                self.patches.append(image.copy())
                return self.model._gaze_estimation_model(torch.tensor(image[0,0,:2],dtype=torch.float32)).numpy()
            def predict(self,image,*args):
                self.images.append(image.copy());self.model._run_face_model(image[:2,:2])
                return dict(status='tracking',valid=True,raw=[float(image[0,0,0]),.5])
            def close(self):self.closed=True
        images=[np.full((4,4,3),v,dtype=np.uint8) for v in (10,20)]
        settings=load_settings(use_local=False)
        with patch.object(torch.backends.mps,'is_available',return_value=True),patch.object(torch.mps,'synchronize') as sync:
            report=compare(settings,images,warmup=1,engine_factory=FakeEngine)
        self.assertEqual(len(report['results']),4)
        self.assertTrue(sync.called)
        self.assertTrue(all(e.closed for e in engines))
        for result in report['results']:
            self.assertEqual(result['status'],'measured',result)
            self.assertEqual(result['fixed_patch_count'],2)
            self.assertEqual(result['fixed_patch_output_radians_difference']['max_abs'],0.)
            self.assertEqual(result['raw_normalized_difference']['max_abs'],0.)
        for engine in engines:
            for a,b in zip(engine.images[1:],images):np.testing.assert_array_equal(a,b)
            np.testing.assert_array_equal(engine.patches[-2],images[0][:2,:2])

    def test_unavailable_mps_is_skipped_and_never_called_a_measurement(self):
        import torch
        with patch.object(torch.backends.mps,'is_available',return_value=False):
            report=compare(load_settings(use_local=False),[np.zeros((2,2,3),dtype=np.uint8)],warmup=0,
                           engine_factory=lambda s:(_ for _ in ()).throw(RuntimeError('fake CPU unavailable')))
        self.assertEqual([r['status'] for r in report['results']],['failed','failed','skipped','skipped'])
        self.assertTrue(all('engine_ms' not in r for r in report['results']))


if __name__=='__main__':unittest.main()
