"""Direct-camera pipeline, independent of the synchronous browser camera path."""
import argparse
from dataclasses import dataclass, replace
import json
import math
import queue
import sys
import threading
import time

from .config import DEFAULT_CONFIG, load_settings, print_configuration
from .camera_io import read_camera_frames


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
    # The caller owns the returned handle, including configuration failures.
    return cv2.VideoCapture(options.index, backend)


class CameraPipeline:
    """Two owned workers and one replaceable pending frame; never a frame queue.

    pause stops both workers and releases the camera on its owning thread.
    resume reuses the loaded engine; only close releases it after joining workers.
    A result contains no image. Timing samples are retained only when explicitly
    enabled for a finite benchmark; ordinary use retains only the latest result.
    An optional recorder receives only result dictionaries and original timing;
    recording failures are reported separately and never stop inference.
    """
    def __init__(self, settings=None, *, camera_factory=None, engine_factory=None,
                 collect_timings=False, clock_ns=time.perf_counter_ns,
                 recorder=None, recording_factory=None, display_pipeline=None):
        if recorder is not None and recording_factory is not None:
            raise ValueError('recorder 和 recording_factory 只能指定一个。')
        self.settings = settings or load_settings()
        self._camera_factory = camera_factory or open_camera
        self._engine_factory = engine_factory or self._new_engine
        self._collect_timings, self._clock_ns = collect_timings, clock_ns
        self._condition = threading.Condition()
        self._control = threading.RLock()
        self._stop, self._run = threading.Event(), threading.Event()
        self._threads = ()
        self._closed = False
        self._closing = False
        self._engine = None
        self._recorder = recorder
        self._recording_factory = recording_factory
        self._recording_attempted = False
        self._recording_error = None
        self._recorder_closed = False
        self._recording_thread = None
        self._recording_stop = threading.Event()
        self._recording_finalize = threading.Event()
        self._recording_queue = queue.Queue(self.settings.recording.queue_size)
        self._recording_close_failure = None
        self._display_pipeline = display_pipeline
        self._sequence = 0  # Result cursors remain valid across pause/resume.
        self._reset_session()

    def _new_engine(self):
        from .engine import Engine
        return Engine(self.settings)

    def _reset_session(self):
        if self._display_pipeline is not None:self._display_pipeline.reset()
        self._pending = self._latest = self._error = None
        self._in_flight = False
        self._captured = self._predicted = self._dropped = self._discarded_on_stop = 0
        self._valid_predictions, self._status_counts = 0, {}
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
            if self._closed or self._closing:
                raise RuntimeError('管线已关闭或正在关闭，不能重新启动。')
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

    def _stop_workers(self):
        if not self._threads:
            return
        with self._condition:
            if self._stopped_ns is None:
                self._stopped_ns = self._clock_ns()
            self._stop.set()
            self._run.set()
            self._condition.notify_all()
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
            self._closing = True
            self._stop_workers()
            # Join first: an in-flight prediction still belongs in this session.
            # pause intentionally keeps the recorder and the loaded model alive.
            self._stop_recording()
            # No worker can still be using the model after a successful join.
            # Keep the handle on failure so that cleanup can be retried.
            if self._engine is not None:
                self._engine.close()
                self._engine = None
            self._closed = True

    def _set_recording_error(self, error):
        with self._condition:
            if self._recording_error is None:
                self._recording_error = str(error)
            self._condition.notify_all()

    def _prepare_recorder(self, engine, image):
        if (self._recording_factory is None and self._recorder is None) or self._recording_attempted:
            return
        self._recording_attempted = True
        height, width = image.shape[:2]
        # Only engine provenance and dimensions cross this boundary; not pixels.
        self._recording_thread = threading.Thread(
            target=self._recording_loop, args=(engine, (width, height)),
            name='gaze-recording', daemon=True)
        try:
            self._recording_thread.start()
        except Exception as exc:
            self._recording_thread = None
            self._set_recording_error(exc)

    def _record_prediction(self, frame, completed_ns, data):
        if not self._recording_attempted or self._recording_error is not None:
            return
        try:
            from .recorder import RESULT_FIELDS, validate_payload
            if set(data)-RESULT_FIELDS-{'landmarks'}:
                raise ValueError('推理结果含非数值录制字段，拒绝缓存。')
            numeric = {key: (list(value) if isinstance(value, (list, tuple)) else value)
                       for key, value in data.items() if key in RESULT_FIELDS}
            timing = {'capture_ns': frame.captured_ns,
                      'inference_completed_ns': completed_ns, 'frame_id': frame.sequence}
            validate_payload('frame', {**numeric, **timing}, 'python')
            self._recording_queue.put_nowait((numeric, timing))
        except queue.Full:
            self._set_recording_error('录制准备/转交队列已满；录制已停止，推理继续运行。')
            self._recording_stop.set()
        except Exception as exc:
            self._set_recording_error(exc)
            self._recording_stop.set()

    def _recording_loop(self, engine, actual_resolution):
        """Initialize/drain on a dedicated thread, including slow first flush."""
        try:
            if self._recorder is None:
                self._recorder = self._recording_factory(engine, actual_resolution)
                if self._recorder is None:
                    raise RuntimeError('录制工厂未返回 recorder。')
            while not self._recording_stop.is_set() or not self._recording_queue.empty():
                try:
                    data, timing = self._recording_queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    if not self._recorder.record_frame(data, **timing):
                        self._set_recording_error(self._recorder.error or '录制器拒绝帧记录。')
                        self._recording_stop.set()
                except Exception as exc:
                    self._set_recording_error(exc)
                    self._recording_stop.set()
                finally:
                    self._recording_queue.task_done()
        except Exception as exc:
            self._set_recording_error(exc)
            self._recording_stop.set()
            while True:
                try:
                    self._recording_queue.get_nowait()
                    self._recording_queue.task_done()
                except queue.Empty:
                    break
        finally:
            # Even a failed bridge must not close its session before the
            # capture/inference workers have delivered their final result.
            self._recording_finalize.wait()
            try:
                self._close_recorder()
            except Exception as exc:
                self._recording_close_failure = exc
                self._set_recording_error(exc)

    def _close_recorder(self):
        if self._recorder is None or self._recorder_closed:
            return
        # Drain accepted results before propagating a bridge failure; fail()
        # stops the recorder's producer queue and marks the end record failed.
        if self._recording_error and not self._recorder.error:
            self._recorder.fail(self._recording_error)
        self._recorder.close(reason='recording_failed' if self._recording_error else 'pipeline_closed')
        self._recorder_closed = True

    def _stop_recording(self):
        self._recording_stop.set()
        self._recording_finalize.set()  # Called only after both workers joined.
        if self._recording_thread is not None:
            self._recording_thread.join(self.settings.recording.close_timeout_s)
            if self._recording_thread.is_alive():
                error = RuntimeError('录制准备/转交线程未能及时退出；录制器与引擎尚未释放，可重试关闭。')
                self._set_recording_error(error)
                raise error
            self._recording_thread = None
            if self._recording_close_failure is not None:
                error, self._recording_close_failure = self._recording_close_failure, None
                raise RuntimeError(f'录制器尚未完成关闭，可重试：{error}') from error
        else:
            # Injected recorders may exist without a camera frame ever arriving.
            self._close_recorder()

    def recording_status(self):
        if self._recorder is not None:
            try:
                status = dict(self._recorder.status())
                if status.get('error'):
                    self._set_recording_error(status['error'])
            except Exception as exc:
                self._set_recording_error(exc)
                status = {}
        else:
            status = {'enabled': self._recording_factory is not None}
        with self._condition:
            return {**status, 'pipeline_error': self._recording_error,
                    'buffered_predictions': self._recording_queue.qsize(),
                    'preparing': self._recording_attempted and self._recorder is None and
                                 bool(self._recording_thread and self._recording_thread.is_alive())}

    def _capture_loop(self):
        camera = None
        try:
            import cv2
            camera = self._camera_factory(self.settings.camera)
            if not camera.isOpened():
                raise RuntimeError('无法打开摄像头；请检查权限、设备编号或是否被浏览器/其他程序占用。')
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.camera.width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.camera.height)
            with self._condition:
                self._actual_backend = camera.getBackendName()
            self._capture_ready.set()
            self._run.wait()
            for image, captured_ns in read_camera_frames(camera, self.settings.camera,
                                                        stop=self._stop, clock_ns=self._clock_ns):
                height, width = image.shape[:2]
                image = image.copy()  # Own the pixels even if a source reuses its buffer.
                with self._condition:
                    if self._stop.is_set():
                        break
                    self._captured += 1
                    self._sequence += 1
                    self._actual_resolution = [width, height]
                    if self._pending is not None:
                        self._dropped += 1
                    self._pending = CapturedFrame(self._sequence, captured_ns, image)
                    self._condition.notify_all()
        except Exception as exc:
            self._fail(exc)
        finally:
            self._capture_ready.set()
            # Only the capture worker owns this handle. Never release from a
            # different thread to interrupt a read; wait for read to return.
            if camera is not None:
                try:
                    camera.release()
                except Exception as exc:
                    self._fail(exc)

    def _inference_loop(self):
        try:
            if self._engine is None:
                self._engine = self._engine_factory()
            engine = self._engine
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
                self._prepare_recorder(engine, frame.image)
                started_ns = self._clock_ns()
                data = engine.predict(frame.image, self.settings.geometry, self.settings.camera.grayscale)
                completed_ns = self._clock_ns()
                self._record_prediction(frame, completed_ns, data)
                if self._display_pipeline is not None:
                    display=self._display_pipeline.process(data,source='python',capture_ns=frame.captured_ns,
                                                           geometry=self.settings.geometry)
                    data=dict(data,display=display)
                with self._condition:
                    self._predicted += 1
                    self._valid_predictions += int(bool(data.get('valid', False)))
                    status = data.get('status', 'unknown')
                    self._status_counts[status] = self._status_counts.get(status, 0) + 1
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
            with self._condition:
                self._in_flight = False
                self._condition.notify_all()

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
                'camera': self._latest.data.get('camera') if self._latest else None,
                'requested_resolution': [self.settings.camera.width, self.settings.camera.height],
                'actual_resolution': self._actual_resolution,
                'grayscale': self.settings.camera.grayscale,
                'requested_backend': self.settings.camera.backend, 'actual_backend': self._actual_backend,
                'captured_frames': self._captured, 'predicted_frames': self._predicted,
                'valid_predictions': self._valid_predictions, 'prediction_status_counts': dict(self._status_counts),
                'capture_fps': self._captured/seconds if seconds > 0 else None,
                'inference_ms': timing_summary(self._inference_ms),
                'read_to_result_latency_ms': timing_summary(self._latency_ms),
                'dropped_frames': self._dropped,
                'pending_frames': int(self._pending is not None), 'in_flight_frames': int(self._in_flight),
                'discarded_on_stop': self._discarded_on_stop,
                'recording': self.recording_status(),
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
    report['recording'] = pipeline.recording_status()  # Include final flush/close failures.
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Python 直接摄像头管线测速；请先关闭浏览器摄像头。')
    parser.add_argument('--benchmark', type=float, required=True, metavar='秒数')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--grayscale', action=argparse.BooleanOptionalAction, default=None,
                        help='覆盖配置中的灰度选项；--no-grayscale 使用彩色')
    parser.add_argument('--record', action='store_true',
                        help='同时录制原始数值结果到 data/sessions；不记录图像')
    args = parser.parse_args(argv)
    if not math.isfinite(args.benchmark) or args.benchmark <= 0:
        parser.error('--benchmark 必须是有限正数')
    try:
        settings = load_settings(args.config)
        if args.grayscale is not None:
            settings = replace(settings, camera=replace(settings.camera, grayscale=args.grayscale),
                               key_sources=dict(settings.key_sources or {}, **{'camera.grayscale':'命令行'}))
        print_configuration(settings)
        recording_factory = None
        if args.record:
            from .recorder import SessionRecorder, session_metadata
            def recording_factory(engine, actual_resolution):
                metadata = session_metadata(
                    source='python', engine=engine, geometry=settings.geometry,
                    camera_size=actual_resolution, grayscale=settings.camera.grayscale,
                    screen=None, measured=settings.geometry.measured, page_clock_id=None)
                return SessionRecorder(metadata, settings=settings.recording)
        report = run_benchmark(CameraPipeline(settings, collect_timings=True,
                                             recording_factory=recording_factory), args.benchmark)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (Exception, KeyboardInterrupt) as exc:
        print(f'测速未完成：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
