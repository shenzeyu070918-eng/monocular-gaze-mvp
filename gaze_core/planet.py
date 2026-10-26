"""Visual-only particle planet options; no camera or input dependencies."""
from dataclasses import dataclass,fields
import re
from .devices import finite


@dataclass(frozen=True)
class PlanetVisualSettings:
    planet_particles: int = 80000
    ring_particles: int = 150000
    low_particle_count: int = 100000
    seed: int = 918
    radius: float = 1.5
    ring_inner_ratio: float = 1.4
    ring_outer_ratio: float = 8.
    ring_density_power: float = 2.6
    ring_light_power: float = 1.5
    ring_outer_light: float = .15
    ring_gap_centers: tuple = (2.25,3.3)  # Radii in planet-radius units.
    ring_gap_width: float = .07
    ring_gap_depth: float = .85
    ring_thickness: float = .035
    orbit_speed: float = .34
    planet_spin: float = .08
    swirl_amplitude: float = .035
    swirl_speed: float = .22
    wander_fraction: float = .025
    wander_distance: float = .6
    camera_distance: float = 7.8
    camera_pitch_deg: float = 25.
    camera_yaw_deg: float = 0.
    camera_roll_deg: float = -12.
    close_distance: float = 3.6
    field_of_view: float = 50.
    point_size: float = 1.65
    point_size_min: float = .5
    point_size_max: float = 5.0
    bright_fraction: float = .015
    bright_size: float = 1.9
    bright_intensity: float = 2.0
    bright_halo: float = .12
    twinkle_speed: float = .5
    rim_strength: float = 3.5
    rim_power: float = 3.0
    planet_intensity: float = 4.6
    ring_intensity: float = 1.5
    core_size_px: float = 32.
    absorption: float = .9
    exposure: float = .9
    bloom_strength: float = .18
    bloom_threshold: float = 1.2
    bloom_radius: float = .25
    background_stars: int = 600
    nebula_intensity: float = .012
    galaxy_intensity: float = 2.8
    galaxy_core_intensity: float = .85
    galaxy_distance: float = 10.4
    galaxy_close_distance: float = 9.6
    galaxy_radius: float = 5.2
    galaxy_arm_width: float = .7
    galaxy_spin: float = .1
    galaxy_pitch_deg: float = 15.
    galaxy_disk_scale: float = .34       # Exponential scale length / outer radius.
    galaxy_diffuse_fraction: float = .30 # Of the non-bulge, non-knot disk.
    galaxy_diffuse_brightness: float = 1.6
    galaxy_bulge_fraction: float = .22
    galaxy_bulge_radius: float = .32     # In galaxy-radius units.
    galaxy_bulge_flattening: float = .55
    galaxy_branch_fraction: float = .12 # Of arm particles; short secondary segments.
    galaxy_outer_brightness: float = .28
    galaxy_fragmentation: float = .6
    galaxy_dust_depth: float = .7
    galaxy_knot_fraction: float = .009
    galaxy_knot_clusters: int = 18
    galaxy_knot_size: float = .013      # In galaxy-radius units.
    galaxy_drift_fraction: float = .006 # Bounded periodic displacement ALONG log arms.
    galaxy_drift_speed: float = .35
    galaxy_stability_tolerance_deg: float = 2.
    galaxy_face_fill: float = .9
    galaxy_view_fill: float = 1.1
    galaxy_fly_ratio: float = .5
    galaxy_zoom_bloom_gain: float = .65
    galaxy_arm_colors: tuple = ('#649ac4','#b3e0ed','#a89ad0')
    galaxy_knot_colors: tuple = ('#ed78b8','#ffc0df','#d858a5')
    text_distance: float = 7.8
    text_close_distance: float = 5.8
    text_viewport_fraction: float = .9
    text_point_size_max: float = 2.5
    text_intensity: float = .65
    text_width: float = 7.2
    text_depth: float = .35
    text_flow: float = .06
    planet_colors: tuple = ('#d8ad55','#ffe498','#fff5bf')
    ring_colors: tuple = ('#437b9f','#83c4d7','#effaff')

    def __post_init__(self):
        integers={'planet_particles','ring_particles','background_stars','seed','low_particle_count','galaxy_knot_clusters'}
        angles={'camera_pitch_deg','camera_yaw_deg','camera_roll_deg'}
        zero_ok={'seed','swirl_amplitude','wander_fraction','bloom_strength','nebula_intensity','rim_strength','bright_fraction','bright_halo','ring_gap_depth','galaxy_drift_fraction','galaxy_fragmentation','galaxy_dust_depth','galaxy_knot_fraction','galaxy_branch_fraction','galaxy_zoom_bloom_gain'}
        for f in fields(self):
            value=getattr(self,f.name)
            if f.name=='ring_gap_centers':
                if not isinstance(value,(tuple,list)) or len(value)>2:raise ValueError('最多两道环缝')
                value=tuple(finite(v,'环缝半径') for v in value)
                object.__setattr__(self,f.name,value);continue
            if f.name.endswith('_colors'):
                if not isinstance(value,(tuple,list)) or len(value)!=3 or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in value):
                    raise ValueError('galaxy.visual.'+f.name+' 需要三个六位十六进制颜色')
                object.__setattr__(self,f.name,tuple(value));continue
            value=finite(value,'galaxy.visual.'+f.name)
            if f.name in integers and type(getattr(self,f.name)) is not int:raise ValueError('粒子数/种子必须为整数')
            if f.name not in angles and (value<0 or (value==0 and f.name not in zero_ok)):raise ValueError('视觉参数必须为正数')
        if not 1<self.ring_inner_ratio<self.ring_outer_ratio:raise ValueError('星环内外半径顺序无效')
        if self.planet_particles+self.ring_particles>1_000_000:raise ValueError('粒子总数超过100万上限')
        if not 2<=self.low_particle_count<=1_000_000:raise ValueError('低档粒子数超出上限')
        if not 0<self.point_size_min<=self.point_size_max or not 0<self.field_of_view<150:raise ValueError('点大小/视角无效')
        if not self.radius<self.close_distance<=self.camera_distance:raise ValueError('镜头必须在球体外')
        if self.galaxy_close_distance>self.galaxy_distance or self.text_close_distance>self.text_distance:raise ValueError('各场景推近距离不能超过默认距离')
        if not 1<=self.galaxy_view_fill<=1.5 or not .2<=self.galaxy_fly_ratio<1:raise ValueError('银河取景/飞入比例无效')
        if not 0<self.text_viewport_fraction<=.9 or not self.point_size_min<=self.text_point_size_max<=self.point_size_max:raise ValueError('文字视口比例/点尺寸上限无效')
        if abs(self.camera_pitch_deg)>85 or abs(self.camera_yaw_deg)>180 or abs(self.camera_roll_deg)>90:raise ValueError('镜头角度超出范围')
        if not 0<=self.wander_fraction<=.2 or self.ring_density_power>3:raise ValueError('游离比例/密度衰减无效')

        if not 5<=self.galaxy_pitch_deg<=35:raise ValueError('银河螺距角须在5–35度内')
        if not .25<=self.galaxy_diffuse_fraction<=.35:raise ValueError('银河弥散星盘比例须在25%–35%内')
        if not .05<=self.galaxy_bulge_fraction<=.4 or not 0<=self.galaxy_knot_fraction<=.03:raise ValueError('银河核球/亮团比例无效')
        if not .1<=self.galaxy_disk_scale<=1 or not .1<=self.galaxy_bulge_radius<=.5 or not 0<self.galaxy_bulge_flattening<=1:raise ValueError('银河尺度/核球形状无效')
        if not 0<=self.galaxy_branch_fraction<=.3 or not 0<self.galaxy_outer_brightness<=1:raise ValueError('银河支臂/外缘亮度无效')
        if not 0<=self.galaxy_dust_depth<=.95 or not 0<=self.galaxy_fragmentation<=.9:raise ValueError('银河尘埃/碎片程度无效')
        if not 1<=self.galaxy_knot_clusters<=100 or not 0<self.galaxy_knot_size<=.1:raise ValueError('银河亮团数量/尺度无效')
        if not 0<=self.galaxy_drift_fraction<=.02 or not 0<self.galaxy_face_fill<=.95 or not 0<self.galaxy_stability_tolerance_deg<=5:raise ValueError('银河漂移/取景/检查阈值无效')
        if any(not self.ring_inner_ratio<c<self.ring_outer_ratio for c in self.ring_gap_centers):raise ValueError('环缝必须在环内')
        if not 0<self.ring_gap_width<=.5:raise ValueError('环缝宽度必须在(0, 0.5]个球半径内')
        if not 0<=self.bright_fraction<=.05 or not 0<self.ring_outer_light<=1 or not 0<=self.ring_gap_depth<=.95:raise ValueError('亮颗粒比例/环渐变无效')


