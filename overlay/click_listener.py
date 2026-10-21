"""Opt-in mouse-down delivery, independent of Qt and recording ownership.

The native callback transfers a small numeric record through a bounded queue.
The consumer must provide exclusion/handle functions backed by immutable cached
window data: these may run on a native callback thread and must not access Qt.
"""
from dataclasses import dataclass
import math
from numbers import Real
import queue
import sys
import threading
import time

from gaze_core.event_clock import EventTime


BUTTONS = frozenset(('left', 'right', 'middle', 'other'))
EXCLUSION_REASONS = frozenset(('own_ui', 'own_task', 'own_panel'))


@dataclass(frozen=True)
class NativeMouseDown:
    x: float
    y: float
    button: str
    timing: EventTime
    # True/False is an identity result, not a rectangle guess. None is reserved
    # for injected backends without an identity API and uses cached rectangles.
    own_ui: bool | None = None


def normalized_screen_point(screen, point):
    """Reject outside clicks; Qt logical coordinate scaling is applied once."""
    values = []
    for value in point:
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError('点击坐标必须为有限实数。')
        values.append(float(value))
    if len(values) != 2:
        raise ValueError('点击坐标必须为二维。')
    x, y = values
    if not (screen.x <= x < screen.x + screen.width and
            screen.y <= y < screen.y + screen.height):
        return None
    # Native Retina coordinates can land in the last half logical pixel. Keep
    # those real inside-screen clicks while retaining M3's integer-corner map.
    # This edge convention applies only to physical clicks, never gaze/raw.
    return min(1., (x - screen.x) / (screen.width - 1)), min(1., (y - screen.y) / (screen.height - 1))


def native_backend(screen, callback, *, clock_ns=time.perf_counter_ns,
                   own_handles=None, clock_options=None):
    if sys.platform == 'darwin':
        from .clicks_macos import MacMouseDown
        return MacMouseDown(screen, callback, clock_ns=clock_ns, clock_options=clock_options)
    if sys.platform == 'win32':
        from .clicks_windows import WindowsMouseDown
        return WindowsMouseDown(screen, callback, clock_ns=clock_ns,
                                own_handles=own_handles, clock_options=clock_options)
    raise RuntimeError('点击采集暂只支持 macOS 和 Windows。')


class ClickListener:
    def __init__(self, screen, emit, *, exclude=None, backend_factory=None,
                 clock_ns=time.perf_counter_ns, queue_size=1024, own_handles=None,
                 clock_options=None):
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size < 1:
            raise ValueError('点击交付队列大小必须为正整数。')
        self.screen = screen
        self.emit = emit
        self.exclude = exclude or (lambda point: None)
        self._factory = backend_factory or (lambda screen, cb: native_backend(
            screen, cb, clock_ns=clock_ns, own_handles=own_handles, clock_options=clock_options))
        self._queue_size = queue_size
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._active = False
        self._error = None
        self._backend = self._worker = self._queue = self._stop = None
        self._generation = 0
        self._counts = dict(received=0, accepted=0, excluded=0, outside=0)

    def start(self):
        with self._lifecycle:
            with self._lock:
                if self._active:
                    return True
            self.stop()
            with self._lock:
                self._generation += 1
                generation = self._generation
                self._queue = queue.Queue(self._queue_size)
                self._stop = threading.Event()
                self._error = None
                self._active = True
            try:
                backend = self._factory(self.screen, lambda event: self._receive(event, generation))
                self._backend = backend
                worker = threading.Thread(target=self._deliver, name='gaze-click-delivery', daemon=True)
                self._worker = worker
                worker.start()
                backend.start()
            except Exception as exc:
                with self._lock:
                    self._error = '点击采集启动失败：' + str(exc)
                self.stop()
                raise RuntimeError(self._error) from exc
            return True

    def _receive(self, event, generation):
        with self._lock:
            if not self._active or generation != self._generation:
                return
            self._counts['received'] += 1
            try:
                if (not isinstance(event, NativeMouseDown) or
                        (event.own_ui is not None and type(event.own_ui) is not bool)):
                    raise ValueError('鼠标事件身份格式无效。')
                if normalized_screen_point(self.screen, (event.x, event.y)) is None:
                    self._counts['outside'] += 1
                    return
                # Snapshot only our cached numeric rectangles while the event
                # is being received: a local mouse-down may immediately hide
                # its own window before the worker has time to drain the queue.
                # A known foreign event must not be excluded merely because a
                # background panel rectangle overlaps its screen position.
                reason = None if event.own_ui is False else self.exclude((float(event.x), float(event.y)))
                if reason is None and event.own_ui is True:
                    reason = 'own_ui'
                if reason is not None and reason not in EXCLUSION_REASONS:
                    raise ValueError('本程序点击排除原因不在白名单。')
                self._queue.put_nowait((event, reason))
            except queue.Full:
                self._active = False
                self._error = '点击交付队列已满，点击采集已停止。'
                self._stop.set()
            except Exception as exc:
                self._active = False
                self._error = '点击采集交付失败：' + str(exc)
                self._stop.set()

    def _deliver(self):
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    event = self._queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    self._one(*event)
                finally:
                    self._queue.task_done()
        except Exception as exc:
            with self._lock:
                self._active = False
                self._error = '点击采集交付失败：' + str(exc)
                self._stop.set()
        finally:
            if self._backend is not None:
                self._backend.stop()

    def _one(self, event, reason):
        if not isinstance(event, NativeMouseDown) or event.button not in BUTTONS:
            raise ValueError('只接受鼠标按下的固定数值格式。')
        if not isinstance(event.timing, EventTime):
            raise ValueError('点击事件缺少单调时间来源。')
        normalized = normalized_screen_point(self.screen, (event.x, event.y))
        if normalized is None:
            with self._lock:
                self._counts['outside'] += 1
            return
        fields = dict(event.timing.fields(), x=float(normalized[0]), y=float(normalized[1]),
                      button=event.button, excluded=reason is not None, exclusion_reason=reason)
        if self.emit('click', **fields) is False:
            raise RuntimeError('录制器拒绝点击；点击采集已停止。')
        with self._lock:
            self._counts['excluded' if reason is not None else 'accepted'] += 1

    def stop(self):
        with self._lifecycle:
            with self._lock:
                self._active = False
                backend, worker, stop = self._backend, self._worker, self._stop
            if backend is not None:
                backend.stop()
            if stop is not None:
                stop.set()
            if worker is not None and worker is not threading.current_thread():
                worker.join()
            self._backend = self._worker = self._queue = self._stop = None

    def status(self):
        with self._lock:
            native_error = getattr(self._backend, 'error', None)
            if native_error and self._active:
                self._active = False
                self._error = str(native_error)
                self._stop.set()
            return dict(self._counts, active=self._active, error=self._error)
