"""Synthetic raw predictions and fake clocks only; no camera or model."""
from dataclasses import replace
import random
import unittest
from unittest.mock import patch

import numpy as np

from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from gaze_core.evaluation import evaluate, evaluation_targets
from overlay.tasks import TaskRunner


class OverlayTaskTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(use_local=False)
        self.settings = replace(self.settings,
                                geometry=replace(self.settings.geometry, width_cm=30., height_cm=20., measured=True))
        self.now = [1_000_000_000]
        self.sequence = 0

    def runner(self, kind='calibrate', **kwargs):
        return TaskRunner(kind, self.settings, clock_ns=lambda: self.now[0], rng=random.Random(3), **kwargs)

    def prediction(self, timestamp, raw=None, **fields):
        self.sequence += 1
        data = dict(valid=raw is not None, raw=raw, origin_m=[0., .1, .5], distance_cm=np.float64(48.))
        data.update(fields)
        return Prediction(self.sequence, timestamp, timestamp+100_000_000, data)

    def start_target(self, runner):
        self.assertEqual(runner.state, 'presenting')
        runner.presented(self.now[0])
        self.assertEqual(runner.state, 'sampling')
        return self.now[0]

    def fill_target(self, runner, *, offset=(0., 0.), distance=48.):
        start = self.start_target(runner)
        target = runner.current_target['target']
        for i in range(8):
            capture = start+650_000_000+i*100_000_000
            self.now[0] = capture+100_000_000
            # This display result must never enter calibration/check samples.
            prediction = self.prediction(capture, np.array(target)+offset,
                                         display={'position': [123., 456.]}, distance_cm=distance)
            runner.ingest(prediction)
        self.now[0] = start+2_210_000_000
        runner.advance()

    def finish_watermark(self, runner):
        self.assertEqual(runner.state, 'finishing')
        self.now[0] += 30_000_000
        # Invalid frames also prove all earlier results have been consumed.
        runner.ingest(self.prediction(self.now[0]))
        self.assertEqual(runner.state, 'completed')

    def test_calibration_raw_numpy_equal_target_medians_and_distance(self):
        runner = self.runner(calibration_id='old')
        runner.begin()
        self.assertNotEqual(runner.calibration_id, 'old')
        for _ in runner.targets:
            self.fill_target(runner, offset=(.1, -.2))
        self.finish_watermark(runner)
        np.testing.assert_allclose(runner.result['matrix'], [[1., 0.], [0., 1.], [-.1, .2]], atol=1e-12)
        self.assertEqual(runner.result['estimated_distance_cm_median'], 48.)
        self.assertEqual(runner.result['estimated_distance_sample_count'], 72)
        self.assertEqual(runner.result['sample_count'], 72)
        events = runner.drain_events()
        self.assertEqual([e['action'] for e in events], ['appear']+['switch']*8+['end'])
        self.assertTrue(all(isinstance(e['event_ns'], int) for e in events))
        self.assertEqual(runner.drain_events(), [])

    def test_evaluation_uses_existing_summary_and_never_fits_or_clips(self):
        matrix = [[1., 0.], [0., 1.], [-.1, .2]]
        runner = self.runner('evaluate', matrix=matrix, calibration_id='existing')
        runner.begin()
        raw_frames = []
        with patch('overlay.tasks.fit_affine', side_effect=AssertionError('核对不得拟合')):
            for target in runner.targets:
                raw = [target['target'][0]+.1, target['target'][1]-.2]
                raw_frames += [dict(target_id=target['target_id'], valid=True, raw=raw,
                                    origin_m=[0., .1, .5])]*8
                self.fill_target(runner, offset=(.1, -.2))
            self.finish_watermark(runner)
        self.assertEqual(runner.result['summary'], evaluate(raw_frames, runner.targets, self.settings.geometry, matrix))
        self.assertEqual(runner.result['calibration_id'], 'existing')
        self.assertTrue(any(r['raw'][1] < 0 for r in raw_frames))

    def test_evaluation_selection_exactly_replays_valid_and_invalid_raw_frames(self):
        runner = self.runner('evaluate', matrix=[[1.,0.],[0.,1.],[-.1,.2]])
        runner.begin()
        captured = {}
        for index, point in enumerate(runner.targets):
            start = self.start_target(runner)
            # Settling and final watermark frames are not part of the test set.
            settling = self.prediction(start+100_000_000, [.5,.5])
            runner.ingest(settling)
            for offset, raw in [(650_000_000, None),
                                (750_000_000, [point['target'][0]+.1, point['target'][1]-.2])]:
                prediction = self.prediction(start+offset, raw)
                captured[prediction.sequence] = prediction
                runner.ingest(prediction)
            self.now[0] = start+2_210_000_000
            runner.advance()
        self.finish_watermark(runner)
        result = runner.result
        self.assertEqual(result['sampling'], dict(settle_ms=650, dwell_ms=2200, max_dwell_ms=10000))
        self.assertEqual(len(result['sample_selection']), len(runner.targets)*2)
        restored = []
        for selected in result['sample_selection']:
            prediction = captured[selected['frame_id']]
            restored.append(dict(target_id=selected['target_id'], valid=prediction.data['valid'],
                                 raw=prediction.data['raw'], origin_m=prediction.data['origin_m']))
        self.assertTrue(any(not frame['valid'] for frame in restored))
        self.assertEqual(result['summary'], evaluate(restored, runner.targets, self.settings.geometry, runner.matrix))

    def test_capture_window_boundaries_dedup_and_late_result_old_target(self):
        runner = self.runner('evaluate')
        runner.begin()
        start = self.start_target(runner)
        first = runner.current_target
        runner.ingest(self.prediction(start+649_999_999, [.1, .2]))
        included = self.prediction(start+650_000_000, [.3, .4])
        self.assertTrue(runner.ingest(included))
        self.assertFalse(runner.ingest(included))
        runner.ingest(self.prediction(start+2_200_000_000, [.5, .6]))
        self.now[0] = start+2_210_000_000
        runner.advance()
        runner.presented(self.now[0])
        # Late response was captured at the prior point, beyond its stable window.
        runner.ingest(self.prediction(start+2_205_000_000, [.7, .8]))
        self.assertEqual(len(runner._frames), 2)
        self.assertTrue(all(r['target_id'] == first['target_id'] for r in runner._frames))
        second_start = self.now[0]
        runner.ingest(self.prediction(second_start+650_000_000, [.9, 1.1]))
        self.assertEqual(runner._frames[-1]['target_id'], runner.current_target['target_id'])
        # Repaint does not reset the target's timing or emit another switch.
        self.assertFalse(runner.presented(second_start+700_000_000))
        self.assertEqual(len(runner.drain_events()), 2)

    def test_late_valid_calibration_frame_is_assigned_after_next_point_paints(self):
        runner = self.runner()
        runner.begin()
        start = self.start_target(runner)
        for i in range(8):
            runner.ingest(self.prediction(start+650_000_000+i*100_000_000, [.1, .2]))
        self.now[0] = start+2_210_000_000
        runner.advance()
        runner.presented(self.now[0])
        runner.ingest(self.prediction(start+2_205_000_000, [2., -3.]))
        self.assertEqual(len(runner._windows[0]['frames']), 9)
        self.assertEqual(runner._windows[0]['frames'][-1]['raw'], [2., -3.])
        self.assertEqual(len(runner._windows[1]['frames']), 0)

    def test_calibration_insufficient_samples_extends_then_cancels_without_result(self):
        runner = self.runner()
        runner.begin()
        start = self.start_target(runner)
        self.now[0] = start+2_210_000_000
        runner.advance()
        self.assertEqual(runner.state, 'sampling')
        self.now[0] = start+10_010_000_000
        runner.advance()
        self.assertEqual(runner.reason, 'insufficient_samples')
        self.assertIsNone(runner.result)

    def test_finishing_cancel_emits_correct_reason_at_actual_target_cutoff(self):
        runner = self.runner()
        runner.begin()
        for _ in runner.targets:
            self.fill_target(runner)
        cutoff = self.now[0]
        self.assertFalse(any(e['action'] == 'end' for e in runner.drain_events()))
        self.now[0] += 300_000_000
        runner.cancel('cancelled')
        events = runner.drain_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['reason'], 'cancelled')
        self.assertEqual(events[0]['event_ns'], cutoff)

    def test_cancel_at_each_phase_never_returns_partial_matrix(self):
        for phase in ('presenting', 'sampling', 'finishing'):
            runner = self.runner()
            runner.begin()
            if phase == 'sampling':
                self.start_target(runner)
            elif phase == 'finishing':
                for _ in runner.targets:
                    self.fill_target(runner)
            self.assertTrue(runner.cancel())
            self.assertEqual(runner.state, 'cancelled')
            self.assertIsNone(runner.result)
            self.assertFalse(runner.cancel())
            self.assertFalse(runner.ingest(self.prediction(self.now[0]+1, [.5, .5])))

    def test_first_paint_and_finishing_timeout_do_not_hang(self):
        runner = self.runner()
        runner.begin()
        self.now[0] += 10_010_000_000
        runner.advance()
        self.assertEqual(runner.reason, 'target_not_presented')
        runner = self.runner()
        runner.begin()
        for _ in runner.targets:
            self.fill_target(runner)
        self.now[0] += 10_010_000_000
        runner.advance()
        self.assertEqual(runner.reason, 'prediction_timeout')
        self.assertIsNone(runner.result)

    def test_finishing_waits_for_late_stable_result_before_summary(self):
        runner = self.runner('evaluate')
        runner.begin()
        for _ in runner.targets[:-1]:
            self.fill_target(runner)
        start = self.start_target(runner)
        self.now[0] = start+2_210_000_000
        runner.advance()
        self.assertIsNone(runner.result)
        runner.ingest(self.prediction(start+1_000_000_000, [2., -2.]))
        self.assertIsNone(runner.result)
        self.finish_watermark(runner)
        last = next(p for p in runner.result['summary']['groups']['A']['points']
                    if p['target_id'] == runner.targets[-1]['target_id'])
        self.assertEqual(last['prediction'], [2., -2.])

    def test_degenerate_calibration_is_cancelled_without_matrix(self):
        runner = self.runner()
        runner.begin()
        for _ in runner.targets:
            start = self.start_target(runner)
            for i in range(8):
                runner.ingest(self.prediction(start+650_000_000+i*100_000_000, [.5, .5]))
            self.now[0] = start+2_210_000_000
            runner.advance()
        runner.ingest(self.prediction(self.now[0]+30_000_000))
        self.assertEqual(runner.state, 'cancelled')
        self.assertEqual(runner.reason, 'calibration_failed')
        self.assertIn('视线变化过小', runner.error)
        self.assertIsNone(runner.result)
        ends = [e for e in runner.drain_events() if e['action'] == 'end']
        self.assertEqual([e['reason'] for e in ends], ['calibration_failed'])

    def test_valid_sample_count_does_not_depend_on_distance_availability(self):
        runner = self.runner('evaluate')
        runner.begin()
        for _ in runner.targets:
            self.fill_target(runner, distance=None)
        self.finish_watermark(runner)
        self.assertEqual(runner.result['sample_count'], 14*8)
        self.assertEqual(runner.result['estimated_distance_sample_count'], 0)
        self.assertIsNone(runner.result['estimated_distance_cm_median'])


