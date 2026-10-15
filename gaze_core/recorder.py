"""Numeric-only JSONL sessions with a single non-blocking producer queue."""
from datetime import datetime, timezone
import json
import math
from numbers import Integral, Real
from pathlib import Path
import platform
import queue
import subprocess
import threading
import time
import uuid

from .config import ROOT, load_settings

SCHEMA_VERSION = 1
SCREEN_FIELDS = {'width_cm', 'height_cm', 'measured', 'width_px', 'height_px', 'device_pixel_ratio',
                 'window_width_px', 'window_height_px', 'fullscreen', 'stage_left_px', 'stage_top_px',
                 'stage_width_px', 'stage_height_px', 'screen_x', 'screen_y', 'outer_width_px', 'outer_height_px'}
VIDEO_FIELDS = {'source', 'callback_ms', 'presentationTime', 'expectedDisplayTime', 'mediaTime',
                'presentedFrames', 'captureTime', 'receiveTime', 'processingDuration', 'width', 'height'}
RESULT_FIELDS = {'valid', 'status', 'raw', 'origin_m', 'gaze_vector', 'head_degrees', 'distance_cm',
                 'eye_openness', 'inference_ms'}
FRAME_FIELDS = RESULT_FIELDS | {'capture_ns', 'request_received_ns', 'inference_completed_ns', 'frame_id',
                               'browser_capture_ms', 'browser_request_ms', 'browser_response_ms', 'video_frame'}
EVENT_FIELDS = {
    'target': {'action', 'task_type', 'task_id', 'index', 'target', 'stage_target', 'browser_time_ms', 'reason'},
    'calibration': {'action', 'matrix', 'calibration_id', 'group', 'browser_time_ms'},
    'click': {'x', 'y', 'button', 'browser_time_ms'},  # Reserved schema; no input listener in M0.
}
STATUSES = {'tracking', 'no_face', 'blink', 'geometry_invalid', 'prediction_error'}


def number(value, name, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} 必须是有限数值')
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} 必须是有限数值') from exc
    if not math.isfinite(converted):
        raise ValueError(f'{name} 必须是有限数值')
    return converted


def clock_value(value, name, nullable=False):
    if value is None and nullable:
        return
    number(value, name)
    if value < 0 or (name.endswith('_ns') and not isinstance(value, Integral)):
        raise ValueError(f'{name} 必须是非负时间；纳秒使用整数')


def _json_numbers(value, field=''):
    """Normalize validated data; exact integer clocks/identifiers stay integers."""
    if isinstance(value, dict):
        return {key: _json_numbers(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_numbers(item) for item in value]
    if value is None or isinstance(value, (str, bool)):
        return value
    converted = number(value, field or '数值')
    if isinstance(value, Integral) and (field.endswith('_ns') or field in {
            'schema_version', 'seq', 'frame_id', 'index', 'written_lines', 'failed_records'}):
        return int(value)
    return converted


def _json_line(record):
    return json.dumps(_json_numbers(record), ensure_ascii=False, allow_nan=False)+'\n'


def text_value(value, name, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f'{name} 必须是短文本')
    if 'base64' in value.lower() or 'data:image' in value.lower():
        raise ValueError(f'{name} 不接受图像或 base64 数据')


def vector(value, size, name, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f'{name} 数组长度必须为 {size}')
    for item in value:
        number(item, name)


def validate_screen(screen):
    if not isinstance(screen, dict) or set(screen)-SCREEN_FIELDS:
        raise ValueError('screen 包含未允许的字段')
    for key, value in screen.items():
        if key in {'measured', 'fullscreen'}:
            if value is not None and type(value) is not bool:
                raise ValueError(f'{key} 必须是布尔值')
        else:
            number(value, key, nullable=True)


def session_metadata(*, source, engine, geometry, camera_size, grayscale, screen=None,
                     measured=False, page_clock_id=None):
    """Build provenance internally; never accept a request or image as metadata."""
    width, height = camera_size
    focal = width*geometry.focal_scale
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=1)
        commit = result.stdout.strip() if result.returncode == 0 else 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        commit = 'unknown'
    dimensions = dict(screen or {})
    dimensions.update(width_cm=geometry.width_cm, height_cm=geometry.height_cm, measured=measured)
    return {
        'source': source, 'version': json.loads((ROOT/'package.json').read_text())['version'],
        'commit': commit, 'system': platform.platform(), 'device_name': platform.node() or platform.machine(),
        'inference_device': getattr(engine, 'device', 'unknown'),
        'inference_device_name': getattr(engine, 'device_name', 'unknown'),
        'screen': dimensions, 'camera': {'width_px': width, 'height_px': height,
            'intrinsics_source': 'estimated', 'matrix': [[focal,0,width/2],[0,focal,height/2],[0,0,1]],
            'distortion': [0,0,0,0,0]},
        'grayscale': grayscale, 'page_clock_id': page_clock_id,
    }


