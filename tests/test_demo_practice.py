"""Real DemoSession with numeric frames and a fake window; no native input/I/O."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from gaze_core.demo_coordinates import DemoCoordinates
from overlay.demo_session import DemoSession
from overlay.mapping import ScreenRect
from overlay.state import RingView


class PracticeWindow:
    def __init__(self, run, screen, options, *, clock_ns, screen_rect, debug):
        self.run, self.screen_rect, self.debug = run, screen_rect, debug
        self.cancelled = Mock()
        self.open = Mock()
        self.dispose = Mock()
        self.refresh = Mock()

    def coordinates(self):
        screen = self.screen_rect
        return DemoCoordinates(screen, window_origin=(screen.x, screen.y),
                               window_size=(screen.width, screen.height), fullscreen=True)


class DemoPracticeTests(unittest.TestCase):
    def make(self, *, measured=True, **demo_options):
        settings = load_settings(use_local=False)
        settings = replace(settings,
                           geometry=replace(settings.geometry, width_cm=30., height_cm=20., measured=measured),
                           demo=replace(settings.demo, practice=True, dwell_ms=800, **demo_options))
        profile = {'pipeline': 'python', 'matrix': [[1., 0.], [0., 1.], [.03, -.02]],
                   'kind': 'nine_point'}
        controller = SimpleNamespace(snapshot=Mock(return_value=SimpleNamespace(status='running')),
                                     set_matrix=Mock())
        session = SimpleNamespace(settings=settings, active=False, clicks=None, display_revision=3,
                                  _active_group='B', radius=2., controller=controller,
                                  profiles=SimpleNamespace(busy=False, conditions={'camera': 1}, profile=profile),
                                  set_demo_active=Mock(), recording=Mock())
        session.recording.status.return_value = {'active': False}
        overlay = Mock()
        overlay.rect_info = ScreenRect(-1512, 40, 1512, 982, 2)
        overlay.isVisible.return_value = True
        panel = Mock()
        panel.isVisible.return_value = False
        now = [0]
        manager = DemoSession(session, overlay, panel, notice=Mock(), window_factory=PracticeWindow,
                              speech_factory=Mock(side_effect=RuntimeError('synthetic speech failure')),
                              clock_ns=lambda: now[0], debug=True)
        manager._test_sequence = 0
        return manager, session, overlay, panel, now

    def center(self, manager, button=None):
        button = manager.run.buttons[0] if button is None else button
        x, y, w, h = button.rect
        return ((x + w / 2) / (manager.run.width - 1),
                (y + h / 2) / (manager.run.height - 1))

    def tick(self, manager, now, ms, point, *, valid=True):
        now[0] = round(ms * 1_000_000)
        data = {'raw': [.17, .83], 'origin_m': [0., 0., .5]}
        prediction = Prediction(manager._test_sequence, now[0], now[0] + 99_000_000, data)
        manager._test_sequence += 1
        display = RingView(None if point is None else tuple(point), .65, 'tracking', valid)
        manager.tick(prediction, display, now[0], active=True, eligible=True)
        self.assertEqual(prediction.data, data)
        return prediction

    def practice(self, manager, now, offset=(.02, -.03), *, start_ms=0, early_offset=None):
        target = self.center(manager)
        manager.run.presented(round(start_ms * 1_000_000))
        for elapsed in range(0, 801, 50):
            shift = early_offset if early_offset is not None and elapsed <= 200 else offset
            point = tuple(t - delta for t, delta in zip(target, shift))
            self.tick(manager, now, start_ms + elapsed, np.asarray(point))
        return target

    def enter_board(self, manager, now, at_ms=1600):
        now[0] = round(at_ms * 1_000_000)
        manager.tick(None, RingView(None, 0., 'tracking', False), now[0], active=True, eligible=False)
        self.assertEqual(manager.run.phase, 'playing')
        manager.run.presented(now[0])

    def test_last_600ms_corrected_hit_and_debug_before_after(self):
        manager, session, overlay, panel, now = self.make()
        manager.start('board', None)
        target = self.practice(manager, now, early_offset=(.08, .04))
        self.assertEqual(manager.run.phase, 'feedback')
        np.testing.assert_allclose(manager.drift.offset, (.02, -.03))
        self.assertEqual(manager.drift.last_result.sample_count, 13)
        self.assertIn('已修正', panel.demo_status.setText.call_args.args[0])
        args, kwargs = manager.window.refresh.call_args
        expected = manager.window.coordinates().screen_to_window(target)
        np.testing.assert_allclose(args[0], expected)
        np.testing.assert_allclose(kwargs['before_position'],
                                   manager.window.coordinates().screen_to_window((target[0] - .02, target[1] + .03)))
        self.assertNotEqual(args[0], kwargs['before_position'])

        self.enter_board(manager, now)
        button = manager.run.buttons[0]
        button_center = self.center(manager, button)
        for ms in range(1700, 2501, 50):
            self.tick(manager, now, ms, (button_center[0] - .02, button_center[1] + .03))
        self.assertEqual(manager.run.phase, 'feedback')
        self.assertEqual(manager.run.feedback_text, session.settings.demo.labels[0])
        self.assertEqual(manager.run.selections[-1]['target_id'], '0')
        self.assertEqual(manager.run.selections[-1]['stage'], 'application')
        self.assertEqual(manager.run.selections[-1]['dwell_ms'], 800.)
        session.recording.emit.assert_not_called()

    def test_repractice_replaces_correction_without_profile_or_matrix_changes(self):
        manager, session, overlay, panel, now = self.make()
        original = deepcopy(session.profiles.profile)
        manager.start('board', None)
        self.practice(manager, now)
        first_offset = manager.drift.offset
        self.enter_board(manager, now)
        now[0] = 2_000_000_000
        self.assertTrue(manager.repractice())
        self.assertEqual(manager.run.phase, 'practice')
        self.assertEqual(manager.drift.offset, first_offset)
        self.practice(manager, now, offset=(-.01, .01), start_ms=2000)
        self.assertEqual(manager.run.phase, 'feedback')
        np.testing.assert_allclose(manager.drift.offset, (-.01, .01))
        self.assertEqual(session.profiles.profile, original)
        self.assertEqual(session.display_revision, 3)
        session.controller.set_matrix.assert_not_called()
        session.recording.emit.assert_not_called()
        session.recording.manual.assert_not_called()

    def test_over_3cm_stays_in_practice_and_keeps_previous_correction(self):
        manager, session, overlay, panel, now = self.make()
        manager.start('board', None)
        self.practice(manager, now)
        old_offset = manager.drift.offset
        old_magnitude = manager.drift.magnitude_cm
        now[0] = 2_000_000_000
        manager.repractice()
        before_count = len(manager.run.selections)
        self.practice(manager, now, offset=(.11, 0), start_ms=2000)
        self.assertEqual(manager.run.phase, 'practice')
        self.assertEqual(len(manager.run.selections), before_count)
        self.assertEqual(manager.drift.status, 'rejected')
        self.assertGreater(manager.drift.last_result.magnitude_cm, 3)
        self.assertEqual(manager.drift.offset, old_offset)
        self.assertEqual(manager.drift.magnitude_cm, old_magnitude)
        self.assertIn('重新九点校准', manager.run.practice_message)

    def test_unmeasured_screen_continues_without_inventing_cm_or_offset(self):
        manager, session, overlay, panel, now = self.make(measured=False)
        manager.start('board', None)
        self.practice(manager, now)
        self.assertEqual(manager.run.phase, 'feedback')
        self.assertEqual(manager.drift.status, 'unmeasured')
        self.assertEqual(manager.drift.offset, (0., 0.))
        self.assertIsNone(manager.drift.magnitude_cm)
        self.assertIsNone(manager.drift.last_result.magnitude_cm)
        self.assertIn('未实测', panel.demo_status.setText.call_args.args[0])
        self.enter_board(manager, now)

    def test_cancel_clears_session_offset_and_does_not_write_anything(self):
        manager, session, overlay, panel, now = self.make()
        original = deepcopy(session.profiles.profile)
        with patch('builtins.open', side_effect=AssertionError('unexpected file access')), \
                patch.object(Path, 'write_text', side_effect=AssertionError('unexpected file write')):
            manager.start('board', None)
            self.practice(manager, now)
            window = manager.window
            now[0] = 2_000_000_000
            manager.cancel()
        self.assertFalse(manager.active)
        self.assertIsNone(manager.drift)
        self.assertIsNone(manager._mapping)
        window.dispose.assert_called_once()
        overlay.setVisible.assert_called_with(True)
        self.assertIn('偏移已清除', panel.demo_status.setText.call_args.args[0])
        self.assertEqual(session.profiles.profile, original)
        session.recording.emit.assert_not_called()
        session.recording.manual.assert_not_called()

        manager.start('board', None)
        self.assertEqual(manager.drift.offset, (0., 0.))
        self.assertFalse(manager.drift.has_correction)
        self.assertEqual(manager.drift.status, 'pending')

    def test_insufficient_samples_remain_pending_without_numeric_result(self):
        manager, session, overlay, panel, now = self.make(practice_min_samples=20)
        manager.start('board', None)
        self.practice(manager, now)
        self.assertEqual(manager.run.phase, 'practice')
        self.assertFalse(manager.run.selections)
        self.assertEqual(manager.drift.status, 'pending')
        self.assertEqual(manager.drift.offset, (0., 0.))
        self.assertIsNone(manager.drift.magnitude_cm)
        self.assertIsNone(manager.drift.last_result.magnitude_cm)
        self.assertIn('取样不足', manager.run.practice_message)
        session.recording.emit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
