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
class RecordingSettings:
    output_dir: str
    queue_size: int
    close_timeout_s: float
    request_timeout_s: float = 15.0


@dataclass(frozen=True)
class JumpSettings:
    point_count: int
    dwell_ms: float
    min_distance: float
    margin: float


@dataclass(frozen=True)
class Settings:
    inference: InferenceSettings
    camera: CameraSettings
    geometry: ScreenGeometry
    recording: RecordingSettings
    jump: JumpSettings


def select_device(requested, cuda_available):
    if requested not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('inference.device 必须是 auto、cpu 或 cuda')
    if requested == 'cuda' and not cuda_available:
        raise RuntimeError('已指定 CUDA，但当前 PyTorch 无法使用 CUDA；请检查 CUDA 版 PyTorch 安装及显卡驱动，或改用 cpu/auto。')
    return 'cuda' if requested != 'cpu' and cuda_available else 'cpu'


def load_settings(path=DEFAULT_CONFIG):
    import yaml
    with Path(path).open(encoding='utf-8') as source:
        data = yaml.safe_load(source)
    if not isinstance(data, dict) or set(data) != {'inference', 'camera', 'geometry', 'recording', 'jump'}:
        raise ValueError('配置必须包含 inference、camera、geometry、recording、jump 五部分')
    settings = Settings(InferenceSettings(**data['inference']), CameraSettings(**data['camera']),
                        ScreenGeometry(**data['geometry']), RecordingSettings(**data['recording']), JumpSettings(**data['jump']))
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
    r, j = settings.recording, settings.jump
    if not isinstance(r.output_dir, str) or not r.output_dir.strip():
        raise ValueError('recording.output_dir 必须是非空路径')
    if type(r.queue_size) is not int or r.queue_size <= 0:
        raise ValueError('recording.queue_size 必须是正整数')
    if type(r.close_timeout_s) not in (int, float) or not math.isfinite(r.close_timeout_s) or r.close_timeout_s <= 0:
        raise ValueError('recording.close_timeout_s 必须是有限正数')
    if type(r.request_timeout_s) not in (int, float) or not math.isfinite(r.request_timeout_s) or r.request_timeout_s <= r.close_timeout_s:
        raise ValueError('recording.request_timeout_s 必须是大于 close_timeout_s 的有限正数')
    if type(j.point_count) is not int or j.point_count < 8:
        raise ValueError('jump.point_count 至少为 8，以包含四角及四边锚点')
    if type(j.dwell_ms) not in (int, float) or not math.isfinite(j.dwell_ms) or j.dwell_ms <= 0:
        raise ValueError('jump.dwell_ms 必须是有限正数')
    if type(j.min_distance) not in (int, float) or not math.isfinite(j.min_distance) or not 0 < j.min_distance <= math.sqrt(2):
        raise ValueError('jump.min_distance 超出全屏归一化坐标可用范围')
    if type(j.margin) not in (int, float) or not math.isfinite(j.margin) or not 0 < j.margin < .5:
        raise ValueError('jump.margin 必须大于 0 且小于 0.5')
    if j.min_distance > math.sqrt(2)*(1-2*j.margin):
        raise ValueError('jump.min_distance 超出边距内区域的对角线')
    return settings
