"""Read-only M5 replay and the predeclared window/FPS comparison."""
import argparse
from dataclasses import asdict,replace
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from gaze_core.autocalibration import OnlineOffset
from gaze_core.config import ROOT,AutocalSettings
from gaze_core.implicit_calibration import load_experiment_settings,sample_clicks,fit_robust
from gaze_core.implicit_experiment import read_experiments,summaries_match
from gaze_core.evaluation import evaluate


def _fps(frames):
    return (len(frames)-1)*1e9/(frames[-1]['capture_ns']-frames[0]['capture_ns']) if len(frames)>1 else None


def simulate(trial,sampling,window,*,half=False,capacity=40):
    frames=sorted(trial.frames,key=lambda r:r['capture_ns'])
    if half:frames=frames[::2]  # First capture anchors deterministic alternate-frame removal.
    options=AutocalSettings(window_ms=tuple(window),max_clicks=max(10,capacity))
    learner=OnlineOffset(trial.geometry,sampling,options,capacity=max(10,capacity))
    events=[(r['capture_ns'],0,r) for r in frames]+[(r['event_ns'],1,r) for r in trial.clicks]
    for _,kind,row in sorted(events,key=lambda e:e[:2]):
        if kind==0:learner.frame(row)
        else:learner.click(row)
    learner.finish(trial.collection['collection_end_ns'])
    state=learner.state()
    summary=evaluate(trial.check_frames,trial.targets,trial.geometry,state['matrix'])
    score=summary['groups'].pop('B',None)
    if score is not None:summary['groups']['C']=score
    # Same-sample batch reference separates implementation parity from rolling effects.
    batch=sample_clicks(frames,trial.clicks,trial.geometry,sampling,window)
    selected=batch['samples'][-capacity:]
    reference=None;error=None
    try:reference=fit_robust(selected,'offset',trial.geometry,sampling,seed=sampling.seed)
    except ValueError as exc:error=str(exc)
    parity=None if state['matrix'] is None or reference is None else summaries_match(state['matrix'],reference['matrix'])
    reference_score=None if reference is None else evaluate(trial.check_frames,trial.targets,trial.geometry,reference['matrix'])['groups']['B']
    offset_cm=None if state['offset_norm'] is None else [state['offset_norm'][0]*trial.geometry.width_cm,state['offset_norm'][1]*trial.geometry.height_cm]
    return dict(window_ms=list(window),downsample='alternate_capture_ns_sorted' if half else 'original',offset_cm=offset_cm,
                training_frame_count=len(frames),actual_training_fps=_fps(frames),capacity=capacity,
                **state,summary=summary,C=score,batch_reference_matrix=None if reference is None else reference['matrix'],
                batch_reference_error=error,batch_reference_C=reference_score,same_samples_match=parity,
                mean_difference_vs_batch_cm=None if reference_score is None or score is None else score['all']['mean_cm']-reference_score['all']['mean_cm'],
                batch_accepted=batch['accepted_count'],batch_reasons=batch['primary_reason_counts'])


def choose_defaults(rows):
    differences={}
    for label in ('all','recent_40'):
        a=rows['a'][label]['C'];c=rows['c'][label]['C']
        differences[label]=None if a is None or c is None or not a['all']['complete'] or not c['all']['complete'] or a['all']['mean_cm'] is None or c['all']['mean_cm'] is None else abs(c['all']['mean_cm']-a['all']['mean_cm'])
    passed=all(v is not None and v<=.15 for v in differences.values())
    return dict(rule='Both all and recent_40 must have abs(C_c.mean_cm-C_a.mean_cm) <= 0.15 cm; unavailable fails.',
                threshold_cm=.15,differences_cm=differences,passed=passed,
                window_ms=[300,0] if passed else [200,0],max_fps='inference' if passed else None)


