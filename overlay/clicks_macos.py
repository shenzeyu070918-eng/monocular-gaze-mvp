"""AppKit mouse-down monitors only. No event tap or keyboard observation.

The global monitor omits this application, so a local mouse-only monitor marks
our own UI and always returns the original event unchanged. Retain both tokens
and handlers until removal; late callbacks are guarded after stop.
"""
import threading
import time

from gaze_core.event_clock import NativeClockMapper, callback_time
from .click_listener import NativeMouseDown


def cocoa_to_logical(point, primary_top):
    """Cocoa screen points use bottom-left; Qt global logical points use top-left."""
    return float(point[0]), float(primary_top) - float(point[1])


class MacMouseDown:
    def __init__(self, screen, callback, *, appkit=None, foundation=None,
                 clock_ns=time.perf_counter_ns, mapper=None, call_after=None,
                 clock_options=None):
        if appkit is None:
            import AppKit as appkit
        if foundation is None:
            import Foundation as foundation
        self.appkit = appkit
        self.callback = callback
        self.clock_ns = clock_ns
        self.mapper = mapper or NativeClockMapper(
            lambda: foundation.NSProcessInfo.processInfo().systemUptime(), clock_ns=clock_ns,
            **dict(clock_options or {}))
        self._call_after = call_after
        self._lock = threading.RLock()
        self._active = False
        self._tokens = []
        self._handlers = []
        self.error = None
        screens = list(appkit.NSScreen.screens())
        if len(screens) != 1:
            raise RuntimeError('点击采集只支持唯一启用的内置屏，请断开外接显示器。')
        frame = screens[0].frame()
        self.primary_top = float(frame.origin.y + frame.size.height)
        if (abs(frame.size.width - screen.width) > 1 or abs(frame.size.height - screen.height) > 1):
            raise RuntimeError('AppKit 与内置屏逻辑尺寸不一致，拒绝记录点击坐标。')

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('macOS 鼠标监视器必须在应用主线程启动。')
        with self._lock:
            if self._active:
                return
            self._active = True
        self.mapper.refresh()
        kit = self.appkit
        mask = (kit.NSEventMaskLeftMouseDown | kit.NSEventMaskRightMouseDown |
                kit.NSEventMaskOtherMouseDown)
        global_handler = lambda event: self._event(event, own=False)
        def local_handler(event):
            self._event(event, own=True)
            return event
        self._handlers = [global_handler, local_handler]
        try:
            for add, handler in ((kit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_, global_handler),
                                 (kit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_, local_handler)):
                token = add(mask, handler)
                if token is None:
                    raise RuntimeError('NSEvent 鼠标按下监视器未能创建。')
                self._tokens.append(token)
        except Exception:
            self.stop()
            raise

    def _event(self, event, *, own):
        received = int(self.clock_ns())
        with self._lock:
            if not self._active:
                return
        try:
            point = event.locationInWindow()
            if own:
                window = event.window()
                if window is not None:
                    point = window.convertPointToScreen_(point)
            x, y = cocoa_to_logical((point.x, point.y), self.primary_top)
            number = int(event.buttonNumber())
            button = {0: 'left', 1: 'right', 2: 'middle'}.get(number, 'other')
            try:
                timing = self.mapper.map(event.timestamp(), received)
            except Exception:
                timing = callback_time(received)
            self.callback(NativeMouseDown(x, y, button, timing, own_ui=own))
        except Exception as exc:
            # AppKit handlers must never throw into the native event loop.
            self.error = 'macOS 鼠标按下处理失败：' + str(exc)

    def _remove(self):
        with self._lock:
            tokens, self._tokens = self._tokens, []
        for token in tokens:
            self.appkit.NSEvent.removeMonitor_(token)
        self._handlers = []

    def stop(self):
        with self._lock:
            self._active = False
            if not self._tokens:
                return
        if threading.current_thread() is threading.main_thread():
            self._remove()
        else:
            # Queue-overflow/recording-failure cleanup can originate on the
            # numeric delivery worker; AppKit disposal stays on its main loop.
            call_after = self._call_after
            if call_after is None:
                from PyObjCTools.AppHelper import callAfter
                call_after = callAfter
            call_after(self._remove)
