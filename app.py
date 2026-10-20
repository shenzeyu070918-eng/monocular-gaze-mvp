import base64
import binascii
import threading
import time
from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, model_validator
from gaze_core.engine import Engine
from gaze_core.geometry import fit_affine
from gaze_core.evaluation import evaluate
from gaze_core.profiles import save_profile, load_profile
from gaze_core.display import DisplaySessions
from gaze_core.config import load_settings, configuration_info, print_configuration
from gaze_core.web_recording import WebRecording, RecordingConflict

ROOT = Path(__file__).resolve().parent
state = {'ready': False, 'error': None}
lock = threading.Lock()
engine = None
settings = load_settings()
print_configuration(settings)
recording = WebRecording(settings)
display_sessions = DisplaySessions(settings)


def load_engine():
    global engine
    try:
        engine = Engine(settings)
        state.update(ready=True, parameters=engine.parameters, model='ETH-XGaze / ResNet18', device=engine.device.upper(), device_name=engine.device_name)
    except Exception as exc:
        state['error'] = str(exc)
        import traceback
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app):
    worker = threading.Thread(target=load_engine, daemon=True)
    worker.start()
    yield
    worker.join(timeout=5)
    try:
        recording.stop(reason='server_shutdown')
    finally:
        if engine:
            with lock:
                engine.close()


app = FastAPI(lifespan=lifespan)


