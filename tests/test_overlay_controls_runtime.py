"""Deterministic controls-branch integration; no camera, files, browser or waits."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from contextlib import redirect_stderr
from dataclasses import replace
import io
import signal
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction,QImage
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox as QtMessageBox

from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from overlay.actions import OverlayActions
from overlay.controller import ControllerView
from overlay.mapping import ScreenRect
from overlay.panel import ControlPanel
from overlay.runtime import run_camera_overlay
from overlay.window import OverlayWindow


class Clock:
    def __init__(self): self.now = 0.
    def __call__(self): return self.now
    def ns(self): return round(self.now*1_000_000_000)


class Recording:
    def __init__(self):
        self.is_manual = False
        self.task_id = None
        self.error = None
        self.reject_start = False
        self.calls = []
        self.closed = False

    def manual(self, enabled):
        self.calls.append(bool(enabled))
        if enabled and self.reject_start:
            self.error = 'synthetic disk failure'
            self.is_manual = False
            return False
        self.is_manual = bool(enabled)
        return True

    def status(self):
        return dict(active=self.is_manual or self.task_id is not None,
                    manual=self.is_manual, task_id=self.task_id, error=self.error)

    def close(self):
        self.is_manual = False
        self.task_id = None
        self.closed = True


class Profiles:
    def __init__(self):
        self.profile = None
        self.conditions = {}
        self.busy = False
        self.mode = '未校准'
        self.closed = False
        self.disable_calls = 0
        self.web_calls = []

    def disable(self):
        self.disable_calls += 1
        self.profile = None
        self.mode = '未校准'

    def load_web(self, path, *, confirmed):
        self.web_calls.append((path, confirmed))

    def close(self): self.closed = True


class Session:
    """Expose the real runtime contract while replacing all persistence."""
    def __init__(self, settings, screen, *, notice, restore, results):
        self.settings, self.screen = settings, screen
        self.notice, self.restore, self.results = notice, restore, results
        self.recording, self.profiles = Recording(), Profiles()
        self.controller = None
        self.latest = None
        self.accept_latest = True
        self.buffer = Mock()
        self.active = False
        self.display_revision = 0
        self.matrix_since_ns = 0
        self.radius = None
        self.radius_text = '半径来源：配置示意值（非实测精度）'
        self.cancel_calls, self.start_calls, self.sink_calls = [], [], []
        self.record_calibration = Mock()
        self.closed = False

    @property
    def short_mode(self):
        if self.profiles.profile is None: return '未校准'
        return '九点（web 手动）' if self.profiles.profile.get('pipeline') == 'web' else '九点'

    def sink(self, result): self.sink_calls.append(result)

    def tick(self, view):
        if self.accept_latest and view.result is not None:
            self.latest = view.result

    def start(self, kind, screen):
        if self.active: raise ValueError('已有任务')
        self.start_calls.append((kind, screen))
        self.active = True
        self.recording.task_id = 'synthetic-task'

    def cancel(self, reason='cancelled'):
        self.cancel_calls.append(reason)
        if self.active:
            self.active = False
            self.recording.task_id = None
            self.restore()

    def change_screen(self,screen):
        self.screen=screen;self.latest=None;self.profiles.disable();self.profiles.screen_mismatch=True

    def close(self):
        self.recording.close()
        self.profiles.close()
        self.closed = True


class Controller:
    def __init__(self, clock, *, result_sink, shutdown):
        self.clock, self.result_sink, self.shutdown = clock, result_sink, shutdown
        self.closed = threading.Event()
        self.thread = Mock()
        self.status = 'paused'
        self.error = None
        self.result = None
        self.requests = []
        self.sequence = 0
        self.matrix_revision = 0

    def publish(self, status='tracking'):
        self.sequence += 1
        now = self.clock.ns()
        self.result = Prediction(self.sequence, now, now, dict(
            valid=status == 'tracking', status=status,
            display=dict(position=[.5, .5], timestamp_source='capture_ns')))
        self.result_sink(self.result)

    def request(self, action):
        self.requests.append(action)
        self.status, self.result, self.error = action, None, None
        if action == 'running': self.publish()
        elif action == 'closed':
            self.shutdown()
            self.closed.set()

    def snapshot(self):
        return ControllerView(self.status, self.error, self.result, False,
                              dict(device='cpu', device_name='synthetic CPU',predicted_frames=self.sequence),self.matrix_revision)


class Application(QObject):
    screenAdded = Signal(object)
    screenRemoved = Signal(object)

    def __init__(self):
        super().__init__()
        self.scenario = None
        self.quitted = False

    def screens(self): return []
    def exec(self): return self.scenario()
    def quit(self): self.quitted = True


class ControlsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setQuitOnLastWindowClosed(False)

    def exercise(self, scenario, *, heatmap=False,autocal=False,profile_seconds=None):
        clock = Clock()
        settings = load_settings(use_local=False)
        settings = replace(settings, geometry=replace(settings.geometry, width_cm=30., height_cm=20., measured=True))
        app = Application()
        actions = OverlayActions()
        panel = ControlPanel(actions)
        actions.connect('panel',panel.present)
        menu = QMenu(panel)
        actions.add_to_menu(menu)
        controls = SimpleNamespace(actions=actions, panel=panel, screen=object(), mode=QAction('未校准'))
        window = OverlayWindow(ScreenRect(0, 0, 800, 600), settings.overlay, clock=clock)
        tray = QSystemTrayIcon()  # Deliberately never shown or given a native tray geometry.
        timer = QTimer()
        actions['quit'].triggered.connect(app.quit)
        context = SimpleNamespace(clock=clock, settings=settings, app=app, actions=actions,
                                  panel=panel, menu=menu, controls=controls, window=window,
                                  tray=tray, timer=timer)
        qt_errors = []
        heat_timers=[]
        class HeatTimer(QTimer):
            def __init__(self):super().__init__();heat_timers.append(self)
        context.heat_timers=heat_timers
        old_signal = signal.getsignal(signal.SIGINT)

        def make_session(*args, **kwargs):
            context.session = Session(*args, **kwargs)
            if autocal:
                context.session.autocal=Mock(enabled=False,error=None)
                context.session.autocal_text='点击自动粗校准：关闭'
                context.session._active_group='A'
                def enable_coarse(value):
                    context.session.autocal.enabled=bool(value)
                    context.session._active_group='C' if value else 'A'
                    context.session.autocal_text='点击粗校准 · 已用 12 次点击 · 偏移 +0.60/+0.60 cm' if value else '点击自动粗校准：关闭'
                    context.session.mode=context.session.autocal_text if value else '未校准'
                context.session.set_autocal_enabled=enable_coarse
            if heatmap:
                context.heatmap=Mock(enabled=False,viewing=False)
                context.heat_state=dict(enabled=False,viewing=False,uncalibrated=False,error=None,label='热力图：关闭',
                                        exporting=False,export_file=None,latest=None,processed=0,has_session=False,
                                        kernel_text='核尺度 sigma = 2.000 cm · 来源：最近匹配 B 核对平均误差')
                context.heatmap.state.side_effect=lambda:dict(context.heat_state)
                def enable(value):
                    context.heat_state.update(enabled=value,viewing=False,has_session=True,
                                              label='热力图：'+('开启（后台累加）' if value else '关闭'))
                    context.heatmap.enabled=value;context.heatmap.viewing=False;return True
                def view(value):
                    context.heat_state['viewing']=value;context.heatmap.viewing=value;return True
                context.heatmap.set_enabled.side_effect=enable
                context.heatmap.set_viewing.side_effect=view
                context.session.heatmap=context.heatmap
                context.session.set_heatmap_enabled=context.heatmap.set_enabled
                context.session.set_heatmap_viewing=context.heatmap.set_viewing
            return context.session

        def make_controller(received_settings, *, result_sink, shutdown):
            self.assertIs(received_settings, settings)
            context.controller = Controller(clock, result_sink=result_sink, shutdown=shutdown)
            return context.controller

        def run():
            context.hotkey_dispatch=hotkeys.call_args.args[1]
            context.screen_event=monitor.call_args.args[2]
            scenario(context)
            if not context.controller.closed.is_set(): panel.buttons['quit'].click()
            timer.timeout.emit()
            self.assertTrue(app.quitted)
            return 0

        app.scenario = run
        try:
            with patch('overlay.session.OverlaySession', side_effect=make_session), \
                 patch('overlay.hotkeys.Hotkeys') as hotkeys, \
                 patch('overlay.runtime.OverlayController', side_effect=make_controller), \
                 patch('overlay.runtime.time.perf_counter_ns', side_effect=clock.ns), \
                 patch('overlay.runtime.QTimer',HeatTimer), \
                 patch('overlay.runtime.ScreenMonitor') as monitor, \
                 patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as open_web, \
                 patch('PySide6.QtWidgets.QFileDialog.getOpenFileName', return_value=('', '')) as select_web, \
                 patch('overlay.runtime.QMessageBox') as message_box, \
                 patch('sys.excepthook', side_effect=lambda *error: qt_errors.append(error)), \
                 redirect_stderr(io.StringIO()):
                context.open_web, context.message_box, context.select_web = open_web, message_box, select_web
                hotkeys.return_value.text.return_value='fake registered hotkeys'
                hotkeys.return_value.status={}
                message_box.StandardButton = QtMessageBox.StandardButton
                self.assertEqual(run_camera_overlay(app, window, tray, actions['pause'], actions['quit'],
                                                    timer, settings, lambda: None, controls=controls,profile_seconds=profile_seconds), 0)
            self.assertFalse(qt_errors, qt_errors)
            self.assertTrue(context.controller.closed.is_set())
            self.assertTrue(context.session.closed)
            self.assertTrue(context.session.recording.closed)
            self.assertTrue(context.session.profiles.closed)
            context.controller.thread.join.assert_not_called()
        finally:
            signal.signal(signal.SIGINT, old_signal)
            timer.stop()
            window._label_timer.stop()
            window.close()
            window.deleteLater()
            tray.hide()
            panel.hide()
            panel.deleteLater()
            self.qt_app.processEvents()

    def test_demo_hotkey_uses_shared_entry_task_gates_and_pause_cleanup(self):
        from overlay.demo_session import DemoSession
        manager=Mock(spec=DemoSession);manager.active=False
        manager.start.side_effect=lambda *_:setattr(manager,'active',True)
        manager.cancel.side_effect=lambda *_,**__:setattr(manager,'active',False)
        def scenario(c):
            self.tick(c)
            board,game=object(),object()
            dialog=c.message_box.return_value
            dialog.addButton.side_effect=[board,game,object()];dialog.clickedButton.return_value=board
            c.hotkey_dispatch('demo');self.qt_app.processEvents()
            manager.start.assert_called_once_with('board',c.controls.screen)
            self.tick(c,.1)
            manager.tick.assert_called_once()
            self.assertFalse(c.actions['calibrate'].isEnabled());self.assertFalse(c.actions['check'].isEnabled())
            self.assertFalse(c.actions['toggle'].isEnabled())
            c.actions['presentation'].trigger();self.assertEqual(c.window.font().pixelSize(),26)
            c.actions['pause'].trigger();manager.cancel.assert_called_with('camera_paused')
        with patch('overlay.demo_session.DemoSession',return_value=manager):self.exercise(scenario)

    def test_projector_unchanged_does_not_pause_and_resize_recovers_without_restart(self):
        from overlay.screens import BuiltinScreen,SCREEN_CHANGED
        def scenario(c):
            c.timer.timeout.emit()
            selected=BuiltinScreen(self.qt_app.primaryScreen(),c.window.rect_info,'same')
            requests=list(c.controller.requests)
            c.screen_event('unchanged',selected,None)
            self.assertEqual(c.controller.requests,requests)
            resized=BuiltinScreen(selected.qscreen,ScreenRect(0,0,1000,700,1.5),'same')
            c.screen_event('changed',resized,None)
            self.assertEqual(c.controller.requests[-1],'paused')
            c.timer.timeout.emit()
            self.assertEqual(c.window.rect_info,resized.rect);self.assertEqual(c.session.screen,resized.rect)
            self.assertTrue(c.actions['pause'].isEnabled())
            self.assertIn(SCREEN_CHANGED,c.panel.notice.text())
            c.actions['pause'].trigger();c.timer.timeout.emit()
            self.assertEqual(c.controller.status,'running');self.assertTrue(c.actions['calibrate'].isEnabled())
            self.assertEqual(c.session.profiles.disable_calls,2)
        self.exercise(scenario)

    def test_clamshell_unavailable_blocks_resume_and_does_not_draw_external(self):
        def scenario(c):
            c.screen_event('unavailable',None,'内置屏不可用，请打开屏幕')
            c.timer.timeout.emit()
            self.assertFalse(c.window.isVisible());self.assertFalse(c.actions['pause'].isEnabled())
            requests=list(c.controller.requests);c.actions['pause'].trigger()
            self.assertEqual(c.controller.requests,requests)
        self.exercise(scenario)

    def tick(self, context, now=None, *, publish=True):
        if now is not None: context.clock.now = now
        if publish and context.controller.status == 'running': context.controller.publish()
        context.timer.timeout.emit()

    def test_heatmap_runtime_binds_controls_low_rate_timer_and_preserves_existing_tasks(self):
        def scenario(c):
            self.assertEqual(len(c.heat_timers),1);timer=c.heat_timers[0]
            self.assertEqual(timer.interval(),1000)
            c.actions['heatmap_toggle'].trigger();timer.timeout.emit()
            self.assertTrue(c.actions['heatmap_toggle'].isChecked());self.assertIn('开启',c.panel.heatmap.text())
            self.assertFalse(c.actions['heatmap_view'].isChecked())
            self.assertIn('2.000 cm',c.panel.heatmap_sigma.text())
            self.assertIn('最近匹配 B',c.panel.heatmap_sigma.text())
            c.actions['heatmap_clear'].trigger();c.heatmap.clear.assert_called_once()
            # Hidden accumulation exports even though it has never produced an image.
            c.actions['heatmap_export'].trigger();c.heatmap.export.assert_called_once()
            with patch.object(c.window,'set_heatmap',wraps=c.window.set_heatmap) as image, \
                 patch.object(c.window,'update',wraps=c.window.update) as repaint:
                for version in range(1,5):
                    c.heat_state['latest']=dict(version=version,rgba=__import__('numpy').zeros((8,10,4),dtype='uint8'))
                    timer.timeout.emit()
                image.assert_not_called();repaint.assert_not_called()
                c.hotkey_dispatch('heatmap_view');self.qt_app.processEvents()
                self.assertTrue(c.actions['heatmap_view'].isChecked())
                self.assertTrue(c.window._heatmap_enabled)
                image.reset_mock();repaint.reset_mock()
                c.actions['heatmap_view'].trigger()
                self.assertEqual(image.call_count,1);self.assertEqual(repaint.call_count,1)
                image.reset_mock();repaint.reset_mock()
                for _ in range(4):timer.timeout.emit()
                image.assert_not_called();repaint.assert_not_called()
            with patch.object(c.window,'set_heatmap',wraps=c.window.set_heatmap) as image:
                for _ in range(20):self.tick(c,publish=False)
                image.assert_not_called()  # The original tick never polls heatmap grids.
            self.tick(c);c.actions['calibrate'].trigger()
            self.assertEqual(c.session.start_calls,[('calibrate',c.controls.screen)])
            c.session.cancel();self.tick(c);c.actions['pause'].trigger()
            c.heatmap.interrupt.assert_called()
            c.heat_state.update(enabled=False,error='热力图测试失败',label='热力图测试失败')
            timer.timeout.emit();self.assertIn('热力图测试失败',c.panel.notice.text())
            self.assertFalse(c.actions['heatmap_toggle'].isChecked())
        self.exercise(scenario,heatmap=True)

    def test_autocal_panel_and_tray_share_switch_and_show_C_check_label(self):
        def scenario(c):
            self.tick(c)
            c.panel.resize(400,480);c.panel.show();self.qt_app.processEvents()
            self.assertLessEqual(c.panel.height(),480)
            self.assertGreater(c.panel.scroll.verticalScrollBar().maximum(),0)
            self.assertFalse(c.actions['autocal'].isChecked())
            self.assertIs(c.panel.buttons['autocal'].defaultAction(),c.actions['autocal'])
            self.assertIn(c.actions['autocal'],c.menu.actions())
            c.panel.buttons['autocal'].click();self.tick(c)
            self.assertTrue(c.actions['autocal'].isChecked())
            self.assertIn('已用 12',c.panel.autocal.text());self.assertIn('偏移',c.panel.mode.text())
            self.assertIn('A/C',c.actions['check'].text())
            c.actions['autocal'].trigger();self.tick(c)
            self.assertFalse(c.actions['autocal'].isChecked());self.assertIn('A/B',c.actions['check'].text())
        self.exercise(scenario,autocal=True)

    def test_profile_includes_heatmap_callbacks_and_actual_count_rate_without_config_substitution(self):
        from overlay.profiling import CallbackProfiler
        profiles=[]
        with tempfile.TemporaryDirectory() as directory:
            def factory(*args,**kwargs):
                value=CallbackProfiler(*args,**kwargs,directory=directory,stream=io.StringIO());profiles.append(value);return value
            def scenario(c):
                self.tick(c,1.)
                c.actions['heatmap_toggle'].trigger()
                c.heat_state['latest']=dict(version=1,rgba=__import__('numpy').zeros((8,10,4),dtype='uint8'))
                c.actions['heatmap_view'].trigger()
                c.heat_timers[0].timeout.emit()
                c.window.show();self.qt_app.processEvents()
                image=QImage(c.window.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);c.window.render(image)
                self.tick(c,2.)
            with patch('overlay.profiling.CallbackProfiler',side_effect=factory):self.exercise(scenario,heatmap=True,profile_seconds=1.)
            summary=profiles[0].summary
            self.assertTrue({'tick','heatmap_update','heatmap_paint'}<=set(summary['callbacks']))
            rate=summary['context']['profile_window_inference']
            self.assertEqual(rate['predictions'],1);self.assertEqual(rate['seconds'],1.);self.assertEqual(rate['fps'],1.)

    def test_queued_panel_hotkey_fronts_hidden_and_visible_panel_each_time(self):
        from overlay.platforms import present_panel
        def scenario(c):
            for visible in (False,True):
                c.panel.setVisible(visible)
                with patch('overlay.platforms.present_panel',wraps=present_panel) as front:
                    c.hotkey_dispatch('panel')
                    self.qt_app.processEvents()
                    front.assert_called_once_with(c.panel)
                self.assertTrue(c.panel.isVisible())
        self.exercise(scenario)

    def test_unchanged_ticks_skip_render_and_qt_setters_but_stale_results_still_fade(self):
        def scenario(c):
            self.tick(c,1.)
            with patch.object(c.window,'set_display',wraps=c.window.set_display) as render, \
                 patch.object(c.tray,'setToolTip',wraps=c.tray.setToolTip) as tooltip, \
                 patch.object(c.panel.mode,'setText',wraps=c.panel.mode.setText) as text, \
                 patch.object(c.actions['pause'],'setEnabled',wraps=c.actions['pause'].setEnabled) as enabled:
                for _ in range(100):self.tick(c,publish=False)
                render.assert_not_called();tooltip.assert_not_called();text.assert_not_called();enabled.assert_not_called()
                self.tick(c,1.31,publish=False)
                self.assertTrue(render.called)
                self.assertGreater(c.window.opacity,0.)
                self.tick(c,1.51,publish=False)
                self.assertEqual(c.window.opacity,0.)
        self.exercise(scenario)

    def test_shared_panel_actions_start_each_task_once_and_controller_owns_shutdown(self):
        def scenario(c):
            self.tick(c)
            self.assertIs(c.session.controller, c.controller)
            self.assertTrue(c.session.sink_calls)
            for name, button in c.panel.buttons.items():
                self.assertIs(button.defaultAction(), c.actions[name])
                self.assertIn(c.actions[name], c.menu.actions())
            c.panel.buttons['calibrate'].click()
            self.assertEqual(c.session.start_calls, [('calibrate', c.controls.screen)])
            self.assertTrue(c.session.active)
            self.tick(c)
            self.assertFalse(c.actions['calibrate'].isEnabled())
            c.session.cancel('synthetic_task_finished')
            self.tick(c)
            c.actions['check'].trigger()  # The same QAction exposed in the tray menu.
            self.assertEqual(c.session.start_calls,
                             [('calibrate', c.controls.screen), ('evaluate', c.controls.screen)])
        self.exercise(scenario)

    def test_ready_guards_wait_for_camera_profile_io_and_no_existing_task(self):
        def scenario(c):
            c.session.accept_latest = False
            self.tick(c)
            for name in ('calibrate', 'check', 'load_web', 'record'):
                self.assertFalse(c.actions[name].isEnabled(), name)
            c.panel.buttons['calibrate'].click()
            self.assertEqual(c.session.start_calls, [])
            c.session.accept_latest = True
            self.tick(c)
            self.assertTrue(c.actions['calibrate'].isEnabled())
            c.session.profiles.busy = True
            self.tick(c)
            for name in ('calibrate', 'check', 'load_web', 'reset'):
                self.assertFalse(c.actions[name].isEnabled(), name)
            c.session.profiles.busy = False
            c.session.active = True
            self.tick(c)
            for name in ('calibrate', 'check', 'load_web', 'toggle', 'web'):
                self.assertFalse(c.actions[name].isEnabled(), name)
            c.session.active = False
            c.controller.status = 'starting'
            self.tick(c, publish=False)
            for name in ('calibrate', 'check', 'load_web', 'record', 'pause'):
                self.assertFalse(c.actions[name].isEnabled(), name)
        self.exercise(scenario)

    def test_failed_manual_record_returns_shared_switch_to_off_and_shows_error(self):
        def scenario(c):
            self.tick(c)
            c.session.recording.reject_start = True
            c.panel.buttons['record'].click()
            self.assertEqual(c.session.recording.calls, [True])
            self.assertIn('synthetic disk failure', c.panel.notice.text())
            self.assertTrue(c.panel.isVisible())
            self.tick(c)
            self.assertFalse(c.actions['record'].isChecked())
            self.assertFalse(c.panel.buttons['record'].isChecked())
            self.assertIn('失败（已停止）',c.panel.recording.text())
            c.session.record_calibration.assert_not_called()
            self.assertEqual(c.session.recording.calls, [True])
        self.exercise(scenario)

    def test_matching_profile_mode_age_and_radius_display_propagate_without_recalculation(self):
        def scenario(c):
            c.session.profiles.profile = {'pipeline': 'python'}
            c.session.profiles.mode = '九点 · 12 分钟前'
            c.session.radius = 2.5
            c.session.radius_text = '半径来源：最近匹配 B 核对平均误差 2.500 cm'
            self.tick(c)
            self.assertEqual(c.panel.mode.text(), '九点 · 12 分钟前')
            self.assertEqual(c.controls.mode.text(), c.panel.mode.text())
            self.assertEqual(c.panel.radius.text(), c.session.radius_text)
            self.assertIn(c.session.radius_text, c.tray.toolTip())
            self.assertIn('九点 · Python 摄像头', c.tray.toolTip())
            self.assertEqual(c.window.radii, (2.5*800/30, 2.5*600/20))
            c.session.profiles.profile = {'pipeline': 'web'}
            c.session.profiles.mode = '九点 · 365 天前 · web 档案（手动）'
            c.session.radius = None
            c.session.radius_text = '半径来源：配置示意值（web 手动档案）'
            self.tick(c)
            self.assertEqual(c.panel.mode.text(), c.session.profiles.mode)
            self.assertIn('九点（web 手动）', c.tray.toolTip())
            self.assertEqual(c.panel.radius.text(), c.session.radius_text)
        self.exercise(scenario)

    def test_pause_cancels_task_stops_recording_and_clears_old_numeric_state(self):
        def scenario(c):
            self.tick(c)
            c.panel.buttons['record'].click()
            c.session.record_calibration.assert_called_once_with()
            self.assertTrue(c.session.recording.is_manual)
            c.session.active = True
            c.session.recording.task_id = 'synthetic-task'
            c.panel.buttons['pause'].click()
            self.assertIn('camera_paused', c.session.cancel_calls)
            self.assertFalse(c.session.active)
            self.assertFalse(c.session.recording.is_manual)
            self.assertEqual(c.session.recording.calls, [True, False])
            c.session.buffer.clear.assert_called_once_with()
            self.assertIsNone(c.session.latest)
            self.assertEqual(c.controller.requests, ['running', 'paused'])
            self.tick(c, publish=False)
            self.assertFalse(c.actions['record'].isChecked())
            self.assertEqual(c.actions['pause'].text(), '恢复摄像头')
        self.exercise(scenario)

    def test_open_web_pauses_before_launch_and_never_opens_real_browser(self):
        def scenario(c):
            self.tick(c)
            order = []
            original_request = c.controller.request
            def request(action):
                order.append(action)
                return original_request(action)
            c.controller.request = request
            c.open_web.side_effect = lambda url: order.append(('web', url.toString())) or True
            c.panel.buttons['web'].click()
            self.assertEqual(order, ['paused', ('web', c.settings.overlay.web_url)])
            self.assertIn('camera_paused', c.session.cancel_calls)
            self.assertFalse(c.session.recording.is_manual)
            c.open_web.assert_called_once()
            self.assertIn('已暂停悬浮层摄像头', c.panel.notice.text())
        self.exercise(scenario)

    def test_quit_button_closes_resources_with_hidden_tray_and_no_native_geometry(self):
        def scenario(c):
            self.tick(c)
            self.assertFalse(c.tray.isVisible())
            c.panel.buttons['quit'].click()
            self.assertTrue(c.controller.closed.is_set())
            self.assertEqual(c.controller.requests[-1], 'closed')
            self.assertIn('overlay_closed', c.session.cancel_calls)
            self.assertTrue(c.session.recording.closed)
            self.assertTrue(c.session.profiles.closed)
        self.exercise(scenario)

    def test_manual_web_load_requires_explicit_risk_confirmation(self):
        def scenario(c):
            self.tick(c)
            c.select_web.return_value = ('/synthetic/web.json', 'JSON (*.json)')
            c.message_box.warning.return_value = QtMessageBox.StandardButton.Cancel
            c.panel.buttons['load_web'].click()
            self.assertEqual(c.session.profiles.web_calls, [])
            self.assertIn('裁切、内参和时间戳', c.message_box.warning.call_args.args[2])
            self.assertEqual(c.message_box.warning.call_args.args[-1], QtMessageBox.StandardButton.Cancel)
            c.message_box.warning.return_value = QtMessageBox.StandardButton.Yes
            c.panel.buttons['load_web'].click()
            self.assertEqual(c.session.profiles.web_calls, [('/synthetic/web.json', True)])
        self.exercise(scenario)

    def test_requested_matrix_revision_rejects_inflight_old_matrix_even_with_new_capture(self):
        def scenario(c):
            self.tick(c,1.)
            c.session.display_revision+=1;c.session.matrix_since_ns=1_010_000_000
            c.controller.matrix_revision=1
            self.tick(c,1.02)
            self.assertIsNone(c.window.position)
            c.controller.result.data['display']['matrix_revision']=1
            self.tick(c,1.03,publish=False)
            self.assertGreater(c.window.opacity,0.)
        self.exercise(scenario)

    def test_new_profile_revision_does_not_draw_a_prediction_captured_before_matrix_change(self):
        def scenario(c):
            self.tick(c, 1.)
            self.assertGreater(c.window.opacity, 0.)
            c.clock.now = 1.01
            c.session.display_revision += 1
            c.session.matrix_since_ns = c.clock.ns()
            self.tick(c, publish=False)
            self.assertIsNone(c.window.position)
            self.assertEqual(c.window.opacity, 0.)
            self.tick(c, 1.02)
            self.assertIsNotNone(c.window.position)
            self.assertGreater(c.window.opacity, 0.)
        self.exercise(scenario)


if __name__ == '__main__':
    unittest.main()
