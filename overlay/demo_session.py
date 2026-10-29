"""Demo ownership, filtered-result delivery and optional numeric recording."""
import time
from copy import deepcopy
from gaze_core.demo import DemoRun


class DemoSession:
    def __init__(self,session,overlay,panel,*,notice,window_factory=None,speech_factory=None,clock_ns=time.perf_counter_ns,debug=False):
        self.session,self.overlay,self.panel,self.notice=session,overlay,panel,notice
        self.window_factory=window_factory;self.speech_factory=speech_factory;self.clock=clock_ns
        self.run=self.window=self.speech=None;self._recorded={};self._saved=None
        self._token=None;self._deferred=[];self.debug=debug;self._mapping_reason=None
        self.drift=None;self._mapping=None
        self._viewport=None;self._coordinate_blocked_since=None
        self.galaxy=None;self._galaxy_dialog=None;self._qscreen=None
        self.start_calibration=None;self._calibration_return=None

    @property
    def active(self):return self.run is not None

    def _context(self):
        s=self.session
        return s.display_revision,s.profiles.conditions,getattr(s,'_active_group','A')

    def start(self,app,qscreen,*,force_practice=False):
        self._calibration_return=None
        s=self.session
        if self.active or s.active or s.profiles.busy or (s.clicks is not None and s.clicks.active):
            raise ValueError('请先结束当前任务或点击采集会话')
        group=getattr(s,'_active_group','A')
        if group not in {'B','C'} or s.profiles.conditions is None or (group=='B' and (s.profiles.profile or {}).get('pipeline')!='python'):
            raise ValueError('请先完成条件匹配的悬浮层九点校准或点击粗校准')
        if s.controller.snapshot().status!='running':raise ValueError('请先恢复摄像头')
        settings=s.settings;rect=self.overlay.rect_info
        self._qscreen=qscreen
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
        if force_practice:run.repractice(self.clock())
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
        window.calibration_available=False
        window.cancelled.connect(self.cancel)
        if hasattr(window,'calibration_requested'):window.calibration_requested.connect(self.calibrate_from_practice)
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
        if self.galaxy is not None:
            self._galaxy_tick();return
        if run.app=='stars' and run.worker.error:
            self.notice(run.worker.error,True);self.cancel('prediction_error');return
        mapping=self.window.coordinates()
        coordinate_ready=self._prepare_coordinates(mapping,now_ns)
        run.advance(now_ns)
        if run.app=='galaxy' and run.phase=='playing':
            from .galaxy_session import GalaxySession
            self.window.hide()
            self.galaxy=GalaxySession(self.session,run.radii,self.drift.offset,debug=self.debug)
            self._events();return
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
        self.window.calibration_available=result.status=='rejected' and self.start_calibration is not None
        return result.status in {'applied','unmeasured'}

    def calibrate_from_practice(self):
        if not self.active or self.run.phase!='practice' or self.drift.status!='rejected' or self.start_calibration is None:return False
        app,screen,saved=self.run.app,self._qscreen,self._saved
        conditions=deepcopy(self.session.profiles.conditions);rect=self.overlay.rect_info
        self.cancel('recalibrate')
        try:
            runner=self.start_calibration()
            self._calibration_return=(app,screen,rect,conditions,runner.calibration_id,saved)
        except Exception:
            # The normal task launcher reports the error and restores controls.
            try:
                self.start(app,screen,force_practice=True);self._saved=saved
            except Exception as exc:self.notice('未能返回 Demo 练习：'+str(exc),True)
            return False
        return True

    def calibration_succeeded(self,qscreen=None):
        pending,self._calibration_return=self._calibration_return,None
        if pending is None:return False
        app,screen,rect,conditions,calibration_id,saved=pending
        if (self.session.profiles.calibration_id!=calibration_id or self.overlay.rect_info!=rect
                or self.session.profiles.conditions!=conditions):return False
        try:self.start(app,screen if qscreen is None else qscreen,force_practice=True)
        except Exception as exc:self.notice('九点已保存，但未能返回 Demo 练习：'+str(exc),True);return False
        self._saved=saved
        return True

    def poll_calibration_return(self,*,active):
        # Called only while waiting; success is signalled after profile activation.
        if self._calibration_return is not None and (not active or not self.session.active):
            self._calibration_return=None

    def repractice(self):
        if not self.active:return False
        if self.galaxy is not None:self.galaxy.close();self.galaxy=None
        if self.speech is not None:self.speech.stop()
        self.drift.restart();self.run.repractice(self.clock());self.window.calibration_available=False
        self._practice_status('请看着练习按钮中间的点；新的结果替换旧偏移')
        self.panel.hide();self.window.open()
        return True

    def _galaxy_tick(self):
        for kind,value in self.galaxy.poll():
            if kind=='stop':self.cancel('window_closed');return
            if kind=='disconnected':kind,value='error','本地连接已断开，可重试或切回 Qt 星空'
            if kind=='fallback':self._galaxy_fallback();return
            if kind=='warning':self.notice('眼手星系：'+str(value),False)
            if kind=='geometry':
                message='星系网页实际客户区：'+str(value)
                logger=getattr(self.session,'geometry_logger',None)
                if logger is not None:logger(message)
            if kind=='error' and self._galaxy_dialog is None:
                from PySide6.QtWidgets import QMessageBox
                value={1:'页面连接不可用',2:'WebGL2或GPU浮点渲染不可用',3:'本地连接断开'}.get(value,value)
                self.galaxy.close();self.panel.present()
                self.notice('眼手星系不可用：'+str(value),True)
                box=QMessageBox(self.panel);box.setWindowTitle('眼手星系')
                box.setText('星系连接或WebGL不可用。是否切回Qt星空？\n'+str(value))
                fallback=box.addButton('切回 Qt 星空',QMessageBox.ButtonRole.AcceptRole)
                box.addButton('退出 Demo',QMessageBox.ButtonRole.RejectRole)
                self._galaxy_dialog=box
                def chosen(button):
                    if not self.active:return
                    use_qt=button is fallback
                    self._galaxy_dialog=None
                    if use_qt:self._galaxy_fallback()
                    else:self.cancel('window_closed')
                box.buttonClicked.connect(chosen);box.open()
                box.finished.connect(lambda *_:self.cancel('window_closed') if self._galaxy_dialog is box and self.active else None)

    def _galaxy_fallback(self):
        screen=self._qscreen
        self.cancel('window_closed')
        self.start('stars',screen)

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
        self._calibration_return=None
        if not self.active:return
        if self._galaxy_dialog is not None:
            dialog,self._galaxy_dialog=self._galaxy_dialog,None;dialog.close()
        if self.galaxy is not None:self.galaxy.close();self.galaxy=None
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