def validate_metadata(metadata):
    allowed = {'source', 'version', 'commit', 'system', 'device_name', 'inference_device',
               'inference_device_name', 'screen', 'camera', 'grayscale', 'page_clock_id'}
    if set(metadata) != allowed or metadata['source'] not in {'web', 'python'}:
        raise ValueError('会话元信息字段不完整或包含未允许的数据')
    for key in allowed-{'source', 'screen', 'camera', 'grayscale', 'page_clock_id'}:
        text_value(metadata[key], key)
    if type(metadata['grayscale']) is not bool:
        raise ValueError('grayscale 必须是布尔值')
    text_value(metadata['page_clock_id'], 'page_clock_id', nullable=metadata['source']=='python')
    validate_screen(metadata['screen'])
    camera = metadata['camera']
    if not isinstance(camera, dict) or set(camera) != {'width_px','height_px','intrinsics_source','matrix','distortion'}:
        raise ValueError('camera 包含未允许的数据')
    if camera['intrinsics_source'] not in {'estimated', 'calibrated'}:
        raise ValueError('相机内参来源无效')
    number(camera['width_px'], 'camera.width_px'); number(camera['height_px'], 'camera.height_px')
    if not isinstance(camera['matrix'], list) or len(camera['matrix']) != 3:
        raise ValueError('相机矩阵必须为 3×3')
    for row in camera['matrix']: vector(row, 3, 'camera.matrix')
    vector(camera['distortion'], 5, 'camera.distortion')


def validate_payload(kind, fields, source):
    allowed = FRAME_FIELDS if kind == 'frame' else EVENT_FIELDS.get(kind)
    if allowed is None or set(fields)-allowed:
        raise ValueError('记录包含未知类型或未允许字段；不接受图像、字节或 base64')
    if kind == 'frame':
        if type(fields.get('valid')) is not bool or fields.get('status') not in STATUSES:
            raise ValueError('frame 状态无效')
        for key in FRAME_FIELDS-{'valid','status','raw','origin_m','gaze_vector','head_degrees','video_frame'}:
            if key.endswith(('_ns', '_ms')) and key != 'inference_ms':
                clock_value(fields.get(key), key, nullable=True)
            else:
                number(fields.get(key), key, nullable=True)
        if fields.get('frame_id') is not None and (not isinstance(fields['frame_id'], Integral) or fields['frame_id'] < 0):
            raise ValueError('frame_id 必须是非负整数')
        for key, size in [('raw',2),('origin_m',3),('gaze_vector',3),('head_degrees',3)]:
            vector(fields.get(key), size, key, nullable=not (key=='raw' and fields['valid']))
        clock_value(fields.get('inference_completed_ns'), 'inference_completed_ns')
        field = 'browser_capture_ms' if source=='web' else 'capture_ns'
        clock_value(fields.get(field), field)
        video = fields.get('video_frame')
        if video is not None:
            if not isinstance(video, dict) or set(video)-VIDEO_FIELDS:
                raise ValueError('video_frame 包含未允许的数据')
            if video.get('source') not in {'drawImage','requestVideoFrameCallback','requestVideoFrameCallback_latest'}:
                raise ValueError('视频元数据来源无效')
            for key in set(video)-{'source'}: number(video[key], key, nullable=True)
    else:
        clock_value(fields.get('browser_time_ms'), 'browser_time_ms', nullable=source=='python')
        if kind == 'target':
            if fields.get('action') not in {'appear','switch','end'} or fields.get('task_type') not in {'calibrate','evaluate','jump'}:
                raise ValueError('target 类型或动作无效')
            text_value(fields.get('task_id'), 'task_id')
            number(fields.get('index'), 'index')
            vector(fields.get('target'), 2, 'target')
            vector(fields.get('stage_target'), 2, 'stage_target', nullable=True)
            if 'reason' in fields: text_value(fields['reason'], 'reason', nullable=True)
        elif kind == 'calibration':
            if fields.get('action') not in {'applied','reset'} or fields.get('group') not in {'A','B'}:
                raise ValueError('calibration 动作或分组无效')
            text_value(fields.get('calibration_id'), 'calibration_id')
            matrix = fields.get('matrix')
            if matrix is not None:
                if not isinstance(matrix, list) or len(matrix) != 3: raise ValueError('校准矩阵必须为 3×2')
                for row in matrix: vector(row, 2, 'matrix')
        else:
            number(fields.get('x'), 'x'); number(fields.get('y'), 'y')
            if fields.get('button') not in {'left','middle','right'}: raise ValueError('按键类型无效')


