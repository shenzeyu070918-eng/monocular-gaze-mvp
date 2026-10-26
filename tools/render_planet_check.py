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
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>三场景交互检查</title><style>body{background:#090e14;color:#cddbe7;font:15px system-ui;margin:20px}.row{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}figure{margin:0}img{width:100%}figcaption{padding:5px}</style><h1>三场景交互 · 合成输入</h1><p>同一时间 t=8秒、固定视角；只有推近改变镜头距离。独立Chrome/SwiftShader，不代表实机帧率。</p>'+''.join(rows)+'</html>'


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
                    if state=='zoom' and info['camera_distance']>=checks[-3]['camera_distance']*.9:raise RuntimeError('没有实际推近')
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
            (matrix/'index.html').write_text(interaction_html(),encoding='utf-8')
            (matrix/'manifest.json').write_text(json.dumps(dict(source='synthetic_swiftshader',hardware_fps_verified=False,checks=checks),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
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
                viewport=[1512,950],time_s=8,captures=captures,motion_checks=motion,interaction_checks=checks,preview_controls_ok=controls,preview_simulation_controls_ok=simulations,unexpected_events=events,script_errors=errors)
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
