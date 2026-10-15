"""Direct-camera pipeline, independent of the synchronous browser camera path."""
import argparse
from dataclasses import dataclass, replace
import json
import math
import sys
import threading
import time

from .config import DEFAULT_CONFIG, load_settings


@dataclass(frozen=True)
class CapturedFrame:
    sequence: int
    captured_ns: int
    image: object


@dataclass(frozen=True)
class Prediction:
    sequence: int
    captured_ns: int
    completed_ns: int
    data: dict


def timing_summary(values):
    """Linear-interpolated median/P90 in milliseconds; no samples means unverified."""
    if not values:
        return {'median': None, 'p90': None}
    ordered = sorted(values)
    def percentile(q):
        index = (len(ordered)-1)*q
        lower = math.floor(index)
        upper = math.ceil(index)
        return ordered[lower] + (ordered[upper]-ordered[lower])*(index-lower)
    return {'median': percentile(.5), 'p90': percentile(.9)}


def open_camera(options):
    import cv2
    if options.backend == 'dshow' and sys.platform != 'win32':
        raise ValueError('DirectShow 仅适用于 Windows；当前系统请使用 camera.backend: auto。')
    backend = cv2.CAP_DSHOW if options.backend == 'dshow' else cv2.CAP_ANY
    camera = cv2.VideoCapture(options.index, backend)
    try:
        if not camera.isOpened():
            raise RuntimeError('无法打开摄像头；请检查权限、设备编号或是否被浏览器/其他程序占用。')
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, options.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, options.height)
        return camera
    except BaseException:
        camera.release()
        raise


