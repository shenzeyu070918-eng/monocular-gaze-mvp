"""Optional numeric Demo seam. Models are opened/closed by inference's thread."""
import threading


class DemoFrameSlot:
    def __init__(self):self._lock=threading.Lock();self._wanted=None;self._current=None
    def set(self,processor):
        with self._lock:self._wanted=processor
    def process(self,frame,data):
        with self._lock:wanted=self._wanted
        if wanted is not self._current:
            if self._current is not None:self._current.close()
            self._current=wanted
        if self._current is not None:
            try:self._current.process(frame,data)
            except Exception as exc:
                self._current.error='星系数值处理失败：'+str(exc)
                self._current.stop();self._current.close()
    def close(self):
        if self._current is not None:self._current.close();self._current=None
