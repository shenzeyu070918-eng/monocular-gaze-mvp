"""Camera coordinates: x camera-right, y down, z towards the user (metres)."""
import numpy as np


def intersect_screen(origin, direction, width_cm, height_cm, top_cm=0.0, horizontal_cm=0.0):
    if width_cm is None or height_cm is None:
        return None
    if not np.isfinite([width_cm, height_cm, top_cm, horizontal_cm]).all() or width_cm <= 0 or height_cm <= 0:
        raise ValueError('屏幕尺寸和摄像头偏移无效')
    origin, direction = np.asarray(origin, float), np.asarray(direction, float)
    if not np.isfinite(origin).all() or not np.isfinite(direction).all() or direction[2] >= -1e-5:
        return None
    distance = -origin[2] / direction[2]
    if distance <= 0:
        return None
    point = origin + distance * direction
    # Camera faces the viewer: its x axis is opposite the display x axis.
    return [(width_cm / 2 + horizontal_cm - point[0] * 100) / width_cm,
            (point[1] * 100 - top_cm) / height_cm]


def fit_affine(samples):
    """Fit only explicitly collected calibration labels, never evaluation labels."""
    raw = np.array([s['raw'] for s in samples], float)
    target = np.array([s['target'] for s in samples], float)
    if len(samples) < 9 or not np.isfinite(raw).all() or not np.isfinite(target).all():
        raise ValueError('有效校准点不足，请重新校准。')
    center, scale = raw.mean(0), raw.std(0)
    if np.any(scale < 0.002):
        raise ValueError('视线变化过小，请保持头部稳定并依次看向各个圆点。')
    design = np.column_stack(((raw-center)/scale, np.ones(len(raw))))
    if np.linalg.matrix_rank(design) < 3 or np.linalg.cond(design) > 1000:
        raise ValueError('校准数据不稳定，请调整光线后重试。')
    coef = np.linalg.lstsq(design, target, rcond=None)[0]
    matrix = np.vstack((coef[:2] / scale[:, None], coef[2] - (center/scale) @ coef[:2]))
    return matrix.tolist()
