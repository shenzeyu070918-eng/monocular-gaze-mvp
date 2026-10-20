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
                 'stage_width_px', 'stage_height_px', 'horizontal_cm', 'top_cm', 'screen_x', 'screen_y', 'outer_width_px', 'outer_height_px'}
VIDEO_FIELDS = {'source', 'callback_ms', 'presentationTime', 'expectedDisplayTime', 'mediaTime',
                'presentedFrames', 'captureTime', 'receiveTime', 'processingDuration', 'width', 'height'}
RESULT_FIELDS = {'valid', 'status', 'raw', 'origin_m', 'gaze_vector', 'head_degrees', 'distance_cm',
                 'eye_openness', 'inference_ms', 'camera'}
FRAME_FIELDS = RESULT_FIELDS | {'capture_ns', 'request_received_ns', 'inference_completed_ns', 'frame_id',
                               'browser_capture_ms', 'browser_request_ms', 'browser_response_ms', 'video_frame'}
EVENT_FIELDS = {
    'target': {'action', 'task_type', 'task_id', 'index', 'target', 'stage_target', 'browser_time_ms', 'reason', 'target_id', 'region'},
    'calibration': {'action', 'matrix', 'calibration_id', 'group', 'browser_time_ms', 'coordinate_system'},
    'distance_check': {'measured_cm', 'estimated_cm', 'bias_cm', 'bias_percent', 'sample_count',
                       'sample_start_ms', 'sample_end_ms', 'browser_time_ms', 'warning', 'camera'},
    'evaluation_summary': {'task_id', 'run_id', 'calibration_id', 'completed', 'reason', 'summary', 'browser_time_ms'},
    'click': {'x', 'y', 'button', 'browser_time_ms'},  # Reserved schema; no input listener in M0.
}
for _event_fields in EVENT_FIELDS.values():
    _event_fields.add('event_ns')
for _kind in ('calibration', 'evaluation_summary'):
    EVENT_FIELDS[_kind].update({'estimated_distance_cm_median', 'estimated_distance_sample_count',
                                'conditions_fingerprint', 'pipeline', 'profile_file'})
STATUSES = {'tracking', 'no_face', 'blink', 'geometry_invalid', 'prediction_error', 'screen_unconfigured'}


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
    if isinstance(value, Integral) and (field.endswith(('_ns','_count','_frames','_targets')) or field in {
            'schema_version', 'seq', 'frame_id', 'index', 'camera_index', 'configured_camera_index', 'sample_count', 'written_lines', 'failed_records'}):
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
                     measured=False, page_clock_id=None, browser_camera=None):
    """Build provenance internally; never accept a request or image as metadata."""
    from .camera import CameraAdapter
    provider = getattr(engine, 'camera_metadata', None)
    camera = (provider(camera_size, geometry) if provider is not None else
              CameraAdapter().prepare(camera_size, geometry.focal_scale).metadata)
    engine_settings = getattr(engine, 'settings', None)
    if engine_settings is not None:
        camera = dict(camera, configured_camera_index=engine_settings.camera.index)
        if source == 'python':
            camera['camera_index'] = engine_settings.camera.index
    if browser_camera is not None:
        if not isinstance(browser_camera, dict) or set(browser_camera)-{'device_id', 'label'}:
            raise ValueError('浏览器相机信息字段无效')
        camera = dict(camera, browser_camera=browser_camera)
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=1)
        commit = result.stdout.strip() if result.returncode == 0 else 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        commit = 'unknown'
    dimensions = dict(screen or {})
    profile = getattr(getattr(engine, 'settings', None), 'device_profile', None)
    if source == 'python' and profile and profile.screen_resolution:
        dimensions.update(width_px=profile.screen_resolution[0], height_px=profile.screen_resolution[1])
    dimensions.update(width_cm=geometry.width_cm, height_cm=geometry.height_cm, measured=measured,
                      horizontal_cm=getattr(geometry, 'horizontal_cm', 0), top_cm=geometry.top_cm)
    return {
        'source': source, 'version': json.loads((ROOT/'package.json').read_text())['version'],
        'commit': commit, 'system': platform.platform(), 'device_name': platform.node() or platform.machine(),
        'inference_device': getattr(engine, 'device', 'unknown'),
        'inference_device_name': getattr(engine, 'device_name', 'unknown'),
        'screen': dimensions, 'camera': camera,
        'grayscale': grayscale, 'page_clock_id': page_clock_id,
    }


