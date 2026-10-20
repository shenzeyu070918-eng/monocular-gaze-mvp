"""Offscreen Qt only: no physical display, camera, model or input injection."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from contextlib import redirect_stdout,redirect_stderr
import io
import signal
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QTimer,QSize,QRectF
from PySide6.QtGui import QImage,QAction,QIcon,QRegion
from PySide6.QtWidgets import QApplication,QSystemTrayIcon
from dataclasses import replace
from gaze_core.config import OverlaySettings,load_settings
from gaze_core.capture import Prediction
from overlay.controller import ControllerView
from overlay.mapping import ScreenRect
from overlay.window import OverlayWindow


class QtAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_window_flags_transparency_complete_corners_and_no_focus(self):
        w=OverlayWindow(ScreenRect(0,0,1512,982,2),OverlaySettings(),diagnostic=True)
        try:
            flags=w.windowFlags()
            for flag in (Qt.WindowType.Tool,Qt.WindowType.WindowTransparentForInput,
                         Qt.WindowType.WindowDoesNotAcceptFocus,Qt.WindowType.WindowStaysOnTopHint):
                self.assertTrue(flags&flag)
            self.assertTrue(w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertEqual(w.focusPolicy(),Qt.FocusPolicy.NoFocus)
            w.show();self.app.processEvents()
            image=QImage(w.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
            w.render(image)
            self.assertEqual(image.pixelColor(750,500).alpha(),0)
            for x,y in ((0,0),(1511,0),(0,981),(1511,981)):
                self.assertGreater(image.pixelColor(x,y).alpha(),0)
            self.assertEqual(w.width(),1512);self.assertEqual(w.height(),982)
        finally:w.close();w.deleteLater();self.app.processEvents()

    def test_native_failure_propagates_and_latest_ring_is_not_clipped(self):
        def fail(_):raise RuntimeError('fake native failure')
        w=OverlayWindow(ScreenRect(0,0,800,600),OverlaySettings(),native_configure=fail)
        try:
            with self.assertRaisesRegex(RuntimeError,'native'):w.prepare_native()
            w.set_display((-.2,700),(28,28),.5,'未校准')
            self.assertEqual(w.position,(-.2,700))
        finally:w.close();w.deleteLater()

    def test_label_positions_fit_screen_and_use_measured_width(self):
        for position in ('bottom_center','bottom_right','top_left'):
            for size in ((1512,982),(320,180),(80,40)):
                options=OverlaySettings(label_position=position)
                w=OverlayWindow(ScreenRect(-100,20,*size,2),options)
                try:
                    for label in ('未校准','未校准 · '+('长状态文字'*30)):
                        w.set_display(None,(28,28),0,label)
                        rect=w._label_rect()
                        self.assertTrue(QRectF(w.rect()).contains(rect),(position,size,rect))
                    if size==(1512,982):
                        w.set_display(None,(28,28),0,'未校准')
                        rect=w._label_rect()
                        expected=w._label_metrics.horizontalAdvance('未校准')+24
                        self.assertEqual(rect.width(),expected)
                        if position=='bottom_center':self.assertEqual(rect.center().x(),w.width()/2)
                        if position.startswith('bottom_'):self.assertEqual(w.height()-rect.bottom(),90)
                finally:w.close();w.deleteLater()

    def test_label_clock_change_and_fade_only_invalidate_label_regions(self):
        now=[0.]
        w=OverlayWindow(ScreenRect(0,0,1512,982),OverlaySettings(),clock=lambda:now[0])
        try:
            with patch.object(w,'update') as update:
                now[0]=4.;w._refresh_label();self.assertEqual(w._label_opacity,1.)
                update.assert_not_called()
                now[0]=4.1;w._refresh_label();self.assertAlmostEqual(w._label_opacity,.5)
                rect=w._label_rect();self.assertEqual(update.call_args.args,(QRegion(rect.toAlignedRect()),))
                now[0]=4.3;w._refresh_label();self.assertEqual(w._label_opacity,0.)
                self.assertFalse(w._label_timer.isActive())
                old=w._label_rect();update.reset_mock()
                w.set_display(None,(w.options.fallback_radius_px,)*2,0,'未校准 · 新状态较长')
                new=w._label_rect()
                expected=QRegion(old.toAlignedRect())|QRegion(new.toAlignedRect())
                update.assert_called_once_with(expected)
                self.assertEqual(w._label_opacity,1.);self.assertTrue(w._label_timer.isActive())
                now[0]=7.;w._refresh_label();self.assertEqual(w._label_opacity,1.)
                # Unchanged status + moving gaze cannot restart its hold.
                w.set_display((10,10),(28,28),.65,w.label)
                now[0]=8.6;w._refresh_label();self.assertEqual(w._label_opacity,0.)
                self.assertEqual(w.opacity,.65)
        finally:w.close();w.deleteLater()

    def test_diagnostic_and_zero_autohide_keep_label_and_ring_independent(self):
        for diagnostic,hold in ((True,4.),(False,0.)):
            now=[0.];w=OverlayWindow(ScreenRect(0,0,800,600),OverlaySettings(label_autohide_s=hold),
                                   diagnostic=diagnostic,clock=lambda:now[0])
            try:
                now[0]=100.;w._refresh_label()
                self.assertEqual(w._label_opacity,1.);self.assertFalse(w._label_timer.isActive())
                w.set_display((400,300),(28,28),.65,'未校准 · 持续显示')
                now[0]=200.;w._refresh_label();self.assertEqual(w._label_opacity,1.)
                image=QImage(w.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);w.render(image)
                if not diagnostic:self.assertGreater(image.pixelColor(428,300).alpha(),0)
            finally:w.close();w.deleteLater()

    def test_diagnostic_entrypoint_never_constructs_camera_and_exits_without_hardware(self):
        from overlay.app import main
        old=signal.getsignal(signal.SIGINT)
        try:
            for available,code in ((True,0),(False,1)):
                with patch('overlay.platforms.prepare_application'),patch('overlay.platforms.configure_window'), \
                     patch('overlay.platforms.builtin_screen',side_effect=lambda screens:screens[0]), \
                     patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=available), \
                     patch('gaze_core.capture.CameraPipeline',side_effect=AssertionError('camera must stay closed')), \
                     redirect_stdout(io.StringIO()) as output,redirect_stderr(io.StringIO()):
                    self.assertEqual(main(['--diagnostic','--duration','.02']),code)
                    text=output.getvalue()
                    for field in ('按 Ctrl+C 退出',f'isSystemTrayAvailable={available}',
                                  'tray.isVisible()='+('True' if available else '未创建'),'tray.geometry()='):
                        self.assertIn(field,text)
        finally:
            signal.signal(signal.SIGINT,old)
            for w in self.app.topLevelWidgets():w.close()
            self.app.processEvents()

    def test_mac_template_icon_has_transparent_black_ring_at_both_pixel_densities(self):
        from overlay.app import make_tray_icon
        with patch('overlay.app.sys.platform','darwin'):icon=make_tray_icon('#35d6b4')
        self.assertTrue(icon.isMask())
        self.assertEqual({(s.width(),s.height()) for s in icon.availableSizes()},{(18,18),(36,36)})
        for scale in (1,2):
            pixmap=icon.pixmap(QSize(18,18),float(scale))
            self.assertEqual(pixmap.devicePixelRatio(),scale)
            image=pixmap.toImage()
            self.assertEqual(image.size(),QSize(18*scale,18*scale))
            self.assertEqual(image.pixelColor(0,0).alpha(),0)
            self.assertEqual(image.pixelColor(9*scale,9*scale).alpha(),0)
            ring=image.pixelColor(3*scale,9*scale)
            self.assertGreater(ring.alpha(),0)
            self.assertEqual((ring.red(),ring.green(),ring.blue()),(0,0,0))

    def test_non_mac_tray_icon_remains_colored_and_not_a_mask(self):
        from overlay.app import make_tray_icon
        with patch('overlay.app.sys.platform','win32'):icon=make_tray_icon('#35d6b4')
        self.assertFalse(icon.isMask())
        image=icon.pixmap(32,32).toImage()
        self.assertEqual(image.pixelColor(0,0).alpha(),0)
        self.assertEqual(image.pixelColor(5,16).name(),'#35d6b4')

    def test_live_tray_pause_resume_and_exit_use_asynchronous_controller(self):
        from overlay.runtime import run_camera_overlay
        class Controller:
            def __init__(self):self.closed=threading.Event();self.status='paused';self.requests=[];self.seq=0;self.result=None
            def request(self,action):
                self.requests.append(action);self.status=action
                if action=='closed':self.closed.set()
                if action=='running':
                    self.seq+=1;ns=time.perf_counter_ns()
                    self.result=Prediction(self.seq,ns,ns,dict(valid=True,status='tracking',display=dict(position=[.5,.5],timestamp_source='capture_ns')))
            def snapshot(self):return ControllerView(self.status,None,self.result,False)
        settings=load_settings(use_local=False)
        settings=replace(settings,geometry=replace(settings.geometry,width_cm=30,height_cm=20,measured=True))
        for use_sigint in (False,True):
            c=Controller();w=OverlayWindow(ScreenRect(0,0,800,600),settings.overlay)
            tray=QSystemTrayIcon(QIcon());pause=QAction('暂停');quit_action=QAction('退出')
            quit_action.triggered.connect(self.app.quit)
            timer=QTimer();timer.start(settings.overlay.refresh_ms)
            old=signal.getsignal(signal.SIGINT);timed_out=[]
            def guard(controller=c,failures=timed_out):
                if not controller.closed.is_set():failures.append(True);controller.closed.set();self.app.quit()
            def finish():
                if use_sigint:signal.getsignal(signal.SIGINT)(signal.SIGINT,None)
                else:quit_action.trigger()
            try:
                QTimer.singleShot(30,pause.trigger)
                QTimer.singleShot(60,pause.trigger)
                QTimer.singleShot(100,finish)
                QTimer.singleShot(1000,guard)
                with patch('overlay.runtime.OverlayController',return_value=c):
                    self.assertEqual(run_camera_overlay(self.app,w,tray,pause,quit_action,timer,settings,lambda:None),0)
                self.assertEqual(c.requests,['running','paused','running','closed'])
                self.assertFalse(timed_out)
            finally:
                signal.signal(signal.SIGINT,old);timer.stop();w.close();w.deleteLater()


if __name__=='__main__':unittest.main()
