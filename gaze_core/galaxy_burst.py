"""Capture-clock closed-eye charge, shared with page-local manual burst actions."""
READY,SHORT,NO_FACE,GAP,COOLDOWN,BLOCKED,NEEDS_OPEN,INVALID=range(8)


class ClosedEyeBurst:
    def __init__(self,options):
        self.o=options;self.last=None;self.since=None;self.last_closed=None;self.armed=False
        self.started=None;self.cool_until=0;self.revision=0;self.blink_count=0;self.source=0;self.reason=NEEDS_OPEN

    def _clear(self,reason):
        self.since=None;self.last_closed=None;self.armed=False;self.reason=reason

    def trigger(self,now_ns,source=2,enabled=True):
        if not enabled:self._clear(BLOCKED);return False
        if now_ns<self.cool_until:self._clear(COOLDOWN);return False
        self.started=now_ns;self.cool_until=now_ns+int(self.o.burst_cooldown_ms*1e6)
        self.revision+=1;self.source=source;self.blink_count+=int(source==1);self._clear(READY);return True

    def feed(self,capture_ns,status,*,enabled=True,stale_ms=400):
        if self.last is not None and capture_ns<=self.last:return
        gap=self.last is not None and capture_ns-self.last>stale_ms*1e6;self.last=capture_ns
        if not enabled:
            self.started=None;self._clear(BLOCKED);return
        if gap:self._clear(GAP)
        if status=='no_face':self._clear(NO_FACE);return
        if status in ('geometry_invalid','screen_unconfigured'):status='tracking' # Engine has already passed EAR with a detected face.
        if status not in ('tracking','blink'):self._clear(INVALID);return
        if capture_ns<self.cool_until:self._clear(COOLDOWN);return
        if status=='tracking':
            duration=0 if self.since is None else (self.last_closed-self.since)/1e6
            if not gap and self.since is not None:
                if duration>=self.o.blink_hold_ms:self.trigger(capture_ns,1)
                else:self._clear(SHORT)
            self.since=None;self.last_closed=None;self.armed=True
        elif self.armed and not gap:
            if self.since is None:self.since=capture_ns
            self.last_closed=capture_ns
        elif self.reason not in (GAP,NO_FACE,BLOCKED,INVALID):self.reason=NEEDS_OPEN

    def snapshot(self,now_ns):
        elapsed=0. if self.started is None else max(0.,(now_ns-self.started)/1e6)
        active=self.started is not None and elapsed<self.o.burst_duration_ms
        closed=0. if self.since is None else (self.last_closed-self.since)/1e6
        return dict(burst_revision=self.revision,burst_active=int(active),burst_elapsed_ms=float(min(elapsed,self.o.burst_duration_ms)),
                    burst_source=self.source,blink_closed_ms=float(closed),blink_count=self.blink_count,blink_reason=self.reason)