def validate_metadata(metadata):
    required = {'source', 'version', 'commit', 'system', 'device_name', 'inference_device',
               'inference_device_name', 'screen', 'camera', 'grayscale', 'page_clock_id'}
    allowed = required | {'pipeline', 'conditions_fingerprint', 'profile_file'}
    if not required <= set(metadata) or set(metadata)-allowed or metadata['source'] not in {'web', 'python'}:
        raise ValueError('会话元信息字段不完整或包含未允许的数据')
    for key in required-{'source', 'screen', 'camera', 'grayscale', 'page_clock_id'}:
        text_value(metadata[key], key)
    if 'pipeline' in metadata and metadata['pipeline'] not in ('web', 'python'):
        raise ValueError('pipeline 必须为 web 或 python')
    for key in ('conditions_fingerprint', 'profile_file'):
        if key in metadata:
            text_value(metadata[key], key, nullable=True)
    if type(metadata['grayscale']) is not bool:
        raise ValueError('grayscale 必须是布尔值')
    text_value(metadata['page_clock_id'], 'page_clock_id', nullable=metadata['source']=='python')
    validate_screen(metadata['screen'])
    validate_camera(metadata['camera'])


def validate_camera(camera, nullable=False):
    if camera is None and nullable:
        return
    required = {'width_px', 'height_px', 'intrinsics_source', 'matrix', 'distortion'}
    extra = {'intrinsics_reason', 'device_file', 'calibration_resolution', 'reprojection_error_px',
             'input_matrix', 'input_distortion', 'scaled', 'undistort_enabled', 'undistort_applied',
             'camera_index', 'configured_camera_index', 'browser_camera'}
    if not isinstance(camera, dict) or not required <= set(camera) or set(camera)-required-extra:
        raise ValueError('camera 包含未允许的数据')
    for key in ('camera_index', 'configured_camera_index'):
        if key in camera and (not isinstance(camera[key], Integral) or isinstance(camera[key], bool) or camera[key] < 0):
            raise ValueError(key+' 必须是非负整数')
    if 'browser_camera' in camera:
        value = camera['browser_camera']
        if not isinstance(value, dict) or set(value)-{'device_id', 'label'}:
            raise ValueError('浏览器相机字段无效')
        for key, item in value.items():
            text_value(item, key, nullable=True)
    if camera['intrinsics_source'] not in {'estimated', 'calibrated'}:
        raise ValueError('相机内参来源无效')
    for key in ('width_px', 'height_px'):
        if number(camera[key], key) <= 0:
            raise ValueError('相机分辨率必须为正数')
    for key in ('matrix', 'input_matrix'):
        if key not in camera:
            continue
        if not isinstance(camera[key], list) or len(camera[key]) != 3:
            raise ValueError('相机矩阵必须为 3×3')
        for row in camera[key]:
            vector(row, 3, 'camera.'+key)
    for key in ('distortion', 'input_distortion'):
        if key in camera:
            vector(camera[key], 5, 'camera.'+key)
    if camera.get('intrinsics_reason') not in {None, 'no_device_file', 'aspect_ratio_mismatch'}:
        raise ValueError('相机内参回退原因无效')
    if 'device_file' in camera:
        text_value(camera['device_file'], 'device_file', nullable=True)
    if camera.get('calibration_resolution') is not None:
        vector(camera['calibration_resolution'], 2, 'calibration_resolution')
    if 'reprojection_error_px' in camera:
        value = number(camera['reprojection_error_px'], 'reprojection_error_px', nullable=True)
        if value is not None and value < 0:
            raise ValueError('重投影误差不能为负')
    for key in ('scaled', 'undistort_enabled', 'undistort_applied'):
        if key in camera and type(camera[key]) is not bool:
            raise ValueError(key+' 必须为布尔值')


