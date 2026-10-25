"""Deterministic Demo/worker bridge tests; no Qt, threads, camera or disk I/O."""
from dataclasses import replace
import unittest

import numpy as np

from gaze_core.config import DemoSettings
from gaze_core.starfield import StarfieldModel, StarfieldSettings
from overlay.star_run import StarDemoRun


def ns(milliseconds):
    return int(round(milliseconds * 1_000_000))


class SynchronousWorker:
    """Use the real numerical model but make message delivery synchronous."""
    def __init__(self, width, height, radii, settings, *, clock_ns):
        self.clock = clock_ns
        self.model = StarfieldModel(width, height, radii, settings, seed=7)
        self.calls = []
        self.error = None
        self.closed = False

    def start(self):
        self.calls.append(('start', self.clock()))

    def presented(self, now_ns):
        self.calls.append(('presented', now_ns))
        self.model.presented(now_ns)

    def pause(self, now_ns):
        self.calls.append(('pause', now_ns))
        self.model.pause(now_ns)

    def resume(self, now_ns):
        self.calls.append(('resume', now_ns))
        self.model.resume(now_ns)

    def ingest(self, point, capture_ns, now_ns, *, valid=True):
        self.calls.append(('ingest', capture_ns, now_ns, valid))
        return self.model.ingest(point, capture_ns, now_ns, valid=valid)

    def latest(self):
        return self.model.snapshot()

    def tick(self, now_ns):
        self.model.update(now_ns)

    def close(self, reason='escape', *, now_ns):
        self.calls.append(('close', reason, now_ns))
        self.model.close(now_ns, reason)
        self.closed = True

    def drain_events(self):
        return self.model.drain_events()


