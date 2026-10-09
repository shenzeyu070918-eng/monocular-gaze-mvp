import base64
import binascii
import threading
from contextlib import asynccontextmanager
from pathlib import Path
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from engine import Engine
from geometry import fit_affine

ROOT = Path(__file__).resolve().parent
state = {'ready': False, 'error': None}
lock = threading.Lock()
engine = None


def load_engine():
    global engine
    try:
        engine = Engine()
        state.update(ready=True, parameters=engine.parameters, model='ETH-XGaze / ResNet18', device='CPU')
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
    if engine:
        with lock:
            engine.close()


app = FastAPI(lifespan=lifespan)


class Geometry(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    width_cm: float = Field(default=30.2, ge=10, le=100)
    height_cm: float = Field(default=19.6, ge=8, le=80)
    top_cm: float = Field(default=0.2, ge=-5, le=10)
    focal_scale: float = Field(default=1, ge=0.4, le=3)


class Frame(BaseModel):
    image: str = Field(max_length=1500000)
    grayscale: bool = False
    geometry: Geometry = Field(default_factory=Geometry)


class Sample(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    raw: tuple[float, float]
    target: tuple[float, float]


class Calibration(BaseModel):
    samples: list[Sample] = Field(min_length=9, max_length=5000)


@app.get('/api/status')
def status():
    return state


@app.post('/api/frame')
def frame(req: Frame):
    if not state['ready']:
        raise HTTPException(503, state['error'] or '模型正在加载')
    try:
        data = base64.b64decode(req.image, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, '图像编码无效')
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] > 768 or image.shape[1] > 1024:
        raise HTTPException(400, '图像无效或超过 1024×768')
    with lock:
        try:
            return engine.predict(image, req.geometry, req.grayscale)
        except (ValueError, RuntimeError, cv2.error):
            return {'valid': False, 'status': 'prediction_error', 'inference_ms': 0}


@app.post('/api/calibrate')
def calibrate(req: Calibration):
    try:
        return {'matrix': fit_affine([s.model_dump() for s in req.samples])}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


app.mount('/', StaticFiles(directory=ROOT/'static', html=True), name='ui')
