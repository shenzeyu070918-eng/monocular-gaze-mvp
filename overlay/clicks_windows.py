"""WH_MOUSE_LL on its own message thread; mouse-down only, never a keyboard hook.

The hook uses its event's original physical point and timestamp, not the cursor
position when Python later runs. All messages are unconditionally passed along.
"""
import ctypes as C
from ctypes import wintypes as W
import threading
import time
from dataclasses import replace

from gaze_core.event_clock import NativeClockMapper, callback_time, expand_windows_ticks
from .click_listener import NativeMouseDown


WH_MOUSE_LL = 14
WM_QUIT = 0x0012
DOWN_MESSAGES = {0x0201: 'left', 0x0204: 'right', 0x0207: 'middle', 0x020b: 'other'}
CALLBACK = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)
HOOKPROC = CALLBACK(C.c_ssize_t, C.c_int, W.WPARAM, W.LPARAM)


class Point(C.Structure):
    _fields_ = [('x', C.c_int32), ('y', C.c_int32)]


class Rect(C.Structure):
    _fields_ = [('left', C.c_int32), ('top', C.c_int32), ('right', C.c_int32), ('bottom', C.c_int32)]


class MouseHookData(C.Structure):
    _fields_ = [('pt', Point), ('mouseData', C.c_uint32), ('flags', C.c_uint32),
                ('time', C.c_uint32), ('dwExtraInfo', C.c_size_t)]


def physical_to_logical(point, physical_rect, screen):
    """Unique built-in monitor physical pixels -> its Qt logical rectangle."""
    left, top, right, bottom = physical_rect
    x, y = point
    if not (left <= x < right and top <= y < bottom):
        return None
    return screen.x + (x - left) / screen.dpr, screen.y + (y - top) / screen.dpr


def validate_monitor(physical_rect, screen):
    left, top, right, bottom = physical_rect
    # Qt rounds logical monitor dimensions; tolerate at most one logical pixel.
    if (right <= left or bottom <= top or
            abs((right - left) / screen.dpr - screen.width) > 1 or
            abs((bottom - top) / screen.dpr - screen.height) > 1):
        raise RuntimeError('Windows 物理屏幕与 Qt 逻辑尺寸/DPI 不一致，拒绝记录点击。')
    return tuple(physical_rect)