@dataclass(frozen=True)
class PlanetInteractionSettings:
    yaw_range_deg: float = 35.
    pitch_range_deg: float = 35.
    galaxy_rotation_range_deg: float = 45.
    galaxy_pitch_range_deg: float = 8.
    galaxy_min_pitch_deg: float = 20.
    galaxy_zoom_core_ratio: float = .35
    text_yaw_range_deg: float = 4.
    text_pitch_range_deg: float = 3.
    camera_spring_hz: float = .7
    zoom_dwell_ms: float = 1500.
    zoom_spring_hz: float = .65
    corner_ratio: float = .2
    corner_leave_grace_ms: float = 300.
    corner_feedback_px: float = 44.
    corner_feedback_margin_px: float = 24.
    corner_feedback_fade_ms: float = 450.
    well_attraction: float = .12
    well_brightness: float = .65
    well_radius_scale: float = 1.5
    galaxy_well_attraction: float = 0.
    text_well_attraction: float = 0.
    galaxy_well_brightness: float = .9
    spotlight_size_gain: float = .2
    spotlight_twinkle: float = .08
    spotlight_speed: float = 1.2
    text_letter_scale: float = 1.08
    text_letter_lift: float = .12
    text_letter_brightness: float = 1.25
    text_letter_spring_hz: float = 2.
    galaxy_scatter_swirl: float = .9
    blink_hold_ms: float = 600.
    burst_duration_ms: float = 2500.
    burst_attack_ms: float = 300.
    burst_hold_ms: float = 400.
    burst_cooldown_ms: float = 3500.
    gaze_fade_s: float = .5
    hand_response_s: float = .55
    scatter_distance: float = 4.
    scatter_height: float = 1.2
    galaxy_scatter_distance: float = 2.4
    galaxy_scatter_height: float = .35
    text_scatter_distance: float = 2.0  # Full text width (two half-width units).
    text_scatter_height: float = .08   # Half-width units, normal to the text.
    text_scatter_curl: float = .4
    text_scatter_stagger: float = .35  # Fraction of the hand envelope, seeded per particle.
    text_well_brightness: float = 1.0
    spin_boost: float = 3.
    hand_speed_scale: float = .6
    min_hand_scale: float = .65
    max_hand_scale: float = 1.5

    def __post_init__(self):
        for f in fields(self):
            v=finite(getattr(self,f.name),'galaxy.interaction.'+f.name)
            if v<0 or v==0 and f.name not in {'yaw_range_deg','pitch_range_deg','text_yaw_range_deg','text_pitch_range_deg','galaxy_rotation_range_deg','galaxy_pitch_range_deg','text_scatter_curl','text_scatter_stagger','text_well_brightness','galaxy_well_attraction','text_well_attraction','galaxy_well_brightness','spotlight_twinkle','spotlight_size_gain','text_letter_lift','galaxy_scatter_swirl','well_attraction','well_brightness','spin_boost','corner_leave_grace_ms','corner_feedback_fade_ms','corner_feedback_margin_px'}:
                raise ValueError('交互参数必须为正数（可禁用项允许零）')
        if not 0<self.corner_ratio<.5 or max(self.yaw_range_deg,self.pitch_range_deg)>75:
            raise ValueError('角落范围/相机角度无效')
        if not 0<self.galaxy_zoom_core_ratio<=1 or not 0<self.galaxy_min_pitch_deg<=80 or self.galaxy_rotation_range_deg>180 or self.galaxy_pitch_range_deg>45:raise ValueError('银河核心/俯仰/旋转范围无效')
        if not 0<=self.text_scatter_stagger<.8 or not 0<=self.text_scatter_curl<=1:raise ValueError('文字错峰/卷曲范围无效')
        if self.text_scatter_height>.2:raise ValueError('文字散开深度不能超过半字宽的0.2倍')
        if max(self.text_yaw_range_deg,self.text_pitch_range_deg)>45:raise ValueError('文字视角范围不能超过45度，以保持可读')
        if not 300<=self.blink_hold_ms<=5000 or self.burst_cooldown_ms<self.burst_duration_ms:raise ValueError('闭眼阈值至少300ms，冷却不能短于包络')
        if self.burst_attack_ms+self.burst_hold_ms>=self.burst_duration_ms:raise ValueError('打散包络必须留出回收时间')
        if not 1<=self.text_letter_scale<=1.3 or not 0<=self.text_letter_lift<=.5:raise ValueError('字母放大/浮起范围无效')
        if not 0<self.min_hand_scale<=1<=self.max_hand_scale:
            raise ValueError('手势尺度无效')
