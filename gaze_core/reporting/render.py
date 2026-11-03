"""Self-contained Chinese report, projection-first layout and separate print styles."""
import base64,html,io,math
from PIL import Image
from .data import public_data
from .settings import ReportSettings

PALETTE=dict(background='#f2f7fc',ink='#102f50',accent='#215e93',warning='#d78220',surface='#ffffff',muted='#4e657a')
STYLE='''
:root{--bg:#f2f7fc;--ink:#102f50;--accent:#215e93;--warn:#d78220;--paper:#fff;--muted:#4e657a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:22px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}main{max-width:1440px;margin:auto;padding:48px 64px}h1{font-size:48px;line-height:1.25;margin:12px 0 24px;letter-spacing:.03em}h2{font-size:32px;margin:48px 0 18px}h3{font-size:25px;margin:24px 0 12px}p{max-width:80ch}a{color:var(--accent)}.eyebrow{font-size:18px;letter-spacing:.12em;color:var(--accent)}.deck{color:var(--muted);max-width:72ch}.key{display:grid;grid-template-columns:repeat(3,1fr);gap:0;border-top:3px solid var(--accent);border-bottom:1px solid #b3c6d8;margin:32px 0}.fact{padding:22px 20px 22px 0;min-width:0}.fact b{display:block;font-size:42px;line-height:1.35;overflow-wrap:anywhere}.fact span{font-size:18px;color:var(--muted)}.visual{background:#fff;padding:16px;border:1px solid #c9d8e5}.visual img,.visual svg{display:block;width:100%;height:auto}figcaption{font-size:18px;color:var(--muted);margin-top:12px}figure{margin:18px 0 32px}table{border-collapse:collapse;width:100%;font-size:18px;background:var(--paper)}th,td{padding:12px 10px;border-bottom:1px solid #d5e1ec;text-align:left;vertical-align:top}th{background:#e6eff8}td:first-child{max-width:320px;overflow-wrap:anywhere}.tablewrap{overflow:auto}.note{border-left:4px solid var(--warn);padding:14px 20px;background:#fff7e9;font-size:19px}.meta{color:var(--muted);font-size:17px;overflow-wrap:anywhere}details{margin:18px 0}summary{cursor:pointer;color:var(--accent)}.timeline{width:100%;height:auto}button{font:inherit;padding:8px 20px}footer{border-top:1px solid #b3c6d8;margin-top:48px;padding-top:18px;font-size:17px;color:var(--muted)}@media(max-width:800px){main{padding:24px}h1{font-size:36px}.key{grid-template-columns:repeat(2,1fr)}.fact b{font-size:32px}}@media print{@page{size:A4;margin:14mm}body{background:#fff;font-size:10.5pt;line-height:1.5}main{padding:0;max-width:none}h1{font-size:25pt}h2{font-size:18pt;margin-top:20pt}h3{font-size:13pt}.key{grid-template-columns:repeat(3,1fr);margin:16px 0}.fact{padding:8px}.fact b{font-size:19pt}.fact span,figcaption,.note{font-size:10pt}table{font-size:9pt}th,td{padding:5px}.meta,footer{font-size:8pt}.visual{break-inside:avoid}figure{margin:10pt 0 18pt}.no-print{display:none}.tablewrap{overflow:visible}details>*{display:block}h2,h3{break-after:avoid}tr{break-inside:avoid}}
'''


def esc(value):return html.escape(str(value),quote=True)
def fmt(value,suffix='',digits=2):return '未记录' if value is None else f'{float(value):.{digits}f}'+suffix
def pct(value):return fmt(None if value is None else value*100,'% ',1).strip()
def data_uri(raw,mime='image/png'):return f'data:{mime};base64,'+base64.b64encode(raw).decode('ascii')


def scene_image(scene,options=None):
    options=options or ReportSettings();base=Image.open(io.BytesIO(scene['image'])).convert('RGBA');base.thumbnail((options.image_max_width,options.image_max_width*4))
    heat=Image.open(io.BytesIO(scene['heat'])).convert('RGBA').resize(base.size,Image.Resampling.BICUBIC)
    combined=Image.alpha_composite(base,heat);output=io.BytesIO();combined.save(output,format='PNG');return output.getvalue()


