"""Shared display-only pipeline, used by web, optional Python capture and tuning.

Engine dictionaries are never changed. Timestamp provenance is explicit: browser
capture milliseconds or OpenCV read-return nanoseconds, never server arrival or
completion. All computation precedes viewport clipping, in full-screen units.
"""
from collections import OrderedDict
from numbers import Integral
import threading
import time
import numpy as np
from .config import FilterSettings, FixationSettings, DisplaySettings
from .devices import finite
from .filters import OneEuro2D, position
from .fixation import FixationDetector


_UNSET = object()


def calibration_matrix(value):
    if value is None:return None
    if not isinstance(value,(list,tuple,np.ndarray)) or len(value)!=3:
        raise ValueError('个人矩阵必须为 3×2')
    return np.array([position(row) for row in value])


class DisplayPipeline:
    def __init__(self, filter_settings=None, fixation_settings=None, *, matrix=None):
        self.matrix=calibration_matrix(matrix)
        self.filter=OneEuro2D(filter_settings or FilterSettings())
        self.detector=FixationDetector(fixation_settings or FixationSettings())
        self.source=None;self.clock_origin_ns=None;self.last_seen_s=None
        self.last_s=self.signature=None

    def _clear_signal(self):
        finished=self.detector.reset();self.filter.clear();self.last_s=None
        return finished

    def reset(self):
        finished=self._clear_signal()
        self.source=self.clock_origin_ns=self.last_seen_s=self.signature=None
        return finished

    def process(self, data, *, source, geometry, matrix=_UNSET, browser_capture_ms=None, capture_ns=None):
        if source not in {'web','python'}:raise ValueError('显示时间来源必须为 web/python')
        if self.source is not None and source!=self.source:raise ValueError('显示管线不能混用两种采集时钟')
        self.source=source
        if (source=='web' and browser_capture_ms is None) or (source=='python' and capture_ns is None):
            return dict(position=None,unfiltered=None,state='missing_capture_timestamp',finished_fixations=self._clear_signal())
        if source=='web':
            timestamp=finite(browser_capture_ms,'browser_capture_ms')/1000
            if timestamp<0:raise ValueError('browser_capture_ms 必须非负')
        else:
            if isinstance(capture_ns,bool) or not isinstance(capture_ns,Integral) or capture_ns<0:
                raise ValueError('capture_ns 必须为非负整数')
            if self.clock_origin_ns is None:self.clock_origin_ns=int(capture_ns)
            timestamp=(int(capture_ns)-self.clock_origin_ns)/1e9
        if self.last_seen_s is not None and timestamp<=self.last_seen_s:
            return dict(position=None,unfiltered=None,state='nonincreasing_capture_timestamp',finished_fixations=[])
        self.last_seen_s=timestamp
        transform=calibration_matrix(self.matrix if matrix is _UNSET else matrix)
        signature=(None if transform is None else tuple(transform.flat),
                   tuple(getattr(geometry,k) for k in ('width_cm','height_cm','measured','horizontal_cm','top_cm')))
        finished=self._clear_signal() if signature!=self.signature else []
        self.signature=signature
        if not data.get('valid') or data.get('raw') is None:
            finished+=self._clear_signal()
            return dict(position=None,unfiltered=None,state='invalid_signal',finished_fixations=finished)
        raw=position(data['raw'])
        corrected=np.append(raw,1)@transform if transform is not None else raw
        dt=None if self.last_s is None else timestamp-self.last_s
        if dt is not None and dt*1000>self.detector.settings.max_gap_ms:
            finished+=self._clear_signal();dt=None
        detection=self.detector.update(corrected,timestamp,origin_m=data.get('origin_m'),geometry=geometry)
        filtered=(self.filter.reset(corrected,timestamp) if detection['state']=='saccade'
                  else self.filter.update(corrected,timestamp))
        self.last_s=timestamp
        detection['finished_fixations']=finished+detection['finished_fixations']
        return dict(position=filtered,unfiltered=corrected.tolist(),**detection,dt_s=dt,
                    timestamp_s=timestamp,clock_origin_ns=self.clock_origin_ns,
                    timestamp_source='browser_capture_ms' if source=='web' else 'capture_ns')


class DisplaySessions:
    """Bounded per-page state. Server time only expires entries, never filters data."""
    def __init__(self, settings, *, monotonic=time.monotonic):
        self.settings=settings;self.monotonic=monotonic
        self.pages=OrderedDict();self.lock=threading.Lock()

    def process(self, page_id, generation, revision, data, **kwargs):
        epoch=(generation,revision)
        with self.lock:
            now=self.monotonic()
            expired=[key for key,(_,_,seen) in self.pages.items() if now-seen>self.settings.display.idle_timeout_s]
            for key in expired:del self.pages[key]
            entry=self.pages.get(page_id)
            if entry is not None and epoch<entry[0]:
                return dict(position=None,unfiltered=None,state='stale_display_context',finished_fixations=[])
            pipeline=entry[1] if entry is not None and entry[0]==epoch else DisplayPipeline(self.settings.filter,self.settings.fixation)
            result=pipeline.process(data,source='web',**kwargs)
            self.pages[page_id]=(epoch,pipeline,now);self.pages.move_to_end(page_id)
            while len(self.pages)>self.settings.display.max_pages:self.pages.popitem(last=False)
            return result
