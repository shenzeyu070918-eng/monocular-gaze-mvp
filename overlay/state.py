"""Camera-independent visibility state; no profiles, calibration or Qt."""
from dataclasses import dataclass
from gaze_core.devices import finite


@dataclass(frozen=True)
class RingView:
    position: tuple | None
    opacity: float
    label: str


class RingState:
    def __init__(self, options):
        self.options=options
        self.clear()

    def clear(self):
        self.position=None;self.capture_ns=None;self.invalid_ns=None
        self.reason='等待画面';self.sequence=-1

    def accept(self, result, now_ns):
        if result.sequence<=self.sequence:return
        self.sequence=result.sequence
        display=result.data.get('display',{})
        age=now_ns-result.captured_ns
        valid=(result.data.get('valid') is True and result.data.get('status')=='tracking'
               and display.get('timestamp_source')=='capture_ns' and 0<=age<self.options.stale_ms*1_000_000)
        point=display.get('position')
        try:
            if not valid or point is None or len(point)!=2:raise ValueError('无效结果')
            position=tuple(finite(v,'overlay position') for v in point)
        except (ValueError,TypeError):
            reason={'blink':'眨眼','no_face':'未检测到人脸'}.get(result.data.get('status'),'画面无效或已过期')
            self.invalidate(now_ns,reason)
            return
        self.position=position;self.capture_ns=result.captured_ns
        self.invalid_ns=None;self.reason='Python 摄像头 · 半径为配置示意值'

    def invalidate(self,now_ns,reason):
        if self.invalid_ns is None:self.invalid_ns=now_ns
        self.reason=reason

    def view(self,now_ns):
        invalid=self.invalid_ns
        if self.capture_ns is not None:
            deadline=self.capture_ns+int(self.options.stale_ms*1_000_000)
            if now_ns>=deadline:
                invalid=deadline if invalid is None else min(invalid,deadline)
                if self.invalid_ns is None:self.reason='数据过期'
        amount=1. if invalid is None else max(0.,1-(now_ns-invalid)/(self.options.fade_ms*1_000_000))
        opacity=self.options.opacity*amount if self.position is not None else 0.
        return RingView(self.position if opacity>0 else None,opacity,'未校准 · '+self.reason)
