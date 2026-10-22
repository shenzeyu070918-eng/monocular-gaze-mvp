"""Overlay task/profile/recording coordination, separate from Qt rendering."""
from copy import deepcopy
import time
from types import SimpleNamespace

from gaze_core.capture import NumericResultBuffer
from gaze_core.python_recording import PythonRecording
from gaze_core.recorder import session_metadata
from .profiles import ProfileManager, conditions_id
from .tasks import TaskRunner


class OverlaySession:
    def __init__(self,settings,screen,*,notice,restore,results,clock_ns=time.perf_counter_ns,
                 profile_directory=None,recording_factory=PythonRecording):
        self.settings,self.screen=settings,screen
        self.notice,self.restore,self.results=notice,restore,results
        self.clock_ns=clock_ns;self.controller=None
        self.buffer=NumericResultBuffer(settings.recording.queue_size)
        self.profiles=ProfileManager(settings,screen,on_matrix=lambda _:self._sync_correction(),directory=profile_directory)
        self.recording=recording_factory(settings,metadata_factory=self.metadata)
        self.latest=None;self._metadata_snapshot=None;self.task=None;self.task_window=None
        self._radius_text_revision=None;self._radius_text=None
        self._saving=None;self._revision=-1;self._record_error=None
        self._pending_calibration=None;self._first_calibration=None
        self.request_redo=None;self.dismiss_redo=None;self._window_factory=None;self._qscreen=None
        self.display_revision=0;self.matrix_since_ns=0
        self._inference_task_active=False
        self.clicks=None
        from .heatmap import HeatmapWorker
        self.heatmap=HeatmapWorker(settings,screen,clock_ns=clock_ns)
        self._matrix_revision=0;self._heatmap_start_pending=settings.heatmap.enabled
        from .autocalibration import AutocalManager
        self.autocal=AutocalManager(settings,name=self.profiles.name,directory=profile_directory,clock_ns=clock_ns,
                                   validate_device=self.profiles.validate_device,device_signature=self.profiles.device_signature)
        self._click_hub=None;self._running=True;self._autocal_rate=None
        self._correction_cursor=None;self._active_group='A';self._active_matrix=None
        self._active_id='none';self._active_count=0;self._applied_signature=None;self._radius_signature=None
        self._coarse_mode_text='';self._autocal_text_key=None;self._autocal_text='点击自动粗校准：关闭'
        self._autocal_error=None;self._autocal_reason=None
        self._active_c_radius=None

    def bind_clicks(self,*,start_task,exclude=None,own_handles=None,listener_factory=None):
        from .click_session import ClickSession
        from .click_hub import ClickHub
        self._click_hub=ClickHub(self.settings,self.screen,self.recording,self.autocal,
                                exclude=exclude,own_handles=own_handles,listener_factory=listener_factory)
        self.clicks=ClickSession(self.settings,self.screen,self.recording,self.profiles,start_task=start_task,
                                set_rate=self.controller.set_click_session_rate,notice=self.notice,
                                listener_factory=lambda *a,**k:self._click_hub.recording_listener,exclude=exclude,clock_ns=self.clock_ns,
                                own_handles=own_handles,cancel_task=self.cancel)
        return self.clicks

    def set_running(self,running):
        self._running=bool(running)
        self._sync_autocal_capture()

    def change_screen(self,screen):
        """Only after camera pause: discard stale coordinate contexts, not algorithms."""
        self.set_running(False);self.cancel('screen_changed');self.recording.manual(False)
        self.screen=screen;self.latest=None;self.buffer.clear();self._metadata_snapshot=None;self._saving=None
        self.heatmap.change_screen(screen);self._heatmap_start_pending=False
        self.autocal.configure(None);self.profiles.change_screen(screen)
        if self._click_hub is not None:
            # Listener is stopped while paused; the next start creates a fresh
            # native coordinate adapter using this screen's resolved identity.
            self._click_hub.listener.screen=screen
            self.clicks.screen=screen
        self._sync_correction()

    def refresh_screen_mapping(self,screen):
        if self.screen==screen:return
        self.screen=screen;self.profiles.screen=screen
        if self._click_hub is not None:self._click_hub.change_screen_mapping(screen);self.clicks.screen=screen

    def set_autocal_enabled(self,enabled):
        self.autocal.set_enabled(enabled)
        self._sync_autocal_capture();self._sync_correction()
        if self.autocal.error and self.autocal.error!=self._autocal_error:
            self._autocal_error=self.autocal.error;self.notice(self.autocal.error,True)
        if self.autocal.error:raise RuntimeError(self.autocal.error)

    def _sync_autocal_capture(self):
        self.autocal.set_frozen(self.active or not self._running)
        try:
            if self._click_hub is not None:
                self._click_hub.set_running(self._running)
                error=self._click_hub.listener.status().get('error')
                if error and self.autocal.enabled:raise RuntimeError(error)
        except Exception as exc:
            self.autocal.enabled=False;self.autocal.error='自动粗校准鼠标采集失败：'+str(exc);self.autocal.revision+=1
            self.notice(self.autocal.error,True)
        rate=self.autocal.enabled and not self.autocal.frozen and self.autocal.conditions is not None
        if rate!=self._autocal_rate:
            self._autocal_rate=rate
            if self.controller is not None and hasattr(self.controller,'set_autocal_rate'):
                self.controller.set_autocal_rate(rate,max_fps=self.settings.autocal.inference_limit(self.settings.inference))

    def _sync_correction(self):
        cursor=(self.profiles.revision,self.autocal.revision,self.autocal.enabled)
        if cursor==self._correction_cursor:return
        if self.profiles.conditions is not None and self.autocal.conditions!=self.profiles.conditions:
            self.autocal.configure(self.profiles.conditions)
            cursor=(self.profiles.revision,self.autocal.revision,self.autocal.enabled)
        self._correction_cursor=cursor
        coarse,coarse_id,coarse_matrix,coarse_radius=self.autocal.candidate()
        if self.profiles.profile is not None:
            group,matrix,ident,count='B',self.profiles.matrix,self.profiles.calibration_id,0
        elif self.autocal.enabled and coarse is not None:
            group,matrix,ident,count='C',coarse_matrix,coarse_id,coarse['sample_count']
        else:group,matrix,ident,count='A',None,'none',0
        self._active_group,self._active_matrix,self._active_id,self._active_count=group,matrix,ident,count
        self._active_c_radius=coarse_radius if group=='C' else None
        if group=='C':
            x,y=coarse['offset_norm'];geometry=self.settings.geometry
            self._coarse_mode_text=f'点击粗校准 · 已用 {count} 次点击 · 偏移 {x*geometry.width_cm:+.2f}/{y*geometry.height_cm:+.2f} cm'
        signature=(group,ident,None if matrix is None else tuple(tuple(row) for row in matrix))
        radius=self.radius
        radius_signature=(signature,radius)
        if signature!=self._applied_signature:
            self._applied_signature=signature;self._radius_signature=radius_signature;self._matrix(matrix)
        elif radius_signature!=self._radius_signature:
            self._radius_signature=radius_signature;self._configure_heatmap();self.record_calibration()

    def _task_inference(self,active):
        if active==self._inference_task_active:return
        if self.controller is not None:self.controller.set_task_active(active)
        self._inference_task_active=active

    def _matrix(self,matrix):
        self.matrix_since_ns=self.clock_ns();self.display_revision+=1
        revision=self.controller.set_matrix(matrix) if self.controller is not None else None
        self._matrix_revision=revision if type(revision) is int else self.display_revision
        self._configure_heatmap()
        if self._saving is None:self.record_calibration()

    def record_calibration(self):
        if not self.recording.status()['active']:return
        self.recording.emit('calibration',action='applied' if self._active_matrix is not None else 'reset',
                            matrix=self._active_matrix,group=self._active_group,
                            calibration_id=self._active_id,coordinate_system='full_screen_normalized',pipeline='python',
                            **self._calibration_metadata())

    def _calibration_metadata(self):
        radius=self.radius
        measured=self.settings.geometry.measured
        return dict(matrix_revision=self._matrix_revision,
                    calibration_matched=self._active_group=='C' or (self.profiles.profile is not None and self.profiles.profile.get('pipeline')=='python'),
                    error_radius_cm=(radius if radius is not None else self.settings.overlay.radius_cm) if measured else None,
                    error_radius_source=('matching_C_check' if self._active_group=='C' else 'matching_B_check') if measured and radius is not None else 'configured_cm' if measured else 'configured_logical_pixels')

    def _configure_heatmap(self):
        from gaze_core.heatmap import kernel_scale
        meta=self._calibration_metadata();profile=self.profiles.profile
        calibrated=self._active_group=='C' or (profile is not None and profile.get('pipeline')=='python')
        phase=dict(calibration_id=self._active_id,group=self._active_group if calibrated else 'A',
                   matrix_revision=self._matrix_revision,calibrated=calibrated,matrix=deepcopy(self._active_matrix),
                   conditions_fingerprint=conditions_id(self.profiles.conditions) if self.profiles.conditions is not None else None)
        self.heatmap.configure(dict(matrix=deepcopy(self._active_matrix),revision=self._matrix_revision,calibrated=calibrated,
                              geometry=self.settings.geometry,phase=phase,
                              kernel=kernel_scale(self.settings.geometry,(self.screen.width,self.screen.height),
                                      meta['error_radius_cm'] or self.settings.overlay.radius_cm,
                                      self.settings.overlay.fallback_radius_px,meta['error_radius_source'])))

    def set_heatmap_enabled(self,enabled):
        self._heatmap_start_pending=False
        return self.heatmap.set_enabled(enabled)

    def set_heatmap_viewing(self,viewing):
        return self.heatmap.set_viewing(viewing)

    def sink(self,prediction):
        # Both receive numeric copies; overflow is explicit and never waits.
        self.recording.record_prediction(prediction)
        self.buffer.push(prediction)
        if self.clicks is not None:self.clicks.observe(prediction)
        self.heatmap.observe(prediction)
        self.autocal.observe(prediction)

    def metadata(self):
        snapshot=self._metadata_snapshot
        if snapshot is None:raise ValueError('尚未取得摄像头实际条件，不能开始录制')
        camera,report,conditions=deepcopy(snapshot)
        if self.controller is not None:report=getattr(self.controller.snapshot(),'report',None) or report
        engine=SimpleNamespace(settings=self.settings,device=report.get('device') or 'unknown',
                               device_name=report.get('device_name') or 'unknown',camera_metadata=lambda *_:camera)
        screen=dict(width_px=self.screen.width,height_px=self.screen.height,screen_x=self.screen.x,screen_y=self.screen.y,
                    device_pixel_ratio=self.screen.dpr,window_width_px=self.screen.width,window_height_px=self.screen.height,fullscreen=True)
        metadata=session_metadata(source='python',engine=engine,geometry=self.settings.geometry,
                                  camera_size=(camera['width_px'],camera['height_px']),grayscale=self.settings.camera.grayscale,
                                  screen=screen,measured=self.settings.geometry.measured)
        metadata['screen'].update(screen)  # QScreen, not a stored device resolution.
        metadata.update(pipeline='python',conditions_fingerprint=conditions_id(conditions))
        return metadata

    @property
    def active(self):return self.task is not None or self._saving is not None or self._pending_calibration is not None

    @property
    def short_mode(self):
        if self._active_group=='C':return self._coarse_mode_text
        if self.profiles.profile is None:return '未校准'
        return '九点（web 手动）' if self.profiles.profile.get('pipeline')=='web' else '九点'

    @property
    def mode(self):return self.short_mode if self._active_group=='C' else self.profiles.mode

    @property
    def autocal_text(self):
        key=(self.autocal.revision,self.autocal.enabled,self._active_group)
        if key==self._autocal_text_key:return self._autocal_text
        self._autocal_text_key=key;state=self.autocal.state()
        text='点击自动粗校准：'+('开启' if state['enabled'] else '关闭')
        if state['enabled']:
            text+=f' · 本轮有效 {state["accepted"]} · 剔除 {state["rejected"]} · 待处理 {state["pending"]}'
            if self._active_group=='B':text+=' · 九点优先，粗校准待命'
            if state['profile'] is not None:
                x,y=state['offset_cm'];text+=f' · 已用 {state["used_count"]} 次点击 · 偏移 {x:+.2f}/{y:+.2f} cm（右/下为正）'
            elif state['fit_error']:text+=' · '+state['fit_error']
            else:text+=f' · 等待至少 {self.settings.autocal.min_clicks} 次有效点击及 {self.settings.autocal.min_occupied_cells} 个网格 · '+state['reason']
        if state['error']:text+=' · '+state['error']
        self._autocal_text=text;return text

    @property
    def radius(self):return self._active_c_radius if self._active_group=='C' else self.profiles.radius

    @property
    def radius_text(self):
        revision=(self.profiles.revision,self._active_group,self._active_id,self.radius)
        if self._radius_text_revision==revision:return self._radius_text
        radius=self.radius
        if radius is not None:text=f'半径来源：最近匹配 {self._active_group} 核对平均误差 {radius:.3f} cm'
        elif self.settings.geometry.measured:
            text=f'半径来源：配置示意值 {self.settings.overlay.radius_cm:g} cm（非实测精度）'
        else:text=f'半径来源：配置示意值 {self.settings.overlay.fallback_radius_px:g} 逻辑像素（尺寸未实测）'
        self._radius_text_revision=revision;self._radius_text=text
        return text

    def _events(self):
        if self.task is None:return
        for event in self.task.drain_events():
            kind=event.pop('type');self.recording.emit(kind,**event)

    def tick(self,view):
        report=view.report or {}
        for prediction in self.buffer.drain():
            camera=prediction.data.get('camera')
            if camera is not None:
                changed=self.profiles.observe(camera)
                if changed:self.autocal.configure(self.profiles.conditions)
                if changed and (self.active or (self.clicks is not None and self.clicks.active)):self.cancel('conditions_changed')
                self.latest=prediction
                # Session metadata needs camera conditions and device identity,
                # not per-tick counters. Preserve an owned snapshot only when
                # those values change; readers on the writer thread get a
                # complete tuple through one atomic reference assignment.
                identity=dict(device=report.get('device'),device_name=report.get('device_name'))
                previous=self._metadata_snapshot
                if previous is None or changed or camera!=previous[0] or identity!=previous[1]:
                    conditions=deepcopy(self.profiles.conditions) if previous is None or changed else previous[2]
                    self._metadata_snapshot=(deepcopy(camera),identity,conditions)
            if self.task is not None:
                try:self.task.ingest(prediction)
                except (ValueError,TypeError) as exc:
                    self.notice('任务取样失败：'+str(exc),True);self.cancel('invalid_sample')
        error=self.buffer.error or report.get('result_sink_error')
        if error and self.active:
            self.notice('任务数值结果不完整：'+str(error),True);self.cancel('numeric_results_incomplete')
        status=self.recording.status()
        if self.clicks is not None:self.clicks.tick()
        if status['error'] and status['error']!=self._record_error:
            self._record_error=status['error'];self.notice('录制失败：'+status['error'],True)
            self.cancel('recording_failed')
        if self.task is not None:
            self.task.advance(self.clock_ns());self._events();self.task_window.refresh()
        self.profiles.poll(frozen=self.task is not None or self._pending_calibration is not None)
        self._sync_autocal_capture();self._sync_correction()
        if self.autocal.error and self.autocal.error!=self._autocal_error:
            self._autocal_error=self.autocal.error;self.notice(self.autocal.error,True)
        if self.autocal.enabled and self.autocal.reason!=self._autocal_reason:
            self._autocal_reason=self.autocal.reason;self.notice(self.autocal.reason,False)
        if self._saving is not None and not self.profiles.busy:
            saved=self._saving;self._saving=None
            if self.profiles.profile is not None and self.profiles.calibration_id==saved['calibration_id'] and not self.profiles.last_error:
                self.recording.emit('calibration',action='applied',matrix=saved['matrix'],group='B',
                                    calibration_id=saved['calibration_id'],coordinate_system='full_screen_normalized',
                                    estimated_distance_cm_median=saved['estimated_distance_cm_median'],
                                    estimated_distance_sample_count=saved['estimated_distance_sample_count'],
                                    pipeline='python',conditions_fingerprint=conditions_id(self.profiles.conditions),**self._calibration_metadata())
                self.recording.end_task(saved['task_id'],'completed')
                self.notice('九点档案已保存；'+self.radius_text,False)
                if self.clicks is not None:self.clicks.baseline_ready(self.profiles.profile)
            else:
                self.recording.end_task(saved['task_id'],'profile_save_failed')
                self.notice('九点档案未激活：'+(self.profiles.last_error or '保存期间档案或设备条件改变'),True)
                if self.clicks is not None and self.clicks.active:self.clicks.abort('profile_save_failed')
        if self.profiles.revision!=self._revision:
            self._revision=self.profiles.revision
            self._configure_heatmap()
            # Radius-only profile updates do not change the matrix revision,
            # but their scale provenance still belongs in numeric recordings.
            self.record_calibration()
            self.notice(self.profiles.reason+'；'+self.radius_text,False)
        if self._heatmap_start_pending and self.profiles.conditions is not None and not self.profiles.busy:
            self._heatmap_start_pending=False;self.heatmap.set_enabled(True)

    def start(self,kind,qscreen,*,window_factory=None):
        if self.active or self.profiles.busy:raise ValueError('已有任务或档案操作进行中')
        if self.latest is None or self.profiles.conditions is None:raise ValueError('请等待摄像头实际条件就绪')
        if self.controller.snapshot().status!='running':raise ValueError('请先恢复摄像头')
        if window_factory is None:
            from .task_window import TaskWindow
            window_factory=TaskWindow
        self._window_factory,self._qscreen=window_factory,qscreen
        self.autocal.set_frozen(True);self._sync_correction()
        if self._click_hub is not None:self._click_hub.sync()
        runner=TaskRunner(kind,self.settings,matrix=self._active_matrix,calibration_id=self._active_id,clock_ns=self.clock_ns,
                          screen_size=(self.screen.width,self.screen.height),corrected_group='C' if self._active_group=='C' else 'B')
        window=window_factory(runner,qscreen,self.settings.overlay,clock_ns=self.clock_ns)
        if not self.recording.begin_task(runner.task_id):
            window.close();window.deleteLater()
            raise ValueError(self.recording.status()['error'] or '录制会话无法开启')
        self.buffer.clear()
        if hasattr(self.controller,'clear_numeric_error'):self.controller.clear_numeric_error()
        self.task,self.task_window=runner,window
        window.completed.connect(self._completed);window.cancelled.connect(self._cancelled)
        self.record_calibration()
        try:
            self._task_inference(True)
            runner.begin();window.open()
        except Exception:
            runner.cancel('task_window_failed');self._events()
            self.recording.end_task(runner.task_id,'task_window_failed')
            self._dispose()
            raise
        return runner

    def cancel(self,reason='cancelled'):
        self._task_inference(False)
        if self.clicks is not None:self.clicks.abort(reason)
        if self._pending_calibration is not None:
            result=self._pending_calibration;self._pending_calibration=None;self._first_calibration=None
            if self.dismiss_redo is not None:self.dismiss_redo()
            self.recording.end_task(result['task_id'],reason)
        if self.task is not None:
            self.task.cancel(reason);self._events();self.task_window.refresh()

    def _dispose(self):
        self._task_inference(False)
        window=self.task_window;self.task=self.task_window=None
        window.close();window.deleteLater();self.restore()

    def _cancelled(self,reason):
        if self.task is None:return
        runner=self.task;self._events()
        first=self._first_calibration;self._first_calibration=None
        retain=first is not None and reason in {'cancelled','escape','window_closed','fullscreen_exited','insufficient_samples','prediction_timeout','calibration_failed','target_not_presented'}
        if not retain:self.recording.end_task(runner.task_id,reason)
        self._dispose();self.notice('任务已取消：'+(runner.error or reason),False)
        if self.clicks is not None and not retain:self.clicks.cancelled_task(runner.kind,reason)
        if retain:
            self._quality_event(first,'redo_cancelled');self._save_calibration(first)

    def _completed(self,result):
        # Sampling has ended; restore the configured cap before formatting,
        # profile IO or a redo confirmation can keep the user waiting.
        self._task_inference(False)
        self._events()
        if result['kind']=='calibrate':
            quality=result.get('quality')
            self._quality_event(result,'initial' if not result.get('redo_round') else 'final')
            self._dispose()
            self._show_quality(result)
            if quality and not result.get('redo_round') and any(p['redo'] for p in quality['points']) and self.request_redo is not None:
                self._pending_calibration=result
                try:self.request_redo(quality,self.decide_redo)
                except Exception as exc:
                    self.notice('补看提示失败，保留首次完整校准：'+str(exc),True)
                    self.decide_redo(False)
            else:self._save_calibration(result)
            return
        else:
            from .panel import summary_text
            self.recording.emit('evaluation_summary',task_id=result['task_id'],run_id=result['run_id'],
                                calibration_id=result['calibration_id'],completed=True,reason='completed',summary=result['summary'],
                                estimated_distance_cm_median=result['estimated_distance_cm_median'],
                                estimated_distance_sample_count=result['estimated_distance_sample_count'],pipeline='python',
                                sample_selection=result['sample_selection'],sampling=result['sampling'],
                                conditions_fingerprint=conditions_id(self.profiles.conditions))
            self.results(summary_text(result['summary'],result['estimated_distance_cm_median']))
            corrected='C' if 'C' in result['summary']['groups'] else 'B'
            group=result['summary']['groups'].get(corrected,{}).get('all',{})
            if group.get('complete') and group.get('mean_cm') is not None:
                if corrected=='C':self.autocal.save_check(result)
                else:self.profiles.save_check(result)
            else:self.notice(f'核对已完成；无完整、实测尺寸的 {corrected} 组结果，保留原半径来源',False)
            self.recording.end_task(result['task_id'],'completed')
            if self.clicks is not None:self.clicks.completed_check(result)
        self._dispose()

    def _quality_event(self,result,phase):
        if result.get('quality') is not None:
            self.recording.emit('calibration_quality',task_id=result['task_id'],calibration_id=result['calibration_id'],
                                phase=phase,quality=result['quality'],pipeline='python',
                                estimated_distance_cm_median=result['estimated_distance_cm_median'],
                                estimated_distance_sample_count=result['estimated_distance_sample_count'])

    def _show_quality(self,result):
        from gaze_core.calibration_quality import quality_text
        distance=result['estimated_distance_cm_median']
        self.results(quality_text(result.get('quality'))+'\n估计人脸到屏幕距离中位数：'+
                     ('不可用' if distance is None else f'{distance:.3f} cm')+'（仅记录）')

    def _save_calibration(self,result):
        if not result.get('redo_round'):self._quality_event(result,'final')
        self._first_calibration=None;self._saving=result
        self.profiles.save_calibration(result)
        self._show_quality(result)

    def decide_redo(self,accepted):
        first=self._pending_calibration
        if first is None:return  # A pause/condition change/quit invalidates a dialog callback.
        self._pending_calibration=None
        if not accepted:
            self._save_calibration(first);return
        if self.controller.snapshot().status!='running':
            self.recording.end_task(first['task_id'],'camera_not_running');return
        try:
            runner=TaskRunner('calibrate',self.settings,clock_ns=self.clock_ns,task_id=first['task_id'],
                              screen_size=(self.screen.width,self.screen.height),redo_from=first,
                              redo_target_ids=[p['target_id'] for p in first['quality']['points'] if p['redo']])
            window=self._window_factory(runner,self._qscreen,self.settings.overlay,clock_ns=self.clock_ns)
            self._first_calibration=first;self.task,self.task_window=runner,window
            self.buffer.clear();window.completed.connect(self._completed);window.cancelled.connect(self._cancelled)
            self._task_inference(True)
            runner.begin();window.open()
        except Exception as exc:
            if self.task_window is not None:self._dispose()
            self.notice('补看未完成，保留首次完整校准：'+str(exc),True)
            self._quality_event(first,'redo_cancelled');self._save_calibration(first)

    def close(self):
        # Called by the lifecycle worker only after the camera/sink have drained.
        self._task_inference(False)
        if self.clicks is not None:self.clicks.close()
        if self._click_hub is not None:self._click_hub.close()
        self.autocal.close()
        self.heatmap.close();self.recording.close();self.profiles.close()