def path_svg(scene,options=None):
    options=options or ReportSettings();image=Image.open(io.BytesIO(scene['image']));w,h=image.size
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="带序号的注视路径图">',f'<image width="{w}" height="{h}" href="{data_uri(scene["image"])}"/>']
    previous=None
    for p in scene['scanpath']:
        x,y=p['center'][0]*w,p['center'][1]*h;r=max(4,math.sqrt(p['duration_s'])*min(w,h)*.027)
        if previous is not None and p['connect']:parts.append(f'<path d="M {previous[0]:.3f} {previous[1]:.3f} L {x:.3f} {y:.3f}" stroke="#ffae36" stroke-width="{max(2,w/600):.2f}" fill="none"/>')
        parts.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r:.3f}" fill="#143553" fill-opacity=".7" stroke="#ffbe5c" stroke-width="2"/><text x="{x:.3f}" y="{y:.3f}" text-anchor="middle" dominant-baseline="central" font-size="{max(12,min(w,h)*.025):.2f}" font-family="sans-serif" fill="white">{p["number"]}</text>');previous=(x,y)
    parts.append('</svg>');return ''.join(parts)


def cards(data):
    scenes=data['scenes'];rows=[(s,r) for s in scenes for r in s['stats']['regions']]
    first=min(((s,r) for s,r in rows if r.get('first_fixation_s') is not None),key=lambda p:p[0].get('start_s',0)+p[1]['first_fixation_s'],default=None)
    top=max(rows,key=lambda p:p[1].get('hard_s') or 0,default=None)
    facts=[('有效注视覆盖',pct(data['coverage']))]
    if data['kind']=='aggregate':facts.append(('匿名参与者',f'N = {data["N_total"]}（有效 {data["N_valid"]}）'))
    else:facts.append(('会话时长',fmt(data['summary']['duration_s'],' 秒',1)))
    facts.append(('最先记录到注视的区域','无记录' if first is None else str(first[1].get('label',first[1]['id']))[:24]+' · '+fmt(first[0].get('start_s',0)+first[1]['first_fixation_s'],' 秒',1)))
    facts.append(('停留最久的区域','无记录' if top is None or not top[1].get('hard_s') else str(top[1].get('label',top[1]['id']))[:24]+' · '+fmt(top[1]['hard_s'],' 秒',1)))
    facts.append(('未记录硬归属注视的区域',str(sum(not r.get('hard_s') for _,r in rows))+' 个'))
    return facts


