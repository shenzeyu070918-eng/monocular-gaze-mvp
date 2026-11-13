"""Self-contained Chinese report, projection-first layout and separate print styles."""
import base64,html,io,math,re
from PIL import Image,ImageDraw,ImageFont
from decimal import Decimal, ROUND_HALF_UP
from .data import public_data, display_size_cm
from .settings import ReportSettings
from gaze_core.attention.aoi import primary_regions
from gaze_core.attention.settings import AttentionSettings

PALETTE=dict(background='#f2f7fc',ink='#102f50',accent='#215e93',warning='#d78220',surface='#ffffff',muted='#4e657a')
STYLE='''
:root{--bg:#f2f7fc;--ink:#102f50;--accent:#215e93;--warn:#d78220;--paper:#fff;--muted:#4e657a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:22px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}main{max-width:1440px;margin:auto;padding:48px 64px}h1{font-size:48px;line-height:1.25;margin:12px 0 24px;letter-spacing:.03em}h2{font-size:32px;margin:48px 0 18px}h3{font-size:25px;margin:24px 0 12px}p{max-width:80ch}a{color:var(--accent)}.eyebrow{font-size:18px;letter-spacing:.12em;color:var(--accent)}.deck{color:var(--muted);max-width:72ch}.key{display:grid;grid-template-columns:repeat(3,1fr);gap:0;border-top:3px solid var(--accent);border-bottom:1px solid #b3c6d8;margin:32px 0}.fact{padding:22px 20px 22px 0;min-width:0}.fact b{display:block;font-size:42px;line-height:1.35;overflow-wrap:anywhere}.fact span{font-size:18px;color:var(--muted)}.visual{background:#fff;padding:16px;border:1px solid #c9d8e5}.visual img,.visual svg{display:block;width:100%;height:auto}figcaption{font-size:18px;color:var(--muted);margin-top:12px}figure{margin:18px 0 32px}table{border-collapse:collapse;width:100%;font-size:18px;background:var(--paper)}th,td{padding:12px 10px;border-bottom:1px solid #d5e1ec;text-align:left;vertical-align:top}th{background:#e6eff8}td:first-child{max-width:320px;overflow-wrap:anywhere}.tablewrap{overflow:auto}.note{border-left:4px solid var(--warn);padding:14px 20px;background:#fff7e9;font-size:19px}.meta{color:var(--muted);font-size:17px;overflow-wrap:anywhere}details{margin:18px 0}summary{cursor:pointer;color:var(--accent)}.timeline{width:100%;height:auto}button{font:inherit;padding:8px 20px}footer{border-top:1px solid #b3c6d8;margin-top:48px;padding-top:18px;font-size:17px;color:var(--muted)}.scene-overview{display:flex;gap:16px;overflow-x:auto;padding:8px 0 16px}.scene-thumb{display:block;flex:0 0 230px;color:var(--ink);text-decoration:none}.scene-thumb img{width:230px;height:138px;object-fit:contain;background:#e6eff8}.scene-thumb b,.scene-thumb small{display:block;font-size:16px}.scene-thumb small,.provenance{display:block;color:var(--muted);font-size:14px}.scene-analysis.portrait{display:grid;grid-template-columns:minmax(0,0.95fr) minmax(0,1.2fr);gap:24px;align-items:start}.scene-analysis.portrait .visual{margin-top:24px}.scene-analysis.portrait th{white-space:nowrap;font-size:16px}.scene-analysis .aoi-analysis{min-width:0}.scene-detail{border-top:1px solid #b3c6d8;padding-top:14px}.scene-detail>summary{font-size:24px;font-weight:600}.scene-detail>section>h2{margin-top:20px}@media(max-width:800px){.scene-analysis.portrait{display:block}main{padding:24px}h1{font-size:36px}.key{grid-template-columns:repeat(2,1fr)}.fact b{font-size:32px}}@media print{@page{size:A4;margin:14mm}body{background:#fff;font-size:10.5pt;line-height:1.5}main{padding:0;max-width:none}h1{font-size:25pt}h2{font-size:18pt;margin-top:20pt}h3{font-size:13pt}.key{grid-template-columns:repeat(3,1fr);margin:16px 0}.fact{padding:8px}.fact b{font-size:19pt}.fact span,figcaption,.note{font-size:10pt}table{font-size:9pt}th,td{padding:5px}.meta,footer{font-size:8pt}.visual{break-inside:avoid}figure{margin:10pt 0 18pt}.no-print{display:none}.scene-analysis.portrait{display:block}.scene-overview{flex-wrap:wrap;overflow:visible}.scene-thumb{flex:0 0 28%}.scene-thumb img{width:100%;height:80px}details::details-content{content-visibility:visible!important}.tablewrap{overflow:visible}details>*{display:block}h2,h3{break-after:avoid}tr{break-inside:avoid}}
'''


