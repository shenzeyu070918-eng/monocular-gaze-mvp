"""Offline, unfiltered click samples and seeded robust linear calibration.

This module knows nothing about windows, files, profiles or the display pipeline.
Click targets are noisy training labels; independent check targets never enter fit.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, field
from numbers import Real
import math
import numpy as np
import yaml

from .config import ROOT, FixationSettings
from .fixation import FixationDetector


MODELS = ('offset', 'scale_offset', 'affine')
M5_RULE_VERSION = 'm4-2026-10-21'
M5_RULE_TEXT = ('在同一次末尾核对上，若训练阶段预选模型的「全部点击」C 组整体平均误差 ≤ B 组，'
                '或「最近 N 分钟」C 组 ≤ B 组，判定 M5 值得做；两者均未达到且全部与预选最近 N 两组完整 C 组'
                '比 B 组差至少 0.5 cm，判定点击不能替代九点，M5 改为「九点 + 点击微调」；'
                '介于两者之间或结果不足时，报告「证据不足」。+0.5 cm 边界属于九点 + 点击微调。')


def _finite(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(name+' 必须为有限实数')
    value=float(value)
    if positive and value<=0:raise ValueError(name+' 必须大于零')
    return value


def points(values):
    array=np.asarray(values,dtype=float)
    if array.ndim!=2 or array.shape[1]!=2 or not np.isfinite(array).all():
        raise ValueError('坐标必须为有限 N×2；原始坐标不裁剪')
    return array


@dataclass(frozen=True)
class ExperimentSettings:
    # Exploratory search grid, not a claim about the best window/model.
    windows_ms: tuple = ((200.,0.),(300.,0.),(500.,0.),(500.,100.),(500.,200.),(800.,200.))
    models: tuple = MODELS
    counts: tuple = (5,10,20,40)
    recent_minutes: tuple = (1.,3.,5.,10.)
    seed: int = 20261021
    repeats: int = 30
    interval_percentiles: tuple = (10.,90.)
    min_frames: int = 4
    min_window_coverage_ratio: float = .5
    max_frame_gap_ms: float = 250.
    min_valid_ratio: float = .8
    min_fixation_ratio: float = .5
    max_head_degrees: tuple = (35.,40.,35.)
    max_position_sd_cm: float = 2.5
    duplicate_ms: float = 500.
    duplicate_radius_cm: float = 1.
    ransac_threshold_cm: float = 2.5
    ransac_iterations: int = 200
    min_training_clicks: int = 5
    min_axis_span: float = .05
    min_occupied_cells: int = 3
    cv_folds: int = 3
    fixation: dict = field(default_factory=lambda: asdict(FixationSettings()))

    def __post_init__(self):
        if not self.windows_ms:raise ValueError('windows_ms 不能为空')
        for window in self.windows_ms:
            if len(window)!=2 or not 0<=_finite(window[1],'window end')<_finite(window[0],'window start',positive=True):
                raise ValueError('点击前窗口须满足 start_ms > end_ms ≥ 0')
        if not self.models or set(self.models)-set(MODELS):raise ValueError('不支持的线性模型')
        if len(set(self.models))!=len(self.models):raise ValueError('模型不可重复')
        for key in ('repeats','min_frames','ransac_iterations','min_training_clicks','min_occupied_cells','cv_folds'):
            if type(getattr(self,key)) is not int or getattr(self,key)<1:raise ValueError(key+' 必须为正整数')
        if self.cv_folds<2 or self.min_training_clicks<3 or self.min_occupied_cells>9:raise ValueError('训练配置范围无效')
        if type(self.seed) is not int or self.seed<0:raise ValueError('seed 必须是非负整数')
        if not self.counts or any(type(n) is not int or n<1 for n in self.counts):raise ValueError('counts 必须为正整数数组')
        if not self.recent_minutes:raise ValueError('recent_minutes 不能为空')
        for value in self.recent_minutes:_finite(value,'recent_minutes',positive=True)
        for key in ('min_valid_ratio','min_fixation_ratio','min_window_coverage_ratio'):
            if not 0<=_finite(getattr(self,key),key)<=1:raise ValueError(key+' 必须在 [0,1]')
        for key in ('max_position_sd_cm','duplicate_ms','duplicate_radius_cm','ransac_threshold_cm','min_axis_span','max_frame_gap_ms'):
            _finite(getattr(self,key),key,positive=True)
        if len(self.max_head_degrees)!=3:raise ValueError('max_head_degrees 需要 xyz 三轴上限')
        for value in self.max_head_degrees:_finite(value,'head limit',positive=True)
        if len(self.interval_percentiles)!=2 or not 0<=self.interval_percentiles[0]<50<self.interval_percentiles[1]<=100:
            raise ValueError('区间须包含中位数且在 [0,100]')
        FixationSettings(**self.fixation)


def load_experiment_settings(path=ROOT/'config/implicit_experiment.yaml'):
    data=yaml.safe_load(__import__('pathlib').Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('实验配置必须为映射')
    return ExperimentSettings(**data)


def cm_scale(geometry):
    if not geometry.measured or geometry.width_cm is None or geometry.height_cm is None:
        raise ValueError('点击实验需要录制时实测屏幕尺寸；不会借用当前机器配置')
    return np.array([_finite(geometry.width_cm,'width_cm',positive=True),
                     _finite(geometry.height_cm,'height_cm',positive=True)])


def deduplicate_clicks(clicks, geometry, settings):
    """First down anchors both time and position; no chaining or drag endpoint."""
    scale=cm_scale(geometry);groups=[]
    for click in sorted(clicks,key=lambda row:row['event_ns']):
        p=np.array([_finite(click['x'],'click.x'),_finite(click['y'],'click.y')])
        duplicate=False
        for group in reversed(groups):
            first=group['click']
            if click['event_ns']-first['event_ns']>settings.duplicate_ms*1e6:break
            if float(np.linalg.norm((p-np.array([first['x'],first['y']]))*scale))<=settings.duplicate_radius_cm:
                group['merged_presses']+=1;duplicate=True;break
        if not duplicate:groups.append(dict(click=click,merged_presses=1))
    return groups


def coverage(samples):
    grid=np.zeros((3,3),dtype=int)
    for sample in samples:
        x,y=sample['target']
        # Clicks outside the built-in screen should already have been dropped.
        if not 0<=x<=1 or not 0<=y<=1:raise ValueError('点击目标必须位于内置屏内')
        grid[min(2,int(y*3)),min(2,int(x*3))]+=1
    raw=points([s['raw'] for s in samples]) if samples else np.empty((0,2))
    target=points([s['target'] for s in samples]) if samples else np.empty((0,2))
    return dict(grid=grid.tolist(),occupied_cells=int(np.count_nonzero(grid)),
                empty_cells=[[int(x),int(y)] for y,x in zip(*np.where(grid==0))],
                raw_span=np.ptp(raw,axis=0).tolist() if len(raw) else [0.,0.],
                target_span=np.ptp(target,axis=0).tolist() if len(target) else [0.,0.])


def raw_fixation_state(detector, frame, geometry):
    """Shared causal raw-state step for both batch M4 and online sampling."""
    if not frame.get('valid') or frame.get('raw') is None:
        detector.reset();return None
    try:
        return detector.update(frame['raw'],frame['capture_ns']/1e9,
                               origin_m=frame.get('origin_m'),geometry=geometry)
    except (ValueError,TypeError):
        detector.reset();return None


def sample_clicks(frames, clicks, geometry, settings, window_ms, *, fixation_states=None):
    """Every rejection has one primary reason; diagnostics may overlap explicitly.

    Detector uses the same raw (not B corrected) coordinates as C fitting, and
    never consumes a frame after a click's configured window end.
    """
    scale=cm_scale(geometry)
    ordered=sorted(frames,key=lambda r:r['capture_ns']);times=[r['capture_ns'] for r in ordered]
    if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('capture_ns 必须严格递增')
    states=fixation_states
    if states is None:
        states={};detector=FixationDetector(FixationSettings(**settings.fixation))
        for frame in ordered:
            state=raw_fixation_state(detector,frame,geometry)
            if state is not None:states[frame['capture_ns']]=state
    accepted=[];audit=[]
    first_pass=[r for r in clicks if not r.get('excluded',False)]
    groups=deduplicate_clicks(first_pass,geometry,settings)
    group_keys={id(group['click']):group for group in groups}
    duplicate_count=len(first_pass)-len(groups)
    for click in clicks:
        if click.get('excluded',False):
            audit.append(dict(event_ns=click['event_ns'],reason='self_window',detail=click.get('exclusion_reason'),merged_presses=1));continue
        if id(click) not in group_keys:
            audit.append(dict(event_ns=click['event_ns'],reason='duplicate_press',merged_presses=1));continue
        group=group_keys[id(click)];start,end=window_ms
        low,high=click['event_ns']-int(start*1e6),click['event_ns']-int(end*1e6)
        rows=ordered[bisect_left(times,low):bisect_right(times,high)]
        diagnostics=Counter(str(r.get('status','unknown')) for r in rows if not r.get('valid'))
        row_audit=dict(event_ns=click['event_ns'],reason=None,merged_presses=group['merged_presses'],
                       window_frame_count=len(rows),invalid_status_counts=dict(diagnostics))
        # Exploratory quality limits, measured from actual capture times. No
        # configured FPS assumption; invalid rows still count toward timing.
        coverage_ratio=(rows[-1]['capture_ns']-rows[0]['capture_ns'])/(high-low) if len(rows)>1 else 0.
        gap_ms=max((b['capture_ns']-a['capture_ns'])/1e6 for a,b in zip(rows,rows[1:])) if len(rows)>1 else None
        row_audit.update(window_coverage_ratio=float(coverage_ratio),max_frame_gap_ms=gap_ms,
                         window_first_capture_ns=rows[0]['capture_ns'] if rows else None,
                         window_last_capture_ns=rows[-1]['capture_ns'] if rows else None)
        reason=None
        if len(rows)<settings.min_frames:reason='insufficient_frames'
        elif coverage_ratio<settings.min_window_coverage_ratio:reason='insufficient_time_coverage'
        elif gap_ms is not None and gap_ms>settings.max_frame_gap_ms:reason='excessive_frame_gap'
        valid=[r for r in rows if r.get('valid') and r.get('raw') is not None]
        if reason is None and len(valid)/len(rows)<settings.min_valid_ratio:
            reason='no_face' if diagnostics['no_face'] else 'blink' if diagnostics['blink'] else 'invalid_ratio'
        if reason is None and len(valid)<settings.min_frames:reason='insufficient_valid_frames'
        array=None
        if reason is None:
            try:array=points([r['raw'] for r in valid])
            except (ValueError,TypeError,OverflowError):reason='invalid_numeric'
        if reason is None:
            heads=[r.get('head_degrees') for r in valid]
            if any(h is None for h in heads):reason='missing_head_pose'
            else:
                h=np.asarray(heads,dtype=float)
                if h.shape!=(len(valid),3) or not np.isfinite(h).all():reason='invalid_head_pose'
                elif np.any(np.abs(h)>np.array(settings.max_head_degrees)):reason='head_pose'
        if reason is None:
            stability=sum(states.get(r['capture_ns'],{}).get('state')=='fixation' for r in valid)/len(valid)
            row_audit['fixation_ratio']=stability
            row_audit['velocity_fallback_frames']=sum(states.get(r['capture_ns'],{}).get('velocity_basis')!='angular' for r in valid)
            sd=float(np.linalg.norm(np.std(array*scale,axis=0)))
            row_audit['position_sd_cm']=sd
            if stability<settings.min_fixation_ratio:reason='unstable_fixation'
            elif sd>settings.max_position_sd_cm:reason='position_outlier'
        row_audit['reason']=reason or 'accepted';audit.append(row_audit)
        if reason is None:
            accepted.append(dict(event_ns=click['event_ns'],raw=np.median(array,axis=0).tolist(),
                                 target=[float(click['x']),float(click['y'])],frame_count=len(valid),
                                 first_capture_ns=int(valid[0]['capture_ns']),last_capture_ns=int(valid[-1]['capture_ns'])))
    counts=Counter(r['reason'] for r in audit)
    return dict(samples=accepted,audit=audit,click_count=len(clicks),accepted_count=len(accepted),
                rejected_count=len(clicks)-len(accepted),rejection_fraction=(len(clicks)-len(accepted))/len(clicks) if clicks else None,
                primary_reason_counts=dict(sorted(counts.items())),duplicate_presses=duplicate_count,coverage=coverage(accepted))


def _fit(source,target,model):
    if model=='offset':
        delta=np.mean(target-source,axis=0)
        return np.array([[1.,0.],[0.,1.],delta])
    if model=='scale_offset':
        out=np.zeros((3,2))
        for axis in range(2):
            design=np.column_stack((source[:,axis],np.ones(len(source))))
            if np.linalg.matrix_rank(design)<2:raise ValueError('缩放模型坐标退化')
            coefficient=np.linalg.lstsq(design,target[:,axis],rcond=None)[0]
            out[axis,axis],out[2,axis]=coefficient
        return out
    design=np.column_stack((source,np.ones(len(source))))
    if np.linalg.matrix_rank(design)<3:raise ValueError('仿射模型共线或覆盖退化')
    return np.linalg.lstsq(design,target,rcond=None)[0]


def fit_robust(samples, model, geometry, settings, *, seed=None):
    """Seeded RANSAC, threshold measured in screen cm; refit on inliers only."""
    if model not in MODELS:raise ValueError('不支持的模型')
    minimum={'offset':1,'scale_offset':2,'affine':3}[model]
    if len(samples)<max(minimum,settings.min_training_clicks):raise ValueError('点击训练样本不足')
    cov=coverage(samples)
    if cov['occupied_cells']<settings.min_occupied_cells:raise ValueError('点击覆盖网格不足')
    if model!='offset' and min(cov['raw_span']+cov['target_span'])<settings.min_axis_span:
        raise ValueError('缩放或仿射模型的坐标跨度不足')
    source=points([s['raw'] for s in samples]);target=points([s['target'] for s in samples]);scale=cm_scale(geometry)
    rng=np.random.default_rng(settings.seed if seed is None else seed);best=None;best_score=None
    for _ in range(settings.ransac_iterations):
        chosen=rng.choice(len(source),minimum,replace=False)
        try:matrix=_fit(source[chosen],target[chosen],model)
        except (ValueError,np.linalg.LinAlgError):continue
        errors=np.linalg.norm((np.column_stack((source,np.ones(len(source))))@matrix-target)*scale,axis=1)
        mask=errors<=settings.ransac_threshold_cm
        if np.count_nonzero(mask)<minimum:continue
        score=(int(np.count_nonzero(mask)),-float(np.median(errors[mask])))
        if best_score is None or score>best_score:best,best_score=mask,score
    if best is None or np.count_nonzero(best)<settings.min_training_clicks:raise ValueError('稳健拟合内点不足')
    # Reclassification may improve but never turn a rank-deficient set into an implicit model.
    for _ in range(3):
        matrix=_fit(source[best],target[best],model)
        errors=np.linalg.norm((np.column_stack((source,np.ones(len(source))))@matrix-target)*scale,axis=1)
        mask=errors<=settings.ransac_threshold_cm
        if np.array_equal(mask,best):break
        if np.count_nonzero(mask)<max(minimum,settings.min_training_clicks):break
        best=mask
    matrix=_fit(source[best],target[best],model)
    errors=np.linalg.norm((np.column_stack((source,np.ones(len(source))))@matrix-target)*scale,axis=1)
    inlier_coverage=coverage([s for s,keep in zip(samples,best) if keep])
    if inlier_coverage['occupied_cells']<settings.min_occupied_cells:
        raise ValueError('稳健拟合内点的点击覆盖网格不足')
    if model!='offset' and min(inlier_coverage['raw_span']+inlier_coverage['target_span'])<settings.min_axis_span:
        raise ValueError('稳健拟合内点的坐标跨度不足')
    return dict(model=model,matrix=matrix.tolist(),inlier_indices=np.flatnonzero(best).tolist(),
                outlier_indices=np.flatnonzero(~best).tolist(),inlier_count=int(np.count_nonzero(best)),
                outlier_count=int(np.count_nonzero(~best)),outlier_fraction=float(np.mean(~best)),
                residual_cm=errors.tolist(),coverage=cov,inlier_coverage=inlier_coverage)


def blocked_cv(samples, model, geometry, settings):
    """Contiguous time blocks; no public-check data is accepted by this API."""
    ordered=sorted(samples,key=lambda s:s['event_ns'])
    if len(ordered)<settings.min_training_clicks+settings.cv_folds:
        return dict(score_cm=None,reason='insufficient_cv_clicks',folds=[])
    scale=cm_scale(geometry);folds=[]
    for index,held in enumerate(np.array_split(np.arange(len(ordered)),settings.cv_folds)):
        heldset=set(held.tolist());train=[s for i,s in enumerate(ordered) if i not in heldset]
        try:fit=fit_robust(train,model,geometry,settings,seed=settings.seed+index)
        except ValueError as error:return dict(score_cm=None,reason=str(error),folds=folds)
        test=[ordered[i] for i in held]
        raw=points([s['raw'] for s in test]);target=points([s['target'] for s in test])
        errors=np.linalg.norm((np.column_stack((raw,np.ones(len(raw))))@fit['matrix']-target)*scale,axis=1)
        folds.append(dict(training_count=len(train),validation_count=len(test),mean_cm=float(np.mean(errors)),
                          validation_times_ns=[int(s['event_ns']) for s in test]))
    return dict(score_cm=float(np.average([f['mean_cm'] for f in folds],weights=[f['validation_count'] for f in folds])),
                reason=None,folds=folds)


def decide_m5(b_mean_cm, all_c_mean_cm, recent_c_mean_cm):
    """Frozen precollection rule; ties improve, exactly +0.5 means fine-tuning."""
    values=[v for v in (all_c_mean_cm,recent_c_mean_cm) if v is not None and math.isfinite(v)]
    if b_mean_cm is None or not math.isfinite(b_mean_cm) or not values:
        return dict(version=M5_RULE_VERSION,decision='证据不足',reason='缺少可比较的实测 B/C 整体平均误差')
    if any(v<=b_mean_cm for v in values):decision='M5 值得做'
    elif len(values)==2 and all(v-b_mean_cm>=.5 for v in values):decision='点击不能替代九点；M5 改为九点 + 点击微调'
    else:decision='证据不足'
    return dict(version=M5_RULE_VERSION,decision=decision,b_mean_cm=float(b_mean_cm),
                all_c_mean_cm=all_c_mean_cm,recent_c_mean_cm=recent_c_mean_cm)
