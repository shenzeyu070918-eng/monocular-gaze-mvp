"""Replay numeric jump sessions and write a reproducible M2 tuning report."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import yaml

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from gaze_core.config import ROOT, load_settings
from gaze_core.tuning import load_tuning_settings, read_jump_tasks, tune
from tools.replay import ReplayError


REASONS={
    'unmeasured_screen':'录制未确认实测屏幕尺寸',
    'unknown_calibration_coordinates':'个人矩阵缺少全屏坐标说明',
    'fullscreen_not_confirmed_in_session':'会话未确认全屏，不能推断任务时的屏幕条件',
    'personal_calibration_required':'缺少本次要求的 B 组个人校准',
    'unexpected_point_count':'点数不符合本次配置要求',
    'session_missing_end':'会话缺少正常 end',
    'task_not_completed':'任务未正常完成',
    'calibration_changed_during_task':'任务中途改变个人校准',
    'no_task_frames':'任务没有帧',
    'nonincreasing_capture_timestamp':'取帧时间戳不递增',
    'duplicate_task':'重复输入同一个任务',
    'mixed_device_or_group_conditions':'输入包含不同设备/相机条件或 A/B 分组，请分开调参',
}


def number(value,unit=''):
    return '不可用' if value is None else f'{value:.4f}{unit}'


def plot_tradeoff(result,path):
    candidates=result['candidates']
    indices=[i for i,r in enumerate(candidates) if r['extra_delay_ms'] is not None and r['jitter_cm'] is not None]
    if not indices:return False
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'gaze-matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    fig,axis=plt.subplots(figsize=(8,5),layout='constrained')
    for eligible,color,label in [(True,'#167d6e','Qualified candidates'),(False,'#a7b1bb','Exploratory candidates')]:
        group=[i for i in indices if candidates[i]['eligible']==eligible]
        if group:axis.scatter([candidates[i]['extra_delay_ms'] for i in group],[candidates[i]['jitter_cm'] for i in group],c=color,label=label)
    front=sorted(result['pareto'],key=lambda i:candidates[i]['extra_delay_ms'])
    if front:
        axis.plot([candidates[i]['extra_delay_ms'] for i in front],[candidates[i]['jitter_cm'] for i in front],
                  color='#dd8426',marker='o',fillstyle='none',label='Pareto front (all measurable)')
    eligible_front=sorted(result['eligible_pareto'],key=lambda i:candidates[i]['extra_delay_ms'])
    if eligible_front:
        axis.plot([candidates[i]['extra_delay_ms'] for i in eligible_front],[candidates[i]['jitter_cm'] for i in eligible_front],
                  color='#3868b2',linestyle='--',label='Qualified Pareto front')
    recommended=result['recommended']
    if recommended is not None:
        item=candidates[recommended]
        axis.scatter([item['extra_delay_ms']],[item['jitter_cm']],marker='*',s=180,color='#ae3672',label=f'Recommended candidate {recommended+1}')
    axis.set(xlabel='Additional time to 90% (ms)',ylabel='Position SD in common stable windows (cm)',
             title='M2: latency versus jitter'+(' (exploratory; no qualified recommendation)' if recommended is None else ''))
    axis.grid(alpha=.2);axis.legend(fontsize=8)
    fig.savefig(path,dpi=160);plt.close(fig)
    return True


def write_report(result,config,detector,output,inputs):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    image_path=output.with_name(output.stem+'-pareto.png')
    plotted=plot_tradeoff(result,image_path)
    recommended=result['recommended'];candidates=result['candidates']
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                   ('gaze_core/filters.py','gaze_core/fixation.py','gaze_core/display.py','gaze_core/evaluation.py','gaze_core/tuning.py','tools/tune_filter.py')}
    lines=['# M2 滤波调参报告','',f'生成时间（UTC）：{datetime.now(timezone.utc).isoformat()}','',
           '状态：'+('有通过数据与质量门槛的推荐候选；主观实机试用仍未验证。' if recommended is not None else '**待实测 / 无合格推荐参数。默认配置仍是临时初值。**'),'',
           '## 输入与适用范围','',
           f"读取文件 {len(inputs)} 份；可分析的完整跳点任务 {len(result['tasks'])} 个；跳过 {len(result['skipped'])} 个；计算参数组合 {len(candidates)} 组。",
           f'推荐要求至少 {config.min_tasks} 个独立任务，每个 {config.expected_points} 点，并满足下方覆盖/检测门槛。',
           '只使用录制时的设备几何和校准事件，不借用当前机器的测量值或最新个人档案。A/B 不混合计算；不同设备、灰度或相机条件混合时要求分开运行。','']
    for task in result['tasks']:
        lines.append(f'- 文件 `{task.file}`；任务 `{task.task_id}`；{task.group} 组；设备文件 `{task.device_file}`；SHA256 `{task.digest}`；相机内参 `{task.condition[1] if len(task.condition)>1 else 'unknown'}`，去畸变 `{task.condition[2] if len(task.condition)>2 else 'unknown'}`。')
    lines += ['', '算法源文件 SHA256（用于核对调参实现）：', '', *[f'- `{name}`：`{digest}`' for name,digest in source_hashes.items()]]
    if result['skipped']:
        lines+=['','跳过项：','']
        for row in result['skipped']:
            reasons='；'.join(REASONS.get(reason,reason) for reason in row['reasons'])
            lines.append(f"- `{row['file']}` / `{row['task_id']}`：{reasons}。")
    if not inputs:lines+=['- 尚未提供本轮跳点录制；没有生成测量数字或示例成绩。']
    lines+=['','## 统计定义','',
            '- 所有时间来自 browser_capture_ms，按同页浏览器时钟与 target/calibration 事件对齐；不用服务器接收或完成时刻。',
            '- 依时间应用当时的个人矩阵；未滤波基线也是同一矩阵修正后的信号。矩阵变化、丢失跟踪或长间隔清空滤波历史。',
            '- 主抖动指标在每个目标固定稳定窗口内，用所有有效配对帧计算 x/y 总体位置标准差（ddof=0），合成为 sqrt(SDx²+SDy²)；先目标等权、再任务等权。不会按候选检测结果删除高噪声帧。',
            '- 厘米使用录制的实测宽高；角度在每帧 origin_m 上分别连向点及本窗口中心，计算 yaw/pitch 偏差的轴向标准差与合成标准差。缺失/非法 origin 不补值，另报覆盖；各量为抖动，不是精度。',
            '- 另列检测注视段内的抖动、注视覆盖和段数，防止只留下很少的安静样本就得到推荐。',
            '- 90%：沿旧目标到新目标的厘米方向投影，从旧目标算进度；两个信号使用相同起点、方向、90% 阈值及有效帧。逐跳相减：滤波时刻 − 未滤波时刻；保留负差，不截成 0。',
            '- 首个达到阈值的采样时刻作为到达时刻；hold 配置非零时还要求持续达到。没有亚帧插值，也不跨缺帧/任务边界拼接；未达到、无效跳动及配对数单列。',
            '- 目标时间是设置位置的 requestAnimationFrame 回调，实际显示可能晚一个刷新周期；该指标比较输入时间轴上的算法响应，不包括滤波运行开销或实际屏幕呈现；不是摄像头到屏幕的端到端延迟，目标反应时间也不是真实眼跳起始标注。',
            '- 目标切换与检测眼跳按响应窗口一对一匹配。漏检代理=未匹配目标切换/可判定切换；误检代理=未匹配检测/全部被评估检测（仅可判定跳动区间，缺帧区间的检测不计分）（FP/(TP+FP)，不是负类 FPR）。首次目标无前一目标，不用于切换延迟。',
            '- 帕累托轴为额外延迟与统一窗口厘米抖动，越低越好。推荐仅从通过覆盖/检测门槛的前沿选取；归一化后取向理想点偏离两端连线最多的拐点候选，无明显拐点时取距理想点最近者。','',
            '## 配置与固定条件','', '```yaml',yaml.safe_dump({'search_and_measurement':asdict(config),'detector_fixed_values':asdict(detector)},allow_unicode=True,sort_keys=False).rstrip(),'```','',
            '## 推荐与取舍曲线','']
    if recommended is None:
        lines+=['尚无合格推荐。请先在 Mac 全屏、尺寸实测、标定＋去畸变条件下九点校准，录制 3 次完整的 20 点跳点任务，再运行本工具。即便已有探索性结果，也不回写默认配置或声称已完成实机调参。']
    else:
        best=candidates[recommended]
        lines +=[f'推荐候选 **{recommended+1}**，适用于上述输入条件；尚需主观试用：','',
                 '```yaml',yaml.safe_dump({'filter':{k:best['params'][k] for k in ('min_cutoff','beta','d_cutoff')},'fixation':{'angular_threshold_deg_s':best['params']['angular_threshold_deg_s']}},sort_keys=False).rstrip(),'```',
                 f"未滤波 → 滤波抖动：{number(best['raw_jitter_cm'],' cm')} → {number(best['jitter_cm'],' cm')}；{number(best['raw_jitter_deg'],'°')} → {number(best['jitter_deg'],'°')}。",
                 f"90% 时间：{number(best['raw_delay_ms'],' ms')} → {number(best['filtered_delay_ms'],' ms')}；额外 {number(best['extra_delay_ms'],' ms')}。",
                 '以上从本轮输入计算，不代表其他用户或设备；坐标速度回退阈值没有被本次角阈值搜索调优。']
    if plotted:lines+=['',f'![额外延迟与抖动的取舍曲线]({image_path.name})']
    lines+=['','## 各参数组合','',
            '| 候选 | min_cutoff / beta / d_cutoff / deg·s⁻¹ | 原始→滤波 cm SD | 原始→滤波 ° SD | 原始→滤波 90% ms | 额外 ms | 漏检代理 | 误检代理 | 推荐资格 |',
            '|---|---|---|---|---|---|---|---|---|']
    for i,row in enumerate(candidates):
        params=' / '.join(str(v) for v in row['params'].values())
        lines.append(f"| {i+1} | {params} | {number(row['raw_jitter_cm'])} → {number(row['jitter_cm'])} | {number(row['raw_jitter_deg'])} → {number(row['jitter_deg'])} | {number(row['raw_delay_ms'])} → {number(row['filtered_delay_ms'])} | {number(row['extra_delay_ms'])} | {number(row['missed_rate'])} ({row['missed']}/{row['expected_switches']}) | {number(row['false_detection_share'])} ({row['false_detections']}/{row['detected_saccades']}) | {'通过' if row['eligible'] else ', '.join(row['rejection_reasons'])} |")
    lines+=['','## 每轮覆盖与检测注视段','',
            '| 候选 / 任务 | 稳定目标/总目标 | 有效/总帧 | 角度帧/缺origin/非法origin | 注视覆盖 | 检测注视段 cm / ° SD（段数） | 90% 配对/总跳动 | 未到达：原始/滤波；无效跳动 |',
            '|---|---|---|---|---|---|---|---|']
    for i,row in enumerate(candidates):
        for r in row['trials']:
            lines.append(f"| {i+1} / {r['task_id']} | {r['stable_points']}/{r['expected_points']} | {r['stable_valid_frames']}/{r['stable_total_frames']} | {r['angle_frames']}/{r['missing_origin_frames']}/{r['invalid_origin_frames']} | {number(r['fixation_coverage'])} | {number(r['fixation_jitter_cm'])} / {number(r['fixation_jitter_deg'])} ({r['fixation_segments']}) | {r['paired_transitions']}/{r['expected_transitions']} | {r['raw_unreached']}/{r['filtered_unreached']}；{r['invalid_transitions']} |")
    lines+=['','## 待实机主观验证','',
            '- 使用推荐候选在 config/local.yaml 覆盖 filter/fixation，重启本地服务；重新九点校准或明确加载匹配档案。',
            '- 默认关闭的「对比显示」可显示绿=滤波、橙=修正后未滤波；记录稳定注视抖动、看向新位置的跟随感、停顿和异常跳动。',
            '- Mac 主观试用未验证；Windows 实机未验证。大纲中额外 2–3 人试用仍需真实反馈，不由本报告代填。','']
    output.write_text('\n'.join(lines),encoding='utf-8')
    # Numeric sidecar makes every plotted candidate and trial auditable.
    payload={k:v for k,v in result.items() if k!='tasks'}
    payload['tasks']=[dict(file=t.file,digest=t.digest,task_id=t.task_id,group=t.group,device_file=t.device_file,condition=t.condition,geometry=asdict(t.geometry)) for t in result['tasks']]
    payload.update(source_hashes=source_hashes,configuration=asdict(config),fixed_detector=asdict(detector))
    output.with_suffix('.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description='M2 数值跳点录制调参；不读取摄像头或图像')
    parser.add_argument('sessions',nargs='*',type=Path)
    parser.add_argument('--config',type=Path,default=ROOT/'config/filter_tuning.yaml')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/filter_tuning.md')
    parser.add_argument('--pending',action='store_true',help='无录制时写待实测报告，不生成成绩')
    args=parser.parse_args(argv)
    if not args.sessions and not args.pending:parser.error('请传入跳点录制文件，或使用 --pending')
    try:
        config=load_tuning_settings(args.config);settings=load_settings()
        tasks=[]
        for path in args.sessions:tasks.extend(read_jump_tasks(path))
        result=tune(tasks,config,settings.fixation)
        output=write_report(result,config,settings.fixation,args.output,args.sessions)
        print(f'报告：{output}')
        if result['recommended'] is None:
            print('没有合格的实测推荐；原因和待测清单见报告。',file=sys.stderr)
            return 0 if args.pending else 2
        return 0
    except (ValueError,TypeError,OSError,ReplayError,ImportError) as exc:
        print(f'调参失败：{exc}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
