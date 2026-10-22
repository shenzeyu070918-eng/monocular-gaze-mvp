"""Full built-in-screen logical coordinates; independent of Qt and cameras."""
from dataclasses import dataclass
from numbers import Integral
from gaze_core.devices import finite


@dataclass(frozen=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int
    dpr: float = 1.
    native_name: str | None = None  # Windows GDI display name, resolved from built-in identity.
    cocoa_top: float | None = None  # Desktop y-flip reference, not built-in height.

    def __post_init__(self):
        for key in ('x','y','width','height'):
            value=getattr(self,key)
            if isinstance(value,bool) or not isinstance(value,Integral):
                raise ValueError('屏幕逻辑坐标必须为整数')
        if self.width<2 or self.height<2:raise ValueError('屏幕逻辑尺寸无效')
        finite(self.dpr,'devicePixelRatio',positive=True)

    def local(self, point):
        if len(point)!=2:raise ValueError('屏幕坐标必须为二维')
        u,v=(finite(value,'screen coordinate') for value in point)
        # QRect's last addressable corner is (width-1, height-1). Qt already
        # applies devicePixelRatio; multiplying it here would scale twice.
        return u*(self.width-1),v*(self.height-1)

    def global_position(self, point):
        x,y=self.local(point)
        return self.x+x,self.y+y

    def radii(self, cm, geometry, fallback_px):
        if geometry.measured and geometry.width_cm and geometry.height_cm:
            return cm*self.width/geometry.width_cm,cm*self.height/geometry.height_cm
        return fallback_px,fallback_px
