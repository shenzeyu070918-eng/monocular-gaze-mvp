from dataclasses import dataclass
import math

@dataclass(frozen=True)
class AttentionSettings:
    capture_hz: float = 2.
    stable_ms: float = 750.
    min_scene_interval_ms: float = 1000.
    max_unstable_ms: float = 4000.
    thumbnail_width: int = 320
    tile_px: int = 16
    pixel_delta: float = 12.
    min_changed_area: float = .025
    repeat_samples: int = 6
    repeat_count: int = 3
    max_dynamic_area: float = .35
    max_disk_mb: int = 1024
    queue_size: int = 256
    hide_ring: bool = True
    excluded_apps: tuple = ('1Password','微信','WeChat')
    output_dir: str = 'data/attention'
    manual_aoi: str | None = None
    ocr_languages: tuple = ('zh-Hans','en-US')
    scroll_min_confidence: float = .25
    scroll_max_horizontal_px: float = 12.
    aoi_merge_gap_sigma: float = .5
    soft_grid_width: int = 320

    def __post_init__(self):
        for key in ('capture_hz','stable_ms','min_scene_interval_ms','max_unstable_ms','pixel_delta','scroll_max_horizontal_px','aoi_merge_gap_sigma'):
            v=getattr(self,key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError('attention.'+key+' 必须为有限正数')
        if self.capture_hz>5:raise ValueError('attention.capture_hz 不得超过5')
        if self.max_unstable_ms<self.stable_ms:raise ValueError('不稳定上限不得小于稳定时间')
        for key in ('thumbnail_width','tile_px','repeat_samples','repeat_count','max_disk_mb','queue_size','soft_grid_width'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError('attention.'+key+' 必须为正整数')
        if self.repeat_count>self.repeat_samples:raise ValueError('repeat_count 不得超过 repeat_samples')
        for key in ('min_changed_area','max_dynamic_area','scroll_min_confidence'):
            v=getattr(self,key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<v<1:raise ValueError('attention.'+key+' 必须在(0,1)')
        if type(self.hide_ring) is not bool:raise ValueError('attention.hide_ring 必须为布尔值')
        for key in ('excluded_apps','ocr_languages'):
            if not isinstance(getattr(self,key),(list,tuple)) or not all(isinstance(v,str) and v for v in getattr(self,key)):raise ValueError('attention.'+key+' 必须为字符串数组')