class Geometry(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    width_cm: float | None = Field(default=settings.geometry.width_cm, gt=0)
    height_cm: float | None = Field(default=settings.geometry.height_cm, gt=0)
    top_cm: float = settings.geometry.top_cm
    focal_scale: float = Field(default=settings.geometry.focal_scale, gt=0)
    horizontal_cm: float = settings.geometry.horizontal_cm
    measured: bool = settings.geometry.measured

    @model_validator(mode='after')
    def validate_dimensions(self):
        if (self.width_cm is None) != (self.height_cm is None) or (self.measured and self.width_cm is None):
            raise ValueError('屏幕宽高必须同时填写；没有尺寸不能标记为实测')
        return self


class DisplayContext(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    generation: int = Field(ge=0, strict=True)
    revision: int = Field(ge=0, strict=True)
    matrix: list[tuple[float,float]] | None = Field(default=None,min_length=3,max_length=3)


class Frame(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    image: str = Field(max_length=1500000)
    grayscale: bool = False
    geometry: Geometry = Field(default_factory=Geometry)
    recording_session_id: str | None = Field(default=None, max_length=64)
    page_clock_id: str | None = Field(default=None, max_length=64)
    frame_id: int | None = Field(default=None, ge=0)
    browser_capture_ms: float | None = Field(default=None, ge=0)
    browser_request_ms: float | None = Field(default=None, ge=0)
    video_frame: dict | None = None
    display_context: DisplayContext | None = None

    @model_validator(mode='after')
    def validate_display_clock(self):
        if self.display_context is not None and not self.page_clock_id:
            raise ValueError('显示管线需要页面时钟标识')
        return self


class RecordingStart(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    page_clock_id: str = Field(min_length=1, max_length=64)
    screen: dict
    geometry: Geometry
    measured: bool
    grayscale: bool
    browser_camera: dict | None = None
    camera_width: int = Field(ge=1, le=1024)
    camera_height: int = Field(ge=1, le=768)


class RecordingStop(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    session_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=512)
    browser_time_ms: float = Field(ge=0)


class RecordingReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    session_id: str = Field(min_length=1, max_length=64)
    frame_id: int = Field(ge=0)
    browser_response_ms: float = Field(ge=0)


class Sample(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    raw: tuple[float, float]
    target: tuple[float, float]


class Calibration(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    samples: list[Sample] = Field(min_length=9, max_length=5000)
    fullscreen: bool
    screen: dict | None = None
    profile_name: str | None = Field(default=None,max_length=64)
    calibration_id: str | None = Field(default=None,max_length=64)


class EvaluationTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    target_id: str = Field(min_length=1,max_length=64)
    region: str
    target: tuple[float,float]


class EvaluationFrame(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    target_id: str = Field(min_length=1,max_length=64)
    valid: bool
    raw: tuple[float,float] | None = None
    origin_m: tuple[float,float,float] | None = None


class Evaluation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    frames: list[EvaluationFrame] = Field(max_length=100000)
    targets: list[EvaluationTarget] = Field(min_length=1,max_length=100)
    geometry: Geometry
    matrix: list[tuple[float,float]] | None = Field(default=None,min_length=3,max_length=3)
    session_id: str | None = Field(default=None,max_length=64)
    task_id: str = Field(min_length=1,max_length=64)
    run_id: str = Field(min_length=1,max_length=64)
    calibration_id: str = Field(min_length=1,max_length=64)
    completed: bool
    reason: str = Field(min_length=1,max_length=512)
    browser_time_ms: float = Field(ge=0)



@app.get('/api/status')
def status():
    return dict(state, configuration=configuration_info(settings), camera=getattr(engine, 'current_camera', None))


@app.post('/api/frame')
def frame(req: Frame):
    received_ns = time.perf_counter_ns()
    if not state['ready']:
        raise HTTPException(503, state['error'] or '模型正在加载')
    try:
        data = base64.b64decode(req.image, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, '图像编码无效')
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] > 768 or image.shape[1] > 1024:
        raise HTTPException(400, '图像无效或超过 1024×768')
    ticket = recording_call(recording.begin_frame, req.recording_session_id, req.page_clock_id,
                            req.frame_id, request_received_ns=received_ns,
                            browser_capture_ms=req.browser_capture_ms,
                            browser_request_ms=req.browser_request_ms, video_frame=req.video_frame)
    result = {'valid': False, 'status': 'prediction_error', 'inference_ms': 0}
    try:
        with lock:
            try:
                result = engine.predict(image, req.geometry, req.grayscale)
            except (ValueError, RuntimeError, cv2.error):
                pass
            completed_ns = time.perf_counter_ns()
        if req.display_context is None:
            return result
        context=req.display_context
        display=display_sessions.process(req.page_clock_id,context.generation,context.revision,result,
                                         geometry=req.geometry,matrix=context.matrix,browser_capture_ms=req.browser_capture_ms)
        return dict(result,display=display)
    finally:
        recording.complete_frame(ticket, result, locals().get('completed_ns', time.perf_counter_ns()))


def recording_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except RecordingConflict as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, str(exc))


@app.get('/api/recording/config')
def recording_config():
    return {'filter':asdict(settings.filter),'fixation':asdict(settings.fixation), 'configuration': configuration_info(settings), 'evaluation': asdict(settings.evaluation), 'jump': asdict(settings.jump), 'request_timeout_ms': settings.recording.request_timeout_s*1000,
            'geometry': asdict(settings.geometry), 'distance_check': asdict(settings.distance_check),
            'camera': engine.camera_metadata() if engine and hasattr(engine, 'camera_metadata') else None,
            'profile_name': settings.device_profile.name if settings.device_profile else None,
            'device_file': settings.device_profile.filename if settings.device_profile else None,
            'screen_resolution': settings.device_profile.screen_resolution if settings.device_profile else None}


@app.get('/api/recording/status')
def recording_status():
    return recording.status()


@app.post('/api/recording/start')
def recording_start(req: RecordingStart):
    if not state['ready']:
        raise HTTPException(503, state['error'] or '模型正在加载')
    return recording_call(recording.start, engine=engine, geometry=req.geometry, screen=req.screen,
                          measured=req.measured, grayscale=req.grayscale,
                          camera_size=(req.camera_width, req.camera_height), page_clock_id=req.page_clock_id, browser_camera=req.browser_camera)


@app.post('/api/recording/event')
def recording_event(req: dict):
    body = dict(req)
    session_id, kind = body.pop('session_id', None), body.pop('type', None)
    return recording_call(recording.event, session_id, kind, body)


@app.post('/api/recording/receipt')
def recording_receipt(req: RecordingReceipt):
    return recording_call(recording.receipt, req.session_id, req.frame_id, req.browser_response_ms)


@app.post('/api/recording/stop')
def recording_stop(req: RecordingStop):
    return recording_call(recording.stop, req.session_id, req.reason, req.browser_time_ms)


@app.post('/api/evaluation/summary')
def evaluation_summary(req: Evaluation):
    try:
        summary = evaluate([r.model_dump() for r in req.frames], [t.model_dump() for t in req.targets], req.geometry, req.matrix)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    if req.session_id:
        fields = {key:getattr(req,key) for key in ('task_id','run_id','calibration_id','completed','reason','browser_time_ms')}
        recording_call(recording.event,req.session_id,'evaluation_summary',dict(fields,summary=summary))
    return summary


class ProfileLoad(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1,max_length=64)
    geometry: Geometry
    screen: dict


@app.post('/api/profile/load')
def profile_load(req: ProfileLoad):
    name=settings.device_profile.name if settings.device_profile else req.name
    try:
        return load_profile(name=name,device_file=settings.device_profile.filename if settings.device_profile else None,
                            geometry=req.geometry,screen=req.screen)
    except FileNotFoundError:
        raise HTTPException(404,'尚无本机个人档案，请先九点校准')
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc))


@app.post('/api/calibrate')
def calibrate(req: Calibration):
    if not req.fullscreen:
        raise HTTPException(422,'九点校准必须全屏进行')
    try:
        matrix = fit_affine([s.model_dump() for s in req.samples])
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    result = {'matrix':matrix,'profile_file':None,'profile_error':None}
    # A fit remains usable in this page if persistence fails, but never claim it saved.
    try:
        if req.screen is None or req.calibration_id is None:
            raise ValueError('缺少全屏坐标元信息，个人档案未保存')
        name = settings.device_profile.name if settings.device_profile else req.profile_name
        if not name:raise ValueError('请填写设备名，个人档案未保存')
        path = save_profile(matrix,name=name,device_file=settings.device_profile.filename if settings.device_profile else None,
                            screen=req.screen,calibration_id=req.calibration_id)
        result['profile_file'] = path.name
    except (ValueError,OSError) as exc:
        result['profile_error'] = str(exc)
    return result


app.mount('/', StaticFiles(directory=ROOT/'static', html=True), name='ui')
