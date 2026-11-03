"""Loopback fixture, native-clock scheduling and independently observed actions."""
import json,os,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from PySide6.QtCore import QObject,Signal
from gaze_core.attention.acceptance import layout,cpu_snapshot,cpu_difference
from gaze_core.attention.guide import phase_at,phase_start,instructions,foreground_block,native_action,browser_front
from gaze_core.galaxy_protocol import viewport


def mac_foreground():
    from AppKit import NSWorkspace
    a=NSWorkspace.sharedWorkspace().frontmostApplication()
    if a is None:return None
    return dict(pid=int(a.processIdentifier()),app_name=str(a.localizedName() or ''),bundle_id=str(a.bundleIdentifier() or ''),own_ui=int(a.processIdentifier())==os.getpid())


class AttentionGuide(QObject):
    message=Signal(object)
    def __init__(self,screen,parent=None,*,action_s=4,excluded_app='备忘录',excluded_bundle='com.apple.Notes',excluded=(),pause_key='Control+Option+S',foreground=mac_foreground):
        super().__init__(parent)
        self.screen=screen;self.token=secrets.token_urlsafe(32);self.baseline=None;self.running_start=None;self.recording_cpu=None
        self.action_s=action_s;self.duration_s=16+5*action_s;self.excluded_app=excluded_app;self.exclude_target=(excluded_app,excluded_bundle);self.excluded=tuple(excluded);self.pause_key=pause_key;self.foreground=foreground
        self.requested=False;self.armed=False;self.started_ns=None;self.browser_pid=None;self.finished=False
        self.second_ready=False;self.primary_seen_ns=0;self.secondary_since=None;self._stage=None;self._hold=None;self._observed=set();self._wait_reason=''
        self._primary_scroll_fraction=0.;self._scroll_start_fraction=0.
        self._begin_lock=threading.RLock();self._stop=threading.Event();self._baseline_ready=threading.Event();owner=self
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
                public={'/':'guide.html','/second':'second.html','/guide-logic.mjs':'guide-logic.mjs'}
                if self.path in public:
                    source=Path(__file__).resolve().parents[1]/'static/attention'/public[self.path]
                    self.reply(200,source.read_bytes(),'text/javascript; charset=utf-8' if self.path.endswith('.mjs') else 'text/html; charset=utf-8');return
                if not self.authorized():self.reply(403,{});return
                if self.path=='/clock':self.reply(200,owner.status());return
                self.reply(404,{})
            def do_POST(self):
                if not self.authorized() or self.headers.get('Origin')!=owner.origin:self.reply(403,{});return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<8192:self.reply(413,{});return
                    data=json.loads(self.rfile.read(length))
                    if not isinstance(data,dict):raise ValueError('请求必须是对象')
                    with owner._begin_lock:
                        if self.path=='/begin':
                            if not owner._baseline_ready.is_set():raise ValueError('CPU关闭基线仍在自动采集，请稍候')
                            if not owner.second_ready:raise ValueError('请先打开右侧第二个测试标签，再回本页开始')
                            if owner.requested:raise ValueError('此引导已开始，请重新打开引导')
                            mapping=viewport(owner.screen,data);rects=layout(owner.screen)
                            client_rects=[]
                            for r in rects:
                                a,b,c,d=r['rect'];left,top=mapping.screen_to_window((a,b));right,bottom=mapping.screen_to_window((c,d))
                                client_rects.append(dict(r,client_rect=[left,top,right-left,bottom-top]))
                            owner.requested=True;owner.primary_seen_ns=time.perf_counter_ns()
                            front=owner.foreground()
                            if browser_front(front):owner.browser_pid=front['pid']
                            owner.message.emit(dict(kind='begin',cpu=owner.baseline))
                            self.reply(200,dict(rects=client_rects));return
                        if self.path=='/observe':
                            if set(data)-{'page','visible','scroll_fraction'} or data.get('page') not in ('primary','secondary') or type(data.get('visible')) is not bool:raise ValueError('观察字段无效')
                            fraction=data.get('scroll_fraction',0)
                            if isinstance(fraction,bool) or not isinstance(fraction,(int,float)) or not 0<=fraction<=100:raise ValueError('滚动量无效')
                            owner.observe_browser(data,time.perf_counter_ns());self.reply(200,{});return
                        if self.path!='/event' or owner.started_ns is None:raise ValueError('请先开始引导')
                        if set(data)-{'kind','time_ns','id'}:raise ValueError('不允许额外字段')
                        stamp=data['time_ns']
                        if data['kind']!='target' or type(stamp) is not int or abs(stamp-time.perf_counter_ns())>5000000000:raise ValueError('事件时间/类型无效')
                        number=data['id']
                        if type(number) is not int or not 1<=number<=8:raise ValueError('目标编号无效')
                        expected=phase_at(max(0,(stamp-owner.started_ns)/1e9),owner.action_s)
                        if expected!=dict(kind='target',id=number):raise ValueError('目标不在当前注视阶段')
                        if ('target',number) not in owner._observed:
                            owner._observed.add(('target',number))
                            owner.message.emit(dict(type='acceptance',kind='target',time_ns=stamp,target_id=str(number),rect=layout(owner.screen)[number-1]['rect']))
                        self.reply(200,{})
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
        def schedule():
            while not self._stop.wait(.1):
                try:
                    with self._begin_lock:self.tick(time.perf_counter_ns())
                except Exception as exc:
                    self.message.emit(dict(kind='error',error=str(exc)));return
        self.timer_thread=threading.Thread(target=schedule,name='attention-guide-clock',daemon=True);self.timer_thread.start()

    def arm(self):
        with self._begin_lock:self.armed=True

    def status(self):
        with self._begin_lock:
            now=time.perf_counter_ns()
            return dict(time_ns=now,baseline_ready=self._baseline_ready.is_set(),second_ready=self.second_ready,requested=self.requested,
                        elapsed_s=None if self.started_ns is None else max(0,(now-self.started_ns)/1e9),duration_s=self.duration_s,
                        action_s=self.action_s,wait_reason=self._wait_reason,finished=self.finished,excluded_app=self.excluded_app,pause_key=self.pause_key)

    def observe_browser(self,data,now):
        page=data['page'];visible=data['visible']
        if page=='secondary':self.second_ready=True
        if page=='primary' and visible:self.primary_seen_ns=now;self._primary_scroll_fraction=data.get('scroll_fraction',0)
        if self.started_ns is None or self.finished:return
        phase=phase_at((now-self.started_ns)/1e9,self.action_s).get('phase')
        front=self.foreground();browser=front is not None and front.get('pid')==self.browser_pid
        if page=='primary' and visible and browser and phase=='scroll' and self._primary_scroll_fraction-self._scroll_start_fraction>=.5:self.action('scroll',now,'browser_scroll_half_screen')
        if page=='secondary':
            if phase=='tab' and visible and browser:
                if self.secondary_since is None:self.secondary_since=now
                if now-self.secondary_since>=1500000000:self.action('tab',self.secondary_since,'secondary_tab_visible_1.5s')
            else:self.secondary_since=None

    def action(self,phase,now,source):
        if phase in self._observed:return
        self._observed.add(phase);self.message.emit(dict(type='acceptance',kind='action',phase=phase,time_ns=now,source=source))

    def tick(self,now):
        if not self.armed or self.finished:return
        front=self.foreground()
        if self.started_ns is None:
            reason=foreground_block(front)
            if not reason and (not browser_front(front) or self.browser_pid is not None and front['pid']!=self.browser_pid):reason='请回到左侧测试页，让浏览器保持前台后开始'
            if not reason and now-self.primary_seen_ns>1500000000:reason='请回到左侧测试标签，等待测试页可见后开始'
            if reason:
                if reason!=self._wait_reason:self.message.emit(dict(kind='instruction',text=reason))
                self._wait_reason=reason;return
            self._wait_reason='';self.browser_pid=front['pid'];self.running_start=cpu_snapshot();self.started_ns=now
            self.message.emit(dict(type='acceptance',kind='guide_protocol',time_ns=now,version=2,action_s=self.action_s,duration_s=self.duration_s,cpu_window_s=30,browser_app=front['app_name']))
            self.message.emit(dict(kind='instruction',text='依次注视测试页8块中央十字，每块2秒'))
        elapsed=(now-self.started_ns)/1e9;stage=phase_at(elapsed,self.action_s)
        if self.recording_cpu is None and elapsed>=30:self.recording_cpu=cpu_difference(self.running_start,cpu_snapshot())
        if stage.get('kind')=='end':
            self.finished=True
            self.message.emit(dict(kind='end',time_ns=now,cpu=self.recording_cpu or cpu_difference(self.running_start,cpu_snapshot())));return
        if stage!=self._stage:
            self._stage=stage;self._hold=None;self.secondary_since=None
            if stage.get('phase')=='scroll':self._scroll_start_fraction=self._primary_scroll_fraction
            if stage['kind']=='phase':self.message.emit(dict(type='acceptance',kind='phase',phase=stage['phase'],time_ns=now))
        phase=stage.get('phase')
        if phase:
            hold=(phase,front.get('pid') if front else None)
            selected_excluded=self.exclude_target if phase=='exclude' else self.excluded
            if native_action(phase,front,self.browser_pid,selected_excluded):
                if self._hold is None or self._hold[0]!=hold:self._hold=(hold,now)
                if now-self._hold[1]>=1500000000:self.action(phase,self._hold[1],'native_foreground_1.5s')
            else:self._hold=None
            text=instructions(phase,self.excluded_app,self.pause_key)
            remaining=max(0,phase_start(phase,self.action_s)+self.action_s-elapsed)
            self.message.emit(dict(kind='instruction',text=text+f' · {remaining:.0f}s'))

    def close(self):
        self._stop.set();self.server.shutdown();self.server.server_close()
        self.thread.join(2);self.baseline_thread.join(3);self.timer_thread.join(3)
