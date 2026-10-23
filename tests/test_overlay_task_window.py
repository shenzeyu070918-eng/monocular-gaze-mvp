"""Offscreen task-window interactions; no real camera/display/input listener."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import unittest
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch,Mock

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication

from gaze_core.config import load_settings
from gaze_core.capture import Prediction
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

    def notch_client(self, *, fullscreen=True):
        """Simulate the real Mac client without a display or native Space."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.window.screen_rect = ScreenRect(0, 0, 1512, 982, 2.)
        self.window.resize(1512, 950)
        stack.enter_context(patch.object(self.window, 'mapToGlobal', return_value=QPointF(0, 32)))
        stack.enter_context(patch.object(self.window, 'isFullScreen', return_value=fullscreen))
        self.window._invalidate_coordinates()
        return stack

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self.window.render(image)
        return image

    def test_notch_client_uses_complete_screen_target_then_subtracts_actual_origin(self):
        self.notch_client()
        self.window.geometry_logger=Mock()
        self.assertTrue(self.window.coordinates_ready)
        center = self.window._target_rect((.5, .5)).center()
        self.assertEqual((center.x(), center.y()), (755.5, 458.5))
        top = self.window._target_rect((.5, .05)).center()
        self.assertAlmostEqual(top.y(), 17.05)
        with self.assertLogs('overlay.task_window', level='INFO') as logs:
            image = self.render()
        self.assertIn('全局原点=(0.0, 32.0)，尺寸=1512.0×950.0', logs.output[0])
        self.window.geometry_logger.assert_called_once()
        self.assertIn('全局原点=(0.0, 32.0)，尺寸=1512.0×950.0',self.window.geometry_logger.call_args.args[0])
        self.assertEqual(image.pixelColor(755, 458).name(), '#64b5f6')
        event = self.runner.drain_events()[0]
        self.assertEqual(event['target'], [.5, .5])
        self.assertEqual(self.runner.state, 'sampling')

    def test_target_whole_circle_outside_client_refuses_before_any_presentation_or_sample(self):
        self.runner.targets[0]['target'] = [.5, .04]
        self.notch_client()
        seen = []
        self.window.target_presented.connect(seen.append)
        self.assertFalse(self.window.coordinates_ready)
        self.assertIn('完整目标圆', self.window.coordinate_reason)
        self.assertFalse(self.window.accept_prediction())
        self.assertEqual(self.runner.reason, 'target_outside_visible_client')
        self.assertIn('calibration-1', self.runner.error)
        self.render()
        self.assertEqual(seen, [])
        self.assertEqual(self.runner._windows, [])

    def test_coordinate_wait_is_visible_after_one_second_without_presentation_ack(self):
        self.notch_client(fullscreen=False)
        seen = []
        self.window.target_presented.connect(seen.append)
        self.assertFalse(self.window.accept_prediction())
        self.now[0] += 1_000_000_001
        self.assertFalse(self.window.accept_prediction())
        self.assertIn('尚未进入全屏', self.window.coordinate_warning)
        self.render()
        self.assertEqual(seen, [])
        self.assertEqual(self.runner.state, 'presenting')
        self.assertEqual(self.runner._windows, [])

    def test_client_geometry_change_cancels_sampling_instead_of_using_old_target_window(self):
        self.notch_client()
        self.render()
        self.assertEqual(self.runner.state, 'sampling')
        with patch.object(self.window, 'mapToGlobal', return_value=QPointF(0, 40)):
            self.window._invalidate_coordinates()
            self.assertFalse(self.window.accept_prediction())
        self.assertEqual(self.runner.reason, 'task_window_geometry_changed')
        self.assertIn('实际几何已变化', self.runner.error)
        self.assertIsNone(self.runner.result)

    def test_notch_evaluation_samples_unfiltered_screen_coordinates_for_all_targets(self):
        self.runner.cancel('replace')
        settings = replace(self.settings, geometry=replace(self.settings.geometry, width_cm=30., height_cm=20., measured=True))
        self.runner = TaskRunner('evaluate', settings, clock_ns=lambda: self.now[0])
        self.window.runner = self.runner
        self.runner.begin()
        self.notch_client()
        sequence = 0
        for point in self.runner.targets:
            target = list(point['target'])
            self.render()
            presented_ns = self.now[0]
            circle = self.window._target_rect(target).center()
            self.assertAlmostEqual(circle.y(), target[1]*981-32)
            for sample in range(settings.evaluation.calibration_min_samples):
                capture_ns = presented_ns+int((settings.evaluation.settle_ms+sample*50+1)*1e6)
                prediction = Prediction(sequence, capture_ns, capture_ns+1,
                                        dict(valid=True, raw=target, distance_cm=45., origin_m=None))
                self.assertTrue(self.window.accept_prediction(prediction))
                self.runner.ingest(prediction)
                sequence += 1
            self.now[0] = presented_ns+int((settings.evaluation.dwell_ms+1)*1e6)
            self.runner.advance(self.now[0])
        self.assertEqual(self.runner.state, 'finishing')
        self.runner.ingest(Prediction(sequence, self.now[0]+1, self.now[0]+2, dict(valid=False)))
        result = self.runner.result
        self.assertEqual(self.runner.state, 'completed')
        self.assertEqual(result['sample_count'], len(self.runner.targets)*settings.evaluation.calibration_min_samples)
        self.assertEqual(result['summary']['groups']['A']['all']['mean_cm'], 0.)
        for row in self.runner._frames:
            target = next(point['target'] for point in self.runner.targets if point['target_id'] == row['target_id'])
            self.assertEqual(row['raw'], target)

    def test_notch_redo_target_keeps_original_full_screen_position(self):
        self.runner.cancel('replace')
        first = dict(redo_round=0, calibration_id='original-calibration',
                     quality=dict(points=[dict(target_id='calibration-7', redo=True)]))
        self.runner = TaskRunner('calibrate', self.settings, clock_ns=lambda: self.now[0],
                                 redo_from=first, redo_target_ids=['calibration-7'])
        self.window.runner = self.runner
        self.runner.begin()
        self.notch_client()
        self.render()
        target = self.runner.current_target
        self.assertEqual(target['target'], [.5, .88])
        self.assertAlmostEqual(self.window._target_rect(target['target']).center().y(), .88*981-32)
        self.assertEqual(self.runner.drain_events()[0]['target'], [.5, .88])


if __name__ == '__main__':
    unittest.main()
