"""Measured device geometry and OpenCV pinhole calibration, without model imports."""
from dataclasses import dataclass
from datetime import datetime
from numbers import Real, Integral
from pathlib import Path
import re

import numpy as np
import yaml


def finite(value, name, *, positive=False, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f'{name} 必须是有限实数')
    value = float(value)
    if not np.isfinite(value):
        raise ValueError(f'{name} 超出有限浮点数范围')
    if positive and value <= 0:
        raise ValueError(f'{name} 必须为正数')
    return value


def resolution(value, name='resolution', *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2 or any(
            isinstance(v, bool) or not isinstance(v, Integral) or v <= 0 for v in value):
        raise ValueError(f'{name} 必须为两个正整数 [宽, 高]')
    return tuple(int(v) for v in value)


def device_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w-]{1,64}', value, flags=re.UNICODE):
        raise ValueError('设备名只允许字母、数字、汉字、下划线和连字符（最多 64 字符）')
    return value


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    filename: str
    matrix: tuple
    distortion: tuple
    resolution: tuple
    reprojection_error_px: float
    calibrated_at: str
    width_cm: float | None
    height_cm: float | None
    measured: bool
    screen_resolution: tuple | None
    horizontal_cm: float
    top_cm: float


def parse_device(data, filename):
    if not isinstance(data, dict) or set(data) != {'schema_version', 'name', 'camera', 'screen', 'camera_offset_cm'}:
        raise ValueError('设备文件必须包含 schema_version、name、camera、screen、camera_offset_cm')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('设备文件 schema_version 必须为 1')
    name = device_name(data['name'])
    camera, screen, offset = data['camera'], data['screen'], data['camera_offset_cm']
    if not isinstance(camera, dict) or set(camera) != {'matrix', 'distortion', 'resolution', 'reprojection_error_px', 'calibrated_at'}:
        raise ValueError('设备 camera 字段不完整或含未知字段')
    matrix = camera['matrix']
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 3 or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in matrix):
        raise ValueError('camera.matrix 必须为 3×3')
    matrix = tuple(tuple(finite(v, 'camera.matrix') for v in row) for row in matrix)
    k = np.asarray(matrix)
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]) or k[0, 1] != 0 or k[1, 0] != 0:
        raise ValueError('相机矩阵必须为 OpenCV 正焦距、零斜切的针孔模型')
    distortion = camera['distortion']
    if not isinstance(distortion, (tuple, list)) or len(distortion) != 5:
        raise ValueError('camera.distortion 必须为 [k1, k2, p1, p2, k3]')
    distortion = tuple(finite(v, 'camera.distortion') for v in distortion)
    error = finite(camera['reprojection_error_px'], 'reprojection_error_px')
    if error < 0:
        raise ValueError('重投影误差不能为负数')
    stamp = camera['calibrated_at']
    if not isinstance(stamp, str):
        raise ValueError('calibrated_at 必须为 ISO 日期时间')
    try:
        datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('calibrated_at 必须为 ISO 日期时间') from exc
    if not isinstance(screen, dict) or set(screen) != {'width_cm', 'height_cm', 'measured', 'resolution'}:
        raise ValueError('设备 screen 字段不完整或含未知字段')
    width = finite(screen['width_cm'], 'screen.width_cm', positive=True, nullable=True)
    height = finite(screen['height_cm'], 'screen.height_cm', positive=True, nullable=True)
    if type(screen['measured']) is not bool or (width is None) != (height is None) or (screen['measured'] and width is None):
        raise ValueError('实测尺寸必须同时提供有效的宽高并明确 measured')
    if not isinstance(offset, dict) or set(offset) != {'horizontal', 'vertical'}:
        raise ValueError('camera_offset_cm 必须包含 horizontal 和 vertical')
    return DeviceProfile(name, str(filename), matrix, distortion, resolution(camera['resolution']), error, stamp,
                         width, height, screen['measured'], resolution(screen['resolution'], nullable=True),
                         finite(offset['horizontal'], 'horizontal'), finite(offset['vertical'], 'vertical'))


def load_device(path):
    path = Path(path)
    with path.open(encoding='utf-8') as stream:
        return parse_device(yaml.safe_load(stream), path.name)


def scale_intrinsics(matrix, calibrated_size, actual_size):
    """Only proportional resizing is supported; cropping/FOV changes need recalibration."""
    cw, ch = resolution(calibrated_size)
    width, height = resolution(actual_size)
    if width * ch != height * cw:
        raise ValueError('aspect_ratio_mismatch')
    result = np.asarray(matrix, dtype=float).copy()
    result[0, :] *= width / cw
    result[1, :] *= height / ch
    return result
