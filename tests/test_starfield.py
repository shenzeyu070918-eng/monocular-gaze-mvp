"""Synthetic capture/animation clocks, no camera, renderer or performance claims."""
from dataclasses import replace
import unittest

import numpy as np

from gaze_core.starfield import StarfieldModel, StarfieldSettings


def ns(milliseconds):
    return int(round(milliseconds * 1_000_000))


class StarfieldTests(unittest.TestCase):
    def model(self, *, radii=(55., 40.), **options):
        result = StarfieldModel(1512, 982, radii, replace(StarfieldSettings(), **options), seed=41)
        result.presented(0)
        return result

    def sample(self, model, ms, point=(700., 450.), **kwargs):
        return model.ingest(point, ns(ms), ns(ms), **kwargs)

    def test_settings_defaults_and_all_numeric_validation(self):
        options = StarfieldSettings()
        self.assertEqual(options.particle_count, 2000)
        self.assertEqual(options.animation_fps, 60)
        self.assertEqual(options.round_s, 60)
        self.assertEqual(options.replay_s, 5)
        self.assertEqual(options.bloom_ms, 1500)
        self.assertEqual(options.cooldown_ms, 1000)
        self.assertEqual(options.constellation_count, 4)
        for name, value in (('round_s', float('nan')), ('spring_hz', float('inf')),
                            ('particle_count', True), ('bloom_ms', False),
                            ('animation_fps', 0), ('reveal_threshold', 1),
                            ('constellation_count', 2), ('mask_columns', 3),
                            ('firework_pool', 1990), ('firework_particles', 401),
                            ('min_star_px', 5), ('base_brightness', 2),
                            ('color_palette', ((256, 0, 0), (0, 0, 0)))):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                replace(options, **{name: value})

    def test_real_numpy_inputs_and_fixed_particle_pool(self):
        model = self.model(radii=np.array((np.float64(55), np.float32(40))))
        identity = id(model.positions)
        for ms in range(0, 10_001, 100):
            self.assertTrue(model.ingest(np.array((np.float64(700), np.float32(450))),
                                         np.int64(ns(ms)), np.int64(ns(ms))))
            model.update(ns(ms))
        self.assertGreater(model.firework_count, 1)
        self.assertEqual(id(model.positions), identity)
        snapshot = model.snapshot()
        self.assertEqual(snapshot['positions'].shape, (2000, 2))
        self.assertEqual(snapshot['colors'].shape, (2000, 3))
        np.testing.assert_array_equal(snapshot['colors'],
            snapshot['color_palette'][snapshot['color_indices']])
        self.assertTrue(np.isfinite(snapshot['positions']).all())
        self.assertTrue(((snapshot['brightness'] >= 0) & (snapshot['brightness'] <= 1)).all())

    def test_three_to_five_constellations_and_fifth_footprint_is_separate(self):
        for count in (3, 4, 5):
            model = self.model(constellation_count=count)
            self.assertEqual(len(model.snapshot()['constellations']), count)
            self.assertTrue(all(c['area'] > 0 for c in model.constellations))
        model = self.model(constellation_count=5)
        fifth = model.constellations[-1]
        self.assertEqual(fifth['name'], '仙女座')
        self.assertEqual(fifth['id'], 4)
        for earlier in model.constellations[:-1]:
            self.assertFalse(np.any(fifth['footprint'] & earlier['footprint']))
        with self.assertRaises(ValueError):
            replace(StarfieldSettings(), constellation_count=6)

    def test_evidence_begins_after_first_presentation(self):
        model = StarfieldModel(1512, 982, (50, 40), seed=0)
        self.assertFalse(self.sample(model, 0))
        model.presented(ns(100))
        self.assertFalse(model.ingest((700, 450), ns(50), ns(100)))
        self.assertTrue(self.sample(model, 100))
        self.assertEqual(len(model.snapshot()['trajectory']), 1)
        model.presented(ns(200))
        self.assertEqual(model.started_ns, ns(100))

    def test_exact_stable_window_then_full_cooldown_and_new_window(self):
        model = self.model()
        for ms in range(0, 1500, 100):
            self.sample(model, ms)
        self.assertEqual(model.firework_count, 0)
        self.sample(model, 1500)
        self.assertEqual(model.firework_count, 1)
        for ms in range(1600, 4000, 100):
            self.sample(model, ms)
        self.assertEqual(model.firework_count, 1)
        self.sample(model, 4000)
        self.assertEqual(model.firework_count, 2)
        blooms = [event for kind, event in model.drain_events()
                  if kind == 'starfield_event' and event['event_code'] == 1]
        self.assertEqual([event['event_ns'] for event in blooms], [ns(1500), ns(4000)])

    def test_stable_region_uses_anisotropic_error_radius(self):
        model = self.model(radii=(100., 20.), bloom_ms=200.)
        self.sample(model, 0, (700, 450))
        self.sample(model, 100, (780, 450))  # 80% horizontal radius.
        self.sample(model, 200, (700, 469))  # 95% vertical radius.
        self.assertEqual(model.firework_count, 1)
        other = self.model(radii=(100., 20.), bloom_ms=200.)
        self.sample(other, 0, (700, 450))
        self.sample(other, 100, (700, 471))  # 105%, starts a new anchor.
        self.sample(other, 200, (700, 471))
        self.assertEqual(other.firework_count, 0)
        self.sample(other, 300, (700, 471))
        self.assertEqual(other.firework_count, 1)

    def test_blink_breaks_stable_interval_and_does_not_illuminate(self):
        model = self.model(bloom_ms=300.)
        self.sample(model, 0)
        self.sample(model, 100)
        mask = model.mask.copy()
        self.sample(model, 200, None, valid=False)
        np.testing.assert_array_equal(model.mask, mask)
        self.sample(model, 300)
        self.sample(model, 400)
        self.sample(model, 500)
        self.assertEqual(model.firework_count, 0)
        self.sample(model, 600)
        self.assertEqual(model.firework_count, 1)

    def test_missing_callback_gap_does_not_bridge_stability(self):
        model = self.model(bloom_ms=300., max_capture_gap_ms=200.)
        self.sample(model, 0)
        self.sample(model, 100)
        self.sample(model, 500)
        self.sample(model, 600)
        self.sample(model, 700)
        self.assertEqual(model.firework_count, 0)
        self.sample(model, 800)
        self.assertEqual(model.firework_count, 1)

    def test_capture_timestamp_not_callback_spacing_controls_stable_dwell(self):
        model = self.model(bloom_ms=300., stale_ms=400.)
        model.ingest((700, 450), ns(0), ns(0))
        model.ingest((700, 450), ns(100), ns(350))
        model.ingest((700, 450), ns(200), ns(400))
        self.assertEqual(model.firework_count, 0)
        model.ingest((700, 450), ns(300), ns(500))
        self.assertEqual(model.firework_count, 1)

    def test_duplicate_old_stale_and_future_captures_cannot_add_evidence(self):
        model = self.model()
        self.sample(model, 100)
        before = model.mask.copy()
        for capture_ms, receive_ms in ((100, 200), (90, 210), (150, 500), (700, 600)):
            self.assertFalse(model.ingest((0, 0), ns(capture_ms), ns(receive_ms)))
        self.assertEqual(len(model.snapshot()['trajectory']), 1)
        self.assertEqual(model.firework_count, 0)
        np.testing.assert_array_equal(model.mask, before)

    def test_future_and_old_invalid_timestamps_cannot_block_normal_frames(self):
        for invalid in (True, False):
            with self.subTest(valid=invalid):
                model = self.model(bloom_ms=200.)
                self.sample(model, 0)
                self.assertFalse(model.ingest((0, 0), ns(1_000_000), ns(100), valid=invalid))
                self.assertTrue(self.sample(model, 200))
                # Old invalid frames are no newer evidence and do not reset the
                # current interval or move the ordering high-water mark back.
                self.assertFalse(model.ingest(None, ns(150), ns(250), valid=False))
                self.assertTrue(self.sample(model, 300))
                self.assertTrue(self.sample(model, 400))
                self.assertEqual(model.firework_count, 1)
                self.assertEqual(model._last_capture_ns, ns(400))

    def test_trajectory_uses_capture_time_not_callback_receipt_time(self):
        model = self.model(stale_ms=400.)
        model.ingest((700, 450), ns(100), ns(300))
        self.assertEqual(model.snapshot()['trajectory_times'].tolist(), [.1])

    def test_animation_and_spring_path_never_write_sensor_evidence(self):
        model = self.model(bloom_ms=100.)
        self.sample(model, 0, (100, 300))
        self.sample(model, 100, (1400, 300))
        mask = model.mask.copy()
        path = model.snapshot()['trajectory'].copy()
        for ms in range(110, 2001, 10):
            model.update(ns(ms))
        np.testing.assert_array_equal(model.mask, mask)
        np.testing.assert_array_equal(model.snapshot()['trajectory'], path)
        self.assertEqual(model.firework_count, 0)
        self.assertEqual(model.found_count, 0)

    def test_critical_spring_exact_for_uneven_render_intervals(self):
        left, right = self.model(), self.model()
        for model in (left, right):
            self.sample(model, 0, (100, 100))
            self.sample(model, 100, (600, 300))
        left.update(ns(200))
        previous_x = 100
        for ms in (110, 123, 160, 200):
            right.update(ns(ms))
            x, y = right.snapshot()['halo_position']
            self.assertGreaterEqual(x, previous_x)
            self.assertLessEqual(x, 600)
            self.assertTrue(100 <= y <= 300)
            previous_x = x
        np.testing.assert_allclose(left.halo_position, right.halo_position, atol=1e-10)

    def test_loss_fades_halo_and_recovery_does_not_credit_missing_time(self):
        model = self.model(bloom_ms=300.)
        self.sample(model, 0)
        self.sample(model, 100)
        model.update(ns(100))
        bright = model.halo_opacity
        self.assertGreater(bright, 0)
        self.sample(model, 200, None, valid=False)
        model.update(ns(400))
        self.assertLess(model.halo_opacity, bright)
        model.update(ns(1000))
        self.assertLess(model.halo_opacity, .02)
        self.sample(model, 1100)
        self.sample(model, 1200)
        self.assertEqual(model.firework_count, 0)

    def test_stale_held_position_fades_without_new_capture(self):
        model = self.model()
        self.sample(model, 0)
        model.update(ns(250))
        bright = model.halo_opacity
        model.update(ns(1000))
        self.assertLess(model.halo_opacity, bright)
        self.assertEqual(model.firework_count, 0)

    def test_illumination_halo_matches_one_and_half_error_ellipse(self):
        model = self.model(radii=(100, 30))
        self.sample(model, 0, (700, 450))
        self.assertEqual(tuple(model.halo_radii), (150, 45))
        expected = np.hypot((model._grid_x - 700) / 150, (model._grid_y - 450) / 45) <= 1
        np.testing.assert_array_equal(model.mask, expected)

    def test_fixed_footprint_is_stars_and_lines_not_bounding_rectangle(self):
        model = self.model()
        c = model.constellations[0]
        vertices = c['vertices']
        inside_box = ((model._grid_x >= vertices[:, 0].min()) &
                      (model._grid_x <= vertices[:, 0].max()) &
                      (model._grid_y >= vertices[:, 1].min()) &
                      (model._grid_y <= vertices[:, 1].max()))
        self.assertGreater(np.count_nonzero(inside_box & ~c['footprint']), 0)
        self.assertGreater(c['area'], 0)
        # Footprints are independent of changing measurement/error radius.
        other = self.model(radii=(5, 150))
        np.testing.assert_array_equal(c['footprint'], other.constellations[0]['footprint'])

    def test_reveal_strict_threshold_then_gradual_alpha_and_one_event(self):
        model = self.model(reveal_threshold=.7)
        constellation = model.constellations[0]
        indices = np.flatnonzero(constellation['footprint'])
        covered = int(np.floor(len(indices) * .7))
        model.mask.ravel()[indices[:covered]] = True
        self.sample(model, 0, (-10000, -10000))
        self.assertLessEqual(constellation['coverage'], .7)
        self.assertIsNone(constellation['revealed_ns'])
        model.mask.ravel()[indices[covered:covered + 1]] = True
        self.sample(model, 100, (-10000, -10000))
        self.assertGreater(constellation['coverage'], .7)
        self.assertEqual(constellation['reveal_alpha'], 0)
        model.update(ns(500))
        self.assertTrue(0 < constellation['reveal_alpha'] < 1)
        self.sample(model, 600, (-10000, -10000))
        model.update(ns(1500))
        self.assertEqual(constellation['reveal_alpha'], 1)
        events = [e for k, e in model.drain_events() if k == 'starfield_event' and e['event_code'] == 2]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['constellation_id'], 0)

    def test_round_then_five_second_chronological_replay_no_new_evidence(self):
        model = self.model()
        for second in (10, 20, 30, 40, 50):
            self.sample(model, second * 1000, (second * 10, 450))
        model.update(ns(60_000))
        self.assertEqual(model.phase, 'replay')
        self.assertEqual(model.snapshot()['remaining_s'], 0)
        count = len(model.snapshot()['trajectory'])
        self.assertFalse(self.sample(model, 61_000, (0, 0)))
        model.update(ns(62_500))
        snapshot = model.snapshot()
        self.assertEqual(snapshot['replay_progress'], .5)
        self.assertEqual(len(snapshot['replay_trajectory']), 3)
        model.update(ns(65_000))
        self.assertEqual(model.phase, 'finished')
        self.assertEqual(len(model.snapshot()['trajectory']), count)
        events = model.drain_events()
        summaries = [e for k, e in events if k == 'starfield_summary']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['completed'], 1)
        self.assertEqual(summaries[0]['elapsed_ms'], 60_000)
        before = model.snapshot()
        model.update(ns(90_000))
        after = model.snapshot()
        np.testing.assert_array_equal(before['positions'], after['positions'])
        self.assertEqual(before['elapsed_s'], after['elapsed_s'])
        self.assertEqual(model.drain_events(), [])

    def test_repractice_pause_excludes_time_and_rejects_pre_resume_frame(self):
        model = self.model(bloom_ms=300.)
        self.sample(model, 0)
        self.sample(model, 100)
        model.pause(ns(200))
        before = model.snapshot()
        model.update(ns(10_200))
        self.assertFalse(self.sample(model, 10_300))
        self.assertEqual(model.snapshot()['elapsed_s'], .2)
        np.testing.assert_array_equal(before['positions'], model.snapshot()['positions'])
        model.resume(ns(10_400))
        self.assertFalse(model.ingest((700, 450), ns(10_300), ns(10_400)))
        self.sample(model, 10_400)
        self.sample(model, 10_500)
        self.sample(model, 10_600)
        self.assertEqual(model.firework_count, 0)
        self.sample(model, 10_700)
        self.assertEqual(model.firework_count, 1)
        self.assertAlmostEqual(model.snapshot()['elapsed_s'], .5)

    def test_replay_pause_preserves_progress_and_remaining_five_second_clock(self):
        model = self.model()
        for second in (10, 20, 30, 40, 50):
            self.sample(model, second * 1000)
        model.update(ns(61_000))
        self.assertEqual(model.phase, 'replay')
        self.assertAlmostEqual(model.snapshot()['replay_progress'], .2)
        model.pause(ns(61_000))
        visible = model.snapshot()['replay_trajectory'].copy()
        model.update(ns(99_000))
        self.assertEqual(model.phase, 'replay')
        self.assertAlmostEqual(model.snapshot()['replay_progress'], .2)
        np.testing.assert_array_equal(model.snapshot()['replay_trajectory'], visible)
        model.resume(ns(100_000))
        model.update(ns(103_000))
        self.assertEqual(model.phase, 'replay')
        self.assertAlmostEqual(model.snapshot()['replay_progress'], .8)
        model.update(ns(104_000))
        self.assertEqual(model.phase, 'finished')
        self.assertEqual(model.snapshot()['replay_progress'], 1)

    def test_seed_repeatability_and_detached_readonly_snapshots(self):
        left, right = self.model(), self.model()
        for model in (left, right):
            for ms in range(0, 2001, 100):
                self.sample(model, ms)
                model.update(ns(ms))
        a, b = left.snapshot(), right.snapshot()
        for key in ('positions', 'brightness', 'sizes', 'colors', 'trajectory', 'color_indices'):
            np.testing.assert_array_equal(a[key], b[key])
            self.assertFalse(a[key].flags.writeable)
        self.assertFalse(a['constellations'][0]['vertices'].flags.writeable)
        # A snapshot cannot mutate its owner's internal pool, even if a caller
        # deliberately changes its own local array's write flag.
        a['positions'].flags.writeable = True
        a['positions'][0] = 12345
        self.assertFalse(np.array_equal(a['positions'], left.positions))

    def test_invalid_positions_do_not_get_into_trajectory(self):
        model = self.model()
        for index, point in enumerate(((True, 0), (np.nan, 0), (0, np.inf), None, (1,))):
            self.assertFalse(self.sample(model, index * 100, point))
        self.assertEqual(len(model.snapshot()['trajectory']), 0)
        self.assertEqual(model.mask.sum(), 0)

    def test_numeric_recording_only_and_summary_once_on_close(self):
        model = self.model(bloom_ms=200.)
        for ms in (0, 100, 200):
            self.sample(model, ms)
        model.close(ns(250), 'escape')
        events = model.drain_events()
        self.assertEqual(events[-1][0], 'starfield_summary')
        self.assertEqual(events[-1][1]['reason_code'], 1)
        self.assertEqual(events[-1][1]['completed'], 0)
        for kind, event in events:
            self.assertNotIn('trajectory', event)
            self.assertNotIn('position', event)
            self.assertNotIn('image', event)
            for value in event.values():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    self.assertIsInstance(item, (int, float))
                    self.assertTrue(np.isfinite(item))
        model.close(ns(300), 'escape')
        self.assertEqual(model.drain_events(), [])

    def test_invalid_dimensions_radii_and_clock_rejected(self):
        for width, height, radii in ((0, 100, (1, 1)), (100, 1, (1, 1)),
                                     (100, 100, (0, 1)), (100, 100, (1, np.inf))):
            with self.assertRaises(ValueError):
                StarfieldModel(width, height, radii)
        model = self.model()
        model.update(ns(100))
        with self.assertRaisesRegex(ValueError, '倒退'):
            model.update(ns(50))
        with self.assertRaises(ValueError):
            model.ingest((0, 0), -1, ns(150))


if __name__ == '__main__':
    unittest.main()
