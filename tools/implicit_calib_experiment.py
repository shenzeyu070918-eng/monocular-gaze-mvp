"""Read numeric recordings, never cameras/profiles; write ignored M4 reports."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from gaze_core.config import ROOT
from gaze_core.implicit_calibration import load_experiment_settings, M5_RULE_TEXT, M5_RULE_VERSION
from gaze_core.implicit_experiment import analyze_recordings, load_manifest
from tools.replay import ReplayError


def _number(value):return '不可用' if value is None else f'{value:.4f}'


def plot_results(result,path):
    if not result['experiments']:return []
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'gaze-matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    artifacts=[]
    for index,trial in enumerate(result['experiments'],1):
        fig,axes=plt.subplots(2,2,figsize=(12,9),layout='constrained')
        count_axis,recent_axis,scan_axis,grid_axis=axes.ravel()
        for model in result['parameters']['models']:
            for key,axis,xkey in (('count_results',count_axis,'count'),('recent_results',recent_axis,'minutes')):
                rows=sorted(trial[key],key=lambda r:r[xkey]);valid=[]
                for row in rows:
                    distribution=row['distributions'][model]['all']['mean_cm']
                    if distribution['median'] is not None:valid.append((row[xkey],distribution))
                if valid:
                    x=[r[0] for r in valid];y=[r[1]['median'] for r in valid]
                    axis.plot(x,y,marker='o',label=model)
                    axis.fill_between(x,[r[1]['low'] for r in valid],[r[1]['high'] for r in valid],alpha=.12)
        for axis in (count_axis,recent_axis):
            for group,color in (('A','#aa5454'),('B','#555555')):
                value=trial[group]['all']['mean_cm']
                if value is not None:axis.axhline(value,label=group,color=color,linestyle='--')
            axis.set(ylabel='Equal-target mean error (cm)');axis.grid(alpha=.2)
            handles,_=axis.get_legend_handles_labels()
            if handles:axis.legend(fontsize=8)
        count_axis.set(xlabel='Training clicks (last point = all)',title='Paired subsets; fixed seed and quantile intervals')
        recent_axis.set(xlabel='Most recent minutes',title='N selected only from training CV')
        for model in result['parameters']['models']:
            rows=[r for r in trial['window_scan'] if r['model']==model and r['score_cm'] is not None]
            scan_axis.plot([r['window_index'] for r in rows],[r['score_cm'] for r in rows],marker='o',label=model)
        scan_axis.set(xlabel='Window index (see report)',ylabel='Training blocked-CV error (cm)',title='Training only; not check-set scores')
        scan_axis.set_xticks(range(len(result['parameters']['windows_ms'])))
        if scan_axis.get_legend_handles_labels()[0]:scan_axis.legend(fontsize=8)
        selection=trial['selection']
        if selection is not None:
            grid=trial['per_window_audit'][selection['window_index']]['coverage']['grid']
            # Counts only; this is not a gaze dwell-time heatmap (M6).
            grid_axis.set(xlim=(0,3),ylim=(3,0),xticks=[0,1,2,3],yticks=[0,1,2,3],title='Accepted click counts: 3 x 3 coverage')
            grid_axis.grid()
            for y in range(3):
                for x in range(3):grid_axis.text(x+.5,y+.5,str(grid[y][x]),ha='center',va='center',fontsize=18)
        else:grid_axis.text(.5,.5,'No eligible training selection',ha='center',va='center',transform=grid_axis.transAxes)
        fig.suptitle(f"M4: {trial['subject']} / {trial['machine']} / {trial['experiment_id']}")
        output=path.with_name(path.stem+f'-{index:02d}.png');fig.savefig(output,dpi=150);plt.close(fig);artifacts.append(output)
    return artifacts


def write_report(result,output,*,plots=True):
    output=Path(output)
    if output.resolve().is_relative_to((ROOT/'data/profiles').resolve()):
        raise ValueError('实验报告不能写入个人档案目录')
    output.parent.mkdir(parents=True,exist_ok=True)
    images=plot_results(result,output) if plots else []
    json_path=output.with_suffix('.json');csv_path=output.with_suffix('.csv')
    json_path.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    lines=['# M4 点击隐式校准离线实验','',f"生成时间（UTC）：{result['generated_utc']}",'',
           f"样本量：{result['sample_size']['subjects']} 人，{result['sample_size']['machines']} 台机器，{result['sample_size']['experiments']} 个完整实验。",
           result['limitation'],'','## 正式采集前登记的规则','',f'版本：`{M5_RULE_VERSION}`。{M5_RULE_TEXT}',
           '此规则没有命令行覆盖项；不得依据末尾核对结果改阈值、挑模型、挑窗口或挑最近 N。','',
           '## 统计与选择协议','',result['selection_protocol'],result['intervals'],
           'A=同一批末尾核对 raw；B=采集开始时冻结的九点矩阵；C=点击窗口 raw 中位数拟合的线性矩阵。C 不套用 B。',
           '所有组用录制中 sample_selection 的确切 frame_id / target_id，经现有 evaluate() 逐目标中位数、再目标等权汇总。',
           '训练点击没有公共核对目标；不滤波、不裁剪。厘米仅来自录制时实测屏幕。最近 N 从最终完成的公共核对开始时回溯，取消后等待的时间也包含在内。',
           '连点合并以首按位置和时间为锚：配置的厘米半径、毫秒间隔内仅保留首按，不能链式延长；拖动只有按下位置。',
           '下表剔除比例的分母为全部录制按下事件：自窗口、合并连点及窗口筛选按互斥主原因闭合；无效帧原因另作可重叠诊断。',
           '时间覆盖按窗口内全部帧 capture_ns 的首末跨度 / 窗口长度计算，最大间隙按相邻实际 capture_ns 计算；不从配置帧率推导。',
           'min_window_coverage_ratio、max_frame_gap_ms 等为可调探索质量阈值，不是实验结论；每个点击的实际覆盖、间隙与首末时间见 JSON audit。',
           'RANSAC 离群只在已通过前置筛选的训练点击内计算，另列分母，不混入全部点击比例。缺失/无效帧与来源降级如实列出。',
           '实际帧率来自全部推理结果的 capture_ns（包含无脸/眨眼帧），并核对会话事件中的实测值；配置 max_fps 不是实测值。',
           '覆盖图是 3×3 点击样本计数，不是 M6 的视线停留热力图。','']
    if not result['experiments']:lines+=['尚无可完成公平比较的实验；没有生成精度成绩或 M5 有效性结论。','']
    csv_rows=[]
    for index,trial in enumerate(result['experiments'],1):
        lines += [f"## 实验 {index}：{trial['subject']} / {trial['machine']}",'',
                  f"文件 `{trial['file']}`，SHA256 `{trial['sha256']}`；session `{trial['session_id']}`；experiment `{trial['experiment_id']}`。",
                  '录制时系统、设备、相机和屏幕条件：`'+json.dumps(trial['provenance'],ensure_ascii=False)+'`。',
                  f"条件指纹 `{trial['conditions_fingerprint']}`；B 时间 `{trial['baseline_created_at']}`，开始采集时年龄 {_number(trial['baseline_age_s'])} 秒。",
                  f"公共核对 `{trial['check_task_id']}`，确切取样帧 {trial['check_frame_count']}；与录制面板汇总逐项一致：{trial['panel_recomputed_exactly']}。"]
        collection=trial['collection']
        lines += [f"采集 {_number(collection['duration_seconds'])} 秒，共 {collection['frame_count']} 帧（有效 {collection['valid_frame_count']}）；"
                  f"capture_ns 实测 {_number(collection['capture_ns_fps'])} fps；事件记录 {_number(collection['actual_inference_fps'])} fps；"
                  f"差值 {_number(collection['fps_difference'])} fps；事件累计帧数 {collection['recorded_inference_frame_count']}；max_fps 配置 {collection['max_fps']}。",'']
        lines += [f"采集结束至公共核对开始等待 {_number(collection['wait_before_check_seconds'])} 秒；"
                  f"最近 N 分钟锚点 check_start_ns={trial['check_start_ns']}，绑定完成的 task `{trial['check_task_id']}`。",'']
        quality=trial['clock_quality']
        lines += [f"点击时钟来源 `{json.dumps(quality['source_counts'],ensure_ascii=False)}`；回调时间回退 {quality['callback_fallback_count']} 次，"
                  f"比例 {_number(quality['callback_fallback_fraction'])}；缺失来源 {quality['missing_source_count']} 次；"
                  f"回调延迟估计中位/P90 {_number(quality['callback_delay_median_ms'])} / {_number(quality['callback_delay_p90_ms'])} ms，"
                  f"未知延迟 {quality['delay_unknown_count']} 次，最大时钟不确定度 {_number(quality['uncertainty_max_ms'])} ms。",'']
        for warning in trial['warnings']:lines.append('- '+warning)
        lines += ['', '| 分组 | 整体平均 cm | P90 cm | 中央 cm | 边缘 cm | 水平偏移 cm | 垂直偏移 cm | 水平斜率 | 垂直斜率 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        groups=[('A',trial['A']),('B',trial['B'])]
        for label,key in (('C 全部','C_all'),('C 最近 N','C_recent')):
            row=trial[key]
            if row and row['ok']:groups.append((label,row['summary']))
        for label,summary in groups:
            values=[summary['all']['mean_cm'],summary['all']['p90_cm'],summary['center']['mean_cm'],summary['edge']['mean_cm'],
                    summary['all']['horizontal_cm'],summary['all']['vertical_cm'],summary['all']['horizontal_scale'],summary['all']['vertical_scale']]
            lines.append('| '+label+' | '+' | '.join(_number(v) for v in values)+' |')
            for region in ('all','center','edge'):
                csv_rows.append(dict(experiment_id=trial['experiment_id'],subject=trial['subject'],machine=trial['machine'],
                                     kind='final',label=label,model=trial['selection']['model'] if label.startswith('C') else '',
                                     count=None,minutes=None,repeat=None,region=region,**summary[region]))
        lines += ['',f"预登记判定：**{trial['decision']['decision']}**。",'', '训练阶段选定：`'+json.dumps(trial['selection'],ensure_ascii=False)+'`。','',
                  '| 窗口索引 | 点击前窗口 ms | 模型 | 可用点击 | 训练 CV cm | 不可用原因 |','|---:|---|---|---:|---:|---|']
        for row in trial['window_scan']:
            lines.append(f"| {row['window_index']} | {row['window_ms']} | {row['model']} | {row['accepted_clicks']} | {_number(row['score_cm'])} | {row['reason'] or ''} |")
        lines += ['', '| 最近分钟 | 可用点击 | 训练 CV cm | 原因 |','|---:|---:|---:|---|']
        for row in trial['recent_scan']:lines.append(f"| {row['minutes']} | {row['accepted_clicks']} | {_number(row['score_cm'])} | {row['reason'] or ''} |")
        lines += ['', '按窗口的互斥筛选主原因（accepted 也列出，合计等于全部点击数）：','']
        for wi,audit in enumerate(trial['per_window_audit']):
            lines.append(f"- 窗口 {wi}：总 {audit['click_count']}，保留 {audit['accepted_count']}，剔除比例 {_number(audit['rejection_fraction'])}；"
                         f"主原因 `{json.dumps(audit['primary_reason_counts'],ensure_ascii=False)}`；空白网格 `{audit['coverage']['empty_cells']}`。")
        for label,key in (('全部','C_all'),('最近 N','C_recent')):
            row=trial[key]
            if row and row['ok']:
                fit=row['fit'];lines.append(f"- C {label} RANSAC：{fit['outlier_count']} / {fit['inlier_count']+fit['outlier_count']} 训练点击剔除，比例 {_number(fit['outlier_fraction'])}。")
        if trial.get('selected_rejection_audit'):
            audit=trial['selected_rejection_audit']
            lines.append('- 预选方案最终筛选（含 RANSAC），全部按下事件分母：`'+json.dumps(audit,ensure_ascii=False)+'`。')
        lines += ['', '| 子集 | 点击数 | 模型 | 有效重复 | 整体中位 cm | 区间 | 中央中位 cm | 边缘中位 cm |','|---|---:|---|---:|---:|---|---:|---:|']
        for kind,key in (('count','count_results'),('recent','recent_results')):
            for subset in trial[key]:
                label=subset['label'] if kind=='count' else f"最近 {subset['minutes']} 分钟"
                for model,distribution in subset['distributions'].items():
                    all_=distribution['all']['mean_cm']
                    lines.append(f"| {label} | {subset['count']} | {model} | {all_['n']} | {_number(all_['median'])} | "
                                 f"[{_number(all_['low'])}, {_number(all_['high'])}] | {_number(distribution['center']['mean_cm']['median'])} | {_number(distribution['edge']['mean_cm']['median'])} |")
                for repetition in subset['repetitions']:
                    if not repetition['ok']:continue
                    for region in ('all','center','edge'):
                        csv_rows.append(dict(experiment_id=trial['experiment_id'],subject=trial['subject'],machine=trial['machine'],
                          kind=kind,label=label,model=repetition['model'],count=subset['count'],minutes=subset.get('minutes'),
                          repeat=repetition['repeat'],region=region,**repetition['summary'][region]))
        lines+=['','拟合失败分布（保留失败重复，不把失败当作零误差）：','']
        for key in ('count_results','recent_results'):
            for subset in trial[key]:
                failures=Counter(row['reason'] for row in subset['repetitions'] if not row['ok'])
                if failures:lines.append(f"- {key} / {subset.get('label',subset.get('minutes'))}：`{dict(failures)}`。")
                if subset.get('unavailable_reason'):lines.append(f"- {key} / {subset.get('label')}：{subset['unavailable_reason']}。")
        if images:lines+=['',f'![点击数量、最近时间、训练窗口扫描和点击覆盖]({images[index-1].name})','']
    if result['skipped']:
        lines+=['','## 不可分析的输入','']
        for row in result['skipped']:lines.append('- `'+row['file']+'`：'+', '.join(row['reasons']))
    lines += ['','## 可复现配置与摘要','', '```json',json.dumps(result['parameters'],ensure_ascii=False,indent=2),'```','',
              '输入文件只读；不读取、不修改个人档案，不把 C 接入实时显示。完整帧 ID、取样、拟合内外点、重复子集索引和原因见同名 JSON。',
              f'数值明细：`{json_path.name}`；逐组/逐重复 CSV：`{csv_path.name}`。']
    if result.get('source_hashes'):
        lines+=['','代码及实验配置 SHA256：','']
        for key,digest in result['source_hashes'].items():lines.append(f'- `{key}`：`{digest}`')
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    fields=list(dict.fromkeys(key for row in csv_rows for key in row)) or ['experiment_id','kind','label','region','mean_cm']
    with csv_path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(csv_rows)
    return dict(report=output,json=json_path,csv=csv_path,images=images)


def _private_output(path):
    path=Path(path).resolve()
    if path.suffix!='.md':raise ValueError('--output 必须为 .md 文件')
    if path.is_relative_to((ROOT/'data/profiles').resolve()):raise ValueError('实验报告不能写入个人档案目录')
    if path.is_relative_to((ROOT/'data').resolve()):return path
    reports=(ROOT/'reports').resolve()
    if path.is_relative_to(reports) and path.relative_to(reports).parts[0].startswith('implicit_calibration'):
        return path
    raise ValueError('个人报告须放在忽略的 data/ 或 reports/implicit_calibration* 路径下')


def main(argv=None):
    parser=argparse.ArgumentParser(description='只读 Python 点击录制；公共核对上的 A/B/C 离线实验（不改档案）')
    parser.add_argument('recordings',nargs='*',type=Path,help='JSONL；不传文件时生成无成绩说明')
    parser.add_argument('--config',type=Path,default=ROOT/'config/implicit_experiment.yaml')
    parser.add_argument('--manifest',type=Path,help='data/ 下匿名 file/subject/machine JSON 清单')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/implicit_calibration.md')
    parser.add_argument('--no-plots',action='store_true',help='只写 Markdown / JSON / CSV')
    args=parser.parse_args(argv)
    try:
        output=_private_output(args.output);settings=load_experiment_settings(args.config)
        companions={output,output.with_suffix('.json'),output.with_suffix('.csv')}
        inputs={p.resolve() for p in [*args.recordings,args.config,*([args.manifest] if args.manifest else [])]}
        if companions & inputs:raise ValueError('报告输出不能覆盖录制、配置或输入清单')
        result=analyze_recordings(args.recordings,settings,manifest=load_manifest(args.manifest))
        names=['gaze_core/implicit_calibration.py','gaze_core/implicit_experiment.py','gaze_core/evaluation.py','gaze_core/fixation.py','tools/implicit_calib_experiment.py']
        result['source_hashes']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
        result['source_hashes']['experiment_config']=hashlib.sha256(args.config.read_bytes()).hexdigest()
        if args.manifest is not None:result['source_hashes']['anonymous_manifest']=hashlib.sha256(args.manifest.read_bytes()).hexdigest()
        artifacts=write_report(result,output,plots=not args.no_plots)
    except (OSError,ValueError,TypeError,ReplayError) as error:
        print('点击离线实验失败：'+str(error),file=sys.stderr);return 1
    print(f"样本量：{result['sample_size']['subjects']} 人 / {result['sample_size']['machines']} 台 / {result['sample_size']['experiments']} 个实验；跳过 {len(result['skipped'])} 项。")
    for row in result['experiments']:print(f"{row['subject']} / {row['machine']}：{row['decision']['decision']}")
    print('报告：'+str(artifacts['report']));return 0


if __name__=='__main__':raise SystemExit(main())
