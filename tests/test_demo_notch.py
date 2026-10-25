"""Notched-screen client coordinates with synthetic gaze, no native input/I/O."""
from dataclasses import replace
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


class NotchWindow:
    def __init__(self, run, screen, options, *, clock_ns, screen_rect, debug):
        self.run, self.screen_rect = run, screen_rect
        self.origin = (screen_rect.x, screen_rect.y + 32)
        self.size = (1512, 950)
        self.fullscreen = True
        self.coordinate_notice = ''
        self.cancelled = Mock()
        self.open = Mock()
        self.dispose = Mock()
        self.refresh = Mock()

    def coordinates(self):
        return DemoCoordinates(self.screen_rect, window_origin=self.origin,
                               window_size=self.size, fullscreen=self.fullscreen)


class NotchWorker:
    """Only records viewport ownership; never starts a thread or Qt object."""
    def __init__(self, width, height, radii, settings, **kwargs):
        self.width, self.height = width, height
        self.closed = False
        self.error = None
        self.start = Mock()
        self.ingest = Mock()
        self.presented = Mock()
        self.pause = Mock()
        self.resume = Mock()

    def latest(self):
        return {'phase': 'ready'}

    def drain_events(self):
        return []

    def close(self, reason='escape', now_ns=0):
        self.closed = True


