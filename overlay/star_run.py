"""Practice/worker bridge; only genuine capture frames drive star discoveries."""
from gaze_core.demo import DemoRun


class StarDemoRun(DemoRun):
    def __init__(self,options,width,height,radii,*,group,settings,clock_ns,stale_ms=300.,worker_factory=None):
        super().__init__('stars',options,width,height,radii,group=group,stale_ms=stale_ms)
        if worker_factory is None:
            from .starfield import StarfieldWorker
            worker_factory=StarfieldWorker
        self.star_settings=settings
        self.worker=worker_factory(width,height,radii,settings,clock_ns=clock_ns)
        self._stars_started=False;self._stars_paused=False
        self.worker.start()

    def presented(self,now_ns):
        first=self.presented_ns is None
        super().presented(now_ns)
        if first and self.phase=='playing':
            if not self._stars_started:
                self.worker.presented(now_ns);self._stars_started=True
            elif self._stars_paused:
                self.worker.resume(now_ns);self._stars_paused=False

    def advance(self,now_ns):
        super().advance(now_ns)
        if self.phase in {'playing','replay','finished'} and self._stars_started and not self._stars_paused:
            snapshot=self.worker.latest()
            if snapshot is not None and snapshot['phase'] in {'playing','replay','finished'}:
                self.phase=snapshot['phase']

    def feed(self,point,capture_ns,now_ns,*,valid=True,sequence=None):
        if self.phase in {'practice','feedback'}:
            return super().feed(point,capture_ns,now_ns,valid=valid,sequence=sequence)
        self.advance(now_ns)
        if sequence is not None:
            if sequence<=self.last_sequence:return None
            self.last_sequence=sequence
        if self.phase=='playing' and self.presented_ns is not None and capture_ns>=self.presented_ns:
            self.worker.ingest(point,capture_ns,now_ns,valid=valid)
        return None

    def repractice(self,now_ns):
        if not self.active:return False
        if self._stars_started:
            self.worker.pause(now_ns);self._stars_paused=True
        return super().repractice(now_ns)

    def _summary(self,now_ns,reason):
        # The worker emits the actual numeric round summary, not board scores.
        self._reported=True

    def close(self,now_ns,reason='escape'):
        self.worker.close(reason=reason,now_ns=now_ns)
        super().close(now_ns,reason)

    def drain_events(self):
        events=super().drain_events()
        events.extend((kind,dict(run_id=self.run_id,group=self.group,**fields))
                      for kind,fields in self.worker.drain_events())
        return events
