"""Unicode/space model paths: repository loaders, never a modified site-package."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import numpy as np
from gaze_core.config import ROOT
from gaze_core.galaxy import GalaxySettings
from overlay.galaxy_processor import HandModel


def face_loader():
    # Exercise the vendored fix, not an older copy installed in .venv.
    path=ROOT/'vendor/ptgaze/ptgaze/head_pose_estimation/face_landmark_estimator.py'
    spec=importlib.util.spec_from_file_location('face_model_path_under_test',path)
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'ptgaze.common':SimpleNamespace(Face=Mock())}):
        spec.loader.exec_module(module)
    return module.LandmarkEstimator


def face_config(path):
    return SimpleNamespace(face_detector=SimpleNamespace(mode='mediapipe',mediapipe_model_path=str(path),
        mediapipe_static_image_mode=False,mediapipe_max_num_faces=1))


class ModelBufferTests(unittest.TestCase):
    def test_face_and_hand_read_unicode_space_paths_and_supply_exact_bytes_only(self):
        base=Mock(side_effect=lambda **kwargs:SimpleNamespace(**kwargs))
        vision=SimpleNamespace(RunningMode=SimpleNamespace(IMAGE='image',VIDEO='video'),
            FaceLandmarkerOptions=lambda **kwargs:SimpleNamespace(**kwargs),
            HandLandmarkerOptions=lambda **kwargs:SimpleNamespace(**kwargs),
            FaceLandmarker=Mock(),HandLandmarker=Mock())
        mp=SimpleNamespace(Image=Mock(),ImageFormat=SimpleNamespace(SRGB='rgb'),tasks=SimpleNamespace(BaseOptions=base))
        modules={'mediapipe':mp,'mediapipe.tasks.python':SimpleNamespace(BaseOptions=base,vision=vision)}
        with tempfile.TemporaryDirectory(prefix='视线 模型 ') as directory:
            root=Path(directory);folder=root/'中文 项目'/'模型 文件';folder.mkdir(parents=True)
            face=folder/'face_landmarker.task';face_bytes=b'face\x00\xff';face.write_bytes(face_bytes)
            hand=folder/'hand_landmarker.task';hand_bytes=b'hand\x00\xfe';hand.write_bytes(hand_bytes)
            with patch.dict(sys.modules,modules):
                estimator=face_loader()(face_config(face))
                try:
                    self.assertEqual(base.call_args.kwargs,{'model_asset_buffer':face_bytes})
                    options=vision.FaceLandmarker.create_from_options.call_args.args[0]
                    self.assertEqual(options.base_options.model_asset_buffer,face_bytes)
                    self.assertEqual(options.running_mode,'video')
                finally:estimator.close()
                # Both absolute and ROOT-relative configured hand paths work.
                for path in (hand,hand.relative_to(root)):
                    with patch('overlay.galaxy_processor.ROOT',root):
                        model=HandModel(replace(GalaxySettings(),hand_model=str(path)))
                    try:
                        self.assertEqual(base.call_args.kwargs,{'model_asset_buffer':hand_bytes})
                        options=vision.HandLandmarker.create_from_options.call_args.args[0]
                        self.assertEqual(options.base_options.model_asset_buffer,hand_bytes)
                        self.assertEqual((options.running_mode,options.num_hands),('video',2))
                    finally:model.close()
        vision.FaceLandmarker.create_from_options.return_value.close.assert_called_once()
        self.assertEqual(vision.HandLandmarker.create_from_options.return_value.close.call_count,2)

class NativeModelPathTests(unittest.TestCase):
    """Optional installed weights; synthetic black frames, no physical camera."""
    @unittest.skipUnless((ROOT/'models/face_landmarker.task').is_file(),'local face model not installed')
    def test_real_face_model_loads_from_unicode_space_path(self):
        with tempfile.TemporaryDirectory(prefix='视线 原生模型 ') as directory:
            path=Path(directory)/'人脸 模型.task'
            path.write_bytes((ROOT/'models/face_landmarker.task').read_bytes())
            estimator=face_loader()(face_config(path))
            try:self.assertEqual(estimator.detect_faces(np.zeros((480,640,3),dtype=np.uint8)),[])
            finally:estimator.close()

    @unittest.skipUnless((ROOT/'models/hand_landmarker.task').is_file(),'local hand model not installed')
    def test_real_hand_model_loads_from_unicode_space_path(self):
        with tempfile.TemporaryDirectory(prefix='视线 原生模型 ') as directory:
            path=Path(directory)/'手势 模型.task'
            path.write_bytes((ROOT/'models/hand_landmarker.task').read_bytes())
            model=HandModel(replace(GalaxySettings(),hand_model=str(path)))
            try:self.assertEqual(model.detect(np.zeros((480,640,3),dtype=np.uint8),1_000_000),[])
            finally:model.close()

    def test_safetensors_roundtrip_supports_unicode_space_path(self):
        import torch
        from safetensors.torch import load_file,save_file
        with tempfile.TemporaryDirectory(prefix='视线 权重 ') as directory:
            path=Path(directory)/'视线 模型.safetensors';expected=torch.tensor([1.,2.])
            save_file({'weight':expected},path)
            torch.testing.assert_close(load_file(path)['weight'],expected)


if __name__=='__main__':unittest.main()
