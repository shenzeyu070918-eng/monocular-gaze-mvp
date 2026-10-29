"""Low-frequency scene decisions; bounded history, no native or filesystem I/O."""
from collections import deque
import cv2
import numpy as np


def thumbnail(pixels,width):
    h,w=pixels.shape[:2]
    return cv2.resize(cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY),(width,max(1,round(h*width/w))),interpolation=cv2.INTER_AREA).astype(np.float32)


def scroll_offset(old,new,options):
    if old.shape!=new.shape:return None
    a=cv2.cvtColor(old,cv2.COLOR_RGB2GRAY).astype(np.float32);b=cv2.cvtColor(new,cv2.COLOR_RGB2GRAY).astype(np.float32)
    factor=min(1.,options.scroll_width/a.shape[1]);size=(max(2,round(a.shape[1]*factor)),max(2,round(a.shape[0]*factor)))
    a=cv2.resize(a,size);b=cv2.resize(b,size)
    if a.std()<1 or b.std()<1:return dict(vertical_px=None,confidence=0.,accepted=False,method='phase_correlation')
    window=cv2.createHanningWindow(size,cv2.CV_32F)
    (dx,dy),response=cv2.phaseCorrelate(a,b,window)
    accepted=bool(response>=options.scroll_min_confidence and abs(dx/factor)<=options.scroll_max_horizontal_px)
    return dict(vertical_px=float(dy/factor) if accepted else None,horizontal_px=float(dx/factor),confidence=float(np.clip(response,0,1)),accepted=accepted,method='phase_correlation',direction='new_content_y_minus_old_content_y_logical_px')


class SceneDetector:
    def __init__(self,options):
        self.o=options;self.previous=None;self.reference=None;self.last_old=None
        self.changes=deque(maxlen=options.repeat_samples);self.pending=[];self.pending_since=None
        self.last_scene_ns=None;self.last_scene_pixels=None;self.last_app=None;self.number=0

    def _tiles(self,a,b):
        diff=cv2.absdiff(a,b);h,w=diff.shape;tile=self.o.tile_px
        return np.array([[float(diff[y:y+tile,x:x+tile].mean())>=self.o.pixel_delta for x in range(0,w,tile)] for y in range(0,h,tile)])

    def _different(self,a,b,mask):
        tiles=self._tiles(a,b);tiles &= ~mask
        return bool(tiles.mean()>=self.o.min_changed_area)

    def interrupt(self):
        self.previous=self.reference=self.last_old=None;self.changes.clear();self.pending=[];self.pending_since=None

    def observe(self,sample):
        now=sample.time_ns;thumb=thumbnail(sample.pixels,self.o.thumbnail_width)
        if self.previous is not None and self.previous.shape==thumb.shape:self.changes.append(self._tiles(self.previous,thumb))
        self.previous=thumb
        shape=self._tiles(thumb,thumb).shape;mask=np.zeros(shape,bool)
        if len(self.changes)>=self.o.repeat_count:
            repeating=np.sum(self.changes,axis=0)>=self.o.repeat_count
            count,labels,stats,_=cv2.connectedComponentsWithStats(repeating.astype(np.uint8),8)
            eligible=np.zeros_like(repeating)
            for label,(_,_,width,height,_) in enumerate(stats[1:],1):
                # Whole-page vertical changes are not a small animation, even
                # when sparse text occupies only a small fraction of pixels.
                if width/repeating.shape[1]<=self.o.max_dynamic_extent and height/repeating.shape[0]<=self.o.max_dynamic_extent:eligible|=labels==label
            if eligible.mean()<=self.o.max_dynamic_area:mask=eligible
        app_changed=self.last_app is not None and sample.app_name!=self.last_app
        if self.reference is not None and not app_changed and not self._different(self.reference,thumb,mask):
            self.last_old=sample;self.pending=[];self.pending_since=None
            return None
        if self.pending and self.pending[-1][0].app_name!=sample.app_name:self.pending=[];self.pending_since=None
        if not self.pending:self.pending_since=now
        self.pending.append((sample,thumb))
        # Keep only enough history to cover the bounded unstable period.
        limit=max(4,int(self.o.max_unstable_ms*self.o.capture_hz/1000)+4)
        self.pending=self.pending[-limit:]
        matching=[]
        for item,small in reversed(self.pending):
            if item.app_name!=sample.app_name or self._different(small,thumb,mask):break
            matching.append(item)
        first=matching[-1]
        forced=(now-self.pending_since)/1e6>=self.o.max_unstable_ms
        stable=(now-first.time_ns)/1e6>=self.o.stable_ms
        interval_ok=self.last_scene_ns is None or (now-self.last_scene_ns)/1e6>=self.o.min_scene_interval_ms
        if not interval_ok or not (stable or forced):return None
        if forced and not stable:first=self.pending[0][0]
        self.number+=1
        uncertain=None if self.last_old is None else [self.last_old.time_ns,first.time_ns]
        result=dict(type='scene',scene_id=f'{self.number:06d}',start_ns=first.time_ns,confirmed_ns=now,
                    request_ns=first.request_ns,received_ns=first.received_ns,time_source='snapshot_request_receive_midpoint',
                    request_span_ns=first.received_ns-first.request_ns,timestamp_bound_guaranteed=False,transition=uncertain,
                    dynamic=bool(forced and not stable),start_basis='first_changed_timeout' if forced and not stable else 'first_matching_stable',ignored_dynamic_fraction=float(mask.mean()),app_name=sample.app_name)
        if self.last_scene_pixels is not None and self.last_app==sample.app_name:
            result['scroll']=scroll_offset(self.last_scene_pixels,sample.pixels,self.o)
        self.last_scene_ns=now;self.last_scene_pixels=sample.pixels.copy();self.last_app=sample.app_name
        self.reference=thumb;self.last_old=sample;self.pending=[];self.pending_since=None
        return result
