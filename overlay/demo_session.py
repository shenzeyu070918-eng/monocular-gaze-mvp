"""Demo ownership, filtered-result delivery and optional numeric recording."""
import time
from gaze_core.demo import DemoRun


class DemoSession:
    def __init__(self,session,overlay,panel,*,notice,window_factory=None,speech_factory=None,clock_ns=time.perf_counter_ns,debug=False):
        self.session,self.overlay,self.panel,self.notice=session,overlay,panel,notice
        self.window_factory=window_factory;self.speech_factory=speech_factory;self.clock=clock_ns
        self.run=self.window=self.speech=None;self._recorded={};self._saved=None
        self._token=None;self._deferred=[];self.debug=debug;self._mapping_reason=None
        self.drift=None;self._mapping=None
        self._viewport=None;self._coordinate_blocked_since=None

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
        if app=='stars':
            from .star_run import StarDemoRun
            run=StarDemoRun(settings.demo,rect.width,rect.height,radii,group=group,settings=settings.starfield,
                            clock_ns=self.clock,stale_ms=settings.overlay.stale_ms)
        else:run=DemoRun(app,settings.demo,rect.width,rect.height,radii,group=group,stale_ms=settings.overlay.stale_ms)
        from gaze_core.demo_drift import DemoDrift,DriftSettings
        o=settings.demo;geometry=settings.geometry
        self.drift=DemoDrift(DriftSettings(o.practice_window_ms,o.practice_min_samples,o.practice_min_span_ms,o.practice_max_correction_cm),
                             width_cm=geometry.width_cm,height_cm=geometry.height_cm,measured=geometry.measured)
        run.practice_gate=self._practice_gate
        if self.window_factory is None:
            from .demo_window import DemoWindow
            factory=DemoWindow
        else:factory=self.window_factory
        try:window=factory(run,qscreen,settings.demo,clock_ns=self.clock,screen_rect=rect,debug=self.debug)
        except Exception:
            run.close(self.clock(),'window_closed');self.drift=None;raise
        self._saved=(self.overlay.isVisible(),self.panel.isVisible());self._token=self._context()
        self.run,self.window=run,window;self._recorded={};self._deferred=[]
        self._viewport=None;self._coordinate_blocked_since=None
        run.prepare_coordinates=self._prepare_coordinates
        self._practice_status('等待看着练习按钮中间的点' if settings.demo.practice else '未做本次 Demo 漂移修正')
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
        run=self.run
        if run.app=='stars' and run.worker.error:
            self.notice(run.worker.error,True);self.cancel('prediction_error');return
        mapping=self.window.coordinates()
        coordinate_ready=self._prepare_coordinates(mapping,now_ns)
        run.advance(now_ns)
        if prediction is not None and eligible:
            valid=display.tracking and coordinate_ready and not self.panel.isVisible()
            if run.phase=='practice':
                self.drift.observe(display.position,prediction.captured_ns,valid=valid,now_ns=now_ns)
            corrected=self.drift.apply(display.position)
            point=None if corrected is None else mapping.hit_position(corrected)
            event=run.feed(point,prediction.captured_ns,now_ns,
                           valid=valid,sequence=prediction.sequence)
            if event is not None and run.app=='board' and event['stage']=='application' and event['target_id']!='next':
                self._speak(run.feedback_text)
        if self.speech is not None and not run.speech_pending:self.speech.stop()
        self._events()
        before=None if display.position is None or mapping.window_origin is None else mapping.screen_to_window(display.position)
        corrected=self.drift.apply(display.position)
        point=None if corrected is None or mapping.window_origin is None else mapping.screen_to_window(corrected)
        self.window.refresh(point,display.opacity,now_ns,before_position=before)

    def _prepare_coordinates(self,mapping,now_ns):
        """Used by first paint AND frame delivery, before any target is dated."""
        self._mapping=mapping
        reason=mapping.reason
        if mapping.ready:
            signature=(mapping.window_origin,mapping.window_size)
            try:
                changed=self._viewport is not None and signature!=self._viewport
                if changed and self.run.application_sampled:
                    raise ValueError('Demo 可见区域已变化，请按 Esc 退出后重新进入')
                resized=self.run.resize_viewport(*mapping.window_size,now_ns)
                if resized or changed:
                    old=self.drift
                    self.drift=type(old)(old.settings,width_cm=old.width_cm,height_cm=old.height_cm,measured=old.measured)
                    if changed and self.run.options.practice:self.run.repractice(now_ns)
                self.run.target_to_screen=mapping.window_to_screen
                self._viewport=signature
            except ValueError as exc:reason=str(exc)
        ready=mapping.ready and not reason
        self.window.mapping_reason=reason
        if reason!=self._mapping_reason:
            self._mapping_reason=reason
            if reason:self.notice('Demo 坐标暂不可用：'+reason,False)
        if ready:
            self._coordinate_blocked_since=None;self.window.coordinate_notice=''
        else:
            if self._coordinate_blocked_since is None:self._coordinate_blocked_since=now_ns
            delay=self.session.settings.overlay.coordinate_warning_s*1e9
            self.window.coordinate_notice=('坐标暂不可用：'+reason+' · Esc 退出') if now_ns-self._coordinate_blocked_since>=delay else ''
        return ready

    def _practice_status(self,text):
        if hasattr(self.panel,'demo_status'):self.panel.demo_status.setText('Demo 练习修正：'+text)

    def _practice_gate(self,button,event):
        x,y,w,h=button.rect
        target=self._mapping.window_to_screen((x+w/2,y+h/2))
        result=self.drift.estimate(target,event['capture_ns'])
        self.run.practice_message=result.reason;self._practice_status(result.reason)
        return result.status in {'applied','unmeasured'}

    def repractice(self):
        if not self.active:return False
        if self.speech is not None:self.speech.stop()
        self.drift.restart();self.run.repractice(self.clock())
        self._practice_status('请看着练习按钮中间的点；新的结果替换旧偏移')
        self.panel.hide();self.window.open()
        return True

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
        self.drift=None;self._mapping=None
        self._viewport=None;self._coordinate_blocked_since=None
        self._practice_status('Demo 已结束，会话偏移已清除')
        self.session.set_demo_active(False)
        if restore:
            self.overlay.setVisible(self._saved[0])
            if self._saved[1]:self.panel.present()
            else:self.panel.hide()
        self.notice('Demo 已结束'+('，最近一次选择已撤销' if reason=='escape' and undone else ''),False)
