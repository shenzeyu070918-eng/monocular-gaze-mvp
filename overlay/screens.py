"""Built-in selection and topology changes; never substitute an external primary."""
from dataclasses import dataclass
import sys
from .mapping import ScreenRect

SCREEN_CHANGED='屏幕设置已变化，请重新九点校准'


def logical_rect(screen):
    rect=screen.geometry()
    return ScreenRect(rect.x(),rect.y(),rect.width(),rect.height(),screen.devicePixelRatio())


@dataclass(frozen=True)
class BuiltinScreen:
    qscreen: object
    rect: ScreenRect
    identity: str


def select_builtin(screens):
    if sys.platform=='darwin':
        from .screens_macos import select
    elif sys.platform=='win32':
        from .screens_windows import select
    else:raise RuntimeError('悬浮层暂只支持 macOS 和 Windows。')
    return select(list(screens))


def startup_screen(app,*,delay_ms=300,retry_count=3,selector=select_builtin,wait=None):
    """Initial Qt/AppKit/CoreGraphics snapshots may briefly disagree too."""
    if wait is None:
        from PySide6.QtCore import QEventLoop,QTimer
        def wait():
            loop=QEventLoop();QTimer.singleShot(delay_ms,loop.quit);loop.exec()
    for attempt in range(retry_count+1):
        app.processEvents()
        try:return selector(app.screens())
        except (RuntimeError,AttributeError) as exc:
            if attempt==retry_count:raise RuntimeError('无法定位可用内置屏：'+str(exc)) from exc
            wait()


def transition(previous,current):
    """Only built-in identity/geometry/DPR matter, never external screen count."""
    if current is None:return 'unavailable'
    def geometry(rect):return rect.x,rect.y,rect.width,rect.height,rect.dpr
    if previous.identity==current.identity and geometry(previous.rect)==geometry(current.rect):return 'unchanged'
    return 'changed'


class ScreenMonitor:
    """Debounced native/Qt re-resolution, invoked on topology signals only."""
    def __init__(self,app,previous,on_change,*,selector=select_builtin,delay_ms=300,retry_count=3,on_detach=lambda:None):
        from PySide6.QtCore import QTimer
        self.app,self.previous,self.on_change,self.selector=app,previous,on_change,selector
        self.retry_count=retry_count;self.retries=0;self.on_detach=on_detach
        self.timer=QTimer(app);self.timer.setSingleShot(True);self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self.refresh);self.connections=[];self.screen_connections=[]
        self._connect(app.screenAdded,self.schedule);self._connect(app.screenRemoved,self._removed)
        if hasattr(app,'primaryScreenChanged'):self._connect(app.primaryScreenChanged,self.schedule)
        self._watch_screens()

    def _connect(self,event,slot):
        event.connect(slot);self.connections.append((event,slot))

    def _watch_screens(self):
        for event,slot in self.screen_connections:
            try:event.disconnect(slot)
            except (RuntimeError,TypeError):pass
        self.screen_connections=[]
        for screen in self.app.screens():
            for name in ('geometryChanged','logicalDotsPerInchChanged','physicalDotsPerInchChanged'):
                event=getattr(screen,name,None)
                if event is not None:event.connect(self.schedule);self.screen_connections.append((event,self.schedule))

    def schedule(self,*_):self.retries=0;self.timer.start()

    def _removed(self,screen):
        if screen is self.previous.qscreen:self.on_detach()
        self.schedule()

    def refresh(self):
        self._watch_screens()
        try:current=self.selector(self.app.screens());error=None
        except Exception as exc:current=None;error='内置屏暂不可用：'+str(exc)
        if current is None and self.retries<self.retry_count:
            self.retries+=1;self.timer.start();return
        self.retries=0
        kind=transition(self.previous,current)
        if current is not None:self.previous=current
        self.on_change(kind,current,error)

    def close(self):
        self.timer.stop()
        for event,slot in self.connections+self.screen_connections:
            try:event.disconnect(slot)
            except (RuntimeError,TypeError):pass
