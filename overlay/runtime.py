"""Qt orchestration for the uncalibrated M3a controller."""
import signal
import sys
import time
from PySide6.QtCore import QObject,QEvent
from PySide6.QtWidgets import QMessageBox,QSystemTrayIcon
from .controller import OverlayController
from .state import RingState


def run_camera_overlay(app,window,tray,pause_action,quit_action,timer,settings,permission_check):
    notice=permission_check()
    if notice:
        print(notice,file=sys.stderr)
        tray.showMessage('摄像头权限',notice,QSystemTrayIcon.MessageIcon.Information)
    if settings.geometry.width_cm is None or settings.geometry.height_cm is None:
        raise ValueError('请先在 local.yaml 选择设备文件，或配置屏幕宽高；M3a 不使用网页或个人档案。')
    controller=OverlayController(settings)
    ring=RingState(settings.overlay)
    closing=False;shutdown_requested=False;screen_changed=False;last_error=None;error_box=None
    paused_requested=False

    def begin_close():
        nonlocal closing,shutdown_requested
        closing=True;shutdown_requested=True;ring.clear()
        pause_action.setEnabled(False)
        quit_action.setText('正在退出…')
        controller.request('closed')

    class QuitGate(QObject):
        def eventFilter(self,watched,event):
            if event.type()==QEvent.Type.Quit and not controller.closed.is_set():
                begin_close();return True
            return super().eventFilter(watched,event)
    gate=QuitGate(app);app.installEventFilter(gate)
    quit_action.triggered.disconnect()
    quit_action.triggered.connect(begin_close)
    signal.signal(signal.SIGINT,lambda *_:begin_close())

    def pause_or_resume():
        nonlocal paused_requested
        if shutdown_requested or screen_changed:return
        view=controller.snapshot()
        paused_requested=not paused_requested if view.status not in {'paused','error'} else False
        ring.clear()
        controller.request('paused' if paused_requested else 'running')
    pause_action.triggered.connect(pause_or_resume)

    def changed(*_):
        nonlocal screen_changed
        if screen_changed:return
        screen_changed=True;ring.clear();controller.request('paused')
        pause_action.setEnabled(False)
        print('屏幕配置变化：已暂停，请退出并在单块内置屏下重新启动。',file=sys.stderr)
        tray.showMessage('屏幕配置变化','已暂停摄像头，请退出并重新启动。',QSystemTrayIcon.MessageIcon.Warning)
    screen_signals=[app.screenAdded,app.screenRemoved]
    for screen in app.screens():
        screen_signals.extend((screen.geometryChanged,screen.logicalDotsPerInchChanged))
    for event_signal in screen_signals:event_signal.connect(changed)

    def tick():
        nonlocal last_error,error_box,closing
        view=controller.snapshot();now=time.perf_counter_ns()
        if controller.closed.is_set():
            timer.stop();window.hide();tray.hide();app.quit();return
        if view.error and view.error!=last_error:
            last_error=view.error
            print('悬浮层：'+view.error,file=sys.stderr)
            if error_box is not None:error_box.close()
            error_box=QMessageBox(QMessageBox.Icon.Warning,'悬浮层已停止',view.error)
            error_box.open()
        if closing and view.status=='error':
            closing=False
            quit_action.setText('重试退出')
        active=view.status=='running' and not paused_requested and not shutdown_requested and not screen_changed
        if active and view.result is not None:ring.accept(view.result,now)
        elif not active:ring.clear()
        display=ring.view(now)
        label=display.label
        if not active:
            label='未校准 · '+({'starting':'正在启动摄像头/模型','paused':'已暂停','pausing':'正在暂停',
                                  'closing':'正在退出','error':'采集失败，请查看提示'}.get(view.status,'等待画面'))
        if screen_changed:label='未校准 · 屏幕变化，请重新启动'
        if active and view.idle:label='未校准 · 无人脸，降低读取频率'
        position=None if display.position is None else window.rect_info.local(display.position)
        radii=window.rect_info.radii(settings.overlay.radius_cm,settings.geometry,settings.overlay.fallback_radius_px)
        window.set_display(position,radii,display.opacity,label)
        tray.setToolTip(label)
        pause_action.setEnabled(not shutdown_requested and not screen_changed and view.status not in {'starting','pausing','closing'})
        pause_action.setText('恢复摄像头' if view.status in {'paused','error'} else '暂停摄像头')
    timer.timeout.connect(tick)
    controller.request('running')
    try:
        return app.exec()
    finally:
        # Normal quits pass QuitGate and arrive here only after worker cleanup.
        # Exceptional Qt exits still request cleanup and keep it off the UI loop.
        if not controller.closed.is_set():
            controller.request('closed')
            controller.thread.join(settings.camera.startup_timeout_s+settings.camera.stop_timeout_s+settings.recording.close_timeout_s)
        app.removeEventFilter(gate)
        timer.timeout.disconnect(tick)
        for event_signal in screen_signals:
            try:event_signal.disconnect(changed)
            except (RuntimeError,TypeError):pass
