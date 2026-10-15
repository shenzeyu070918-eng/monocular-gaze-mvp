"""Shared configuration without UI or model imports."""
from dataclasses import dataclass
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'config/default.yaml'


@dataclass(frozen=True)
class InferenceSettings:
    device: str
    cpu_threads: int
    max_faces: int
    blink_threshold: float
    distance_min_m: float
    distance_max_m: float
    normalized_camera_distance_m: float


@dataclass(frozen=True)
class CameraSettings:
    index: int
    width: int
    height: int
    backend: str
    grayscale: bool
    startup_timeout_s: float
    stop_timeout_s: float


@dataclass(frozen=True)
class ScreenGeometry:
    width_cm: float
    height_cm: float
    top_cm: float
    focal_scale: float


@dataclass(frozen=True)
class Settings:
    inference: InferenceSettings
    camera: CameraSettings
    geometry: ScreenGeometry


def select_device(requested, cuda_available):
    if requested not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('inference.device 必须是 auto、cpu 或 cuda')
    if requested == 'cuda' and not cuda_available:
        raise RuntimeError('已指定 CUDA，但当前 PyTorch 无法使用 CUDA；请检查 cu128 安装及显卡驱动，或改用 cpu/auto。')
    return 'cuda' if requested != 'cpu' and cuda_available else 'cpu'


def load_settings(path=DEFAULT_CONFIG):
    import yaml
    with Path(path).open(encoding='utf-8') as source:
        data = yaml.safe_load(source)
    if not isinstance(data, dict) or set(data) != {'inference', 'camera', 'geometry'}:
        raise ValueError('配置必须包含 inference、camera、geometry 三部分')
    settings = Settings(InferenceSettings(**data['inference']), CameraSettings(**data['camera']),
                        ScreenGeometry(**data['geometry']))
    i, c, g = settings.inference, settings.camera, settings.geometry
    # Validate the request separately from actual device availability.
    if i.device not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('inference.device 必须是 auto、cpu 或 cuda')
    for name, value in [('cpu_threads', i.cpu_threads), ('max_faces', i.max_faces),
                        ('width', c.width), ('height', c.height)]:
        if type(value) is not int or value <= 0:
            raise ValueError(f'{name} 必须是正整数')
    if type(c.index) is not int or c.index < 0:
        raise ValueError('camera.index 必须是非负整数')
    if c.backend not in {'auto', 'dshow'} or type(c.grayscale) is not bool:
        raise ValueError('camera.backend 必须是 auto 或 dshow，grayscale 必须是布尔值')
    for name, value in [('blink_threshold', i.blink_threshold), ('distance_min_m', i.distance_min_m),
                        ('distance_max_m', i.distance_max_m),
                        ('normalized_camera_distance_m', i.normalized_camera_distance_m),
                        ('startup_timeout_s', c.startup_timeout_s), ('stop_timeout_s', c.stop_timeout_s),
                        ('width_cm', g.width_cm), ('height_cm', g.height_cm), ('focal_scale', g.focal_scale)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} 必须是有限正数')
    if not isinstance(g.top_cm, (int, float)) or not math.isfinite(g.top_cm):
        raise ValueError('top_cm 必须是有限数值')
    if i.distance_min_m >= i.distance_max_m:
        raise ValueError('distance_min_m 必须小于 distance_max_m')
    return settings
