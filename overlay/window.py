"""Rendering only. All positions are already logical pixels; never camera data."""
import time
from PySide6.QtCore import Qt,QRectF,QTimer,QEvent
from PySide6.QtGui import QColor,QPainter,QPen,QFont,QFontMetrics,QRegion,QImage
from PySide6.QtWidgets import QWidget
from .mapping import ScreenRect
from .label import LabelVisibility


class OverlayWindow(QWidget):
    def __init__(self, screen, options, *, native_configure=None, diagnostic=False, clock=time.monotonic):
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
        self._heatmap_image=None;self._heatmap_key=None;self._heatmap_enabled=False;self._heatmap_uncalibrated=False
        self._heatmap_coarse=False
        self.label='未校准 · 窗口诊断（无摄像头）' if diagnostic else '未校准 · 正在启动'
        font=QFont();font.setPixelSize(options.label_font_px);self.setFont(font)
        self._label_metrics=QFontMetrics(font)
        self._label_layout_key=None;self._label_layout_rect=None
        self._observed_status=(self.label,'immediate')
        self._label_visibility=LabelVisibility(self.label,options.label_autohide_s,options.fade_ms,
                                               debounce_s=options.label_debounce_s,diagnostic=diagnostic,clock=clock)
        self._label_opacity=1.
        self._label_timer=QTimer(self);self._label_timer.setInterval(options.refresh_ms)
        self._label_timer.timeout.connect(self._refresh_label)
        if not diagnostic and options.label_autohide_s>0:self._label_timer.start()

    def prepare_native(self):
        # Call outside a Qt virtual callback so failures abort startup visibly.
        self.winId()
        if self.native_configure:self.native_configure(self)

    def attach_screen(self,selection):
        self.screen_selection=selection;self.rect_info=selection.rect
        if selection.qscreen is not None and self.windowHandle() is not None:self.windowHandle().setScreen(selection.qscreen)
        rect=selection.rect;self.setGeometry(rect.x,rect.y,rect.width,rect.height)
        if self.native_configure:self.native_configure(self)

    def change_screen(self,selection):
        self.set_heatmap(None,enabled=False);self.position=None;self.opacity=0.
        self._label_layout_key=None;self.attach_screen(selection)
        self.update()

    def _ring_bounds(self):
        if self.position is None:return QRectF()
        x,y=self.position;rx,ry=self.radii;pad=self.options.stroke_px+2
        return QRectF(x-rx-pad,y-ry-pad,2*(rx+pad),2*(ry+pad))

    def set_display(self,position,radii,opacity,label,*,label_kind='immediate'):
        ring_changed=(position,radii,opacity)!=(self.position,self.radii,self.opacity)
        if ring_changed:
            old_bounds=self._ring_bounds()
            self.position,self.radii,self.opacity=position,radii,opacity
            region=QRegion(old_bounds.toAlignedRect())|QRegion(self._ring_bounds().toAlignedRect())
            if not region.isEmpty():self.update(region.intersected(QRegion(self.rect())))
        self.set_status(label,kind=label_kind)

    def set_status(self,label,*,kind='immediate',force=False):
        observed=(label,'immediate' if force else kind)
        if not force and observed==self._observed_status:
            # The label timer owns debounce/fade progression. Also advance when
            # called directly while it is active, without observing the same
            # status again or restarting either clock.
            if self._label_timer.isActive():self._refresh_label()
            return
        self._label_visibility.set_text(label,kind=kind,force=force)
        self._observed_status=observed
        self._refresh_label()

    def _label_rect(self):
        width,height=self.width(),self.height()
        key=(self.label,width,height)
        if key==self._label_layout_key:return QRectF(self._label_layout_rect)
        inset=min(self.options.label_inset_px,(width-1)/2)
        text_width=self._label_metrics.horizontalAdvance(self.label)
        bar_width=min(text_width+2*self.options.label_padding_px,width-2*inset)
        bar_height=min(self._label_metrics.height()+self.options.label_padding_px,height)
        if self.options.label_position=='top_left':
            x=inset;y=min(self.options.label_inset_px,height-bar_height)
        else:
            x=(width-bar_width)/2 if self.options.label_position=='bottom_center' else width-inset-bar_width
            y=max(0,height-self.options.label_bottom_inset_px-bar_height)
        self._label_layout_key=key;self._label_layout_rect=QRectF(x,y,bar_width,bar_height)
        # paintEvent adjusts its local text rectangle; never expose the cache.
        return QRectF(self._label_layout_rect)

    def changeEvent(self,event):
        if event.type()==QEvent.Type.FontChange and hasattr(self,'_label_metrics'):
            old=self._label_rect()
            self._label_metrics=QFontMetrics(self.font());self._label_layout_key=None
            self._update_label_regions(old,self._label_rect())
        super().changeEvent(event)

    def _update_label_regions(self,old,new):
        region=QRegion(old.toAlignedRect())|QRegion(new.toAlignedRect())
        self.update(region.intersected(QRegion(self.rect())))

    def _refresh_label(self):
        opacity=self._label_visibility.opacity()
        label=self._label_visibility.text
        if label!=self.label or opacity!=self._label_opacity:
            old_rect=self._label_rect()
            self.label=label;self._label_opacity=opacity
            self._update_label_regions(old_rect,self._label_rect())
        ticking=self._label_visibility.pending or (opacity>0 and not self.diagnostic and self.options.label_autohide_s>0)
        if ticking and not self._label_timer.isActive():self._label_timer.start()
        elif not ticking:self._label_timer.stop()

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._heatmap_enabled:self._paint_heatmap(painter)  # Always below the ring.
        painter.setFont(self.font())
        if self._label_opacity>0:
            painter.save();painter.setOpacity(self._label_opacity)
            text_rect=self._label_rect()
            painter.fillRect(text_rect,QColor(20,25,35,190))
            text_rect.adjust(self.options.label_padding_px,0,-self.options.label_padding_px,0)
            if text_rect.width()>0:
                text=self._label_metrics.elidedText(self.label,Qt.TextElideMode.ElideRight,int(text_rect.width()))
                painter.setPen(QColor('white'));painter.drawText(text_rect,Qt.AlignmentFlag.AlignCenter,text)
            painter.restore()
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

    def set_heatmap(self,latest,*,enabled,uncalibrated=False,coarse_calibrated=False):
        if not enabled:
            was_visible=self._heatmap_enabled
            self._heatmap_enabled=False;self._heatmap_key=None
            # Hide once. Hidden accumulation must not copy images, invalidate
            # window regions or add any future heatmap repaint requests.
            if was_visible:self.update()
            return was_visible
        key=(None if latest is None else latest['version'],bool(enabled),bool(uncalibrated),bool(coarse_calibrated))
        if key==self._heatmap_key:return False
        old_enabled=self._heatmap_enabled;self._heatmap_key=key
        self._heatmap_enabled=bool(enabled);self._heatmap_uncalibrated=bool(uncalibrated)
        self._heatmap_coarse=bool(coarse_calibrated)
        if latest is not None and (self._heatmap_image is None or getattr(self,'_heatmap_version',None)!=latest['version']):
            if 'image' in latest:self._heatmap_image=QImage(latest['image'])
            else:
                rgba=latest['rgba'];height,width=rgba.shape[:2]
                self._heatmap_image=QImage(rgba.data,width,height,rgba.strides[0],QImage.Format.Format_RGBA8888).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            self._heatmap_version=latest['version']
        if old_enabled or self._heatmap_enabled:self.update()
        return True

    def _paint_heatmap(self,painter):
        if not self._heatmap_enabled:return
        from gaze_core.heatmap import NOTICE
        painter.save();painter.setOpacity(1.)
        if self._heatmap_image is not None:
            # Bilinear interpolation of premultiplied alpha avoids dark edge
            # halos and nearest-neighbour stair steps on the enlarged grid.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            painter.drawImage(QRectF(self.rect()),self._heatmap_image)
        footer=QRectF(12,max(0,self.height()-52),max(1,self.width()-24),40)
        painter.fillRect(footer,QColor(20,25,35,190))
        painter.setPen(QColor('white'))
        font=painter.font();font.setPixelSize(11);painter.setFont(font)
        painter.drawText(footer,Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,
                         ('未校准 · ' if self._heatmap_uncalibrated else '粗校准 · ' if self._heatmap_coarse else '')+NOTICE)
        painter.restore()
