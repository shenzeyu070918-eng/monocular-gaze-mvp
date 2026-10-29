import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from dataclasses import replace
import numpy as np
from PIL import Image,ImageOps
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QMoveEvent
from PySide6.QtCore import Qt,QPoint
from overlay.mapping import ScreenRect
from overlay.window import OverlayWindow
from overlay.attention import StimulusWindow
from gaze_core.config import load_settings

class AttentionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_hidden_ring_clears_only_ring_and_can_temporarily_show(self):
        window=OverlayWindow(ScreenRect(0,0,640,480),load_settings(use_local=False).overlay,native_configure=None)
        try:
            window.set_display((320,200),(30,30),1.,'录制提示仍由独立窗口显示')
            def pixels():
                image=QImage(640,480,QImage.Format.Format_RGBA8888);image.fill(Qt.GlobalColor.transparent);window.render(image)
                a=np.frombuffer(image.bits(),np.uint8).reshape(480,640,4)
                region=a[165:235,285:355];return int(np.count_nonzero(region[:,:,3]))
            shown=pixels();self.assertGreater(shown,50)
            window.set_attention_ring_hidden(True);self.assertEqual(pixels(),0)
            window.set_attention_ring_hidden(False);self.assertEqual(pixels(),shown)
        finally:window.close();window.deleteLater()
    def test_closed_stimulus_does_not_start_from_late_ready_and_move_cancels(self):
        from unittest.mock import patch
        from gaze_core.demo_coordinates import DemoCoordinates
        from gaze_core.attention.stimulus import StimulusMapping
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.png';Image.new('RGB',(10,10)).save(path)
            screen=self.app.primaryScreen();g=screen.geometry();rect=ScreenRect(g.x(),g.y(),g.width(),g.height())
            window=StimulusWindow(path,screen,rect);ready=[];cancel=[];window.ready.connect(ready.append);window.cancelled.connect(lambda:cancel.append(True))
            try:
                window.mapping=StimulusMapping(DemoCoordinates(rect,window_origin=(g.x(),g.y()),window_size=(g.width(),g.height()),fullscreen=True),(10,10))
                with patch.object(window,'mapToGlobal',return_value=QPoint(g.x()+10,g.y())):window.moveEvent(QMoveEvent(QPoint(10,0),QPoint(0,0)))
                self.assertTrue(cancel);window.close();window._ready();self.assertEqual(ready,[])
            finally:window.close();window.deleteLater()
    def test_idle_actions_enable_after_camera_ready_then_disable_during_task(self):
        from types import SimpleNamespace as NS
        from overlay.actions import OverlayActions
        from overlay.panel import ControlPanel
        from overlay.attention import AttentionUI
        settings=load_settings(use_local=False);rect=ScreenRect(0,0,640,480)
        actions=OverlayActions();panel=ControlPanel(actions);window=OverlayWindow(rect,settings.overlay,native_configure=None)
        session=NS(settings=settings,screen=rect,latest=None,active=False,profiles=NS(profile=None),_active_group='A')
        controls=NS(actions=actions,panel=panel,screen=self.app.primaryScreen())
        ui=AttentionUI(session,window,controls,lambda *a:None)
        try:
            ui.poll(active=False,screen_changed=False,demo_active=False,now_ns=0)
            self.assertFalse(actions['attention_start'].isEnabled())
            session.latest=object();ui.poll(active=True,screen_changed=False,demo_active=False,now_ns=1)
            self.assertTrue(actions['attention_start'].isEnabled());self.assertTrue(actions['attention_guide'].isEnabled())
            session.active=True;ui.poll(active=True,screen_changed=False,demo_active=False,now_ns=2)
            self.assertFalse(actions['attention_start'].isEnabled())
        finally:ui.close_ui();ui.service.close();window.close();window.deleteLater();panel.hide();panel.deleteLater()
    def test_jpeg_exif_matches_analyzed_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'中文 图片.jpg';image=Image.new('RGB',(100,60),'white');exif=image.getexif();exif[274]=6;image.save(path,exif=exif)
            screen=self.app.primaryScreen();g=screen.geometry();window=StimulusWindow(path,screen,ScreenRect(g.x(),g.y(),g.width(),g.height()))
            try:
                expected=ImageOps.exif_transpose(Image.open(path));self.assertEqual((window.image.width(),window.image.height()),expected.size)
            finally:window.close();window.deleteLater()