class SessionRecorder:
    def __init__(self, metadata, *, settings=None, directory=None, file_factory=None):
        validate_metadata(metadata)
        self.settings = settings or load_settings().recording
        self.session_id = uuid.uuid4().hex
        self.source, self.page_clock_id = metadata['source'], metadata['page_clock_id']
        self._queue = queue.Queue(self.settings.queue_size)
        self._lock = threading.RLock()
        self._closing, self._closed, self._ready = threading.Event(), threading.Event(), threading.Event()
        self._error = None
        self._failed = self._written = 0
        self._next_seq = 1
        self._reason, self._end_browser_ms = 'user', None
        self._open = file_factory or open
        folder = Path(directory or self.settings.output_dir)
        if not folder.is_absolute(): folder = ROOT/folder
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.path = folder/f'{stamp}-{self.session_id[:8]}.jsonl'
        self._header = self._envelope('session', 0, **metadata)
        self._writer = threading.Thread(target=self._write_loop, name='gaze-recorder', daemon=True)
        self._writer.start()
        if not self._ready.wait(self.settings.close_timeout_s):
            self._failure('录制文件打开超时')
            self._closing.set()
            raise RuntimeError(self.error)
        if self.error:
            raise RuntimeError(self.error)

    @property
    def error(self):
        with self._lock: return self._error

    def _envelope(self, kind, seq, **fields):
        return dict(schema_version=SCHEMA_VERSION, type=kind, session_id=self.session_id,
                    seq=seq, server_ns=time.perf_counter_ns(), **fields)

    def _failure(self, message):
        with self._lock:
            self._failed += 1
            if self._error is None: self._error = str(message)
            self._reason = 'recording_failed'

    def fail(self, message):
        """Stop admission on a producer error while the writer drains accepted rows."""
        with self._lock:
            if not self._closing.is_set() and not self._closed.is_set():
                self._failure(message)

    def emit(self, kind, **fields):
        validate_payload(kind, fields, self.source)
        with self._lock:
            if self._closing.is_set() or self._closed.is_set() or self._error:
                if not self._closing.is_set() and not self._closed.is_set(): self._failed += 1
                return False
            record = self._envelope(kind, self._next_seq, **fields)
            if self.source == 'web': record['page_clock_id'] = self.page_clock_id
            encoded = _json_line(record)
            try:
                self._queue.put_nowait(encoded)
            except queue.Full:
                self._failure('录制队列已满；录制已停止，推理继续运行。')
                return False
            self._next_seq += 1
            return True

    def record_frame(self, data, *, capture_ns=None, inference_completed_ns=None, frame_id=None,
                     request_received_ns=None, browser_capture_ms=None, browser_request_ms=None,
                     browser_response_ms=None, video_frame=None):
        if set(data)-RESULT_FIELDS-{'landmarks'}:
            raise ValueError('推理结果含有未允许字段，拒绝写入')
        fields = {key: data.get(key) for key in RESULT_FIELDS}
        fields.update(capture_ns=capture_ns, inference_completed_ns=inference_completed_ns, frame_id=frame_id,
                      request_received_ns=request_received_ns, browser_capture_ms=browser_capture_ms,
                      browser_request_ms=browser_request_ms, browser_response_ms=browser_response_ms, video_frame=video_frame)
        return self.emit('frame', **fields)

    def status(self):
        with self._lock:
            return {'session_id': self.session_id, 'active': not self._closing.is_set() and not self._closed.is_set() and self._error is None,
                    'error': self._error, 'file': self.path.name, 'written_lines': self._written,
                    'failed_records': self._failed}

    def close(self, reason='user', browser_time_ms=None):
        text_value(reason, 'reason'); clock_value(browser_time_ms, 'browser_time_ms', nullable=True)
        with self._lock:
            if not self._closing.is_set():
                self._reason = 'recording_failed' if self._error else reason
                self._end_browser_ms = browser_time_ms
            self._closing.set()
        self._writer.join(self.settings.close_timeout_s)
        if self._writer.is_alive(): raise RuntimeError('录制写线程未能及时退出，文件尚未完成收尾。')
        return self.status()

    def _write_loop(self):
        try:
            with self._open(self.path, 'x', encoding='utf-8') as output:
                output.write(_json_line(self._header))
                output.flush()
                with self._lock: self._written += 1
                self._ready.set()
                while not self._closing.is_set() or not self._queue.empty():
                    try: line = self._queue.get(timeout=.05)
                    except queue.Empty: continue
                    output.write(line)
                    output.flush()
                    with self._lock: self._written += 1
                with self._lock:
                    end = self._envelope('end', self._next_seq, written_lines=self._written+1,
                                         failed_records=self._failed, reason=self._reason,
                                         browser_time_ms=self._end_browser_ms)
                    if self.source == 'web': end['page_clock_id'] = self.page_clock_id
                output.write(_json_line(end))
                output.flush()
                with self._lock: self._written += 1
        except Exception as exc:
            self._failure(f'录制写入失败：{exc}')
        finally:
            self._ready.set()
            self._closed.set()
