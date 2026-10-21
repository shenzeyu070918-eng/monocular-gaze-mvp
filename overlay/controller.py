"""One lifecycle owner, asynchronous latest-result handoff, no Qt dependency."""
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ControllerView:
    status: str
    error: str | None
    result: object
    idle: bool
    report: dict | None = None
    matrix_revision: int = 0


class OverlayController:
    def __init__(self,settings,*,pipeline_factory=None,result_sink=None,shutdown=None):
        if pipeline_factory is None:
            from gaze_core.capture import CameraPipeline
            from gaze_core.display import DisplayPipeline
            def pipeline_factory():
                return CameraPipeline(settings,display_pipeline=DisplayPipeline(settings.filter,settings.fixation,matrix=None),
                                      idle_after_s=settings.overlay.idle_after_s,idle_fps=settings.overlay.idle_fps,
                                      result_sink=result_sink)
        self._factory=pipeline_factory
        self._shutdown=shutdown
        self._poll_seconds=settings.overlay.refresh_ms/1000
        self._condition=threading.Condition()
        self._wanted='paused';self._revision=0
        self._status='paused';self._error=None;self._latest=None;self._idle=False
        self._report=None;self._matrix=None;self._matrix_revision=0;self._clear_numeric=False
        self._task_active=False;self._task_revision=0
        self.closed=threading.Event()
        self.thread=threading.Thread(target=self._worker,name='overlay-control',daemon=True)
        self.thread.start()

    def request(self,action):
        if action not in {'running','paused','closed'}:raise ValueError('无效的悬浮层操作')
        with self._condition:
            if self.closed.is_set():return
            if self._wanted=='closed' and self._status!='error':return
            if action in {'paused','closed'} and self._task_active:
                self._task_active=False;self._task_revision+=1
            self._wanted=action;self._revision+=1;self._error=None;self._latest=None
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:return ControllerView(self._status,self._error,self._latest,self._idle,self._report,self._matrix_revision)

    def set_matrix(self,matrix):
        from gaze_core.display import calibration_matrix
        value=calibration_matrix(matrix)
        with self._condition:
            self._matrix=None if value is None else value.tolist()
            self._matrix_revision+=1;self._condition.notify_all()

    def set_task_active(self,active):
        """Deliver pacing intent to the control worker without GUI-thread I/O."""
        if type(active) is not bool:raise ValueError('任务状态必须为布尔值。')
        with self._condition:
            if self.closed.is_set() or self._wanted=='closed':return
            if self._task_active==active:return
            self._task_active=active;self._task_revision+=1
            self._condition.notify_all()

    def clear_numeric_error(self):
        with self._condition:
            self._clear_numeric=True
            if self._report is not None:self._report=dict(self._report,result_sink_error=None)
            self._condition.notify_all()

    def _set(self,status,**values):
        with self._condition:
            self._status=status
            for name,value in values.items():setattr(self,'_'+name,value)
            self._condition.notify_all()

    def _worker(self):
        pipeline=None;active=False;cursor=0;handled=-1;matrix_revision=0;task_revision=-1
        while not self.closed.is_set():
            with self._condition:
                if not active and handled==self._revision:self._condition.wait()
                wanted,revision=self._wanted,self._revision
            try:
                with self._condition:task_active,revision_task=self._task_active,self._task_revision
                if pipeline is not None and revision_task!=task_revision:
                    pipeline.set_task_active(task_active);task_revision=revision_task
                if wanted=='closed':
                    self._set('closing',latest=None)
                    if pipeline is not None:pipeline.close()
                    if self._shutdown is not None:self._shutdown()
                    self._set('closed',latest=None,idle=False);self.closed.set();return
                if wanted=='paused':
                    self._set('pausing',latest=None)
                    if active:pipeline.pause()
                    active=False;handled=revision
                    self._set('paused',latest=None,idle=False)
                    continue
                if not active:
                    self._set('starting',latest=None)
                    if pipeline is None:
                        pipeline=self._factory()
                        with self._condition:task_active,revision_task=self._task_active,self._task_revision
                        pipeline.set_task_active(task_active);task_revision=revision_task
                    with self._condition:matrix,revision_matrix=self._matrix,self._matrix_revision
                    if revision_matrix!=matrix_revision:
                        pipeline.set_display_matrix(matrix,revision=revision_matrix);matrix_revision=revision_matrix
                    pipeline.resume();active=True
                    self._set('running')
                with self._condition:matrix,revision_matrix=self._matrix,self._matrix_revision
                if revision_matrix!=matrix_revision:
                    pipeline.set_display_matrix(matrix,revision=revision_matrix);matrix_revision=revision_matrix
                with self._condition:clear_numeric,self._clear_numeric=self._clear_numeric,False
                if clear_numeric:pipeline.clear_result_sink_error()
                result=pipeline.wait_for_result(cursor,timeout=self._poll_seconds)
                report=pipeline.report()
                with self._condition:
                    if result is not None and revision==self._revision and self._wanted=='running':
                        self._latest=result;cursor=result.sequence
                    self._idle=bool(report.get('software_idle_pacing'));self._report=report
                    self._condition.notify_all()
                handled=revision
            except Exception as exc:
                # Cleanup stays off the GUI thread. CameraPipeline itself owns
                # release() on capture's finally; errors cannot release it here.
                cleanup=None
                with self._condition:
                    if self._task_active:
                        self._task_active=False;self._task_revision+=1
                if pipeline is not None and wanted!='closed':
                    try:
                        pipeline.set_task_active(False)
                        with self._condition:task_revision=self._task_revision
                        pipeline.pause()
                    except Exception as stop_error:cleanup=stop_error
                active=False;handled=revision
                with self._condition:
                    if self._revision==revision and wanted!='closed':self._wanted='paused'
                self._set('error',latest=None,idle=False,error=str(exc)+(f'；停止失败：{cleanup}' if cleanup else ''))
