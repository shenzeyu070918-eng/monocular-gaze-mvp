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
    parser=argparse.ArgumentParser(description='M3a 未校准悬浮层；不加载个人档案')
    parser.add_argument('--config',default=None)
    parser.add_argument('--diagnostic',action='store_true',help='仅验证穿透/全屏/四角，不打开摄像头或模型')
    parser.add_argument('--duration',type=float,default=None,help='诊断模式自动退出秒数')
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
            print('托盘状态：isSystemTrayAvailable=False；tray.isVisible()=未创建；tray.geometry()=未创建',flush=True)
            raise RuntimeError('系统托盘不可用，无法安全提供退出操作。')
        screen=platforms.builtin_screen(app.screens())
        rect=screen.geometry()  # Full logical screen, including menu/taskbar area.
        logical=ScreenRect(rect.x(),rect.y(),rect.width(),rect.height(),screen.devicePixelRatio())
        print(f'内置屏逻辑范围：{logical}；M3a 未校准，不加载任何档案。',flush=True)
        window=OverlayWindow(logical,settings.overlay,native_configure=platforms.configure_window,diagnostic=args.diagnostic)
        tray=QSystemTrayIcon(make_tray_icon(settings.overlay.color),app)
        menu=QMenu();mode=menu.addAction('未校准 · Python 管线');mode.setEnabled(False)
        toggle=menu.addAction('隐藏悬浮层')
        pause=menu.addAction('暂停摄像头');pause.setEnabled(False)
        menu.addSeparator();quit_action=menu.addAction('退出')
        tray.setContextMenu(menu);tray.setToolTip('视线悬浮层 · 未校准')
        def toggle_visible():
            window.setVisible(not window.isVisible())
            toggle.setText('隐藏悬浮层' if window.isVisible() else '显示悬浮层')
        toggle.triggered.connect(toggle_visible)
        quit_action.triggered.connect(app.quit)
        window.prepare_native()
        tray.show();window.show()
        app.processEvents()
        print(f'托盘状态：isSystemTrayAvailable={tray_available}；tray.isVisible()={tray.isVisible()}；tray.geometry()={tray.geometry()}',flush=True)
        if window.geometry()!=rect:
            raise RuntimeError(f'窗口未覆盖完整逻辑屏：期望 {rect}，实际 {window.geometry()}。')
        print(f'窗口逻辑范围已核对：{window.geometry()}；仅核对几何，不代表穿透/全屏实机验收。',flush=True)
        # Keep Python handling terminal Ctrl+C through the Qt event loop.
        signal.signal(signal.SIGINT,lambda *_:app.quit())
        timer=QTimer();timer.timeout.connect(lambda:None);timer.start(settings.overlay.refresh_ms)
        if args.diagnostic:
            pause.setText('窗口诊断 · 摄像头未打开')
            if args.duration is not None:QTimer.singleShot(int(args.duration*1000),app.quit)
            try:
                code=app.exec()
                print(f'窗口诊断结束：Qt 返回 {code}。',flush=True)
                return code
            finally:timer.stop();window.hide();tray.hide()
        # Camera integration is kept below the diagnostic path.
        from .runtime import run_camera_overlay
        return run_camera_overlay(app,window,tray,pause,quit_action,timer,settings,platforms.camera_permission)
    except Exception as exc:
        print(f'悬浮层启动失败：{exc}',file=sys.stderr)
        if args.duration is None:QMessageBox.critical(None,'悬浮层启动失败',str(exc))
        return 1


if __name__=='__main__':raise SystemExit(main())
