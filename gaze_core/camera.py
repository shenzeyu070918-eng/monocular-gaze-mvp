"""Resolution-specific intrinsics and cached rectification shared by both inputs."""
from dataclasses import dataclass
import threading
import warnings

import numpy as np

from .devices import scale_intrinsics, resolution, finite


@dataclass(frozen=True)
class PreparedCamera:
    metadata: dict
    matrix: np.ndarray
    distortion: np.ndarray
    maps: tuple | None


class CameraAdapter:
    def __init__(self, profile=None, *, undistort=True, alpha=0.0):
        self.profile, self.undistort, self.alpha = profile, undistort, alpha
        self._cache = {}
        self._lock = threading.RLock()

    def prepare(self, size, focal_scale=1.0):
        import cv2
        width, height = resolution(size)
        focal_scale = finite(focal_scale, 'focal_scale', positive=True)
        compatible = self.profile is not None and width*self.profile.resolution[1] == height*self.profile.resolution[0]
        key = (width, height, None if compatible else focal_scale)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            profile, reason = self.profile, 'no_device_file'
            if profile:
                try:
                    k = scale_intrinsics(profile.matrix, profile.resolution, size)
                    distortion = np.asarray(profile.distortion, dtype=float)
                    reason = None
                except ValueError:
                    reason = 'aspect_ratio_mismatch'
            if reason:
                focal = width * focal_scale
                k = np.array([[focal, 0, width/2], [0, focal, height/2], [0, 0, 1.]], dtype=float)
                distortion = np.zeros(5)
            scaled = reason is None and tuple(size) != profile.resolution
            if reason == 'aspect_ratio_mismatch':
                warnings.warn(f'相机分辨率 {width}×{height} 与标定 {profile.resolution} 长宽比不同；回退估计内参（aspect_ratio_mismatch）。', stacklevel=2)
            elif scaled:
                warnings.warn(f'相机内参已从 {profile.resolution} 等比例缩放至 {width}×{height}；请确认相机视场未改变。', stacklevel=2)
            applied = self.undistort and reason is None
            new_k, new_distortion, maps = k.copy(), distortion.copy(), None
            if applied:
                new_k, _ = cv2.getOptimalNewCameraMatrix(k, distortion, (width, height), self.alpha, (width, height))
                maps = cv2.initUndistortRectifyMap(k, distortion, None, new_k, (width, height), cv2.CV_32FC1)
                new_distortion = np.zeros(5)
            metadata = dict(width_px=width, height_px=height, intrinsics_source='estimated' if reason else 'calibrated',
                            intrinsics_reason=reason, device_file=profile.filename if profile else None,
                            calibration_resolution=list(profile.resolution) if profile else None,
                            reprojection_error_px=profile.reprojection_error_px if profile else None,
                            matrix=new_k.tolist(), distortion=new_distortion.tolist(),
                            input_matrix=k.tolist(), input_distortion=distortion.tolist(), scaled=scaled,
                            undistort_enabled=self.undistort, undistort_applied=applied)
            prepared = PreparedCamera(metadata, new_k, new_distortion, maps)
            self._cache[key] = prepared
            return prepared

    @staticmethod
    def transform(image, prepared):
        if prepared.maps is None:
            return image
        import cv2
        return cv2.remap(image, *prepared.maps, interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def preview_points(points, prepared):
        """Return landmarks to original video pixels for the unrectified browser preview."""
        if prepared.maps is None:
            return points
        import cv2
        rays = np.column_stack((points, np.ones(len(points)))) @ np.linalg.inv(prepared.matrix).T
        projected, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3),
                                       np.asarray(prepared.metadata['input_matrix']),
                                       np.asarray(prepared.metadata['input_distortion']))
        return projected.reshape(-1, 2)
