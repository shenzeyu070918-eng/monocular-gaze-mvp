"""Opaque, interactive projection UI; all selection/scoring lives in DemoRun."""
import math
import time
from PySide6.QtCore import Qt,QRectF,QEvent,Signal,QPointF,QTimer
from PySide6.QtGui import QColor,QFont,QPainter,QPen,QRegion
from PySide6.QtWidgets import QWidget


class DemoWindow(QWidget):
    cancelled=Signal(str)

    def __init__(self,run,screen,options,*,clock_ns=time.perf_counter_ns,screen_rect=None,debug=False):
        super().__init__(None,Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.run,self.options,self.clock=run,options,clock_ns
        from .mapping import ScreenRect
        geometry=screen.geometry()
        self.screen_rect=screen_rect or ScreenRect(geometry.x(),geometry.y(),run.width,run.height)
        self.debug=debug;self.before_position=None;self.mapping_reason='';self.coordinate_notice=''
        self._coordinates_cache=None
        self.position=None;self.ring_opacity=0.;self._disposed=False;self._paint_key=None
        self._base_key=None;self._progress_key=None;self._ring_key=None;self._time_key=None
        self._old_button=None;self._old_ring=QRectF();self._old_flash=None
        self.setWindowTitle('眼控 Demo')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setGeometry(screen.geometry())
        self.winId();self.windowHandle().setScreen(screen)
        self.button_font=QFont();self.button_font.setPixelSize(options.button_font_px);self.button_font.setBold(True)
        self.heading_font=QFont(self.button_font);self.heading_font.setPixelSize(options.heading_font_px)
        self.star_painter=self.star_timer=self.frame_timer=None;self._star_version=None;self._star_deadline=None
        if run.app=='stars':
            from .starfield import StarfieldPainter,PaintEventTimer
            self.star_painter=StarfieldPainter(run.star_settings)
            self.frame_timer=PaintEventTimer(run.star_settings.frame_budget_ms)
            self.star_timer=QTimer(self);self.star_timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.star_timer.setSingleShot(True)
            self.star_timer.timeout.connect(self._star_tick)

    def open(self):
        self.showFullScreen()
        from .platforms import present_panel
        present_panel(self)
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def dispose(self):
        if self._disposed:return
        if self.star_timer is not None:
            self.star_timer.stop();self.star_painter.close()
            # The worker has been joined by DemoSession before this one-off write.
            from datetime import datetime,timezone
            from gaze_core.config import ROOT
            from .starfield import save_starfield_performance
            path=ROOT/'data/performance'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-stars.json')
            try:
                report=save_starfield_performance(path,self.run.worker,self.star_painter,frame_report=self.frame_timer.report())
                m=report['paint_event']['milliseconds']
                print(f'星空绘制：中位 {m["median"]} ms / P90 {m["p90"]} ms；记录 {path}')
            except Exception as exc:print('星空性能记录失败：'+str(exc))
        self._disposed=True;self.hide();self.deleteLater()

    def _star_tick(self):
        if self._disposed:return
        snapshot=self.run.worker.latest()
        version=None if snapshot is None else (id(self.run.worker),snapshot['version'])
        if version is not None and version!=self._star_version:
            self._star_version=version;self.update()
        if self.run.phase in {'playing','replay'}:self._arm_star_timer()

    def _arm_star_timer(self):
        # Absolute deadlines alternate 16/17 ms at 60 Hz. A fixed rounded
        # 17 ms interval would impose a 58.8 Hz ceiling before drawing costs.
        now=self.clock();period=1e9/self.run.star_settings.animation_fps
        self._star_deadline=now+period if self._star_deadline is None else self._star_deadline+period
        if self._star_deadline<=now:self._star_deadline=now+period
        self.star_timer.start(max(1,math.ceil((self._star_deadline-now)/1e6)))

    def coordinates(self):
        if self._coordinates_cache is not None:return self._coordinates_cache
        from gaze_core.demo_coordinates import DemoCoordinates
        origin=self.mapToGlobal(QPointF(0,0))
        self._coordinates_cache=DemoCoordinates(self.screen_rect,window_origin=(origin.x(),origin.y()),
                                               window_size=(self.width(),self.height()),fullscreen=self.isFullScreen())
        return self._coordinates_cache

    def moveEvent(self,event):
        self._coordinates_cache=None;super().moveEvent(event)

    def resizeEvent(self,event):
        self._coordinates_cache=None;super().resizeEvent(event)

    def refresh(self,position,opacity,now_ns,*,before_position=None):
        run=self.run
        if self.star_timer is not None:
            if run.phase in {'playing','replay'} and not self.star_timer.isActive():self._arm_star_timer()
            elif run.phase not in {'playing','replay'}:self.star_timer.stop();self._star_deadline=None
        base=(run.phase,run.page,run.target_id,run.feedback_text,run.speech_failed,run.score,run.practice_message,tuple(run.buttons),self.coordinate_notice)
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
        visible_position=position if self.options.show_gaze_ring or self.debug else None
        if visible_position is not None and opacity>0:
            x,y=position;rx,ry=run.radii;p=self.options.progress_px
            ring=QRectF(x-rx-p,y-ry-p,2*(rx+p),2*(ry+p))
        ring_key=(visible_position,opacity if visible_position is not None else 0.,before_position if self.debug else None)
        if ring_key!=self._ring_key:
            region|=QRegion(self._old_ring.toAlignedRect())|QRegion(ring.toAlignedRect())
            self._old_ring=ring;self._ring_key=ring_key
            if self.debug:region|=QRegion(self.rect())
        self.position,self.ring_opacity=position,opacity
        self.before_position=before_position
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
        self._coordinates_cache=None
        if (not self._disposed and self.isVisible() and event.type()==QEvent.Type.WindowStateChange
                and (not self.isFullScreen() or self.isMinimized())):
            self.cancelled.emit('fullscreen_exited')
        super().changeEvent(event)

    def paintEvent(self,event):
        token=self.frame_timer.start() if self.frame_timer is not None and self.run.phase in {'playing','replay'} else None
        try:self._paint(event)
        finally:
            if token is not None:self.frame_timer.finish(token)

    def _paint(self,event):
        run=self.run;now=self.clock();o=self.options
        mapping=self.coordinates()
        prepare=getattr(run,'prepare_coordinates',None)
        ready=prepare(mapping,now) if prepare is not None else mapping.ready
        if ready and prepare is None:
            run.resize_viewport(*mapping.window_size,now)
            run.target_to_screen=mapping.window_to_screen
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if run.app=='stars' and run.phase in {'playing','replay','finished'}:
            snapshot=run.worker.latest()
            if not self.star_painter.draw(painter,QRectF(self.rect()),snapshot,now):painter.fillRect(self.rect(),QColor('#040713'))
            painter.setPen(QColor('#E8EEFF'));painter.setFont(self.heading_font)
            if snapshot is None:title='准备星空…'
            elif run.phase=='finished':title=f'找到 {snapshot["found_count"]} 个星座 · 绽放 {snapshot["firework_count"]} 次'
            elif run.phase=='replay':title=f'找到 {snapshot["found_count"]} 个星座 · 回看你的目光'
            else:title=f'星空凝视    {math.ceil(snapshot["remaining_s"])} 秒    星座 {snapshot["found_count"]}/{run.star_settings.constellation_count}'
            painter.drawText(QRectF(o.padding_px,0,self.width()-2*o.padding_px,o.header_px),Qt.AlignmentFlag.AlignCenter,title)
            painter.drawText(QRectF(o.padding_px,self.height()-o.footer_px,self.width()-2*o.padding_px,o.footer_px),
                             Qt.AlignmentFlag.AlignCenter,'用目光照亮星座 · 停留绽放 · Esc 退出')
            if self.debug:self._debug(painter)
            self._coordinate_warning(painter)
            painter.end()
            if ready and snapshot is not None and run.phase=='playing':run.presented(now)
            return
        painter.fillRect(self.rect(),QColor(o.background));painter.setPen(QColor('white'));painter.setFont(self.heading_font)
        title='看着中间的点，保持 '+f'{o.dwell_ms/1000:g}'+' 秒' if run.phase=='practice' else '用目光说出你的需要' if run.app=='board' else '看住亮起的目标'
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
        footer=(run.practice_message+' · ' if run.phase=='practice' and run.practice_message else '')+('语音不可用，仅显示文字。 ' if run.speech_failed else '')+'Esc 退出；触发后 1 秒内可撤销'
        painter.drawText(QRectF(o.padding_px,self.height()-o.footer_px,self.width()-2*o.padding_px,o.footer_px),Qt.AlignmentFlag.AlignCenter,footer)
        if self.options.show_gaze_ring and self.position is not None and self.ring_opacity>0:
            painter.setOpacity(self.ring_opacity);painter.setPen(QPen(QColor(o.progress_color),o.progress_px))
            painter.setBrush(Qt.BrushStyle.NoBrush);x,y=self.position;rx,ry=run.radii
            painter.drawEllipse(QRectF(x-rx,y-ry,rx*2,ry*2))
        if self.debug:self._debug(painter)
        self._coordinate_warning(painter)
        painter.end()
        if ready and run.phase in {'practice','playing'}:run.presented(now)

    def _coordinate_warning(self,painter):
        if not self.coordinate_notice:return
        painter.save();painter.setOpacity(1.)
        rect=QRectF(self.rect()).adjusted(32,self.options.header_px,-32,-self.options.footer_px)
        painter.fillRect(rect,QColor('#231820'))
        painter.setFont(self.heading_font);painter.setPen(QColor('#FFD475'))
        painter.drawText(rect.adjusted(24,24,-24,-24),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.coordinate_notice)
        painter.restore()

    def _button(self,painter,button,now):
        run=self.run;o=self.options;rect=QRectF(*button.rect)
        active=run.selector.current is not None and run.selector.current.id==button.id
        flash=now<run.flash_until and getattr(run,'flash_rect',None)==button.rect
        hole=run.app=='game' and run.phase=='playing' and button.id!='next'
        target=hole and button.id==run.target_id
        color=o.feedback_color if flash or target else o.active_color if active else o.hole_color if hole else o.button_color
        stroke=o.progress_px if active else 1
        painter.setBrush(QColor(color));painter.setPen(QPen(QColor(o.progress_color if active else color),stroke))
        painter.drawRoundedRect(rect.adjusted(stroke/2,stroke/2,-stroke/2,-stroke/2),24,24)
        if button.id=='practice':text=''
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
        text_rect=QRectF(rect.left(),rect.top()+18,rect.width(),60) if hole else rect.adjusted(20,20,-20,-20)
        painter.drawText(text_rect,Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,text)
        if button.id=='practice':
            painter.setBrush(QColor(o.text_color));painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect.center(),o.practice_target_px,o.practice_target_px)
        if active:
            diameter=min(60.,rect.height()/3);circle=QRectF(rect.center().x()-diameter/2,rect.bottom()-diameter-22,diameter,diameter)
            painter.setBrush(Qt.BrushStyle.NoBrush);painter.setPen(QPen(QColor(o.text_color),o.progress_px))
            painter.drawArc(circle,90*16,-round(run.selector.progress*360*16))

    def _debug(self,painter):
        painter.save();painter.setOpacity(1.)
        for point,color in ((self.before_position,self.options.debug_before_color),(self.position,self.options.debug_after_color)):
            if point is not None:
                radius=self.options.debug_point_px;painter.setBrush(QColor(color));painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(*point),radius,radius)
        painter.setPen(QPen(QColor(self.options.debug_after_color),2))
        for button in self.run.buttons:
            x,y,w,h=button.rect;cx,cy=x+w/2,y+h/2
            painter.drawLine(QPointF(cx-10,cy),QPointF(cx+10,cy));painter.drawLine(QPointF(cx,cy-10),QPointF(cx,cy+10))
        painter.drawRect(QRectF(self.rect()).adjusted(1,1,-1,-1))
        font=QFont(self.heading_font);font.setPixelSize(18);painter.setFont(font)
        text='粉：修正前  蓝：修正后/按钮中心  '+(self.mapping_reason or '完整内屏逻辑坐标')
        painter.drawText(QRectF(12,4,self.width()-24,28),Qt.AlignmentFlag.AlignLeft,text)
        painter.restore()