class CameraPipeline:
    """Two owned workers and one replaceable pending frame; never a frame queue.

    start/resume creates new camera/model resources. pause/close releases them.
    A result contains no image. Timing samples are retained only when explicitly
    enabled for a finite benchmark; ordinary use retains only the latest result.
    """
    def __init__(self, settings=None, *, camera_factory=None, engine_factory=None,
                 collect_timings=False, clock_ns=time.perf_counter_ns):
        self.settings = settings or load_settings()
        self._camera_factory = camera_factory or open_camera
        self._engine_factory = engine_factory or self._new_engine
        self._collect_timings, self._clock_ns = collect_timings, clock_ns
        self._condition = threading.Condition()
        self._control = threading.RLock()
        self._stop, self._run = threading.Event(), threading.Event()
        self._threads = ()
        self._closed = False
        self._camera = None
        self._reset_session()

    def _new_engine(self):
        from .engine import Engine
        return Engine(self.settings)

    def _reset_session(self):
        self._pending = self._latest = self._error = None
        self._in_flight = False
        self._captured = self._predicted = self._dropped = self._discarded_on_stop = 0
        self._inference_ms, self._latency_ms = [], []
        self._actual_resolution = self._actual_backend = None
        self._device = self._device_name = None
        self._started_ns = self._stopped_ns = None
        self._capture_ready, self._engine_ready = threading.Event(), threading.Event()

    @property
    def error(self):
        with self._condition:
            return self._error

    @property
    def running(self):
        return bool(self._threads) and not self._stop.is_set()

    def _fail(self, exc):
        with self._condition:
            if self._error is None:
                self._error = exc
            self._stop.set()
            self._run.set()
            self._condition.notify_all()

    def raise_if_failed(self):
        error = self.error
        if error is not None:
            raise RuntimeError(f'摄像头管线失败：{error}') from error

    def start(self):
        with self._control:
            if self._closed:
                raise RuntimeError('管线已关闭，不能重新启动。')
            if self.running:
                return self
            self._stop_workers()
            with self._condition:
                self._reset_session()
            self._stop.clear()
            self._run.clear()
            self._threads = (
                threading.Thread(target=self._capture_loop, name='gaze-capture', daemon=True),
                threading.Thread(target=self._inference_loop, name='gaze-inference', daemon=True),
            )
            for worker in self._threads:
                worker.start()
            deadline = time.monotonic() + self.settings.camera.startup_timeout_s
            for ready in (self._capture_ready, self._engine_ready):
                if not ready.wait(max(0, deadline-time.monotonic())):
                    self._fail(TimeoutError('摄像头或模型启动超时。'))
                    break
            if self.error is not None:
                self._stop_workers()
                self.raise_if_failed()
            with self._condition:
                self._started_ns = self._clock_ns()
            self._run.set()
            return self

    def resume(self):
        return self.start()

    def _release_camera(self):
        with self._condition:
            camera, self._camera = self._camera, None
        if camera is not None:
            try:
                # Also unblocks read() for backends that support cancellation.
                camera.release()
            except Exception as exc:
                self._fail(exc)

    def _stop_workers(self):
        if not self._threads:
            return
        with self._condition:
            if self._stopped_ns is None:
                self._stopped_ns = self._clock_ns()
            self._stop.set()
            self._run.set()
            self._condition.notify_all()
        self._release_camera()
        deadline = time.monotonic() + self.settings.camera.stop_timeout_s
        for worker in self._threads:
            worker.join(max(0, deadline-time.monotonic()))
        alive = [worker.name for worker in self._threads if worker.is_alive()]
        if alive:
            raise RuntimeError('线程未能在超时内退出：' + ', '.join(alive) + '；请检查摄像头后端或推理驱动。')
        with self._condition:
            if self._pending is not None:
                self._discarded_on_stop += 1
            self._pending = None
            self._threads = ()

    def pause(self):
        with self._control:
            self._stop_workers()

    def close(self):
        with self._control:
            self._stop_workers()
            self._closed = True

    def _capture_loop(self):
        try:
            camera = self._camera_factory(self.settings.camera)
            with self._condition:
                self._camera = camera
                self._actual_backend = camera.getBackendName()
            self._capture_ready.set()
            self._run.wait()
            while not self._stop.is_set():
                ok, image = camera.read()
                captured_ns = self._clock_ns()  # Immediately after OpenCV read() returns.
                if self._stop.is_set():
                    break
                if not ok or image is None:
                    raise RuntimeError('摄像头读帧失败。')
                height, width = image.shape[:2]
                image = image.copy()  # Own the pixels even if a source reuses its buffer.
                with self._condition:
                    if self._stop.is_set():
                        break
                    self._captured += 1
                    self._actual_resolution = [width, height]
                    if self._pending is not None:
                        self._dropped += 1
                    self._pending = CapturedFrame(self._captured, captured_ns, image)
                    self._condition.notify_all()
        except Exception as exc:
            self._fail(exc)
        finally:
            self._capture_ready.set()
            self._release_camera()

    def _inference_loop(self):
        engine = None
        try:
            engine = self._engine_factory()
            with self._condition:
                self._device, self._device_name = engine.device, engine.device_name
            self._engine_ready.set()
            self._run.wait()
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stop.is_set() or self._pending is not None)
                    if self._stop.is_set():
                        break
                    frame, self._pending = self._pending, None
                    self._in_flight = True
                started_ns = self._clock_ns()
                data = engine.predict(frame.image, self.settings.geometry, self.settings.camera.grayscale)
                completed_ns = self._clock_ns()
                with self._condition:
                    self._predicted += 1
                    self._in_flight = False
                    if self._collect_timings:
                        self._inference_ms.append((completed_ns-started_ns)/1_000_000)
                        self._latency_ms.append((completed_ns-frame.captured_ns)/1_000_000)
                    self._latest = Prediction(frame.sequence, frame.captured_ns, completed_ns, data)
                    self._condition.notify_all()
        except Exception as exc:
            self._fail(exc)
        finally:
            self._engine_ready.set()
            if engine is not None:
                try:
                    engine.close()
                except Exception as exc:
                    self._fail(exc)
            with self._condition:
                self._in_flight = False
                self._condition.notify_all()
            self._release_camera()

    def wait_for_result(self, after_sequence=0, timeout=None):
        with self._condition:
            self._condition.wait_for(lambda: self._error is not None or self._stop.is_set() or
                                     (self._latest is not None and self._latest.sequence > after_sequence), timeout)
            self.raise_if_failed()
            if self._latest is not None and self._latest.sequence > after_sequence:
                return self._latest
            return None

    def wait(self, seconds):
        self._stop.wait(seconds)
        self.raise_if_failed()

    def report(self):
        with self._condition:
            end_ns = self._stopped_ns if self._stopped_ns is not None else self._clock_ns()
            seconds = (end_ns-self._started_ns)/1_000_000_000 if self._started_ns is not None else 0
            return {
                'measurement_seconds': seconds,
                'device': self._device, 'device_name': self._device_name,
                'requested_resolution': [self.settings.camera.width, self.settings.camera.height],
                'actual_resolution': self._actual_resolution,
                'grayscale': self.settings.camera.grayscale,
                'requested_backend': self.settings.camera.backend, 'actual_backend': self._actual_backend,
                'captured_frames': self._captured, 'predicted_frames': self._predicted,
                'capture_fps': self._captured/seconds if seconds > 0 else None,
                'inference_ms': timing_summary(self._inference_ms),
                'read_to_result_latency_ms': timing_summary(self._latency_ms),
                'dropped_frames': self._dropped,
                'pending_frames': int(self._pending is not None), 'in_flight_frames': int(self._in_flight),
                'discarded_on_stop': self._discarded_on_stop,
                'latency_definition': '从 OpenCV read() 返回到推理结果就绪；不含摄像头曝光与驱动缓冲，不是端到端延迟。',
                'drop_definition': '仅统计新帧覆盖尚未推理的旧帧；统计截止时待处理和推理中的帧单列。',
            }


def run_benchmark(pipeline, seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('测速秒数必须为有限正数。')
    try:
        pipeline.start()  # Model and camera initialization are outside the sample window.
        pipeline.wait(seconds)
        report = pipeline.report()  # Snapshot before shutdown/draining the current prediction.
    finally:
        pipeline.close()
    pipeline.raise_if_failed()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Python 直接摄像头管线测速；请先关闭浏览器摄像头。')
    parser.add_argument('--benchmark', type=float, required=True, metavar='秒数')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--grayscale', action=argparse.BooleanOptionalAction, default=None,
                        help='覆盖配置中的灰度选项；--no-grayscale 使用彩色')
    args = parser.parse_args(argv)
    if not math.isfinite(args.benchmark) or args.benchmark <= 0:
        parser.error('--benchmark 必须是有限正数')
    try:
        settings = load_settings(args.config)
        if args.grayscale is not None:
            settings = replace(settings, camera=replace(settings.camera, grayscale=args.grayscale))
        report = run_benchmark(CameraPipeline(settings, collect_timings=True), args.benchmark)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (Exception, KeyboardInterrupt) as exc:
        print(f'测速未完成：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
