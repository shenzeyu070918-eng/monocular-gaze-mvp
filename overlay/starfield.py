"""Numeric star animation off Qt's thread; a batched, main-thread-only painter.

The worker owns the model.  A single latest-frame slot prevents GUI/render
backpressure from queuing camera data.  Animation ticks never invent gaze
samples.  No image, screen capture or camera object enters this module.
"""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import threading
import time
from types import MappingProxyType

import numpy as np
import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QPolygonF,
                          QRadialGradient)

from gaze_core.capture import timing_summary
from gaze_core.config import ROOT


def _immutable(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, np.ndarray):
        value.setflags(write=False)
    return value


def _polygon(points):
    """Bulk-copy Qt 6's contiguous QPointF (two qreal/doubles) storage.

    The polygon owns its copy and is never changed after publication.  This
    avoids 2,000 Python QPointF constructors on every animation frame.
    """
    points = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 2)
    result = QPolygonF()
    result.resize(len(points))
    if len(points):
        address = shiboken6.getCppPointer(result.data())[0]
        storage = shiboken6.VoidPtr(address, points.nbytes, True)
        np.copyto(np.frombuffer(storage, dtype=np.float64).reshape(-1, 2), points)
    return result


def _batches(snapshot):
    positions = np.asarray(snapshot['positions'], dtype=float)
    if not len(positions):
        return ()
    brightness = np.asarray(snapshot['brightness'], dtype=float)
    sizes = np.asarray(snapshot['sizes'], dtype=float)
    colors = np.asarray(snapshot['colors'], dtype=np.uint8)
    # Six opacity levels and half-pixel widths keep raster calls bounded while
    # retaining the model palette.  These affect drawing, never gaze data.
    alpha = np.clip(np.rint(brightness * 6), 0, 6).astype(np.int16)
    widths = np.clip(np.rint(sizes * 2), 1, 16).astype(np.int16)
    keys = np.column_stack((colors, alpha, widths))
    groups, indices = np.unique(keys, axis=0, return_inverse=True)
    result = []
    for index, group in enumerate(groups):
        red, green, blue, opacity, width = map(int, group)
        if opacity:
            result.append(dict(polygon=_polygon(positions[indices == index]),
                               color=(red, green, blue, round(opacity / 6 * 255)),
                               width=width / 2))
    return tuple(result)


class _Timings:
    def __init__(self, long_frame_ms):
        self.long_frame_ms = float(long_frame_ms)
        self.values = []
        self.starts = []
        self.long_frames = 0
        self._lock = threading.Lock()

    def record(self, started_ns, duration_ms):
        with self._lock:
            self.values.append(float(duration_ms))
            self.starts.append(int(started_ns))
            self.long_frames += duration_ms > self.long_frame_ms

    def report(self):
        with self._lock:
            values, starts = self.values[:], self.starts[:]
            long_frames = self.long_frames
        elapsed = (starts[-1] - starts[0]) / 1e9 if len(starts) > 1 else 0.
        return dict(count=len(values), milliseconds=timing_summary(values),
                    actual_fps=(len(starts) - 1) / elapsed if elapsed > 0 else None,
                    long_frame_ms=self.long_frame_ms, long_frames=long_frames)


class PaintEventTimer:
    """Measure an entire paintEvent, including background, text and debug UI.

    Call ``start`` before constructing QPainter and ``finish`` after ending it,
    or wrap that whole scope in ``with timer.measure():``. This timer does not
    request repaints, sample hardware or write files. Its budget is supplied by
    the caller's configuration; it is independent of the 60 Hz interval.
    """
    def __init__(self, budget_ms=8., *, clock_ns=time.perf_counter_ns):
        if isinstance(budget_ms, bool) or not math.isfinite(budget_ms) or budget_ms <= 0:
            raise ValueError('绘制预算必须为有限正数（ms）。')
        self.budget_ms = float(budget_ms)
        self.clock_ns = clock_ns
        self._timings = _Timings(self.budget_ms)

    def start(self):
        return int(self.clock_ns())

    def finish(self, started_ns):
        ended_ns = int(self.clock_ns())
        if ended_ns < started_ns:
            raise ValueError('绘制计时的单调时钟不得倒退。')
        elapsed_ms = (ended_ns - started_ns) / 1e6
        self._timings.record(started_ns, elapsed_ms)
        return elapsed_ms

    @contextmanager
    def measure(self):
        started_ns = self.start()
        try:
            yield
        finally:
            self.finish(started_ns)

    def report(self):
        value = self._timings.report()
        return dict(count=value['count'], milliseconds=value['milliseconds'],
                    actual_fps=value['actual_fps'], budget_ms=self.budget_ms,
                    over_budget_frames=value['long_frames'])


