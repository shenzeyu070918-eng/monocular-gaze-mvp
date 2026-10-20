"""Deterministic runtime label tests: offscreen widgets, no camera or event loop."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from contextlib import redirect_stderr
from dataclasses import replace
import io
import signal
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from overlay.controller import ControllerView
from overlay.mapping import ScreenRect
from overlay.runtime import run_camera_overlay
from overlay.window import OverlayWindow


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def ns(self):
        return round(self.now * 1_000_000_000)


class DirectController:
    """Requests complete immediately, so snapshots never expose 'starting'."""

    def __init__(self, clock):
        self.clock = clock
        self.closed = threading.Event()
        self.thread = Mock()
        self.status = 'paused'
        self.error = None
        self.result = None
        self.idle = False
        self.sequence = 0
        self.requests = []
        self.observed_statuses = []

    def publish(self, status='tracking', *, idle=False):
        self.sequence += 1
        now = self.clock.ns()
        self.result = Prediction(self.sequence, now, now, {
            'valid': status == 'tracking', 'status': status,
            'display': {'position': [.5, .5], 'timestamp_source': 'capture_ns'},
        })
        self.idle = idle

    def fail(self, message):
        self.status = 'error'
        self.error = message
        self.result = None
        self.idle = False

    def request(self, action):
        self.requests.append(action)
        self.status = action
        self.error = None
        self.result = None
        self.idle = False
        if action == 'running':
            self.publish()
        elif action == 'closed':
            self.closed.set()

    def snapshot(self):
        self.observed_statuses.append(self.status)
        return ControllerView(self.status, self.error, self.result, self.idle)


class SynchronousApplication(QObject):
    """Only runtime orchestration is proxied; OverlayWindow is a real QWidget."""

    screenAdded = Signal(object)
    screenRemoved = Signal(object)

    def __init__(self):
        super().__init__()
        self.runner = None
        self.quitted = False

    def screens(self):
        return []

    def exec(self):
        return self.runner()

    def quit(self):
        self.quitted = True


class RuntimeLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setQuitOnLastWindowClosed(False)

    def exercise(self, scenario):
        clock = Clock()
        settings = load_settings(use_local=False)
        settings = replace(settings,
            geometry=replace(settings.geometry, width_cm=30, height_cm=20, measured=True),
            overlay=replace(settings.overlay, label_autohide_s=4., label_debounce_s=1., fade_ms=200.))
        controller = DirectController(clock)
        window = OverlayWindow(ScreenRect(0, 0, 800, 600), settings.overlay, clock=clock)
        tray = QSystemTrayIcon()
        pause = QAction('暂停')
        quit_action = QAction('退出')
        app = SynchronousApplication()
        quit_action.triggered.connect(app.quit)
        timer = QTimer()
        qt_errors = []
        old_signal = signal.getsignal(signal.SIGINT)
        context = SimpleNamespace(clock=clock, controller=controller, window=window,
                                  tray=tray, pause=pause, app=app, timer=timer)

        def runner():
            scenario(context)
            quit_action.trigger()
            timer.timeout.emit()
            self.assertTrue(app.quitted)
            return 0

        app.runner = runner
        try:
            with patch('overlay.runtime.OverlayController', return_value=controller), \
                 patch('overlay.runtime.time.perf_counter_ns', side_effect=clock.ns), \
                 patch('overlay.runtime.QMessageBox') as message_box, \
                 patch.object(tray, 'showMessage') as tray_message, \
                 patch.object(window, 'set_status', wraps=window.set_status) as status, \
                 patch('sys.excepthook', side_effect=lambda *error: qt_errors.append(error)), \
                 redirect_stderr(io.StringIO()):
                context.status = status
                context.message_box = message_box
                context.tray_message = tray_message
                self.assertEqual(run_camera_overlay(app, window, tray, pause, quit_action,
                                                    timer, settings, lambda: None), 0)
            self.assertFalse(qt_errors, qt_errors)
            self.assertTrue(controller.closed.is_set())
            self.assertEqual(controller.requests[-1], 'closed')
            controller.thread.join.assert_not_called()
        finally:
            signal.signal(signal.SIGINT, old_signal)
            timer.stop()
            window._label_timer.stop()
            window.close()
            window.deleteLater()
            tray.hide()
            self.qt_app.processEvents()

    def tick(self, context, now, status='tracking', *, idle=False):
        context.clock.now = now
        context.controller.publish(status, idle=idle)
        context.timer.timeout.emit()

    def hide_startup_label(self, context):
        self.tick(context, 0.)
        self.tick(context, 6.)
        self.assertEqual(context.window._label_opacity, 0.)

    def test_pause_resume_force_immediate_visibility_without_starting_snapshot(self):
        def scenario(c):
            self.hide_startup_label(c)
            c.status.reset_mock()
            c.pause.trigger()
            c.status.assert_called_once_with('未校准 · 正在暂停', force=True)
            self.assertEqual(c.window.label, '未校准 · 正在暂停')
            self.assertEqual(c.window._label_opacity, 1.)
            self.assertEqual(c.controller.status, 'paused')

            c.timer.timeout.emit()
            c.clock.now = 12.
            c.timer.timeout.emit()
            self.assertEqual(c.window._label_opacity, 0.)
            c.status.reset_mock()
            c.pause.trigger()
            c.status.assert_called_once_with('未校准 · 正在恢复摄像头/模型', force=True)
            self.assertEqual(c.window.label, '未校准 · 正在恢复摄像头/模型')
            self.assertEqual(c.window._label_opacity, 1.)
            self.assertEqual(c.controller.status, 'running')

            c.timer.timeout.emit()
            self.assertIn('Python 摄像头', c.window.label)
            self.tick(c, 16.1)
            self.assertAlmostEqual(c.window._label_opacity, .5)
            self.tick(c, 16.3)
            self.assertEqual(c.window._label_opacity, 0.)
            self.assertNotIn('starting', c.controller.observed_statuses)
            self.assertEqual(c.controller.requests, ['running', 'paused', 'running'])

        self.exercise(scenario)

    def test_tray_remains_live_during_short_anomalies_and_recovery_does_not_wake_label(self):
        def scenario(c):
            self.hide_startup_label(c)
            tracking_label = c.window.label
            for now, state, idle, tooltip in (
                (6.1, 'blink', False, '眨眼'),
                (6.3, 'tracking', False, 'Python 摄像头'),
                (6.5, 'no_face', False, '未检测到人脸'),
                (6.6, 'no_face', True, '无人脸，降低读取频率'),
                (6.9, 'tracking', False, 'Python 摄像头'),
            ):
                with self.subTest(status=state, idle=idle):
                    self.tick(c, now, state, idle=idle)
                    self.assertIn(tooltip, c.tray.toolTip())
                    self.assertEqual(c.window.label, tracking_label)
                    self.assertEqual(c.window._label_opacity, 0.)
            self.assertFalse(c.window._label_visibility.pending)

            self.tick(c, 7., 'no_face')
            self.tick(c, 7.9, 'no_face')
            self.assertEqual(c.window._label_opacity, 0.)
            self.tick(c, 8.01, 'no_face')
            self.assertEqual(c.window.label, '未校准 · 未检测到人脸')
            self.assertEqual(c.window._label_opacity, 1.)
            self.tick(c, 8.1)
            self.assertEqual(c.window.label, tracking_label)
            self.tick(c, 12.11)
            self.assertAlmostEqual(c.window._label_opacity, .5)
            self.tick(c, 12.3)
            self.assertEqual(c.window._label_opacity, 0.)

        self.exercise(scenario)

    def test_screen_change_immediately_overrides_pending_anomaly(self):
        def scenario(c):
            self.hide_startup_label(c)
            self.tick(c, 6.1, 'blink')
            self.assertTrue(c.window._label_visibility.pending)
            c.status.reset_mock()
            c.app.screenAdded.emit(None)
            c.status.assert_called_once_with('未校准 · 屏幕变化，请重新启动', force=True)
            self.assertEqual(c.window.label, '未校准 · 屏幕变化，请重新启动')
            self.assertEqual(c.window._label_opacity, 1.)
            self.assertFalse(c.window._label_visibility.pending)
            self.assertFalse(c.pause.isEnabled())
            self.assertEqual(c.controller.requests[-1], 'paused')
            c.timer.timeout.emit()
            self.assertEqual(c.tray.toolTip(), c.window.label)
            c.tray_message.assert_called_once()

        self.exercise(scenario)

    def test_error_immediately_overrides_pending_anomaly_without_renewing_each_tick(self):
        def scenario(c):
            self.hide_startup_label(c)
            self.tick(c, 6.1, 'blink')
            self.assertTrue(c.window._label_visibility.pending)
            c.clock.now = 6.2
            c.controller.fail('fake camera error')
            c.status.reset_mock()
            c.timer.timeout.emit()
            c.status.assert_any_call('未校准 · 采集失败，请查看提示', force=True)
            self.assertEqual(c.window.label, '未校准 · 采集失败，请查看提示')
            self.assertEqual(c.window._label_opacity, 1.)
            self.assertFalse(c.window._label_visibility.pending)
            self.assertEqual(c.tray.toolTip(), c.window.label)
            c.message_box.return_value.open.assert_called_once_with()

            c.clock.now = 12.
            c.timer.timeout.emit()
            self.assertEqual(c.window._label_opacity, 0.)
            forced = [call for call in c.status.call_args_list if call.kwargs.get('force')]
            self.assertEqual(len(forced), 1)
            c.message_box.return_value.open.assert_called_once_with()

        self.exercise(scenario)


if __name__ == '__main__':
    unittest.main()
