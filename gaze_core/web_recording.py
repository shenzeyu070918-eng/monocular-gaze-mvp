"""One web session, with numeric inference results waiting for browser receipts."""
import threading

from .recorder import (RESULT_FIELDS, SessionRecorder, number, session_metadata,
                       text_value, validate_payload)


class RecordingConflict(ValueError):
    pass


class WebRecording:
    def __init__(self, settings, recorder_factory=SessionRecorder):
        self.settings = settings
        self._factory = recorder_factory
        self._lifecycle = threading.Lock()
        self._condition = threading.Condition(threading.RLock())
        self._recorder = None
        self._closing = False
        self._error = None
        self._failure_cleanup = None
        self._inflight = set()
        self._pending = {}

    def status(self):
        with self._condition:
            if self._recorder is None:
                return {'session_id': None, 'active': False, 'error': None, 'file': None}
            result = self._recorder.status()
            result['error'] = result['error'] or self._error
            result['active'] = result['active'] and not self._closing and not result['error']
            return result

    def start(self, *, engine, geometry, screen, measured, grayscale, camera_size, page_clock_id):
        metadata = session_metadata(source='web', engine=engine, geometry=geometry,
                                    screen=screen, measured=measured, grayscale=grayscale,
                                    camera_size=camera_size, page_clock_id=page_clock_id)
        with self._lifecycle:
            return self._start(metadata)

    def _start(self, metadata):
        with self._condition:
            if self._closing or self._inflight or (self._recorder and self._recorder.status()['active']):
                raise RecordingConflict('已有录制会话，请先结束当前会话。')
            if self._recorder:
                self._flush_pending()
                self._recorder.close('replaced_after_failure')
            self._recorder = self._factory(metadata, settings=self.settings.recording)
            self._error = None
            self._pending.clear()
            return self.status()

    def _signal_failure(self, message):
        # Stop admission now, then drain already accepted results off the inference
        # thread. The end line must wait for pending receipts and in-flight frames.
        if self._error is not None:
            return
        self._error = message
        session_id = self._recorder.session_id
        def cleanup():
            try:
                self.stop(session_id, reason='recording_failed')
            except (RuntimeError, ValueError) as exc:
                with self._condition:
                    if self._recorder.session_id == session_id:
                        self._error = f'{message}；收尾失败：{exc}'
        self._failure_cleanup = threading.Thread(target=cleanup, name='gaze-web-recording-close', daemon=True)
        self._failure_cleanup.start()

    def _current(self, session_id):
        if self._recorder is None or session_id != self._recorder.session_id:
            raise RecordingConflict('录制会话不存在或已切换。')
        return self._recorder

    def begin_frame(self, session_id, page_clock_id, frame_id, **timing):
        if session_id is None:
            return None
        if type(frame_id) is not int or frame_id < 0:
            raise ValueError('frame_id 必须是非负整数')
        # Validate before retaining anything. Image requests never enter this object.
        validate_payload('frame', dict(valid=False, status='prediction_error',
                         frame_id=frame_id, inference_completed_ns=0, **timing), 'web')
        number(timing.get('browser_request_ms'), 'browser_request_ms')
        if timing['browser_request_ms'] < timing['browser_capture_ms']:
            raise ValueError('浏览器请求时间不得早于取帧时间')
        with self._condition:
            recorder = self._current(session_id)
            if page_clock_id != recorder.page_clock_id:
                raise RecordingConflict('页面时钟已改变，请开启新的录制会话。')
            if self._closing or self._error or not recorder.status()['active']:
                return None  # Recording failure must not interrupt inference.
            if frame_id in self._inflight or frame_id in self._pending:
                raise RecordingConflict('frame_id 重复')
            if len(self._pending) + len(self._inflight) >= self.settings.recording.queue_size:
                self._signal_failure('浏览器帧回执队列已满；录制已停止，推理继续运行。')
                return None
            self._inflight.add(frame_id)
            return (recorder, frame_id, dict(timing))

    def complete_frame(self, ticket, result, completed_ns):
        if ticket is None:
            return
        recorder, frame_id, timing = ticket
        with self._condition:
            try:
                # Copy only scalars/vectors. In particular, never retain image or landmarks.
                numeric = {key: result.get(key) for key in RESULT_FIELDS}
                values = dict(timing, frame_id=frame_id, inference_completed_ns=completed_ns)
                validate_payload('frame', dict(numeric, **values), 'web')
                self._pending[frame_id] = (numeric, values)
            except (ValueError, TypeError) as exc:
                self._signal_failure(f'录制结果格式无效：{exc}')
            finally:
                self._inflight.discard(frame_id)
                self._condition.notify_all()

    def receipt(self, session_id, frame_id, browser_response_ms):
        number(browser_response_ms, 'browser_response_ms')
        with self._condition:
            recorder = self._current(session_id)
            item = self._pending.get(frame_id)
            if item is None:
                # A page-close request may already have drained the frame without an ACK.
                if not recorder.status()['active']:
                    return self.status()
                raise RecordingConflict('未找到待确认的帧')
            data, timing = item
            if browser_response_ms < timing['browser_request_ms']:
                raise ValueError('响应时间不得早于请求时间')
            del self._pending[frame_id]
            if not recorder.record_frame(data, **timing, browser_response_ms=browser_response_ms):
                self._signal_failure(recorder.error or '录制器拒绝帧记录')
            return self.status()

    def event(self, session_id, kind, fields):
        with self._condition:
            recorder = self._current(session_id)
            if self._closing:
                raise RecordingConflict('会话正在结束')
            if kind not in {'target', 'calibration', 'distance_check'}:
                raise ValueError('网页只支持 target / calibration / distance_check 事件；click 留待 M4')
            if not recorder.emit(kind, **fields):
                self._signal_failure(recorder.error or '录制器拒绝事件记录')
            return self.status()

    def _flush_pending(self):
        for data, timing in self._pending.values():
            self._recorder.record_frame(data, **timing, browser_response_ms=None)
        self._pending.clear()

    def stop(self, session_id=None, reason='user', browser_time_ms=None):
        text_value(reason, 'reason')
        number(browser_time_ms, 'browser_time_ms', nullable=True)
        with self._lifecycle:
            return self._stop(session_id, reason, browser_time_ms)

    def _stop(self, session_id, reason, browser_time_ms):
        with self._condition:
            if self._recorder is None:
                return self.status()
            if session_id is not None:
                self._current(session_id)
            self._closing = True
            try:
                if not self._condition.wait_for(lambda: not self._inflight,
                                                self.settings.recording.close_timeout_s):
                    self._recorder.fail('等待推理收尾超时；请检查本地服务。')
                    raise RuntimeError('等待推理收尾超时，录制未能正常结束。')
                self._flush_pending()
                if self._error and not self._recorder.error:
                    self._recorder.fail(self._error)
                return self._recorder.close(reason, browser_time_ms)
            finally:
                self._closing = False
