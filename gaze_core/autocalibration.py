"""Bounded causal click-offset learning; no files, native input, Qt or images.

Batch M4 and streaming M5 share raw-state updates, deduplication, sample filters
and seeded robust fitting. Only the rolling sample capacity differs.
"""
from collections import Counter,deque
from copy import deepcopy
from dataclasses import replace
from numbers import Integral
import numpy as np

from .config import AutocalSettings,FixationSettings
from .fixation import FixationDetector
from .implicit_calibration import cm_scale,raw_fixation_state,sample_clicks,fit_robust


def _ns(value):
    if isinstance(value,bool) or not isinstance(value,Integral) or value<0:raise ValueError('需要非负单调时钟纳秒')
    return int(value)


class OnlineOffset:
    def __init__(self,geometry,sampling,options=None,*,capacity=None):
        self.geometry=geometry;cm_scale(geometry)
        self.options=options or AutocalSettings()
        self.sampling=replace(sampling,min_occupied_cells=self.options.min_occupied_cells)
        self.capacity=self.options.max_clicks if capacity is None else capacity
        if type(self.capacity) is not int or self.capacity<self.options.min_clicks:raise ValueError('滚动容量不足')
        self.frames=deque();self.states={};self.pending=deque();self.anchors=deque()
        self.samples=deque(maxlen=self.capacity)
        self.detector=FixationDetector(FixationSettings(**sampling.fixation))
        self.last_capture=None;self.last_click=None;self.reasons=Counter();self.seen=0;self.accepted=0
        self.fit=None;self.fit_error=None;self.revision=0;self.updated_ns=None

    def frame(self,row):
        stamp=_ns(row['capture_ns'])
        if self.last_capture is not None and stamp<=self.last_capture:raise ValueError('capture_ns 必须严格递增')
        data={key:deepcopy(row.get(key)) for key in ('raw','valid','status','origin_m','head_degrees')}
        data['capture_ns']=stamp
        self.last_capture=stamp;self.frames.append(data)
        state=raw_fixation_state(self.detector,data,self.geometry)
        if state is not None:self.states[stamp]=state
        self.advance(stamp)
        cutoff=stamp-round(self.options.frame_history_s*1e9)
        while self.frames and (self.frames[0]['capture_ns']<cutoff or len(self.frames)>self.options.queue_size):
            self.states.pop(self.frames.popleft()['capture_ns'],None)

    def click(self,row):
        stamp=_ns(row['event_ns'])
        if self.last_click is not None and stamp<self.last_click:raise ValueError('点击时间必须单调递增')
        self.last_click=stamp;self.seen+=1
        if len(self.pending)>=self.options.queue_size:raise RuntimeError('自动粗校准待处理点击队列已满')
        self.pending.append({k:row.get(k) for k in ('event_ns','x','y','excluded','exclusion_reason')})
        self.advance(stamp)

    def advance(self,now_ns,*,finished=False):
        now_ns=_ns(now_ns)
        while self.pending:
            click=self.pending[0]
            high=click['event_ns']-round(self.options.window_ms[1]*1e6)
            ready=finished or (self.last_capture is not None and self.last_capture>=high)
            if not ready:
                if now_ns-click['event_ns']<self.options.pending_timeout_s*1e9:break
                self.pending.popleft();self.reasons['inference_watermark_timeout']+=1;continue
            self.pending.popleft();self._sample(click)

    def _sample(self,click):
        cutoff=click['event_ns']-self.sampling.duplicate_ms*1e6
        while self.anchors and self.anchors[0]['event_ns']<cutoff:self.anchors.popleft()
        rows=list(self.anchors)+[click]
        result=sample_clicks(list(self.frames),rows,self.geometry,self.sampling,self.options.window_ms,
                             fixation_states=self.states)
        audit=result['audit'][-1];reason=audit['reason'];self.reasons[reason]+=1
        # Only first presses are anchors, including quality-rejected presses.
        # Retaining a duplicate as an anchor would incorrectly extend a chain.
        if reason not in {'duplicate_press','self_window'}:self.anchors.append(click)
        if reason!='accepted':return
        self.accepted+=1;self.samples.append(result['samples'][-1])
        if len(self.samples)<self.options.min_clicks:return
        try:
            fitted=fit_robust(list(self.samples),'offset',self.geometry,self.sampling,seed=self.sampling.seed)
            if fitted['inlier_count']<self.options.min_clicks:raise ValueError('稳健拟合内点尚未达到最少点击数')
        except ValueError as exc:
            self.fit_error=str(exc);return
        self.fit=fitted;self.fit_error=None;self.revision+=1;self.updated_ns=click['event_ns']

    def finish(self,cutoff_ns):
        """Explicit replay EOF watermark; never manufacture a camera frame."""
        self.advance(cutoff_ns,finished=True)

    def state(self):
        matrix=None if self.fit is None else deepcopy(self.fit['matrix'])
        return dict(seen=self.seen,accepted=self.accepted,reasons=dict(self.reasons),
                    rejected=self.seen-self.accepted-len(self.pending),pending=len(self.pending),
                    window_count=len(self.samples),used_count=0 if self.fit is None else self.fit['inlier_count'],
                    matrix=matrix,offset_norm=None if matrix is None else matrix[2],
                    occupied_cells=0 if self.fit is None else self.fit['inlier_coverage']['occupied_cells'],
                    fit_error=self.fit_error,revision=self.revision,updated_ns=self.updated_ns)


def correction_matrix(offset):
    values=np.asarray(offset,dtype=float)
    if values.shape!=(2,) or not np.isfinite(values).all():raise ValueError('粗校准偏移必须为两个有限数值')
    return [[1.,0.],[0.,1.],values.tolist()]
