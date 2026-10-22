"""Python evaluation-period heatmaps. Targets are validation masks ONLY.

Matrix lookup never reads today's profile. New files use exact frame revisions;
legacy files explicitly fall back to the event_ns/server_ns timeline.
"""
from collections import Counter
import hashlib
from pathlib import Path
import numpy as np

from .config import ScreenGeometry
from .display import calibration_matrix
from .filters import zero_phase_smooth
from .fixation import FixationDetector
from .heatmap import HeatmapAccumulator,kernel_scale,NOTICE
from tools.replay import read_session


class CalibrationTimeline:
    def __init__(self,records):
        self.events=[r for r in records if r['type']=='calibration']
        self.versioned={}
        for row in self.events:
            if 'matrix_revision' in row:self.versioned.setdefault(row['matrix_revision'],[]).append(row)

    def resolve(self,frame):
        if 'matrix_revision' in frame:
            events=self.versioned.get(frame['matrix_revision'],[])
            if not events:return None,'missing_matrix_revision'
            if len({repr((r.get('matrix'),r.get('action'),r.get('coordinate_system'))) for r in events})>1:
                return None,'conflicting_matrix_revision'
            prior=[r for r in events if r.get('event_ns',r['server_ns'])<=frame['capture_ns']]
            event=(prior or events)[-1] if prior else events[0]
            source='exact_matrix_revision'
        else:
            prior=[r for r in self.events if r.get('event_ns',r['server_ns'])<=frame['capture_ns']]
            if not prior:return None,'missing_calibration_event'
            event=prior[-1];source='legacy_event_time'
        if event.get('action')!='applied' or event.get('group')!='B' or event.get('matrix') is None:return None,'uncalibrated'
        if event.get('pipeline') not in (None,'python'):return None,'web_calibration'
        if event.get('calibration_matched') is False:return None,'unmatched_calibration'
        if event.get('coordinate_system')!='full_screen_normalized':return None,'unknown_calibration_coordinates'
        return event,source


def _checks(records,task_id):
    summaries={r['task_id']:r for r in records if r['type']=='evaluation_summary' and r.get('completed') is True}
    checks=[]
    for ident,summary in summaries.items():
        if task_id is not None and ident!=task_id:continue
        events=[r for r in records if r['type']=='target' and r.get('task_type')=='evaluate' and r['task_id']==ident]
        positions=[r for r in events if r['action'] in ('appear','switch')]
        ends=[r for r in events if r['action']=='end' and r.get('reason')=='completed']
        if not positions or not ends:continue
        end=ends[-1].get('event_ns',ends[-1]['server_ns'])
        targets=[]
        for i,row in enumerate(positions):
            start=row.get('event_ns',row['server_ns'])
            stop=positions[i+1].get('event_ns',positions[i+1]['server_ns']) if i+1<len(positions) else end
            targets.append(dict(task_id=ident,target_id=row.get('target_id',str(row['index'])),target=row['target'],start_ns=start,end_ns=stop))
        checks.append(dict(task_id=ident,start_ns=targets[0]['start_ns'],end_ns=end,targets=targets,summary=summary))
    checks.sort(key=lambda r:r['start_ns'])
    if any(a['end_ns']>b['start_ns'] for a,b in zip(checks,checks[1:])):raise ValueError('核对时段重叠，无法生成可比热力图')
    if not checks:raise ValueError('没有完整的 Python 精度核对时段；请先在悬浮层完成一次精度核对并录制')
    return checks


