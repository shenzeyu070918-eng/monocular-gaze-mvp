"""Capture-clock I-VT with a short endpoint window and explicit unit fallback."""
from collections import deque
import numpy as np
from .config import FixationSettings
from .devices import finite
from .filters import position
from .evaluation import screen_ray, ray_angle


class FixationDetector:
    def __init__(self, settings=None):
        self.settings = settings or FixationSettings()
        self.window=deque()
        self._clear_segment()
        self.last_time=self.basis=None

    def _clear_segment(self):
        self.start_s=self.end_s=None
        self.total=np.zeros(2)
        self.count=0

    def _segment(self):
        if not self.count:return None
        start,end=self.start_s,self.end_s
        if (end-start)*1000 < self.settings.min_duration_ms:return None
        return dict(start_s=start,end_s=end,duration_ms=(end-start)*1000,
                    center=(self.total/self.count).tolist(),sample_count=self.count,velocity_basis=self.basis)

    def reset(self):
        segment=self._segment()
        self.window.clear();self._clear_segment();self.last_time=self.basis=None
        return [segment] if segment else []

    def update(self, value, timestamp_s, *, origin_m=None, geometry=None):
        value,timestamp_s = position(value),finite(timestamp_s,'timestamp_s')
        if self.last_time is not None and timestamp_s <= self.last_time:
            raise ValueError('取帧时间戳必须严格递增')
        fallback_reason=None;ray=None
        if geometry is None or not geometry.measured or geometry.width_cm is None or geometry.height_cm is None:
            fallback_reason='unmeasured_geometry'
        elif origin_m is None:
            fallback_reason='missing_origin'
        else:
            try:ray=screen_ray(origin_m,value,geometry)
            except (ValueError,TypeError):fallback_reason='invalid_origin'
        basis='angular' if ray is not None else 'screen_normalized'
        finished=[]
        if self.last_time is not None and (basis!=self.basis or (timestamp_s-self.last_time)*1000>self.settings.max_gap_ms):
            finished=self.reset()
        self.basis=basis;self.last_time=timestamp_s
        self.window.append((timestamp_s,value,ray))
        # Keep one sample bracketing the time window, never assume a frame rate.
        while len(self.window)>2 and timestamp_s-self.window[1][0]>=self.settings.window_ms/1000:
            self.window.popleft()
        velocity=None
        if len(self.window)>1:
            first=self.window[0];dt=timestamp_s-first[0]
            distance=ray_angle(first[2],ray) if basis=='angular' else float(np.linalg.norm(value-first[1]))
            velocity=distance/dt
        threshold=self.settings.angular_threshold_deg_s if basis=='angular' else self.settings.fallback_threshold_norm_s
        if velocity is not None and velocity>=threshold:
            segment=self._segment()
            if segment:finished.append(segment)
            self._clear_segment();state='saccade'
        else:
            if not self.count:self.start_s=timestamp_s
            self.end_s=timestamp_s;self.total+=value;self.count+=1
            state='fixation' if self._segment() is not None else 'candidate' if velocity is not None else 'unknown'
        return dict(state=state,velocity=velocity,velocity_unit='deg/s' if basis=='angular' else 'screen_norm/s',
                    velocity_basis=basis,fallback_reason=fallback_reason,
                    current_fixation=self._segment(),finished_fixations=finished)
