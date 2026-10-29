"""Practice drift tests use numeric synthetic points, never stored profiles."""
from dataclasses import replace
import unittest

import numpy as np

from gaze_core.demo_drift import DemoDrift, DriftSettings


class DemoDriftTests(unittest.TestCase):
    def test_delivery_delay_does_not_shorten_capture_window(self):
        drift=DemoDrift(width_cm=30,height_cm=20,measured=True)
        for ms in range(0,801,50):
            drift.observe((.48,.47),ms*1_000_000,now_ns=(ms+200)*1_000_000)
        result=drift.estimate((.5,.5),800_000_000)
        self.assertTrue(result.applied)
        self.assertEqual(result.span_ms,600.)
        self.assertEqual(result.sample_count,13)
    def make(self, **kwargs):
        values = dict(width_cm=30., height_cm=20., measured=True)
        values.update(kwargs)
        return DemoDrift(**values)

    def collect(self, drift, point=(.48, .53), start_ms=0):
        for ms in range(start_ms, start_ms + 601, 100):
            self.assertTrue(drift.observe(np.asarray(point, dtype=np.float64), np.int64(ms * 1_000_000)))
        return (start_ms + 600) * 1_000_000

    def test_offset_sign_numpy_median_and_no_clipping(self):
        drift = self.make()
        end = self.collect(drift)
        result = drift.estimate(np.array([.5, .5]), np.int64(end))
        self.assertEqual(result.status, 'applied')
        self.assertTrue(result.applied)
        np.testing.assert_allclose(drift.offset, (.02, -.03))
        np.testing.assert_allclose(drift.apply((.48, .53)), (.5, .5))
        self.assertAlmostEqual(result.magnitude_cm, np.hypot(.6, -.6))
        self.assertEqual(result.sample_count, 7)
        self.assertEqual(result.span_ms, 600.)
        self.assertTrue(drift.has_correction)
        self.assertLess(drift.apply((-.5, .5))[0], 0)
        self.assertIsNone(drift.apply(None))

    def test_three_cm_boundary_accepts_but_greater_rejects(self):
        for point, accepted in (((.4, .5), True), ((.5, .35), True),
                                ((.3999, .5), False), ((.5, .3499), False)):
            drift = self.make()
            end = self.collect(drift, point)
            result = drift.estimate((.5, .5), end)
            self.assertEqual(result.applied, accepted)
            if accepted:
                self.assertAlmostEqual(result.magnitude_cm, 3.)
            else:
                self.assertEqual(result.status, 'rejected')
                self.assertGreater(result.magnitude_cm, 3.)
                self.assertIn('按 C 直接开始九点校准', result.reason)
                self.assertEqual(drift.offset, (0., 0.))
                self.assertFalse(drift.has_correction)

    def test_no_centimeter_estimate_without_measured_geometry(self):
        for dims in (dict(width_cm=None, height_cm=None), dict(width_cm=30, height_cm=20)):
            drift = self.make(measured=False, **dims)
            end = self.collect(drift)
            result = drift.estimate((.5, .5), end)
            self.assertEqual(result.status, 'unmeasured')
            self.assertIsNone(result.magnitude_cm)
            self.assertIsNone(drift.magnitude_cm)
            self.assertEqual(drift.offset, (0., 0.))
            self.assertFalse(drift.has_correction)
            self.assertIn('未实测', result.reason)

    def test_last_six_hundred_ms_only_and_robust_median(self):
        drift = self.make()
        self.collect(drift, (.1, .1))
        end = self.collect(drift, (.48, .53), start_ms=700)
        self.assertTrue(drift.observe((.1, .9), 1_350_000_000))
        result = drift.estimate((.5, .5), 1_350_000_000)
        self.assertEqual(result.sample_count, 7)
        self.assertEqual(result.span_ms, 550)
        np.testing.assert_allclose(result.median, (.48, .53))
        np.testing.assert_allclose(result.offset, (.02, -.03))

    def test_minimum_samples_and_span_are_both_required(self):
        drift = self.make()
        for ms in (0, 100, 200, 600):
            drift.observe((.48, .53), ms * 1_000_000)
        result = drift.estimate((.5, .5), 600_000_000)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.sample_count, 4)
        drift.restart()
        for ms in range(0, 401, 100):
            drift.observe((.48, .53), ms * 1_000_000)
        self.assertEqual(drift.estimate((.5, .5), 400_000_000).status, 'pending')
        drift.observe((.48, .53), 500_000_000)
        self.assertTrue(drift.estimate((.5, .5), 500_000_000).applied)

    def test_discrete_fifteen_hz_window_has_enough_span(self):
        drift = self.make()
        for frame in range(13):
            drift.observe((.48, .53), round(frame * 1_000_000_000 / 15))
        result = drift.estimate((.5, .5), 800_000_000)
        self.assertTrue(result.applied)
        self.assertGreaterEqual(result.span_ms, 500)
        self.assertLessEqual(result.span_ms, 600)

    def test_duplicate_backward_future_invalid_and_expired_samples(self):
        drift = self.make()
        self.assertTrue(drift.observe((.48, .53), 100_000_000))
        for stamp in (100_000_000, 50_000_000):
            self.assertFalse(drift.observe((.1, .1), stamp))
        self.assertFalse(drift.observe((.1, .1), 200_000_000, now_ns=150_000_000))
        self.assertFalse(drift.observe((.1, .1), 200_000_000, now_ns=900_000_000))
        self.assertFalse(drift.observe((np.nan, .5), 300_000_000))
        self.assertFalse(drift.observe((.5, np.inf), 400_000_000))
        self.assertFalse(drift.observe((True, .5), 500_000_000))
        self.assertFalse(drift.observe(None, 600_000_000))
        self.assertFalse(drift.observe((.5, .5), 700_000_000, valid=False))
        result = drift.estimate((.5, .5), 1_400_000_000)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.sample_count, 0)
        for stamp in (-1, True, 1.2):
            with self.assertRaises(ValueError):
                drift.observe((.5, .5), stamp)

    def test_repractice_replaces_translation_and_failure_keeps_previous(self):
        drift = self.make()
        end = self.collect(drift)
        first = drift.estimate((.5, .5), end)
        drift.restart()
        self.assertEqual(drift.offset, first.offset)
        end = self.collect(drift, (.49, .51), start_ms=1000)
        second = drift.estimate((.5, .5), end)
        np.testing.assert_allclose(second.offset, (.01, -.01))
        np.testing.assert_allclose(drift.apply((.49, .51)), (.5, .5))
        old_magnitude = drift.magnitude_cm
        drift.restart()
        end = self.collect(drift, (.1, .1), start_ms=2000)
        rejected = drift.estimate((.5, .5), end)
        self.assertEqual(rejected.status, 'rejected')
        self.assertEqual(drift.offset, second.offset)
        self.assertEqual(drift.magnitude_cm, old_magnitude)
        self.assertTrue(drift.has_correction)

    def test_estimate_before_latest_sample_does_not_include_future(self):
        drift = self.make()
        self.collect(drift)
        result = drift.estimate((.5, .5), 400_000_000)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.sample_count, 5)
        self.assertEqual(result.span_ms, 400)

    def test_settings_are_configurable_and_validated(self):
        drift = self.make(settings=DriftSettings(window_ms=300, min_samples=3, min_span_ms=200, max_cm=1))
        for ms in (0, 100, 200):
            drift.observe((.48, .53), ms * 1_000_000)
        self.assertTrue(drift.estimate((.5, .5), 200_000_000).applied)
        for values in (dict(window_ms=0), dict(min_samples=True), dict(min_samples=1),
                       dict(min_span_ms=700), dict(max_cm=np.nan), dict(max_cm=-1)):
            with self.assertRaises(ValueError):
                replace(DriftSettings(), **values)
        with self.assertRaises(ValueError):
            DemoDrift(width_cm=None, height_cm=None, measured=True)


if __name__ == '__main__':
    unittest.main()
