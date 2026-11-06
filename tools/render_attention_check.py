"""Render only the generated synthetic poster/report, using isolated local Chrome."""
import argparse,base64,json,sys,tempfile,subprocess,time
from pathlib import Path
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import ROOT,load_settings
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import ReportArtifact
from gaze_core.reporting.editor import EditorBridge
from overlay.galaxy_browser import chrome_executable


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,default=ROOT/'data/report-preview/poster-check/latest.json');args=parser.parse_args(argv)
    source=args.input.resolve()
    if not source.is_relative_to((ROOT/'data').resolve()):parser.error('只读data/中的合成检查清单')
    info=json.loads(source.read_text(encoding='utf-8'));session=Path(info['session']).resolve();html=Path(info['report']).resolve();output=source.parent
    for p in (session,html):
        if not p.is_relative_to((ROOT/'data').resolve()):parser.error('合成检查路径越界')
    data=load_session(session)
    if not data['metadata'].get('synthetic'):parser.error('禁止本工具渲染个人真实截图；仅允许合成会话')
    options=load_settings(use_local=False).report;artifact=ReportArtifact(html.parent,data,html,options);executable=chrome_executable()
    if executable is None:raise RuntimeError('需要本机Chrome')
    from websockets.sync.client import connect
    errors=[];requests=[];bridge=None
    with tempfile.TemporaryDirectory(prefix='attention-synthetic-') as profile:
        process=subprocess.Popen([executable,'--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update','--disable-gpu','--user-data-dir='+profile,'--remote-debugging-port=0','--window-size=1920,1080','--force-device-scale-factor=1',html.as_uri()],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            active=Path(profile)/'DevToolsActivePort';deadline=time.monotonic()+15
            while not active.exists():
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Chrome未能启动')
                time.sleep(.05)
            port=int(active.read_text().splitlines()[0])
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=3) as r:target=next(t for t in json.load(r) if t['type']=='page')
            with connect(target['webSocketDebuggerUrl'],proxy=None,max_size=32*1024*1024) as ws:
                counter=0
                def command(method,params=None):
                    nonlocal counter
                    counter+=1;ident=counter;ws.send(json.dumps(dict(id=ident,method=method,params=params or {})))
                    while True:
                        response=json.loads(ws.recv(timeout=30))
                        if response.get('method')=='Runtime.exceptionThrown':errors.append(response['params'])
                        if response.get('method')=='Network.requestWillBeSent':requests.append(response['params']['request']['url'])
                        if response.get('id')==ident:
                            if 'error' in response:raise RuntimeError(str(response['error']))
                            return response.get('result',{})
                def evaluate(expression):
                    result=command('Runtime.evaluate',dict(expression=expression,returnByValue=True,awaitPromise=True))
                    if 'exceptionDetails' in result:raise RuntimeError(str(result['exceptionDetails']))
                    return result.get('result',{}).get('value')
                def wait(expression):
                    deadline=time.monotonic()+20
                    while not evaluate(expression):
                        if time.monotonic()>deadline:raise TimeoutError('合成页面未就绪')
                        time.sleep(.05)
                def screenshot(name):
                    evaluate("(()=>{let mark=document.getElementById('synthetic-check-mark');if(!mark){mark=document.createElement('div');mark.id='synthetic-check-mark';mark.textContent='合成示例 · 非实测';mark.style.cssText='position:fixed;right:20px;top:16px;background:#fff7e9;color:#102f50;border:1px solid #d78220;padding:8px 14px;font:18px system-ui;z-index:2147483647';document.body.append(mark);}})()")
                    evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');(output/name).write_bytes(base64.b64decode(command('Page.captureScreenshot',dict(format='png'))['data']))
                command('Runtime.enable');command('Network.enable');command('Page.enable');command('Emulation.setDeviceMetricsOverride',dict(width=1920,height=1080,deviceScaleFactor=1,mobile=False))
                command('Page.navigate',dict(url=html.as_uri()));wait("document.querySelector('.scene-analysis') && document.images.length>0 && Array.from(document.images).every(i=>i.complete)")
                screenshot('poster-report-summary.png');evaluate("window.scrollTo(0,document.querySelector('.scene-analysis').getBoundingClientRect().top+scrollY-110)");screenshot('poster-report-scene.png')
                downloads=output/'downloads';downloads.mkdir(exist_ok=True);command('Browser.setDownloadBehavior',dict(behavior='allow',downloadPath=str(downloads)))
                command('Page.navigate',dict(url=(html.parent/'aoi-editor.html').as_uri()));wait("window.__aoiEditor && document.getElementById('canvas').width===1200")
                evaluate("document.getElementById('download').click()")
                deadline=time.monotonic()+5
                while not list(downloads.glob('manual-aoi-*.json')):
                    if time.monotonic()>deadline:raise TimeoutError('单文件JSON下载失败')
                    time.sleep(.05)
                downloaded=json.loads(max(downloads.glob('manual-aoi-*.json'),key=lambda p:p.stat().st_mtime).read_text())
                if downloaded['stimulus_sha256']!=data['metadata']['stimulus']['sha256']:raise AssertionError('导出SHA256不一致')
                bridge=EditorBridge(artifact);command('Page.navigate',dict(url=bridge.url));wait("window.__aoiEditor && document.getElementById('canvas').width===1200")
                evaluate("document.getElementById('draw').click()")
                box=evaluate("(()=>{const r=document.getElementById('canvas').getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
                x,y=box['x']+.02*box['w'],box['y']+.01*box['h'];endx,endy=box['x']+.30*box['w'],box['y']+.045*box['h']
                command('Input.dispatchMouseEvent',dict(type='mousePressed',x=x,y=y,button='left',buttons=1,clickCount=1))
                command('Input.dispatchMouseEvent',dict(type='mouseMoved',x=endx,y=endy,button='left',buttons=1))
                command('Input.dispatchMouseEvent',dict(type='mouseReleased',x=endx,y=endy,button='left',buttons=0,clickCount=1))
                evaluate("(()=>{const i=document.getElementById('rename');i.value='海报标识';i.dispatchEvent(new Event('input',{bubbles:true}));})()")
                edit=evaluate('window.__aoiEditor.getDocument()')
                if edit['regions'][-1]['name']!='海报标识' or edit['regions'][-1]['rect'][2]<=edit['regions'][-1]['rect'][0]:raise AssertionError('浏览器拖框/改名失败')
                screenshot('poster-editor.png');evaluate("document.getElementById('rebuild').click()")
                wait("location.pathname.endsWith('/report') && document.querySelector('h1')?.textContent==='刺激物注意力报告'")
                if bridge.revision!=1:raise AssertionError('未完成一键重新生成')
                if not any(r['label']=='海报标识' for r in bridge.artifact.data['scenes'][0]['stats']['regions']):raise AssertionError('新报告未使用手动名称')
                command('Page.navigate',dict(url=bridge.url));wait("window.__aoiEditor && document.getElementById('canvas').width===1200");screenshot('poster-editor-saved.png')
                if errors:raise AssertionError('浏览器存在JS异常')
                if any(not u.startswith(('file:','data:','http://127.0.0.1:')) for u in requests):raise AssertionError('出现非本地请求')
                result=dict(synthetic=True,report=str(html),download_json=True,drag_and_rename=True,save_and_regenerate=True,js_exceptions=len(errors),external_requests=0)
                (output/'browser-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
        finally:
            if bridge is not None:bridge.close()
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    return 0


if __name__=='__main__':raise SystemExit(main())
