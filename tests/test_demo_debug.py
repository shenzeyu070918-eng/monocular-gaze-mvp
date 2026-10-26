"""Offscreen debug raster regression: own synthetic UI, no camera or disk images."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtGui import QColor,QImage
from PySide6.QtWidgets import QApplication
from gaze_core.config import DemoSettings
from gaze_core.demo import DemoRun
from gaze_core.demo_coordinates import DemoCoordinates
from overlay.demo_window import DemoWindow
from overlay.mapping import ScreenRect
import test_star_demo_qt


def rgb(image):
    converted=image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(converted.constBits(),np.uint8).reshape(converted.height(),converted.bytesPerLine())[:,:converted.width()*4].reshape(converted.height(),converted.width(),4)[:,:,:3].copy()


class DemoDebugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)

    def assert_sparse_debug(self,window):
        window.debug=True;window.refresh((180,200),1.,0,before_position=(120,200))
        image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);window.render(image)
        pixels=rgb(image);color=QColor(window.options.debug_after_color)
        count=np.all(pixels==[color.red(),color.green(),color.blue()],axis=2).sum()
        self.assertGreater(count,50);self.assertLess(count/pixels.shape[0]/pixels.shape[1],.025)
        return pixels

    def test_practice_board_and_galaxy_keep_target_visible(self):
        for application in ('board','galaxy'):
            with self.subTest(app=application):
                run=DemoRun(application,replace(DemoSettings(),practice=True),1512,982,(90,90),group='B')
                window=DemoWindow(run,self.app.primaryScreen(),run.options,screen_rect=ScreenRect(0,0,1512,982),debug=True,clock_ns=lambda:0)
                window.resize(1512,982)
                window.coordinates=lambda:DemoCoordinates(window.screen_rect,window_origin=(0,0),window_size=(1512,982),fullscreen=True)
                try:
                    pixels=self.assert_sparse_debug(window)
                    x,y,w,h=run.buttons[0].rect;cx,cy=round(x+w/2),round(y+h/2)
                    color=QColor(run.options.text_color);target=pixels[cy-6:cy+7,cx-6:cx+7]
                    self.assertGreater(np.all(target==[color.red(),color.green(),color.blue()],axis=2).sum(),10)
                finally:run.close(0);window.dispose();self.app.processEvents()

    def test_qt_star_fallback_debug_does_not_fill_scene(self):
        for practice in (True,False):
            with self.subTest(practice=practice):
                _,run,window=test_star_demo_qt.StarDemoQtTests.make(self,practice=practice)
                try:self.assert_sparse_debug(window)
                finally:
                    run.close(0)
                    with patch('overlay.starfield.save_starfield_performance',return_value={'paint_event':{'milliseconds':{'median':None,'p90':None}}}):window.dispose()
                    self.app.processEvents()


if __name__=='__main__':unittest.main()