def esc(value):return html.escape(str(value),quote=True)
def fmt(value,suffix='',digits=2):return '未记录' if value is None else f'{float(value):.{digits}f}'+suffix
def pct(value):return fmt(None if value is None else value*100,'% ',1).strip()
def data_uri(raw,mime='image/png'):return f'data:{mime};base64,'+base64.b64encode(raw).decode('ascii')


def format_measure(value, unit):
    if value is None:
        return '未记录'
    number=Decimal(str(value))
    if unit=='ratio':
        return f"{(number*100).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)}%"
    suffix={'s':' 秒','count':' 次','cm':' cm','people':' 名','regions':' 个'}.get(unit,'')
    step='0.1' if unit=='cm' or (unit=='s' and abs(number)<10) else '1'
    if not suffix:
        return format(number.normalize(),'f')
    return str(number.quantize(Decimal(step),rounding=ROUND_HALF_UP))+suffix


_EVIDENCE_NAMES = {
    'hard_s':'硬归属停留', 'soft_s':'软归属停留',
    'hard_fraction':'硬归属占比', 'soft_fraction':'软归属占比',
    'first_fixation_s':'首次注视', 'entries':'进入次数',
    'N_seen':'看过人数', 'N_valid':'有效人数',
}
_DISPLAY_PLACEHOLDER = re.compile(r'\{\{([^{}]+)\}\}(\s*(?:次|秒钟?|厘米|cm|%|名))?')


def _evidence_names(data):
    names={
        'calibration.sigma_cm':('校准','误差核半径'),
        'calibration.sigma_x':('校准','水平归一化误差核'),
        'calibration.sigma_y':('校准','垂直归一化误差核'),
        'sample.N_total':('会话','参与人数'),
        'sample.N_valid':('会话','有效参与人数'),
    }
    for index,scene in enumerate(data['scenes'],1):
        prefix='scene.'+scene['id']
        names[prefix+'.valid_s']=(f'场景 {index}' if data['mode']=='screen' else '本图','有效注视')
        for region in scene['stats']['regions']:
            for field,label in _EVIDENCE_NAMES.items():
                names[f"{prefix}.{region['id']}.{field}"]=(region['label'],label)
    return names


def _short_references(text):
    return re.sub(r'区域\s*(「[^」]+」)',r'\1',text)


def _display_references(text,references):
    def replace(match):
        key=match[1]
        if key not in references:
            return match[0]
        if key.startswith('aoi:'):
            return '「'+references[key]+'」'
        return references[key]
    # Older responses sometimes wrap references in quotes or add an AOI prefix.
    text=re.sub(r'(?:区域\s*)?「(\{\{aoi:[^{}]+\}\})」',r'\1',text)
    text=re.sub(r'区域\s*(\{\{aoi:[^{}]+\}\})',r'\1',text)
    text=re.sub(r'(\{\{const\.sample_n\}\})\s*名',r'\1',text)
    return re.sub(r'\{\{((?:aoi:|const\.)[^{}]+)\}\}',replace,_short_references(text))


