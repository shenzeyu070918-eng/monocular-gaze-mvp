from dataclasses import dataclass
from typing import Protocol
import numpy as np

@dataclass(frozen=True)
class ScreenSample:
    pixels: np.ndarray  # RGB logical resolution; never serialize this dataclass.
    request_ns: int
    received_ns: int
    app_name: str
    @property
    def time_ns(self):return (self.request_ns+self.received_ns)//2

class ScreenCaptureBackend(Protocol):
    def capture(self) -> ScreenSample: ...
    def close(self): ...

class OCRBackend(Protocol):
    def recognize(self,path,languages) -> list: ...

class CapturePaused(RuntimeError):
    """A privacy/foreground transition; discard in-flight pixels."""