class EvaluationTargetTests(unittest.TestCase):
    def test_points_match_web_config_and_margin_anchors(self):
        settings = load_settings(use_local=False)
        points = evaluation_targets(settings.evaluation, np.float64(.05), rng=random.Random(1))
        self.assertEqual(len(points), 14)
        expected_edges = {(.05, .05), (.95, .05), (.95, .95), (.05, .95),
                          (.5, .05), (.95, .5), (.5, .95), (.05, .5)}
        self.assertEqual({tuple(p['target']) for p in points if p['region'] == 'edge'}, expected_edges)
        self.assertEqual({tuple(p['target']) for p in points if p['region'] == 'center'},
                         {tuple(p) for p in settings.evaluation.center_points})
        self.assertTrue(all(tuple(p['target']) not in {tuple(c) for c in settings.evaluation.calibration_points}
                            for p in points))
        other = evaluation_targets(settings.evaluation, .05, rng=random.Random(2))
        self.assertNotEqual(points, other)

    def test_invalid_margin_and_overlapping_points_rejected(self):
        settings = load_settings(use_local=False).evaluation
        for margin in (0., .5, float('nan'), True):
            with self.assertRaises(ValueError):
                evaluation_targets(settings, margin)
        overlap = replace(settings, calibration_points=((.05, .05),))
        with self.assertRaisesRegex(ValueError, '重合'):
            evaluation_targets(overlap, .05)


if __name__ == '__main__':
    unittest.main()