class DemoNotchTests(unittest.TestCase):
    def make(self, *, practice=True, screen_origin=(0, 0)):
        settings = load_settings(use_local=False)
        settings = replace(settings,
                           geometry=replace(settings.geometry, width_cm=30., height_cm=20., measured=True),
                           demo=replace(settings.demo, practice=practice, dwell_ms=800))
        session = SimpleNamespace(settings=settings, active=False, clicks=None,
                                  display_revision=1, _active_group='B', radius=2.,
                                  profiles=SimpleNamespace(busy=False, conditions={'camera': 1},
                                                           profile={'pipeline': 'python'}),
                                  controller=SimpleNamespace(snapshot=lambda: SimpleNamespace(status='running')),
                                  set_demo_active=Mock(), recording=Mock())
        session.recording.status.return_value = {'active': True, 'session_id': 'notch-numeric'}
        overlay = Mock()
        overlay.rect_info = ScreenRect(*screen_origin, 1512, 982, 2)
        overlay.isVisible.return_value = True
        panel = Mock()
        panel.isVisible.return_value = False
        now = [0]
        manager = DemoSession(session, overlay, panel, notice=Mock(), window_factory=NotchWindow,
                              speech_factory=Mock(side_effect=RuntimeError('no real speech')),
                              clock_ns=lambda: now[0], debug=True)
        manager._test_sequence = 0
        self.addCleanup(lambda: manager.cancel('window_closed') if manager.active else None)
        return manager, session, panel, now

    def layout(self, manager, now):
        manager.tick(None, RingView(None, 0., 'tracking', False), now[0],
                     active=True, eligible=False)
        manager.run.presented(now[0])

    def center(self, manager, button=None):
        button = manager.run.buttons[0] if button is None else button
        x, y, width, height = button.rect
        local = (x + width / 2, y + height / 2)
        return local, manager.window.coordinates().window_to_screen(local)

    def frame(self, manager, now, milliseconds, point, *, valid=True):
        now[0] = int(milliseconds * 1_000_000)
        data = {'raw': np.asarray([.3, .6]), 'origin_m': np.asarray([0., 0., .5])}
        prediction = Prediction(manager._test_sequence, now[0], now[0] + 50_000_000, data)
        manager._test_sequence += 1
        manager.tick(prediction, RingView(point, .65, 'tracking', valid), now[0],
                     active=True, eligible=True)
        np.testing.assert_array_equal(prediction.data['raw'], [.3, .6])

    def test_notch_client_ready_and_global_local_roundtrip_without_dpr(self):
        for screen_origin in ((0, 0), (-1512, 40)):
            screen = ScreenRect(*screen_origin, 1512, 982, 2)
            mapping = DemoCoordinates(screen, window_origin=(screen.x, screen.y + 32),
                                      window_size=(1512, 950), fullscreen=True)
            self.assertTrue(mapping.ready)
            self.assertEqual(mapping.hit_position((.5, .5)), (755.5, 458.5))
            self.assertEqual(mapping.screen_to_window((0, 0)), (0., -32.))
            np.testing.assert_allclose(mapping.window_to_screen((755.5, 475.)),
                                       (.5, 507. / 981.))
            np.testing.assert_allclose(mapping.window_to_screen(mapping.hit_position((.3, .7))),
                                       (.3, .7))

    def test_board_hit_and_numeric_target_use_full_screen_coordinates(self):
        manager, session, _, now = self.make(practice=False)
        manager.start('board', None)
        self.layout(manager, now)
        self.assertEqual((manager.run.width, manager.run.height), (1512, 950))
        local, normalized = self.center(manager)
        for milliseconds in range(0, 801, 50):
            self.frame(manager, now, milliseconds, normalized)
        self.assertEqual(manager.run.phase, 'feedback')
        self.assertEqual(manager.run.selections[-1]['target_id'], '0')
        np.testing.assert_allclose(manager.run.selections[-1]['target'], normalized)
        triggered = [call.kwargs for call in session.recording.emit.call_args_list
                     if call.args[0] == 'demo_selection' and call.kwargs.get('action') == 'triggered']
        self.assertEqual(len(triggered), 1)
        np.testing.assert_allclose(triggered[0]['target'],
                                   (local[0] / 1511., (local[1] + 32.) / 981.))
        self.assertNotAlmostEqual(triggered[0]['target'][1], local[1] / 949.)

    def test_practice_median_translation_uses_actual_target_global_position(self):
        manager, session, _, now = self.make(screen_origin=(-1512, 40))
        manager.start('board', None)
        self.layout(manager, now)
        local, target = self.center(manager)
        offset = np.asarray((.01, -.02))
        for milliseconds in range(0, 801, 50):
            self.frame(manager, now, milliseconds, np.asarray(target) - offset)
        self.assertEqual(manager.run.phase, 'feedback')
        self.assertTrue(manager.drift.has_correction)
        np.testing.assert_allclose(manager.drift.offset, offset)
        self.assertAlmostEqual(manager.drift.magnitude_cm, .5)
        np.testing.assert_allclose(manager.window.refresh.call_args.args[0], local)
        np.testing.assert_allclose(manager.run.selections[-1]['target'], target)
        self.assertEqual(session.profiles.profile, {'pipeline': 'python'})

    def test_no_drift_is_invented_for_exact_notch_practice_center(self):
        manager, _, _, now = self.make()
        manager.start('board', None)
        self.layout(manager, now)
        _, target = self.center(manager)
        for milliseconds in range(0, 801, 50):
            self.frame(manager, now, milliseconds, np.asarray(target))
        self.assertEqual(manager.run.phase, 'feedback')
        np.testing.assert_allclose(manager.drift.offset, (0., 0.), atol=1e-15)
        self.assertAlmostEqual(manager.drift.magnitude_cm, 0.)

    def test_star_worker_uses_actual_visible_viewport(self):
        manager, _, _, now = self.make()
        with patch('overlay.starfield.StarfieldWorker', NotchWorker):
            manager.start('stars', None)
            original = manager.run.worker
            self.layout(manager, now)
            self.assertEqual((manager.run.width, manager.run.height), (1512, 950))
            self.assertEqual((manager.run.worker.width, manager.run.worker.height), (1512, 950))
            self.assertTrue(original.closed)
            for button in manager.run.buttons:
                left, top, width, height = button.rect
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(left + width, 1512)
                self.assertLessEqual(top + height, 950)

    def test_unready_geometry_remains_invalid_and_explains_after_one_second(self):
        manager, _, _, now = self.make()
        manager.start('board', None)
        manager.window.origin = (0, 64)  # Bottom now extends beyond the built-in screen.
        self.assertFalse(manager.window.coordinates().ready)
        manager.run.presented(0)
        for milliseconds in range(0, 1101, 50):
            self.frame(manager, now, milliseconds, (.5, .5))
        self.assertEqual(manager.run.phase, 'practice')
        self.assertFalse(manager.run.selections)
        self.assertFalse(manager.drift.has_correction)
        self.assertTrue(manager.window.coordinate_notice)
        self.assertIn(manager.window.coordinates().reason, manager.window.coordinate_notice)


if __name__ == '__main__':
    unittest.main()
