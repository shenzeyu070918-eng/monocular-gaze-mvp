"""Only our interactive Qt rectangles are used for exclusion."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import unittest
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import Qt
from overlay.own_windows import OwnWindows


class OwnWindowsTests(unittest.TestCase):
    def test_hidden_moved_and_click_through_windows(self):
        app=QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        panel=QWidget();panel.setGeometry(10,20,100,100)
        overlay=QWidget();overlay.setWindowFlag(Qt.WindowType.WindowTransparentForInput,True)
        overlay.setGeometry(200,200,100,100)
        cache=OwnWindows(app)
        try:
            panel.show();overlay.show();app.processEvents();cache.refresh()
            self.assertEqual(cache.reason((50,60)),'own_ui')
            self.assertIsNone(cache.reason((250,250)))
            snapshot=cache.rectangles;cache.refresh();self.assertIs(cache.rectangles,snapshot)
            panel.move(400,400);app.processEvents();cache.refresh()
            self.assertIsNone(cache.reason((50,60)))
            self.assertEqual(cache.reason((450,450)),'own_ui')
            panel.hide();app.processEvents();cache.refresh()
            self.assertIsNone(cache.reason((450,450)))
            cache.external_rectangles=lambda:((0,0,1512,982),)  # Only our active Galaxy viewport.
            self.assertEqual(cache.reason((450,450)),'own_ui')
            self.assertIsNone(cache.reason((1600,450)))
            cache.external_rectangles=lambda:()
            self.assertIsNone(cache.reason((450,450)))
        finally:
            cache.close();panel.close();overlay.close();panel.deleteLater();overlay.deleteLater();app.processEvents()


if __name__=='__main__':unittest.main()
