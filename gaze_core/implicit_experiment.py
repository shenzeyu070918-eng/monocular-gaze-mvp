"""Read-only M4 experiments, exact shared check frames, training-only selection."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
from pathlib import Path
import numpy as np

from .config import ScreenGeometry
from .evaluation import evaluate
from .implicit_calibration import (MODELS, M5_RULE_VERSION, M5_RULE_TEXT, ExperimentSettings,
    sample_clicks, blocked_cv, fit_robust, decide_m5, coverage)
from tools.replay import read_session


@dataclass
class ClickExperiment:
    file: str
    digest: str
    session_id: str
    experiment_id: str
    subject: str
    machine: str
    geometry: ScreenGeometry
    frames: list
    clicks: list
    check_frames: list
    targets: list
    baseline: dict
    collection: dict
    summary: dict
    warnings: list
    provenance: dict = field(default_factory=dict)
    check_start_ns: int | None = None


def _geometry(metadata):
    screen=metadata.get('screen',{})
    return ScreenGeometry(width_cm=screen.get('width_cm'),height_cm=screen.get('height_cm'),
                          top_cm=screen.get('top_cm',0.),horizontal_cm=screen.get('horizontal_cm',0.),
                          measured=screen.get('measured') is True,focal_scale=1.)


def read_experiments(path, *, subject='anonymous-1', machine='machine-1'):
    """Old recordings are replayable, but cannot silently become fair M4 trials."""
    path=Path(path);session=read_session(path);digest=hashlib.sha256(path.read_bytes()).hexdigest()
    rows=session.records;trials=[];skipped=[]
    if session.metadata['source']!='python':
        return [],[dict(file=path.name,reasons=['requires_python_capture_clock'])]
    identifiers=sorted({r['experiment_id'] for r in rows if r['type']=='click_session'})
    if not identifiers:
        return [],[dict(file=path.name,reasons=['missing_click_session_protocol'])]
    for identifier in identifiers:
        events=[r for r in rows if r['type']=='click_session' and r['experiment_id']==identifier]
        by_action={r['action']:r for r in events};reasons=[]
        if not session.ended_normally:reasons.append('session_missing_end')
        if session.warnings:reasons.append('recording_has_warnings')
        if 'completed' not in by_action or 'aborted' in by_action:reasons.append('click_session_not_completed')
        if any(action not in by_action for action in ('baseline','collecting','collection_ended')):
            reasons.append('missing_baseline_or_collection_bounds')
        if reasons:
            skipped.append(dict(file=path.name,experiment_id=identifier,reasons=reasons));continue
        baseline=by_action['baseline'];collection=by_action['collection_ended'];began=by_action['collecting'];completed=by_action['completed']
        matrix=baseline.get('baseline_matrix');task_id=completed.get('check_task_id')
        check_starts=[r for r in events if r['action']=='check_started' and r.get('check_task_id')==task_id]
        if not task_id or len(check_starts)!=1:reasons.append('missing_unique_check_start')
        array=np.asarray(matrix,dtype=float)
        if array.shape!=(3,2) or not np.isfinite(array).all():reasons.append('missing_baseline_matrix')
        if not baseline.get('baseline_created_at') or baseline.get('baseline_age_s') is None:
            reasons.append('missing_baseline_time')
        conditions=baseline.get('conditions_fingerprint')
        if not conditions or any(r.get('conditions_fingerprint') not in (None,conditions) for r in events):
            reasons.append('conditions_changed')
        matches=[r for r in rows if r['type']=='evaluation_summary' and r.get('task_id')==task_id and r.get('completed') is True]
        if not task_id or len(matches)!=1:reasons.append('missing_unique_completed_check')
        if reasons:
            skipped.append(dict(file=path.name,experiment_id=identifier,reasons=reasons));continue
        summary=matches[0]
        check_start_ns=check_starts[0]['event_ns']
        if summary.get('conditions_fingerprint') not in (None,conditions):reasons.append('check_conditions_changed')
        if summary.get('calibration_id')!=baseline.get('baseline_calibration_id'):reasons.append('baseline_changed_before_check')
        selection=summary.get('sample_selection');sampling=summary.get('sampling')
        if not selection or not sampling:reasons.append('missing_exact_check_sample_selection')
        start=collection.get('collection_start_ns',began.get('collection_start_ns',began['event_ns']))
        end=collection.get('collection_end_ns',collection['event_ns'])
        if not isinstance(start,int) or not isinstance(end,int) or start>=end:reasons.append('invalid_collection_bounds')
        elif not end<=check_start_ns<=summary['event_ns']<=completed['event_ns']:reasons.append('check_not_after_collection')
        lookup={};duplicate_ids=set()
        for row in rows:
            if row['type']=='frame':
                if row.get('frame_id') in lookup:duplicate_ids.add(row.get('frame_id'))
                lookup[row.get('frame_id')]=row
        targets=[dict(target_id=p['target_id'],region=p['region'],target=p['target'])
                 for p in summary.get('summary',{}).get('groups',{}).get('A',{}).get('points',[])]
        target_ids={t['target_id'] for t in targets};check=[];seen=set()
        for item in selection or []:
            frame_id=item.get('frame_id');target_id=item.get('target_id');row=lookup.get(frame_id)
            if row is None or frame_id in duplicate_ids or frame_id in seen or target_id not in target_ids:
                reasons.append('invalid_exact_check_sample_selection');break
            if row['capture_ns']<check_start_ns:reasons.append('check_sample_precedes_check');break
            check.append(dict(row,target_id=target_id));seen.add(frame_id)
        geometry=_geometry(session.metadata)
        if not geometry.measured or not geometry.width_cm or not geometry.height_cm:reasons.append('unmeasured_screen')
        if not targets or not check:reasons.append('empty_public_check')
        if reasons:
            skipped.append(dict(file=path.name,experiment_id=identifier,reasons=sorted(set(reasons))));continue
        frames=[r for r in rows if r['type']=='frame' and start<=r['capture_ns']<=end]
        clicks=[r for r in rows if r['type']=='click' and start<=r['event_ns']<=end]
        # A check target never appears in the training labels by construction.
        trials.append(ClickExperiment(path.name,digest,session.metadata['session_id'],identifier,subject,machine,
                    geometry,frames,clicks,check,targets,baseline,dict(collection,collection_start_ns=start,collection_end_ns=end),
                    summary,list(session.warnings),provenance={key:session.metadata.get(key) for key in
                      ('system','device_name','inference_device','screen','camera','grayscale')},check_start_ns=check_start_ns))
    return trials,skipped


def select_training(trial, settings):
    """No check_frames, check targets, baseline matrix or test scores used here."""
    samples_by_window=[];scan=[]
    for index,window in enumerate(settings.windows_ms):
        sampled=sample_clicks(trial.frames,trial.clicks,trial.geometry,settings,window)
        samples_by_window.append(sampled)
        for model in settings.models:
            cv=blocked_cv(sampled['samples'],model,trial.geometry,settings)
            scan.append(dict(window_index=index,window_ms=list(window),model=model,
                             accepted_clicks=sampled['accepted_count'],**cv))
    eligible=[r for r in scan if r['score_cm'] is not None]
    if not eligible:return dict(selection=None,window_scan=scan,recent_scan=[],samples_by_window=samples_by_window)
    # Stable tie break follows preregistered config ordering, not public accuracy.
    chosen=min(eligible,key=lambda r:r['score_cm']);samples=samples_by_window[chosen['window_index']]['samples']
    recent_scan=[];end=trial.check_start_ns
    if not isinstance(end,int):raise ValueError('最近 N 分钟需要绑定末尾核对开始的单调时间')
    for minutes in settings.recent_minutes:
        recent=[s for s in samples if s['event_ns']>=end-int(minutes*60e9)]
        cv=blocked_cv(recent,chosen['model'],trial.geometry,settings)
        recent_scan.append(dict(minutes=float(minutes),accepted_clicks=len(recent),**cv))
    available=[r for r in recent_scan if r['score_cm'] is not None]
    chosen_minutes=min(available,key=lambda r:r['score_cm'])['minutes'] if available else None
    return dict(selection=dict(window_index=chosen['window_index'],window_ms=chosen['window_ms'],model=chosen['model'],
                               score_cm=chosen['score_cm'],recent_minutes=chosen_minutes,
                               selection_basis='click_training_contiguous_time_block_cv_only'),
                window_scan=scan,recent_scan=recent_scan,samples_by_window=samples_by_window)


def _check(trial,matrix=None):
    result=evaluate(trial.check_frames,trial.targets,trial.geometry,matrix=matrix)
    return result['groups']['B' if matrix is not None else 'A']


def _summary_distribution(values, settings):
    """Intervals describe repeated subset sampling, not population confidence."""
    if not values:return dict(median=None,low=None,high=None,n=0)
    return dict(median=float(np.median(values)),low=float(np.percentile(values,settings.interval_percentiles[0])),
                high=float(np.percentile(values,settings.interval_percentiles[1])),n=len(values))


def _score_subset(trial,samples,indices,model,settings,seed):
    try:fit=fit_robust([samples[i] for i in indices],model,trial.geometry,settings,seed=seed)
    except ValueError as error:return dict(ok=False,reason=str(error),selected_indices=[int(i) for i in indices])
    summary=_check(trial,fit['matrix'])
    return dict(ok=True,selected_indices=[int(i) for i in indices],fit=fit,summary=summary)


def _repeat_subsets(trial,samples,count,settings,*,seed):
    if count>len(samples):return []
    rng=np.random.default_rng(seed);rows=[]
    for repeat in range(1 if count==len(samples) else settings.repeats):
        indices=sorted(rng.choice(len(samples),count,replace=False).tolist())
        # Each model sees the identical indices in this repeat.
        for model in settings.models:
            result=_score_subset(trial,samples,indices,model,settings,settings.seed+repeat)
            rows.append(dict(model=model,repeat=repeat,**result))
    return rows


def _distributions(rows,settings):
    return {model:{region:{metric:_summary_distribution(
        [r['summary'][region][metric] for r in rows if r['model']==model and r['ok'] and r['summary'][region][metric] is not None],settings)
        for metric in ('mean_cm','p90_cm','horizontal_cm','vertical_cm','yaw_deg','pitch_deg','horizontal_scale','vertical_scale')}
        for region in ('all','center','edge')} for model in settings.models}


def _clock_quality(clicks):
    sources=Counter(row.get('timestamp_source','unknown') for row in clicks)
    delays=[row['callback_delay_estimate_ns']/1e6 for row in clicks if row.get('callback_delay_estimate_ns') is not None]
    uncertainty=[row['clock_uncertainty_ns']/1e6 for row in clicks if row.get('clock_uncertainty_ns') is not None]
    return dict(source_counts=dict(sources),callback_fallback_count=sources['callback_received'],
                callback_fallback_fraction=sources['callback_received']/len(clicks) if clicks else None,
                missing_source_count=sources['unknown'],
                callback_delay_median_ms=float(np.median(delays)) if delays else None,
                callback_delay_p90_ms=float(np.percentile(delays,90)) if delays else None,
                uncertainty_max_ms=float(max(uncertainty)) if uncertainty else None,
                delay_measured_count=len(delays),delay_unknown_count=len(clicks)-len(delays))


def analyze_experiment(trial,settings):
    trained=select_training(trial,settings);chosen=trained['selection']
    baseline=_check(trial,trial.baseline['baseline_matrix']);unadjusted=_check(trial)
    panel=trial.summary.get('summary',{}).get('groups',{})
    comparable=(panel.get('A')==unadjusted and panel.get('B')==baseline)
    times=sorted(r['capture_ns'] for r in trial.frames)
    measured_fps=(len(times)-1)*1e9/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] else None
    recorded_fps=trial.collection.get('actual_inference_fps')
    base=dict(file=trial.file,sha256=trial.digest,session_id=trial.session_id,experiment_id=trial.experiment_id,
              subject=trial.subject,machine=trial.machine,conditions_fingerprint=trial.baseline.get('conditions_fingerprint'),
              provenance=trial.provenance,
              baseline_created_at=trial.baseline['baseline_created_at'],baseline_age_s=trial.baseline['baseline_age_s'],
              baseline_calibration_id=trial.baseline['baseline_calibration_id'],baseline_matrix=trial.baseline['baseline_matrix'],
              check_task_id=trial.summary['task_id'],check_frame_count=len(trial.check_frames),
              check_start_ns=trial.check_start_ns,
              check_frame_ids=[r['frame_id'] for r in trial.check_frames],sampling=trial.summary['sampling'],
              panel_recomputed_exactly=comparable,A=unadjusted,B=baseline,selection=chosen,
              window_scan=trained['window_scan'],recent_scan=trained['recent_scan'],
              collection=dict(duration_seconds=(trial.collection['collection_end_ns']-trial.collection['collection_start_ns'])/1e9,
                              wait_before_check_seconds=(trial.check_start_ns-trial.collection['collection_end_ns'])/1e9,
                              frame_count=len(times),valid_frame_count=sum(r.get('valid') is True for r in trial.frames),
                              actual_inference_fps=recorded_fps,capture_ns_fps=measured_fps,
                              fps_difference=(recorded_fps-measured_fps) if recorded_fps is not None and measured_fps is not None else None,
                              recorded_inference_frame_count=trial.collection.get('inference_frame_count'),
                              max_fps=trial.collection.get('max_fps'),
                              fps_definition='(N-1)/(last_capture_ns-first_capture_ns), includes invalid frames'),
              clock_quality=_clock_quality(trial.clicks),
              per_window_audit=[{k:v for k,v in row.items() if k!='samples'} for row in trained['samples_by_window']],
              sample_count=len(trial.clicks),count_results=[],recent_results=[],C_all=None,C_recent=None)
    if chosen is None:
        base['decision']=decide_m5(baseline['all']['mean_cm'],None,None)
        base['warnings']=trial.warnings+['训练点击不足或退化，没有从公共核对成绩选择模型或时间窗口。'];return base
    samples=trained['samples_by_window'][chosen['window_index']]['samples']
    for count in list(dict.fromkeys([*settings.counts,len(samples)])):
        rows=_repeat_subsets(trial,samples,count,settings,seed=settings.seed+count)
        base['count_results'].append(dict(count=count,label='all' if count==len(samples) else str(count),
                                          repetitions=rows,distributions=_distributions(rows,settings),
                                          unavailable_reason='可用点击数量不足' if count>len(samples) else None))
    end=trial.check_start_ns
    for minutes in settings.recent_minutes:
        recent=[s for s in samples if s['event_ns']>=end-int(minutes*60e9)]
        rows=_repeat_subsets(trial,recent,len(recent),settings,seed=settings.seed)
        base['recent_results'].append(dict(minutes=float(minutes),count=len(recent),repetitions=rows,
                                           distributions=_distributions(rows,settings)))
    all_result=_score_subset(trial,samples,list(range(len(samples))),chosen['model'],settings,settings.seed)
    base['C_all']=all_result
    all_audit=trained['samples_by_window'][chosen['window_index']]
    reasons=dict(all_audit['primary_reason_counts'])
    if all_result['ok']:
        count=all_result['fit']['outlier_count'];reasons['accepted']=reasons.get('accepted',0)-count
        if count:reasons['ransac_outlier']=count
        base['selected_rejection_audit']=dict(click_count=len(trial.clicks),primary_reason_counts=reasons,
             retained_count=all_result['fit']['inlier_count'],
             rejection_fraction=(len(trial.clicks)-all_result['fit']['inlier_count'])/len(trial.clicks) if trial.clicks else None,
             final_inlier_coverage=all_result['fit']['inlier_coverage'])
    else:
        base['selected_rejection_audit']=dict(click_count=len(trial.clicks),primary_reason_counts=reasons,
                                             fit_failure=all_result['reason'])
    minutes=chosen['recent_minutes'];recent_result=None
    if minutes is not None:
        recent=[s for s in samples if s['event_ns']>=end-int(minutes*60e9)]
        recent_result=_score_subset(trial,recent,list(range(len(recent))),chosen['model'],settings,settings.seed)
    base['C_recent']=recent_result
    all_mean=all_result['summary']['all']['mean_cm'] if all_result['ok'] else None
    recent_mean=recent_result['summary']['all']['mean_cm'] if recent_result and recent_result['ok'] else None
    # A partial public check does not support a preregistered overall conclusion.
    complete=baseline['all']['complete'] and unadjusted['all']['complete']
    base['decision']=decide_m5(baseline['all']['mean_cm'] if complete and comparable else None,all_mean,recent_mean)
    base['warnings']=trial.warnings+([] if complete else ['公共核对目标不完整，M5 判定为证据不足。'])+(
        [] if comparable else ['录制面板汇总与原始选帧重算不一致，保留结果但 M5 判定为证据不足。'])
    return base


def load_manifest(path):
    """Optional anonymous file-name labels; no current hostname/profile lookup."""
    if path is None:return {}
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or set(data)!={'recordings'} or not isinstance(data['recordings'],list):
        raise ValueError('manifest 格式为 {recordings:[{file,subject,machine}]}')
    result={}
    for row in data['recordings']:
        if not isinstance(row,dict) or set(row)!={'file','subject','machine'} or any(not isinstance(v,str) or not v for v in row.values()):
            raise ValueError('manifest 仅接受匿名 file / subject / machine 文本')
        if row['file'] in result:raise ValueError('manifest 文件条目重复')
        result[row['file']]=dict(subject=row['subject'],machine=row['machine'])
    return result


def analyze_recordings(paths,settings=None,*,manifest=None):
    settings=settings or ExperimentSettings();manifest=manifest or {};results=[];skipped=[];seen=set()
    for path in paths:
        labels=manifest.get(str(path),manifest.get(Path(path).name,dict(subject='anonymous-1',machine='machine-1')))
        trials,notices=read_experiments(path,**labels);skipped.extend(notices)
        for trial in trials:
            key=(trial.digest,trial.experiment_id)
            if key in seen:skipped.append(dict(file=trial.file,experiment_id=trial.experiment_id,reasons=['duplicate_input']));continue
            seen.add(key);results.append(analyze_experiment(trial,settings))
    return dict(rule_version=M5_RULE_VERSION,rule=M5_RULE_TEXT,parameters=asdict(settings),
                generated_utc=datetime.now(timezone.utc).isoformat(),
                sample_size=dict(subjects=len({r['subject'] for r in results}),machines=len({r['machine'] for r in results}),
                                 experiments=len(results)),experiments=results,skipped=skipped,
                limitation='单用户、单台 Mac 可以分析；人数、机器与条件如实列出，不推断多人或 Windows 表现。',
                selection_protocol='窗口、模型、recent N 只由点击训练的时间分块交叉验证确定；最近 N 从最终完成的核对开始时回溯，公共核对的帧与目标从不用于选择。',
                intervals='固定种子、无放回子集重复的预设分位区间；不是总体置信区间。全部/最近 N 的全集只拟合一次。')
