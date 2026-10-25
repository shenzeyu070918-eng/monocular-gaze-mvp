"""Synthetic star data only: no camera, global input, personal recording or image I/O."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from gaze_core.starfield import StarfieldSettings
from overlay.starfield import (PaintEventTimer, StarfieldPainter, StarfieldWorker, _Timings,
                               _batches, _polygon, save_starfield_performance)


class FakeModel:
    def __init__(self, width, height, radii, settings, *, seed=None):
        self.calls = []
        self.events = []
        self.phase = 'ready'
        self.paused = False

    def _call(self, name, value):
        self.calls.append((name, value, threading.current_thread().name))

    def presented(self, now):
        self._call('presented', now)
        self.phase = 'playing'

    def pause(self, now):
        self._call('pause', now)
        self.paused = True

    def resume(self, now):
        self._call('resume', now)
        self.paused = False

    def ingest(self, position, capture_ns, now_ns, valid=True):
        self._call('frame', (position, capture_ns, now_ns, valid))

    def update(self, now):
        self._call('update', now)

    def snapshot(self):
        return dict(positions=np.array([[10., 20.], [30., 40.]]),
                    colors=np.array([[190, 215, 255], [190, 215, 255]], dtype=np.uint8),
                    brightness=np.array([.5, 1.]), sizes=np.array([1., 2.]),
                    halo_position=None, halo_opacity=0., halo_radii=(20., 20.),
                    constellations=(), phase=self.phase, paused=self.paused,
                    firework_count=0, found_count=0, elapsed_s=0., remaining_s=60.,
                    replay_trajectory=np.empty((0, 2)))

    def drain_events(self):
        result, self.events = self.events, []
        return result

    def close(self, now, reason='escape'):
        self._call('close', (now, reason))
        self.phase = 'finished'
        self.events.append(('starfield_summary', dict(event_ns=now, reason_code=1)))


class StarfieldWorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.workers = []
        self.options = replace(StarfieldSettings(), animation_fps=1.)

    def worker(self, **kwargs):
        worker = StarfieldWorker(800, 600, (30, 30), self.options,
                                 clock_ns=lambda: self.now,
                                 model_factory=kwargs.pop('model_factory', FakeModel), **kwargs)
        self.workers.append(worker)
        return worker

    def fence(self, worker):
        self.assertTrue(worker.flush())
        self.assertIsNone(worker.error)

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            self.assertFalse(worker.thread.is_alive())

    def test_explicit_start_pause_resume_close_and_worker_owns_model(self):
        worker = self.worker()
        self.assertIsNone(worker.latest())
        self.assertFalse(worker.thread.is_alive())
        self.assertFalse(worker.ingest((10, 20), 0))
        worker.start()
        self.fence(worker)
        self.assertEqual(worker.latest()['phase'], 'ready')
        self.assertFalse(any(call[0] == 'update' for call in worker._model.calls))
        worker.presented(0)
        self.fence(worker)
        self.now = 10_000_000
        self.assertTrue(worker.ingest((10, 20), self.now))
        self.fence(worker)
        worker.pause(self.now)
        self.fence(worker)
        version = worker.latest()['version']
        updates = sum(call[0] == 'update' for call in worker._model.calls)
        self.now = 2_000_000_000
        worker.ingest((10, 20), self.now)
        self.fence(worker)
        self.assertEqual(worker.latest()['version'], version)
        self.assertEqual(sum(call[0] == 'update' for call in worker._model.calls), updates)
        worker.resume(self.now)
        self.fence(worker)
        self.assertGreater(worker.latest()['version'], version)
        worker.close(reason='escape')
        calls = len(worker._model.calls)
        self.assertFalse(worker.start())
        self.assertFalse(worker.presented())
        self.assertFalse(worker.ingest((1, 2), self.now + 1))
        self.assertEqual(len(worker._model.calls), calls)
        self.assertTrue(all(call[2] == 'gaze-stars' for call in worker._model.calls))
        self.assertEqual(worker.drain_events()[0][0], 'starfield_summary')
        self.assertEqual(worker.drain_events(), [])

    def test_new_capture_is_delivered_once_and_invalid_frame_is_not_fabricated(self):
        worker = self.worker()
        worker.presented(0)
        self.fence(worker)
        self.now = 100_000_000
        self.assertTrue(worker.ingest((np.float64(20), np.float64(30)), self.now))
        self.fence(worker)
        self.assertFalse(worker.ingest((20, 30), self.now))
        self.assertFalse(worker.ingest((20, 30), self.now - 1))
        self.fence(worker)
        self.now += 1
        self.assertTrue(worker.ingest(None, self.now, valid=False))
        self.fence(worker)
        frames = [call[1] for call in worker._model.calls if call[0] == 'frame']
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][0], (20., 30.))
        self.assertFalse(frames[1][-1])
        self.assertEqual(worker.processed_frames, 2)

    def test_latest_frame_slot_is_bounded_and_snapshot_is_readonly(self):
        entered, release = threading.Event(), threading.Event()
        class SlowModel(FakeModel):
            def update(self, now):
                if now == 0:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test wait expired')
                super().update(now)
        worker = self.worker(model_factory=SlowModel)
        worker.presented(0)
        self.assertTrue(entered.wait(3))
        try:
            for number in range(1, 21):
                self.now = number
                self.assertTrue(worker.ingest((number, number), number))
            self.assertEqual(worker.dropped_frames, 19)
        finally:
            release.set()
        self.fence(worker)
        frames = [call[1] for call in worker._model.calls if call[0] == 'frame']
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1], 20)
        snapshot = worker.latest()
        with self.assertRaises(TypeError):
            snapshot['phase'] = 'corrupt'
        with self.assertRaises(ValueError):
            snapshot['positions'][0, 0] = 100

    def test_worker_initialization_does_not_overrun_queued_first_paint_time(self):
        self.now = 1000
        worker = self.worker(model_factory=None)
        worker.presented(500)
        self.fence(worker)
        self.assertEqual(worker.latest()['phase'], 'playing')
        self.assertEqual(worker._model.started_ns, 500)

    def test_frame_handoff_after_animation_tick_keeps_capture_time_and_monotonic_now(self):
        worker = self.worker(model_factory=None)
        worker.presented(0)
        self.fence(worker)
        self.now = 120_000_000
        worker.ingest((50, 50), 90_000_000, now_ns=100_000_000)
        self.fence(worker)
        self.assertEqual(worker._model._last_capture_ns, 90_000_000)
        self.assertGreaterEqual(worker._model._now_ns, 120_000_000)

    def test_future_capture_does_not_poison_latest_frame_high_water_mark(self):
        worker = self.worker()
        worker.presented(0)
        self.fence(worker)
        self.now = 100_000_000
        self.assertFalse(worker.ingest((50, 50), 1_000_000_000))
        self.assertIsNone(worker._last_capture_ns)
        self.assertTrue(worker.ingest((50, 50), 90_000_000))
        self.fence(worker)
        self.assertEqual(worker.processed_frames, 1)
        self.assertEqual(worker._last_capture_ns, 90_000_000)


class StarfieldPainterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def snapshot(self):
        model = FakeModel(800, 600, (30, 30), StarfieldSettings())
        snapshot = model.snapshot()
        snapshot['draw_batches'] = _batches(snapshot)
        snapshot['update_ms'] = 3.
        return snapshot

    def test_bulk_polygon_is_owned_and_points_are_correct(self):
        data = np.array([[1., 2.], [3., 4.]], dtype=np.float32)
        polygon = _polygon(data)
        data[:] = 0
        self.assertEqual(polygon.size(), 2)
        self.assertEqual((polygon[1].x(), polygon[1].y()), (3., 4.))

    def test_batched_drawing_caches_glow_and_records_actual_draw_not_update_request(self):
        ticks = iter((0, 2_000_000, 10_000_000, 14_000_000))
        renderer = StarfieldPainter(clock_ns=lambda: next(ticks))
        image = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        glow = renderer._glow
        snapshot = self.snapshot()
        with patch.object(QImage, 'save', side_effect=AssertionError('must not save image')):
            self.assertTrue(renderer.draw(painter, QRectF(0, 0, 800, 600), snapshot))
            self.assertTrue(renderer.draw(painter, QRectF(0, 0, 800, 600), snapshot))
        painter.end()
        self.assertIs(renderer._glow, glow)
        report = renderer.performance_report()
        self.assertEqual(report['draw']['count'], 2)
        self.assertEqual(report['draw']['milliseconds']['median'], 3.)
        self.assertEqual(report['update_plus_draw']['milliseconds']['median'], 6.)
        self.assertEqual(report['draw']['actual_fps'], 100.)
        renderer.close()
        self.assertFalse(renderer.draw(Mock(), QRectF(0, 0, 1, 1), snapshot))
        self.assertEqual(renderer.performance_report()['draw']['count'], 2)

    def test_painter_rejects_non_main_thread(self):
        renderer = StarfieldPainter()
        errors = []
        def wrong_thread():
            try:
                renderer.draw(Mock(), QRectF(0, 0, 1, 1), self.snapshot())
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(3)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertEqual(renderer.performance_report()['draw']['count'], 0)

    def test_timing_summary_uses_frame_starts_and_counts_long_work(self):
        timings = _Timings(8.)
        self.assertEqual(timings.report()['milliseconds'], dict(median=None, p90=None))
        self.assertIsNone(timings.report()['actual_fps'])
        for start, duration in ((0, 1), (10_000_000, 2), (20_000_000, 9)):
            timings.record(start, duration)
        report = timings.report()
        self.assertEqual(report['long_frames'], 1)
        self.assertEqual(report['actual_fps'], 100.)
        self.assertEqual(report['milliseconds']['median'], 2.)
        self.assertAlmostEqual(report['milliseconds']['p90'], 7.6)

    def test_whole_paint_timer_counts_background_labels_and_budget_independently(self):
        now = [0]
        timer = PaintEventTimer(8., clock_ns=lambda: now[0])
        self.assertEqual(timer.report()['milliseconds'], dict(median=None, p90=None))
        token = timer.start()
        now[0] += 1_000_000  # painter/background setup
        now[0] += 3_000_000  # actual stars
        now[0] += 2_000_000  # title/debug/footer plus painter.end
        self.assertEqual(timer.finish(token), 6.)
        now[0] = 20_000_000
        with timer.measure():
            now[0] += 10_000_000
        report = timer.report()
        self.assertEqual(report['count'], 2)
        self.assertEqual(report['milliseconds']['median'], 8.)
        self.assertAlmostEqual(report['milliseconds']['p90'], 9.6)
        self.assertEqual(report['actual_fps'], 50.)
        self.assertEqual(report['budget_ms'], 8.)
        self.assertEqual(report['over_budget_frames'], 1)

    def test_whole_paint_timer_records_exception_scope_and_rejects_bad_budget_or_clock(self):
        ticks = iter((0, 9_000_000))
        timer = PaintEventTimer(7., clock_ns=lambda: next(ticks))
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with timer.measure():
                raise RuntimeError('synthetic')
        self.assertEqual(timer.report()['over_budget_frames'], 1)
        for budget in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                PaintEventTimer(budget)
        backwards = PaintEventTimer(clock_ns=lambda: 0)
        with self.assertRaises(ValueError):
            backwards.finish(1)
        self.assertEqual(backwards.report()['count'], 0)

    def test_save_is_explicit_single_numeric_json_under_data(self):
        worker = Mock()
        worker.performance_report.return_value = {'update': {'count': 1}}
        renderer = StarfieldPainter()
        with tempfile.TemporaryDirectory() as directory, patch('overlay.starfield.ROOT', Path(directory)):
            path = Path(directory) / 'data/performance/stars.json'
            full_frame = PaintEventTimer().report()
            report = save_starfield_performance(path, worker, renderer, frame_report=full_frame)
            self.assertEqual(json.loads(path.read_text()), report)
            self.assertEqual(report['paint_event'], full_frame)
            self.assertEqual(list(Path(directory).rglob('*.json')), [path])
            for bad in (Path(directory) / 'outside.json', path.with_suffix('.png')):
                with self.assertRaises(ValueError):
                    save_starfield_performance(bad, worker, renderer)
            self.assertEqual(list(Path(directory).rglob('*.png')), [])


if __name__ == '__main__':
    unittest.main()
