"""Qt orchestration for the uncalibrated M3a controller."""
import signal
import sys
import time
from PySide6.QtCore import QObject,QEvent,QTimer
from PySide6.QtWidgets import QMessageBox,QSystemTrayIcon
from .controller import OverlayController
from .state import RingState
from .screens import BuiltinScreen,ScreenMonitor,SCREEN_CHANGED


def run_camera_overlay(app,window,tray,pause_action,quit_action,timer,settings,permission_check,*,controls=None,profile_seconds=None,demo_debug=False):
    notice=permission_check()
    if notice:
        print(notice,file=sys.stderr)
        tray.showMessage('摄像头权限',notice,QSystemTrayIcon.MessageIcon.Information)
    if settings.geometry.width_cm is None or settings.geometry.height_cm is None:
        raise ValueError('请先在 local.yaml 选择设备文件，或配置屏幕宽高；M3a 不使用网页或个人档案。')
    ring=RingState(settings.overlay)
    closing=False;shutdown_requested=False;screen_changed=False;last_error=None;error_box=None
    paused_requested=False;session=None;display_revision=0;restore_visible=True;hotkeys=None
    painted=None;radius_key=object();radii=None;ui_values={}
    clicks=None;own_windows=None;heatmap=None;heat_timer=None;heat_error=None;heatmap_rendered=False
    pending_screen=None;screen_error=None;screen_mismatch_seen=False;detached_visible=None
    demo=None;demo_dialog=None

    def update(widget,method,value):
        key=(widget,method)
        if key not in ui_values or ui_values[key]!=value:
            getattr(widget,method)(value);ui_values[key]=value

    def notice(text,error=False):
        print('悬浮层：'+text,file=sys.stderr)
        window.set_status(text,force=True)
        if controls is not None:
            controls.panel.notice.setText(text)
            controls.panel.notice.setStyleSheet('color: #b42318' if error else '')
            if error:controls.panel.present()

    def restore():
        ring.clear()
        if not shutdown_requested and not screen_changed:
            window.setVisible(restore_visible)
            if controls is not None:controls.panel.present()

    if controls is not None:
        from .session import OverlaySession
        session=OverlaySession(settings,window.rect_info,notice=notice,restore=restore,
                               results=controls.panel.results.setPlainText)
        controller=OverlayController(settings,result_sink=session.sink,shutdown=session.close)
        session.controller=controller
        heatmap=getattr(session,'heatmap',None)
    else:controller=OverlayController(settings)

    def begin_close():
        nonlocal closing,shutdown_requested
        closing=True;shutdown_requested=True;ring.clear()
        if demo_dialog is not None:demo_dialog.reject()
        if demo is not None:demo.cancel('overlay_closed',restore=False)
        if heatmap is not None:heatmap.set_enabled(False)
        if session is not None and hasattr(session,'set_running'):session.set_running(False)
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
        if demo_dialog is not None:demo_dialog.reject()
        if demo is not None:demo.cancel('camera_paused')
        if session is not None:
            if hasattr(session,'set_running'):session.set_running(False)
            session.cancel('camera_paused');session.recording.manual(False)
            session.buffer.clear();session.latest=None
        if heatmap is not None:heatmap.interrupt()
        view=controller.snapshot()
        paused_requested=not paused_requested if view.status not in {'paused','error'} else False
        ring.clear()
        window.set_status('未校准 · '+('正在暂停' if paused_requested else '正在恢复摄像头/模型'),force=True)
        controller.request('paused' if paused_requested else 'running')
    pause_action.triggered.connect(pause_or_resume)

    def changed(kind,selection,error):
        nonlocal screen_changed,pending_screen,screen_error,paused_requested,restore_visible,detached_visible
        if shutdown_requested:return
        if kind=='unchanged' and not screen_changed:
            # Qt can replace a QScreen object while its native identity and
            # geometry stay fixed. Reattach without resetting any samples.
            try:
                if controls is not None:controls.screen=selection.qscreen
                if session is not None and hasattr(session,'refresh_screen_mapping'):session.refresh_screen_mapping(selection.rect)
                window.attach_screen(selection)
                if detached_visible is not None:window.setVisible(detached_visible);detached_visible=None
                return
            except Exception as exc:
                selection=None;error='屏幕坐标适配失败：'+str(exc)
        restore_visible=restore_visible if screen_changed else (window.isVisible() if detached_visible is None else detached_visible)
        if demo is not None and demo.active:
            restore_visible=demo._saved[0];demo.cancel('screen_changed',restore=False)
        detached_visible=None
        screen_changed=True;screen_error=error;pending_screen=selection
        if demo_dialog is not None:demo_dialog.reject()
        paused_requested=True;ring.clear();window.hide()
        if session is not None and hasattr(session,'set_running'):session.set_running(False)
        controller.request('paused')
        if heatmap is not None:heatmap.set_enabled(False)
        if session is not None:
            session.cancel('screen_changed');session.profiles.disable();session.recording.manual(False)
        text=error or SCREEN_CHANGED
        window.set_status(text,force=True)
        update(pause_action,'setEnabled',False)
        notice(text+'；已暂停摄像头。'+('请恢复内置屏后再继续。' if selection is None else '暂停完成后可恢复并重新校准，无需重启。'),True)
        tray.showMessage('屏幕配置变化',text,QSystemTrayIcon.MessageIcon.Warning)
    initial=getattr(window,'screen_selection',BuiltinScreen(getattr(controls,'screen',None),window.rect_info,'startup'))
    def detach():
        nonlocal detached_visible
        if demo is not None and demo.active:
            detached_visible=demo._saved[0];demo.cancel('screen_changed',restore=False)
        if detached_visible is None:detached_visible=window.isVisible()
        window.hide()  # Qt must not migrate a visible gaze overlay onto an external primary.
    screen_monitor=ScreenMonitor(app,initial,changed,delay_ms=settings.overlay.screen_debounce_ms,
                                 retry_count=settings.overlay.screen_retry_count,on_detach=detach)

    if controls is not None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QFileDialog
        actions,panel=controls.actions,controls.panel
        from .demo_session import DemoSession
        demo=DemoSession(session,window,panel,notice=notice,debug=demo_debug)
        session.calibration_completed=window.show_calibration_notice
        actions['presentation'].setChecked(settings.overlay.presentation)
        actions['presentation'].setEnabled(True)
        actions.connect('presentation',window.set_presentation)
        def start_demo():
            nonlocal demo_dialog
            if demo.active:return
            if demo_dialog is not None:
                demo_dialog.raise_();demo_dialog.activateWindow();return
            chooser=QMessageBox(panel)
            chooser.setWindowTitle('眼控 Demo');chooser.setText('请选择体验内容。进入后先练习一次，Esc 随时退出。')
            board=chooser.addButton('沟通板',QMessageBox.ButtonRole.AcceptRole)
            game=chooser.addButton('眼控打地鼠',QMessageBox.ButtonRole.AcceptRole)
            chooser.addButton(QMessageBox.StandardButton.Cancel)
            demo_dialog=chooser
            try:chooser.exec()
            finally:demo_dialog=None
            if chooser.clickedButton() not in (board,game):return
            try:
                demo.start('board' if chooser.clickedButton() is board else 'game',controls.screen)
                if own_windows is not None:own_windows.refresh()
            except Exception as exc:notice('无法进入 Demo：'+str(exc),True)
        actions.connect('demo',start_demo)
        actions.connect('demo_practice',demo.repractice)
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
            if demo.active:raise ValueError('请先按 Esc 结束 Demo')
            restore_visible=window.isVisible()
            try:
                window.hide();panel.hide()
                return session.start(kind,controls.screen)
            except Exception as exc:
                restore();notice('无法开始任务：'+str(exc),True)
                raise
        if hasattr(session,'bind_clicks'):
            from .own_windows import OwnWindows
            own_windows=OwnWindows(app,ignore=(window,))
            clicks=session.bind_clicks(start_task=start_task,exclude=own_windows.reason,own_handles=lambda:own_windows.handles)

        def capture_clicks(enabled):
            if clicks is None:return
            try:clicks.set_capture(bool(enabled))
            except Exception as exc:
                if actions['clicks'].isChecked():actions['clicks'].setChecked(False)
                notice('点击采集未开启：'+str(exc),True)

        def click_workflow():
            if clicks is None:return
            try:
                if not clicks.active:
                    reuse=False
                    if clicks.recent_baseline():
                        reuse=QMessageBox.question(panel,'点击会话 B 基线',
                            '当前 Python 九点档案不到 5 分钟。是否复用？选择否将先重新做九点。',
                            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
                    clicks.begin(reuse_recent=reuse)
                else:
                    if clicks.state=='collecting':clicks.end_collection()
                    if clicks.state=='awaiting_check':
                        answer=QMessageBox.question(panel,'末尾公共精度核对',
                            '点击采集已停止。现在做一次精度核对，作为所有离线方法的公共测试集？选择稍后可保留会话并重试。',
                            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Yes)
                        if answer==QMessageBox.StandardButton.Yes:clicks.begin_check()
            except Exception as exc:notice('点击会话：'+str(exc),True)

        def normal_task(kind):
            try:start_task(kind)
            except Exception:pass  # start_task already restores UI and reports.
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
        actions.connect('calibrate',lambda:normal_task('calibrate'))
        actions.connect('check',lambda:normal_task('evaluate'))
        actions.connect('clicks',capture_clicks)
        actions.connect('click_session',click_workflow)
        actions.connect('abort_click_session',lambda:session.cancel('user_aborted'))
        actions.connect('load_web',manual_web)
        actions.connect('reset',session.profiles.disable)
        actions.connect('record',manual_record)
        actions.connect('web',open_web)
        def auto_calibrate(checked):
            try:session.set_autocal_enabled(bool(checked))
            except Exception as exc:
                actions['autocal'].setChecked(False)
                notice('点击自动粗校准未开启：'+str(exc),True)
        if hasattr(session,'set_autocal_enabled'):actions.connect('autocal',auto_calibrate)
        else:actions['autocal'].setEnabled(False)
        if heatmap is not None:
            def toggle_heatmap(checked):
                if not session.set_heatmap_enabled(bool(checked)):
                    actions['heatmap_toggle'].setChecked(False)
                    notice(heatmap.state()['error'] or '热力图未开启',True)
                heatmap_update()
            actions.connect('heatmap_toggle',toggle_heatmap)
            def view_heatmap(checked):
                if not session.set_heatmap_viewing(bool(checked)):
                    actions['heatmap_view'].setChecked(False)
                    notice(heatmap.state()['error'] or '热力图暂不能查看',True)
                heatmap_update()  # Hide immediately; first fresh image arrives at view rate.
            actions.connect('heatmap_view',view_heatmap)
            actions.connect('heatmap_clear',lambda:heatmap.clear())
            actions.connect('heatmap_export',lambda:heatmap.export())
        from .hotkeys import Hotkeys
        # Carbon and WM_HOTKEY execute on the Qt thread. Queue actions outside
        # native callbacks so modal UI and shutdown cannot reenter Carbon.
        def dispatch_hotkey(name):
            QTimer.singleShot(0,lambda:actions[name].trigger() if not shutdown_requested else None)
        hotkeys=Hotkeys(settings.overlay.hotkeys,dispatch_hotkey)
        panel.hotkeys.setText(hotkeys.text())
        print('系统热键（不申请输入监控）：\n'+hotkeys.text(),file=sys.stderr)
        failures=[v for v in hotkeys.status.values() if v.startswith('注册失败')]
        if failures:notice('系统热键部分注册失败：'+'；'.join(failures),True)

    def heatmap_update():
        nonlocal heat_error,heatmap_rendered
        if heatmap is None:return
        state=heatmap.state()
        viewing=state['enabled'] and state['viewing'] and not shutdown_requested and not screen_changed
        if viewing and state['latest'] is not None:
            window.set_heatmap(state['latest'],enabled=True,uncalibrated=state['uncalibrated'],coarse_calibrated=state.get('coarse_calibrated',False));heatmap_rendered=True
        elif heatmap_rendered:
            window.set_heatmap(None,enabled=False);heatmap_rendered=False
        if controls is not None:
            update(panel.heatmap,'setText',state['label'])
            update(panel.heatmap_sigma,'setText',state['kernel_text'])
            update(panel.heatmap,'setStyleSheet','color: #b42318' if state['error'] else '')
            if actions['heatmap_toggle'].isChecked()!=state['enabled']:actions['heatmap_toggle'].setChecked(state['enabled'])
            if actions['heatmap_view'].isChecked()!=state['viewing']:actions['heatmap_view'].setChecked(state['viewing'])
            update(actions['heatmap_view'],'setText','隐藏热力图' if state['viewing'] else '查看热力图')
            update(actions['heatmap_view'],'setEnabled',state['enabled'] and not shutdown_requested and not screen_changed)
            for name in ('heatmap_toggle','heatmap_clear'):update(actions[name],'setEnabled',not shutdown_requested and not screen_changed)
            update(actions['heatmap_export'],'setEnabled',state['has_session'] and not state['exporting'] and not shutdown_requested)
        if state['error'] and state['error']!=heat_error:
            heat_error=state['error'];notice(state['error'],True)

    def tick():
        nonlocal last_error,error_box,closing,display_revision,painted,radius_key,radii,pending_screen,screen_changed,screen_mismatch_seen
        view=controller.snapshot();now=time.perf_counter_ns()
        if controller.closed.is_set():
            if controls is not None:controls.panel.hide()
            timer.stop();window.hide();tray.hide();app.quit();return
        if screen_changed and pending_screen is not None and view.status=='paused':
            selected=pending_screen;pending_screen=None
            window.change_screen(selected)
            if controls is not None:controls.screen=selected.qscreen
            if session is not None:session.change_screen(selected.rect)
            screen_changed=False;ring.clear();painted=None;radius_key=object()
            window.setVisible(restore_visible)
            notice(SCREEN_CHANGED+'；旧热图已清空。请恢复摄像头后重新校准。',True)
        if view.error and view.error!=last_error:
            if demo is not None:demo.cancel('prediction_error')
            last_error=view.error
            window.set_status('未校准 · 采集失败，请查看提示',force=True)
            print('悬浮层：'+view.error,file=sys.stderr)
            if error_box is not None:error_box.close()
            error_box=QMessageBox(QMessageBox.Icon.Warning,'悬浮层已停止',view.error)
            error_box.open()
            if session is not None:session.cancel('prediction_error')
            if heatmap is not None:heatmap.interrupt()
        if closing and view.status=='error':
            closing=False
            quit_action.setText('重试退出')
        active=view.status=='running' and not paused_requested and not shutdown_requested and not screen_changed
        if session is not None:
            if hasattr(session,'set_running'):session.set_running(active)
            if own_windows is not None:own_windows.refresh()
            session.tick(view)
            mismatch=getattr(session.profiles,'screen_mismatch',False)
            if mismatch and not screen_mismatch_seen:notice(SCREEN_CHANGED,True)
            screen_mismatch_seen=bool(mismatch)
            if session.display_revision!=display_revision:
                display_revision=session.display_revision;ring.clear()
        eligible=session is None or (view.result is not None and view.result.captured_ns>=session.matrix_since_ns
                    and view.result.data.get('display',{}).get('matrix_revision',0)==view.matrix_revision)
        if active and view.result is not None and eligible:ring.accept(view.result,now)
        elif not active:ring.clear()
        display=ring.view(now)
        if demo is not None and demo.active:
            demo.tick(view.result,display,now,active=active,eligible=eligible)
        radius=None if session is None else session.radius
        radius=settings.overlay.radius_cm if radius is None else radius
        radius_text=None if session is None else session.radius_text
        short_mode=None if session is None else session.short_mode
        # Keep the cheap stale/fade state machine ticking even without frames.
        # Position conversion, text construction and Qt setters only run when
        # the corresponding rendered state changes.
        render=(display,active,view.status,view.idle,screen_changed,radius,short_mode,radius_text,display_revision)
        if render!=painted:
            painted=render
            label=display.label
            label_kind='tracking' if display.tracking else 'transient'
            if not active:
                label='未校准 · '+({'starting':'正在启动摄像头/模型','paused':'已暂停','pausing':'正在暂停',
                                      'closing':'正在退出','error':'采集失败，请查看提示'}.get(view.status,'等待画面'))
                label_kind='immediate'
            if screen_changed:label=screen_error or SCREEN_CHANGED;label_kind='immediate'
            if active and view.idle:label='未校准 · 无人脸，降低读取频率';label_kind='transient'
            if session is not None:
                label=label.replace('未校准',short_mode,1)
                if display.tracking:label=short_mode+' · Python 摄像头 · '+radius_text
                if getattr(session.profiles,'screen_mismatch',False):label=SCREEN_CHANGED;label_kind='immediate'
            position=None if display.position is None else window.rect_info.local(display.position)
            if radius_key!=radius:
                radius_key=radius;radii=window.rect_info.radii(radius,settings.geometry,settings.overlay.fallback_radius_px)
            window.set_display(position,radii,display.opacity,label,label_kind=label_kind)
            update(tray,'setToolTip',label)
        update(pause_action,'setEnabled',not shutdown_requested and not screen_changed and view.status not in {'starting','pausing','closing'})
        update(pause_action,'setText','恢复摄像头' if view.status in {'paused','error'} else '暂停摄像头')
        if controls is not None:
            mode=getattr(session,'mode',session.profiles.mode)
            update(panel.mode,'setText',mode)
            if getattr(session.profiles,'screen_mismatch',False):update(panel.notice,'setText',SCREEN_CHANGED)
            update(controls.mode,'setText',mode)
            update(panel.radius,'setText',radius_text)
            if hasattr(session,'autocal'):
                update(panel.autocal,'setText',session.autocal_text)
                if actions['autocal'].isChecked()!=session.autocal.enabled:actions['autocal'].setChecked(session.autocal.enabled)
                update(actions['autocal'],'setEnabled',active and session.latest is not None and not session.active and not (demo is not None and demo.active) and not shutdown_requested)
                update(actions['check'],'setText','精度核对（A/C）' if session._active_group=='C' else '精度核对（A/B）')
            click_active=clicks is not None and clicks.active
            demo_active=demo is not None and demo.active
            ready=active and session.latest is not None and not session.active and not demo_active and not session.profiles.busy and not click_active
            update(actions['demo'],'setEnabled',ready)
            update(actions['demo_practice'],'setEnabled',demo_active and active)
            for name in ('calibrate','check'):update(actions[name],'setEnabled',ready)
            update(actions['load_web'],'setEnabled',ready)
            update(actions['reset'],'setEnabled',not session.active and not demo_active and not click_active and not session.profiles.busy and session.profiles.profile is not None)
            record=session.recording.status()
            record_text=('失败（已停止）：'+record['error'] if record['error'] else
                         '正在准备' if record.get('preparing') else
                         ('手动录制中' if record['manual'] else '任务自动录制中') if record.get('active') else
                         '正在收尾' if record.get('closing') else '关闭')
            update(panel.recording,'setText','录制：'+record_text+(' · '+record['file'] if record.get('active') and record.get('file') else ''))
            update(panel.recording,'setStyleSheet','color: #b42318' if record['error'] else '')
            # This property also changes through user interaction, so compare
            # the actual QAction rather than relying on our setter cache.
            if actions['record'].isChecked()!=record['manual']:actions['record'].setChecked(record['manual'])
            update(actions['record'],'setEnabled',active and session.latest is not None and not shutdown_requested)
            update(actions['record'],'setText','手动录制'+('（任务自动录制中）' if record['task_id'] and not record['manual'] else ''))
            if clicks is not None:
                click_status=clicks.listener.status();enabled=bool(click_status.get('active'))
                update(panel.clicks,'setText',f'点击采集：'+('开启' if enabled else '关闭')+
                       f' · 已记录 {record.get("written_clicks",0)} · 排除 {record.get("excluded_clicks",0)} · 会话 {clicks.state}')
                if actions['clicks'].isChecked()!=enabled:actions['clicks'].setChecked(enabled)
                update(actions['clicks'],'setEnabled',active and session.latest is not None and not shutdown_requested and
                       (not click_active or clicks.state=='collecting'))
                update(actions['click_session'],'setEnabled',active and session.latest is not None and not session.active and not demo_active and
                       not session.profiles.busy and (not click_active or clicks.state in {'collecting','awaiting_check'}))
                update(actions['click_session'],'setText','结束采集并核对' if clicks.state=='collecting' else
                       '重试末尾精度核对' if clicks.state=='awaiting_check' else '开始点击采集会话（先九点）')
                update(actions['abort_click_session'],'setEnabled',click_active)
            else:
                for name in ('clicks','click_session','abort_click_session'):update(actions[name],'setEnabled',False)
            update(actions['web'],'setEnabled',not shutdown_requested and not session.active and not demo_active)
            update(actions['toggle'],'setEnabled',not session.active and not demo_active and not shutdown_requested)
    profiler=None;timer_tick=tick;profiled_label=None;heatmap_callback=heatmap_update;original_paint=window._paint_heatmap
    profile_origin=None
    if profile_seconds is not None:
        from .profiling import CallbackProfiler
        def ready():
            nonlocal profile_origin
            snapshot=controller.snapshot()
            result=snapshot.status=='running' and snapshot.result is not None and (session is None or
                   (session.profiles.conditions is not None and not session.profiles.busy))
            if result:profile_origin=(time.perf_counter_ns(),(snapshot.report or {}).get('predicted_frames'))
            return result
        def context():
            import platform
            from dataclasses import asdict
            snapshot=controller.snapshot();report=snapshot.report or {}
            elapsed=None if profile_origin is None else (time.perf_counter_ns()-profile_origin[0])/1e9
            count=None if profile_origin is None or profile_origin[1] is None or report.get('predicted_frames') is None else report['predicted_frames']-profile_origin[1]
            rate=count/elapsed if count is not None and count>=0 and elapsed is not None and elapsed>0 else None
            return dict(system=platform.platform(),python=platform.python_version(),
                        inference=dict(device=settings.inference.device,num_threads=settings.inference.num_threads,max_fps=settings.inference.max_fps),
                        actual_device=report.get('device'),actual_resolution=report.get('actual_resolution'),
                        measurement_source=report.get('measurement_source','live_camera'),
                        predicted_frames=report.get('predicted_frames'),valid_predictions=report.get('valid_predictions'),
                        camera=asdict(settings.camera),refresh_ms=settings.overlay.refresh_ms,
                        profile_loaded=session is not None and session.profiles.profile is not None,
                        demo_active=demo is not None and demo.active,
                        demo_app=demo.run.app if demo is not None and demo.active else None,
                        presentation=window._presentation,
                        heatmap_enabled=heatmap is not None and heatmap.enabled,heatmap_viewing=heatmap is not None and heatmap.viewing,
                        heatmap_display_mode=settings.heatmap.display_mode,heatmap_update_hz=settings.heatmap.view_update_hz,
                        heatmap_background_update_hz=settings.heatmap.update_hz,heatmap_alpha_threshold=settings.heatmap.alpha_threshold,
                        heatmap_render=None if heatmap is None else {key:heatmap.state().get(key) for key in ('render_log','last_render')},
                        profile_window_inference=dict(predictions=count,seconds=elapsed,fps=rate,
                                      definition='剖析窗口内实际推理完成数/单调时钟秒，含无效结果；不是配置上限或摄像头采集 fps。'))
        profiler=CallbackProfiler(profile_seconds,ready=ready,context=context)
        timer_tick=profiler.wrap(tick,'tick')
        profiled_label=profiler.wrap(window._refresh_label,'label')
        window._label_timer.timeout.disconnect(window._refresh_label)
        window._label_timer.timeout.connect(profiled_label)
        heatmap_callback=profiler.wrap(heatmap_update,'heatmap_update')
        window._paint_heatmap=profiler.wrap(original_paint,'heatmap_paint')
    if heatmap is not None:
        heat_timer=QTimer();heat_timer.setInterval(round(1000/settings.heatmap.view_update_hz))
        heat_timer.timeout.connect(heatmap_callback);heat_timer.start()
        heatmap_update()
    timer.timeout.connect(timer_tick)
    try:
        controller.request('running')
        return app.exec()
    finally:
        if demo is not None:demo.cancel('overlay_closed',restore=False)
        if heat_timer is not None:heat_timer.stop()
        window._paint_heatmap=original_paint
        if profiler is not None:
            profiler.finish()
            window._label_timer.timeout.disconnect(profiled_label)
            window._label_timer.timeout.connect(window._refresh_label)
        if hotkeys is not None:hotkeys.close()
        if clicks is not None:clicks.close()
        if own_windows is not None:own_windows.close()
        # Normal quits pass QuitGate and arrive here only after worker cleanup.
        # Exceptional Qt exits still request cleanup and keep it off the UI loop.
        if not controller.closed.is_set():
            controller.request('closed')
            controller.thread.join(settings.camera.startup_timeout_s+settings.camera.stop_timeout_s+settings.recording.close_timeout_s)
        app.removeEventFilter(gate)
        timer.timeout.disconnect(timer_tick)
        screen_monitor.close()
