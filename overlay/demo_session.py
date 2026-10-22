"""Demo ownership, filtered-result delivery and optional numeric recording."""
import time
from gaze_core.demo import DemoRun


class DemoSession:
    def __init__(self,session,overlay,panel,*,notice,window_factory=None,speech_factory=None,clock_ns=time.perf_counter_ns):
        self.session,self.overlay,self.panel,self.notice=session,overlay,panel,notice
        self.window_factory=window_factory;self.speech_factory=speech_factory;self.clock=clock_ns
        self.run=self.window=self.speech=None;self._recorded={};self._saved=None
        self._token=None;self._deferred=[]

    @property
    def active(self):return self.run is not None

    def _context(self):
        s=self.session
        return s.display_revision,s.profiles.conditions,getattr(s,'_active_group','A')

    def start(self,app,qscreen):
        s=self.session
        if self.active or s.active or s.profiles.busy or (s.clicks is not None and s.clicks.active):
            raise ValueError('请先结束当前任务或点击采集会话')
        group=getattr(s,'_active_group','A')
        if group not in {'B','C'} or s.profiles.conditions is None or (group=='B' and (s.profiles.profile or {}).get('pipeline')!='python'):
            raise ValueError('请先完成条件匹配的悬浮层九点校准或点击粗校准')
        if s.controller.snapshot().status!='running':raise ValueError('请先恢复摄像头')
        settings=s.settings;rect=self.overlay.rect_info
        radius=s.radius if s.radius is not None else settings.overlay.radius_cm
        radii=rect.radii(radius,settings.geometry,settings.overlay.fallback_radius_px)
        run=DemoRun(app,settings.demo,rect.width,rect.height,radii,group=group,stale_ms=settings.overlay.stale_ms)
        if self.window_factory is None:
            from .demo_window import DemoWindow
            factory=DemoWindow
        else:factory=self.window_factory
        window=factory(run,qscreen,settings.demo,clock_ns=self.clock)
        self._saved=(self.overlay.isVisible(),self.panel.isVisible());self._token=self._context()
        self.run,self.window=run,window;self._recorded={};self._deferred=[]
        window.cancelled.connect(self.cancel)
        try:
            s.set_demo_active(True)
            self.overlay.hide();self.panel.hide();window.open()
        except Exception:
            self.cancel('window_closed');raise

    def tick(self,prediction,display,now_ns,*,active,eligible):
        if not self.active:return
        if self._context()!=self._token:self.cancel('conditions_changed');return
        if not active:self.cancel('camera_paused');return
        run=self.run;run.advance(now_ns)
        if prediction is not None and eligible:
            point=None if display.position is None else self.overlay.rect_info.local(display.position)
            event=run.feed(point,prediction.captured_ns,now_ns,
                           valid=display.tracking and not self.panel.isVisible(),sequence=prediction.sequence)
            if event is not None and run.app=='board' and event['stage']=='application' and event['target_id']!='next':
                self._speak(run.feedback_text)
        if self.speech is not None and not run.speech_pending:self.speech.stop()
        self._events()
        point=None if display.position is None else self.overlay.rect_info.local(display.position)
        self.window.refresh(point,display.opacity,now_ns)

    def _speak(self,text):
        if self.speech is None:
            if self.speech_factory is None:
                from .speech import SystemSpeech
                factory=SystemSpeech
            else:factory=self.speech_factory
            try:
                self.speech=factory(self.window)
                self.speech.finished.connect(lambda ok:self.run.speech_finished(ok) if self.active else None)
            except Exception:
                self.run.speech_finished(False);return
        try:self.speech.speak(text)
        except Exception:self.run.speech_finished(False)

    def _events(self,*,final=False):
        recording=self.session.recording
        events,self._deferred=self._deferred+self.run.drain_events(),[]
        state=recording.status()
        if not state.get('active'):
            self._recorded.clear();return
        if state.get('preparing') and not final:
            self._deferred=events;return
        for kind,event in events:
            identity=state.get('session_id') or state.get('file')
            if kind=='demo_selection':
                ident=event['selection_id']
                if event['action']=='triggered':self._recorded[ident]=identity
                elif ident not in self._recorded or self._recorded.pop(ident)!=identity:continue
            recording.emit(kind,**event)

    def cancel(self,reason='escape',*,restore=True):
        if not self.active:return
        self.run.close(self.clock(),reason);self._events(final=True)
        undone=bool(self.run.selections and self.run.selections[-1]['false_activation'] is True)
        if self.speech is not None:self.speech.stop();self.speech=None
        self.window.dispose();self.window=None;self.run=None
        self.session.set_demo_active(False)
        if restore:
            self.overlay.setVisible(self._saved[0])
            if self._saved[1]:self.panel.present()
            else:self.panel.hide()
        self.notice('Demo 已结束'+('，最近一次选择已撤销' if reason=='escape' and undone else ''),False)
