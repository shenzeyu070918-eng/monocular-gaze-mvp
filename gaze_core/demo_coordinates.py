"""Full-screen Demo coordinates, without Qt or physical-pixel conversions.

The screen rectangle includes the menu-bar area.  ``window_origin`` is the
actual client origin in global *logical* coordinates (for example Qt's
``mapToGlobal(QPoint(0, 0))``), not the outer native frame origin.  A native
full-screen transition may temporarily change that client rectangle; drawing
can still inspect the mappings. A notched Mac may place the full-screen client
below the menu/notch area; that contained rectangle is valid without rescaling
the full-screen normalized signal to the smaller client.
"""
from .devices import finite


def _pair(value, name):
    try:
        if len(value) != 2:
            raise ValueError(f'{name} 必须为二维坐标')
        return tuple(finite(v, name) for v in value)
    except TypeError as exc:
        raise ValueError(f'{name} 必须为二维坐标') from exc


class DemoCoordinates:
    def __init__(self, screen_rect, *, window_origin=None, window_size=None,
                 fullscreen=False, transitioning=False, tolerance_px=0.5):
        self.screen_origin = (finite(screen_rect.x, 'screen.x'),
                              finite(screen_rect.y, 'screen.y'))
        self.screen_size = (finite(screen_rect.width, 'screen.width'),
                            finite(screen_rect.height, 'screen.height'))
        if min(self.screen_size) < 2:
            raise ValueError('内屏逻辑尺寸必须至少为 2 × 2')
        # Retain the scale for diagnostics, but never multiply coordinates by it.
        self.dpr = finite(getattr(screen_rect, 'dpr', 1.), 'devicePixelRatio', positive=True)
        self.window_origin = None if window_origin is None else _pair(window_origin, 'window_origin')
        self.window_size = None if window_size is None else _pair(window_size, 'window_size')
        if self.window_size is not None and min(self.window_size) <= 0:
            raise ValueError('Demo 窗口逻辑尺寸必须为正数')
        if type(fullscreen) is not bool or type(transitioning) is not bool:
            raise ValueError('全屏与切换状态必须为布尔值')
        self.fullscreen, self.transitioning = fullscreen, transitioning
        self.tolerance_px = finite(tolerance_px, 'tolerance_px')
        if self.tolerance_px < 0:
            raise ValueError('几何容差必须非负')

    @property
    def geometry_matches(self):
        """Whether the actual client is wholly within the built-in screen."""
        if self.window_origin is None or self.window_size is None:
            return False
        tolerance=self.tolerance_px
        return all(s-tolerance<=w and w+size<=s+extent+tolerance
                   for s,extent,w,size in zip(self.screen_origin,self.screen_size,self.window_origin,self.window_size))

    @property
    def ready(self):
        return self.fullscreen and not self.transitioning and self.geometry_matches

    @property
    def reason(self):
        if self.transitioning:
            return 'Demo 全屏切换尚未完成'
        if not self.fullscreen:
            return 'Demo 尚未进入全屏'
        if self.window_origin is None or self.window_size is None:
            return '尚未获得 Demo 窗口的实际客户区坐标'
        if not self.geometry_matches:
            return '全屏窗口客户区超出内置屏逻辑区域，暂不进行命中判断'
        return ''

    def screen_to_global(self, position):
        u, v = _pair(position, 'screen position')
        x, y = self.screen_origin
        width, height = self.screen_size
        # Match ScreenRect.local: normalized 1 is the last addressable pixel.
        return x + u * (width - 1), y + v * (height - 1)

    def global_to_screen(self, position):
        x, y = _pair(position, 'global position')
        left, top = self.screen_origin
        width, height = self.screen_size
        return (x - left) / (width - 1), (y - top) / (height - 1)

    def global_to_window(self, position):
        if self.window_origin is None:
            raise ValueError('尚未获得 Demo 窗口的实际客户区原点')
        x, y = _pair(position, 'global position')
        return x - self.window_origin[0], y - self.window_origin[1]

    def window_to_global(self, position):
        if self.window_origin is None:
            raise ValueError('尚未获得 Demo 窗口的实际客户区原点')
        x, y = _pair(position, 'window position')
        return x + self.window_origin[0], y + self.window_origin[1]

    def screen_to_window(self, position):
        return self.global_to_window(self.screen_to_global(position))

    def window_to_screen(self, position):
        return self.global_to_screen(self.window_to_global(position))

    def hit_position(self, position):
        """Return an unclipped client point only when native geometry is ready."""
        if position is None or not self.ready:
            return None
        return self.screen_to_window(position)
