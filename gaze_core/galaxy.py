"""Validated Galaxy options and capture-clock interaction; no UI/hardware I/O."""
from dataclasses import dataclass, fields
import math
from numbers import Integral
import numpy as np
from .devices import finite
from .planet import PlanetVisualSettings,PlanetInteractionSettings


@dataclass(frozen=True)
class GalaxySettings:
    text: str = 'BME'
    particle_count: int = 50000
    pixel_ratio: float = 1.5
    min_pixel_ratio: float = .75
    low_fps: float = 50.
    low_fps_s: float = 3.
    intro_s: float = 2.
    dwell_ms: float = 1500.
    transition_s: float = 1.6
    transition_scatter_s: float = .5
    stale_ms: float = 400.
    hand_enter_frames: int = 3
    hand_leave_ms: float = 500.
    hand_smoothing_ms: float = 100.
    hand_closed: float = .25
    hand_open: float = .7
    hand_confidence: float = .5
    connection_timeout_s: float = 30.
    heartbeat_timeout_s: float = 5.
    disconnect_grace_s: float = .5
    max_message_bytes: int = 8192
    bloom_strength: float = 1.3
    bloom_radius: float = .55
    bloom_threshold: float = .5
    particle_brightness: float = .65
    text_brightness: float = .3
    gaze_brightness: float = 1.5
    spring_hz: float = 2.
    attraction: float = 1.5
    restore: float = 2.5
    drag: float = 1.5
    explode: float = 7.
    rotation: float = 2.
    star_count: int = 2200
    point_size: float = 2.2
    camera_z: float = 8.
    field_of_view: float = 55.
    hand_model: str = 'models/hand_landmarker.task'
    visual: PlanetVisualSettings = PlanetVisualSettings()
    interaction: PlanetInteractionSettings = PlanetInteractionSettings()

    def __post_init__(self):
        if isinstance(self.interaction,dict):object.__setattr__(self,'interaction',PlanetInteractionSettings(**self.interaction))
        if not isinstance(self.interaction,PlanetInteractionSettings):raise ValueError('galaxy.interaction 配置无效')
        if isinstance(self.visual,dict):object.__setattr__(self,'visual',PlanetVisualSettings(**self.visual))
        if not isinstance(self.visual,PlanetVisualSettings):raise ValueError('galaxy.visual 配置无效')
        if not isinstance(self.text,str) or not self.text.strip() or len(self.text)>80 or any(ord(c)<32 for c in self.text):
            raise ValueError('galaxy.text 必须为1–80字的单行文字')
        if self.particle_count not in (50000,100000) or type(self.particle_count) is not int:
            raise ValueError('galaxy.particle_count 必须为50000或100000')
        integers={'hand_enter_frames','max_message_bytes','star_count'}
        for f in fields(self):
            v=getattr(self,f.name)
            if f.name in ('text','hand_model','particle_count','visual','interaction'):continue
            if f.name in integers and (type(v) is not int or v<1):raise ValueError('galaxy.'+f.name+' 必须为正整数')
            finite(v,'galaxy.'+f.name,positive=True)
        if not 0<self.hand_closed<self.hand_open<1 or not 0<self.hand_confidence<=1:
            raise ValueError('手势阈值必须有序且在0到1内')
        if self.min_pixel_ratio>self.pixel_ratio or not 0<self.field_of_view<150:raise ValueError('星系渲染尺度无效')
        if not isinstance(self.hand_model,str) or not self.hand_model.strip():raise ValueError('需要本地手势模型路径')


from .galaxy_scene import PatternCycle


def hand_features(landmarks):
    """Only aggregate controls leave this function. Palm scale removes distance."""
    p=np.asarray(landmarks,dtype=float)
    if p.shape!=(21,3) or not np.isfinite(p).all():raise ValueError('需要21个有限三维手部关键点')
    palm=p[[0,5,9,13,17]].mean(axis=0)
    scale=float(np.linalg.norm(p[5]-p[17]))
    if scale<1e-6:raise ValueError('掌尺度过小')
    extension=[]
    for a,b,c in ((5,6,8),(9,10,12),(13,14,16),(17,18,20)):
        u,v=p[a]-p[b],p[c]-p[b]
        cosine=np.dot(u,v)/max(np.linalg.norm(u)*np.linalg.norm(v),1e-9)
        extension.append(np.clip((math.acos(float(np.clip(cosine,-1,1)))/math.pi-.35)/.6,0,1))
    return [float(1-palm[0]),float(palm[1]),float(np.median(extension)),float(np.linalg.norm(p[4]-p[8])/scale)]


class HandControls:
    def __init__(self,options):
        self.o=options;self.last=None;self.seen=None;self.consecutive=0;self.active=False;self.tracks=[]

    def update(self,features,capture_ns):
        if self.last is not None and capture_ns<=self.last:return self.snapshot(capture_ns)
        dt=0 if self.last is None else (capture_ns-self.last)/1e9
        self.snapshot(capture_ns)
        contiguous=self.last is None or dt*1000<=self.o.hand_leave_ms
        self.last=capture_ns
        if features:
            self.consecutive=(self.consecutive+1) if contiguous else 1;self.seen=capture_ns
            if self.consecutive>=self.o.hand_enter_frames:self.active=True
            remaining=list(self.tracks);tracks=[]
            for f in features[:2]:
                if len(f)!=4 or not np.isfinite(f).all():raise ValueError('手势特征无效')
                old=min(remaining,key=lambda t:math.hypot(t[0]-f[0],t[1]-f[1])) if remaining and contiguous else None
                if old is not None:remaining.remove(old)
                opening=float(f[2]);velocity=[0.,0.];mode=0;history=[]
                if old is not None:
                    history=(old[7]+[opening])[-3:];opening=float(np.median(history))
                    opening=old[2]+(opening-old[2])*(1-math.exp(-dt*1000/self.o.hand_smoothing_ms))
                    velocity=[(f[0]-old[0])/max(dt,1e-6),(f[1]-old[1])/max(dt,1e-6)];mode=old[6]
                else:history=[opening]
                if opening<=self.o.hand_closed:mode=-1
                elif opening>=self.o.hand_open:mode=1
                tracks.append([float(f[0]),float(f[1]),opening,float(f[3]),*velocity,mode,history])
            self.tracks=tracks
        else:self.consecutive=0
        return self.snapshot(capture_ns)

    def snapshot(self,now_ns):
        if self.seen is None or now_ns-self.seen>self.o.hand_leave_ms*1e6:self.active=False;self.tracks=[]
        hands=[t[:7] for t in self.tracks] if self.active else []
        distance=math.hypot(hands[0][0]-hands[1][0],hands[0][1]-hands[1][1]) if len(hands)==2 else 0.
        return dict(control=int(self.active),hands=hands,distance=distance)
