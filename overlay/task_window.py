"""The temporary, opaque, interactive full-screen calibration/check window."""
import time

from PySide6.QtCore import Qt, Signal, QRectF, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .mapping import ScreenRect


class TaskWindow(QWidget):
    # Python object preserves the full monotonic nanosecond integer on all OSes.
    target_presented = Signal(object)
    completed = Signal(object)
    cancelled = Signal(str)

    def __init__(self, runner, screen, options, *, clock_ns=time.perf_counter_ns):
        super().__init__()
        self.runner, self.options, self._clock = runner, options, clock_ns
        self._notified = False
        self.setWindowTitle('悬浮层九点校准' if runner.kind == 'calibrate' else '悬浮层精度核对')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(screen.geometry())
        for point in runner.targets:
            if not QRectF(self.rect()).contains(self._target_rect(point['target'])):
                raise ValueError('当前屏幕或目标尺寸无法完整显示目标圆，请调整点位边距或目标大小。')
        self.winId()
        self.windowHandle().setScreen(screen)
        self._font = QFont()
        self._font.setPixelSize(options.label_font_px)

    def _target_rect(self, point):
        x, y = ScreenRect(0, 0, self.width(), self.height()).local(point)
        radius = self.options.task_radius_px
        return QRectF(x-radius, y-radius, radius*2, radius*2)

    def open(self):
        """Root calls runner.begin first and feeds/advances it on its own tick."""
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def refresh(self):
        """Draw only; scheduling and numeric-result delivery remain with root."""
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
        if (event.type() == QEvent.Type.WindowStateChange and self.runner.active and self.isVisible()
                and (not self.isFullScreen() or self.isMinimized())):
            self.runner.cancel('fullscreen_exited')
            self.refresh()
        super().changeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#141923'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)
        target = self.runner.current_target
        if target is not None:
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
        if target is None or not hint.intersects(circle):
            painter.setPen(QColor('#f1f4f8'))
            painter.drawText(hint, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        if self.runner.state == 'presenting':
            now = int(self._clock())
            if self.runner.presented(now):
                self.target_presented.emit(now)
