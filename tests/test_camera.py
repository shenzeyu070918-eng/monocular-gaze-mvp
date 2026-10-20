"""Rectification and fallback tests use synthetic matrices and in-memory pixels."""
from dataclasses import replace
import importlib.util
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from gaze_core.camera import CameraAdapter
from gaze_core.config import ROOT, load_settings
from gaze_core.devices import parse_device
from test_devices import synthetic_device
import test_engine


class CameraTests(unittest.TestCase):
    def test_rectification_uses_new_matrix_zero_distortion_and_cached_maps(self):
        profile = parse_device(synthetic_device(), 'synthetic-test.yaml')
        adapter = CameraAdapter(profile)
        with patch.object(cv2, 'initUndistortRectifyMap', wraps=cv2.initUndistortRectifyMap) as maps:
            first = adapter.prepare((640,480))
            self.assertIs(adapter.prepare((640,480)), first)
            self.assertIs(adapter.prepare((640,480), 2), first)  # Focal slider cannot alter calibrated K.
            maps.assert_called_once()
            expected, _ = cv2.getOptimalNewCameraMatrix(np.array(profile.matrix), np.array(profile.distortion), (640,480), 0, (640,480))
            np.testing.assert_allclose(first.matrix, expected)
            np.testing.assert_allclose(maps.call_args.args[3], expected)
            np.testing.assert_allclose(first.distortion, np.zeros(5))
            image = np.arange(640*480*3,dtype=np.uint8).reshape(480,640,3)
            out = adapter.transform(image, first)
            np.testing.assert_array_equal(out, cv2.remap(image,*first.maps,cv2.INTER_LINEAR))
            self.assertEqual(out.shape, image.shape)
            self.assertTrue(first.metadata['undistort_applied'])
            self.assertEqual(first.metadata['device_file'], 'synthetic-test.yaml')
            with self.assertWarnsRegex(UserWarning, '等比例缩放'):
                adapter.prepare((320,240))
            self.assertIs(adapter.prepare((640,480)), first)
            self.assertEqual(maps.call_count, 2)

    def test_disabled_retains_calibrated_matrix_and_coefficients(self):
        profile = parse_device(synthetic_device(), 'test.yaml')
        adapter = CameraAdapter(profile, undistort=False)
        result = adapter.prepare((640,480))
        self.assertIsNone(result.maps)
        np.testing.assert_allclose(result.matrix, profile.matrix)
        np.testing.assert_allclose(result.distortion, profile.distortion)
        self.assertFalse(result.metadata['undistort_enabled'])
        self.assertFalse(result.metadata['undistort_applied'])
        image = np.zeros((480,640,3),np.uint8)
        self.assertIs(adapter.transform(image,result), image)

    def test_aspect_mismatch_falls_back_and_caches_warning_per_resolution(self):
        profile = parse_device(synthetic_device(), 'test.yaml')
        adapter = CameraAdapter(profile)
        with self.assertWarnsRegex(UserWarning, 'aspect_ratio_mismatch'):
            result = adapter.prepare((640,360),1.1)
        self.assertEqual(result.metadata['intrinsics_source'], 'estimated')
        self.assertEqual(result.metadata['intrinsics_reason'], 'aspect_ratio_mismatch')
        self.assertFalse(result.metadata['undistort_applied'])
        self.assertEqual(result.metadata['device_file'], 'test.yaml')
        np.testing.assert_allclose(result.matrix, [[704,0,320],[0,704,180],[0,0,1]])
        np.testing.assert_allclose(result.distortion,np.zeros(5))
        self.assertIs(adapter.prepare((640,360),1.1), result)
        with self.assertWarns(UserWarning):
            other = adapter.prepare((640,360),2)
        self.assertEqual(other.matrix[0,0],1280)
        compatible = adapter.prepare((640,480))
        self.assertEqual(compatible.metadata['intrinsics_source'], 'calibrated')

    def test_preview_landmarks_are_projected_back_to_original_pixels(self):
        profile = parse_device(synthetic_device(), 'test.yaml')
        prepared = CameraAdapter(profile).prepare((640,480))
        original = np.array([[[80.,70.]],[[400.,300.]]])
        rectified = cv2.undistortPoints(original, np.array(profile.matrix), np.array(profile.distortion), P=prepared.matrix)
        preview = CameraAdapter.preview_points(rectified.reshape(-1,2), prepared)
        np.testing.assert_allclose(preview, original.reshape(-1,2), atol=1e-5)

    def test_engine_passes_rectified_image_new_camera_and_provenance(self):
        engine, backend, _, _ = test_engine.EngineTests().build_engine()
        profile = parse_device(synthetic_device(), 'engine-test.yaml')
        engine.cameras = CameraAdapter(profile)
        engine.settings = replace(engine.settings, device_profile=profile)
        image = np.ones((480,640,3),np.uint8)
        result = engine.predict(image,load_settings(use_local=False).geometry)
        camera = engine._Camera.call_args.args
        prepared = engine.cameras.prepare((640,480))
        np.testing.assert_allclose(camera[2],prepared.matrix)
        np.testing.assert_allclose(camera[3],np.zeros((5,1)))
        np.testing.assert_array_equal(backend.detect_faces.call_args.args[0],engine.cameras.transform(image,prepared))
        self.assertEqual(result['camera']['intrinsics_source'],'calibrated')
        self.assertEqual(engine.current_camera,result['camera'])

    def test_head_pose_uses_coefficients_corresponding_to_input_image(self):
        path = ROOT/'vendor/ptgaze/ptgaze/common/face_model.py'
        spec = importlib.util.spec_from_file_location('synthetic_face.model',path)
        module = importlib.util.module_from_spec(spec)
        # Relative imports need a small isolated package; no model dependency.
        class Pose:
            def __init__(self,**kwargs):
                self.__dict__.update(kwargs)
        with patch.dict(sys.modules,{'synthetic_face.camera':SimpleNamespace(Camera=object),
                                    'synthetic_face.results':SimpleNamespace(FaceCenters=object,HeadPose=Pose),
                                    'ptgaze.mode':SimpleNamespace(GazeEstimationMode=object)}):
            spec.loader.exec_module(module)
        fake = SimpleNamespace(LANDMARKS=np.zeros((6,3)))
        for distortion in (np.zeros(5),np.array([.1,0,0,0,0])):
            camera = SimpleNamespace(camera_matrix=np.eye(3),dist_coefficients=distortion)
            with patch.object(module.cv2,'solvePnP',return_value=(True,np.zeros(3),np.array([0,0,1.]))) as solve:
                module.FaceModel.estimate_head_pose(fake,np.zeros((6,2)),camera)
            np.testing.assert_array_equal(solve.call_args.args[3],distortion)


if __name__ == '__main__':
    unittest.main()