class StarfieldWorker:
    """Explicit lifecycle; no updates before start or after pause/close.

    ``latest()`` returns an owned immutable snapshot.  ``flush`` is a drain
    fence for tests and deterministic shutdown, and is not used per UI tick.
    """
    def __init__(self, width, height, radii, settings=None, *,
                 clock_ns=time.perf_counter_ns, model_factory=None, seed=None):
        from gaze_core.starfield import StarfieldModel, StarfieldSettings
        self.settings = settings or StarfieldSettings()
        self.width, self.height, self.radii = width, height, tuple(radii)
        self.clock_ns = clock_ns
        self._model_factory = model_factory or StarfieldModel
        self._seed = seed
        self._queue = queue.SimpleQueue()
        self._wake = threading.Event()
        self._frame_lock = threading.Lock()
        self._pending_frame = None
        self._last_capture_ns = None
        self._latest = None
        self._events = deque()
        self._event_lock = threading.Lock()
        self._started = False
        self.closed = False
        self.error = None
        self._model = None
        self._version = 0
        self._observed_ns = 0
        self.processed_frames = 0
        self.dropped_frames = 0
        self._timings = _Timings(self.settings.long_frame_ms)
        self.thread = threading.Thread(target=self._run, name='gaze-stars', daemon=True)

    def start(self):
        if self.closed:
            return False
        if not self._started:
            self._started = True
            self.thread.start()
        return True

    def _put(self, kind, now_ns=None):
        if not self.start():
            return False
        self._queue.put((kind, int(self.clock_ns() if now_ns is None else now_ns)))
        self._wake.set()
        return True

    def presented(self, now_ns=None):
        return self._put('presented', now_ns)

    def pause(self, now_ns=None):
        return self._put('pause', now_ns)

    def resume(self, now_ns=None):
        return self._put('resume', now_ns)

    def ingest(self, position, capture_ns, now_ns=None, valid=True):
        if not self._started or self.closed:
            return False
        capture_ns = int(capture_ns)
        position = None if position is None else tuple(float(v) for v in position)
        frame = (position, capture_ns,
                 int(self.clock_ns() if now_ns is None else now_ns), bool(valid))
        # A malformed future timestamp must not poison the high-water mark and
        # make every subsequent real capture look old.
        if capture_ns > frame[2]:
            return False
        with self._frame_lock:
            if self._last_capture_ns is not None and capture_ns <= self._last_capture_ns:
                return False
            self._last_capture_ns = capture_ns
            if self._pending_frame is not None:
                self.dropped_frames += 1
            self._pending_frame = frame
        self._wake.set()
        return True

    def latest(self):
        return self._latest

    def drain_events(self):
        with self._event_lock:
            result = list(self._events)
            self._events.clear()
        return result

    def flush(self, timeout=3):
        if not self._started:
            return True
        if not self.thread.is_alive():
            return self.error is None
        done = threading.Event()
        self._queue.put(('barrier', done))
        self._wake.set()
        return done.wait(timeout)

    def _collect_events(self):
        events = self._model.drain_events()
        if events:
            with self._event_lock:
                self._events.extend(events)

    def _model_time(self, value):
        # A handoff can wait behind an animation tick. Capture_ns remains the
        # evidence timestamp; model wall time must never move backwards.
        self._observed_ns = max(self._observed_ns, int(value))
        return self._observed_ns

    def _publish(self, now_ns, *, record=True, update=True):
        began = time.perf_counter_ns()
        if update:
            self._model.update(self._model_time(now_ns))
        snapshot = self._model.snapshot()
        snapshot['draw_batches'] = _batches(snapshot)
        snapshot['replay_polygon'] = _polygon(snapshot.get('replay_trajectory', ()))
        self._version += 1
        snapshot['version'] = self._version
        snapshot['published_ns'] = int(now_ns)
        elapsed = (time.perf_counter_ns() - began) / 1e6
        snapshot['update_ms'] = elapsed
        self._latest = _immutable(snapshot)
        self._collect_events()
        if record:
            self._timings.record(began, elapsed)

    def _run(self):
        active = False
        next_update = time.monotonic()
        try:
            self._model = self._model_factory(self.width, self.height, self.radii,
                                             self.settings, seed=self._seed)
            # Do not advance model time before an already queued first-paint
            # timestamp has been delivered.
            self._publish(self.clock_ns(), record=False, update=False)
            while True:
                self._wake.clear()
                barriers = []
                changed = False
                while True:
                    try:
                        kind, value = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if kind == 'close':
                        self._model.close(self._model_time(value[0]), reason=value[1])
                        self._collect_events()
                        return
                    if kind == 'barrier':
                        barriers.append(value)
                    elif kind == 'presented':
                        self._model.presented(self._model_time(value))
                        active = True
                        changed = True
                        next_update = time.monotonic()
                    elif kind == 'pause':
                        self._model.pause(self._model_time(value))
                        active = False
                        changed = True
                    elif kind == 'resume':
                        self._model.resume(self._model_time(value))
                        active = True
                        changed = True
                        next_update = time.monotonic()
                with self._frame_lock:
                    frame, self._pending_frame = self._pending_frame, None
                if active and frame is not None:
                    now_ns = self._model_time(max(frame[2], self.clock_ns()))
                    self._model.ingest(frame[0], frame[1], now_ns, valid=frame[3])
                    self.processed_frames += 1
                    changed = True
                now = time.monotonic()
                if changed or (active and now >= next_update):
                    self._publish(self.clock_ns(), record=active)
                    next_update = now + 1 / self.settings.animation_fps
                    if self._latest['phase'] == 'finished':
                        active = False
                for barrier in barriers:
                    barrier.set()
                timeout = max(0., next_update - time.monotonic()) if active else None
                self._wake.wait(timeout)
        except Exception as exc:
            self.error = '星空动画失败：' + str(exc)
        finally:
            self.closed = True
            # Do not strand test/shutdown fences after a model failure.
            while True:
                try:
                    kind, value = self._queue.get_nowait()
                except queue.Empty:
                    break
                if kind == 'barrier':
                    value.set()

    def performance_report(self):
        return dict(update=self._timings.report(), processed_gaze_frames=self.processed_frames,
                    dropped_gaze_frames=self.dropped_frames, error=self.error,
                    note='update 含模型更新、只读快照和批量绘制数据准备；不包含实际绘制。')

    def close(self, reason='escape', *, now_ns=None, timeout=3):
        if not self._started:
            self.closed = True
            return
        if self.thread.is_alive() and not self.closed:
            self.closed = True
            now = int(self.clock_ns() if now_ns is None else now_ns)
            self._queue.put(('close', (now, str(reason))))
            self._wake.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout)
        if self.thread.is_alive():
            raise RuntimeError('星空动画线程尚未退出，请稍后重试。')