class StarRunTests(unittest.TestCase):
    def run_model(self, *, practice=False, **star_options):
        self.now = 0
        options = replace(DemoSettings(), practice=practice)
        run = StarDemoRun(options, 1512, 982, (55, 40), group='B',
                         settings=replace(StarfieldSettings(), **star_options),
                         clock_ns=lambda: self.now, worker_factory=SynchronousWorker)
        self.addCleanup(lambda: run.close(self.now, 'escape') if run.active else None)
        return run

    def feed(self, run, ms, point=(700., 450.), *, sequence=None, valid=True):
        self.now = ns(ms)
        return run.feed(point, self.now, self.now, valid=valid,
                        sequence=int(ms) if sequence is None else sequence)

    def animate(self, run, ms):
        self.now = ns(ms)
        run.worker.tick(self.now)
        run.advance(self.now)

    def practice_success(self, run, start_ms):
        self.now = ns(start_ms)
        run.presented(self.now)
        x, y, width, height = run.buttons[0].rect
        for ms in range(start_ms, start_ms + 801, 100):
            event = self.feed(run, ms, (x + width / 2, y + height / 2))
        self.assertIsNotNone(event)
        self.assertEqual(event['stage'], 'practice')
        self.assertEqual(run.phase, 'feedback')
        self.now = ns(start_ms + 1600)
        run.advance(self.now)
        self.assertEqual(run.phase, 'playing')
        self.assertIsNone(run.presented_ns)

    def test_first_paint_starts_once_not_construction_or_timer_refresh(self):
        run = self.run_model()
        self.assertEqual(run.worker.calls, [('start', 0)])
        self.assertEqual(run.worker.model.phase, 'ready')
        self.now = ns(5000)
        run.advance(self.now)
        self.assertIsNone(run.worker.model.started_ns)
        self.assertIsNone(run.presented_ns)
        run.presented(self.now)
        self.assertEqual(run.worker.model.started_ns, ns(5000))
        self.now = ns(6000)
        run.presented(self.now)
        self.assertEqual([call[0] for call in run.worker.calls].count('presented'), 1)
        self.animate(run, 64_000)
        self.assertEqual(run.phase, 'playing')
        self.animate(run, 65_000)
        self.assertEqual(run.phase, 'replay')

    def test_initial_practice_and_feedback_do_not_use_any_round_time(self):
        run = self.run_model(practice=True)
        self.practice_success(run, 1000)
        self.assertEqual(run.worker.model.phase, 'ready')
        self.assertEqual(run.worker.calls, [('start', 0)])
        self.now = ns(3000)
        run.presented(self.now)
        self.assertEqual(run.worker.model.started_ns, ns(3000))
        self.animate(run, 62_000)
        self.assertEqual(run.phase, 'playing')

    def test_capture_before_first_paint_and_repeated_sequence_cannot_reach_worker(self):
        run = self.run_model()
        self.feed(run, 0)
        self.assertFalse(any(call[0] == 'ingest' for call in run.worker.calls))
        self.now = ns(100)
        run.presented(self.now)
        run.feed((700, 450), ns(50), self.now, sequence=1)
        self.assertFalse(any(call[0] == 'ingest' for call in run.worker.calls))
        self.feed(run, 200, sequence=2)
        self.feed(run, 250, sequence=2)
        self.feed(run, 300, sequence=1)
        frames = [call for call in run.worker.calls if call[0] == 'ingest']
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1], ns(200))

    def test_only_real_capture_can_bloom_and_invalid_capture_is_forwarded(self):
        run = self.run_model(bloom_ms=300.)
        run.presented(0)
        self.feed(run, 0)
        self.feed(run, 100)
        self.feed(run, 200, None, valid=False)
        self.feed(run, 300)
        self.feed(run, 400)
        self.assertEqual(run.worker.model.firework_count, 0)
        self.animate(run, 500)
        self.assertEqual(run.worker.model.firework_count, 0)
        self.feed(run, 500)
        self.feed(run, 600)
        self.assertEqual(run.worker.model.firework_count, 1)
        mask = run.worker.model.mask.copy()
        self.animate(run, 900)
        self.animate(run, 1200)
        self.assertEqual(run.worker.model.firework_count, 1)
        np.testing.assert_array_equal(run.worker.model.mask, mask)
        frames = [call for call in run.worker.calls if call[0] == 'ingest']
        self.assertFalse(frames[2][-1])

    def test_repractice_pauses_at_once_and_only_first_return_paint_resumes(self):
        run = self.run_model()
        run.presented(0)
        self.animate(run, 10_000)
        self.now = ns(10_000)
        self.assertTrue(run.repractice(self.now))
        self.assertTrue(run.worker.model.paused)
        self.assertEqual(run.phase, 'practice')
        self.practice_success(run, 11_000)
        self.assertTrue(run.worker.model.paused)
        self.assertAlmostEqual(run.worker.model.snapshot()['elapsed_s'], 10)
        self.now = ns(13_000)
        run.presented(self.now)
        self.assertFalse(run.worker.model.paused)
        self.assertEqual([call[0] for call in run.worker.calls].count('resume'), 1)
        self.animate(run, 62_000)
        self.assertEqual(run.phase, 'playing')
        self.animate(run, 63_000)
        self.assertEqual(run.phase, 'replay')

    def test_repractice_during_replay_resumes_remaining_replay_not_new_round(self):
        run = self.run_model()
        run.presented(0)
        self.animate(run, 61_000)
        self.assertEqual(run.phase, 'replay')
        self.assertTrue(run.repractice(self.now))
        self.practice_success(run, 62_000)
        self.now = ns(64_000)
        run.presented(self.now)
        self.animate(run, 67_000)
        self.assertEqual(run.phase, 'replay')
        self.assertAlmostEqual(run.worker.latest()['replay_progress'], .8)
        self.animate(run, 68_000)
        self.assertEqual(run.phase, 'finished')

    def test_round_end_and_five_second_replay_summary_are_worker_owned(self):
        run = self.run_model()
        run.presented(0)
        self.feed(run, 100)
        self.animate(run, 60_000)
        self.assertEqual(run.phase, 'replay')
        self.assertFalse(any(kind == 'starfield_summary' for kind, _ in run.drain_events()))
        self.animate(run, 64_999)
        self.assertEqual(run.phase, 'replay')
        self.animate(run, 65_000)
        self.assertEqual(run.phase, 'finished')
        events = run.drain_events()
        summaries = [value for kind, value in events if kind == 'starfield_summary']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['elapsed_ms'], 60_000)
        self.assertEqual(summaries[0]['completed'], 1)
        self.assertEqual(summaries[0]['group'], 'B')
        self.assertEqual(summaries[0]['run_id'], run.run_id)
        self.assertFalse(any(kind == 'demo_summary' for kind, _ in events))

    def test_escape_closes_worker_before_run_and_summary_has_no_trajectory(self):
        run = self.run_model()
        run.presented(0)
        self.feed(run, 100)
        self.now = ns(200)
        run.close(self.now, 'escape')
        self.assertTrue(run.worker.closed)
        self.assertEqual(run.phase, 'closed')
        self.assertFalse(run.active)
        self.assertEqual(run.worker.calls[-1], ('close', 'escape', ns(200)))
        events = run.drain_events()
        summaries = [value for kind, value in events if kind == 'starfield_summary']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['reason_code'], 1)
        self.assertNotIn('trajectory', summaries[0])
        self.assertNotIn('position', summaries[0])
        self.assertEqual(run.drain_events(), [])


if __name__ == '__main__':
    unittest.main()
