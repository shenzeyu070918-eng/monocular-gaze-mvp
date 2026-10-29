"""ScreenCaptureKit adapter. Imports and permissions occur ONLY on user start."""
import os,threading,time
import numpy as np
from gaze_core.attention.contracts import ScreenSample,CapturePaused

PERMISSION='没有屏幕录制权限：请到「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」为实际启动的终端/应用授权，按系统提示重新打开后重试。'

class MacScreenCapture:
    def __init__(self,screen,excluded_apps=(),*,request_permission=False):
        import Quartz as Q,ScreenCaptureKit as S
        from AppKit import NSWorkspace
        self.Q,self.S,self.workspace=Q,S,NSWorkspace.sharedWorkspace()
        if not Q.CGPreflightScreenCaptureAccess():
            if request_permission:Q.CGRequestScreenCaptureAccess()
            if not Q.CGPreflightScreenCaptureAccess():raise PermissionError(PERMISSION)
        self.screen=screen;self.excluded=set(excluded_apps);self.closed=False;self._activation_revision=0
        from AppKit import NSWorkspaceDidActivateApplicationNotification
        def activated(note):self._activation_revision+=1
        self._observer=self.workspace.notificationCenter().addObserverForName_object_queue_usingBlock_(NSWorkspaceDidActivateApplicationNotification,None,None,activated)

    def _wait(self,invoke):
        done=threading.Event();result=[]
        def callback(obj,error):result.append((obj,error));done.set()
        invoke(callback)
        if not done.wait(15):raise RuntimeError('截屏接口超时，已停止采集')
        obj,error=result[0]
        if error is not None:raise RuntimeError('屏幕采集失败：'+str(error))
        return obj

    def _content(self):
        return self._wait(lambda cb:self.S.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(False,True,cb))

    def _front(self):
        a=self.workspace.frontmostApplication()
        if a is None:raise CapturePaused('frontmost_unknown')
        return (int(a.processIdentifier()),str(a.localizedName() or ''),str(a.bundleIdentifier() or ''))

    def capture(self):
        if self.closed:raise RuntimeError('截屏已关闭')
        import objc
        with objc.autorelease_pool():
            activation=getattr(self,'_activation_revision',0)
            front=self._front()
            if front[0]==os.getpid():raise CapturePaused('own_ui')
            if front[1] in self.excluded or front[2] in self.excluded:raise CapturePaused('excluded_app')
            content=self._content();apps=list(content.applications())
            display=next((d for d in content.displays() if self.Q.CGDisplayIsBuiltin(d.displayID())==1),None)
            if display is None:raise CapturePaused('builtin_unavailable')
            frame=display.frame()
            if (round(frame.size.width),round(frame.size.height))!=(self.screen.width,self.screen.height):raise CapturePaused('screen_geometry_changed')
            own=[a for a in apps if a.processID()==os.getpid()]
            if not own:raise RuntimeError('无法建立本程序窗口排除过滤器，拒绝截屏')
            filtered=own+[a for a in apps if str(a.applicationName()) in self.excluded or str(a.bundleIdentifier()) in self.excluded]
            native_filter=self.S.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(display,filtered,[])
            config=self.S.SCStreamConfiguration.alloc().init();config.setWidth_(self.screen.width);config.setHeight_(self.screen.height)
            config.setShowsCursor_(False);config.setCapturesAudio_(False)
            start=time.perf_counter_ns()
            image=self._wait(lambda cb:self.S.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(native_filter,config,cb))
            end=time.perf_counter_ns()
            after=self._front()
            # Refresh applications after completion: a newly launched excluded
            # app cannot slip into a saved frame through an old PID filter.
            current=self._content()
            signature=lambda values:sorted((int(a.processID()),str(a.bundleIdentifier())) for a in values)
            if after!=front or getattr(self,'_activation_revision',0)!=activation or signature(current.applications())!=signature(apps):raise CapturePaused('foreground_or_filter_changed')
            pixels=self._rgb(image)
            if pixels.shape[:2]!=(self.screen.height,self.screen.width):raise RuntimeError('实际截屏尺寸不匹配内屏逻辑尺寸')
            return ScreenSample(pixels,start,end,front[1])

    def _rgb(self,image):
        q=self.Q;w,h=q.CGImageGetWidth(image),q.CGImageGetHeight(image)
        bits=q.CGImageGetBitsPerPixel(image);info=int(q.CGImageGetBitmapInfo(image));alpha=info&31
        if bits==32 and info&0x7000==0x2000 and alpha in (2,4,6):
            data=q.CGDataProviderCopyData(q.CGImageGetDataProvider(image));stride=q.CGImageGetBytesPerRow(image)
            rows=np.frombuffer(data,np.uint8).reshape(h,stride)[:,:w*4].reshape(h,w,4)
            return rows[:,:,:3][:,:,::-1].copy()
        # Portable format fallback; direct BGRA path avoids PNG on normal frames.
        from AppKit import NSBitmapImageRep,NSPNGFileType
        from PIL import Image
        import io
        rep=NSBitmapImageRep.alloc().initWithCGImage_(image)
        return np.asarray(Image.open(io.BytesIO(bytes(rep.representationUsingType_properties_(NSPNGFileType,{})))).convert('RGB')).copy()

    def close(self):
        self.closed=True
        observer=getattr(self,'_observer',None)
        if observer is not None:self.workspace.notificationCenter().removeObserver_(observer);self._observer=None
