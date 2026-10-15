from pathlib import Path
import time
import cv2
import numpy as np
import torch
import ptgaze
from omegaconf import OmegaConf
from ptgaze.config_schema import AppConfig
from ptgaze.gaze_estimator import GazeEstimator
from ptgaze.common import Camera
from ptgaze.head_pose_estimation import HeadPoseNormalizer
from .geometry import intersect_screen

ROOT = Path(__file__).resolve().parents[1]


class Engine:
    def __init__(self):
        torch.set_num_threads(4)
        package = Path(ptgaze.__file__).resolve().parent
        cfg = OmegaConf.merge(OmegaConf.structured(AppConfig), OmegaConf.load(package/'data/configs/eth-xgaze.yaml'))
        cfg.PACKAGE_ROOT = str(package)
        cfg.face_detector.mediapipe_model_path = str(ROOT/'models/face_landmarker.task')
        cfg.face_detector.mediapipe_max_num_faces = 1
        cfg.face_detector.mediapipe_static_image_mode = True
        cfg.gaze_estimator.checkpoint = str(ROOT/'models/eth-xgaze.safetensors')
        cfg.gaze_estimator.use_dummy_camera_params = True
        cfg.gaze_estimator.dummy_camera_size = [640, 480]
        self.model = GazeEstimator(cfg)
        self.camera_key = None
        self.parameters = sum(p.numel() for p in self.model._gaze_estimation_model.parameters())

    def predict(self, image, geometry, grayscale=False):
        start = time.perf_counter()
        h, w = image.shape[:2]
        key = (w, h, geometry.focal_scale)
        if key != self.camera_key:
            focal = w * geometry.focal_scale
            camera = Camera(w, h, np.array([[focal,0,w/2],[0,focal,h/2],[0,0,1.]], float), np.zeros((5,1)))
            self.model.camera = camera
            self.model._head_pose_normalizer = HeadPoseNormalizer(camera, self.model._normalized_camera, 0.6)
            self.camera_key = key
        if grayscale:
            image = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        faces = self.model.detect_faces(image)
        if not faces:
            return {'valid': False, 'status': 'no_face', 'inference_ms': (time.perf_counter()-start)*1000}
        face = faces[0]
        p = face.landmarks
        def ear(ids):
            a,b,c,d,e,f = p[ids]
            return (np.linalg.norm(b-f)+np.linalg.norm(c-e))/(2*max(np.linalg.norm(a-d), 1e-6))
        openness = (ear([33,160,158,133,153,144])+ear([362,385,387,263,373,380]))/2
        preview = (p[[33,133,159,145,362,263,386,374,1]] / [w,h]).tolist()
        if openness < 0.14:
            return {'valid': False, 'status': 'blink', 'landmarks': preview, 'inference_ms': (time.perf_counter()-start)*1000}
        self.model.estimate_gaze(image, face)
        raw = intersect_screen(face.center, face.gaze_vector, geometry.width_cm, geometry.height_cm, geometry.top_cm)
        valid = raw is not None and np.isfinite(raw).all() and 0.15 < face.center[2] < 1.5
        return {'valid': bool(valid), 'status': 'tracking' if valid else 'geometry_invalid',
                'raw': raw if valid else None, 'landmarks': preview,
                'origin_m': face.center.tolist(), 'gaze_vector': face.gaze_vector.tolist(),
                'head_degrees': face.head_pose_rot.as_euler('xyz', degrees=True).tolist(),
                'distance_cm': float(face.center[2]*100), 'eye_openness': float(openness),
                'inference_ms': (time.perf_counter()-start)*1000}

    def close(self):
        self.model.close()
