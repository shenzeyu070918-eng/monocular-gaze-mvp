from dataclasses import replace
from types import SimpleNamespace
from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock,call
import numpy as np

from gaze_core.config import load_settings
from gaze_core.devices import DeviceProfile
from gaze_core.camera import CameraAdapter
from tools.compare_inference import (measured_call,difference,compare,collect_images,ComparisonCameraError,
                                    main,output_path,summary_table)


def settings_for(size=(4,4)):
    base=load_settings(use_local=False);w,h=size
    profile=DeviceProfile('synthetic','synthetic.yaml',((w,0,w/2),(0,w,h/2),(0,0,1)),(0,)*5,size,
                          0.,'2026-01-01T00:00:00',None,None,False,None,0.,0.)
    return replace(base,device_file='synthetic.yaml',device_profile=profile,
                   camera=replace(base.camera,width=w,height=h,undistort=False))


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
                return dict(status='tracking',valid=True,raw=[float(image[0,0,0]),.5],camera=camera)
            def close(self):self.closed=True
        images=[np.full((4,4,3),v,dtype=np.uint8) for v in (10,20)]
        settings=settings_for()
        camera=CameraAdapter(settings.device_profile,undistort=False).prepare((4,4)).metadata
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
            report=compare(settings_for((2,2)),[np.zeros((2,2,3),dtype=np.uint8)],warmup=0,
                           engine_factory=lambda s:(_ for _ in ()).throw(RuntimeError('fake CPU unavailable')))
        self.assertEqual([r['status'] for r in report['results']],['failed','failed','skipped','skipped'])
        self.assertTrue(all('engine_ms' not in r for r in report['results']))

    def test_shared_setup_requests_config_resolution_before_read_and_releases(self):
        import cv2
        settings=settings_for((640,480));camera=Mock();frames=[np.ones((480,640,3),dtype=np.uint8)*50]*2
        def reader(handle,options):
            camera.set.assert_has_calls([call(cv2.CAP_PROP_FRAME_WIDTH,640),call(cv2.CAP_PROP_FRAME_HEIGHT,480)])
            self.assertIs(handle,camera);self.assertIs(options,settings.camera)
            return iter((frame,i) for i,frame in enumerate(frames))
        with patch('tools.compare_inference.open_camera',return_value=camera) as opener, \
             patch('tools.compare_inference.read_camera_frames',side_effect=reader), \
             patch('tools.compare_inference.time.monotonic',side_effect=[0.,4.,5.]):
            images=collect_images(settings,2)
        opener.assert_called_once_with(settings.camera)
        camera.isOpened.assert_called_once();camera.release.assert_called_once()
        self.assertEqual(len(images),2)
        self.assertFalse(np.shares_memory(images[0],frames[0]))

    def test_actual_resolution_mismatch_aborts_even_during_warmup_and_cli_never_measures(self):
        settings=settings_for((640,480));camera=Mock()
        with patch('tools.compare_inference.load_settings',return_value=settings), \
             patch('tools.compare_inference.open_camera',return_value=camera), \
             patch('tools.compare_inference.read_camera_frames',return_value=iter([(np.ones((1080,1920,3),dtype=np.uint8),0)])), \
             patch('tools.compare_inference.compare') as measure,redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['--frames','1']),1)
        self.assertIn('1920×1080',error.getvalue());self.assertIn('640×480',error.getvalue())
        measure.assert_not_called();camera.release.assert_called_once()

    def test_intrinsics_fallback_aborts_before_camera_or_engine_and_checks_all_inputs(self):
        base=settings_for((640,480))
        for settings,reason in ((replace(base,device_profile=None),'no_device_file'),
                                (replace(base,camera=replace(base.camera,width=1920,height=1080)),'aspect_ratio_mismatch')):
            with self.subTest(reason=reason),patch('tools.compare_inference.open_camera') as opener,self.assertRaisesRegex(ComparisonCameraError,reason):
                collect_images(settings,1)
            opener.assert_not_called()
        factory=Mock()
        with self.assertRaisesRegex(ComparisonCameraError,'实际分辨率'):
            compare(base,[np.zeros((480,640,3),dtype=np.uint8),np.zeros((960,1280,3),dtype=np.uint8)],engine_factory=factory)
        factory.assert_not_called()

    def test_runtime_intrinsics_fallback_aborts_remaining_devices_and_closes_engine(self):
        settings=settings_for()
        good=dict(valid=False,status='no_face',camera=CameraAdapter(settings.device_profile,undistort=False).prepare((4,4)).metadata)
        bad=dict(valid=False,status='no_face',camera=dict(intrinsics_source='estimated',intrinsics_reason='aspect_ratio_mismatch'))
        for warmup,results in ((0,[bad]),(1,[good,bad])):
            with self.subTest(warmup=warmup):
                engine=Mock(device='cpu');engine.predict.side_effect=results
                factory=Mock(return_value=engine)
                with self.assertRaisesRegex(ComparisonCameraError,'aspect_ratio_mismatch'):
                    compare(settings,[np.ones((4,4,3),dtype=np.uint8)],warmup=warmup,engine_factory=factory)
                factory.assert_called_once();engine.close.assert_called_once()

    def test_closed_camera_setup_failure_releases_without_reading(self):
        for opened,message in ((False,'无法打开'),(True,'synthetic resolution setup failure')):
            with self.subTest(opened=opened):
                camera=Mock();camera.isOpened.return_value=opened
                if opened:camera.set.side_effect=RuntimeError(message)
                with patch('tools.compare_inference.open_camera',return_value=camera), \
                     patch('tools.compare_inference.read_camera_frames') as reader,self.assertRaisesRegex(RuntimeError,message):
                    collect_images(settings_for(),1)
                reader.assert_not_called();camera.release.assert_called_once()

    def test_cli_output_json_stays_under_data_and_summary_uses_report_numbers(self):
        report=dict(results=[dict(requested_device='cpu',actual_device='cpu',num_threads=2,status='measured',
                                 engine_ms=dict(median=12.345,p90=23.456),model_forward_ms=dict(median=1.234,p90=2.345),valid=7,inputs=8),
                             dict(requested_device='mps',num_threads=4,status='skipped',reason='synthetic unavailable')])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);destination=root/'data/nested/result.json'
            with patch('tools.compare_inference.ROOT',root),patch('tools.compare_inference.load_settings',return_value=settings_for()), \
                 patch('tools.compare_inference.collect_images',return_value=[np.ones((4,4,3),dtype=np.uint8)]), \
                 patch('tools.compare_inference.compare',return_value=report),redirect_stdout(io.StringIO()) as output,redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output',str(destination)]),0)
            self.assertEqual(json.loads(destination.read_text()),report)
            self.assertIn('12.345 / 23.456',output.getvalue());self.assertIn('1.234 / 2.345',output.getvalue())
            self.assertIn('7/8',output.getvalue());self.assertIn('skipped',output.getvalue())
            self.assertEqual([p for p in root.rglob('*') if p.is_file()],[destination])
            with patch('tools.compare_inference.ROOT',root):
                for path in (root/'outside.json',root/'data'):
                    with self.assertRaisesRegex(ValueError,'data/'):output_path(path)
                (root/'data/link').symlink_to(root/'outside',target_is_directory=True)
                with self.assertRaisesRegex(ValueError,'data/'):output_path(root/'data/link/result.json')

    def test_bad_camera_conditions_create_no_output_or_empty_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);destination=root/'data/report.json'
            with patch('tools.compare_inference.ROOT',root),patch('tools.compare_inference.load_settings',return_value=settings_for()), \
                 patch('tools.compare_inference.collect_images',side_effect=ComparisonCameraError('synthetic mismatch')), \
                 patch('tools.compare_inference.compare') as measure,redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output',str(destination)]),1)
            measure.assert_not_called();self.assertFalse(destination.exists())


if __name__=='__main__':unittest.main()
