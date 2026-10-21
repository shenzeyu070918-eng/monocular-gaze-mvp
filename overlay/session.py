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
        self.profiles=ProfileManager(settings,screen,on_matrix=self._matrix,directory=profile_directory)
        self.recording=recording_factory(settings,metadata_factory=self.metadata)
        self.latest=None;self._metadata_snapshot=None;self.task=None;self.task_window=None
        self._saving=None;self._revision=-1;self._record_error=None
        self._pending_calibration=None;self._first_calibration=None
        self.request_redo=None;self.dismiss_redo=None;self._window_factory=None;self._qscreen=None
        self.display_revision=0;self.matrix_since_ns=0

    def _matrix(self,matrix):
        self.matrix_since_ns=self.clock_ns();self.display_revision+=1
        if self.controller is not None:self.controller.set_matrix(matrix)
        if self._saving is None:self.record_calibration()

    def record_calibration(self):
        if not self.recording.status()['active']:return
        self.recording.emit('calibration',action='applied' if self.profiles.matrix is not None else 'reset',
                            matrix=self.profiles.matrix,group='B' if self.profiles.matrix is not None else 'A',
                            calibration_id=self.profiles.calibration_id,coordinate_system='full_screen_normalized',pipeline='python')

    def sink(self,prediction):
        # Both receive numeric copies; overflow is explicit and never waits.
        self.recording.record_prediction(prediction)
        self.buffer.push(prediction)

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
        if self.profiles.profile is None:return '未校准'
        return '九点（web 手动）' if self.profiles.profile.get('pipeline')=='web' else '九点'

    @property
    def radius(self):return self.profiles.radius

    @property
    def radius_text(self):
        radius=self.radius
        if radius is not None:return f'半径来源：最近匹配 B 核对平均误差 {radius:.3f} cm'
        if self.settings.geometry.measured:
            return f'半径来源：配置示意值 {self.settings.overlay.radius_cm:g} cm（非实测精度）'
        return f'半径来源：配置示意值 {self.settings.overlay.fallback_radius_px:g} 逻辑像素（尺寸未实测）'

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
                if changed and self.active:self.cancel('conditions_changed')
                self.latest=prediction
                self._metadata_snapshot=(deepcopy(camera),deepcopy(report),deepcopy(self.profiles.conditions))
            if self.task is not None:
                try:self.task.ingest(prediction)
                except (ValueError,TypeError) as exc:
                    self.notice('任务取样失败：'+str(exc),True);self.cancel('invalid_sample')
        error=self.buffer.error or report.get('result_sink_error')
        if error and self.active:
            self.notice('任务数值结果不完整：'+str(error),True);self.cancel('numeric_results_incomplete')
        status=self.recording.status()
        if status['error'] and status['error']!=self._record_error:
            self._record_error=status['error'];self.notice('录制失败：'+status['error'],True)
            self.cancel('recording_failed')
        if self.task is not None:
            self.task.advance(self.clock_ns());self._events();self.task_window.refresh()
        self.profiles.poll(frozen=self.task is not None or self._pending_calibration is not None)
        if self._saving is not None and not self.profiles.busy:
            saved=self._saving;self._saving=None
            if self.profiles.profile is not None and self.profiles.calibration_id==saved['calibration_id'] and not self.profiles.last_error:
                self.recording.emit('calibration',action='applied',matrix=saved['matrix'],group='B',
                                    calibration_id=saved['calibration_id'],coordinate_system='full_screen_normalized',
                                    estimated_distance_cm_median=saved['estimated_distance_cm_median'],
                                    estimated_distance_sample_count=saved['estimated_distance_sample_count'],
                                    pipeline='python',conditions_fingerprint=conditions_id(self.profiles.conditions))
                self.recording.end_task(saved['task_id'],'completed')
                self.notice('九点档案已保存；'+self.radius_text,False)
            else:
                self.recording.end_task(saved['task_id'],'profile_save_failed')
                self.notice('九点档案未激活：'+(self.profiles.last_error or '保存期间档案或设备条件改变'),True)
        if self.profiles.revision!=self._revision:
            self._revision=self.profiles.revision
            self.notice(self.profiles.reason+'；'+self.radius_text,False)

    def start(self,kind,qscreen,*,window_factory=None):
        if self.active or self.profiles.busy:raise ValueError('已有任务或档案操作进行中')
        if self.latest is None or self.profiles.conditions is None:raise ValueError('请等待摄像头实际条件就绪')
        if self.controller.snapshot().status!='running':raise ValueError('请先恢复摄像头')
        if window_factory is None:
            from .task_window import TaskWindow
            window_factory=TaskWindow
        self._window_factory,self._qscreen=window_factory,qscreen
        runner=TaskRunner(kind,self.settings,matrix=self.profiles.matrix,calibration_id=self.profiles.calibration_id,clock_ns=self.clock_ns,
                          screen_size=(self.screen.width,self.screen.height))
        window=window_factory(runner,qscreen,self.settings.overlay,clock_ns=self.clock_ns)
        if not self.recording.begin_task(runner.task_id):
            window.close();window.deleteLater()
            raise ValueError(self.recording.status()['error'] or '录制会话无法开启')
        self.buffer.clear()
        if hasattr(self.controller,'clear_numeric_error'):self.controller.clear_numeric_error()
        self.task,self.task_window=runner,window
        window.completed.connect(self._completed);window.cancelled.connect(self._cancelled)
        self.recording.emit('calibration',action='applied' if self.profiles.matrix is not None else 'reset',
                            matrix=self.profiles.matrix,group='B' if self.profiles.matrix is not None else 'A',
                            calibration_id=self.profiles.calibration_id,coordinate_system='full_screen_normalized',pipeline='python')
        try:
            runner.begin();window.open()
        except Exception:
            runner.cancel('task_window_failed');self._events()
            self.recording.end_task(runner.task_id,'task_window_failed')
            self._dispose()
            raise
        return runner

    def cancel(self,reason='cancelled'):
        if self._pending_calibration is not None:
            result=self._pending_calibration;self._pending_calibration=None;self._first_calibration=None
            if self.dismiss_redo is not None:self.dismiss_redo()
            self.recording.end_task(result['task_id'],reason)
        if self.task is not None:
            self.task.cancel(reason);self._events();self.task_window.refresh()

    def _dispose(self):
        window=self.task_window;self.task=self.task_window=None
        window.close();window.deleteLater();self.restore()

    def _cancelled(self,reason):
        if self.task is None:return
        runner=self.task;self._events()
        first=self._first_calibration;self._first_calibration=None
        retain=first is not None and reason in {'cancelled','escape','window_closed','fullscreen_exited','insufficient_samples','prediction_timeout','calibration_failed','target_not_presented'}
        if not retain:self.recording.end_task(runner.task_id,reason)
        self._dispose();self.notice('任务已取消：'+(runner.error or reason),False)
        if retain:
            self._quality_event(first,'redo_cancelled');self._save_calibration(first)

    def _completed(self,result):
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
                                conditions_fingerprint=conditions_id(self.profiles.conditions))
            self.results(summary_text(result['summary'],result['estimated_distance_cm_median']))
            group=result['summary']['groups'].get('B',{}).get('all',{})
            if group.get('complete') and group.get('mean_cm') is not None:self.profiles.save_check(result)
            else:self.notice('核对已完成；无完整、实测尺寸的 B 组结果，保留原半径来源',False)
            self.recording.end_task(result['task_id'],'completed')
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
            runner.begin();window.open()
        except Exception as exc:
            if self.task_window is not None:self._dispose()
            self.notice('补看未完成，保留首次完整校准：'+str(exc),True)
            self._quality_event(first,'redo_cancelled');self._save_calibration(first)

    def close(self):
        # Called by the lifecycle worker only after the camera/sink have drained.
        self.recording.close();self.profiles.close()
