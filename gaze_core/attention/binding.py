"""Capture-clock fixation extraction and conservative interval joins."""
import numpy as np
from gaze_core.fixation import FixationDetector
from gaze_core.display import calibration_matrix


def frame_record(prediction):
    from gaze_core.recorder import number
    data=prediction.data
    row=dict(type='frame',capture_ns=int(prediction.captured_ns),completed_ns=int(prediction.completed_ns),
             valid=bool(data.get('valid')),status=str(data.get('status','invalid')))
    for key in ('raw','origin_m'):
        value=data.get(key);row[key]=None if value is None else [number(v,key) for v in value]
    return row


def extract_fixations(frames,fixation_settings,geometry,matrix,start_ns):
    detector=FixationDetector(fixation_settings);matrix=calibration_matrix(matrix)
    if matrix is None:raise ValueError('注意力会话需要九点矩阵')
    previous=None;last_state=None;durations={}
    def convert(segment):
        return dict(start_ns=start_ns+round(segment['start_s']*1e9),end_ns=start_ns+round(segment['end_s']*1e9),
                    center=segment['center'],sample_count=segment['sample_count'],velocity_basis=segment['velocity_basis'])
    for row in frames:
        now=int(row['capture_ns'])
        if previous is not None and now<=previous:raise ValueError('视线capture_ns必须严格递增')
        gap=previous is not None and (now-previous)/1e6>fixation_settings.max_gap_ms
        if previous is not None:
            key='capture_gap' if gap else last_state
            durations[key]=durations.get(key,0.)+(now-previous)/1e9
        previous=now
        if not row.get('valid') or row.get('status')!='tracking' or row.get('raw') is None:
            last_state=row.get('status','invalid')
            for segment in detector.reset():yield 'fixation',convert(segment)
            continue
        point=np.append(np.asarray(row['raw'],float),1)@matrix
        if not np.isfinite(point).all() or np.any(point<0) or np.any(point>1):
            last_state='outside'
            for segment in detector.reset():yield 'fixation',convert(segment)
            continue
        result=detector.update(point,(now-start_ns)/1e9,origin_m=row.get('origin_m'),geometry=geometry)
        last_state=result['state']
        for segment in result['finished_fixations']:yield 'fixation',convert(segment)
    for segment in detector.reset():yield 'fixation',convert(segment)
    yield 'states',durations


def merge_intervals(intervals):
    result=[]
    for a,b in sorted(intervals):
        if b<=a:continue
        if result and a<=result[-1][1]:result[-1][1]=max(b,result[-1][1])
        else:result.append([a,b])
    return result


class Timeline:
    def __init__(self,events,start_ns,end_ns):
        self.start_ns,self.end_ns=start_ns,end_ns
        self.scenes=sorted((e for e in events if e['type']=='scene'),key=lambda e:e['start_ns'])
        self.states=sorted((e for e in events if e['type']=='state'),key=lambda e:e['time_ns'])
        self.uncertain=merge_intervals([e['transition'] for e in self.scenes if e.get('transition')])
        self.intervals=[];self.breaks={start_ns,end_ns}
        for i,scene in enumerate(self.scenes):
            following=self.scenes[i+1] if i+1<len(self.scenes) else None
            end=(following.get('transition') or [following['start_ns']])[0] if following else end_ns
            pauses=[e['time_ns'] for e in self.states if e['status']!='recording' and e['time_ns']>=scene['start_ns']]
            if pauses:end=min(end,min(pauses))
            self.intervals.append((scene['start_ns'],end,scene['scene_id']))
            self.breaks.update((scene['start_ns'],end))
        for a,b in self.uncertain:self.breaks.update((a,b))
        self.breaks.update(e['time_ns'] for e in self.states)

    def at(self,time_ns):
        states=[e for e in self.states if e['time_ns']<=time_ns]
        if states and states[-1]['status']!='recording':return None,states[-1]['status']
        if any(a<=time_ns<b for a,b in self.uncertain):return None,'transition_uncertain'
        for a,b,name in self.intervals:
            if a<=time_ns<b:return name,None
        return None,'unobserved_scene'

    def split(self,segment):
        a,b=segment['start_ns'],segment['end_ns'];points=sorted({a,b}|{v for v in self.breaks if a<v<b})
        for left,right in zip(points,points[1:]):
            if right<=left:continue
            name,reason=self.at((left+right)//2)
            yield dict(segment,start_ns=left,end_ns=right,scene_id=name,excluded_reason=reason)

    def summary(self):
        span=max(0,self.end_ns-self.start_ns);uncertain=sum(max(0,min(b,self.end_ns)-max(a,self.start_ns)) for a,b in self.uncertain)
        return dict(duration_s=span/1e9,uncertain_s=uncertain/1e9,uncertain_fraction=uncertain/span if span else None)