def compare(trial,sampling):
    rows={}
    for name,half,window in (('a',False,(200,0)),('b',True,(200,0)),('c',True,(300,0))):
        rows[name]={label:simulate(trial,sampling,window,half=half,capacity=capacity)
                    for label,capacity in (('all',max(10,len(trial.clicks))),('recent_40',40))}
    reference=rows['a']['all']['C']
    for scenarios in rows.values():
        for row in scenarios.values():
            row['mean_difference_vs_M4_original_all_cm']=(row['C']['all']['mean_cm']-reference['all']['mean_cm']
                    if row['C'] is not None and reference is not None and row['C']['all']['mean_cm'] is not None and reference['all']['mean_cm'] is not None else None)
    return dict(input_file=trial.file,sha256=trial.digest,session_id=trial.session_id,
                experiment_id=trial.experiment_id,conditions_fingerprint=trial.baseline.get('conditions_fingerprint'),
                sampling=asdict(sampling),scenarios=rows,
                baselines=evaluate(trial.check_frames,trial.targets,trial.geometry,trial.baseline['baseline_matrix']),
                public_check=dict(task_id=trial.summary['task_id'],frame_ids=[f['frame_id'] for f in trial.check_frames],
                                  note='All six fits use the identical full original public check. Only training frames are thinned.'),
                decision=choose_defaults(rows),
                limitations='Single person, Mac, one historical session; no new live measurements, no test-set parameter scan beyond the predeclared comparison.')


def save_report(result,destination):
    destination=Path(destination).resolve();root=(ROOT/'data').resolve()
    if not destination.is_relative_to(root) or destination==root:raise ValueError('报告只能写入 data/ 下的新子目录')
    destination.mkdir(parents=True,exist_ok=False)
    (destination/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    lines=['# M5 点击粗校准回放','',result['limitations'],'',f"输入 SHA256：{result['sha256']}",'',
           '仅对训练帧按capture_ns排序隔帧丢弃；全部组合使用相同完整末尾核对。30/15为名义标签，实测帧率单列。','',
           '| 组合 | 口径 | 实际fps | 有效点击 | 使用内点 | 偏移cm x/y | C平均cm | C P90cm | 同样本一致 |',
           '|---|---|---:|---:|---:|---|---:|---:|---|']
    def fmt(value,digits=9):return '不可用' if value is None else f'{value:.{digits}f}'
    audits=[]
    for name,scenarios in result['scenarios'].items():
        for label,row in scenarios.items():
            c=row['C'];score=fmt(None if c is None else c['all']['mean_cm'])
            p90=fmt(None if c is None else c['all']['p90_cm'])
            lines.append(f"| {name}: {row['window_ms']} | {label} | {fmt(row['actual_training_fps'],6)} | {row['accepted']} | {row['used_count']} | {row['offset_cm']} | {score} | {p90} | {row['same_samples_match']} |")
            audits+=['',f"{name}/{label} 取样剔除原因：`{row['reasons']}`；滚动窗口候选{row['window_count']}，最终拟合离群{row['fit_outliers']}，内点{row['used_count']}；拟合状态：{row['fit_error']}。不足最少点击数时不提供C成绩。同样本相对M4批量offset的均值差：{fmt(row['mean_difference_vs_batch_cm'])} cm；相对M4原始全部点击的均值差：{fmt(row['mean_difference_vs_M4_original_all_cm'])} cm。",'']
    lines+=audits
    lines+=['','## 预先固定的默认值判定','',result['decision']['rule'],'',
            json.dumps(result['decision'],ensure_ascii=False,indent=2),'',
            '有效点击、剔除及偏移包含个人数据，仅保留data/，不改输入录制或个人档案。','']
    (destination/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return destination


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session');parser.add_argument('--output',required=True)
    parser.add_argument('--sampling-config',default=str(ROOT/'config/implicit_experiment.yaml'))
    args=parser.parse_args(argv)
    try:
        trials,skipped=read_experiments(args.session)
        if len(trials)!=1:raise ValueError('需要恰好一个完整M4会话：'+str(skipped))
        source=Path(args.session);before=hashlib.sha256(source.read_bytes()).hexdigest()
        result=compare(trials[0],load_experiment_settings(args.sampling_config))
        if hashlib.sha256(source.read_bytes()).hexdigest()!=before:raise RuntimeError('回放期间输入文件改变')
        destination=save_report(result,args.output)
        print('报告：'+str(destination));print(json.dumps(result['decision'],ensure_ascii=False))
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print('回放失败：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
