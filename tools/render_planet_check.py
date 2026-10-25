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
            # Exercise the same in-page scene/view controls used by reviewers.
            controls=evaluate('''(()=>{document.querySelector('[data-scene="planet"]').click();
                const view=document.getElementById('preview-view');view.value='top';view.dispatchEvent(new Event('change'));
                return window.__galaxyPreview.scene==='planet'&&window.__galaxyPreview.view==='top';})()''')
            if not controls:raise RuntimeError('预览场景/镜头按钮切换失败')
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
                viewport=[1512,950],time_s=8,captures=captures,motion_checks=motion,preview_controls_ok=controls,unexpected_events=events,script_errors=errors)
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
