"""Synthetic logical rectangles only; no camera or native full-screen changes."""
from types import SimpleNamespace
import unittest

import numpy as np

from gaze_core.demo_coordinates import DemoCoordinates
from overlay.mapping import ScreenRect


class DemoCoordinateTests(unittest.TestCase):
    def mapping(self, **kwargs):
        values = dict(window_origin=(-1512, 30), window_size=(1512, 982), fullscreen=True)
        values.update(kwargs)
        return DemoCoordinates(ScreenRect(-1512, 30, 1512, 982, 2), **values)

    def test_full_logical_screen_includes_menu_area_and_corners(self):
        mapping = self.mapping()
        self.assertTrue(mapping.ready)
        self.assertEqual(mapping.reason, '')
        for point, local in (((0, 0), (0., 0.)), ((1, 1), (1511., 981.)),
                             ((.5, .5), (755.5, 490.5))):
            self.assertEqual(mapping.hit_position(point), local)
            self.assertEqual(mapping.window_to_screen(local), point)
        self.assertEqual(mapping.screen_to_global((0, 0)), (-1512., 30.))
        self.assertEqual(mapping.screen_to_global((1, 1)), (-1., 1011.))

    def test_dpr_is_diagnostic_and_never_scales_twice(self):
        for scale in (1, 1.25, 1.5, 2):
            screen = ScreenRect(100, -982, 1512, 982, scale)
            mapping = DemoCoordinates(screen, window_origin=(100, -982),
                                      window_size=(1512, 982), fullscreen=True)
            self.assertEqual(mapping.hit_position((np.float64(.5), np.float64(.5))), (755.5, 490.5))
            self.assertEqual(mapping.dpr, scale)

    def test_actual_client_origin_is_subtracted_without_rescaling(self):
        mapping = self.mapping(window_origin=(-1502, 54), window_size=(1502, 958))
        self.assertEqual(mapping.screen_to_window((0, 0)), (-10., -24.))
        self.assertEqual(mapping.window_to_screen((-10, -24)), (0., 0.))
        self.assertFalse(mapping.ready)
        self.assertIsNone(mapping.hit_position((.5, .5)))
        self.assertIn('含菜单栏', mapping.reason)

    def test_transition_or_nonfullscreen_never_accepts_hits(self):
        for values, reason in ((dict(fullscreen=False), '尚未进入全屏'),
                               (dict(transitioning=True), '切换尚未完成'),
                               (dict(window_origin=None), '尚未获得'),
                               (dict(window_size=None), '尚未获得')):
            mapping = self.mapping(**values)
            self.assertFalse(mapping.ready)
            self.assertIsNone(mapping.hit_position((.5, .5)))
            self.assertIn(reason, mapping.reason)

    def test_client_geometry_must_match_whole_screen(self):
        for values in (dict(window_origin=(-1512, 62)), dict(window_size=(1512, 950)),
                       dict(window_size=(3024, 1964)), dict(window_origin=(0, 0))):
            mapping = self.mapping(**values)
            self.assertFalse(mapping.geometry_matches)
            self.assertIsNone(mapping.hit_position((0, 0)))

    def test_inverse_and_unclipped_coordinates_with_numpy(self):
        mapping = self.mapping()
        for point in (np.array([-.1, 1.2]), np.array([.33, .74])):
            local = mapping.screen_to_window(point)
            np.testing.assert_allclose(mapping.window_to_screen(local), point, rtol=0, atol=1e-14)
            global_point = mapping.window_to_global(local)
            np.testing.assert_allclose(mapping.global_to_window(global_point), local)
            np.testing.assert_allclose(mapping.global_to_screen(global_point), point)
        self.assertLess(mapping.hit_position((-.1, 1.2))[0], 0)
        self.assertGreater(mapping.hit_position((-.1, 1.2))[1], 981)
        self.assertIsNone(mapping.hit_position(None))

    def test_invalid_coordinates_and_rectangles(self):
        mapping = self.mapping()
        for point in ((True, 0), (np.inf, 0), (0, np.nan), (0,), None):
            with self.assertRaises(ValueError):
                mapping.screen_to_global(point)
        for values in (dict(window_size=(0, 982)), dict(window_origin=(0, True)),
                       dict(fullscreen=1), dict(transitioning=0), dict(tolerance_px=-1)):
            with self.assertRaises(ValueError):
                self.mapping(**values)
        with self.assertRaises(ValueError):
            DemoCoordinates(SimpleNamespace(x=0, y=0, width=1, height=982))


if __name__ == '__main__':
    unittest.main()
