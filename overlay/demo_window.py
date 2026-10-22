"""Opaque, interactive projection UI; all selection/scoring lives in DemoRun."""
import math
import time
from PySide6.QtCore import Qt,QRectF,QEvent,Signal
from PySide6.QtGui import QColor,QFont,QPainter,QPen,QRegion
from PySide6.QtWidgets import QWidget


class DemoWindow(QWidget):
    cancelled=Signal(str)

    def __init__(self,run,screen,options,*,clock_ns=time.perf_counter_ns):
        super().__init__(None,Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.run,self.options,self.clock=run,options,clock_ns
        self.position=None;self.ring_opacity=0.;self._disposed=False;self._paint_key=None
        self._base_key=None;self._progress_key=None;self._ring_key=None;self._time_key=None
        self._old_button=None;self._old_ring=QRectF();self._old_flash=None
        self.setWindowTitle('眼控 Demo')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setGeometry(screen.geometry())
        self.winId();self.windowHandle().setScreen(screen)
        self.button_font=QFont();self.button_font.setPixelSize(options.button_font_px);self.button_font.setBold(True)
        self.heading_font=QFont(self.button_font);self.heading_font.setPixelSize(options.heading_font_px)

    def open(self):
        self.showFullScreen()
        from .platforms import present_panel
        present_panel(self)
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def dispose(self):
        self._disposed=True;self.hide();self.deleteLater()

    def refresh(self,position,opacity,now_ns):
        run=self.run
        base=(run.phase,run.page,run.target_id,run.feedback_text,run.speech_failed,run.score,tuple(run.buttons))
        region=QRegion()
        if base!=self._base_key:self._base_key=base;region=QRegion(self.rect())
        current=run.selector.current
        button=None if current is None else QRectF(*current.rect).adjusted(-self.options.progress_px,-self.options.progress_px,self.options.progress_px,self.options.progress_px)
        progress=(None if current is None else current.id,run.selector.progress)
        if progress!=self._progress_key:
            for rect in (self._old_button,button):
                if rect is not None:region|=QRegion(rect.toAlignedRect())
            self._old_button=button;self._progress_key=progress
        ring=QRectF()
        if position is not None and opacity>0:
            x,y=position;rx,ry=run.radii;p=self.options.progress_px
            ring=QRectF(x-rx-p,y-ry-p,2*(rx+p),2*(ry+p))
        if (position,opacity)!=self._ring_key:
            region|=QRegion(self._old_ring.toAlignedRect())|QRegion(ring.toAlignedRect())
            self._old_ring=ring;self._ring_key=(position,opacity)
        self.position,self.ring_opacity=position,opacity
        second=self.remaining(now_ns)
        if second!=self._time_key:
            self._time_key=second;region|=QRegion(0,0,self.width(),int(self.options.header_px))
        flash=getattr(run,'flash_rect',None) if now_ns<run.flash_until else None
        if flash!=self._old_flash:
            for rect in (flash,self._old_flash):
                if rect is not None:region|=QRegion(QRectF(*rect).toAlignedRect())
            self._old_flash=flash
        if not region.isEmpty():self.update(region.intersected(QRegion(self.rect())))

    def remaining(self,now_ns):
        if self.run.app!='game':return None
        elapsed=0. if self.run.started_ns is None else (now_ns-self.run.started_ns)/1e9
        return math.ceil(max(0.,self.options.round_s-elapsed))

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.cancelled.emit('escape');event.accept();return
        super().keyPressEvent(event)

    def closeEvent(self,event):
        if not self._disposed:self.cancelled.emit('window_closed')
        event.accept()

    def changeEvent(self,event):
        if (not self._disposed and self.isVisible() and event.type()==QEvent.Type.WindowStateChange
                and (not self.isFullScreen() or self.isMinimized())):
            self.cancelled.emit('fullscreen_exited')
        super().changeEvent(event)

    def paintEvent(self,event):
        run=self.run;now=self.clock();o=self.options
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),QColor(o.background));painter.setPen(QColor('white'));painter.setFont(self.heading_font)
        title='看着这个按钮 '+f'{o.dwell_ms/1000:g}'+' 秒试试' if run.phase=='practice' else '用目光说出你的需要' if run.app=='board' else '看住亮起的目标'
        if run.phase=='playing' and run.app=='game':title+=f'    得分 {run.score}    剩余 {self.remaining(now)} 秒'
        painter.drawText(QRectF(o.padding_px,0,self.width()-2*o.padding_px,o.header_px),Qt.AlignmentFlag.AlignCenter,title)
        painter.setFont(self.button_font)
        if run.phase=='feedback':
            painter.setPen(QColor(o.feedback_color))
            font=QFont(self.button_font);font.setPixelSize(max(o.button_font_px,int(o.button_font_px*1.8)));painter.setFont(font)
            painter.drawText(QRectF(self.rect()).adjusted(o.padding_px,o.header_px,-o.padding_px,-o.footer_px),
                             Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,run.feedback_text)
        elif run.phase=='finished':
            average=sum(run.reactions)/len(run.reactions) if run.reactions else None
            text=f'本局得分 {run.score}\n平均反应时间 '+('暂无有效选择' if average is None else f'{average/1000:.2f} 秒')
            painter.drawText(QRectF(self.rect()),Qt.AlignmentFlag.AlignCenter,text)
        else:
            for button in run.buttons:self._button(painter,button,now)
        painter.setFont(self.heading_font);painter.setPen(QColor('white'))
        footer=('语音不可用，仅显示文字。 ' if run.speech_failed else '')+'Esc 退出；触发后 1 秒内可撤销'
        painter.drawText(QRectF(o.padding_px,self.height()-o.footer_px,self.width()-2*o.padding_px,o.footer_px),Qt.AlignmentFlag.AlignCenter,footer)
        if self.position is not None and self.ring_opacity>0:
            painter.setOpacity(self.ring_opacity);painter.setPen(QPen(QColor(o.progress_color),o.progress_px))
            painter.setBrush(Qt.BrushStyle.NoBrush);x,y=self.position;rx,ry=run.radii
            painter.drawEllipse(QRectF(x-rx,y-ry,rx*2,ry*2))
        painter.end()
        if run.phase in {'practice','playing'}:run.presented(now)

    def _button(self,painter,button,now):
        run=self.run;o=self.options;rect=QRectF(*button.rect)
        active=run.selector.current is not None and run.selector.current.id==button.id
        flash=now<run.flash_until and getattr(run,'flash_rect',None)==button.rect
        hole=run.app=='game' and run.phase=='playing' and button.id!='next'
        target=hole and button.id==run.target_id
        color=o.feedback_color if flash or target else o.hole_color if hole else o.button_color
        stroke=o.progress_px if active else 1
        painter.setBrush(QColor(color));painter.setPen(QPen(QColor(o.progress_color if active else color),stroke))
        painter.drawRoundedRect(rect.adjusted(stroke/2,stroke/2,-stroke/2,-stroke/2),24,24)
        if button.id=='practice':text='看着这里'
        elif button.id=='next':text=f'下一页 {run.page+1}/{run.pages}'
        elif run.app=='board':text=o.labels[int(button.id)]
        else:text='看这里' if target else ''
        if hole:
            # Draw a small vector mole, not an emoji/font-dependent asset.
            radius=min(rect.width(),rect.height())*.18
            cx,cy=rect.center().x(),rect.center().y()
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(o.text_color))
            painter.drawEllipse(QRectF(cx-radius*1.3,cy+radius*.55,radius*2.6,radius*.7))
            if target:
                painter.setBrush(QColor(o.button_color));painter.drawEllipse(QRectF(cx-radius,cy-radius,2*radius,2*radius))
                painter.setBrush(QColor(o.text_color))
                for dx in (-.35,.35):painter.drawEllipse(QRectF(cx+dx*radius-5,cy-8,10,14))
                painter.setPen(QPen(QColor(o.text_color),4));painter.drawArc(QRectF(cx-radius*.35,cy+5,radius*.7,radius*.45),180*16,180*16)
        painter.setPen(QColor(o.text_color));painter.setFont(self.button_font)
        text_rect=QRectF(rect.left(),rect.top()+18,rect.width(),60) if hole else rect.adjusted(20,20,-20,-70)
        painter.drawText(text_rect,Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,text)
        if active:
            diameter=min(60.,rect.height()/3);circle=QRectF(rect.center().x()-diameter/2,rect.bottom()-diameter-22,diameter,diameter)
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QPen(QColor(o.text_color),o.progress_px))
            painter.drawArc(circle,90*16,-round(run.selector.progress*360*16))
