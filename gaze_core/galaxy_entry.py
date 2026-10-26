"""Short-lived, session-only entry correction. Reuses M4's exact sampler."""
from collections import deque
import threading
import numpy as np
from .devices import finite
from .fixation import FixationDetector
from .config import FixationSettings
from .implicit_calibration import load_experiment_settings,raw_fixation_state,sample_clicks,cm_scale

# Numeric diagnostics travel over the bridge; no raw sample history is written.
REASONS={'accepted':0,'missing_clock':1,'unmeasured_screen':2,'insufficient_frames':3,
         'invalid_sample':4,'excessive_offset':5,'missing_corrected_position':6}


class BrowserCaptureClock:
    def __init__(self,max_rtt_ms):self.max_rtt=max_rtt_ms;self.probe_id=0;self.sent=None;self.offset=None;self.rtt=None
    def probe(self,now_ns):
        self.probe_id+=1;self.sent=now_ns;return self.probe_id
    def reply(self,probe_id,browser_ms,now_ns):
        browser_ms=finite(browser_ms,'browser clock')
        if browser_ms<0:raise ValueError('浏览器时钟必须非负')
        if type(probe_id) is not int or probe_id!=self.probe_id or self.sent is None:return False
        self.rtt=(now_ns-self.sent)/1e6;self.sent=None
        if not 0<=self.rtt<=self.max_rtt:return False
        self.offset=(now_ns-int(self.rtt*1e6/2))-browser_ms*1e6;return True
    def capture_time(self,browser_ms,now_ns,max_age_ms):
        if self.offset is None:return None
        t=round(finite(browser_ms,'click time')*1e6+self.offset)
        # Half RTT is clock alignment uncertainty, not an observed callback delay.
        uncertainty=(self.rtt or 0)*.5e6
        return t if -uncertainty<=now_ns-t<=max_age_ms*1e6+uncertainty else None


class EntryRefresh:
    def __init__(self,options,geometry,offset,*,settings=None):
        self.o=options;self.geometry=geometry;self.offset=tuple(offset);self.settings=settings or load_experiment_settings()
        self.rows=deque(maxlen=256);self.states={};self.detector=FixationDetector(FixationSettings(**self.settings.fixation));self.lock=threading.Lock()
        self.reports=[]
    def add(self,capture_ns,data):
        with self.lock:
            if self.rows and capture_ns<=self.rows[-1]['capture_ns']:return
            row=dict(capture_ns=int(capture_ns),valid=bool(data.get('valid')),status=data.get('status'),
                     raw=data.get('raw'),head_degrees=data.get('head_degrees'),origin_m=data.get('origin_m'),
                     corrected=data.get('display',{}).get('unfiltered'))
            # Copy only short numeric arrays; never keep frame/image references.
            for k in ('raw','head_degrees','origin_m','corrected'):
                if row[k] is not None:row[k]=tuple(row[k])
            state=raw_fixation_state(self.detector,row,self.geometry)
            self.rows.append(row)
            if state is not None:self.states[int(capture_ns)]=state
            cutoff=capture_ns-int((self.o.entry_window_ms+500)*1e6)
            while self.rows and self.rows[0]['capture_ns']<cutoff:self.rows.popleft()
            live={r['capture_ns'] for r in self.rows};self.states={t:s for t,s in self.states.items() if t in live}
    def clear(self):
        with self.lock:self.rows.clear();self.states.clear();self.detector.reset()

    def apply(self,event_ns,target):
        with self.lock:
            report=dict(applied=0,offset_cm=[0.,0.],samples=0,reason=REASONS['missing_clock'])
            if not self.geometry.measured or self.geometry.width_cm is None or self.geometry.height_cm is None:
                report['reason']=REASONS['unmeasured_screen']
            elif event_ns is not None:
                try:
                    scale=cm_scale(self.geometry)
                    result=sample_clicks(list(self.rows),[dict(event_ns=int(event_ns),x=target[0],y=target[1])],self.geometry,self.settings,(self.o.entry_window_ms,0),fixation_states=self.states)
                    report['samples']=sum(r['valid'] and r['raw'] is not None for r in self.rows if event_ns-self.o.entry_window_ms*1e6<=r['capture_ns']<=event_ns)
                    if not result['samples']:
                        reason=result['audit'][0]['reason'];report['reason']=REASONS['insufficient_frames'] if reason.startswith('insufficient') else REASONS['invalid_sample']
                    else:
                        sample=result['samples'][0]
                        rows=[r for r in self.rows if sample['first_capture_ns']<=r['capture_ns']<=sample['last_capture_ns'] and r['valid'] and r['raw'] is not None]
                        corrected=np.asarray([r['corrected'] for r in rows],dtype=float)
                        if corrected.shape!=(len(rows),2) or not np.isfinite(corrected).all():report['reason']=REASONS['missing_corrected_position']
                        else:
                            delta=np.asarray(target)-np.median(corrected,axis=0);cm=delta*scale
                            report['offset_cm']=cm.tolist()
                            if np.linalg.norm(cm)>self.o.entry_max_correction_cm:report['reason']=REASONS['excessive_offset']
                            else:self.offset=tuple(float(v) for v in delta);report.update(applied=1,reason=REASONS['accepted'])
                except (ValueError,TypeError,OverflowError):report['reason']=REASONS['invalid_sample']
            report['used_offset_cm']=[float(v*s) for v,s in zip(self.offset,cm_scale(self.geometry))] if self.geometry.measured and self.geometry.width_cm and self.geometry.height_cm else [0.,0.]
            self.reports.append(report);self.reports=self.reports[-20:]
            return report