def session_heatmap(path,settings,*,task_id=None):
    session=read_session(path);header=session.metadata
    if header['source']!='python':raise ValueError('M6 会话热力图只接受 Python capture_ns 录制')
    screen=header['screen'];screen_size=(screen['width_px'],screen['height_px'])
    geometry=ScreenGeometry(width_cm=screen.get('width_cm'),height_cm=screen.get('height_cm'),measured=screen.get('measured') is True,
                            horizontal_cm=screen.get('horizontal_cm',0.),top_cm=screen.get('top_cm',0.),focal_scale=1.)
    checks=_checks(session.records,task_id);timeline=CalibrationTimeline(session.records)
    start_ns=checks[0]['start_ns'];acc=HeatmapAccumulator(settings.heatmap,screen_size,start_s=0.)
    skipped=Counter();alignment=Counter();smoothing=Counter();fallback=0
    targets=[dict(t,near_heat_s=0.,during_heat_s=0.,kernel=None) for check in checks for t in check['targets']]

    def accumulate(segment,samples,scale,phase):
        points=[r for r in samples if segment['start_s']<=r['t']<=segment['end_s']]
        if not points:return
        result=zero_phase_smooth([r['point'] for r in points],[r['t'] for r in points],settings.offline_filter)
        smoothing.update(s['status'] for s in result['segments'])
        segment=dict(segment,center=np.mean(result['positions'],axis=0).tolist())
        acc.add(segment,scale,phase=phase)
        kernel=acc.kernel(segment['center'],scale)
        if kernel is None:return
        for target in targets:
            a=(target['start_ns']-start_ns)/1e9;b=(target['end_ns']-start_ns)/1e9
            duration=max(0.,min(b,segment['end_s'])-max(a,segment['start_s']))
            if duration<=0:continue
            target['kernel']=scale
            mask=(((acc.x-target['target'][0])/scale['sigma_x'])**2+((acc.y-target['target'][1])/scale['sigma_y'])**2)<=settings.heatmap.target_neighborhood_sigma**2
            target['near_heat_s']+=duration*float(kernel[mask].sum());target['during_heat_s']+=duration

    for check in checks:
        detector=FixationDetector(settings.fixation);samples=[];context=None;scale=None;phase=None;last=None
        def finish():
            for segment in detector.reset():accumulate(segment,samples,scale,phase)
            samples.clear();acc.regions.interrupt()
        frames=[r for r in session.records if r['type']=='frame' and check['start_ns']<=r['capture_ns']<check['end_ns']]
        for row in frames:
            event,source=timeline.resolve(row)
            if event is None:
                finish();context=None;skipped[source]+=1;continue
            if event.get('conditions_fingerprint') not in (None,header.get('conditions_fingerprint')):
                finish();context=None;skipped['conditions_mismatch']+=1;continue
            key=(event.get('matrix_revision'),repr(event['matrix']),event.get('error_radius_cm'),event.get('error_radius_source'))
            if key!=context:
                finish();context=key
                radius=event.get('error_radius_cm') or settings.overlay.radius_cm
                scale=kernel_scale(geometry,screen_size,radius,settings.overlay.fallback_radius_px,event.get('error_radius_source','configured_cm'))
                phase=dict(calibration_id=event['calibration_id'],matrix_revision=event.get('matrix_revision'),matrix=event['matrix'],
                           conditions_fingerprint=event.get('conditions_fingerprint'),group='B',alignment=source)
            alignment[source]+=1
            t=(row['capture_ns']-start_ns)/1e9
            if last is not None and (t<=last or (t-last)*1000>settings.fixation.max_gap_ms):
                finish()
                if t<=last:skipped['nonincreasing_capture_ns']+=1;continue
            last=t
            if row.get('valid') is not True or row.get('status')!='tracking' or row.get('raw') is None:
                finish();skipped[row.get('status','invalid')]+=1;continue
            matrix=calibration_matrix(event['matrix']);point=np.append(row['raw'],1)@matrix
            detection=detector.update(point,t,origin_m=row.get('origin_m'),geometry=geometry)
            for segment in detection['finished_fixations']:accumulate(segment,samples,scale,phase)
            if detection['state']=='saccade':samples.clear();skipped['saccade']+=1
            else:samples.append(dict(t=t,point=point.tolist()))
            fallback+=int(detection['velocity_basis']!='angular')
        finish()
    result=acc.snapshot((checks[-1]['end_ns']-start_ns)/1e9)
    total=float(result['grid'].sum());target_rows=[]
    default_scale=kernel_scale(geometry,screen_size,settings.overlay.radius_cm,settings.overlay.fallback_radius_px,'configured_cm')
    for target in targets:
        scale=target['kernel'] or default_scale
        mask=(((acc.x-target['target'][0])/scale['sigma_x'])**2+((acc.y-target['target'][1])/scale['sigma_y'])**2)<=settings.heatmap.target_neighborhood_sigma**2
        row={k:v for k,v in target.items() if k not in ('kernel','near_heat_s','during_heat_s')}
        row.update(neighborhood_sigma=settings.heatmap.target_neighborhood_sigma,kernel=scale,
                   whole_heat_fraction=float(result['grid'][mask].sum())/total if total else None,
                   during_target_heat_fraction=target['near_heat_s']/target['during_heat_s'] if target['during_heat_s'] else None,
                   during_target_fixation_s=target['during_heat_s'])
        target_rows.append(row)
    result.update(source='python_recording_checks_only',input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                  session_id=header['session_id'],screen=screen,targets=target_rows,skipped=dict(skipped),alignment=dict(alignment),
                  smoothing=dict(smoothing),fallback_frames=fallback,warnings=list(session.warnings),
                  completed_checks=[r['task_id'] for r in checks],notice=NOTICE,
                  target_note='目标只用于热量占比验收，不参与拟合、滤波或注视检测。附近圆/椭圆可能重叠，各目标占比不能相加。')
    return result
