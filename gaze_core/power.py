"""Optional software read pacing. Camera/model lifetime remains in capture.py."""
from .devices import finite


class NoFacePacing:
    def __init__(self,after_s,fps):
        self.after_ns=int(finite(after_s,'idle_after_s',positive=True)*1_000_000_000)
        self.period_ns=max(1,int(1_000_000_000/finite(fps,'idle_fps',positive=True)))
        self.reset()

    def reset(self):
        self.since_ns=None;self.idle=False

    def observe(self,status,capture_ns):
        if status!='no_face':self.reset();return
        if self.since_ns is None:self.since_ns=capture_ns
        self.idle=capture_ns-self.since_ns>=self.after_ns
