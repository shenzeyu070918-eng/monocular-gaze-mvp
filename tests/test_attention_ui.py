import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from dataclasses import replace
import numpy as np
from PIL import Image,ImageOps
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt
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
    def test_jpeg_exif_matches_analyzed_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'中文 图片.jpg';image=Image.new('RGB',(100,60),'white');exif=image.getexif();exif[274]=6;image.save(path,exif=exif)
            screen=self.app.primaryScreen();g=screen.geometry();window=StimulusWindow(path,screen,ScreenRect(g.x(),g.y(),g.width(),g.height()))
            try:
                expected=ImageOps.exif_transpose(Image.open(path));self.assertEqual((window.image.width(),window.image.height()),expected.size)
            finally:window.close();window.deleteLater()
