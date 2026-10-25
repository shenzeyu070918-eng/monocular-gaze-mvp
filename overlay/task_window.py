"""The temporary, opaque, interactive full-screen calibration/check window."""
import logging
import time

from PySide6.QtCore import Qt, Signal, QRectF, QEvent, QPointF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .mapping import ScreenRect
from gaze_core.demo_coordinates import DemoCoordinates


logger = logging.getLogger(__name__)


class TaskWindow(QWidget):
    # Python object preserves the full monotonic nanosecond integer on all OSes.
    target_presented = Signal(object)
    completed = Signal(object)
    cancelled = Signal(str)

    def __init__(self, runner, screen, options, *, clock_ns=time.perf_counter_ns, screen_rect=None):
        super().__init__()
        self.runner, self.options, self._clock = runner, options, clock_ns
        self._notified = False
        geometry = screen.geometry()
        self.screen_rect = screen_rect or ScreenRect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
        self._coordinates_cache = None
        self._validation_cache = None
        self._blocked_since_ns = None
        self._coordinate_warning = ''
        self._ack_geometry = None
        self._logged_geometry = None
        self.geometry_logger = None
        self.setWindowTitle('悬浮层九点校准' if runner.kind == 'calibrate' else '悬浮层精度核对')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(screen.geometry())
        self.winId()
        self.windowHandle().setScreen(screen)
        self._font = QFont()
        self._font.setPixelSize(options.label_font_px)

    def _target_rect(self, point):
        # Targets always belong to the complete built-in screen, even when
        # macOS places a native full-screen client area below the notch.
        x, y = self.coordinates().screen_to_window(point)
        radius = self.options.task_radius_px
        return QRectF(x-radius, y-radius, radius*2, radius*2)

    def coordinates(self):
        if self._coordinates_cache is None:
            origin = self.mapToGlobal(QPointF(0, 0))
            self._coordinates_cache = DemoCoordinates(
                self.screen_rect, window_origin=(origin.x(), origin.y()),
                window_size=(self.width(), self.height()), fullscreen=self.isFullScreen())
        return self._coordinates_cache

    def _coordinate_status(self):
        if self._validation_cache is None:
            mapping = self.coordinates()
            reason = mapping.reason.replace('Demo', '任务')
            outside = False
            if mapping.ready:
                for point in self.runner.targets:
                    if not QRectF(self.rect()).contains(self._target_rect(point['target'])):
                        reason = ('目标 '+str(point['target_id'])+' 的完整目标圆不在任务窗口实际可见区域内，'
                                  '请调整屏幕设置或目标边距后重新开始。')
                        outside = True
                        break
            self._validation_cache = (not reason, reason, outside)
        return self._validation_cache

    @property
    def coordinates_ready(self):
        return self._coordinate_status()[0]

    @property
    def coordinate_reason(self):
        return self._coordinate_status()[1]

    @property
    def coordinate_warning(self):
        return self._coordinate_warning

    def _invalidate_coordinates(self):
        self._coordinates_cache = None
        self._validation_cache = None

    def _geometry_signature(self):
        mapping = self.coordinates()
        return mapping.window_origin + mapping.window_size

    def _check_coordinates(self):
        """Freeze intake while geometry is unknown; never advance a runner."""
        ready, reason, outside = self._coordinate_status()
        now = int(self._clock())
        if self._ack_geometry is not None and self.runner.active and (
                not ready or self._geometry_signature() != self._ack_geometry):
            # A previously presented target cannot retain its old capture-time
            # sampling interval after the client area changes.
            self.runner.error = '任务窗口的实际几何已变化，请重新开始校准或精度核对。'
            self.runner.cancel('task_window_geometry_changed')
            ready, reason = False, self.runner.error
        elif outside and self.runner.active:
            self.runner.error = reason
            logger.error(reason)
            self.runner.cancel('target_outside_visible_client')
        previous = self._coordinate_warning
        if ready:
            self._blocked_since_ns = None
            self._coordinate_warning = ''
        else:
            if self._blocked_since_ns is None:
                self._blocked_since_ns = now
            warning_s = getattr(self.options, 'coordinate_warning_s', 1.)
            if outside or now-self._blocked_since_ns >= warning_s*1e9:
                self._coordinate_warning = reason
        if self._coordinate_warning != previous:
            self.update()
        return ready

    def accept_prediction(self, prediction=None):
        """The session calls this before ingesting an unfiltered prediction."""
        return self.runner.active and self._check_coordinates()

    def moveEvent(self, event):
        self._invalidate_coordinates()
        super().moveEvent(event)

    def resizeEvent(self, event):
        self._invalidate_coordinates()
        super().resizeEvent(event)

    def open(self):
        """Root calls runner.begin first and feeds/advances it on its own tick."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def refresh(self):
        """Draw only; scheduling and numeric-result delivery remain with root."""
        if self.runner.active:
            self._check_coordinates()
        if self.runner.state in {'completed', 'cancelled'} and not self._notified:
            self._notified = True
            self.hide()
            if self.runner.state == 'completed':
                self.completed.emit(self.runner.result)
            else:
                self.cancelled.emit(self.runner.reason)
            return
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.runner.cancel('cancelled')
            self.refresh()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.runner.active:
            self.runner.cancel('window_closed')
        self.refresh()
        event.accept()

    def changeEvent(self, event):
        self._invalidate_coordinates()
        if (event.type() == QEvent.Type.WindowStateChange and self.runner.active and self.isVisible()
                and (not self.isFullScreen() or self.isMinimized())):
            self.runner.cancel('fullscreen_exited')
            self.refresh()
        super().changeEvent(event)

    def paintEvent(self, event):
        ready = self._check_coordinates()
        mapping = self.coordinates()
        geometry = self._geometry_signature()
        if mapping.fullscreen and geometry != self._logged_geometry:
            self._logged_geometry = geometry
            message=('任务窗口实际客户区：全局原点=(%s, %s)，尺寸=%s×%s；内置屏=(%s, %s, %s, %s)' %
                     (*geometry,self.screen_rect.x,self.screen_rect.y,self.screen_rect.width,self.screen_rect.height))
            logger.info(message)
            if self.geometry_logger is not None:self.geometry_logger(message)
            else:print(message,flush=True)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#141923'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)
        target = self.runner.current_target
        if target is not None and ready:
            circle = self._target_rect(target['target'])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#64b5f6'))
            painter.drawEllipse(circle)
        # Omit the hint if its center rectangle would intersect the target.
        title = '九点校准' if self.runner.kind == 'calibrate' else '独立精度核对'
        progress = f'{self.runner.index+1}/{len(self.runner.targets)}'
        text = f'{title} · {progress} · Esc 取消' if target is not None else '正在等待最后的推理结果 · Esc 取消'
        metrics = QFontMetrics(self._font)
        hint = QRectF(0, 0, min(self.width(), metrics.horizontalAdvance(text)+self.options.label_padding_px*2),
                      metrics.height()+self.options.label_padding_px)
        hint.moveCenter(QRectF(self.rect()).center())
        if self._coordinate_warning:
            painter.setPen(QColor('#ffcf78'))
            painter.drawText(QRectF(self.rect()).adjusted(24, 24, -24, -24),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._coordinate_warning+'\nEsc 取消')
        elif not ready:
            painter.setPen(QColor('#f1f4f8'))
            painter.drawText(hint, Qt.AlignmentFlag.AlignCenter, '正在确认任务窗口坐标 · Esc 取消')
        elif target is None or not hint.intersects(circle):
            painter.setPen(QColor('#f1f4f8'))
            painter.drawText(hint, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        if ready and self.runner.state == 'presenting':
            now = int(self._clock())
            if self.runner.presented(now):
                self._ack_geometry = geometry
                self.target_presented.emit(now)
