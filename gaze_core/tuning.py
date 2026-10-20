"""Numeric jump-task replay, common-window measurements and parameter selection.

Targets are command labels, not eye-tracker truth. Detection miss/false figures
are task proxies with explicit denominators. No camera/model access here.
"""
from dataclasses import dataclass, field, replace
import hashlib
import itertools
from pathlib import Path
import numpy as np
import yaml
from .config import ROOT, FilterSettings, FixationSettings, ScreenGeometry
from .devices import finite
from .display import DisplayPipeline, calibration_matrix
from .evaluation import ray_errors
from tools.replay import read_session


@dataclass(frozen=True)
class TuningSettings:
    min_cutoff: tuple = (.5,1.,2.)
    beta: tuple = (0.,.05,.2)
    d_cutoff: tuple = (1.,2.)
    angular_threshold_deg_s: tuple = (45.,90.,180.)
    stable_settle_ms: float = 500.
    min_stable_frames: int = 5
    response_window_ms: float = 600.
    crossing_hold_ms: float = 0.
    min_signal_jump_snr: float = 3.
    min_valid_coverage: float = .8
    min_angle_coverage: float = .8
    min_fixation_coverage: float = .5
    min_transition_coverage: float = .8
    max_missed_rate: float = .3
    max_false_detection_share: float = .3
    min_tasks: int = 3
    expected_points: int = 20
    require_personal_calibration: bool = True

    def __post_init__(self):
        for key in ('min_cutoff','beta','d_cutoff','angular_threshold_deg_s'):
            values=getattr(self,key)
            if not isinstance(values,(list,tuple)) or not values:raise ValueError(key+' 搜索数组不能为空')
            for value in values:
                if finite(value,key,positive=key!='beta')<0:raise ValueError('beta 必须非负')
        for key in ('stable_settle_ms','response_window_ms','min_signal_jump_snr'):
            finite(getattr(self,key),key,positive=True)
        if finite(self.crossing_hold_ms,'crossing_hold_ms')<0:raise ValueError('crossing_hold_ms 必须非负')
        for key in ('min_stable_frames','min_tasks','expected_points'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError(key+' 必须为正整数')
        for key in ('min_valid_coverage','min_angle_coverage','min_fixation_coverage','min_transition_coverage','max_missed_rate','max_false_detection_share'):
            if not 0<=finite(getattr(self,key),key)<=1:raise ValueError(key+' 必须在 [0,1]')
        if type(self.require_personal_calibration) is not bool:raise ValueError('require_personal_calibration 必须是布尔值')


def load_tuning_settings(path=ROOT/'config/filter_tuning.yaml'):
    data=yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('调参配置必须为映射')
    return TuningSettings(**data)


@dataclass
class JumpTask:
    file: str
    digest: str
    task_id: str
    geometry: ScreenGeometry
    targets: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    end_s: float | None = None
    completed: bool = False
    reasons: list = field(default_factory=list)
    group: str | None = None
    device_file: str | None = None
    condition: tuple = ()


def read_jump_tasks(path):
    session=read_session(path)
    header=session.metadata;screen=header.get('screen',{})
    if header['source']!='web':raise ValueError('当前跳点任务需要网页 web 录制及 browser_capture_ms')
    geometry=ScreenGeometry(width_cm=screen.get('width_cm'),height_cm=screen.get('height_cm'),
                            measured=screen.get('measured') is True,top_cm=screen.get('top_cm',0.),
                            horizontal_cm=screen.get('horizontal_cm',0.),focal_scale=1.)
    # Never borrow physical measurements or a new profile from this machine.
    tasks=[];active=None;matrix=None;known_matrix=True
    digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for row in session.records:
        kind=row['type']
        if kind=='calibration':
            old=None if matrix is None else matrix.tolist()
            matrix=calibration_matrix(row.get('matrix'))
            known_matrix=matrix is None or row.get('coordinate_system')=='full_screen_normalized'
            if active is not None and (old!=(None if matrix is None else matrix.tolist())):
                active.reasons.append('calibration_changed_during_task')
        elif kind=='target' and row.get('task_type')=='jump':
            t=finite(row.get('browser_time_ms'),'target.browser_time_ms')/1000
            if row['action']=='appear':
                if active is not None:active.reasons.append('overlapping_jump_tasks')
                camera=header.get('camera',{})
                condition=(camera.get('device_file'),camera.get('intrinsics_source'),camera.get('undistort_applied'),
                           camera.get('width_px'),camera.get('height_px'),header.get('grayscale'),
                           geometry.width_cm,geometry.height_cm,geometry.top_cm,geometry.horizontal_cm,
                           screen.get('width_px'),screen.get('height_px'))
                active=JumpTask(Path(path).name,digest,row['task_id'],geometry,device_file=camera.get('device_file'),condition=condition)
                if not session.ended_normally:active.reasons.append('session_missing_end')
                if not geometry.measured or geometry.width_cm is None or geometry.height_cm is None:
                    active.reasons.append('unmeasured_screen')
                if screen.get('fullscreen') is not True:active.reasons.append('fullscreen_not_confirmed_in_session')
                if not known_matrix:active.reasons.append('unknown_calibration_coordinates')
                active.group='B' if matrix is not None else 'A'
                tasks.append(active)
            if active is None or active.task_id!=row['task_id']:continue
            if row['action']=='end':
                active.end_s=t;active.completed=row.get('reason')=='completed';active=None
            else:
                target=[finite(v,'target') for v in row['target']]
                if len(target)!=2:raise ValueError('跳点坐标必须是二维')
                active.targets.append(dict(time_s=t,point=target,index=row['index']))
        elif kind=='frame' and active is not None:
            t=finite(row.get('browser_capture_ms'),'frame.browser_capture_ms')/1000
            if not known_matrix and 'unknown_calibration_coordinates' not in active.reasons:
                active.reasons.append('unknown_calibration_coordinates')
            active.frames.append(dict(time_s=t,data=row,matrix=None if matrix is None else matrix.tolist()))
    for task in tasks:
        if not task.completed:task.reasons.append('task_not_completed')
        if not task.frames:task.reasons.append('no_task_frames')
        times=[r['time_s'] for r in task.frames]
        if any(b<=a for a,b in zip(times,times[1:])):task.reasons.append('nonincreasing_capture_timestamp')
    return tasks


def simulate(task, filter_settings, detector_settings):
    pipe=DisplayPipeline(filter_settings,detector_settings)
    samples=[];segments=[];events=[];previous=None
    for frame in task.frames:
        output=pipe.process(frame['data'],source='web',geometry=task.geometry,matrix=frame['matrix'],
                            browser_capture_ms=frame['data']['browser_capture_ms'])
        row=dict(time_s=frame['time_s'],unfiltered=output.get('unfiltered'),position=output.get('position'),
                 state=output['state'],origin_m=frame['data'].get('origin_m'),
                 fallback_reason=output.get('fallback_reason'),velocity_basis=output.get('velocity_basis'))
        samples.append(row);segments.extend(output['finished_fixations'])
        if output['state']=='saccade' and previous!='saccade':events.append(row['time_s'])
        previous=output['state']
    segments.extend(pipe.reset())
    return samples,segments,events


def jitter(samples, key, geometry):
    """Population SD per axis; combined = sqrt(SDx²+SDy²). Degrees use ray yaw/pitch."""
    points=np.array([r[key] for r in samples],dtype=float)
    if not len(points):return None
    cm=points*np.array([geometry.width_cm,geometry.height_cm])
    std=np.std(cm,axis=0,ddof=0)
    center=points.mean(0);angles=[]
    for row in samples:
        if row['origin_m'] is None:continue
        try:angle=ray_errors(row['origin_m'],row[key],center,geometry)
        except (ValueError,TypeError):continue
        angles.append([angle['yaw_deg'],angle['pitch_deg']])
    angular=np.std(angles,axis=0,ddof=0) if angles else None
    return dict(cm=float(np.linalg.norm(std)),x_cm=float(std[0]),y_cm=float(std[1]),
                deg=float(np.linalg.norm(angular)) if angular is not None else None,
                yaw_deg=float(angular[0]) if angular is not None else None,
                pitch_deg=float(angular[1]) if angular is not None else None,angle_frames=len(angles),frames=len(points))


def crossing_time(samples, key, before, after, geometry, fraction, hold_ms):
    scale=np.array([geometry.width_cm,geometry.height_cm])
    origin=np.array(before)*scale;direction=(np.array(after)-before)*scale
    square=float(direction@direction)
    if square<=0:return None
    progress=[float((np.array(r[key])*scale-origin)@direction/square) for r in samples]
    for index,value in enumerate(progress):
        if value<fraction:continue
        if hold_ms==0:return samples[index]['time_s']
        for end in range(index,len(samples)):
            if progress[end]<fraction:break
            if (samples[end]['time_s']-samples[index]['time_s'])*1000>=hold_ms:return samples[index]['time_s']
    return None


def match_saccades(switch_times, detections, response_window_s, switch_ends=None):
    """One-to-one command matching; false_share is FP/(TP+FP), not clinical FPR."""
    switches=list(switch_times);used=set();considered=[]
    for t in detections:
        if not switches or t<switches[0]:continue  # Initial target has no known preceding gaze.
        considered.append(t)
        candidates=[i for i,start in enumerate(switches) if i not in used and start<=t<=min(start+response_window_s,switch_ends[i] if switch_ends is not None else switches[i+1] if i+1<len(switches) else float('inf'))]
        if candidates:used.add(candidates[-1])
    missed=len(switches)-len(used);false=len(considered)-len(used)
    return dict(expected_switches=len(switches),detected_saccades=len(considered),matched=len(used),
                missed=missed,false_detections=false,missed_rate=missed/len(switches) if switches else None,
                false_detection_share=false/len(considered) if considered else None)


def _mean(values):
    values=[v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def measure_task(task, samples, segments, detections, config, detector_settings):
    targets=task.targets;stable=[];stable_valid=stable_total=angle_count=fixation_frames=0
    references={}
    invalid_origin=missing_origin=0
    for i,target in enumerate(targets):
        end=targets[i+1]['time_s'] if i+1<len(targets) else task.end_s
        start=target['time_s']+config.stable_settle_ms/1000
        window=[r for r in samples if start<=r['time_s']<end]
        valid=[r for r in window if r['position'] is not None and r['unfiltered'] is not None]
        # Endpoints/noise come ONLY from the corrected unfiltered signal, never
        # from a candidate's filtered positions or its fixation classification.
        raw_valid=[r for r in window if r['unfiltered'] is not None]
        timeline=[start,*[r['time_s'] for r in raw_valid],end]
        if (len(raw_valid)>=config.min_stable_frames and window
                and len(raw_valid)/len(window)>=config.min_valid_coverage
                and max(np.diff(timeline),default=0)*1000<=detector_settings.max_gap_ms):
            references[i]=dict(position=np.median([r['unfiltered'] for r in raw_valid],axis=0).tolist(),
                               noise_cm=jitter(raw_valid,'unfiltered',task.geometry)['cm'])
        stable_total+=len(window);stable_valid+=len(valid)
        fixation_frames+=sum(r['state']=='fixation' for r in valid)
        missing_origin+=sum(r['origin_m'] is None for r in valid)
        invalid_origin+=sum(r['fallback_reason']=='invalid_origin' for r in valid)
        if len(valid)>=config.min_stable_frames:
            raw=jitter(valid,'unfiltered',task.geometry);filtered=jitter(valid,'position',task.geometry)
            angle_count+=filtered['angle_frames']
            stable.append(dict(index=target['index'],unfiltered=raw,filtered=filtered))
    segment_values=[]
    for segment in segments:
        valid=[r for r in samples if segment['start_s']<=r['time_s']<=segment['end_s'] and r['position'] is not None]
        if len(valid)>=config.min_stable_frames:segment_values.append(jitter(valid,'position',task.geometry))
    paired=[];legacy=[];signal_transitions=[]
    raw_unreached=filtered_unreached=invalid_transition=small_jumps=missing_stable=0
    legacy_raw_unreached=legacy_filtered_unreached=0;switches=[];ranges=[]
    for i in range(1,len(targets)):
        previous,current=targets[i-1],targets[i]
        start=current['time_s'];end=targets[i+1]['time_s'] if i+1<len(targets) else task.end_s
        window=[r for r in samples if start<=r['time_s']<end]
        valid=[r for r in window if r['position'] is not None and r['unfiltered'] is not None]
        # Quality is based on raw validity/time coverage, shared across candidates.
        timeline=[start,*[r['time_s'] for r in valid],end]
        if not window or len(valid)/len(window)<config.min_valid_coverage or max(np.diff(timeline),default=0)*1000>detector_settings.max_gap_ms:
            invalid_transition+=1
            signal_transitions.append(dict(index=current['index'],status='invalid_frames'))
            continue
        switches.append(start);ranges.append((start,end))
        # Retain the former target-based definition unchanged as auxiliary data.
        raw=crossing_time(valid,'unfiltered',previous['point'],current['point'],task.geometry,.9,config.crossing_hold_ms)
        filtered=crossing_time(valid,'position',previous['point'],current['point'],task.geometry,.9,config.crossing_hold_ms)
        legacy_raw_unreached+=raw is None;legacy_filtered_unreached+=filtered is None
        if raw is not None and filtered is not None:
            legacy.append(dict(index=current['index'],raw_ms=(raw-start)*1000,filtered_ms=(filtered-start)*1000,extra_ms=(filtered-raw)*1000))
        if i-1 not in references or i not in references:
            missing_stable+=1
            signal_transitions.append(dict(index=current['index'],status='missing_stable_reference'))
            continue
        before,after=references[i-1],references[i]
        distance=float(np.linalg.norm((np.array(after['position'])-before['position'])*
                                     [task.geometry.width_cm,task.geometry.height_cm]))
        noise=float(np.sqrt((before['noise_cm']**2+after['noise_cm']**2)/2))
        threshold=config.min_signal_jump_snr*noise
        detail=dict(index=current['index'],before=before['position'],after=after['position'],
                    distance_cm=distance,noise_cm=noise,min_distance_cm=threshold)
        if distance<=0 or distance<threshold:
            small_jumps+=1
            signal_transitions.append(dict(**detail,status='small_signal_jump'))
            continue
        raw=crossing_time(valid,'unfiltered',before['position'],after['position'],task.geometry,.9,config.crossing_hold_ms)
        filtered=crossing_time(valid,'position',before['position'],after['position'],task.geometry,.9,config.crossing_hold_ms)
        raw_unreached+=raw is None;filtered_unreached+=filtered is None
        signal_transitions.append(dict(**detail,status='paired' if raw is not None and filtered is not None else 'unreached',
                                       raw_ms=None if raw is None else (raw-start)*1000,
                                       filtered_ms=None if filtered is None else (filtered-start)*1000))
        if raw is not None and filtered is not None:
            paired.append(dict(index=current['index'],raw_ms=(raw-start)*1000,filtered_ms=(filtered-start)*1000,extra_ms=(filtered-raw)*1000))
    matching=match_saccades(switches,[t for t in detections if any(a<=t<b for a,b in ranges)],config.response_window_ms/1000,[b for _,b in ranges])
    return dict(file=task.file,task_id=task.task_id,group=task.group,stable_points=len(stable),expected_points=len(targets),
                stable_valid_frames=stable_valid,stable_total_frames=stable_total,angle_frames=angle_count,
                missing_origin_frames=missing_origin,invalid_origin_frames=invalid_origin,
                valid_coverage=stable_valid/stable_total if stable_total else 0,
                angle_coverage=angle_count/stable_valid if stable_valid else 0,
                fixation_coverage=fixation_frames/stable_valid if stable_valid else 0,
                jitter_cm=_mean([r['filtered']['cm'] for r in stable]),
                jitter_deg=_mean([r['filtered']['deg'] for r in stable]),
                raw_jitter_cm=_mean([r['unfiltered']['cm'] for r in stable]),
                raw_jitter_deg=_mean([r['unfiltered']['deg'] for r in stable]),
                fixation_jitter_cm=_mean([r['cm'] for r in segment_values]),
                fixation_jitter_deg=_mean([r['deg'] for r in segment_values]),fixation_segments=len(segments),
                extra_delay_ms=_mean([r['extra_ms'] for r in paired]),
                raw_delay_ms=_mean([r['raw_ms'] for r in paired]),filtered_delay_ms=_mean([r['filtered_ms'] for r in paired]),
                paired_transitions=len(paired),expected_transitions=max(0,len(targets)-1),
                delay_transitions=max(0,len(targets)-1-small_jumps),small_signal_jumps=small_jumps,
                missing_stable_transitions=missing_stable,
                raw_unreached=int(raw_unreached),filtered_unreached=int(filtered_unreached),invalid_transitions=invalid_transition,
                transition_coverage=len(paired)/max(1,len(targets)-1-small_jumps),
                legacy_target_extra_delay_ms=_mean([r['extra_ms'] for r in legacy]),
                legacy_target_raw_delay_ms=_mean([r['raw_ms'] for r in legacy]),
                legacy_target_filtered_delay_ms=_mean([r['filtered_ms'] for r in legacy]),
                legacy_target_paired_transitions=len(legacy),legacy_target_raw_unreached=int(legacy_raw_unreached),
                legacy_target_filtered_unreached=int(legacy_filtered_unreached),
                legacy_target_transition_coverage=len(legacy)/max(1,len(targets)-1),
                **matching,points=stable,transitions=paired,signal_transitions=signal_transitions,
                legacy_target_transitions=legacy)


def pareto_front(candidates):
    valid=[i for i,c in enumerate(candidates) if c.get('extra_delay_ms') is not None and c.get('jitter_cm') is not None]
    return [i for i in valid if not any(
        candidates[j]['extra_delay_ms']<=candidates[i]['extra_delay_ms'] and candidates[j]['jitter_cm']<=candidates[i]['jitter_cm']
        and (candidates[j]['extra_delay_ms']<candidates[i]['extra_delay_ms'] or candidates[j]['jitter_cm']<candidates[i]['jitter_cm'])
        for j in valid if j!=i)]


def choose_knee(candidates,indices):
    if not indices:return None
    points=np.array([[candidates[i]['extra_delay_ms'],candidates[i]['jitter_cm']] for i in indices])
    span=np.ptp(points,axis=0);span[span==0]=1
    normalized=(points-points.min(0))/span
    # A useful knee bends toward the ideal below the chord between the extrema.
    gain=1-normalized.sum(axis=1)
    chosen=int(np.argmax(gain)) if len(indices)>2 and float(gain.max())>0 else int(np.argmin(np.linalg.norm(normalized,axis=1)))
    return indices[chosen]


def tune(tasks, config, base_detector=None):
    base_detector=base_detector or FixationSettings()
    usable=[];skipped=[];seen=set()
    for task in tasks:
        reasons=list(dict.fromkeys(task.reasons))
        identity=(task.digest,task.task_id)
        if identity in seen:reasons.append('duplicate_task')
        seen.add(identity)
        if config.require_personal_calibration and task.group!='B':reasons.append('personal_calibration_required')
        if len(task.targets)!=config.expected_points:reasons.append('unexpected_point_count')
        if reasons:skipped.append(dict(file=task.file,task_id=task.task_id,reasons=reasons))
        else:usable.append(task)
    conditions={task.condition for task in usable};groups={task.group for task in usable}
    homogeneous=len(conditions)<=1 and len(groups)<=1
    if not homogeneous:
        skipped.extend(dict(file=t.file,task_id=t.task_id,reasons=['mixed_device_or_group_conditions']) for t in usable)
        return dict(tasks=[],skipped=skipped,candidates=[],pareto=[],eligible_pareto=[],recommended=None,homogeneous=False)
    candidates=[]
    if usable:
        for cutoff,beta,d_cutoff,threshold in itertools.product(config.min_cutoff,config.beta,config.d_cutoff,config.angular_threshold_deg_s):
            filtering=FilterSettings(cutoff,beta,d_cutoff);detector=replace(base_detector,angular_threshold_deg_s=threshold)
            trials=[measure_task(task,*simulate(task,filtering,detector),config,detector) for task in usable]
            missed=sum(r['missed'] for r in trials);expected=sum(r['expected_switches'] for r in trials)
            false=sum(r['false_detections'] for r in trials);detected=sum(r['detected_saccades'] for r in trials)
            rejection=[]
            if len(usable)<config.min_tasks:rejection.append('insufficient_tasks')
            if not homogeneous:rejection.append('mixed_device_or_group_conditions')
            for trial in trials:
                for key,minimum in [('valid_coverage',config.min_valid_coverage),('angle_coverage',config.min_angle_coverage),('fixation_coverage',config.min_fixation_coverage),('transition_coverage',config.min_transition_coverage)]:
                    if trial[key]<minimum:rejection.append(key)
                if trial['stable_points']!=trial['expected_points']:rejection.append('missing_stable_points')
            miss_rate=missed/expected if expected else None
            false_share=false/detected if detected else None
            if miss_rate is None or miss_rate>config.max_missed_rate:rejection.append('missed_rate')
            if false_share is None or false_share>config.max_false_detection_share:rejection.append('false_detection_share')
            candidates.append(dict(params=dict(min_cutoff=cutoff,beta=beta,d_cutoff=d_cutoff,angular_threshold_deg_s=threshold),
                                   **{key:_mean([r[key] for r in trials]) for key in ('jitter_cm','jitter_deg','raw_jitter_cm','raw_jitter_deg','extra_delay_ms','raw_delay_ms','filtered_delay_ms','fixation_jitter_cm','fixation_jitter_deg','legacy_target_extra_delay_ms','legacy_target_raw_delay_ms','legacy_target_filtered_delay_ms')},
                                   missed=missed,expected_switches=expected,missed_rate=miss_rate,
                                   false_detections=false,detected_saccades=detected,false_detection_share=false_share,
                                   eligible=not rejection,rejection_reasons=sorted(set(rejection)),trials=trials))
    front=pareto_front(candidates)
    eligible_indices=[i for i,c in enumerate(candidates) if c['eligible']]
    eligible_front=[eligible_indices[i] for i in pareto_front([candidates[j] for j in eligible_indices])]
    recommended=choose_knee(candidates,eligible_front)
    return dict(tasks=usable,skipped=skipped,candidates=candidates,pareto=front,eligible_pareto=eligible_front,
                recommended=recommended,homogeneous=homogeneous)
