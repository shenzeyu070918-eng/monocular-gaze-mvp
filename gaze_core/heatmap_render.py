"""Display-only float interpolation before color/alpha quantization; no Qt."""
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import uuid
import cv2
import numpy as np

from .devices import finite
from .heatmap import rgba_grid


def render_heatmap(grid,screen_size,*,max_opacity=.4,alpha_threshold=.15,render_scale=.5,dither_strength=.5):
    """Return RGBA at a fraction of logical screen size, never physical DPR.

    Cubic interpolation may overshoot: clamp to the input range so it cannot
    manufacture a new peak or negative heat. This is visualization only; the
    accumulator, its CSV and all region statistics retain their original grid.
    """
    values=np.asarray(grid,np.float32)
    if values.ndim!=2 or not values.size or not np.isfinite(values).all() or (values<0).any():
        raise ValueError('热图网格必须为非空二维非负有限数值')
    scale=finite(render_scale,'render_scale',positive=True)
    if scale>1:raise ValueError('render_scale 不得超过1')
    width,height=[finite(v,'logical screen size',positive=True) for v in screen_size]
    target=(max(1,round(width*scale)),max(1,round(height*scale)))
    enlarged=cv2.resize(values,target,interpolation=cv2.INTER_CUBIC)
    np.clip(enlarged,0,float(values.max()),out=enlarged)
    return rgba_grid(enlarged,max_opacity,alpha_threshold,dither_strength=dither_strength)


class RenderLog:
    """Lazy, worker-owned numeric log: no file until an actual view render."""
    def __init__(self,directory=None):
        self.directory=directory;self.path=None;self._stream=None;self.count=0;self.last=None

    def record(self,elapsed_ms,rgba,options):
        if self._stream is None:
            from .config import ROOT
            directory=Path(self.directory or ROOT/'data/performance');directory.mkdir(parents=True,exist_ok=True)
            self.path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex[:8]+'-heatmap-render.jsonl')
            self._stream=self.path.open('x',encoding='utf-8')
        row=dict(render_ns=time.perf_counter_ns(),render_ms=float(elapsed_ms),width=rgba.shape[1],height=rgba.shape[0],
                 render_scale=options.render_scale,dither_strength=options.dither_strength)
        self._stream.write(json.dumps(row,allow_nan=False)+'\n');self._stream.flush()
        self.count+=1;self.last=row

    def close(self):
        if self._stream is not None:self._stream.close();self._stream=None
