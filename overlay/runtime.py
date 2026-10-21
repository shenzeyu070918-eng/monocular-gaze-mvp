"""Qt orchestration for the uncalibrated M3a controller."""
import signal
import sys
import time
from PySide6.QtCore import QObject,QEvent
from PySide6.QtWidgets import QMessageBox,QSystemTrayIcon
from .controller import OverlayController
from .state import RingState


def run_camera_overlay(app,window,tray,pause_action,quit_action,timer,settings,permission_check,*,controls=None):
    notice=permission_check()
    if notice:
        print(notice,file=sys.stderr)
        tray.showMessage('摄像头权限',notice,QSystemTrayIcon.MessageIcon.Information)
    if settings.geometry.width_cm is None or settings.geometry.height_cm is None:
        raise ValueError('请先在 local.yaml 选择设备文件，或配置屏幕宽高；M3a 不使用网页或个人档案。')
    ring=RingState(settings.overlay)
    closing=False;shutdown_requested=False;screen_changed=False;last_error=None;error_box=None
    paused_requested=False;session=None;display_revision=0;restore_visible=True;hotkeys=None

    def notice(text,error=False):
        print('悬浮层：'+text,file=sys.stderr)
        window.set_status(text,force=True)
        if controls is not None:
            controls.panel.notice.setText(text)
            controls.panel.notice.setStyleSheet('color: #b42318' if error else '')
            if error:controls.panel.present()

    def restore():
        ring.clear()
        if not shutdown_requested:
            window.setVisible(restore_visible)
            if controls is not None:controls.panel.present()

    if controls is not None:
        from .session import OverlaySession
        session=OverlaySession(settings,window.rect_info,notice=notice,restore=restore,
                               results=controls.panel.results.setPlainText)
        controller=OverlayController(settings,result_sink=session.sink,shutdown=session.close)
        session.controller=controller
    else:controller=OverlayController(settings)

    def begin_close():
        nonlocal closing,shutdown_requested
        closing=True;shutdown_requested=True;ring.clear()
        if session is not None:session.cancel('overlay_closed')
        pause_action.setEnabled(False)
        quit_action.setText('正在退出…')
        window.set_status('未校准 · 正在退出',force=True)
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
        if session is not None:
            session.cancel('camera_paused');session.recording.manual(False)
            session.buffer.clear();session.latest=None
        view=controller.snapshot()
        paused_requested=not paused_requested if view.status not in {'paused','error'} else False
        ring.clear()
        window.set_status('未校准 · '+('正在暂停' if paused_requested else '正在恢复摄像头/模型'),force=True)
        controller.request('paused' if paused_requested else 'running')
    pause_action.triggered.connect(pause_or_resume)

    def changed(*_):
        nonlocal screen_changed
        if screen_changed:return
        screen_changed=True;ring.clear();controller.request('paused')
        if session is not None:
            session.cancel('screen_changed');session.profiles.disable();session.recording.manual(False)
        window.set_status('未校准 · 屏幕变化，请重新启动',force=True)
        pause_action.setEnabled(False)
        print('屏幕配置变化：已暂停，请退出并在单块内置屏下重新启动。',file=sys.stderr)
        tray.showMessage('屏幕配置变化','已暂停摄像头，请退出并重新启动。',QSystemTrayIcon.MessageIcon.Warning)
    screen_signals=[app.screenAdded,app.screenRemoved]
    for screen in app.screens():
        screen_signals.extend((screen.geometryChanged,screen.logicalDotsPerInchChanged))
    for event_signal in screen_signals:event_signal.connect(changed)

    if controls is not None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QFileDialog
        actions,panel=controls.actions,controls.panel
        redo_dialog=None
        def offer_redo(quality,decide):
            nonlocal redo_dialog
            points=', '.join(p['target_id'] for p in quality['points'] if p['redo'])
            redo_dialog=QMessageBox(QMessageBox.Icon.Question,'九点校准质量',
                f'以下校准点自身拟合残差超过 {quality["threshold"]:g} cm：{points}。\n是否补看这些点？最多一轮，与原样本合并后仍使用仿射拟合。\n'
                '取消会保留首次完整结果；这不是独立 B 组精度。',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,panel)
            redo_dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            def finished(answer):
                decide(answer==QMessageBox.StandardButton.Yes)
                if session.task is not None:
                    window.hide();panel.hide()
            redo_dialog.finished.connect(finished);redo_dialog.open()
        session.request_redo=offer_redo
        session.dismiss_redo=lambda:redo_dialog.reject() if redo_dialog is not None and redo_dialog.isVisible() else None
        def start_task(kind):
            nonlocal restore_visible
            restore_visible=window.isVisible()
            try:
                window.hide();panel.hide()
                session.start(kind,controls.screen)
            except Exception as exc:
                restore();notice('无法开始任务：'+str(exc),True)
        def manual_record(enabled):
            if session.recording.manual(bool(enabled)):
                if enabled:
                    session.record_calibration();notice('手动录制已请求开启，面板会显示准备或失败状态。',False)
            else:notice('录制未开启：'+str(session.recording.status()['error']),True)
        def manual_web():
            path,_=QFileDialog.getOpenFileName(panel,'选择网页九点档案',str(settings.recording.output_dir),'JSON (*.json)')
            if not path:return
            answer=QMessageBox.warning(panel,'网页档案链路风险',
                '网页与 OpenCV 管线可能有不同裁切、内参和时间戳。网页九点矩阵不能保证适用于悬浮层。'
                '加载后会标明 web 手动来源，不使用网页核对半径。仍要临时加载吗？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Yes:session.profiles.load_web(path,confirmed=True)
        def open_web():
            if controller.snapshot().status=='running':pause_or_resume()
            if not QDesktopServices.openUrl(QUrl(settings.overlay.web_url)):
                notice('无法打开本地网页，请检查网页服务是否已启动：'+settings.overlay.web_url,True)
            else:notice('已暂停悬浮层摄像头并打开本地网页；网页服务需已启动。',False)
        actions.connect('calibrate',lambda:start_task('calibrate'))
        actions.connect('check',lambda:start_task('evaluate'))
        actions.connect('load_web',manual_web)
        actions.connect('reset',session.profiles.disable)
        actions.connect('record',manual_record)
        actions.connect('web',open_web)
        from .hotkeys import Hotkeys
        from PySide6.QtCore import QTimer
        # Carbon and WM_HOTKEY execute on the Qt thread. Queue actions outside
        # native callbacks so modal UI and shutdown cannot reenter Carbon.
        def dispatch_hotkey(name):
            QTimer.singleShot(0,lambda:actions[name].trigger() if not shutdown_requested else None)
        hotkeys=Hotkeys(settings.overlay.hotkeys,dispatch_hotkey)
        panel.hotkeys.setText(hotkeys.text())
        print('系统热键（不申请输入监控）：\n'+hotkeys.text(),file=sys.stderr)
        failures=[v for v in hotkeys.status.values() if v.startswith('注册失败')]
        if failures:notice('系统热键部分注册失败：'+'；'.join(failures),True)

    def tick():
        nonlocal last_error,error_box,closing,display_revision
        view=controller.snapshot();now=time.perf_counter_ns()
        if controller.closed.is_set():
            if controls is not None:controls.panel.hide()
            timer.stop();window.hide();tray.hide();app.quit();return
        if view.error and view.error!=last_error:
            last_error=view.error
            window.set_status('未校准 · 采集失败，请查看提示',force=True)
            print('悬浮层：'+view.error,file=sys.stderr)
            if error_box is not None:error_box.close()
            error_box=QMessageBox(QMessageBox.Icon.Warning,'悬浮层已停止',view.error)
            error_box.open()
            if session is not None:session.cancel('prediction_error')
        if closing and view.status=='error':
            closing=False
            quit_action.setText('重试退出')
        active=view.status=='running' and not paused_requested and not shutdown_requested and not screen_changed
        if session is not None:
            session.tick(view)
            if session.display_revision!=display_revision:
                display_revision=session.display_revision;ring.clear()
        eligible=session is None or (view.result is not None and view.result.captured_ns>=session.matrix_since_ns
                    and view.result.data.get('display',{}).get('matrix_revision',0)==view.matrix_revision)
        if active and view.result is not None and eligible:ring.accept(view.result,now)
        elif not active:ring.clear()
        display=ring.view(now)
        label=display.label
        label_kind='tracking' if display.tracking else 'transient'
        if not active:
            label='未校准 · '+({'starting':'正在启动摄像头/模型','paused':'已暂停','pausing':'正在暂停',
                                  'closing':'正在退出','error':'采集失败，请查看提示'}.get(view.status,'等待画面'))
            label_kind='immediate'
        if screen_changed:label='未校准 · 屏幕变化，请重新启动';label_kind='immediate'
        if active and view.idle:label='未校准 · 无人脸，降低读取频率';label_kind='transient'
        if session is not None:
            label=label.replace('未校准',session.short_mode,1)
            if display.tracking:label=session.short_mode+' · Python 摄像头 · '+session.radius_text
        position=None if display.position is None else window.rect_info.local(display.position)
        radius=settings.overlay.radius_cm if session is None or session.radius is None else session.radius
        radii=window.rect_info.radii(radius,settings.geometry,settings.overlay.fallback_radius_px)
        window.set_display(position,radii,display.opacity,label,label_kind=label_kind)
        tray.setToolTip(label)
        pause_action.setEnabled(not shutdown_requested and not screen_changed and view.status not in {'starting','pausing','closing'})
        pause_action.setText('恢复摄像头' if view.status in {'paused','error'} else '暂停摄像头')
        if controls is not None:
            panel.mode.setText(session.profiles.mode)
            controls.mode.setText(session.profiles.mode)
            panel.radius.setText(session.radius_text)
            ready=active and session.latest is not None and not session.active and not session.profiles.busy
            for name in ('calibrate','check'):actions[name].setEnabled(ready)
            actions['load_web'].setEnabled(ready)
            actions['reset'].setEnabled(not session.active and not session.profiles.busy and session.profiles.profile is not None)
            record=session.recording.status()
            record_text=('失败（已停止）：'+record['error'] if record['error'] else
                         '正在准备' if record.get('preparing') else
                         ('手动录制中' if record['manual'] else '任务自动录制中') if record.get('active') else
                         '正在收尾' if record.get('closing') else '关闭')
            panel.recording.setText('录制：'+record_text+(' · '+record['file'] if record.get('active') and record.get('file') else ''))
            panel.recording.setStyleSheet('color: #b42318' if record['error'] else '')
            actions['record'].setChecked(record['manual'])
            actions['record'].setEnabled(active and session.latest is not None and not shutdown_requested)
            actions['record'].setText('手动录制'+('（任务自动录制中）' if record['task_id'] and not record['manual'] else ''))
            actions['web'].setEnabled(not shutdown_requested and not session.active)
            actions['toggle'].setEnabled(not session.active and not shutdown_requested)
    timer.timeout.connect(tick)
    try:
        controller.request('running')
        return app.exec()
    finally:
        if hotkeys is not None:hotkeys.close()
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
