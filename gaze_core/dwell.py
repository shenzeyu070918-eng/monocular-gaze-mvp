"""Dwell selection in logical pixels. No Qt, camera, I/O or wall-clock time.

Only consecutive, valid capture intervals accumulate. Grace/loss intervals
freeze progress. Sticky expansion applies solely to the current candidate.
"""
from dataclasses import dataclass
from numbers import Integral
from .devices import finite


@dataclass(frozen=True)
class Button:
    id: str
    rect: tuple  # left, top, width, height; logical pixels, never Retina pixels

    def contains(self, point, expansion=(0.,0.)):
        x,y=point;left,top,width,height=self.rect;rx,ry=expansion
        return left-rx<=x<=left+width+rx and top-ry<=y<=top+height+ry


class DwellSelector:
    def __init__(self, options, radii, *, stale_ms=300.):
        self.options=options
        self.expansion=tuple(finite(v,'radius',positive=True)*options.hit_expand_ratio for v in radii)
        self.stale_ns=int(stale_ms*1e6)
        self.buttons=();self.last_capture=None;self.cooldown_until=0
        self.clear()

    def clear(self):
        self.current=None;self.accumulated_ns=0;self.entered_ns=None
        self.leave_ns=self.loss_ns=None;self.previous_inside=False

    def set_buttons(self, buttons):
        self.buttons=tuple(buttons);self.clear()

    def advance(self, now_ns):
        if self.last_capture is not None and now_ns-self.last_capture>self.stale_ns:
            if self.loss_ns is None:self.loss_ns=self.last_capture
            self.previous_inside=False
        if ((self.loss_ns is not None and now_ns-self.loss_ns>self.options.loss_reset_ms*1e6)
                or (self.leave_ns is not None and now_ns-self.leave_ns>self.options.leave_grace_ms*1e6)):
            self.clear()

    def feed(self, point, capture_ns, now_ns, *, valid=True):
        if isinstance(capture_ns,bool) or not isinstance(capture_ns,Integral) or capture_ns<0:
            raise ValueError('capture_ns 必须为非负整数')
        capture_ns=int(capture_ns)
        if capture_ns>now_ns or (self.last_capture is not None and capture_ns<=self.last_capture):return None
        self.advance(now_ns)
        previous=self.last_capture;self.last_capture=capture_ns
        if not valid or point is None or now_ns-capture_ns>self.stale_ns:
            if self.loss_ns is None:self.loss_ns=now_ns
            self.previous_inside=False
            return None
        point=tuple(finite(v,'display position') for v in point)
        if len(point)!=2:raise ValueError('display position 必须为二维')
        self.loss_ns=None
        if now_ns<self.cooldown_until:self.clear();return None
        hit=self.current if self.current is not None and self.current.contains(point,self.expansion) else None
        if hit is None and self.current is not None:
            if self.leave_ns is None:self.leave_ns=now_ns
            self.previous_inside=False
            if now_ns-self.leave_ns<=self.options.leave_grace_ms*1e6:return None
            self.clear()
        if hit is None:hit=next((button for button in self.buttons if button.contains(point)),None)
        if hit is None:return None
        if hit!=self.current:
            self.clear();self.current=hit;self.entered_ns=now_ns
        elif self.previous_inside and previous is not None and capture_ns-previous<=self.stale_ns:
            self.accumulated_ns+=capture_ns-previous
        self.leave_ns=None;self.previous_inside=True
        if self.accumulated_ns<self.options.dwell_ms*1e6:return None
        result=dict(target_id=hit.id,dwell_ms=self.accumulated_ns/1e6,
                    selection_ms=(now_ns-self.entered_ns)/1e6,capture_ns=capture_ns,event_ns=now_ns)
        self.cooldown_until=now_ns+int(self.options.cooldown_ms*1e6);self.clear()
        return result

    @property
    def progress(self):return min(1.,self.accumulated_ns/(self.options.dwell_ms*1e6))
