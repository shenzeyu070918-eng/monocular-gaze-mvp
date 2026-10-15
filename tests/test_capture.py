"""Controlled camera/engine doubles; never opens a physical device."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from gaze_core.capture import CameraPipeline, main, open_camera, run_benchmark, timing_summary
from gaze_core.config import load_settings


class FakeCamera:
    def __init__(self, *, stop_requested=None, opened=True):
        self.frames = queue.Queue()
        self.reads = threading.Condition()
        self.read_count = self.releases = 0
        self.reader_ids = set()
        self.setup_threads, self.properties = [], []
        self.release_threads, self.release_during_read = [], []
        self.active_reads = 0
        self.stop_requested, self.opened = stop_requested, opened

    def isOpened(self):
        self.setup_threads.append(threading.current_thread().name)
        return self.opened

    def set(self, property_id, value):
        self.setup_threads.append(threading.current_thread().name)
        self.properties.append((property_id, value))
        return True

    def getBackendName(self):
        self.setup_threads.append(threading.current_thread().name)
        return 'FAKE'

    def read(self):
        with self.reads:
            self.reader_ids.add(threading.get_ident())
            self.active_reads += 1
            self.read_count += 1
            self.reads.notify_all()
        try:
            # A real camera can still be inside read() when pause is requested.
            time.sleep(.03)
            if self.stop_requested is None:
                try:
                    frame = self.frames.get_nowait()
                except queue.Empty:
                    frame = np.zeros((6, 8, 3), dtype=np.uint8)
            else:
                # Controlled frames make the latest-frame overwrite test exact.
                # Stop is polled independently; release() must never unblock read().
                while True:
                    try:
                        frame = self.frames.get(timeout=.03)
                        break
                    except queue.Empty:
                        if self.stop_requested():
                            return False, None
            if isinstance(frame, Exception): raise frame
            return frame is not None, frame
        finally:
            with self.reads:
                self.active_reads -= 1
                self.reads.notify_all()

    def send(self, value):
        self.frames.put(np.full((6, 8, 3), value, dtype=np.uint8))

    def wait_for_reads(self, count):
        with self.reads:
            if not self.reads.wait_for(lambda: self.read_count >= count, 3):
                raise AssertionError('fake camera was not read')

    def release(self):
        with self.reads:
            self.releases += 1
            self.release_threads.append(threading.current_thread().name)
            self.release_during_read.append(self.active_reads > 0)


class FakeEngine:
    device, device_name = 'cpu', 'FAKE CPU'
    def __init__(self, gate=None, failure=None):
        self.owner = threading.get_ident()
        self.gate, self.failure = gate, failure
        self.started = threading.Event()
        self.values, self.gray_values = [], []
        self.closed = False
        self.close_count = 0
        self.predicting = threading.Lock()

    def predict(self, image, geometry, grayscale):
        assert self.predicting.acquire(blocking=False), 'concurrent predict() calls'
        try:
            assert not self.closed
            self.values.append(int(image[0,0,0]))
            self.gray_values.append(grayscale)
            self.started.set()
            if self.gate is not None and not self.gate.wait(3):
                raise RuntimeError('fake engine gate timed out')
            if self.failure is not None: raise self.failure
            return {'valid': False, 'status': 'no_face', 'inference_ms': 0}
        finally:
            self.predicting.release()

    def close(self):
        assert self.predicting.acquire(blocking=False), 'close() while predicting'
        try:
            self.closed = True
            self.close_count += 1
        finally:
            self.predicting.release()


class PipelineTests(unittest.TestCase):
    def make_pipeline(self, gate=None, failure=None, settings=None, controlled=False):
        cameras, engines = [], []
        def camera_factory(options):
            self.assertEqual((options.width, options.height), (640, 480))
            camera = FakeCamera(stop_requested=pipeline._stop.is_set if controlled else None)
            cameras.append(camera)
            return camera
        def engine_factory():
            engine = FakeEngine(gate, failure)
            engines.append(engine)
            return engine
        pipeline = CameraPipeline(settings, camera_factory=camera_factory, engine_factory=engine_factory,
                                  collect_timings=True)
        self.addCleanup(pipeline.close)
        return pipeline, cameras, engines

    def assert_workers_stopped(self):
        self.assertFalse([t for t in threading.enumerate() if t.name in {'gaze-capture', 'gaze-inference'}])

    def assert_camera_released_safely(self, camera):
        self.assertEqual(camera.releases, 1)
        self.assertEqual(camera.release_threads, ['gaze-capture'])
        self.assertEqual(camera.release_during_read, [False])
        self.assertEqual(camera.active_reads, 0)

    def test_latest_pending_frame_replaces_old_frames(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        pipeline, cameras, engines = self.make_pipeline(gate, controlled=True)
        pipeline.start()
        camera, engine = cameras[0], engines[0]
        try:
            camera.send(1)
            self.assertTrue(engine.started.wait(3))
            for value in [2, 3, 4]: camera.send(value)
            camera.wait_for_reads(5)
            report = pipeline.report()
            self.assertEqual(report['captured_frames'], 4)
            self.assertEqual(report['dropped_frames'], 2)
            self.assertEqual(report['pending_frames'], 1)
            self.assertEqual(report['in_flight_frames'], 1)
            gate.set()
            result = pipeline.wait_for_result(after_sequence=1, timeout=3)
            self.assertEqual(result.sequence, 4)
            self.assertEqual(engine.values, [1,4])
            self.assertGreaterEqual(result.completed_ns, result.captured_ns)
            report = pipeline.report()
            self.assertEqual(report['actual_resolution'], [8,6])
            self.assertEqual(report['requested_resolution'], [640,480])
            self.assertFalse(report['grayscale'])
            self.assertEqual(report['actual_backend'], 'FAKE')
            self.assertGreater(report['measurement_seconds'], 0)
            self.assertEqual(report['capture_fps'], report['captured_frames']/report['measurement_seconds'])
            self.assertGreaterEqual(report['read_to_result_latency_ms']['median'], report['inference_ms']['median'])
            self.assertNotIn(engine.owner, camera.reader_ids)
            self.assertIsNone(pipeline.wait_for_result(after_sequence=4, timeout=0))
        finally:
            gate.set()
            pipeline.close()
        self.assertTrue(engine.closed)
        self.assert_camera_released_safely(camera)
        self.assert_workers_stopped()

    def test_twenty_pause_resume_cycles_reuse_engine_and_release_only_in_capture_thread(self):
        settings = load_settings()
        settings = replace(settings, camera=replace(settings.camera, grayscale=True))
        pipeline, cameras, engines = self.make_pipeline(settings=settings)
        last_sequence = 0
        for number in range(1, 21):
            pipeline.resume()
            pipeline.start()  # An already running pipeline is not duplicated.
            self.assertEqual(len(cameras), number)
            self.assertEqual(len(engines), 1)
            cameras[-1].send(number)
            result = pipeline.wait_for_result(after_sequence=last_sequence, timeout=3)
            self.assertIsNotNone(result)
            self.assertGreater(result.sequence, last_sequence)
            last_sequence = result.sequence
            cameras[-1].wait_for_reads(2)
            pipeline.pause()
            self.assertFalse(pipeline.running)
            self.assertFalse(engines[0].closed)
            self.assertTrue(all(engines[0].gray_values))
            self.assert_camera_released_safely(cameras[-1])
            self.assert_workers_stopped()
        pipeline.close()
        pipeline.close()
        self.assertTrue(engines[0].closed)
        self.assertEqual(engines[0].close_count, 1)
        with self.assertRaises(RuntimeError): pipeline.resume()

    def test_close_waits_for_blocking_read_before_capture_thread_releases_camera(self):
        pipeline, cameras, engines = self.make_pipeline()
        pipeline.start()
        cameras[0].wait_for_reads(1)
        pipeline.close()
        self.assert_camera_released_safely(cameras[0])
        self.assertTrue(engines[0].closed)
        self.assert_workers_stopped()

    def test_read_failure_stops_engine_and_releases_camera(self):
        pipeline, cameras, engines = self.make_pipeline()
        pipeline.start()
        cameras[0].frames.put(RuntimeError('fake read error'))
        with self.assertRaisesRegex(RuntimeError, 'fake read error'):
            pipeline.wait_for_result(timeout=3)
        pipeline.close()
        self.assert_camera_released_safely(cameras[0])
        self.assertTrue(engines[0].closed)
        self.assert_workers_stopped()

    def test_inference_failure_signals_stop_and_capture_thread_releases_its_camera(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        pipeline, cameras, engines = self.make_pipeline(gate, failure=ValueError('fake inference error'),
                                                       controlled=True)
        pipeline.start()
        cameras[0].send(1)
        self.assertTrue(engines[0].started.wait(3))
        cameras[0].wait_for_reads(2)
        gate.set()
        with self.assertRaisesRegex(RuntimeError, 'fake inference error'):
            pipeline.wait_for_result(timeout=3)
        pipeline.close()
        self.assert_camera_released_safely(cameras[0])
        self.assertTrue(engines[0].closed)
        self.assert_workers_stopped()

    def test_initialization_failures_release_other_worker_resources(self):
        for fail_camera in [False, True]:
            with self.subTest(fail_camera=fail_camera):
                cameras, engines = [], []
                def camera_factory(options):
                    if fail_camera: raise RuntimeError('fake camera init')
                    camera = FakeCamera(); cameras.append(camera); return camera
                def engine_factory():
                    if not fail_camera: raise RuntimeError('fake engine init')
                    engine = FakeEngine(); engines.append(engine); return engine
                pipeline = CameraPipeline(camera_factory=camera_factory, engine_factory=engine_factory)
                try:
                    with self.assertRaises(RuntimeError): pipeline.start()
                finally:
                    pipeline.close()
                for camera in cameras:
                    self.assert_camera_released_safely(camera)
                self.assertTrue(all(e.closed for e in engines))
                self.assert_workers_stopped()

    def test_stop_timeout_is_reported_and_cleanup_can_be_retried(self):
        settings = load_settings()
        settings = replace(settings, camera=replace(settings.camera, stop_timeout_s=.01))
        gate = threading.Event()
        self.addCleanup(gate.set)
        pipeline, cameras, engines = self.make_pipeline(gate, settings=settings)
        pipeline.start()
        cameras[0].send(1)
        try:
            self.assertTrue(engines[0].started.wait(3))
            with self.assertRaisesRegex(RuntimeError, '线程未能'):
                pipeline.pause()
            self.assertFalse(engines[0].closed)
        finally:
            gate.set()
            for worker in pipeline._threads: worker.join(3)
            pipeline.close()
        self.assert_camera_released_safely(cameras[0])
        self.assertTrue(engines[0].closed)
        self.assert_workers_stopped()

    def test_camera_configuration_and_failed_open_cleanup_belong_to_capture_thread(self):
        import cv2
        for opened in [True, False]:
            with self.subTest(opened=opened):
                camera = FakeCamera(opened=opened)
                pipeline = CameraPipeline(camera_factory=lambda _: camera, engine_factory=FakeEngine)
                try:
                    if opened:
                        pipeline.start()
                        self.assertEqual(camera.properties, [(cv2.CAP_PROP_FRAME_WIDTH, 640),
                                                              (cv2.CAP_PROP_FRAME_HEIGHT, 480)])
                    else:
                        with self.assertRaisesRegex(RuntimeError, '无法打开摄像头'):
                            pipeline.start()
                finally:
                    pipeline.close()
                self.assertEqual(set(camera.setup_threads), {'gaze-capture'})
                self.assert_camera_released_safely(camera)
                self.assert_workers_stopped()


class BenchmarkTests(unittest.TestCase):
    def test_percentiles_and_no_sample_result(self):
        self.assertEqual(timing_summary([]), {'median':None, 'p90':None})
        self.assertEqual(timing_summary([7]), {'median':7, 'p90':7})
        self.assertEqual(timing_summary([5,1,4,2,3]), {'median':3, 'p90':4.6})

    def test_benchmark_collects_report_from_fake_workers_and_closes_them(self):
        camera = FakeCamera()
        camera.send(1)
        engines = []
        def factory():
            engine = FakeEngine(); engines.append(engine); return engine
        pipeline = CameraPipeline(camera_factory=lambda _: camera, engine_factory=factory, collect_timings=True)
        # Finish the sample window on a deterministic result, not an assumed speed.
        pipeline.wait = lambda seconds: self.assertIsNotNone(pipeline.wait_for_result(timeout=3))
        report = run_benchmark(pipeline, 1)
        self.assertGreaterEqual(report['predicted_frames'], 1)
        self.assertEqual(report['valid_predictions'], 0)
        self.assertEqual(report['prediction_status_counts'], {'no_face':report['predicted_frames']})
        self.assertEqual(report['actual_resolution'], [8,6])
        self.assertEqual(report['device_name'], 'FAKE CPU')
        self.assertIn('不含摄像头曝光与驱动缓冲', report['latency_definition'])
        self.assertIsNotNone(report['inference_ms']['p90'])
        json.dumps(report, allow_nan=False)
        self.assertTrue(engines[0].closed)
        self.assertEqual(camera.releases, 1)

    def test_benchmark_closes_after_failure_and_rejects_invalid_duration(self):
        pipeline = Mock()
        pipeline.wait.side_effect = RuntimeError('fake error')
        with self.assertRaises(RuntimeError): run_benchmark(pipeline, 1)
        pipeline.close.assert_called_once()
        for seconds in [0,-1,float('inf'),float('nan')]:
            with self.assertRaises(ValueError): run_benchmark(Mock(), seconds)

    def test_cli_propagates_grayscale_and_outputs_report_without_opening_camera(self):
        with patch('gaze_core.capture.CameraPipeline') as factory, \
             patch('gaze_core.capture.run_benchmark', return_value={'test_only':True}) as benchmark, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--benchmark','1','--grayscale']), 0)
        self.assertTrue(factory.call_args.args[0].camera.grayscale)
        self.assertEqual(benchmark.call_args.args[1], 1)
        self.assertEqual(json.loads(output.getvalue()), {'test_only':True})

    def test_opencv_factory_selects_backend_and_leaves_camera_lifecycle_to_capture_thread(self):
        settings = load_settings().camera
        import cv2
        camera = Mock()
        camera.isOpened.return_value = False
        with patch.object(cv2, 'VideoCapture', return_value=camera) as factory:
            self.assertIs(open_camera(settings), camera)
        factory.assert_called_once_with(0, cv2.CAP_ANY)
        camera.isOpened.assert_not_called()
        camera.set.assert_not_called()
        camera.release.assert_not_called()
        with patch('gaze_core.capture.sys.platform', 'win32'), \
             patch.object(cv2, 'VideoCapture', return_value=camera) as factory:
            camera.isOpened.return_value = True
            open_camera(replace(settings, backend='dshow'))
        factory.assert_called_once_with(0, cv2.CAP_DSHOW)
        with patch('gaze_core.capture.sys.platform', 'darwin'):
            with self.assertRaises(ValueError): open_camera(replace(settings, backend='dshow'))


if __name__ == '__main__': unittest.main()
