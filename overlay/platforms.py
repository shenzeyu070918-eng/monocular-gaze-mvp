"""Small native window/permission adapter. No keyboard or mouse monitoring."""
import sys


def prepare_application():
    if sys.platform=='darwin':
        from AppKit import NSApplication,NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    elif sys.platform!='win32':
        raise RuntimeError('M3a 暂只支持 macOS 和 Windows。')


def builtin_screen(screens):
    from .screens import select_builtin
    return select_builtin(screens).qscreen


def configure_window(widget):
    if sys.platform=='darwin':
        import objc
        from AppKit import (NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary,NSStatusWindowLevel)
        # On Cocoa, QWidget.winId() identifies the NSView, not the NSWindow.
        view=objc.objc_object(c_void_p=int(widget.winId()))
        native=view.window()
        if native is None:raise RuntimeError('无法取得悬浮层的 macOS 窗口。')
        import AppKit
        # Qt Tool windows inherit MoveToActiveSpace. Cocoa rejects combining it
        # with CanJoinAllSpaces; Primary/None also conflict with Auxiliary.
        conflicts=sum(getattr(AppKit,name,0) for name in
                      ('NSWindowCollectionBehaviorMoveToActiveSpace','NSWindowCollectionBehaviorFullScreenPrimary',
                       'NSWindowCollectionBehaviorFullScreenNone'))
        behavior=(native.collectionBehavior()&~conflicts)|NSWindowCollectionBehaviorCanJoinAllSpaces|NSWindowCollectionBehaviorFullScreenAuxiliary
        # Newer macOS also exposes the all-applications behavior (Stage Manager).
        behavior|=getattr(AppKit,'NSWindowCollectionBehaviorCanJoinAllApplications',0)
        native.setCollectionBehavior_(behavior)
        native.setLevel_(NSStatusWindowLevel)
        native.setIgnoresMouseEvents_(True)
        if native.respondsToSelector_('setHidesOnDeactivate:'):native.setHidesOnDeactivate_(False)
    # On Windows Qt's Tool / DoesNotAcceptFocus / TransparentForInput flags
    # supply the native tool-window, no-activation and click-through behavior.


def present_panel(widget):
    """Bring ordinary controls forward, including an accessory app on macOS."""
    from PySide6.QtGui import QGuiApplication
    native = None
    # Offscreen Qt has no Cocoa NSView. Keep headless tests and other platforms
    # on Qt's normal window path instead of importing or activating AppKit.
    if sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa':
        try:
            import objc
            import AppKit
            application = AppKit.NSApplication.sharedApplication()
            view = objc.objc_object(c_void_p=int(widget.winId()))
            native = view.window()
            if native is None:
                raise RuntimeError('无法取得控制面板的 macOS 窗口。')
            # The panel follows the current Space when summoned, rather than
            # activating the Space where an already-visible panel was left.
            conflicts = (AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                         AppKit.NSWindowCollectionBehaviorFullScreenPrimary |
                         getattr(AppKit, 'NSWindowCollectionBehaviorFullScreenNone', 0))
            behavior = ((native.collectionBehavior() & ~conflicts) |
                        AppKit.NSWindowCollectionBehaviorMoveToActiveSpace |
                        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
            native.setCollectionBehavior_(behavior)
            application.activateIgnoringOtherApps_(True)
        except Exception as exc:
            native = None
            print('控制面板原生前置失败：' + str(exc), file=sys.stderr, flush=True)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    if native is not None:
        native.makeKeyAndOrderFront_(None)
        native.orderFrontRegardless()


def camera_permission():
    """Preflight only; OpenCV owns capture. Never request input monitoring."""
    if sys.platform=='darwin':
        from AVFoundation import AVCaptureDevice,AVMediaTypeVideo
        status=int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo))
        if status in (1,2):
            raise RuntimeError('摄像头权限被拒绝或受限制。请在「系统设置 → 隐私与安全性 → 摄像头」'
                               '为启动本程序的终端授权，退出并重新打开终端后再试。')
        if status==0:
            return ('尚未授予摄像头权限；请允许系统随后出现的请求。若预热超时，授权后从托盘恢复。'
                    '也可在「系统设置 → 隐私与安全性 → 摄像头」检查终端权限。')
    return None
