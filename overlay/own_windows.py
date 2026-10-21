"""Cache only this process's interactive window rectangles; no foreign queries."""
from PySide6.QtCore import QObject,QEvent,Qt
from PySide6.QtWidgets import QApplication


class OwnWindows(QObject):
    EVENTS=frozenset((QEvent.Type.Show,QEvent.Type.Hide,QEvent.Type.Move,QEvent.Type.Resize,
                      QEvent.Type.Close,QEvent.Type.WindowStateChange))
    def __init__(self,app,*,ignore=()):
        super().__init__(app)
        self.app=app;self.ignore=tuple(ignore);self.rectangles=();self.handles=frozenset();self.dirty=True
        app.installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type() in self.EVENTS:self.dirty=True
        return False

    def refresh(self):
        if not self.dirty:return
        self.dirty=False;rectangles=[];handles=[]
        for widget in QApplication.topLevelWidgets():
            if widget in self.ignore or not widget.isVisible() or widget.windowFlags()&Qt.WindowType.WindowTransparentForInput:continue
            rect=widget.frameGeometry()
            rectangles.append((rect.x(),rect.y(),rect.width(),rect.height()))
            handles.append(int(widget.winId()))
        self.rectangles=tuple(rectangles)
        self.handles=frozenset(handles)

    def reason(self,point):
        x,y=point
        for left,top,width,height in self.rectangles:
            if left<=x<left+width and top<=y<top+height:return 'own_ui'
        return None

    def close(self):self.app.removeEventFilter(self);self.rectangles=();self.handles=frozenset()
