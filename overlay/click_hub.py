"""One mouse-only native listener, independent M4 recording and M5 owners."""
from .click_listener import ClickListener


class _RecordingOwner:
    def __init__(self,hub):self.hub=hub
    def start(self):return self.hub.set_recording(True)
    def stop(self):self.hub.set_recording(False)
    def status(self):
        state=self.hub.listener.status()
        return dict(state,active=self.hub.recording_owner and state['active'])


class ClickHub:
    def __init__(self,settings,screen,recording,autocal,*,exclude=None,own_handles=None,listener_factory=None):
        self.recording,self.autocal=recording,autocal
        self.recording_owner=False;self.running=True;self.closed=False
        options=settings.click_session
        factory=listener_factory or (lambda *a,**k:ClickListener(*a,queue_size=settings.recording.queue_size,
                   own_handles=own_handles,clock_options=dict(resample_ns=int(options.clock_resample_ms*1e6),
                   max_uncertainty_ns=int(options.clock_max_uncertainty_ms*1e6),max_drift_ns=int(options.clock_max_drift_ms*1e6),
                   max_delay_ns=int(options.clock_max_delay_ms*1e6)),**k))
        self.listener=factory(screen,self._emit,exclude=exclude)
        self.recording_listener=_RecordingOwner(self)
        self._wanted=False

    def _emit(self,kind,**fields):
        # A disabled recorder is normal for M5; it must not stop mouse delivery.
        if self.autocal.enabled and not self.autocal.frozen:self.autocal.click(fields)
        status=self.recording.status()
        if status['active']:return self.recording.emit(kind,**fields)
        return not self.recording_owner

    def set_recording(self,enabled):
        self.recording_owner=bool(enabled)
        self.sync()
        return True

    def set_running(self,running):
        self.running=bool(running);self.sync()

    def sync(self):
        wanted=not self.closed and self.running and (self.recording_owner or (
            self.autocal.enabled and not self.autocal.frozen and self.autocal.conditions is not None))
        if wanted==self._wanted:return
        try:
            if wanted:
                if self.listener.start() is False:raise RuntimeError(self.listener.status().get('error') or '鼠标监听未开启')
            else:self.listener.stop()
        except Exception:
            self._wanted=False;raise
        self._wanted=wanted

    def close(self):
        self.closed=True;self.sync()