def validate_payload(kind, fields, source):
    allowed = FRAME_FIELDS if kind == 'frame' else EVENT_FIELDS.get(kind)
    if allowed is None or set(fields)-allowed:
        raise ValueError('记录包含未知类型或未允许字段；不接受图像、字节或 base64')
    if kind == 'frame':
        if type(fields.get('valid')) is not bool or fields.get('status') not in STATUSES:
            raise ValueError('frame 状态无效')
        for key in FRAME_FIELDS-{'valid','status','raw','origin_m','gaze_vector','head_degrees','video_frame','camera'}:
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
        validate_camera(fields.get('camera'), nullable=True)
        video = fields.get('video_frame')
        if video is not None:
            if not isinstance(video, dict) or set(video)-VIDEO_FIELDS:
                raise ValueError('video_frame 包含未允许的数据')
            if video.get('source') not in {'drawImage','requestVideoFrameCallback','requestVideoFrameCallback_latest'}:
                raise ValueError('视频元数据来源无效')
            for key in set(video)-{'source'}: number(video[key], key, nullable=True)
    else:
        clock_value(fields.get('browser_time_ms'), 'browser_time_ms', nullable=source=='python')
        if 'event_ns' in fields:
            clock_value(fields['event_ns'], 'event_ns')
        if 'pipeline' in fields and fields['pipeline'] not in ('python', 'web'):
            raise ValueError('pipeline 必须为 python 或 web')
        for key in ('conditions_fingerprint', 'profile_file'):
            if key in fields:
                text_value(fields[key], key, nullable=True)
        if 'estimated_distance_cm_median' in fields:
            value = number(fields['estimated_distance_cm_median'], 'estimated_distance_cm_median', nullable=True)
            if value is not None and value <= 0:
                raise ValueError('estimated_distance_cm_median 必须为正数或 null')
        if 'estimated_distance_sample_count' in fields:
            value = fields['estimated_distance_sample_count']
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
                raise ValueError('estimated_distance_sample_count 必须为非负整数')
        if kind == 'target':
            if fields.get('action') not in {'appear','switch','end'} or fields.get('task_type') not in {'calibrate','evaluate','jump'}:
                raise ValueError('target 类型或动作无效')
            if 'target_id' in fields:text_value(fields['target_id'],'target_id')
            if 'region' in fields and fields['region'] not in {'center','edge'}:raise ValueError('region 无效')
            text_value(fields.get('task_id'), 'task_id')
            number(fields.get('index'), 'index')
            vector(fields.get('target'), 2, 'target')
            vector(fields.get('stage_target'), 2, 'stage_target', nullable=True)
            if 'reason' in fields: text_value(fields['reason'], 'reason', nullable=True)
        elif kind == 'calibration':
            if fields.get('action') not in {'applied','reset'} or fields.get('group') not in {'A','B'}:
                raise ValueError('calibration 动作或分组无效')
            if fields.get('coordinate_system') not in {None,'full_screen_normalized'}:raise ValueError('校准坐标系无效')
            text_value(fields.get('calibration_id'), 'calibration_id')
            matrix = fields.get('matrix')
            if matrix is not None:
                if not isinstance(matrix, list) or len(matrix) != 3: raise ValueError('校准矩阵必须为 3×2')
                for row in matrix: vector(row, 2, 'matrix')
        elif kind == 'distance_check':
            for key in ('measured_cm', 'estimated_cm', 'bias_cm', 'bias_percent', 'sample_count'):
                number(fields.get(key), key, nullable=key in {'estimated_cm', 'bias_cm', 'bias_percent'})
            if fields['measured_cm'] <= 0 or not isinstance(fields['sample_count'], Integral) or fields['sample_count'] < 0:
                raise ValueError('视距自检的实测值和样本数无效')
            if type(fields.get('warning')) is not bool:
                raise ValueError('视距自检 warning 必须为布尔值')
            for key in ('sample_start_ms', 'sample_end_ms'):
                clock_value(fields.get(key), key, nullable=True)
            validate_camera(fields.get('camera'), nullable=True)
        elif kind == 'evaluation_summary':
            from .evaluation import validate_summary
            for key in ('task_id','run_id','calibration_id','reason'):text_value(fields.get(key),key)
            if type(fields.get('completed')) is not bool:raise ValueError('completed 必须为布尔值')
            validate_summary(fields.get('summary'))
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
