from pathlib import Path
import time
import numpy as np
from .geometry import intersect_screen
from .config import load_settings, select_device
from .camera import CameraAdapter

ROOT = Path(__file__).resolve().parents[1]


class Engine:
    def __init__(self, settings=None):
        # Heavy dependencies are loaded only when an engine is explicitly created.
        import cv2
        import torch
        import ptgaze
        from omegaconf import OmegaConf
        from ptgaze.config_schema import AppConfig
        from ptgaze.gaze_estimator import GazeEstimator
        from ptgaze.common import Camera
        from ptgaze.head_pose_estimation import HeadPoseNormalizer

        self.settings = settings or load_settings()
        options = self.settings.inference
        self.device = select_device(options.device, options.device != 'cpu' and torch.cuda.is_available())
        self.device_name = torch.cuda.get_device_name(0) if self.device == 'cuda' else 'CPU'
        self._cv2, self._Camera, self._Normalizer = cv2, Camera, HeadPoseNormalizer
        torch.set_num_threads(options.cpu_threads)
        package = Path(ptgaze.__file__).resolve().parent
        cfg = OmegaConf.merge(OmegaConf.structured(AppConfig), OmegaConf.load(package/'data/configs/eth-xgaze.yaml'))
        cfg.PACKAGE_ROOT = str(package)
        cfg.device = self.device
        cfg.face_detector.mediapipe_model_path = str(ROOT/'models/face_landmarker.task')
        cfg.face_detector.mediapipe_max_num_faces = options.max_faces
        cfg.face_detector.mediapipe_static_image_mode = False
        cfg.gaze_estimator.checkpoint = str(ROOT/'models/eth-xgaze.safetensors')
        cfg.gaze_estimator.use_dummy_camera_params = True
        cfg.gaze_estimator.dummy_camera_size = [self.settings.camera.width, self.settings.camera.height]
        cfg.gaze_estimator.normalized_camera_distance = options.normalized_camera_distance_m
        self.model = GazeEstimator(cfg)
        self.cameras = CameraAdapter(self.settings.device_profile, undistort=self.settings.camera.undistort,
                                     alpha=self.settings.camera.undistort_alpha)
        self.camera_key = None
        self.current_camera = None
        self.parameters = sum(p.numel() for p in self.model._gaze_estimation_model.parameters())

    def camera_metadata(self, size=None, geometry=None):
        size = size or (self.settings.camera.width, self.settings.camera.height)
        geometry = geometry or self.settings.geometry
        return self.cameras.prepare(size, geometry.focal_scale).metadata

    def predict(self, image, geometry, grayscale=False):
        start = time.perf_counter()
        cv2, options = self._cv2, self.settings.inference
        h, w = image.shape[:2]
        prepared = self.cameras.prepare((w, h), geometry.focal_scale)
        if prepared is not self.camera_key:
            camera = self._Camera(w, h, prepared.matrix, prepared.distortion.reshape(-1, 1))
            self.model.camera = camera
            self.model._head_pose_normalizer = self._Normalizer(camera, self.model._normalized_camera, options.normalized_camera_distance_m)
            self.camera_key = prepared
        self.current_camera = prepared.metadata
        image = self.cameras.transform(image, prepared)
        if grayscale:
            image = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        faces = self.model.detect_faces(image)
        if not faces:
            return {'valid': False, 'status': 'no_face', 'camera': prepared.metadata, 'inference_ms': (time.perf_counter()-start)*1000}
        face = faces[0]
        p = face.landmarks
        def ear(ids):
            a,b,c,d,e,f = p[ids]
            return (np.linalg.norm(b-f)+np.linalg.norm(c-e))/(2*max(np.linalg.norm(a-d), 1e-6))
        openness = (ear([33,160,158,133,153,144])+ear([362,385,387,263,373,380]))/2
        preview_points = self.cameras.preview_points(p[[33,133,159,145,362,263,386,374,1]], prepared)
        preview = [[float(v) for v in point] for point in preview_points / [w,h]]
        if openness < options.blink_threshold:
            return {'valid': False, 'status': 'blink', 'landmarks': preview, 'camera': prepared.metadata, 'inference_ms': (time.perf_counter()-start)*1000}
        self.model.estimate_gaze(image, face)
        raw = intersect_screen(face.center, face.gaze_vector, geometry.width_cm, geometry.height_cm, geometry.top_cm, geometry.horizontal_cm)
        valid = raw is not None and np.isfinite(raw).all() and options.distance_min_m < face.center[2] < options.distance_max_m
        return {'valid': bool(valid), 'status': 'tracking' if valid else ('screen_unconfigured' if geometry.width_cm is None else 'geometry_invalid'),
                'raw': [float(v) for v in raw] if valid else None, 'landmarks': preview,
                'origin_m': [float(v) for v in face.center], 'gaze_vector': [float(v) for v in face.gaze_vector],
                'head_degrees': [float(v) for v in face.head_pose_rot.as_euler('xyz', degrees=True)],
                'distance_cm': float(face.center[2]*100), 'eye_openness': float(openness),
                'camera': prepared.metadata, 'inference_ms': (time.perf_counter()-start)*1000}

    def close(self):
        self.model.close()
