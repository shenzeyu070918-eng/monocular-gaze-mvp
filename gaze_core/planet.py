"""Visual-only particle planet options; no camera or input dependencies."""
from dataclasses import dataclass,fields
import re
from .devices import finite


@dataclass(frozen=True)
class PlanetVisualSettings:
    planet_particles: int = 80000
    ring_particles: int = 150000
    seed: int = 918
    radius: float = 1.5
    ring_inner_ratio: float = 1.4
    ring_outer_ratio: float = 8.
    ring_density_power: float = 1.5
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
    point_size_max: float = 2.7
    planet_intensity: float = 2.6
    ring_intensity: float = 1.5
    core_size_px: float = 32.
    absorption: float = .9
    exposure: float = .9
    bloom_strength: float = .18
    bloom_threshold: float = 1.2
    bloom_radius: float = .25
    background_stars: int = 600
    nebula_intensity: float = .012
    galaxy_intensity: float = 1.6
    galaxy_core_intensity: float = .32
    galaxy_distance: float = 10.4
    galaxy_radius: float = 5.2
    galaxy_arm_width: float = .4
    galaxy_spin: float = .1
    text_intensity: float = .65
    text_width: float = 7.2
    text_depth: float = .35
    text_flow: float = .06
    planet_colors: tuple = ('#c99441','#f7d778','#fff5bf')
    ring_colors: tuple = ('#74b6c6','#c6e9ed','#eefbff')

    def __post_init__(self):
        integers={'planet_particles','ring_particles','background_stars','seed'}
        angles={'camera_pitch_deg','camera_yaw_deg','camera_roll_deg'}
        zero_ok={'seed','swirl_amplitude','wander_fraction','bloom_strength','nebula_intensity'}
        for f in fields(self):
            value=getattr(self,f.name)
            if f.name.endswith('_colors'):
                if not isinstance(value,(tuple,list)) or len(value)!=3 or any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in value):
                    raise ValueError('galaxy.visual.'+f.name+' 需要三个六位十六进制颜色')
                object.__setattr__(self,f.name,tuple(value));continue
            value=finite(value,'galaxy.visual.'+f.name)
            if f.name in integers and type(getattr(self,f.name)) is not int:raise ValueError('粒子数/种子必须为整数')
            if f.name not in angles and (value<0 or (value==0 and f.name not in zero_ok)):raise ValueError('视觉参数必须为正数')
        if not 1<self.ring_inner_ratio<self.ring_outer_ratio:raise ValueError('星环内外半径顺序无效')
        if self.planet_particles+self.ring_particles>1_000_000:raise ValueError('粒子总数超过100万上限')
        if not 0<self.point_size_min<=self.point_size_max or not 0<self.field_of_view<150:raise ValueError('点大小/视角无效')
        if not self.radius<self.close_distance<=self.camera_distance:raise ValueError('镜头必须在球体外')
        if abs(self.camera_pitch_deg)>85 or abs(self.camera_yaw_deg)>180 or abs(self.camera_roll_deg)>90:raise ValueError('镜头角度超出范围')
        if not 0<=self.wander_fraction<=.2 or self.ring_density_power>3:raise ValueError('游离比例/密度衰减无效')
