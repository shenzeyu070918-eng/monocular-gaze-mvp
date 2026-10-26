"""Capture-clock scene selection; callers serialize feed/step on one owner lock."""
import numpy as np

NONE,LEAVE,GAP,COOLDOWN,INVALID=range(5)


def _corner_at(point,radii,size,ratio,mode='corners',tolerance_ratio=None):
    try:
        p=np.asarray(point,dtype=float);r=np.asarray(radii,dtype=float);s=np.asarray(size,dtype=float)
        if p.shape!=r.shape or p.shape!=s.shape or p.shape!=(2,) or not np.isfinite([p,r,s]).all() or min(r)<=0 or min(s)<=0:
            return -1,INVALID
    except (TypeError,ValueError):return -1,INVALID
    clipped=np.clip(p,0,s)
    # Elliptical distance to the nearest screen edge/corner. This clamp is ONLY
    # for the hit test; neither the original gaze nor recorded xy is modified.
    tolerance=np.maximum(r,s*(ratio if tolerance_ratio is None else tolerance_ratio))
    if np.linalg.norm((p-clipped)/tolerance)>1:return -1,LEAVE
    uv=clipped/s
    if mode=='off':return -1,LEAVE
    if mode=='edges':return (0,NONE) if uv[0]<=ratio else (1,NONE) if uv[0]>=1-ratio else (-1,LEAVE)
    if not np.all((uv<=ratio)|(uv>=1-ratio)):return -1,LEAVE
    right,bottom=uv>.5
    return ((2 if right else 3) if bottom else (1 if right else 0)),NONE


class PatternCycle:
    """Region dwell with frozen grace, cooldown and a shared scene revision."""
    def __init__(self,options):
        self.options=options;self.pattern=0;self.revision=0;self.last=None;self.cool_until=0
        self.corner=-1;self.dwell_ns=0;self.away_since=None;self.away_reason=NONE
        self.transition_start=None;self.previous_pattern=0
        self.last_inside=False;self.completed_corner=-1;self.reset_reason=NONE

    def _clear(self,reason):
        self.corner=-1;self.dwell_ns=0;self.away_since=None;self.last_inside=False;self.reset_reason=reason

    def advance(self,direction,now_ns,*,completed_corner=-1):
        if type(direction) is not int or direction not in (-1,1):raise ValueError('场景方向必须为±1')
        if self.transition_start is not None:return self.snapshot(now_ns)
        self.previous_pattern=self.pattern;self.transition_start=now_ns
        self.pattern=(self.pattern+direction)%3;self.revision+=1
        self._clear(COOLDOWN);self.completed_corner=completed_corner
        self.cool_until=now_ns+int((self.options.transition_scatter_s+self.options.transition_s)*1e9)
        return self.snapshot(now_ns)

    def complete(self,revision,now_ns):
        if revision!=self.revision or self.transition_start is None or now_ns<self.cool_until:return False
        self.transition_start=None;self.completed_corner=-1;self._clear(COOLDOWN);return True

    def snapshot(self,now_ns=None):
        now=(self.last or 0) if now_ns is None else now_ns
        cooling=self.transition_start is not None or now<self.cool_until
        corner=self.completed_corner if cooling else self.corner
        duration=min(self.dwell_ns/1e6,self.options.dwell_ms)
        if cooling:duration=self.options.dwell_ms if corner>=0 else 0.
        state=3 if cooling else 2 if self.corner>=0 and self.away_since is not None else 1 if self.corner>=0 else 0
        return dict(switch_mode={'corners':0,'edges':1,'off':2}[self.options.interaction.scene_switch_mode],pattern=self.pattern,scene_revision=self.revision,scene_from=self.previous_pattern,scene_transition=int(self.transition_start is not None),scene_transition_ms=float(0 if self.transition_start is None else min(max(0,(now-self.transition_start)/1e6),(self.options.transition_scatter_s+self.options.transition_s)*1000)),corner=int(corner),corner_dwell_ms=float(duration),
                    corner_progress=float(duration/self.options.dwell_ms),corner_state=state,corner_reset=int(self.reset_reason))

    def feed(self,point,radii,capture_ns,valid=True,*,viewport_size=None):
        if self.last is not None and capture_ns<=self.last:return self.pattern
        dt=0 if self.last is None else capture_ns-self.last
        gap=dt>self.options.stale_ms*1e6;self.last=capture_ns
        if gap:self._clear(GAP)
        if self.transition_start is not None or capture_ns<self.cool_until:return self.pattern
        self.completed_corner=-1
        candidate,reason=_corner_at(point,radii,viewport_size,self.options.interaction.edge_ratio if self.options.interaction.scene_switch_mode=='edges' else self.options.interaction.corner_ratio,self.options.interaction.scene_switch_mode,self.options.interaction.corner_ratio) if valid else (-1,INVALID)
        grace=self.options.interaction.corner_leave_grace_ms*1e6
        if self.corner>=0 and self.away_since is not None and capture_ns-self.away_since>grace:
            self._clear(self.away_reason)
        if self.corner<0:
            if candidate>=0:self.corner=candidate;self.last_inside=True
            elif not gap:self.reset_reason=reason
            return self.pattern
        if candidate==self.corner:
            if self.away_since is None and self.last_inside:self.dwell_ns+=dt
            self.away_since=None;self.last_inside=True
            if self.dwell_ns>=self.options.dwell_ms*1e6:self.advance(-1 if self.options.interaction.scene_switch_mode=='edges' and self.corner==0 else 1,capture_ns,completed_corner=self.corner)
        else:
            if grace==0:
                self._clear(reason or LEAVE)
                if candidate>=0:self.corner=candidate;self.last_inside=True
                return self.pattern
            if self.away_since is None:self.away_since=capture_ns;self.away_reason=reason or LEAVE
            self.last_inside=False
        return self.pattern
