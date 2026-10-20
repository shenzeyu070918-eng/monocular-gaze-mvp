"""Offscreen task-window interactions; no real camera/display/input listener."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication

from gaze_core.config import load_settings
from overlay.tasks import TaskRunner
from overlay.task_window import TaskWindow
from overlay.mapping import ScreenRect


class TaskWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.settings = load_settings(use_local=False)
        self.now = [12_345_678_901_234]
        self.runner = TaskRunner('calibrate', self.settings, clock_ns=lambda: self.now[0])
        self.runner.begin()
        self.window = TaskWindow(self.runner, self.app.primaryScreen(), self.settings.overlay,
                                 clock_ns=lambda: self.now[0])

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_interactive_fullscreen_flags_paint_feedback_keeps_64_bit_clock(self):
        seen = []
        self.window.target_presented.connect(seen.append)
        self.window.open()
        self.app.processEvents()
        self.assertTrue(self.window.isFullScreen())
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertFalse(self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertEqual(self.runner.state, 'sampling')
        self.assertEqual(seen, self.now)
        self.window.refresh()
        self.app.processEvents()
        self.assertEqual(len(seen), 1)

    def test_escape_and_close_cancel_without_partial_result(self):
        cancelled = []
        self.window.cancelled.connect(cancelled.append)
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.window.keyPressEvent(event)
        self.assertEqual(cancelled, ['cancelled'])
        self.assertIsNone(self.runner.result)
        self.assertFalse(self.window.isVisible())

    def test_close_is_cancellation_not_success(self):
        cancelled = []
        self.window.cancelled.connect(cancelled.append)
        self.window.close()
        self.assertEqual(cancelled, ['window_closed'])
        self.assertIsNone(self.runner.result)

    def test_hint_does_not_cover_center_target(self):
        self.window.open()
        self.app.processEvents()
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self.window.render(image)
        # The first nine-point target is at screen center. Its interior remains
        # uniformly blue; no center hint is painted over the target.
        x, y = self.window.width()//2, self.window.height()//2
        self.assertEqual(image.pixelColor(x, y).name(), '#64b5f6')
        self.assertEqual(image.pixelColor(x+2, y).name(), '#64b5f6')

    def test_leaving_fullscreen_cancels(self):
        self.window.open()
        self.app.processEvents()
        self.window.showNormal()
        self.app.processEvents()
        self.assertEqual(self.runner.state, 'cancelled')
        self.assertEqual(self.runner.reason, 'fullscreen_exited')

    def test_target_centers_use_same_logical_mapping_as_overlay(self):
        mapping = ScreenRect(0, 0, self.window.width(), self.window.height(), 2.)
        for point in ((.05, .05), (.95, .95), (.5, .5), (.12, .88)):
            center = self.window._target_rect(point).center()
            self.assertEqual((center.x(), center.y()), mapping.local(point))


if __name__ == '__main__':
    unittest.main()
