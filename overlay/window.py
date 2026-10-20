"""Rendering only. All positions are already logical pixels; never camera data."""
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QFont
from PySide6.QtWidgets import QWidget
from .mapping import ScreenRect


class OverlayWindow(QWidget):
    def __init__(self, screen, options, *, native_configure=None, diagnostic=False):
        super().__init__(None,Qt.WindowType.FramelessWindowHint|Qt.WindowType.Tool|
                         Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput|
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle('视线悬浮层 · 未校准')
        self.rect_info=screen;self.options=options
        self.setGeometry(screen.x,screen.y,screen.width,screen.height)
        self.native_configure=native_configure
        self.diagnostic=diagnostic
        self.position=None;self.radii=(options.fallback_radius_px,)*2;self.opacity=0.
        self.label='未校准 · 窗口诊断（无摄像头）' if diagnostic else '未校准 · 正在启动'

    def prepare_native(self):
        # Call outside a Qt virtual callback so failures abort startup visibly.
        self.winId()
        if self.native_configure:self.native_configure(self)

    def _ring_bounds(self):
        if self.position is None:return QRectF()
        x,y=self.position;rx,ry=self.radii;pad=self.options.stroke_px+2
        return QRectF(x-rx-pad,y-ry-pad,2*(rx+pad),2*(ry+pad))

    def set_display(self,position,radii,opacity,label):
        values=(position,radii,opacity,label)
        if values==(self.position,self.radii,self.opacity,self.label):return
        old_bounds=self._ring_bounds();old_label=self.label
        self.position,self.radii,self.opacity,self.label=values
        self.update(old_bounds.united(self._ring_bounds()).toAlignedRect())
        if old_label!=label:self.update(self._label_rect().toAlignedRect())

    def _label_rect(self):
        return QRectF(self.options.label_inset_px,self.options.label_inset_px,520,self.options.label_font_px+12)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font=QFont();font.setPixelSize(self.options.label_font_px);painter.setFont(font)
        text_rect=self._label_rect()
        painter.fillRect(text_rect,QColor(20,25,35,190))
        painter.setPen(QColor('white'));painter.drawText(text_rect,Qt.AlignmentFlag.AlignCenter,self.label)
        if self.diagnostic:
            painter.setPen(QPen(QColor(self.options.color),self.options.stroke_px))
            size=self.options.diagnostic_size_px
            for index,point in enumerate(((0,0),(1,0),(0,1),(1,1))):
                x,y=self.rect_info.local(point)
                painter.drawLine(QRectF(x-size,y,size*2,0).topLeft(),QRectF(x-size,y,size*2,0).topRight())
                painter.drawLine(QRectF(x,y-size,0,size*2).topLeft(),QRectF(x,y-size,0,size*2).bottomLeft())
                painter.drawText(QRectF(x-120 if point[0] else x+8,y-35 if point[1] else y+8,112,25),
                                 Qt.AlignmentFlag.AlignCenter,f'{index+1}: {point}')
        elif self.position is not None and self.opacity>0:
            painter.setOpacity(self.opacity)
            painter.setPen(QPen(QColor(self.options.color),self.options.stroke_px))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x,y=self.position;rx,ry=self.radii
            painter.drawEllipse(QRectF(x-rx,y-ry,rx*2,ry*2))