class WindowsMouseDown:
    def __init__(self, screen, callback, *, user32=None, kernel32=None,
                 clock_ns=time.perf_counter_ns, physical_rect=None, mapper=None,
                 own_handles=None, clock_options=None):
        self.user = user32 or C.WinDLL('user32', use_last_error=True)
        self.kernel = kernel32 or C.WinDLL('kernel32', use_last_error=True)
        self.screen = screen
        self.callback = callback
        self.clock_ns = clock_ns
        self.own_handles = own_handles
        self._thread = None
        self._thread_id = None
        self._hook = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self.error = None
        self._proc = HOOKPROC(self._event)
        self._configure()
        self.physical_rect = validate_monitor(
            physical_rect if physical_rect is not None else self._monitor(), screen)
        resolution = self._tick_resolution()
        options = dict(max_uncertainty_ns=0 if resolution is None else resolution + 5_000_000,
                       max_drift_ns=20_000_000 if resolution is None else max(20_000_000, 2 * resolution))
        options.update(clock_options or {})
        self.mapper = mapper or NativeClockMapper(
            (lambda: self.kernel.GetTickCount64() if resolution is not None else None), ns_per_unit=1_000_000,
            native_resolution_ns=resolution or 0, clock_ns=clock_ns,
            # GetTickCount64's millisecond units are not 1 ms precision. The
            # actual OS timer increment is read below, never fabricated.
            **options)

    def _configure(self):
        specs = {
            'SetWindowsHookExW': ([C.c_int, HOOKPROC, C.c_void_p, C.c_uint32], C.c_void_p),
            'UnhookWindowsHookEx': ([C.c_void_p], W.BOOL),
            'CallNextHookEx': ([C.c_void_p, C.c_int, W.WPARAM, W.LPARAM], C.c_ssize_t),
            'GetMessageW': ([C.POINTER(W.MSG), C.c_void_p, C.c_uint32, C.c_uint32], C.c_int),
            'PeekMessageW': ([C.POINTER(W.MSG), C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32], W.BOOL),
            'PostThreadMessageW': ([C.c_uint32, C.c_uint32, W.WPARAM, W.LPARAM], W.BOOL),
            'TranslateMessage': ([C.POINTER(W.MSG)], W.BOOL),
            'DispatchMessageW': ([C.POINTER(W.MSG)], C.c_ssize_t),
            'SetThreadDpiAwarenessContext': ([C.c_void_p], C.c_void_p),
            'WindowFromPoint': ([Point], C.c_void_p),
            'GetAncestor': ([C.c_void_p, C.c_uint32], C.c_void_p),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.user, name)
            function.argtypes = args
            function.restype = result
        for name, args, result in (
                ('GetTickCount64', [], C.c_uint64),
                ('GetCurrentThreadId', [], C.c_uint32),
                ('GetModuleHandleW', [C.c_wchar_p], C.c_void_p),
                ('GetSystemTimeAdjustment', [C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.POINTER(W.BOOL)], W.BOOL)):
            function = getattr(self.kernel, name)
            function.argtypes = args
            function.restype = result

    def _tick_resolution(self):
        adjustment = C.c_uint32()
        increment = C.c_uint32()
        disabled = W.BOOL()
        if not self.kernel.GetSystemTimeAdjustment(C.byref(adjustment), C.byref(increment), C.byref(disabled)):
            return None
        return int(increment.value) * 100 if increment.value else None

    def _monitor(self):
        rectangles = []
        enumproc = CALLBACK(W.BOOL, C.c_void_p, C.c_void_p, C.POINTER(Rect), W.LPARAM)
        def collect(_, __, rect, ___):
            value = rect.contents
            rectangles.append((value.left, value.top, value.right, value.bottom))
            return True
        enum = enumproc(collect)
        self.user.EnumDisplayMonitors.argtypes = [C.c_void_p, C.c_void_p, enumproc, W.LPARAM]
        self.user.EnumDisplayMonitors.restype = W.BOOL
        previous = self.user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if not previous:
            raise RuntimeError('无法建立 Windows per-monitor DPI 坐标环境。')
        try:
            success = self.user.EnumDisplayMonitors(None, None, enum, 0)
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)
        if not success or len(rectangles) != 1:
            raise RuntimeError('点击采集需要唯一启用的内置屏；不能自动选择主屏或外接屏。')
        return rectangles[0]

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._ready.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, name='gaze-mouse-hook', daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.stop()
            raise RuntimeError('Windows 鼠标消息线程启动超时。')
        if self.error:
            self.stop()
            raise RuntimeError(self.error)

    def _run(self):
        previous_dpi = None
        try:
            previous_dpi = self.user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
            if not previous_dpi:
                raise RuntimeError('无法建立 Windows per-monitor DPI 鼠标坐标环境。')
            self._thread_id = int(self.kernel.GetCurrentThreadId())
            message = W.MSG()
            # Force creation before another thread can post WM_QUIT.
            self.user.PeekMessageW(C.byref(message), None, 0, 0, 0)
            self.mapper.refresh()
            self._hook = self.user.SetWindowsHookExW(WH_MOUSE_LL, self._proc,
                                                    self.kernel.GetModuleHandleW(None), 0)
            if not self._hook:
                code = getattr(C, 'get_last_error', lambda: 0)()
                raise RuntimeError(f'WH_MOUSE_LL 注册失败，WinError={code}。')
            self._ready.set()
            while not self._stopping.is_set():
                result = self.user.GetMessageW(C.byref(message), None, 0, 0)
                if result == 0:
                    if not self._stopping.is_set():
                        raise RuntimeError('Windows 鼠标消息线程意外结束。')
                    break
                if result < 0:
                    raise RuntimeError('Windows 鼠标消息读取失败。')
                self.user.TranslateMessage(C.byref(message))
                self.user.DispatchMessageW(C.byref(message))
        except Exception as exc:
            self.error = 'Windows 点击采集失败：' + str(exc)
        finally:
            if self._hook:
                self.user.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._ready.set()
            self._thread_id = None
            if previous_dpi:
                self.user.SetThreadDpiAwarenessContext(previous_dpi)

    def _event(self, code, message, data):
        received = int(self.clock_ns())
        try:
            if code >= 0 and int(message) in DOWN_MESSAGES and not self._stopping.is_set():
                event = C.cast(data, C.POINTER(MouseHookData)).contents
                point = physical_to_logical((event.pt.x, event.pt.y), self.physical_rect, self.screen)
                if point is None:
                    # Transfer no foreign-screen coordinates. The controller
                    # only receives an out-of-bounds sentinel for its count.
                    point = (self.screen.x - 1., self.screen.y - 1.)
                try:
                    expanded = expand_windows_ticks(int(event.time), int(self.kernel.GetTickCount64()))
                    mapped = self.mapper.map(expanded, received)
                    # Preserve the original DWORD as the native provenance.
                    timing = replace(mapped, native_timestamp=float(event.time))
                except Exception:
                    timing = callback_time(received, float(event.time))
                own = None
                if self.own_handles is not None:
                    # Compare only against our cached Qt HWNDs. Never query a
                    # foreign title, process name, application or window text.
                    handles = self.own_handles()
                    window = self.user.WindowFromPoint(event.pt)
                    root = self.user.GetAncestor(window, 2) if window else None
                    own = bool(window in handles or root in handles)
                self.callback(NativeMouseDown(*point, DOWN_MESSAGES[int(message)], timing, own_ui=own))
        except Exception as exc:
            self.error = 'Windows 鼠标按下处理失败：' + str(exc)
        # Never suppress a click, including when our validation/recorder fails.
        return self.user.CallNextHookEx(self._hook, code, message, data)

    def stop(self):
        self._stopping.set()
        thread = self._thread
        ident = self._thread_id
        if ident is not None:
            self.user.PostThreadMessageW(ident, WM_QUIT, 0, 0)
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError('Windows 鼠标消息线程未及时退出。')
        self._thread = None
