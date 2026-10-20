"""One lifecycle owner, asynchronous latest-result handoff, no Qt dependency."""
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ControllerView:
    status: str
    error: str | None
    result: object
    idle: bool


class OverlayController:
    def __init__(self,settings,*,pipeline_factory=None):
        if pipeline_factory is None:
            from gaze_core.capture import CameraPipeline
            from gaze_core.display import DisplayPipeline
            def pipeline_factory():
                return CameraPipeline(settings,display_pipeline=DisplayPipeline(settings.filter,settings.fixation,matrix=None),
                                      idle_after_s=settings.overlay.idle_after_s,idle_fps=settings.overlay.idle_fps)
        self._factory=pipeline_factory
        self._poll_seconds=settings.overlay.refresh_ms/1000
        self._condition=threading.Condition()
        self._wanted='paused';self._revision=0
        self._status='paused';self._error=None;self._latest=None;self._idle=False
        self.closed=threading.Event()
        self.thread=threading.Thread(target=self._worker,name='overlay-control',daemon=True)
        self.thread.start()

    def request(self,action):
        if action not in {'running','paused','closed'}:raise ValueError('无效的悬浮层操作')
        with self._condition:
            if self.closed.is_set():return
            if self._wanted=='closed' and self._status!='error':return
            self._wanted=action;self._revision+=1;self._error=None;self._latest=None
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:return ControllerView(self._status,self._error,self._latest,self._idle)

    def _set(self,status,**values):
        with self._condition:
            self._status=status
            for name,value in values.items():setattr(self,'_'+name,value)
            self._condition.notify_all()

    def _worker(self):
        pipeline=None;active=False;cursor=0;handled=-1
        while not self.closed.is_set():
            with self._condition:
                if not active and handled==self._revision:self._condition.wait()
                wanted,revision=self._wanted,self._revision
            try:
                if wanted=='closed':
                    self._set('closing',latest=None)
                    if pipeline is not None:pipeline.close()
                    self._set('closed',latest=None,idle=False);self.closed.set();return
                if wanted=='paused':
                    self._set('pausing',latest=None)
                    if active:pipeline.pause()
                    active=False;handled=revision
                    self._set('paused',latest=None,idle=False)
                    continue
                if not active:
                    self._set('starting',latest=None)
                    if pipeline is None:pipeline=self._factory()
                    pipeline.resume();active=True
                    self._set('running')
                result=pipeline.wait_for_result(cursor,timeout=self._poll_seconds)
                with self._condition:
                    if result is not None and revision==self._revision and self._wanted=='running':
                        self._latest=result;cursor=result.sequence
                    self._idle=bool(pipeline.report().get('software_idle_pacing'))
                    self._condition.notify_all()
                handled=revision
            except Exception as exc:
                # Cleanup stays off the GUI thread. CameraPipeline itself owns
                # release() on capture's finally; errors cannot release it here.
                cleanup=None
                if pipeline is not None and wanted!='closed':
                    try:pipeline.pause()
                    except Exception as stop_error:cleanup=stop_error
                active=False;handled=revision
                with self._condition:
                    if self._revision==revision and wanted!='closed':self._wanted='paused'
                self._set('error',latest=None,idle=False,error=str(exc)+(f'；停止失败：{cleanup}' if cleanup else ''))