def _claim_html(claim,names,legacy=None,*,references=None):
    # Older ai.json files store evidence as display strings.
    if isinstance(claim,str):
        text,separator,tail=claim.rpartition('（')
        labels=tail[:-1].split('；')
        if separator and tail.endswith('）') and all(' = ' in part for part in labels):
            claim=dict(text=text,evidence_labels=labels)
        else:
            claim=dict(text=claim)
    refs=claim.get('evidence',[])
    if legacy is not None:
        refs=[legacy[label] for line in claim.get('evidence_labels',[])
              if (label:=line.rpartition(' = ')[0]) in legacy]
    evidence={ref['metric_id']:ref for ref in refs}
    units={key:('people' if key.rsplit('.',1)[-1] in ('N_total','N_valid','N_seen') else ref['unit'])
           for key,ref in evidence.items()}
    no_fixation={key for key,ref in evidence.items() if ref['value']==0 and
                 key.rsplit('.',1)[-1] in {'hard_s','soft_s','entries'}}
    values={key:'未记录到注视' if key in no_fixation else format_measure(ref['value'],units[key])
            for key,ref in evidence.items()}
    redundant={'s':('秒','秒钟'),'count':('次',),'cm':('cm','厘米'),'ratio':('%',),'people':('名',)}

    def replace(match):
        key,suffix=match.groups()
        if key not in values:
            return match[0]
        return values[key]+(suffix if suffix and suffix.strip() not in redundant.get(units[key],()) else '')

    body=_display_references(_DISPLAY_PLACEHOLDER.sub(replace,claim['text']),references or {})
    parts=['<p>'+esc(body)+'</p>']
    if evidence:
        labels=[]
        previous=None
        for key in evidence:
            group,name=names.get(key,('','统计值'))
            label=(group+' · ' if group and group!=previous else '')+name+('：' if key in no_fixation else ' ')+values[key]
            labels.append(f'<span title="{esc(key)}">{esc(label)}</span>')
            previous=group
        parts.append('<p class="meta">依据：'+'；'.join(labels)+'</p>')
    return ''.join(parts)


def _rect_size(rect):
    x0,y0,x1,y1 = rect
    return x1-x0,y1-y0


def _aoi_size_note(region, definitions, metadata):
    width,height = _rect_size(region['rect'])
    sizes = metadata.get('aggregate_display_sizes_cm', [display_size_cm(metadata)])
    known = [(width * w, height * h) for w,h in filter(None, sizes)]
    if not known:
        notes = ['物理尺寸未记录']
    else:
        axes = [fmt(min(axis)) if min(axis)==max(axis) else fmt(min(axis))+'–'+fmt(max(axis)) for axis in zip(*known)]
        notes = [' × '.join(axes)+' cm（宽 × 高）']
        if len(known)!=len(sizes):
            notes.append(f'{len(known)}/{len(sizes)} 份有实测尺寸')
    scale = metadata.get('attention', {}).get('aoi_min_sigma_scale', AttentionSettings.aoi_min_sigma_scale)
    kernels = metadata.get('aggregate_kernels', [metadata.get('kernel', {})])
    scales = metadata.get('aggregate_aoi_min_scales', [scale]*len(kernels))
    if any(width+1e-12 < k.get('sigma_x',0)*m or height+1e-12 < k.get('sigma_y',0)*m for k,m in zip(kernels, scales)):
        notes.append('小于误差尺度，区域结论不可靠')
    merged = region.get('merged_ids', [])
    if merged:
        if region.get('source')=='image_extent':
            notes.append('候选区域不足最小尺寸，扩展至整图')
        else:
            minimum = region.get('minimum_size')
            members = [definitions[k] for k in merged if k in definitions]
            if minimum and any(any(length+1e-12 < limit for length,limit in zip(_rect_size(r['rect']),minimum)) for r in members):
                notes.append('自动合并：候选区域低于误差尺度对应的最小尺寸')
            elif minimum and len(members)==len(merged):
                notes.append('自动合并：主区域数量上限')
            else:
                notes.append('自动合并：受最小尺寸或主区域数量上限约束')
    return ' · '.join(notes)


