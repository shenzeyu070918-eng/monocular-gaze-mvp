"""Device, configuration and tracking tests without a camera or model weights."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import yaml

from gaze_core.config import DEFAULT_CONFIG, ROOT, load_settings, select_device
from gaze_core.engine import Engine


class SettingsTests(unittest.TestCase):
    def test_device_selection(self):
        for available in [False, True]:
            self.assertEqual(select_device('auto', available), 'cuda' if available else 'cpu')
            self.assertEqual(select_device('cpu', available), 'cpu')
        self.assertEqual(select_device('cuda', True), 'cuda')
        with self.assertRaises(RuntimeError): select_device('cuda', False)
        with self.assertRaises(ValueError): select_device('mps', True)

    def test_defaults_match_web_input(self):
        settings = load_settings()
        self.assertEqual(settings.inference.device, 'auto')
        self.assertEqual((settings.camera.width, settings.camera.height), (640, 480))
        self.assertEqual(settings.camera.backend, 'auto')
        self.assertFalse(settings.camera.grayscale)
        self.assertEqual(settings.recording.output_dir, 'data/sessions')
        self.assertEqual((settings.jump.point_count,settings.jump.dwell_ms,settings.jump.min_distance), (20,1500,.35))

    def test_invalid_configuration_rejected(self):
        for section, name, value in [('inference', 'device', 'mps'), ('camera', 'backend', 'other'),
                                      ('camera', 'width', 0), ('camera', 'index', -1),
                                      ('camera', 'grayscale', 'false'), ('camera', 'stop_timeout_s', float('nan')),
                                      ('inference', 'distance_min_m', 2), ('recording','queue_size',0),
                                      ('recording','close_timeout_s',True), ('recording','output_dir',''),
                                      ('recording','request_timeout_s',10),
                                      ('jump','point_count',7), ('jump','dwell_ms',float('nan')),
                                      ('jump','min_distance',2), ('jump','min_distance','invalid')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                data = yaml.safe_load(DEFAULT_CONFIG.read_text())
                data[section][name] = value
                p = Path(directory)/'settings.yaml'
                p.write_text(yaml.safe_dump(data))
                with self.assertRaises(ValueError): load_settings(p)


class EngineTests(unittest.TestCase):
    def build_engine(self, mode='auto', available=False):
        settings = load_settings()
        settings = replace(settings, inference=replace(settings.inference, device=mode))
        backend = SimpleNamespace(_gaze_estimation_model=SimpleNamespace(parameters=lambda: []),
                                  _normalized_camera=object(), detect_faces=Mock(return_value=[]),
                                  estimate_gaze=Mock(), close=Mock())
        factory = Mock(return_value=backend)
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=Mock(return_value=available),
                                                     get_device_name=Mock(return_value='fake CUDA device')),
                                set_num_threads=Mock())
        from omegaconf import OmegaConf
        modules = {
            'torch': torch,
            'ptgaze': SimpleNamespace(__file__=str(ROOT/'vendor/ptgaze/ptgaze/__init__.py')),
            'ptgaze.config_schema': SimpleNamespace(AppConfig=lambda: {}),
            'ptgaze.gaze_estimator': SimpleNamespace(GazeEstimator=factory),
            'ptgaze.common': SimpleNamespace(Camera=Mock()),
            'ptgaze.head_pose_estimation': SimpleNamespace(HeadPoseNormalizer=Mock()),
        }
        with patch.dict(sys.modules, modules), patch.object(OmegaConf, 'structured', return_value={}):
            engine = Engine(settings)
        return engine, backend, factory.call_args.args[0], torch

    def test_video_mode_model_paths_and_real_device(self):
        engine, _, cfg, _ = self.build_engine(available=True)
        self.assertEqual(engine.device, 'cuda')
        self.assertEqual(engine.device_name, 'fake CUDA device')
        self.assertEqual(cfg.device, 'cuda')
        self.assertFalse(cfg.face_detector.mediapipe_static_image_mode)
        self.assertEqual(Path(cfg.gaze_estimator.checkpoint), ROOT/'models/eth-xgaze.safetensors')
        self.assertEqual(Path(cfg.face_detector.mediapipe_model_path), ROOT/'models/face_landmarker.task')

    def test_explicit_cpu_does_not_probe_cuda(self):
        engine, _, _, torch = self.build_engine(mode='cpu', available=True)
        self.assertEqual(engine.device, 'cpu')
        torch.cuda.is_available.assert_not_called()

    def test_prediction_statuses_geometry_and_raw_coordinates(self):
        engine, backend, _, _ = self.build_engine()
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        geometry = load_settings().geometry
        self.assertEqual(engine.predict(image, geometry)['status'], 'no_face')
        landmarks = np.zeros((468, 2))
        face = SimpleNamespace(landmarks=landmarks, center=np.array([0., .1, .5]),
                               gaze_vector=np.array([0., 0., -1.]),
                               head_pose_rot=SimpleNamespace(as_euler=lambda *a, **k: np.zeros(3)))
        backend.detect_faces.return_value = [face]
        self.assertEqual(engine.predict(image, geometry)['status'], 'blink')
        for ids in [[33,160,158,133,153,144], [362,385,387,263,373,380]]:
            landmarks[ids] = [[0,0],[1,1],[2,1],[3,0],[2,-1],[1,-1]]
        result = engine.predict(image, geometry, grayscale=True)
        self.assertTrue(result['valid'])
        self.assertEqual(result['status'], 'tracking')
        np.testing.assert_allclose(result['raw'], [.5,.5])
        face.center[0] = 1
        self.assertLess(engine.predict(image, geometry)['raw'][0], 0)
        face.gaze_vector[2] = 1
        self.assertEqual(engine.predict(image, geometry)['status'], 'geometry_invalid')
        engine.close()
        backend.close.assert_called_once()


class TrackingTests(unittest.TestCase):
    def test_video_timestamps_and_reacquisition_after_no_face(self):
        path = ROOT/'vendor/ptgaze/ptgaze/head_pose_estimation/face_landmark_estimator.py'
        spec = importlib.util.spec_from_file_location('tracking_under_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'ptgaze.common': SimpleNamespace(Face=lambda b, p: p)}):
            spec.loader.exec_module(module)
        tracker = module.LandmarkEstimator.__new__(module.LandmarkEstimator)
        tracker.mode, tracker._mp_static, tracker._mp_timestamp_ms = 'mediapipe', False, -1
        tracker._mp_image_cls = lambda **kwargs: kwargs
        tracker._mp_image_format = 0
        points = [SimpleNamespace(x=.5, y=.5)]*478
        tracker.detector = SimpleNamespace(detect_for_video=Mock(side_effect=[
            SimpleNamespace(face_landmarks=[]), SimpleNamespace(face_landmarks=[points]),
            SimpleNamespace(face_landmarks=[])]))
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with patch.object(module.time, 'perf_counter_ns', side_effect=[1000000,1000000,2000000000]):
            self.assertEqual(tracker.detect_faces(image), [])
            self.assertEqual(len(tracker.detect_faces(image)), 1)
            self.assertEqual(tracker.detect_faces(image), [])
        self.assertEqual([c.args[1] for c in tracker.detector.detect_for_video.call_args_list], [1,2,2000])


if __name__ == '__main__': unittest.main()
