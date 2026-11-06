"""Standalone offline AOI editor plus an ephemeral authenticated local rebuild helper."""
from dataclasses import replace
from datetime import datetime,timezone
import hashlib,hmac,json,secrets,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from gaze_core.attention.aoi import primary_regions
from gaze_core.attention.manual_aoi import select_document,save_template
from .render import data_uri


def render_editor(data,*,options,endpoint=None,token=None):
    from gaze_core.config import ROOT
    if data['kind']=='aggregate':raise ValueError('请先编辑任一单会话；同图模板会在重新汇总时复用')
    if not data['scenes']:
        return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>编辑关注区域</title><h1>暂无可编辑的场景图</h1><p>本会话未保存场景，统计报告仍可查看。</p></html>'
    scenes=[]
    for scene in data['scenes']:
        labels={r['id']:r['label'] for r in scene['stats']['regions']}
        rows=[dict(id=r['id'],rect=r['rect'],name=r.get('name') or labels.get(r['id'],'区域 '+str(i))) for i,r in enumerate(primary_regions(scene['regions']),1)]
        scenes.append(dict(id=scene['id'],image=data_uri(scene['image']),regions=rows))
    bootstrap=dict(meta=dict(mode=data['mode'],session_id=data['session_id'],stimulus_sha256=data['metadata'].get('stimulus',{}).get('sha256')),scenes=scenes,endpoint=endpoint,token=token)
    raw=json.dumps(bootstrap,ensure_ascii=False,allow_nan=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    root=ROOT/'static/aoi-editor';page=(root/'editor.html').read_text(encoding='utf-8');logic=(root/'logic.mjs').read_text(encoding='utf-8').replace('export const ','const ').replace('export function ','function ')
    page=page.replace('__BOOTSTRAP__',raw).replace('__LOGIC__',logic)
    connect="'self'"+(' '+urlsplit(endpoint).scheme+'://'+urlsplit(endpoint).netloc if endpoint else '')
    policy=f"default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src {connect}; base-uri 'none'; form-action 'none'"
    return page.replace('<title>',f'<meta http-equiv="Content-Security-Policy" content="{policy}"><title>',1)


def rebuild(artifact,document,*,share_template=False,cancelled=lambda:False):
    """Save rectangles, create a new derived version and report; originals immutable."""
    from gaze_core.attention.storage import SessionStore
    from gaze_core.attention.settings import AttentionSettings
    from gaze_core.attention.processing import process_session
    from .data import load_session
    from .build import build
    if artifact.data['kind']!='session':raise ValueError('请先选择单个会话编辑')
    if not isinstance(document,dict) or set(document)-{'schema_version','coordinate_space','stimulus_sha256','regions','scenes','template_scope'}:raise ValueError('只接受手动AOI坐标与名称，不接受图片或任意字段')
    m=artifact.data['metadata'];known={s['id'] for s in artifact.data['scenes']}
    if not known:raise ValueError('本会话没有可编辑场景')
    if 'scenes' in document and (not isinstance(document['scenes'],dict) or set(document['scenes'])!=known):raise ValueError('场景集合与当前报告不一致')
    selected_scenes={}
    from gaze_core.attention.settings import AttentionSettings
    maximum=AttentionSettings(**m.get('attention',{})).aoi_max_primary
    for name in known:
        original=document.get('regions') if 'regions' in document else document.get('scenes',{}).get(name)
        if not isinstance(original,list) or any(not isinstance(r,dict) or set(r)-{'id','name','rect'} for r in original):raise ValueError('AOI只接受id、name、rect，不接受图像或任意字段')
        selected=select_document(document,name,m)
        if selected is not None and len(selected)>maximum:raise ValueError(f'每场景最多{maximum}个主AOI，请在编辑器中合并相邻区域')
        if selected is None:raise ValueError('请为每个场景提供区域列表（可为空）')
        selected_scenes[name]=[dict(id=r['id'],name=r['name'],rect=r['rect']) for r in selected]
    document=dict(schema_version=2,coordinate_space='image_normalized' if m['mode']=='stimulus' else 'full_screen_normalized',**(dict(stimulus_sha256=m['stimulus']['sha256'],regions=next(iter(selected_scenes.values()))) if m['mode']=='stimulus' else dict(scenes=selected_scenes)))
    if type(share_template) is not bool:raise ValueError('模板选项必须为布尔值')
    if cancelled():raise InterruptedError('编辑已关闭')
    root=Path(artifact.data['source_root']);store=SessionStore.existing(root,m.get('attention',{}).get('max_disk_mb',1024)*1024*1024)
    version=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-aoi-'+uuid.uuid4().hex[:8];prefix='derived/'+version+'/'
    try:
        document=dict(document,template_scope='shared' if share_template else 'session')
        store.write_json(prefix+'manual-aoi.json',document)
        options=replace(AttentionSettings(**m.get('attention',{})),manual_aoi=str(root/(prefix+'manual-aoi.json')))
        if m['mode']=='stimulus' and share_template:save_template(store.root,m['stimulus']['sha256'],select_document(document,next(iter(known)),m))
        process_session(store,options,reocr=False,prefix=prefix,cancelled=cancelled)
        result=build(load_session(root,derived=prefix.rstrip('/'),options=artifact.options),options=artifact.options,cancelled=cancelled)
        return result
    finally:store.close()


class EditorBridge:
    def __init__(self,artifact):
        from .build import atomic
        self.artifact=artifact;self.token=secrets.token_urlsafe(32);self.cancel=threading.Event();self.lock=threading.Lock();self.last_activity=time.monotonic();self._closed=False;self.revision=0;self.saved_editor=artifact.directory/'aoi-editor.html'
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def authenticated(self,post=False):
                if urlsplit(self.path).path.split('/')[1:2]!=[owner.token]:return False
                origin=self.headers.get('Origin')
                if origin not in (None,'null',owner.origin):return False
                if post and not hmac.compare_digest(self.headers.get('X-AOI-Token',''),owner.token):return False
                owner.last_activity=time.monotonic();return not owner.cancel.is_set()
            def respond(self,status,raw,mime='application/json; charset=utf-8'):
                self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff')
                origin=self.headers.get('Origin')
                if origin in ('null',owner.origin):self.send_header('Access-Control-Allow-Origin',origin)
                self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_OPTIONS(self):
                if not self.authenticated():self.respond(403,b'{}');return
                self.send_response(204);self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin',owner.origin));self.send_header('Access-Control-Allow-Headers','Content-Type, X-AOI-Token');self.send_header('Access-Control-Allow-Methods','POST');self.end_headers()
            def do_GET(self):
                if not self.authenticated():self.respond(403,b'{}');return
                path=urlsplit(self.path).path
                if path==owner.prefix+'/editor':
                    page=render_editor(owner.artifact.data,options=owner.artifact.options,endpoint=owner.endpoint,token=owner.token);self.respond(200,page.encode(),'text/html; charset=utf-8')
                elif path==owner.prefix+'/report':self.respond(200,owner.artifact.html.read_bytes(),'text/html; charset=utf-8')
                else:self.respond(404,b'{}')
            def do_POST(self):
                if not self.authenticated(post=True):self.respond(403,b'{}');return
                if urlsplit(self.path).path!=owner.prefix+'/save':self.respond(404,b'{}');return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=1024*1024:raise ValueError('编辑数据超出大小限制')
                    payload=json.loads(self.rfile.read(length))
                    if not isinstance(payload,dict) or set(payload)!={'document','save_template'}:raise ValueError('编辑数据格式无效')
                    if not owner.lock.acquire(blocking=False):self.respond(409,json.dumps(dict(error='正在重新生成，请稍候'),ensure_ascii=False).encode());return
                    try:
                        owner.artifact=rebuild(owner.artifact,payload['document'],share_template=payload['save_template'],cancelled=owner.cancel.is_set);owner.revision+=1
                    finally:owner.lock.release()
                    self.respond(200,json.dumps(dict(report_url=owner.endpoint+'/report',derived=owner.artifact.data['source_version']),ensure_ascii=False).encode())
                except (ValueError,KeyError,TypeError) as exc:self.respond(400,json.dumps(dict(error=str(exc)),ensure_ascii=False).encode())
                except Exception:self.respond(500,json.dumps(dict(error='本机保存或重新生成失败；原会话未改写，请检查磁盘和整理结果'),ensure_ascii=False).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.origin='http://127.0.0.1:'+str(self.server.server_port);self.prefix='/'+self.token;self.endpoint=self.origin+self.prefix;self.url=self.endpoint+'/editor'
        try:
            atomic(self.saved_editor,render_editor(artifact.data,options=artifact.options,endpoint=self.endpoint,token=self.token).encode())
            self.thread=threading.Thread(target=self.server.serve_forever,name='aoi-editor-local',daemon=True);self.thread.start()
            self.watch=threading.Thread(target=self._watch,name='aoi-editor-idle',daemon=True);self.watch.start()
        except BaseException:self.server.server_close();raise
    def _watch(self):
        while not self.cancel.wait(1):
            if not self.lock.locked() and time.monotonic()-self.last_activity>self.artifact.options.editor_idle_s:self.close();return
    def close(self):
        if self._closed:return
        self._closed=True;self.cancel.set();self.server.shutdown();self.server.server_close();self.thread.join(timeout=1)
