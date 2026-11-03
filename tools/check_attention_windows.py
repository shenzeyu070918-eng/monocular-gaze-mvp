"""Run once on macOS: controlled self-window exclusion, no gaze/camera required.

Only the inset controlled fixture is saved, never the surrounding desktop.
The child process supplies a fixed background. All tested own windows stay
visible throughout both filtered captures; no hide-before-capture workaround.
"""
import argparse,json,os,subprocess,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description='M9 受控自窗口零残留测试（不打开摄像头）')
    parser.add_argument('--fixture-child',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--output',default='data/attention-window-check')
    args=parser.parse_args()
    if sys.platform!='darwin':parser.error('此受控原生过滤测试目前只支持 macOS')
    from PySide6.QtWidgets import QApplication,QWidget,QLabel,QMenu
    from PySide6.QtCore import Qt,QTimer,QRectF,QPoint
    from PySide6.QtGui import QPainter,QColor,QImage
    from overlay.screens import startup_screen
    from gaze_core.config import ROOT,load_settings
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);selected=startup_screen(app);s=selected.rect
    if args.fixture_child:
        class Fixture(QWidget):
            def paintEvent(self,e):
                p=QPainter(self);p.fillRect(self.rect(),QColor('#203448'));p.end()
        w=Fixture(None,Qt.WindowType.FramelessWindowHint);w.setGeometry(s.x,s.y,s.width,s.height);w.show();w.raise_();w.activateWindow()
        import objc,AppKit
        native=objc.objc_object(c_void_p=int(w.winId())).window();native.setLevel_(AppKit.NSStatusWindowLevel-1);native.setHasShadow_(False)
        AppKit.NSApp.activateIgnoringOtherApps_(True);native.makeKeyAndOrderFront_(None)
        QTimer.singleShot(90000,app.quit);return app.exec()
    output=Path(args.output)
    if not output.is_absolute():output=ROOT/output
    if not output.resolve().is_relative_to((ROOT/'data/attention-window-check').resolve()):parser.error('测试结果只能放在data/attention-window-check下')
    output.mkdir(parents=True,exist_ok=True)
    print('将显示固定底图及本程序所有测试窗口，约10秒；请勿切换应用。不会打开摄像头。',flush=True)
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--fixture-child'])
    windows=[];result={};phase_data=[];error=[];done=threading.Event();backend=None
    try:
        from dataclasses import replace
        import numpy as np
        from overlay.window import OverlayWindow
        from overlay.platforms import configure_window
        from overlay.actions import OverlayActions
        from overlay.panel import ControlPanel
        from overlay.attention_macos import MacScreenCapture
        from gaze_core.attention.window_check import COMPONENTS,compare_fixture
        margin=64;W,H=s.width,s.height
        if W<900 or H<650:raise RuntimeError('受控布局需要至少900×650逻辑像素内屏')
        regions={
            'ring':(int(W*.18)-40,int(H*.28)-40,80,80),
            'heatmap':(int(W*.08),int(H*.52),int(W*.2),int(H*.15)),
            'status':(int(W*.35),int(H*.83),int(W*.25),40),
            'badge':(int(W*.08),int(H*.11),220,40),
            'panel':(int(W*.64),int(H*.16),int(W*.26),int(H*.40)),
            'menu':(int(W*.65),int(H*.65),210,80),
            'late_window':(int(W*.38),int(H*.18),int(W*.18),80)}
        class MarkedOverlay(OverlayWindow):
            def paintEvent(self,e):
                super().paintEvent(e);p=QPainter(self)
                x,y,w,h=regions['status'];p.fillRect(x,y,w,h,QColor('#ff00ff'));p.end()
        options=replace(load_settings(use_local=False).overlay,color='#ff00ff',label_autohide_s=0)
        overlay=MarkedOverlay(s,options,native_configure=configure_window);overlay.prepare_native();windows.append(overlay)
        x,y,w,h=regions['ring'];overlay.set_display((x+w/2,y+h/2),(32,32),1.,'受控状态条 · 不隐藏')
        heat=QImage(W,H,QImage.Format.Format_RGBA8888);heat.fill(Qt.GlobalColor.transparent)
        p=QPainter(heat);p.fillRect(*regions['heatmap'],QColor('#ff00ff'));p.end()
        overlay.set_heatmap(dict(version=1,image=heat),enabled=True)
        def bring_above_fixture(widget):
            import objc,AppKit
            native=objc.objc_object(c_void_p=int(widget.winId())).window()
            native.setLevel_(AppKit.NSStatusWindowLevel+1);native.setHasShadow_(False)
            native.orderFrontRegardless()
        def label(name):
            x,y,w,h=regions[name];v=QLabel(name,None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
            v.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            v.setGeometry(s.x+x,s.y+y,w,h);v.setStyleSheet('background:#ff00ff;color:black');v.show();bring_above_fixture(v);windows.append(v);return v
        badge=label('badge');configure_window(badge)
        panel=ControlPanel(OverlayActions());panel.setGeometry(s.x+regions['panel'][0],s.y+regions['panel'][1],regions['panel'][2],regions['panel'][3]);panel.setStyleSheet('background:#ff00ff;color:black');windows.append(panel)
        menu=QMenu();menu.setStyleSheet('QMenu {background:#ff00ff;color:black;padding:14px;}');menu.addAction('受控菜单 A');menu.addAction('受控菜单 B');windows.append(menu)
        def show_own():
            overlay.show();panel.show();bring_above_fixture(panel)
            menu.popup(QPoint(s.x+regions['menu'][0],s.y+regions['menu'][1]));bring_above_fixture(menu)
        # Ignore foreground identity ONLY inside this fixed-fixture tool. This
        # deliberately isolates exclusion even with our panel/menu in front.
        # The production foreground denylist test remains a separate guide step.
        class ControlledCapture(MacScreenCapture):
            def _front(self):return child.pid,'M9 controlled fixture','fixture'
        def capture(filtered):
            if filtered:return backend.capture().pixels
            content=backend._content();display=next(d for d in content.displays() if backend.Q.CGDisplayIsBuiltin(d.displayID()))
            f=backend.S.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display,[])
            c=backend.S.SCStreamConfiguration.alloc().init();c.setWidth_(W);c.setHeight_(H);c.setShowsCursor_(False)
            image=backend._wait(lambda cb:backend.S.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(f,c,cb))
            return backend._rgb(image)
        visibility={'all':False}
        def worker():
            nonlocal backend
            try:
                backend=ControlledCapture(s,request_permission=True)
                for phase in range(2):
                    # The late window is created only after the same backend
                    # has already completed a filtered capture.
                    if phase==1:
                        create_late.set()
                        if not late_ready.wait(5):raise RuntimeError('后创建窗口未显示')
                    time.sleep(.7)
                    raw=capture(False);filtered=capture(True)
                    crop=(slice(margin,H-margin),slice(margin,W-margin))
                    expected=np.full_like(filtered[crop],[32,52,72])
                    local={n:(x-margin,y-margin,w,h) for n,(x,y,w,h) in regions.items()}
                    comparison=compare_fixture(raw[crop],filtered[crop],expected,local)
                    comparison['phase']='initial' if phase==0 else 'post_new_window'
                    comparison['all_visible']=visibility['all']
                    comparison['passed']=comparison['passed'] and comparison['all_visible']
                    phase_data.append(comparison)
                    if not comparison['passed']:raise RuntimeError('固定底图/窗口可见性/排除像素检查失败；不保存可能含外部画面的截图')
                    from PIL import Image
                    Image.fromarray(raw[crop]).save(output/f'{phase}-included.png')
                    Image.fromarray(filtered[crop]).save(output/f'{phase}-excluded.png')
            except Exception as exc:error.append(str(exc))
            finally:
                if backend:backend.close()
                done.set()
        create_late=threading.Event();late_ready=threading.Event();started=[False];late_created=[False]
        placeholder=label('late_window')
        def poll():
            if not started[0]:
                started[0]=True;show_own();threading.Thread(target=worker,name='attention-window-check',daemon=True).start()
            if create_late.is_set() and not late_created[0]:
                late_created[0]=True;new=label('late_window');new.raise_();late_ready.set()
            visibility['all']=all(v.isVisible() for v in windows)
            if done.is_set():app.quit()
        timer=QTimer();timer.timeout.connect(poll)
        QTimer.singleShot(1800,lambda:timer.start(100));QTimer.singleShot(45000,app.quit)
        app.exec()
        result=dict(schema_version=1,screen_size=[W,H],component_names=list(COMPONENTS),completed_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    phases=phase_data,passed=done.is_set() and not error and len(phase_data)==2 and all(p['passed'] for p in phase_data),errors=error,
                    note='固定底图外接进程；不隐藏任何测试窗口；前台身份固定以隔离过滤测试，不替代排除应用验收。只保存受控内框，不保存外部桌面。')
    except Exception as exc:result=dict(schema_version=1,passed=False,errors=[str(exc)])
    finally:
        for w in windows:w.close()
        child.terminate()
        try:child.wait(4)
        except subprocess.TimeoutExpired:child.kill();child.wait()
    path=output/'latest.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(('通过' if result['passed'] else '失败')+'：自窗口零残留测试 · '+str(path),flush=True)
    if result.get('errors'):print('；'.join(result['errors']))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
