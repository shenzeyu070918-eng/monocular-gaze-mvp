"""Loopback-only known-layout fixture; no session files or screenshots served."""
import json,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import QObject,Signal
from gaze_core.attention.acceptance import layout,cpu_snapshot,cpu_difference
from gaze_core.galaxy_protocol import viewport


class AttentionGuide(QObject):
    message=Signal(object)
    def __init__(self,screen,parent=None):
        super().__init__(parent);self.screen=screen;self.token=secrets.token_urlsafe(32);self.baseline=None;self.running_start=None
        self._begin_lock=threading.Lock();self._stop=threading.Event();self._baseline_ready=threading.Event();owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,data,kind='application/json'):
                payload=data if isinstance(data,bytes) else json.dumps(data).encode()
                self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(payload)
            def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{owner.server.server_port}'
            def authorized(self):return self.valid_host() and self.headers.get('X-Attention-Token')==owner.token
            def do_GET(self):
                if not self.valid_host():self.reply(403,{});return
                if self.path=='/':
                    source=Path(__file__).resolve().parents[1]/'static/attention/guide.html'
                    self.reply(200,source.read_bytes(),'text/html; charset=utf-8');return
                if self.path=='/guide-logic.mjs':
                    source=Path(__file__).resolve().parents[1]/'static/attention/guide-logic.mjs'
                    self.reply(200,source.read_bytes(),'text/javascript; charset=utf-8');return
                if not self.authorized():self.reply(403,{});return
                if self.path=='/clock':self.reply(200,dict(time_ns=time.perf_counter_ns(),baseline_ready=owner._baseline_ready.is_set()));return
                self.reply(404,{})
            def do_POST(self):
                if not self.authorized() or self.headers.get('Origin')!=owner.origin:self.reply(403,{});return
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<8192:self.reply(413,{});return
                try:
                    data=json.loads(self.rfile.read(length))
                    if self.path=='/begin':
                        if not owner._baseline_ready.is_set():raise ValueError('CPU关闭基线仍在自动采集，请稍候')
                        mapping=viewport(owner.screen,data);rects=layout(owner.screen)
                        with owner._begin_lock:
                            if owner.running_start is not None:raise ValueError('此引导已开始，请重新打开引导')
                            owner.running_start=cpu_snapshot()
                        owner.message.emit(dict(kind='begin',cpu=owner.baseline))
                        origin=mapping.window_origin
                        client_rects=[]
                        for r in rects:
                            a,b,c,d=r['rect'];left,top=mapping.screen_to_window((a,b));right,bottom=mapping.screen_to_window((c,d))
                            client_rects.append(dict(r,client_rect=[left,top,right-left,bottom-top]))
                        self.reply(200,dict(rects=client_rects));return
                    if self.path!='/event' or owner.running_start is None:raise ValueError('请先开始引导')
                    if set(data)-{'kind','time_ns','id','phase'}:raise ValueError('不允许额外字段')
                    kind=data['kind'];stamp=data['time_ns']
                    if kind not in ('target','phase','end') or type(stamp) is not int or abs(stamp-time.perf_counter_ns())>5000000000:raise ValueError('事件时间/类型无效')
                    event=dict(type='acceptance',kind=kind,time_ns=stamp)
                    if kind=='target':
                        number=int(data['id'])
                        if not 1<=number<=8:raise ValueError('目标编号无效')
                        event.update(target_id=str(number),rect=layout(owner.screen)[number-1]['rect'])
                    if kind=='phase':
                        if data['phase'] not in ('scroll','tab','app','exclude','pause'):raise ValueError('阶段无效')
                        event['phase']=data['phase']
                    if kind=='end':event['cpu']=cpu_difference(owner.running_start,cpu_snapshot())
                    owner.message.emit(event);self.reply(200,{})
                except Exception as exc:self.reply(400,dict(error=str(exc)))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.url=self.origin+'/#'+self.token
        self.thread=threading.Thread(target=self.server.serve_forever,name='attention-guide-http',daemon=True);self.thread.start()
        def baseline():
            try:
                before=cpu_snapshot()
                if not self._stop.wait(30):self.baseline=cpu_difference(before,cpu_snapshot());self._baseline_ready.set()
            except Exception as exc:self.message.emit(dict(kind='error',error=str(exc)))
        self.baseline_thread=threading.Thread(target=baseline,name='attention-guide-baseline',daemon=True);self.baseline_thread.start()
    def close(self):
        self._stop.set();self.server.shutdown();self.server.server_close();self.thread.join(2);self.baseline_thread.join(3)
