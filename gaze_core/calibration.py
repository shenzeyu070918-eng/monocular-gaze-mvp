"""Chessboard measurements and calibration; this module never stores images."""
import cv2
import numpy as np

from .devices import finite, resolution


def board_points(corners, square_mm):
    cols, rows = resolution(corners, '内角点数')
    if cols < 3 or rows < 3:
        raise ValueError('棋盘格每个方向至少需要 3 个内角点')
    square_mm = finite(square_mm, 'square_mm', positive=True)
    points = np.zeros((cols*rows, 3), dtype=np.float32)
    points[:, :2] = np.mgrid[:cols, :rows].T.reshape(-1, 2)*square_mm
    return points


def view_descriptor(points, corners, size, square_mm):
    width, height = resolution(size)
    points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    object_points = board_points(corners, square_mm)
    if len(points) != len(object_points) or not np.isfinite(points).all():
        raise ValueError('角点数量或数值无效')
    k = np.array([[width, 0, width/2], [0, width, height/2], [0, 0, 1.]], float)
    ok, rvec, _ = cv2.solvePnP(object_points, points, k, np.zeros(5))
    if not ok:
        raise ValueError('无法估计棋盘格姿态，请调整角度')
    rotation, _ = cv2.Rodrigues(rvec)
    # A board can be detected in either orientation: use its plane normal.
    normal = rotation[:, 2]
    if normal[2] < 0:
        normal = -normal
    tilt = np.degrees(np.arctan2(normal[:2], abs(normal[2])))
    area = abs(cv2.contourArea(cv2.convexHull(points))) / (width*height)
    return np.r_[points.mean(axis=0)/[width, height], np.sqrt(area), tilt/90]


def diverse_enough(descriptors, settings):
    if len(descriptors) < settings.views:
        return False
    descriptors = np.asarray(descriptors)
    spans = np.ptp(descriptors, axis=0)
    return bool(np.all(spans[:2] >= settings.min_center_span)
                and np.max(spans[3:]*90) >= settings.min_tilt_span_deg)


def calibrate_views(views, corners, square_mm, size):
    size = resolution(size)
    points = board_points(corners, square_mm)
    if len(views) < 3:
        raise ValueError('至少需要 3 张有效视图；实际数量按配置要求采集')
    images = []
    for view in views:
        view = np.asarray(view, dtype=np.float32)
        if view.size != len(points)*2 or not np.isfinite(view).all():
            raise ValueError('标定角点无效')
        images.append(view.reshape(-1, 1, 2))
    error, matrix, distortion, rotations, translations = cv2.calibrateCamera(
        [points.copy() for _ in views], images, size, None, None)
    errors = []
    for observed, rotation, translation in zip(images, rotations, translations):
        projected, _ = cv2.projectPoints(points, rotation, translation, matrix, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((observed-projected)**2, axis=2)))))
    if not np.isfinite(matrix).all() or not np.isfinite(distortion).all() or not np.isfinite(error):
        raise ValueError('标定结果无效，请重新采集')
    return dict(matrix=matrix.tolist(), distortion=distortion.reshape(-1).tolist(), resolution=list(size),
                reprojection_error_px=float(error)), errors
