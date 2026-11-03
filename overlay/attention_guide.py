"""Loopback fixture, native-clock scheduling and independently observed actions."""
import json,os,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from PySide6.QtCore import QObject,Signal
from gaze_core.attention.acceptance import layout,cpu_snapshot,cpu_difference
from gaze_core.attention.guide import phase_at,instructions,foreground_block,native_action,browser_front,ActionSteps
from gaze_core.galaxy_protocol import viewport


def mac_foreground():
    from AppKit import NSWorkspace
    a=NSWorkspace.sharedWorkspace().frontmostApplication()
    if a is None:return None
    return dict(pid=int(a.processIdentifier()),app_name=str(a.localizedName() or ''),bundle_id=str(a.bundleIdentifier() or ''),own_ui=int(a.processIdentifier())==os.getpid())


class AttentionGuide(QObject):
    message=Signal(object)
    def __init__(self,screen,parent=None,*,action_s=4,excluded_app='备忘录',excluded_bundle='com.apple.Notes',excluded=(),pause_key='Control+Option+S',foreground=mac_foreground,stable_s=1.5,feedback_s=1.):
        super().__init__(parent)
        self.screen=screen;self.token=secrets.token_urlsafe(32);self.baseline=None;self.running_start=None;self.recording_cpu=None
        self.action_s=action_s;self.duration_s=None;self.excluded_app=excluded_app;self.exclude_target=(excluded_app,excluded_bundle);self.excluded=tuple(excluded);self.pause_key=pause_key;self.foreground=foreground
        self.requested=False;self.armed=False;self.started_ns=None;self.browser_pid=None;self.finished=False
        self.second_ready=False;self.primary_seen_ns=0;self.secondary_since=None;self._stage=None;self._hold=None;self._observed=set();self._wait_reason=''
        self._primary_scroll_fraction=0.;self._scroll_start_fraction=0.
        self.flow=ActionSteps(stable_s,feedback_s);self._browser={};self._step_browser_revision=None
        self._session=dict(paused=False,recording=False);self._cpu_before=None;self._cpu_revision=None;self._cpu_reason=''
        self._baseline_before=None;self._baseline_revision=None;self._baseline_elapsed_s=0.;self._last_instruction=None
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
                        if self.path=='/skip':
                            if set(data)!={'step_id'} or type(data['step_id']) is not int:raise ValueError('跳过步骤无效')
                            if not owner.skip(data['step_id']):raise ValueError('步骤已变化或当前不可跳过')
                            self.reply(200,{});return
                        if self.path=='/observe':
                            if set(data)-{'page','visible','scroll_fraction','revision','reason','target_visible','fullscreen'} or data.get('page') not in ('primary','secondary') or type(data.get('visible')) is not bool:raise ValueError('观察字段无效')
                            fraction=data.get('scroll_fraction',0)
                            if isinstance(fraction,bool) or not isinstance(fraction,(int,float)) or not 0<=fraction<=100:raise ValueError('滚动量无效')
                            if type(data.get('revision')) is not int or data['revision']<0 or data.get('reason') not in ('heartbeat','scroll','visibilitychange','load'):raise ValueError('浏览器动作来源无效')
                            if type(data.get('target_visible',False)) is not bool or type(data.get('fullscreen',False)) is not bool:raise ValueError('浏览器状态无效')
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
            while not self._stop.wait(.2):
                try:
                    with self._begin_lock:self.baseline_tick(time.perf_counter_ns())
                except Exception as exc:self.message.emit(dict(kind='error',error=str(exc)));return
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

    def _primary_ready(self,front,now):
        page=self._browser.get('primary',{})
        return (browser_front(front) and (self.browser_pid is None or front['pid']==self.browser_pid)
                and page.get('visible',False) and page.get('fullscreen',False) and now-page.get('seen_ns',0)<1500000000)

    def baseline_tick(self,now):
        if self.requested or self._baseline_ready.is_set():return
        front=self.foreground() if self.second_ready else None
        page=self._browser.get('primary',{});revision=page.get('revision')
        ready=self.second_ready and self._primary_ready(front,now)
        if not ready or revision!=self._baseline_revision:
            self._baseline_before=None;self._baseline_elapsed_s=0.;self._baseline_revision=revision
        if not ready:return
        if self._baseline_before is None:
            self.browser_pid=front['pid']
            self._baseline_before=cpu_snapshot();self.message.emit(dict(kind='baseline_started'))
        self._baseline_elapsed_s=max(0,(now-self._baseline_before['time_ns'])/1e9)
        if self._baseline_elapsed_s>=30:
            after=cpu_snapshot()
            self.baseline=dict(cpu_difference(self._baseline_before,after),window_start_ns=self._baseline_before['time_ns'],window_end_ns=after['time_ns'],action_free=True)
            self._baseline_ready.set()

    def update_session_state(self,paused,recording,excluded=False):
        # Immutable handoff: the Qt tick must not wait for native CPU sampling
        # or the HTTP lock. The clock worker owns all step transitions.
        self._session=dict(paused=bool(paused),recording=bool(recording),excluded=bool(excluded))

    def status(self):
        with self._begin_lock:
            now=time.perf_counter_ns();elapsed=None if self.started_ns is None else max(0,(now-self.started_ns)/1e9)
            step=self.flow.snapshot(now)
            stage=(dict(kind='waiting') if elapsed is None else dict(kind='target',id=int(elapsed//2)+1) if elapsed<16 else
                   dict(kind='cpu') if self.recording_cpu is None else dict(kind='end') if self.finished else dict(kind='action',**step))
            return dict(time_ns=now,baseline_ready=self._baseline_ready.is_set(),baseline_elapsed_s=self._baseline_elapsed_s,
                        second_ready=self.second_ready,requested=self.requested,elapsed_s=elapsed,stage=stage,
                        cpu_remaining_s=max(0,30-(now-self._cpu_before['time_ns'])/1e9) if self._cpu_before else 30,
                        cpu_reason=self._cpu_reason,wait_reason=self._wait_reason,finished=self.finished,
                        excluded_app=self.excluded_app,pause_key=self.pause_key,stable_s=self.flow.stable_ns/1e9)

    def observe_browser(self,data,now):
        page=data['page'];old=self._browser.get(page,{})
        if data['revision']<old.get('revision',-1):return
        # Keep the actual last scroll/visibilitychange time across heartbeats.
        changed=data['reason'] in ('scroll','visibilitychange') and data['revision']!=old.get('revision')
        self._browser[page]=dict(data,seen_ns=now,changed_ns=now if changed else old.get('changed_ns',now),
                                 change_reason=data['reason'] if changed else old.get('change_reason','load'))
        if page=='secondary':self.second_ready=True
        if page=='primary' and data['visible']:self.primary_seen_ns=now;self._primary_scroll_fraction=data.get('scroll_fraction',0)

    def skip(self,step_id):
        with self._begin_lock:
            result=self.flow.skip(time.perf_counter_ns(),step_id)
            self._emit_steps();return result

    def _emit_steps(self):
        for event in self.flow.drain():
            self.message.emit(dict(type='acceptance',**event))
            if event['kind']=='phase':
                page='primary' if event['phase']=='scroll' else 'secondary'
                self._step_browser_revision=self._browser.get(page,{}).get('revision',-1)

    def _instruction(self,text):
        if text!=self._last_instruction:
            self._last_instruction=text;self.message.emit(dict(kind='instruction',text=text))

    def tick(self,now):
        if not self.armed or self.finished:return
        front=self.foreground()
        if self.started_ns is None:
            reason=foreground_block(front)
            if not reason and not self._primary_ready(front,now):reason='请回到左侧全屏测试页，等待浏览器前台就绪'
            if reason:self._wait_reason=reason;self._instruction(reason);return
            self._wait_reason='';self.browser_pid=front['pid'];self.started_ns=now
            self.message.emit(dict(type='acceptance',kind='guide_protocol',time_ns=now,version=3,
                                   action_mode='self_paced',stable_s=self.flow.stable_ns/1e9,cpu_window_s=30,browser_app=front['app_name']))
        elapsed=(now-self.started_ns)/1e9
        if self.recording_cpu is None:
            page=self._browser.get('primary',{});revision=page.get('revision')
            ready=self._primary_ready(front,now) and self._session['recording'] and not self._session['paused']
            if not ready or revision!=self._cpu_revision:
                self._cpu_before=None;self._cpu_revision=revision;self._cpu_reason='需保持测试页前台、停止操作；中断后重测完整30秒'
            if ready:
                if self._cpu_before is None:self._cpu_before=cpu_snapshot();self._cpu_reason=''
                if now-self._cpu_before['time_ns']>=30000000000:
                    after=cpu_snapshot()
                    self.recording_cpu=dict(cpu_difference(self._cpu_before,after),window_start_ns=self._cpu_before['time_ns'],window_end_ns=after['time_ns'])
                    self.message.emit(dict(type='acceptance',kind='cpu_recording',time_ns=now,
                                           measurement=dict(self.recording_cpu,comparison_valid=True,action_free=True)))
        if elapsed<16:
            self._instruction('依次注视测试页8块中央十字，每块2秒');return
        if self.recording_cpu is None:
            remaining=30 if self._cpu_before is None else max(0,30-(now-self._cpu_before['time_ns'])/1e9)
            self._instruction(f'注视完成，请保持此页静止 · CPU测量剩余{remaining:.0f}秒');return
        self.flow.start(now)
        # Retain the actual tab completion time; restoring the primary page
        # is a UI prerequisite, not a second scene-detection timestamp.
        if self.flow.phase=='tab' and self.flow.feedback is not None and not self._primary_ready(front,now):
            self._instruction('请点回主测试标签，点击“恢复全屏并继续”');return
        self.flow.tick(now);self._emit_steps()
        if self.flow.finished:
            self.finished=True;self.message.emit(dict(kind='end',time_ns=now));return
        phase=self.flow.phase
        if self.flow.feedback is not None:
            self._instruction('✓ 完成' if self.flow.feedback=='completed' else '已跳过 · 记为未执行');return
        browser=front is not None and front.get('pid')==self.browser_pid
        if phase in ('scroll','tab'):
            page=self._browser.get('primary' if phase=='scroll' else 'secondary',{})
            expected='scroll' if phase=='scroll' else 'visibilitychange'
            condition=(browser and page.get('visible',False) and now-page.get('seen_ns',0)<1500000000
                       and page.get('revision',-1)>self._step_browser_revision and page.get('change_reason')==expected)
            if phase=='scroll':condition=condition and page.get('target_visible',False)
            self.flow.observe(phase,now,condition,page.get('revision'),expected,
                              observed_ns=page.get('changed_ns',now),app_name=(front or {}).get('app_name'))
        elif phase in ('app','exclude'):
            selected=self.exclude_target if phase=='exclude' else self.excluded
            condition=native_action(phase,front,self.browser_pid,selected)
            if phase=='exclude':condition=condition and self._session.get('excluded',False)
            self.flow.observe(phase,now,condition,(front or {}).get('app_name'),'native_foreground',app_name=(front or {}).get('app_name'))
        else:self.flow.pause_state(now,self._session['paused'],self._session['recording'])
        self._emit_steps()
        text=('✓ 完成' if self.flow.feedback=='completed' else
              '已恢复，请回测试页保持1.5秒' if phase=='pause' and self.flow.resume_ready and not self._session['paused'] and not self._session['recording'] else
              f'按 {self.pause_key} 恢复注意力会话' if phase=='pause' and self.flow.resume_ready else instructions(phase,self.excluded_app,self.pause_key))
        self._instruction(text)

    def close(self):
        self._stop.set();self.server.shutdown();self.server.server_close()
        self.thread.join(2);self.baseline_thread.join(3);self.timer_thread.join(3)
