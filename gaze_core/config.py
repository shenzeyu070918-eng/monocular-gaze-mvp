"""Shared configuration without UI or model imports."""
from dataclasses import dataclass, replace, field
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
    max_fps: float | None = None

    @property
    def num_threads(self):
        return self.cpu_threads  # Backward-compatible dataclass construction.


@dataclass(frozen=True)
class CameraSettings:
    index: int
    width: int
    height: int
    backend: str
    grayscale: bool
    startup_timeout_s: float
    stop_timeout_s: float
    undistort: bool = True
    undistort_alpha: float = 0.0
    warmup_timeout_s: float = 3.0
    read_retry_interval_s: float = 0.05
    read_retry_count: int = 3
    black_threshold: float = 2.0
    black_frame_count: int = 10


@dataclass(frozen=True)
class ScreenGeometry:
    width_cm: float | None
    height_cm: float | None
    top_cm: float
    focal_scale: float
    horizontal_cm: float = 0.0
    measured: bool = False


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
class CalibrationSettings:
    views: int = 20
    max_error_px: float = 1.0
    min_view_distance: float = 0.08
    min_center_span: float = 0.2
    min_tilt_span_deg: float = 15.0
    refine_window_px: int = 11
    refine_max_iterations: int = 30
    refine_epsilon: float = 0.001


@dataclass(frozen=True)
class DistanceCheckSettings:
    distances_cm: tuple = (40, 50, 60)
    settle_ms: float = 1000.0
    duration_ms: float = 3000.0
    min_samples: int = 20
    warning_cm: float = 5.0


@dataclass(frozen=True)
class EvaluationSettings:
    center_points: tuple = ((.25, .25), (.75, .25), (.25, .75), (.75, .75), (.5, .34), (.34, .5))
    calibration_points: tuple = ((.5, .5), (.12, .12), (.5, .12), (.88, .12), (.88, .5), (.88, .88), (.5, .88), (.12, .88), (.12, .5))
    settle_ms: float = 650.0
    dwell_ms: float = 2200.0
    max_dwell_ms: float = 10000.0
    calibration_min_samples: int = 8


@dataclass(frozen=True)
class FilterSettings:
    # Match default.yaml candidate 44: three Mac single-user tasks; multi-user validation pending.
    min_cutoff: float = 2.0
    beta: float = 0.05
    d_cutoff: float = 1.0

    def __post_init__(self):
        from .devices import finite
        finite(self.min_cutoff, 'filter.min_cutoff', positive=True)
        finite(self.d_cutoff, 'filter.d_cutoff', positive=True)
        if finite(self.beta, 'filter.beta') < 0:
            raise ValueError('filter.beta 必须非负')


@dataclass(frozen=True)
class FixationSettings:
    window_ms: float = 80.0
    angular_threshold_deg_s: float = 90.0
    fallback_threshold_norm_s: float = 1.5
    min_duration_ms: float = 100.0
    max_gap_ms: float = 250.0

    def __post_init__(self):
        from .devices import finite
        for key, value in vars(self).items():
            finite(value, 'fixation.'+key, positive=True)
        if self.max_gap_ms <= self.window_ms:
            raise ValueError('fixation.max_gap_ms 必须大于 window_ms')


@dataclass(frozen=True)
class OfflineFilterSettings:
    cutoff_hz: float = 3.0
    order: int = 2
    max_gap_ms: float = 250.0

    def __post_init__(self):
        from .devices import finite
        finite(self.cutoff_hz, 'offline_filter.cutoff_hz', positive=True)
        finite(self.max_gap_ms, 'offline_filter.max_gap_ms', positive=True)
        if type(self.order) is not int or self.order < 1:
            raise ValueError('offline_filter.order 必须为正整数')


@dataclass(frozen=True)
class DisplaySettings:
    max_pages: int = 16
    idle_timeout_s: float = 300.0

    def __post_init__(self):
        from .devices import finite
        if type(self.max_pages) is not int or self.max_pages < 1:
            raise ValueError('display.max_pages 必须为正整数')
        finite(self.idle_timeout_s, 'display.idle_timeout_s', positive=True)


