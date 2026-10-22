"""Quartz physical identity -> mirror source -> AppKit/Qt logical screen."""
from .screens import BuiltinScreen,logical_rect


def mirror_source(ident,online,quartz):
    visited=set()
    while ident not in visited and ident in online:
        visited.add(ident);other=int(quartz.CGDisplayMirrorsDisplay(ident))
        if not other:return ident
        ident=other
    raise RuntimeError('显示器镜像关系尚未稳定，无法定位内置屏。')


def select(screens,*,quartz=None,appkit=None):
    if quartz is None:import Quartz as quartz
    if appkit is None:import AppKit as appkit
    error,ids,count=quartz.CGGetOnlineDisplayList(64,None,None)
    if error or count>=64:raise RuntimeError('无法完整读取在线显示器列表。')
    online={int(i) for i in ids}
    builtins=[i for i in online if quartz.CGDisplayIsBuiltin(i)]
    if len(builtins)!=1 or quartz.CGDisplayIsAsleep(builtins[0]):
        raise RuntimeError('内置屏不可用（可能已合盖或休眠）；不支持只在外接屏上追踪。')
    builtin=builtins[0];source=mirror_source(builtin,online,quartz)
    # Hardware mirror slaves need not be CGDisplayIsActive or have a QScreen.
    # Qt itself skips mirror slaves; bind the verified built-in's mirror source.
    native=[n for n in appkit.NSScreen.screens() if int(n.deviceDescription()['NSScreenNumber'])==source]
    if len(native)!=1:raise RuntimeError('内置屏的镜像源尚无可用逻辑屏幕，请稍后重试。')
    native=native[0];frame=native.frame()
    primary=quartz.CGDisplayBounds(quartz.CGMainDisplayID())
    # Same Cocoa bottom-left -> Qt top-left convention used by Qt's Cocoa plugin.
    expected=(round(frame.origin.x),round(primary.size.height-frame.origin.y-frame.size.height),
              round(frame.size.width),round(frame.size.height))
    candidates=[]
    for screen in screens:
        rect=logical_rect(screen)
        if ((rect.x,rect.y,rect.width,rect.height)==expected and screen.name()==native.localizedName()
                and abs(rect.dpr-float(native.backingScaleFactor()))<.01):candidates.append(screen)
    if len(candidates)!=1:raise RuntimeError('无法唯一对应内置屏的原生身份和Qt逻辑屏幕；不自动选择外接主屏。')
    # These public functions are exposed by PyObjC (the UUID creator is not
    # exposed in every bridge build). With exactly one verified physical
    # built-in, vendor/model/serial give a stable in-process hardware identity
    # even when its display ID or mirror source changes. Never use a name or
    # the external primary's identity as a substitute for CGDisplayIsBuiltin.
    identity='builtin:'+':'.join(str(int(getattr(quartz,name)(builtin))) for name in
                                ('CGDisplayVendorNumber','CGDisplayModelNumber','CGDisplaySerialNumber'))
    from dataclasses import replace
    rect=replace(logical_rect(candidates[0]),cocoa_top=float(primary.size.height))
    return BuiltinScreen(candidates[0],rect,identity)
