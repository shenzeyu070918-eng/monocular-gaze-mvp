"""Capture only our local preview in an isolated headless Chrome profile."""
import argparse
import base64
from contextlib import ExitStack
import json
from hashlib import sha256
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

from gaze_core.config import ROOT
from overlay.galaxy_browser import chrome_executable


def comparison_html():
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>粒子行星 · 画面对照</title>
<style>body{margin:0;padding:28px;background:#080c12;color:#dce4ec;font:16px/1.5 system-ui,sans-serif}h1{font-size:26px;margin:0}p{color:#a1b1c1}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cell{background:#010203;padding:12px;min-width:0}.cell h2{font-size:16px;font-weight:500;margin:0 0 8px}.cell img{display:block;width:100%;height:auto}.crop{aspect-ratio:592/420;position:relative;overflow:hidden}.crop img{position:absolute;width:200%;left:0;top:-200%}</style>
<h1>粒子行星 · 第一阶段画面对照</h1><p>左：用户提供的视频拍屏；右：本地WebGL直接渲染。固定种子、t=8秒，无摄像头/眼动/手势输入。此图不用于帧率判断。</p>
<div class="grid"><div class="cell"><h2>参考：拼图左下帧，宽环斜视</h2><div class="crop"><img src="/reference/particle_planet_frames.png"></div></div>
<div class="cell"><h2>本次：金色体积球 + 青白吸积盘</h2><img src="/reference/render_check.png"></div>
<div class="cell"><h2>参考：球体近景</h2><img src="/reference/particle_planet_closeup.png"></div>
<div class="cell"><h2>本次：同一粒子场推近</h2><img src="/reference/render_closeup.png"></div>
<div class="cell"><h2>本次：螺旋星系（持续运动）</h2><img src="/reference/render_galaxy.png"></div>
<div class="cell"><h2>本次：实心文字（笔画内流动）</h2><img src="/reference/render_text.png"></div></div></html>'''


def interaction_html():
    rows=[]
    for scene,label in [('planet','粒子行星'),('galaxy','螺旋星系'),('text','文字')]:
        cells=''.join(f'<figure><img src="/render-check/{scene}-{state}.png"><figcaption>{title}</figcaption></figure>' for state,title in [('base','静止'),('scatter','张手甩散'),('well','视线引力井'),('zoom','凝视推近')])
        rows.append(f'<h2>{label}</h2><div class="row">{cells}</div>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>三场景交互检查</title><style>body{background:#090e14;color:#cddbe7;font:15px system-ui;margin:20px}.row{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}figure{margin:0}img{width:100%}figcaption{padding:5px}</style><h1>三场景交互 · 合成输入</h1><p>同一时间 t=8秒、固定视角；只有推近改变镜头距离。独立Chrome/SwiftShader，不代表实机帧率。</p>'+''.join(rows)+''.join(f'<h2>{label} · 2秒甩散→收回</h2><img width=756 src="/render-check/{scene}-scatter-return.gif"><p>0–650ms张手，其后握拳，10fps合成采样。</p><h2>四角及上下边缘 · 上排默认距离 / 下排最坏推近</h2><img width="100%" src="/render-check/{scene}-views.png">' for scene,label in [('galaxy','银河'),('text','文字')])+'<h2>银河长期稳定性 · 上排正面 / 下排斜视</h2><img src="/render-check/galaxy-times.png"><p>8/60/300/600秒；GPU实际顶点输出的螺距/角分布检查见galaxy-stability.json。</p><h2>银河默认 / 飞入银心 / 字母点亮</h2><div class="row"><img src="/render-check/galaxy-default.png"><img src="/render-check/galaxy-fly-in.png"><img src="/render-check/text-letter-focus.png"></div><h2>换景：旧场散开 → 新场汇聚</h2><img src="/render-check/assembly.gif"><h2>闭眼蓄力后打散（合成采集帧）</h2>'+''.join(f'<h3>{scene}</h3><img src="/render-check/{scene}-eye-burst.gif">' for scene in ('planet','galaxy','text'))+'<h2>场景内蓄力 · 同镜头0 / 50 / 100%</h2><img src="/render-check/charge-contact.png">'+'</html>'


def capture(url,output):
    from websockets.sync.client import connect
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('只允许本机预览服务器根地址，如 http://127.0.0.1:8768')
    output=Path(output).resolve()
    if not output.is_relative_to((ROOT/'data').resolve()):raise ValueError('输出必须在data/下')
    output.mkdir(parents=True,exist_ok=True)
    executable=chrome_executable()
    if executable is None:raise ValueError('需要安装Chrome才能生成离线对照图')
    events=[];errors=[];counter=0;process=None
    with tempfile.TemporaryDirectory(prefix='planet-visual-check-') as profile,ExitStack() as stack:
        try:
            process=subprocess.Popen([executable,'--headless=new','--no-first-run','--no-default-browser-check',
                '--disable-background-networking','--disable-background-mode','--enable-unsafe-swiftshader','--use-angle=swiftshader',
                '--remote-debugging-port=0','--user-data-dir='+profile,'--window-size=1512,950','--force-device-scale-factor=1',url+'/?preview&still&clean'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            active=Path(profile)/'DevToolsActivePort';deadline=time.monotonic()+30
            while not active.is_file():
                if process.poll() is not None:raise RuntimeError('Chrome提前退出')
                if time.monotonic()>deadline:raise TimeoutError('Chrome启动超时')
                time.sleep(.05)
            port=int(active.read_text().splitlines()[0])
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=5) as response:targets=json.load(response)
            target=next(t for t in targets if t.get('type')=='page')
            ws=stack.enter_context(connect(target['webSocketDebuggerUrl'],proxy=None,max_size=32*1024*1024,open_timeout=5))
            def command(method,params=None):
                nonlocal counter
                counter+=1;ident=counter;ws.send(json.dumps(dict(id=ident,method=method,params=params or {})))
                while True:
                    message=json.loads(ws.recv(timeout=30))
                    kind=message.get('method','')
                    if kind=='Network.webSocketCreated':events.append('unexpected_websocket')
                    if kind=='Runtime.exceptionThrown' or kind=='Runtime.consoleAPICalled' and message['params'].get('type')=='error':errors.append(message)
                    if message.get('id')==ident:
                        if 'error' in message:raise RuntimeError(str(message['error']))
                        return message.get('result',{})
            def evaluate(source):
                result=command('Runtime.evaluate',dict(expression=source,returnByValue=True,awaitPromise=True))
                if 'exceptionDetails' in result:raise RuntimeError(str(result['exceptionDetails']))
                return result.get('result',{}).get('value')
            command('Runtime.enable');command('Network.enable');command('Page.enable')
            command('Emulation.setDeviceMetricsOverride',dict(width=1512,height=950,deviceScaleFactor=1,mobile=False))
            captures=[];motion=[]
            for scene,view,name in [('planet','oblique','render_check.png'),('planet','close','render_closeup.png'),('galaxy','oblique','render_galaxy.png'),('text','oblique','render_text.png')]:
                target_url=url+f'/?preview&scene={scene}&view={view}&still&clean&t=8'
                command('Page.navigate',dict(url=target_url));deadline=time.monotonic()+40
                while not evaluate('location.href==='+json.dumps(target_url)+' && !!window.__galaxyPreview?.ready'):
                    if time.monotonic()>deadline:raise TimeoutError('预览未就绪：'+str(evaluate('document.getElementById("message").textContent')))
                    time.sleep(.05)
                evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                image=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                (output/name).write_bytes(image)
                evaluate('window.__galaxyPreview.freeze(9)')
                later=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                changed=sha256(image).digest()!=sha256(later).digest()
                motion.append(dict(scene=scene,view=view,times_s=[8,9],pixels_changed=changed))
                if not changed:raise RuntimeError('粒子静止：'+scene+'/'+view)
                captures.append(dict(scene=scene,view=view,file=name))
            # Matrix captures isolate each interaction at the same time/camera angles.
            from PIL import Image,ImageChops,ImageStat,ImageDraw
            import io
            matrix=output/'render_check';matrix.mkdir(exist_ok=True);checks=[]
            for scene in ('planet','galaxy','text'):
                base_image=None
                for state in ('base','scatter','well','zoom'):
                    info=evaluate(f'window.__galaxyPreview.snapshot({json.dumps(scene)},{json.dumps(state)},8)')
                    evaluate('new Promise(resolve=>requestAnimationFrame(resolve))')
                    png=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                    image=Image.open(io.BytesIO(png)).convert('RGB')
                    if base_image is None:base_image=image
                    diff=ImageChops.difference(base_image,image)
                    info['mean_absolute_pixel_difference']=sum(ImageStat.Stat(diff).mean)/3
                    info['pixels_changed']=diff.getbbox() is not None
                    if state!='base' and not info['pixels_changed']:raise RuntimeError(scene+'/'+state+'没有改变画面')
                    if state=='scatter' and info['scatter']<.9:raise RuntimeError('张手未传到GPU')
                    if state=='zoom' and (info['camera_distance']>=checks[-3]['camera_distance']-.01 or info['zoom']<.9):raise RuntimeError('没有实际推近')
                    if state=='well' and info['well_alpha']<.9:raise RuntimeError('引力井未传到GPU')
                    info['file']=f'{scene}-{state}.png';(matrix/info['file']).write_bytes(png);checks.append(info)
                # An opened hand must relax to the CURRENT moving shape at t=9.
                restored=evaluate(f'window.__galaxyPreview.snapshot({json.dumps(scene)},"restore",9)')
                restore=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                evaluate(f'window.__galaxyPreview.snapshot({json.dumps(scene)},"base",9)')
                baseline=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                difference=ImageChops.difference(Image.open(io.BytesIO(restore)).convert('RGB'),Image.open(io.BytesIO(baseline)).convert('RGB'))
                restored['mean_absolute_pixel_difference_from_current_base']=sum(ImageStat.Stat(difference).mean)/3
                if restored['scatter']>.000001 or restored['mean_absolute_pixel_difference_from_current_base']>.05:raise RuntimeError('握拳后没有回到当前动态形状')
                checks.append(restored)
            sheet=Image.new('RGB',(1600,840),'#090e14');labels=ImageDraw.Draw(sheet)
            for row,scene in enumerate(('planet','galaxy','text')):
                for col,state in enumerate(('base','scatter','well','zoom')):
                    tile=Image.open(matrix/f'{scene}-{state}.png');tile.thumbnail((400,251))
                    sheet.paste(tile,(col*400,row*280+24));labels.text((col*400+8,row*280+5),f'{scene} / {state}',fill='white')
            sheet.save(matrix/'contact-sheet.png')
            sequences=[];view_checks=[]
            for scene in ('galaxy','text'):
                frames=[];sequence_info=[]
                for ms in range(0,2001,100):
                    info=evaluate(f'window.__galaxyPreview.sequenceSample({json.dumps(scene)},{ms})');info['elapsed_ms']=ms;sequence_info.append(info)
                    png=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                    image=Image.open(io.BytesIO(png)).convert('RGB');image.thumbnail((756,475));frames.append(image)
                # One shared palette prevents palette flicker across the particle animation.
                atlas=Image.new('RGB',(756,475*len(frames)))
                for n,frame in enumerate(frames):atlas.paste(frame,(0,n*475))
                palette=atlas.quantize(colors=256)
                animation=[im.quantize(palette=palette,dither=Image.Dither.NONE) for im in frames[:-1]]
                animation[0].save(matrix/f'{scene}-scatter-return.gif',save_all=True,append_images=animation[1:],duration=100,loop=0,disposal=2,optimize=False)
                strip=Image.new('RGB',(5*504,340),'#090e14');labels=ImageDraw.Draw(strip)
                for n,index in enumerate((0,5,10,15,20)):
                    tile=frames[index].copy();tile.thumbnail((504,317));strip.paste(tile,(n*504,23));labels.text((n*504+8,5),f'{index/10:.1f}s',fill='white')
                strip.save(matrix/f'{scene}-scatter-return.png');sequences.append(dict(scene=scene,duration_ms=2000,open_until_ms=650,frames=sequence_info))
                # Rendering an identical time after another frame must not retain a trail.
                evaluate(f'window.__galaxyPreview.sequenceSample({json.dumps(scene)},500)')
                first=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                evaluate(f'window.__galaxyPreview.sequenceSample({json.dumps(scene)},1700)')
                evaluate(f'window.__galaxyPreview.sequenceSample({json.dumps(scene)},500)')
                repeated=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                history=ImageChops.difference(Image.open(io.BytesIO(first)).convert('RGB'),Image.open(io.BytesIO(repeated)).convert('RGB'))
                sequences[-1]['history_independent']=history.getbbox() is None
                if not sequences[-1]['history_independent']:raise RuntimeError('画面保留了历史帧拖影')
                view_sheet=Image.new('RGB',(6*378,2*262),'#090e14');labels=ImageDraw.Draw(view_sheet)
                for col,(edge,uv) in enumerate([('tl',[0,0]),('tr',[1,0]),('bl',[0,1]),('br',[1,1]),('top',[.5,0]),('bottom',[.5,1])]):
                    for row,(zoom_name,zoom) in enumerate([('far',0),('near',1)]):
                        info=evaluate(f'window.__galaxyPreview.inspectView({json.dumps(scene)},{json.dumps(uv)},{zoom})');info.update(edge=edge,gaze_uv=uv,forced_zoom=zoom)
                        if scene=='galaxy' and info['pose']['pitch']<20:raise RuntimeError('银河侧视')
                        if scene=='text' and any(max(abs(x),abs(y))>.900001 for x,y in info['text_projection']):raise RuntimeError('文字裁字')
                        png=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                        (matrix/f'{scene}-view-{edge}-{zoom_name}.png').write_bytes(png)
                        tile=Image.open(io.BytesIO(png)).convert('RGB');tile.thumbnail((378,238));view_sheet.paste(tile,(col*378,row*262+24));labels.text((col*378+7,row*262+5),f'{edge} / {zoom_name}',fill='white');view_checks.append(info)
                view_sheet.save(matrix/f'{scene}-views.png')
            # Inspect the ACTUAL production vertex shader with transform feedback.
            # Also test motionTime accelerated at the existing maximum hand spin.
            evaluate('window.__galaxyPreview.snapshot("galaxy","base",8)')
            accelerated=evaluate('fetch("/preview-config").then(r=>r.json()).then(o=>600*(1+o.interaction.spin_boost))')
            stability=evaluate(f'window.__galaxyPreview.galaxyCheck([8,60,300,600,{accelerated}])')
            (matrix/'galaxy-stability.json').write_text(json.dumps(stability,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            if not stability['passed']:raise RuntimeError('银河螺距/旋臂角分布随时间改变，见galaxy-stability.json')
            time_sheet=Image.new('RGB',(4*504,2*342),'#090e14');labels=ImageDraw.Draw(time_sheet)
            for row,view in enumerate(('face','oblique')):
                for col,t in enumerate((8,60,300,600)):
                    evaluate(f'window.__galaxyPreview.setView({json.dumps(view)});window.__galaxyPreview.freeze({t})')
                    png=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                    (matrix/f'galaxy-time-{t}-{view}.png').write_bytes(png)
                    if t==8:(matrix/f'galaxy-{view}.png').write_bytes(png)
                    tile=Image.open(io.BytesIO(png)).convert('RGB');tile.thumbnail((504,317));time_sheet.paste(tile,(col*504,row*342+25));labels.text((col*504+8,row*342+6),f'{view} / t={t}s',fill='white')
            time_sheet.save(matrix/'galaxy-times.png')
            charge_core=evaluate('window.__galaxyPreview.chargeCoreCheck()')
            charge_checks=[];charge_sheet=Image.new('RGB',(3*504,3*342),'#090e14');charge_labels=ImageDraw.Draw(charge_sheet)
            for row,scene in enumerate(('planet','galaxy','text')):
                base_charge=None
                for col,percent in enumerate((0,50,100)):
                    info=evaluate(f'window.__galaxyPreview.chargeSample({json.dumps(scene)},{percent/100})')
                    if abs(info['charge']-percent/100)>1e-9:raise RuntimeError('蓄力进度不一致')
                    png=base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data'])
                    if percent==0:base_charge=png
                    elif png==base_charge:raise RuntimeError('场景内蓄力没有改变画面')
                    (matrix/f'{scene}-charge-{percent}.png').write_bytes(png)
                    tile=Image.open(io.BytesIO(png)).convert('RGB');tile.thumbnail((504,317));charge_sheet.paste(tile,(col*504,row*342+25))
                    charge_labels.text((col*504+8,row*342+5),f'{scene} / {percent}%',fill='white');charge_checks.append(info)
            charge_sheet.save(matrix/'charge-contact.png')
            (matrix/'charge-checks.json').write_text(json.dumps(dict(source='synthetic_fixed_visual_progress',camera_and_time_fixed=True,core_gpu=charge_core,checks=charge_checks),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            active_checks=evaluate('window.__galaxyPreview.spotlightCheck()')
            # Dedicated gallery views for the stronger scene interactions.
            for state,name in [('base','galaxy-default.png'),('zoom','galaxy-fly-in.png')]:
                info=evaluate(f'window.__galaxyPreview.snapshot("galaxy",{json.dumps(state)},8)')
                (matrix/name).write_bytes(base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data']))
            letter=evaluate('window.__galaxyPreview.letterSample(0)')
            if letter['letter_selected']!=0 or letter['letter_weights'][0]<.99:raise RuntimeError('未整体选中字母')
            (matrix/'text-letter-focus.png').write_bytes(base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data']))
            def animation(name,rows,expression):
                frames=[];metadata=[]
                for ms,extra in rows:
                    metadata.append(evaluate(expression(ms,extra)))
                    image=Image.open(io.BytesIO(base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data']))).convert('RGB');image.thumbnail((756,475));frames.append(image)
                atlas=Image.new('RGB',(756,475*len(frames)))
                for n,frame in enumerate(frames):atlas.paste(frame,(0,n*475))
                palette=atlas.quantize(colors=256);gif=[im.quantize(palette=palette,dither=Image.Dither.NONE) for im in frames]
                gif[0].save(matrix/f'{name}.gif',save_all=True,append_images=gif[1:],duration=100,loop=0,disposal=2,optimize=False)
                indices=[0,len(frames)//4,len(frames)//2,len(frames)*3//4,len(frames)-1]
                strip=Image.new('RGB',(5*504,342),'#090e14');draw=ImageDraw.Draw(strip)
                for col,index in enumerate(indices):
                    tile=frames[index].copy();tile.thumbnail((504,317));strip.paste(tile,(col*504,25));draw.text((col*504+8,5),str(rows[index][0])+'ms',fill='white')
                strip.save(matrix/f'{name}.png');return metadata
            total=evaluate('fetch("/preview-config").then(r=>r.json()).then(o=>(o.transition_scatter_s+o.transition_s)*1000)')
            assembly=animation('assembly',[(t,None) for t in range(0,round(total)+101,100)],lambda t,_:f'window.__galaxyPreview.assemblySample("planet","galaxy",{t})')
            if any(r['scatter']>0 for r in assembly if r['transition']['busy']):raise RuntimeError('汇聚期间响应了手势')
            # The real Python detector supplies all charge/trigger fields; only pixels are synthetic.
            from types import SimpleNamespace
            from gaze_core.galaxy_burst import ClosedEyeBurst
            config=evaluate('fetch("/preview-config").then(r=>r.json())')
            detector=ClosedEyeBurst(SimpleNamespace(**config['interaction']));eye_rows=[]
            closed_end=100+((round(config['interaction']['blink_hold_ms'])+99)//100)*100
            eye_end=closed_end+100+round(config['interaction']['burst_duration_ms'])
            for ms in range(0,eye_end+100,100):
                detector.feed(ms*1_000_000,'blink' if 100<=ms<=closed_end else 'tracking',stale_ms=config['stale_ms'])
                eye_rows.append((ms,detector.snapshot(ms*1_000_000)))
            if detector.blink_count!=1:raise RuntimeError('真实采集时钟闭眼状态机没有只触发一次')
            bursts={}
            for scene in ('planet','galaxy','text'):
                bursts[scene]=animation(scene+'-eye-burst',eye_rows,lambda t,s:f'window.__galaxyPreview.eyeBurstSample({json.dumps(scene)},{t},{json.dumps(s)})')
                if max(r['scatter'] for r in bursts[scene])<.99 or bursts[scene][-1]['scatter']>1e-5:raise RuntimeError('闭眼包络没有完整打散/回收')
            combined=[]
            for scene in ('planet','galaxy','text'):
                for elapsed in (150,300):
                    row=evaluate(f'window.__galaxyPreview.combinedSample({json.dumps(scene)},{elapsed})');combined.append(row)
                    if abs(row['scatter']-max(row['hand_scatter'],row['burst_scatter']))>1e-8 or row['scatter']>1:raise RuntimeError('手势与闭眼打散发生叠加')
            active_report=dict(charge=charge_checks,combined=combined,spotlights=active_checks,letter=letter,assembly=assembly,bursts=bursts)
            (matrix/'active-checks.json').write_text(json.dumps(active_report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            (matrix/'index.html').write_text(interaction_html(),encoding='utf-8')
            (matrix/'manifest.json').write_text(json.dumps(dict(source='synthetic_swiftshader',hardware_fps_verified=False,checks=checks,sequences=sequences,view_checks=view_checks,galaxy_stability=stability,active_checks=active_report),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            # Exercise the same in-page scene/view controls used by reviewers.
            controls=evaluate('''(()=>{document.querySelector('[data-scene="planet"]').click();
                const view=document.getElementById('preview-view');view.value='top';view.dispatchEvent(new Event('change'));
                return window.__galaxyPreview.scene==='planet'&&window.__galaxyPreview.view==='top';})()''')
            if not controls:raise RuntimeError('预览场景/镜头按钮切换失败')
            evaluate("document.querySelector('[data-hand=\"open\"]').click()")
            deadline=time.monotonic()+10
            while not evaluate('window.__galaxyPreview.simulation.scatter>.4'):
                if time.monotonic()>deadline:raise TimeoutError('预览张手按钮没有生效')
                time.sleep(.05)
            simulations=evaluate("""(()=>{const api=window.__galaxyPreview;
                document.querySelector('[data-hand="fist"]').click();const fist=api.simulation.hand==='fist';
                dispatchEvent(new KeyboardEvent('keydown',{key:'o'}));const open=api.simulation.hand==='open';
                dispatchEvent(new KeyboardEvent('keydown',{key:'n'}));const none=api.simulation.hand==='none';
                document.querySelector('[data-gaze="center"]').click();const center=api.simulation.gaze==='center';
                dispatchEvent(new KeyboardEvent('keydown',{key:'g'}));const mouse=api.simulation.gaze==='mouse';
                document.getElementById('preview-reset').click();return fist&&open&&none&&center&&mouse&&!api.simulation.enabled;})()""")
            if not simulations:raise RuntimeError('预览视线/手势按钮或按键失败')
            (output/'render_comparison.html').write_text(comparison_html(),encoding='utf-8')
            # The server's reference route is deliberately limited to data/reference.
            if output==(ROOT/'data/reference').resolve() and all((output/n).is_file() for n in ('particle_planet_frames.png','particle_planet_closeup.png')):
                command('Page.navigate',dict(url=url+'/comparison'));deadline=time.monotonic()+15
                while not evaluate('location.pathname==="/comparison" && document.readyState==="complete"'):
                    if time.monotonic()>deadline:raise TimeoutError('对照页面未就绪')
                    time.sleep(.05)
                evaluate('Promise.all([...document.images].map(i=>i.decode().catch(()=>null)))')
                height=evaluate('document.documentElement.scrollHeight')
                (output/'render_comparison.png').write_bytes(base64.b64decode(command('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True,clip=dict(x=0,y=0,width=1512,height=height,scale=1)))['data']))
            report=dict(source='isolated_headless_swiftshader',real_camera_used=False,hardware_fps_verified=False,
                viewport=[1512,950],time_s=8,captures=captures,motion_checks=motion,interaction_checks=checks,sequence_checks=sequences,view_checks=view_checks,galaxy_stability=stability,active_checks=active_report,preview_controls_ok=controls,preview_simulation_controls_ok=simulations,unexpected_events=events,script_errors=errors)
            (output/'render_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            if errors or events:raise RuntimeError('渲染检查出现脚本/着色器错误或意外WebSocket，见render_check.json')
            print('画面与对照图：'+str(output),flush=True)
        finally:
            if process is not None:
                process.terminate()
                try:process.wait(5)
                except subprocess.TimeoutExpired:process.kill();process.wait()


def main(argv=None):
    parser=argparse.ArgumentParser(description='本地画面预览离线截图，不运行相机或眼动。')
    parser.add_argument('--url',default='http://127.0.0.1:8768');parser.add_argument('--output',type=Path,default=ROOT/'data/reference')
    args=parser.parse_args(argv);capture(args.url.rstrip('/'),args.output)


if __name__=='__main__':main()