def scene_image(scene,options=None):
    options=options or ReportSettings();base=Image.open(io.BytesIO(scene['image'])).convert('RGBA');base.thumbnail((options.image_max_width,options.image_max_width*4))
    heat=Image.open(io.BytesIO(scene['heat'])).convert('RGBA').resize(base.size,Image.Resampling.BICUBIC)
    combined=Image.alpha_composite(base,heat)
    draw=ImageDraw.Draw(combined);w,h=combined.size
    for index,region in enumerate(primary_regions(scene['regions']),1):
        a,b,c,d=region['rect'];box=(round(a*w),round(b*h),round(c*w),round(d*h))
        draw.rectangle(box,outline=(30,93,144,210),width=max(1,w//700))
        size=max(18,round(w/(28 if h>w else 52)));font=ImageFont.load_default(size=size);text=str(index);bb=draw.textbbox((0,0),text,font=font);bw=bb[2]-bb[0]+12;bh=bb[3]-bb[1]+12
        bx=min(w-bw,box[0]);by=box[1]-bh-2 if box[1]>bh+2 else box[1]+2
        draw.rectangle((bx,by,bx+bw,by+bh),fill=(16,47,80,235));draw.text((bx+6,by+6-bb[1]),text,fill='white',font=font)
    output=io.BytesIO();combined.save(output,format='PNG');return output.getvalue()


def path_svg(scene,options=None):
    options=options or ReportSettings();image=Image.open(io.BytesIO(scene['image']));w,h=image.size
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="带序号的注视路径图">',f'<image width="{w}" height="{h}" href="{data_uri(scene["image"])}"/>']
    previous=None
    for p in scene['scanpath']:
        x,y=p['center'][0]*w,p['center'][1]*h;r=math.sqrt(p['duration_s'])*min(w,h)*.027
        if previous is not None and p['connect']:parts.append(f'<path d="M {previous[0]:.3f} {previous[1]:.3f} L {x:.3f} {y:.3f}" stroke="#ffae36" stroke-width="{max(2,w/600):.2f}" fill="none"/>')
        parts.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r:.3f}" fill="#143553" fill-opacity=".7" stroke="#ffbe5c" stroke-width="2"/><text x="{x:.3f}" y="{y:.3f}" text-anchor="middle" dominant-baseline="central" font-size="{max(12,min(w,h)*.025):.2f}" font-family="sans-serif" fill="white">{p["number"]}</text>');previous=(x,y)
    parts.append('</svg>');return ''.join(parts)


def cards(data):
    scenes=data['scenes'];rows=[(s,r) for s in scenes for r in s['stats']['regions']]
    first=min(((s,r) for s,r in rows if r.get('first_fixation_s') is not None),key=lambda p:p[0].get('start_s',0)+p[1]['first_fixation_s'],default=None)
    top=max(rows,key=lambda p:p[1].get('hard_s') or 0,default=None)
    facts=[('有效注视覆盖',format_measure(data['coverage'],'ratio'))]
    if data['kind']=='aggregate':facts.append(('匿名参与者',format_measure(data["N_total"],"people")+f'（有效 {format_measure(data["N_valid"],"people")}）'))
    else:facts.append(('会话时长',format_measure(data['summary']['duration_s'],'s')))
    facts.append((('平均首次注视最早的区域' if data['kind']=='aggregate' else '最先记录到注视的区域'),'无记录' if first is None else str(first[1].get('label',first[1]['id']))+' · '+format_measure(first[0].get('start_s',0)+first[1]['first_fixation_s'],'s')))
    facts.append(('停留最久的区域','无记录' if top is None or not top[1].get('hard_s') else str(top[1].get('label',top[1]['id']))+' · '+format_measure(top[1]['hard_s'],'s')))
    count_scene=max(scenes,key=lambda s:s['stats'].get('valid_fixation_s',0),default=None) if data['mode']=='screen' else None
    count_rows=count_scene['stats']['regions'] if count_scene is not None else [r for _,r in rows]
    label='未记录硬归属注视的主区域'+('（停留最久场景 '+count_scene['id']+'）' if count_scene is not None else '')
    facts.append((label,format_measure(sum(not r.get('hard_s') for r in count_rows),'regions')))
    return facts


def scene_overview(scenes,options):
    parts=['<h2>场景总览</h2><p class="meta">按有效注视时长排序；不足 '+fmt(options.scene_min_valid_s,' 秒')+' 的场景仅保留缩略图。</p><nav class="scene-overview" aria-label="场景缩略图总览">']
    for s in scenes:
        image=Image.open(io.BytesIO(s['image'])).convert('RGB');image.thumbnail((320,220));raw=io.BytesIO();image.save(raw,format='PNG');name=esc(s['id']);low=s['stats'].get('valid_fixation_s',0)<options.scene_min_valid_s
        tag='div' if low else 'a';parts.append('<'+tag+' class="scene-thumb"'+('' if low else ' href="#scene-'+name+'"')+'><img alt="场景 '+name+'" src="'+data_uri(raw.getvalue())+'"><b>场景 '+name+' · '+fmt(s['stats'].get('valid_fixation_s'),' 秒')+'</b><small>'+esc(s.get('app') or '应用名未记录')+(' · 仅总览' if low else '')+'</small></'+tag+'>')
    return ''.join(parts)+'</nav>'


def render_html(data,*,options=None,ai=None,ai_reason=None,generated_at='',generation_ms=None):
    options=options or ReportSettings();aggregate=data['kind']=='aggregate'
    title='刺激物注意力汇总' if aggregate else '刺激物注意力报告' if data['mode']=='stimulus' else '屏幕注意力报告'
    ordered_scenes=sorted(data['scenes'],key=lambda s:(-s['stats'].get('valid_fixation_s',0),s['id'])) if data['mode']=='screen' else data['scenes']
    deck=('记录的是这张图片上的块级注意力分配；热图和区域统计对应同一张图片。停留不代表理解或偏好。' if data['mode']=='stimulus' else '记录的是屏幕区域上的注意力分配。画面切换、滚动会让同一位置对应不同内容；停留不代表理解或偏好。')
    parts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src \'none\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">',f'<title>{title}</title><style>{STYLE}</style></head><body><main>',f'<div class="eyebrow">屏幕注意力分析助手 · 本地统计'+(' · 合成示例，非实测' if data['metadata'].get('synthetic') else '')+f'</div><h1>{title}</h1>',
           '<p class="deck">'+esc(deck)+'</p>',
           (scene_overview(ordered_scenes,options) if data['mode']=='screen' else '')+'<h2>关键结论</h2><section class="key">'+''.join(f'<div class="fact"><b>{esc(v)}</b><span>{esc(k)}</span></div>' for k,v in cards(data))+'</section>']
    if aggregate:parts.append(f'<p>匿名参与者 N_total = {data["N_total"]}；有效参与者 N_valid = {data["N_valid"]}。'+('仅一人数据，不作群体推断。' if data['N_total']==1 else '每人等权，不按会话数或注视时长加权。')+'</p>')
    else:parts.append(f'<p>模式：{("图片刺激物" if data["mode"]=="stimulus" else "屏幕")} · 匿名编号：{esc(data.get("participant_id") or "旧会话未记录")}<br>不确定区间占比：{pct(data["summary"].get("uncertain_fraction"))}。有效注视覆盖率 = 已绑定有效注视时长 ÷ 会话墙钟时长。</p>')
    kernel=data['metadata'].get('kernel',{});profile=data['metadata'].get('profile',{})
    parts.append(f'<p class="meta">九点校准：{esc(profile.get("created_at") or profile.get("calibration_id") or "未记录")} · 核尺度 σ = {esc(fmt(kernel.get("sigma_cm")," cm"))}，来源 {esc(kernel.get("source","未记录"))}。σ沿用M9核尺度；配置示意值不冒充实测精度。</p>')
    if aggregate:parts.append('<details><summary>每位参与者的核尺度</summary><p>'+esc(str(data['metadata'].get('aggregate_kernels',[])))+'</p></details>')
    expanded=0
    for s in ordered_scenes:
        screen=data['mode']=='screen'
        if screen and s['stats'].get('valid_fixation_s',0)<options.scene_min_valid_s:continue
        name=esc(s['id']);app=s.get('app') or ('应用名未记录' if screen else '图片刺激物')
        if screen:
            parts.append('<details class="scene-detail" id="scene-'+name+'"'+(' open' if expanded<options.scene_expand_count else '')+'><summary>场景 '+name+' · '+fmt(s['stats'].get('valid_fixation_s'),' 秒有效注视')+' · '+esc(app)+'</summary>');expanded+=1
        scene_title='场景 '+name if screen else esc(s.get('filename') or data['metadata'].get('stimulus',{}).get('filename') or '刺激图片（原文件名未记录）')
        parts.append(f'<section><h2>{scene_title}</h2><p class="meta">起点 {fmt(s.get("start_s")," 秒")} · {esc(app)}'+(' · 动态场景' if s.get('dynamic') else '')+'</p>')
        parts.append('<p class="meta">AOI 来源：'+('手动' if s.get('aoi_source')=='manual' else '自动')+' · 版本 '+esc(s.get('aoi_algorithm_version','legacy-flat-v1'))+'；只对主 AOI 排名，文字子 AOI 不重复计时。</p>')
        image_size=Image.open(io.BytesIO(s['image'])).size
        parts.append('<div class="scene-analysis'+(' portrait' if image_size[1]>image_size[0] else '')+'">')
        parts.append(f'<figure class="visual"><img alt="场景{ name }叠加热力图" src="{data_uri(scene_image(s,options))}"><figcaption>热量按注视时长累加，尺度沿用误差半径；细框编号对应下方AOI表，不表示逐行阅读。</figcaption></figure>')
        definitions={r['id']:r for r in s['regions']}
        region_numbers={r['id']:i for i,r in enumerate(primary_regions(s['regions']),1)}
        regions=sorted(s['stats']['regions'],key=lambda r:-(r.get('hard_s') or 0))
        parts.append('<div class="aoi-analysis"><h3>AOI 排名 · 硬归属与软归属</h3><div class="tablewrap"><table><thead><tr><th>区域</th><th>硬时长 / 占比</th><th>软时长 / 占比</th><th>首次注视</th><th>进入次数</th>'+('<th>看过人数 / 有效人数</th>' if aggregate else '')+'</tr></thead><tbody>')
        for r in regions:
            number=region_numbers.get(r['id'],'?');label=r.get('label',r['id']);display_label=label if label.startswith('区域 '+str(number)) else str(number)+' · '+label
            size_note=_aoi_size_note(definitions.get(r['id'],r),definitions,data['metadata'])
            parts.append('<tr><td>'+esc(display_label)+'<small class="provenance">'+('手动' if r.get('source')=='manual' else '自动')+'</small><small class="provenance">'+esc(size_note)+'</small></td><td>'+fmt(r.get('hard_s'),' s')+' / '+pct(r.get('hard_fraction'))+'</td><td>'+fmt(r.get('soft_s'),' s')+' / '+pct(r.get('soft_fraction'))+'</td><td>'+fmt(r.get('first_fixation_s'),' s')+'</td><td>'+fmt(r.get('entries'),digits=1 if aggregate else 0)+'</td>'+('<td>'+f'{r["N_seen"]} / {r["N_valid"]}（{pct(r["seen_fraction"])}）'+'</td>' if aggregate else '')+'</tr>')
        parts.append('</tbody></table></div><p class="meta">占比分母为有效注视时间，保留背景和屏外概率，不将AOI重新归一为100%。首次与进入次数来自硬归属；软归属是概率分摊。'+('多人列为每人平均；首次只对看过者求平均。' if aggregate else '')+'</p>')
        missing=[r.get('label',r['id']) for r in regions if not r.get('hard_s')]
        parts.append('<p>未记录硬归属注视的 AOI：'+esc('、'.join(missing) or '无')+'。</p>')
        parts.append('</div></div>')
        if not aggregate:
            parts.append('<h3>注视路径与顺序</h3><figure class="visual">'+path_svg(s,options)+f'<figcaption>按时间编号，圆面积表示停留时长；显示 {len(s["scanpath"])} / {s["scanpath_total"]} 个显示节点。跨排除、暂停、场景不连线；显示简化不改统计。</figcaption></figure>')
            order=[]
            for p in s['segments']:
                matches=[r for r in sorted(primary_regions(s['regions']),key=lambda r:(r.get('source')!='manual',(r['rect'][2]-r['rect'][0])*(r['rect'][3]-r['rect'][1]),r['id'])) if r['rect'][0]<=p['center'][0]<=r['rect'][2] and r['rect'][1]<=p['center'][1]<=r['rect'][3]]
                value=matches[0]['id'] if matches else '背景'
                if not order or order[-1]!=value:order.append(value)
            parts.append('<details><summary>完整区域顺序（连续相同区域合并）</summary><p>'+esc(' → '.join(order) or '无有效注视')+'</p></details>')
        parts.append('</section>'+('</details>' if screen else ''))
    if not aggregate:
        parts.append('<h2>状态时间线</h2><p class="meta">状态层可能重叠，不叠加作分母；位置按capture_ns对齐。未采样区间不能解释为在看。</p><div class="tablewrap"><table><tr><th>类型</th><th>起点 / 秒</th><th>终点 / 秒</th></tr>')
        for s in data['scenes']:parts.append('<tr><td>场景 '+esc(s['id'])+'</td><td>'+fmt(s['start_s'])+'</td><td>—</td></tr>')
        # Merge adjacent identical frame states for readable but lossless intervals.
        merged=[]
        for row in sorted(data['timeline'],key=lambda r:(r['kind'],r['start_s'])):
            if merged and merged[-1]['kind']==row['kind'] and abs(merged[-1]['end_s']-row['start_s'])<1e-8:merged[-1]=dict(merged[-1],end_s=row['end_s'])
            else:merged.append(dict(row))
        labels={'blink':'眨眼','no_face':'无人脸','outside':'离屏','user_paused':'用户暂停','excluded_app':'排除应用','transition_uncertain':'切换不确定','capture_gap':'采集断档','recording':'录制'}
        for row in sorted(merged,key=lambda r:r['start_s']):parts.append('<tr><td>'+esc(labels.get(row['kind'],row['kind']))+'</td><td>'+fmt(row['start_s'])+'</td><td>'+fmt(row['end_s'])+'</td></tr>')
        parts.append('</table></div><details><summary>M9状态时长原值</summary><pre>'+esc(str(data['summary'].get('frame_interval_state_s',{})))+'</pre></details>')
    parts.append('<h2>可选 AI 解读</h2>')
    if ai:
        if ai_reason:parts.append('<p class="note">最近一次AI请求：'+esc(ai_reason)+'</p>')
        evidence_names=_evidence_names(data)
        legacy=None
        if isinstance(ai['summary'],str):
            from .ai import registry
            metrics=registry(data,tuple(scene['id'] for scene in data['scenes']))
            legacy={m['label']:dict(metric_id=key,value=m['value'],unit=m['unit']) for key,m in metrics.items()}
        references=ai.get('references',{})
        if ai.get('summary_rejection'):
            parts.append('<p class="note">AI 摘要未通过校验，已替换为统计摘要。原因：'+esc(ai['summary_rejection'])+'。</p>')
        parts.append(_claim_html(ai['summary'],evidence_names,legacy,references=references))
        removed=ai.get('removed',[])
        if removed:
            reasons='、'.join(dict.fromkeys(item['reason'] for item in removed))
            parts.append(f'<p class="note">已移除 {len(removed)} 条未通过本地校验的内容。原因：'+esc(reasons)+'。'+esc(ai.get('diagnostic_error',''))+'</p>')
        for finding in ai['findings']:
            parts.append('<h3>'+esc(_display_references(finding['title'],references))+'</h3>'+_claim_html(finding,evidence_names,legacy,references=references))
        if ai['recommendations']:
            parts.append('<h3>面向设计者的建议</h3><ul>'+''.join('<li>'+_claim_html(r,evidence_names,legacy,references=references)+'</li>' for r in ai['recommendations'])+'</ul>')
        parts.append('<p>'+esc(ai['limitations'])+'</p>')
        prov=ai['provenance'];usage=prov['usage'];cost=prov['computed_usd']
        parts.append('<p class="meta">模型 '+esc(prov['model'])+' · '+esc(prov['time'])+' · 提示词 '+esc(prov['prompt_version'])+'<br>实际输入 / 输出 token：'+esc(str(usage['input_tokens']) if usage['input_tokens'] is not None else '未知')+' / '+esc(str(usage['output_tokens']) if usage['output_tokens'] is not None else '未知')+' · 费用估算（USD，非账单）：'+('未知' if cost is None else f'${cost:.6f}')+'<br>'+esc(prov['cost_definition'])+'</p><details><summary>发送与价格快照</summary><pre>'+esc(str(prov))+'</pre></details>')
    else:parts.append('<p>'+esc(ai_reason or '尚未请求AI解读。统计报告可完全离线使用。')+'</p><p class="meta">需要解读时，请回到控制面板点击「AI解读当前报告」，逐次预览并确认。此HTML没有网络连接能力，不含密钥。</p>')
    parts.append('<h2>数据质量与局限</h2><p class="note">'+esc(data['limitation'])+'</p>')
    parts.extend('<p class="note">'+esc(w)+'</p>' for w in data['warnings'])
    parts.append('<footer>会话 '+esc(data['session_id'])+' · 数据版本 '+esc(data['source_version'])+' · 生成 '+esc(generated_at)+'<br>报告文件保存在本机；AI发送状态见上节。移动此HTML即可离线查看。打印 / PDF使用独立样式。'+('本报告为合成数据测试，不是实测成绩。' if data['metadata'].get('synthetic') else '')+('<br>本次HTML构建 '+fmt(generation_ms,' ms')+'（不等于结束至浏览器呈现时间）' if generation_ms is not None else '')+'</footer></main></body></html>')
    return ''.join(parts)