@dataclass(frozen=True)
class HeatmapSettings:
    enabled: bool = False
    allow_uncalibrated: bool = False
    grid_width: int = 160
    update_hz: float = 4.0  # Background idle checks, independent of viewing.
    display_mode: str = 'on_demand'
    view_update_hz: float = 1.0
    alpha_threshold: float = .15
    render_scale: float = .5  # Fraction of full logical screen, independent of Retina DPR.
    dither_strength: float = .5  # Peak-to-peak alpha quantization levels; 0 disables.
    half_life_s: float = 60.0
    max_opacity: float = .4
    queue_size: int = 256
    regions: object = None  # None means a non-overlapping 3 x 3 partition.
    target_neighborhood_sigma: float = 1.0

    def __post_init__(self):
        from .devices import finite
        for key in ('enabled','allow_uncalibrated'):
            if type(getattr(self,key)) is not bool:raise ValueError('heatmap.'+key+' 必须为布尔值')
        for key in ('grid_width','queue_size'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError('heatmap.'+key+' 必须为正整数')
        if self.grid_width>1024:raise ValueError('heatmap.grid_width 不得超过 1024')
        for key in ('update_hz','view_update_hz','half_life_s','max_opacity','target_neighborhood_sigma'):
            finite(getattr(self,key),'heatmap.'+key,positive=True)
        if self.update_hz>5:raise ValueError('heatmap.update_hz 不得超过 5')
        if self.view_update_hz>5:raise ValueError('heatmap.view_update_hz 不得超过 5')
        if self.display_mode not in ('on_demand','continuous'):raise ValueError('heatmap.display_mode 必须为 on_demand / continuous')
        if not 0<=finite(self.alpha_threshold,'heatmap.alpha_threshold')<1:raise ValueError('heatmap.alpha_threshold 必须在 [0,1)')
        if not 0<finite(self.render_scale,'heatmap.render_scale')<=1:raise ValueError('heatmap.render_scale 必须在 (0,1]')
        if not 0<=finite(self.dither_strength,'heatmap.dither_strength')<=1:raise ValueError('heatmap.dither_strength 必须在 [0,1]，单位为透明度量化级')
        if self.max_opacity>.4:raise ValueError('heatmap.max_opacity 不得超过 0.4')
        if self.regions is not None:
            if not isinstance(self.regions,(list,tuple)) or not self.regions:raise ValueError('heatmap.regions 必须为非空矩形数组或 null')
            names=set();rects=[]
            for item in self.regions:
                if not isinstance(item,dict) or set(item)!={'id','rect'} or not isinstance(item['id'],str) or not item['id'] or item['id'] in names:
                    raise ValueError('每个区域需要唯一 id 和 rect')
                names.add(item['id']);rect=item['rect']
                if not isinstance(rect,(list,tuple)) or len(rect)!=4:raise ValueError('rect 为 [left, top, right, bottom]')
                x0,y0,x1,y1=[finite(v,'rect') for v in rect]
                if not (0<=x0<x1<=1 and 0<=y0<y1<=1):raise ValueError('区域必须在归一化屏幕内且面积为正')
                if any(max(x0,a)<min(x1,c) and max(y0,b)<min(y1,d) for a,b,c,d in rects):raise ValueError('热力图区域不得重叠')
                rects.append((x0,y0,x1,y1))


@dataclass(frozen=True)
class OverlaySettings:
    radius_cm: float = 2.0
    fallback_radius_px: float = 28.0
    opacity: float = .65
    fade_ms: float = 200.0
    stale_ms: float = 300.0
    stroke_px: float = 2.0
    refresh_ms: int = 16
    idle_after_s: float = 3.0
    idle_fps: float = 5.0
    color: str = '#35d6b4'
    label_font_px: int = 13
    label_inset_px: int = 40
    label_position: str = 'bottom_center'
    label_bottom_inset_px: int = 90
    label_padding_px: int = 12
    label_autohide_s: float = 4.0
    label_debounce_s: float = 1.0
    diagnostic_size_px: int = 12
    task_radius_px: int = 12
    profile_name: str = 'builtin'
    profile_poll_s: float = 1.0
    tray_check_delay_ms: int = 500
    screen_debounce_ms: int = 300
    screen_retry_count: int = 3
    web_url: str = 'http://127.0.0.1:8765'
    panel_on_start: str = 'auto'
    calibration_redo_cm: float = 2.0
    presentation: bool = False
    presentation_stroke_px: float = 6.0
    presentation_color: str = '#FFD166'
    presentation_label_font_px: int = 26
    calibration_notice_s: float = 3.0
    hotkeys: dict = field(default_factory=lambda: dict(toggle='Ctrl+Alt+G', pause='Ctrl+Alt+P', calibrate='Ctrl+Alt+C', panel='Ctrl+Alt+O', heatmap_toggle='Ctrl+Alt+H',heatmap_view='Ctrl+Alt+V',demo='Ctrl+Alt+D'))

    def __post_init__(self):
        import re
        from .devices import finite
        for key in ('radius_cm','fallback_radius_px','opacity','fade_ms','stale_ms','stroke_px','idle_after_s','idle_fps'):
            finite(getattr(self,key),'overlay.'+key,positive=True)
        if self.opacity>1:raise ValueError('overlay.opacity 必须在 (0,1]')
        if type(self.screen_retry_count) is not int or self.screen_retry_count<0:raise ValueError('overlay.screen_retry_count 必须为非负整数')
        for key in ('refresh_ms','label_font_px','label_inset_px','diagnostic_size_px','task_radius_px','tray_check_delay_ms','screen_debounce_ms'):
            if type(getattr(self,key)) is not int or getattr(self,key)<=0:raise ValueError('overlay.'+key+' 必须为正整数')
        for key in ('label_bottom_inset_px','label_padding_px'):
            if type(getattr(self,key)) is not int or getattr(self,key)<0:raise ValueError('overlay.'+key+' 必须为非负整数')
        if self.label_position not in ('bottom_center','bottom_right','top_left'):
            raise ValueError('overlay.label_position 必须为 bottom_center / bottom_right / top_left')
        for key in ('label_autohide_s','label_debounce_s'):
            if finite(getattr(self,key),'overlay.'+key)<0:
                raise ValueError('overlay.'+key+' 必须为非负数')
        if not isinstance(self.color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',self.color):
            raise ValueError('overlay.color 必须为 #RRGGBB')
        from .devices import device_name
        device_name(self.profile_name)
        finite(self.profile_poll_s,'overlay.profile_poll_s',positive=True)
        if self.panel_on_start not in ('auto','always','never'):
            raise ValueError('overlay.panel_on_start 必须为 auto / always / never')
        finite(self.calibration_redo_cm,'overlay.calibration_redo_cm',positive=True)
        if type(self.presentation) is not bool:raise ValueError('overlay.presentation 必须为布尔值')
        finite(self.presentation_stroke_px,'overlay.presentation_stroke_px',positive=True)
        finite(self.calibration_notice_s,'overlay.calibration_notice_s',positive=True)
        if type(self.presentation_label_font_px) is not int or self.presentation_label_font_px<1:
            raise ValueError('overlay.presentation_label_font_px 必须为正整数')
        if not isinstance(self.presentation_color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',self.presentation_color):
            raise ValueError('overlay.presentation_color 必须为 #RRGGBB')
        if not isinstance(self.hotkeys,dict) or not {'toggle','pause','calibrate','panel'}<=set(self.hotkeys) or set(self.hotkeys)-{'toggle','pause','calibrate','panel','heatmap_toggle','heatmap_view','demo'} or any(not isinstance(v,str) for v in self.hotkeys.values()):
            raise ValueError('overlay.hotkeys 必须包含 toggle / pause / calibrate / panel，可加 heatmap_toggle / heatmap_view / demo；空字符串禁用该项')
        from urllib.parse import urlsplit
        url=urlsplit(self.web_url)
        if url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost','::1'}:
            raise ValueError('overlay.web_url 必须为本机 http 地址')


@dataclass(frozen=True)
class ClickSessionSettings:
    max_fps: float | None = None
    recent_baseline_max_age_s: float = 300.0
    clock_resample_ms: float = 10000.0
    clock_max_uncertainty_ms: float = 20.0
    clock_max_drift_ms: float = 20.0
    clock_max_delay_ms: float = 10000.0

    def __post_init__(self):
        from .devices import finite
        if self.max_fps is not None:finite(self.max_fps,'click_session.max_fps',positive=True)
        finite(self.recent_baseline_max_age_s,'click_session.recent_baseline_max_age_s',positive=True)
        if self.recent_baseline_max_age_s>300:
            raise ValueError('点击实验 B 基线复用时间不得超过 5 分钟')
        for name in ('clock_resample_ms','clock_max_uncertainty_ms','clock_max_drift_ms','clock_max_delay_ms'):
            finite(getattr(self,name),'click_session.'+name,positive=True)


@dataclass(frozen=True)
class AutocalSettings:
    enabled: bool = False
    window_ms: tuple = (300.,0.)
    max_fps: float | str | None = 'inference'  # null=unlimited; 'inference'=inherit.
    min_clicks: int = 10
    max_clicks: int = 120  # Provisional, based on one session; independent live check pending.
    min_occupied_cells: int = 3
    frame_history_s: float = 2.0
    pending_timeout_s: float = 2.0
    queue_size: int = 256
    close_timeout_s: float = 5.0
    sampling_config: str = 'implicit_experiment.yaml'

    def __post_init__(self):
        from .devices import finite
        if type(self.enabled) is not bool:raise ValueError('autocal.enabled 必须为布尔值')
        if not isinstance(self.window_ms,(list,tuple)) or len(self.window_ms)!=2:
            raise ValueError('autocal.window_ms 需要点击前 [start, end] 毫秒')
        start,end=[finite(v,'autocal.window_ms') for v in self.window_ms]
        if not 0<=end<start:raise ValueError('autocal.window_ms 必须满足 start > end ≥ 0')
        if self.max_fps is not None and self.max_fps!='inference':finite(self.max_fps,'autocal.max_fps',positive=True)
        for key in ('min_clicks','max_clicks','min_occupied_cells','queue_size'):
            value=getattr(self,key)
            if type(value) is not int or value<1:raise ValueError('autocal.'+key+' 必须为正整数')
        if self.min_clicks<5 or self.max_clicks<self.min_clicks or not 3<=self.min_occupied_cells<=9:
            raise ValueError('autocal 点击数/覆盖范围无效')
        for key in ('frame_history_s','pending_timeout_s','close_timeout_s'):
            finite(getattr(self,key),'autocal.'+key,positive=True)
        if self.frame_history_s*1000<start:raise ValueError('autocal.frame_history_s 不足以覆盖取样窗口')
        if not isinstance(self.sampling_config,str) or not self.sampling_config:raise ValueError('autocal.sampling_config 必须为配置文件名')

    def inference_limit(self,inference):
        return inference.max_fps if self.max_fps=='inference' else self.max_fps


@dataclass(frozen=True)
class DemoSettings:
    dwell_ms: float = 800.0
    show_gaze_ring: bool = False
    leave_grace_ms: float = 150.0
    cooldown_ms: float = 800.0
    loss_reset_ms: float = 400.0
    hit_expand_ratio: float = .5  # Active button only; 0 disables sticky hit testing.
    practice: bool = True
    labels: tuple = ('我想喝水','我不舒服','请叫护士','开灯','是','否')
    round_s: float = 60.0
    undo_ms: float = 1000.0
    feedback_ms: float = 2000.0
    flash_ms: float = 350.0
    speech_timeout_ms: float = 15000.0
    button_font_px: int = 48
    heading_font_px: int = 36
    progress_px: float = 10.0
    min_button_px: float = 160.0
    padding_px: float = 32.0
    header_px: float = 120.0
    footer_px: float = 65.0
    background: str = '#101B30'
    button_color: str = '#EDF3FF'
    hole_color: str = '#294363'
    text_color: str = '#112444'
    progress_color: str = '#FFCA55'
    feedback_color: str = '#FFCA55'
    active_color: str = '#FFD475'
    debug_before_color: str = '#F47BA9'
    debug_after_color: str = '#68DBFF'
    debug_point_px: float = 5.0
    practice_target_px: float = 5.0

    def __post_init__(self):
        import re
        from .devices import finite
        for name in ('dwell_ms','loss_reset_ms','round_s','undo_ms','feedback_ms','flash_ms','speech_timeout_ms',
                     'progress_px','min_button_px','padding_px','header_px','footer_px','debug_point_px','practice_target_px'):
            finite(getattr(self,name),'demo.'+name,positive=True)
        for name in ('leave_grace_ms','cooldown_ms','hit_expand_ratio'):
            if finite(getattr(self,name),'demo.'+name)<0:raise ValueError('demo.'+name+' 必须非负')
        if type(self.practice) is not bool:raise ValueError('demo.practice 必须为布尔值')
        if type(self.show_gaze_ring) is not bool:raise ValueError('demo.show_gaze_ring 必须为布尔值')
        for name,minimum in (('button_font_px',40),('heading_font_px',1)):
            if type(getattr(self,name)) is not int or getattr(self,name)<minimum:raise ValueError('demo.'+name+f' 必须为整数且至少 {minimum}')
        if not isinstance(self.labels,(tuple,list)) or len(self.labels)!=6 or any(not isinstance(v,str) or not v.strip() or len(v)>120 for v in self.labels):
            raise ValueError('demo.labels 需要六条非空短文字，每条不超过120字')
        for name in ('background','button_color','hole_color','text_color','progress_color','feedback_color','active_color','debug_before_color','debug_after_color'):
            if not isinstance(getattr(self,name),str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',getattr(self,name)):
                raise ValueError('demo.'+name+' 必须为 #RRGGBB')


@dataclass(frozen=True)
class Settings:
    inference: InferenceSettings
    camera: CameraSettings
    geometry: ScreenGeometry
    recording: RecordingSettings
    jump: JumpSettings
    calibration: CalibrationSettings = CalibrationSettings()
    distance_check: DistanceCheckSettings = DistanceCheckSettings()
    device_file: str | None = None
    device_profile: object = None
    evaluation: EvaluationSettings = EvaluationSettings()
    config_sources: tuple = ()
    key_sources: object = None
    filter: FilterSettings = FilterSettings()
    fixation: FixationSettings = FixationSettings()
    offline_filter: OfflineFilterSettings = OfflineFilterSettings()
    display: DisplaySettings = DisplaySettings()
    overlay: OverlaySettings = OverlaySettings()
    click_session: ClickSessionSettings = ClickSessionSettings()
    heatmap: HeatmapSettings = HeatmapSettings()
    autocal: AutocalSettings = AutocalSettings()
    demo: DemoSettings = DemoSettings()


def select_device(requested, cuda_available, mps_available=False):
    if requested not in {'auto', 'cpu', 'cuda', 'mps'}:
        raise ValueError('inference.device 必须是 auto、cpu、cuda 或 mps')
    if requested == 'mps':
        return 'mps' if mps_available else 'cpu'
    if requested == 'cuda' and not cuda_available:
        raise RuntimeError('已指定 CUDA，但当前 PyTorch 无法使用 CUDA；请检查 CUDA 版 PyTorch 安装及显卡驱动，或改用 cpu/auto。')
    return 'cuda' if requested != 'cpu' and cuda_available else 'cpu'


def normalize_inference_keys(data):
    """Normalize each layer BEFORE merging so a local legacy key overrides defaults."""
    if not isinstance(data,dict) or not isinstance(data.get('inference'),dict):return data
    options=dict(data['inference'])
    if 'num_threads' in options:
        if 'cpu_threads' in options and (type(options['num_threads']) is not type(options['cpu_threads']) or options['num_threads']!=options['cpu_threads']):
            raise ValueError('同一配置层的 inference.num_threads 与 cpu_threads 冲突')
        options['cpu_threads']=options.pop('num_threads')
    return dict(data,inference=options)


def merge_config(base, overrides):
    """Recursive mapping merge; lists/scalars/null replace the complete value."""
    result = dict(base)
    for key, value in overrides.items():
        result[key] = merge_config(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def load_settings(path=DEFAULT_CONFIG, *, use_local=True):
    import yaml
    path = Path(path).resolve()
    with path.open(encoding='utf-8') as source:
        data = normalize_inference_keys(yaml.safe_load(source))
    sources = [str(path)]
    key_sources = {}
    def note(mapping, filename, prefix=''):
        for key, value in mapping.items():
            name = prefix+key
            if isinstance(value, dict):
                note(value, filename, name+'.')
            else:
                key_sources[name] = filename
                if name=='inference.cpu_threads':key_sources['inference.num_threads']=filename
    if isinstance(data, dict):
        note(data, str(path))
    local = path.with_name('local.yaml')
    if use_local and path == DEFAULT_CONFIG.resolve() and local.exists():
        with local.open(encoding='utf-8') as source:
            override = normalize_inference_keys(yaml.safe_load(source))
        if not isinstance(override, dict):
            raise ValueError('local.yaml 必须为映射')
        data = merge_config(data, override)
        note(override, str(local))
        sources.append(str(local))
    required = {'inference', 'camera', 'geometry', 'recording', 'jump'}
    if not isinstance(data, dict) or not required <= set(data) or set(data)-required-{'device_file', 'calibration', 'distance_check', 'evaluation', 'filter', 'fixation', 'offline_filter', 'display', 'overlay', 'click_session', 'heatmap', 'autocal', 'demo'}:
        raise ValueError('配置必须包含 inference、camera、geometry、recording、jump 五部分')
    settings = Settings(InferenceSettings(**data['inference']), CameraSettings(**data['camera']),
                        ScreenGeometry(**data['geometry']), RecordingSettings(**data['recording']), JumpSettings(**data['jump']),
                        CalibrationSettings(**data.get('calibration', {})), DistanceCheckSettings(**data.get('distance_check', {})),
                        data.get('device_file'))
    settings = replace(settings, evaluation=EvaluationSettings(**data.get('evaluation', {})), config_sources=tuple(sources), key_sources=key_sources)
    settings = replace(settings, filter=FilterSettings(**data.get('filter', {})),
                       fixation=FixationSettings(**data.get('fixation', {})),
                       offline_filter=OfflineFilterSettings(**data.get('offline_filter', {})),
                       display=DisplaySettings(**data.get('display', {})),
                       overlay=OverlaySettings(**data.get('overlay', {})),
                       click_session=ClickSessionSettings(**data.get('click_session', {})),
                       heatmap=HeatmapSettings(**data.get('heatmap', {})),
                       autocal=AutocalSettings(**data.get('autocal', {})),
                       demo=DemoSettings(**data.get('demo', {})))
    i, c, g = settings.inference, settings.camera, settings.geometry
    # Validate the request separately from actual device availability.
    if i.device not in {'auto', 'cpu', 'cuda', 'mps'}:
        raise ValueError('inference.device 必须是 auto、cpu、cuda 或 mps')
    if i.max_fps is not None and (isinstance(i.max_fps,bool) or not isinstance(i.max_fps,(int,float)) or not math.isfinite(i.max_fps) or i.max_fps<=0):
        raise ValueError('inference.max_fps 必须为有限正数或 null（不限制）')
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
                        ('warmup_timeout_s', c.warmup_timeout_s), ('read_retry_interval_s', c.read_retry_interval_s),
                        ('focal_scale', g.focal_scale)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} 必须是有限正数')
    if (type(c.black_frame_count) is not int or c.black_frame_count <= 0 or isinstance(c.black_threshold,bool)
            or not isinstance(c.black_threshold,(int,float)) or not math.isfinite(c.black_threshold) or not 0 <= c.black_threshold <= 255):
        raise ValueError('黑屏阈值必须在 [0,255] 且连续帧数必须为正整数')
    if type(c.read_retry_count) is not int or c.read_retry_count < 0:
        raise ValueError('camera.read_retry_count 必须是非负整数')
    from .devices import finite, load_device
    for key in ('width_cm', 'height_cm'):
        finite(getattr(g, key), key, positive=True, nullable=True)
    if (g.width_cm is None) != (g.height_cm is None) or type(g.measured) is not bool or (g.measured and g.width_cm is None):
        raise ValueError('屏幕宽高必须同时填写，实测状态必须明确')
    finite(g.top_cm, 'top_cm'); finite(g.horizontal_cm, 'horizontal_cm')
    if type(c.undistort) is not bool or not 0 <= finite(c.undistort_alpha, 'undistort_alpha') <= 1:
        raise ValueError('去畸变开关必须为布尔值，alpha 必须在 [0, 1]')
    if settings.device_file is not None:
        if not isinstance(settings.device_file, str) or not settings.device_file.strip():
            raise ValueError('device_file 必须是设备文件路径或 null')
        device_path = Path(settings.device_file)
        if not device_path.is_absolute():
            device_path = Path(path).resolve().parent/device_path
        profile = load_device(device_path)
        for key in ('width_cm', 'height_cm', 'top_cm', 'horizontal_cm', 'measured'):
            key_sources['geometry.'+key] = str(device_path)
        settings = replace(settings, device_profile=profile,
                           geometry=replace(g, width_cm=profile.width_cm, height_cm=profile.height_cm,
                                            top_cm=profile.top_cm, horizontal_cm=profile.horizontal_cm, measured=profile.measured))
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
    calibration, check = settings.calibration, settings.distance_check
    for key in ('views',):
        if type(getattr(calibration, key)) is not int or getattr(calibration, key) < 3:
            raise ValueError('calibration.views 至少为 3')
    for key in ('max_error_px', 'min_view_distance', 'min_center_span', 'min_tilt_span_deg', 'refine_epsilon'):
        finite(getattr(calibration, key), 'calibration.'+key, positive=True)
    for key in ('refine_window_px', 'refine_max_iterations'):
        if type(getattr(calibration, key)) is not int or getattr(calibration, key) <= 0:
            raise ValueError('calibration.'+key+' 必须为正整数')
    if calibration.min_center_span > 1 or calibration.min_tilt_span_deg > 180:
        raise ValueError('标定多样性阈值超出可用范围')
    if not isinstance(check.distances_cm, (list, tuple)) or not check.distances_cm:
        raise ValueError('distance_check.distances_cm 必须为非空数组')
    for value in check.distances_cm:
        finite(value, 'distances_cm', positive=True)
    for key in ('settle_ms', 'duration_ms', 'warning_cm'):
        finite(getattr(check, key), 'distance_check.'+key, positive=True)
    if type(check.min_samples) is not int or check.min_samples <= 0:
        raise ValueError('distance_check.min_samples 必须为正整数')
    e = settings.evaluation
    for key in ('settle_ms', 'dwell_ms', 'max_dwell_ms'):
        finite(getattr(e, key), 'evaluation.'+key, positive=True)
    if not e.settle_ms < e.dwell_ms < e.max_dwell_ms or type(e.calibration_min_samples) is not int or e.calibration_min_samples < 1:
        raise ValueError('评估/校准时间窗口或样本数无效')
    for name, points in [('center_points', e.center_points), ('calibration_points', e.calibration_points)]:
        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError(name+' 必须为非空点数组')
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2 or any(not 0 < finite(v, name) < 1 for v in point):
                raise ValueError(name+' 必须是全屏归一化坐标')
    if len(e.calibration_points) != 9 or any(list(p) in [list(c) for c in e.calibration_points] for p in e.center_points):
        raise ValueError('九点校准必须有九点，中央评估点必须与校准点错开')
    return settings


def configuration_info(settings):
    from dataclasses import asdict
    return {'files': list(settings.config_sources), 'keys': dict(settings.key_sources or {}),
            'device_file': settings.device_file, 'camera': asdict(settings.camera), 'geometry': asdict(settings.geometry), 'filter': asdict(settings.filter),
            'fixation': asdict(settings.fixation), 'offline_filter': asdict(settings.offline_filter), 'heatmap': asdict(settings.heatmap),
            'inference':dict(device=settings.inference.device,num_threads=settings.inference.num_threads,max_fps=settings.inference.max_fps)}


def print_configuration(settings):
    import json, sys
    info = configuration_info(settings)
    print('实际配置：'+json.dumps(info, ensure_ascii=False), file=sys.stderr)
