"""Qt entry point; native window diagnosis is available without a camera/model."""
import argparse
import signal
import sys


def make_tray_icon(color):
    """Cocoa template image with 1x/2x representations; retain Windows color."""
    from PySide6.QtCore import Qt,QRectF
    from PySide6.QtGui import QColor,QIcon,QPainter,QPen,QPixmap
    icon=QIcon()
    if sys.platform=='darwin':
        for scale in (1,2):
            pixmap=QPixmap(18*scale,18*scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter=QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(Qt.GlobalColor.black,2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(3,3,12,12))
            painter.end()
            icon.addPixmap(pixmap)
        icon.setIsMask(True)
    else:
        pixmap=QPixmap(32,32);pixmap.fill(QColor(0,0,0,0))
        painter=QPainter(pixmap);painter.setPen(QColor(color));painter.drawEllipse(5,5,22,22);painter.end()
        icon.addPixmap(pixmap)
    return icon


def main(argv=None):
    parser=argparse.ArgumentParser(description='悬浮层九点校准、独立核对与控制面板')
    parser.add_argument('--config',default=None)
    parser.add_argument('--diagnostic',action='store_true',help='仅验证穿透/全屏/四角，不打开摄像头或模型')
    parser.add_argument('--duration',type=float,default=None,help='诊断模式自动退出秒数')
    parser.add_argument('--control-panel',action='store_true',help='启动时显示控制面板')
    args=parser.parse_args(argv)
    if args.duration is not None and (not args.diagnostic or not 0<args.duration<float('inf')):
        parser.error('--duration 必须为有限正数且只用于 --diagnostic')
    print('按 Ctrl+C 退出',flush=True)
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication,QMenu,QSystemTrayIcon,QMessageBox
        from gaze_core.config import load_settings,print_configuration
        from .mapping import ScreenRect
        from .window import OverlayWindow
        from .actions import OverlayActions,TrayFallback,panel_at_start,panel_on_tray_failure
        from .panel import ControlPanel
        from . import platforms
    except ImportError as exc:
        print(f'缺少悬浮层依赖：{exc}。请用项目 Python 安装 requirements-overlay.txt。',file=sys.stderr)
        return 1
    app=QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName('视线悬浮层');app.setQuitOnLastWindowClosed(False)
    try:
        settings=load_settings() if args.config is None else load_settings(args.config)
        print_configuration(settings)
        platforms.prepare_application()
        tray_available=QSystemTrayIcon.isSystemTrayAvailable()
        if not tray_available:
            print('系统托盘不可用，将使用控制面板。',flush=True)
        screen=platforms.builtin_screen(app.screens())
        rect=screen.geometry()  # Full logical screen, including menu/taskbar area.
        logical=ScreenRect(rect.x(),rect.y(),rect.width(),rect.height(),screen.devicePixelRatio())
        print(f'内置屏逻辑范围：{logical}；个人档案仅在实际 Python 相机条件匹配后加载。',flush=True)
        window=OverlayWindow(logical,settings.overlay,native_configure=platforms.configure_window,diagnostic=args.diagnostic)
        tray=QSystemTrayIcon(make_tray_icon(settings.overlay.color),app)
        actions=OverlayActions(app);panel=ControlPanel(actions);finished=False
        menu=QMenu();mode=menu.addAction('未校准 · Python 管线');mode.setEnabled(False)
        actions.add_to_menu(menu)
        toggle,pause,quit_action=(actions[name] for name in ('toggle','pause','quit'))
        for name in ('calibrate','check','load_web','reset','record','pause','web'):actions[name].setEnabled(False)
        tray.setContextMenu(menu);tray.setToolTip('视线悬浮层 · 未校准')
        def toggle_visible():
            window.setVisible(not window.isVisible())
            toggle.setText('隐藏悬浮层' if window.isVisible() else '显示悬浮层')
        toggle.triggered.connect(toggle_visible)
        actions.connect('panel',panel.present)
        def diagnostic_quit():
            nonlocal finished
            # A normal panel close deliberately vetoes quitting. Hide it first
            # so QApplication.quit cannot be vetoed by its closeEvent.
            finished=True;panel.hide();window.hide();tray.hide();app.quit()
        quit_action.triggered.connect(diagnostic_quit)
        window.prepare_native()
        if tray_available:tray.show()
        window.show()
        app.processEvents()
        print(f'托盘状态：isSystemTrayAvailable={tray_available}；tray.isVisible()={tray.isVisible()}；tray.geometry()={tray.geometry()}',flush=True)
        fallback=TrayFallback()
        def check_tray(final=False):
            if finished:return
            if final:
                print(f'托盘复查：tray.isVisible()={tray.isVisible()}；tray.geometry()={tray.geometry()}',flush=True)
            if fallback.check(tray_available,tray.isVisible(),tray.geometry().height(),final=final) and panel_on_tray_failure(settings.overlay.panel_on_start):
                print('托盘不可见或几何高度为 0，已打开控制面板；请保留面板，或使用 --control-panel 启动。',flush=True)
                panel.present()
        QTimer.singleShot(0,check_tray)
        QTimer.singleShot(settings.overlay.tray_check_delay_ms,lambda:check_tray(True))
        if panel_at_start(settings.overlay.panel_on_start,sys.platform,forced=args.control_panel):
            print('启动控制面板（panel_on_start='+settings.overlay.panel_on_start+'）；不依据托盘几何判断 macOS 菜单栏可见性。',flush=True)
            panel.present()
        if window.geometry()!=rect:
            raise RuntimeError(f'窗口未覆盖完整逻辑屏：期望 {rect}，实际 {window.geometry()}。')
        print(f'窗口逻辑范围已核对：{window.geometry()}；仅核对几何，不代表穿透/全屏实机验收。',flush=True)
        # Keep Python handling terminal Ctrl+C through the Qt event loop.
        signal.signal(signal.SIGINT,lambda *_:diagnostic_quit())
        timer=QTimer();timer.timeout.connect(lambda:None);timer.start(settings.overlay.refresh_ms)
        if args.diagnostic:
            pause.setText('窗口诊断 · 摄像头未打开')
            if args.duration is not None:QTimer.singleShot(int(args.duration*1000),diagnostic_quit)
            try:
                code=app.exec()
                print(f'窗口诊断结束：Qt 返回 {code}。',flush=True)
                return code
            finally:finished=True;timer.stop();window.hide();tray.hide();panel.hide()
        # Camera integration is kept below the diagnostic path.
        from .runtime import run_camera_overlay
        from types import SimpleNamespace
        controls=SimpleNamespace(actions=actions,panel=panel,screen=screen,mode=mode)
        try:return run_camera_overlay(app,window,tray,pause,quit_action,timer,settings,platforms.camera_permission,controls=controls)
        finally:finished=True;panel.hide()
    except Exception as exc:
        print(f'悬浮层启动失败：{exc}',file=sys.stderr)
        if args.duration is None:QMessageBox.critical(None,'悬浮层启动失败',str(exc))
        return 1


if __name__=='__main__':raise SystemExit(main())
