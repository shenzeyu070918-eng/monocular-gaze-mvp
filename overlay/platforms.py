"""Small native window/permission adapter. No keyboard or mouse monitoring."""
import json
import subprocess
import sys


def prepare_application():
    if sys.platform=='darwin':
        from AppKit import NSApplication,NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    elif sys.platform!='win32':
        raise RuntimeError('M3a 暂只支持 macOS 和 Windows。')


def builtin_screen(screens):
    if len(screens)!=1:
        raise RuntimeError('M3a 仅支持单块内置屏；请断开外接显示器后重试。')
    if sys.platform=='darwin':
        from AppKit import NSScreen
        from Quartz import CGDisplayIsBuiltin
        native=list(NSScreen.screens())
        if len(native)!=1 or not CGDisplayIsBuiltin(native[0].deviceDescription()['NSScreenNumber']):
            raise RuntimeError('未找到唯一启用的内置屏；M3a 不支持外接显示器。')
    elif sys.platform=='win32':
        command=('Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams | '
                 'Where-Object Active | Select-Object -ExpandProperty VideoOutputTechnology | ConvertTo-Json -Compress')
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
                                  capture_output=True,text=True,timeout=8,check=True,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            outputs=json.loads(result.stdout)
            outputs=outputs if isinstance(outputs,list) else [outputs]
            if len(outputs)!=1 or outputs[0] not in (6,11,13,0x80000000,-2147483648):
                raise ValueError('不是单块内置屏')
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            raise RuntimeError('无法确认内置屏；M3a 不支持外接屏或无法识别的屏幕连接。') from exc
    return screens[0]


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
