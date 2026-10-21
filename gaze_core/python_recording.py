"""Runtime Python recording, independent of Qt and camera ownership.

Every admitted command shares one ordered, bounded queue. Preparing metadata,
opening a session and final flushing happen on the control worker, never on the
inference or Qt thread. SessionRecorder retains its numeric-only writer boundary.
"""
from copy import deepcopy
import queue
import threading
import time

from .capture import numeric_prediction
from .recorder import SessionRecorder, validate_metadata, validate_payload, text_value, _json_numbers


class PythonRecording:
    def __init__(self, settings, metadata_factory=None, *, recorder_factory=None):
        self.settings = getattr(settings, 'recording', settings)
        self._metadata_factory = metadata_factory
        self._factory = recorder_factory or (lambda metadata: SessionRecorder(metadata, settings=self.settings))
        self._metadata = None
        self._lock = threading.RLock()
        self._queue = queue.Queue(self.settings.queue_size)
        self._abort = threading.Event()
        self._stop = threading.Event()
        self._manual = self._desired = self._closed = False
        self._task = None
        self._recorder = None
        self._last = {}
        self._error = None
        self._operation_error = None
        self._worker = threading.Thread(target=self._run, name='gaze-python-recording', daemon=True)
        self._worker.start()

    def set_metadata(self, metadata):
        validate_metadata(metadata)
        if metadata['source'] != 'python':
            raise ValueError('Python 录制的 source 必须为 python。')
        with self._lock:
            if self._desired:
                raise RuntimeError('录制进行中不能替换会话元信息。')
            self._metadata = _json_numbers(deepcopy(metadata))

    def _failure(self, message):
        with self._lock:
            if self._error is None:
                self._error = str(message)
            self._manual = self._desired = False
            self._task = None
            self._abort.set()

    def _put(self, command):
        try:
            self._queue.put_nowait(command)
            return True
        except queue.Full:
            self._failure('录制转交队列已满；录制已停止，推理继续运行。')
            return False

    def _enable(self):
        if self._closed:
            self._error = '录制控制器已关闭。'
            return False
        if self._abort.is_set():
            return False
        if self._desired:
            return True
        if self._metadata is None and self._metadata_factory is None:
            self._error = '尚未取得摄像头实际条件，暂不能录制。'
            return False
        self._error = None
        self._desired = True
        return self._put(('open', deepcopy(self._metadata)))

    def manual(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('录制开关必须为布尔值。')
        with self._lock:
            if enabled:
                if not self._enable():
                    return False
                self._manual = True
                return True
            self._manual = False
            if self._task is None and self._desired:
                self._desired = False
                return self._put(('close', 'user'))
            return True

    def begin_task(self, task_id):
        text_value(task_id, 'task_id')
        with self._lock:
            if self._task is not None:
                self._operation_error = '同一时间只能运行一个录制任务，不允许嵌套。'
                return False
            if not self._enable():
                return False
            self._operation_error = None
            self._task = task_id
            return True

    def end_task(self, task_id, reason='task_completed'):
        text_value(task_id, 'task_id'); text_value(reason, 'reason')
        with self._lock:
            if self._task != task_id:
                return False
            self._task = None
            if not self._manual and self._desired:
                self._desired = False
                return self._put(('close', reason))
            return True

    def record_prediction(self, prediction):
        with self._lock:
            if not self._desired or self._closed:
                return True
            try:
                numeric = numeric_prediction(prediction)
            except Exception as exc:
                self._failure(exc)
                return False
            return self._put(('frame', numeric))

    def emit(self, kind, **fields):
        # Python events use the same monotonic epoch as capture_ns. No fake
        # performance.now() value is ever created for a Python session.
        fields.setdefault('event_ns', time.perf_counter_ns())
        try:
            validate_payload(kind, fields, 'python')
            normalized = _json_numbers(fields)
        except Exception as exc:
            self._failure(exc)
            return False
        with self._lock:
            if not self._desired or self._closed:
                return False
            return self._put(('event', kind, normalized))

    def status(self):
        with self._lock:
            recorder = self._recorder
            current = dict(recorder.status()) if recorder is not None else dict(self._last)
            writer_error = current.get('error')
            if writer_error and self._error is None:
                self._failure(writer_error)
            return dict(current, active=self._desired and self._error is None,
                        manual=self._manual, task_id=self._task,
                        preparing=self._desired and recorder is None,
                        closing=not self._desired and (recorder is not None or not self._queue.empty()),
                        buffered_predictions=self._queue.qsize(), error=self._error,
                        operation_error=self._operation_error)

    def _finish(self, reason, *, failed=False):
        with self._lock:
            recorder = self._recorder
        if recorder is None:
            return
        if failed and not recorder.error:
            recorder.fail(self._error or '录制转交失败')
        recorder.close(reason=reason)
        with self._lock:
            self._last = dict(recorder.status())
            self._recorder = None

    def _execute(self, command):
        if command[0] == 'open':
            if self._recorder is not None:
                raise RuntimeError('拒绝嵌套录制会话。')
            metadata = command[1] if command[1] is not None else self._metadata_factory()
            validate_metadata(metadata)
            if metadata['source'] != 'python':
                raise ValueError('Python 录制的 source 必须为 python。')
            recorder = self._factory(_json_numbers(deepcopy(metadata)))
            if recorder is None:
                raise RuntimeError('录制工厂未返回 recorder。')
            with self._lock:
                self._recorder = recorder
            return
        if command[0] == 'close':
            self._finish(command[1])
            return
        recorder = self._recorder
        if recorder is None:
            raise RuntimeError('录制会话尚未就绪，无法写入数值记录。')
        if command[0] == 'frame':
            prediction = command[1]
            accepted = recorder.record_frame(prediction.data, capture_ns=prediction.captured_ns,
                                             inference_completed_ns=prediction.completed_ns,
                                             frame_id=prediction.sequence)
        else:
            accepted = recorder.emit(command[1], **command[2])
        if not accepted:
            raise RuntimeError(recorder.error or '录制器拒绝数值记录。')

    def _run(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                command = self._queue.get(timeout=.05)
            except queue.Empty:
                command = None
            if command is not None:
                try:
                    self._execute(command)
                except Exception as exc:
                    self._failure(exc)
                finally:
                    self._queue.task_done()
            if self._abort.is_set() and self._queue.empty():
                try:
                    self._finish('recording_failed', failed=True)
                except Exception as exc:
                    self._failure(exc)
                else:
                    self._abort.clear()
        try:
            self._finish('recording_failed' if self._error else self._close_reason,
                         failed=self._error is not None)
        except Exception as exc:
            self._failure(exc)

    def close(self, reason='overlay_closed'):
        """Drain after CameraPipeline.close(); call from the shutdown worker."""
        text_value(reason, 'reason')
        with self._lock:
            self._closed = True
            self._desired = self._manual = False
            self._task = None
            self._close_reason = reason
            self._stop.set()
        self._worker.join(self.settings.close_timeout_s)
        if self._worker.is_alive():
            self._failure('录制控制线程未能及时退出，数值文件尚未完成收尾。')
            raise RuntimeError(self._error)
        if self._recorder is not None:
            raise RuntimeError(self._error or '录制器尚未关闭。')
        return self.status()