class StarfieldPainter:
    """Cached glow and batched stars; only QPainter work happens on Qt's thread."""
    def __init__(self, settings=None, *, clock_ns=time.perf_counter_ns):
        from gaze_core.starfield import StarfieldSettings
        self.settings = settings or StarfieldSettings()
        self.clock_ns = clock_ns
        self.owner = threading.main_thread().ident
        self._draw_timings = _Timings(self.settings.long_frame_ms)
        self._combined_timings = _Timings(self.settings.long_frame_ms)
        self._glow = self._make_glow()
        self._heading = QFont()
        self._heading.setPixelSize(26)
        self._heading.setBold(True)
        self.closed = False

    @staticmethod
    def _make_glow():
        image = QImage(192, 192, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        gradient = QRadialGradient(QPointF(96, 96), 96)
        gradient.setColorAt(0., QColor(200, 225, 255, 115))
        gradient.setColorAt(.2, QColor(117, 164, 255, 90))
        gradient.setColorAt(.6, QColor(123, 87, 245, 35))
        gradient.setColorAt(1., QColor(50, 20, 130, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(0, 0, 192, 192))
        painter.end()
        return image

    def draw(self, painter, rect, snapshot, now_ns=None):
        if self.closed:
            return False
        if threading.get_ident() != self.owner:
            raise RuntimeError('星空绘制只能在主线程执行。')
        if snapshot is None:
            return False
        started = self.clock_ns()
        painter.save()
        try:
            painter.setClipRect(rect)
            painter.fillRect(rect, QColor('#040713'))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            position = snapshot.get('halo_position')
            opacity = float(snapshot.get('halo_opacity', 0.))
            if position is not None and opacity > 0:
                rx, ry = snapshot['halo_radii']
                painter.setOpacity(opacity)
                painter.drawImage(QRectF(position[0] - rx, position[1] - ry, rx * 2, ry * 2), self._glow)
                painter.setOpacity(1.)
            for batch in snapshot.get('draw_batches', ()):
                painter.setPen(QPen(QColor(*batch['color']), batch['width'],
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPoints(batch['polygon'])
            self._constellations(painter, snapshot)
            if snapshot.get('phase') in {'replay', 'finished'}:
                self._replay(painter, snapshot)
        finally:
            painter.restore()
            elapsed = (self.clock_ns() - started) / 1e6
            self._draw_timings.record(started, elapsed)
            self._combined_timings.record(started, elapsed + float(snapshot.get('update_ms', 0.)))
        return True

    def _constellations(self, painter, snapshot):
        painter.setFont(self._heading)
        for item in snapshot.get('constellations', ()):
            opacity = float(item['reveal_alpha'])
            if opacity <= 0:
                continue
            painter.setOpacity(opacity)
            vertices = item['vertices']
            painter.setPen(QPen(QColor('#a9cbff'), 2.5))
            for first, second in item['edges']:
                a, b = vertices[int(first)], vertices[int(second)]
                painter.drawLine(QPointF(*a), QPointF(*b))
            if len(vertices):
                top = np.min(vertices, axis=0)
                painter.setPen(QColor('#e8eeff'))
                painter.drawText(QPointF(float(top[0]), float(top[1]) - 14), str(item['name']))
        painter.setOpacity(1.)

    @staticmethod
    def _replay(painter, snapshot):
        trajectory = snapshot.get('replay_polygon')
        if trajectory is None or trajectory.size() < 2:
            return
        painter.setPen(QPen(QColor(255, 220, 125, 170), 3.,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(trajectory)

    def performance_report(self):
        return dict(draw=self._draw_timings.report(), update_plus_draw=self._combined_timings.report(),
                    note='draw 为星空实际 QPainter 调用墙钟耗时；update 与 draw 分属不同线程，合计仅为本快照更新与此次绘制耗时相加，不代表完整 paintEvent 墙钟耗时、屏幕呈现延迟或 CPU 百分比。')

    def close(self):
        self.closed = True


def save_starfield_performance(path, worker, painter, *, frame_report=None):
    """Explicit one-off numeric JSON; never save a rendered/camera image."""
    path = Path(path).expanduser().resolve()
    directory = (ROOT / 'data').resolve()
    if not path.is_relative_to(directory) or path.suffix.lower() != '.json':
        raise ValueError('星空性能记录必须为 data/ 下的 JSON 文件。')
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                  worker=worker.performance_report(), painter=painter.performance_report())
    if frame_report is not None:
        report['paint_event'] = frame_report
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report