def render_html(data,*,options=None,ai=None,ai_reason='尚未请求AI解读；统计报告可完全离线使用。',generated_at='',generation_ms=None):
    options=options or ReportSettings();aggregate=data['kind']=='aggregate'
    title='刺激物注意力汇总' if aggregate else '屏幕注意力报告'
    parts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src \'none\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">',f'<title>{title}</title><style>{STYLE}</style></head><body><main>',f'<div class="eyebrow">屏幕注意力分析助手 · 本地统计</div><h1>{title}</h1>',
           '<p class="deck">记录的是屏幕区域上的注意力分配。画面切换、滚动会让同一位置对应不同内容；停留不代表理解或偏好。</p>',
           '<h2>关键结论</h2><section class="key">'+''.join(f'<div class="fact"><b>{esc(v)}</b><span>{esc(k)}</span></div>' for k,v in cards(data))+'</section>']
    if aggregate:parts.append(f'<p>匿名参与者 N_total = {data["N_total"]}；有效参与者 N_valid = {data["N_valid"]}。'+('仅一人数据，不作群体推断。' if data['N_total']==1 else '每人等权，不按会话数或注视时长加权。')+'</p>')
    else:parts.append(f'<p>模式：{("图片刺激物" if data["mode"]=="stimulus" else "屏幕")} · 匿名编号：{esc(data.get("participant_id") or "旧会话未记录")}<br>不确定区间占比：{pct(data["summary"].get("uncertain_fraction"))}。有效注视覆盖率 = 已绑定有效注视时长 ÷ 会话墙钟时长。</p>')
    kernel=data['metadata'].get('kernel',{});profile=data['metadata'].get('profile',{})
    parts.append(f'<p class="meta">九点校准：{esc(profile.get("created_at") or profile.get("calibration_id") or "未记录")} · 核尺度 σ = {esc(fmt(kernel.get("sigma_cm")," cm"))}，来源 {esc(kernel.get("source","未记录"))}。σ沿用M9核尺度；配置示意值不冒充实测精度。</p>')
    if aggregate:parts.append('<details><summary>每位参与者的核尺度</summary><p>'+esc(str(data['metadata'].get('aggregate_kernels',[])))+'</p></details>')
    for s in data['scenes']:
        name=esc(s['id']);parts.append(f'<section><h2>场景 {name}</h2><p class="meta">起点 {fmt(s.get("start_s")," 秒")} · {esc(s.get("app") or "图片刺激物")}'+(' · 动态场景' if s.get('dynamic') else '')+'</p>')
        parts.append(f'<figure class="visual"><img alt="场景{ name }叠加热力图" src="{data_uri(scene_image(s,options))}"><figcaption>热量按注视时长累加，尺度沿用误差半径；不表示逐行阅读。</figcaption></figure>')
        regions=sorted(s['stats']['regions'],key=lambda r:-(r.get('hard_s') or 0))
        parts.append('<h3>AOI 排名 · 硬归属与软归属</h3><div class="tablewrap"><table><thead><tr><th>区域</th><th>硬时长 / 占比</th><th>软时长 / 占比</th><th>首次注视</th><th>进入次数</th>'+('<th>看过人数 / 有效人数</th>' if aggregate else '')+'</tr></thead><tbody>')
        for r in regions:
            parts.append('<tr><td>'+esc(r.get('label',r['id']))+'</td><td>'+fmt(r.get('hard_s'),' s')+' / '+pct(r.get('hard_fraction'))+'</td><td>'+fmt(r.get('soft_s'),' s')+' / '+pct(r.get('soft_fraction'))+'</td><td>'+fmt(r.get('first_fixation_s'),' s')+'</td><td>'+fmt(r.get('entries'),digits=1 if aggregate else 0)+'</td>'+('<td>'+f'{r["N_seen"]} / {r["N_valid"]}（{pct(r["seen_fraction"])}）'+'</td>' if aggregate else '')+'</tr>')
        parts.append('</tbody></table></div><p class="meta">占比分母为有效注视时间，保留背景和屏外概率，不将AOI重新归一为100%。首次与进入次数来自硬归属；软归属是概率分摊。'+('多人列为每人平均；首次只对看过者求平均。' if aggregate else '')+'</p>')
        missing=[r.get('label',r['id']) for r in regions if not r.get('hard_s')]
        parts.append('<p>未记录硬归属注视的 AOI：'+esc('、'.join(missing) or '无')+'。</p>')
        if not aggregate:
            parts.append('<h3>注视路径与顺序</h3><figure class="visual">'+path_svg(s,options)+f'<figcaption>按时间编号，圆面积表示停留时长；显示 {len(s["scanpath"])} / {s["scanpath_total"]} 个显示节点。跨排除、暂停、场景不连线；显示简化不改统计。</figcaption></figure>')
            order=[]
            for p in s['segments']:
                matches=[r for r in sorted(s['regions'],key=lambda r:(r.get('source')!='manual',(r['rect'][2]-r['rect'][0])*(r['rect'][3]-r['rect'][1]),r['id'])) if r['rect'][0]<=p['center'][0]<=r['rect'][2] and r['rect'][1]<=p['center'][1]<=r['rect'][3]]
                value=matches[0]['id'] if matches else '背景'
                if not order or order[-1]!=value:order.append(value)
            parts.append('<details><summary>完整区域顺序（连续相同区域合并）</summary><p>'+esc(' → '.join(order) or '无有效注视')+'</p></details>')
        parts.append('</section>')
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
        parts.append('<p>'+esc(ai['summary'])+'</p>')
        for finding in ai['findings']:parts.append('<h3>'+esc(finding['title'])+'</h3><p>'+esc(finding['text'])+'</p><p class="meta">证据：'+esc('；'.join(finding['evidence_labels']))+'</p>')
        parts.append('<h3>面向设计者的建议</h3><ul>'+''.join('<li>'+esc(r)+'</li>' for r in ai['recommendations'])+'</ul><p>'+esc(ai['limitations'])+'</p><p class="meta">'+esc(str(ai['provenance']))+'</p>')
    else:parts.append('<p>'+esc(ai_reason)+'</p><p class="meta">需要解读时，请回到控制面板点击「AI解读当前报告」，逐次预览并确认。此HTML没有网络连接能力，不含密钥。</p>')
    parts.append('<h2>数据质量与局限</h2><p class="note">'+esc(data['limitation'])+'</p>')
    parts.extend('<p class="note">'+esc(w)+'</p>' for w in data['warnings'])
    parts.append('<footer>会话 '+esc(data['session_id'])+' · 数据版本 '+esc(data['source_version'])+' · 生成 '+esc(generated_at)+'<br>仅本机保存；移动此HTML即可离线查看。打印 / PDF使用独立样式。'+('本报告为合成数据测试，不是实测成绩。' if data['metadata'].get('synthetic') else '')+('<br>本次HTML构建 '+fmt(generation_ms,' ms')+'（不等于结束至浏览器呈现时间）' if generation_ms is not None else '')+'</footer></main></body></html>')
    return ''.join(parts)
