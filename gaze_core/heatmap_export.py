"""Export numeric heatmap snapshots to ignored data/heatmaps only."""
import csv
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
import uuid
import numpy as np
from .config import ROOT
from .heatmap import NOTICE,rgba_grid


def export_heatmap(snapshot,*,directory=None):
    root=(ROOT/'data/heatmaps').resolve()
    directory=Path(directory or root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex[:8])).resolve()
    if not directory.is_relative_to(root) or directory==root:raise ValueError('导出必须位于 data/heatmaps/ 的独立子目录内')
    directory.mkdir(parents=True,exist_ok=False)
    grid=np.array(snapshot['grid'],float,copy=True)
    if grid.ndim!=2 or not np.isfinite(grid).all() or (grid<0).any():raise ValueError('热图网格必须为二维非负有限数值')
    height,width=grid.shape
    with (directory/'grid.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(('column','row','x_normalized','y_normalized',snapshot['unit']))
        for y in range(height):
            for x in range(width):writer.writerow((x,y,(x+.5)/width,(y+.5)/height,float(grid[y,x])))
    metadata={key:value for key,value in snapshot.items() if key not in ('grid','total_grid','rgba')}
    metadata.update(grid_width=width,grid_height=height,grid_sum=float(grid.sum()),
                    statistics_note='区域停留时间为累计、不衰减；网格单位见 unit。',notice=NOTICE)
    (directory/'regions.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'gaze-matplotlib'))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    # Use a local font on the machine; never download fonts or capture a screen.
    from matplotlib import font_manager
    available={f.name for f in font_manager.fontManager.ttflist}
    font=next((name for name in ('PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Arial Unicode MS') if name in available),'DejaVu Sans')
    fig=Figure(figsize=(10,7),facecolor='#111827');FigureCanvasAgg(fig)
    ax=fig.add_axes((.08,.25,.78,.65));ax.set_facecolor('#111827')
    from matplotlib.colors import ListedColormap,Normalize
    from matplotlib.cm import ScalarMappable
    visual=snapshot.get('visualization',{})
    threshold=visual.get('alpha_threshold',.15);opacity=visual.get('max_opacity',.4)
    # Same warm, transparent scale as the live layer. No dark low-end overlay.
    colors=rgba_grid(np.linspace(0,1,256).reshape(1,-1),opacity,threshold)[0]/255
    cmap=ListedColormap(colors);peak=float(grid.max())
    norm=Normalize(vmin=0,vmax=peak if peak>0 else 1)
    ax.imshow(rgba_grid(grid,opacity,threshold),extent=(0,1,1,0),interpolation='bilinear',aspect='auto')
    image=ScalarMappable(norm=norm,cmap=cmap)
    for region in snapshot['regions']['regions']:
        from matplotlib.patches import Rectangle
        a,b,c,d=region['rect'];ax.add_patch(Rectangle((a,b),c-a,d-b,fill=False,edgecolor='#cbd5e1',linewidth=.6))
    ax.tick_params(colors='white');bar=fig.colorbar(image,ax=ax,fraction=.045,pad=.04)
    bar.set_label(snapshot['unit'],color='white');bar.ax.tick_params(colors='white')
    uncalibrated=snapshot.get('uncalibrated',False)
    coarse=snapshot.get('coarse_calibrated',False)
    title='屏幕区域热力图'+(' · 未校准' if uncalibrated else ' · 粗校准' if coarse else '')
    fig.text(.08,.95,title,color='white',fontfamily=font,fontsize=16)
    fig.text(.08,.16,'热力图表示「看了屏幕的哪个区域」，不表示「看了什么内容」。\n窗口切换和滚动会让同一位置对应不同内容。',
             color='white',fontfamily=font,fontsize=11)
    fig.text(.08,.07,'仅合成热图、区域网格和图例；不含屏幕或摄像头内容。\n区域统计为累计时间；衰减网格与累计统计分开记录。',color='#cbd5e1',fontfamily=font,fontsize=9)
    fig.savefig(directory/'heatmap.png',dpi=150,facecolor=fig.get_facecolor())
    lines=['# 屏幕区域热力图','',NOTICE,'',f"网格：{snapshot['unit']}；区域统计：累计、不衰减。",'',
           '核尺度统一为 sigma = 圆环误差半径；具体阶段及来源见 regions.json。',
           f'显示按当前最大热量归一化；低于其{threshold:.0%}完全透明，从阈值到2倍阈值用smoothstep平滑恢复正常透明度，黄色至红色，不透明度上限{opacity:g}。网格数值不受显示阈值影响。',
           '状态：'+('未校准' if uncalibrated else 'C 点击粗校准（可能含历史 B 阶段）；各阶段见 JSON。' if coarse else 'B 九点校准；匹配与历史对齐方式见 JSON。' if snapshot['fixation_count'] else '没有可用的校准注视段；原因见 JSON。'),'',
           '## 边缘容差与屏幕外时长','',
           '中心到屏幕最近点的距离不超过同源核尺度1 sigma时，仅在热图/区域统计中夹回边缘；超过才排除。原始坐标和注视检测不夹回。',
           '以下时长累计、不衰减；夹回时长已计入有效注视，不能重复相加。','',
           '| 分类 | 秒 |','|---|---:|',
           f"| 边缘容差内夹回的时长（edge_clamped_s） | {snapshot.get('edge_clamped_s',0.):.6f} |",
           f"| 真正屏幕外的时长（outside_s） | {snapshot['outside_s']:.6f} |",'',
           '## 区域统计','',
           '| 区域 | 停留秒 | 占比 | 首次注视秒 | 进入次数 | 注视段数 |','|---|---:|---:|---:|---:|---:|']
    def f(v):return '不可用' if v is None else f'{v:.6f}'
    for row in snapshot['regions']['regions']:
        lines.append(f"| {row['id']} | {f(row['dwell_s'])} | {f(row['dwell_fraction'])} | {f(row['first_fixation_s'])} | {row['entries']} | {row['fixations']} |")
    if snapshot.get('targets'):
        lines+=['','## 核对目标附近热量占比','',snapshot['target_note'],'',
                '| 任务 / 目标 | 全核对热量在该目标附近占比 | 该目标显示期间附近占比 | 有效注视秒 |','|---|---:|---:|---:|']
        for row in snapshot['targets']:
            lines.append(f"| {row['task_id']} / {row['target_id']} | {f(row['whole_heat_fraction'])} | {f(row['during_target_heat_fraction'])} | {f(row['during_target_fixation_s'])} |")
    if snapshot.get('alignment',{}).get('legacy_event_time'):
        lines+=['','旧录制缺矩阵版本号，按校准事件单调时间对齐，矩阵切换附近可能存在时间歧义。']
    lines+=['','未提供的精度或性能结果：未验证。','']
    (directory/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return directory
