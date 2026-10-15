"""Controlled camera/engine doubles; never opens a physical device."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import queue
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from gaze_core.capture import CameraPipeline, main, open_camera, run_benchmark, timing_summary
from gaze_core.config import load_settings


class FakeCamera:
    def __init__(self):
        self.frames = queue.Queue()
        self.reads = threading.Condition()
        self.read_count = self.releases = 0
        self.reader_ids = set()

    def getBackendName(self): return 'FAKE'

    def read(self):
        self.reader_ids.add(threading.get_ident())
        with self.reads:
            self.read_count += 1
            self.reads.notify_all()
        frame = self.frames.get(timeout=3)
        if isinstance(frame, Exception): raise frame
        return frame is not None, frame

    def send(self, value):
        self.frames.put(np.full((6, 8, 3), value, dtype=np.uint8))

    def wait_for_reads(self, count):
        with self.reads:
            if not self.reads.wait_for(lambda: self.read_count >= count, 3):
                raise AssertionError('fake camera was not read')

    def release(self):
        self.releases += 1
        self.frames.put(None)


class FakeEngine:
    device, device_name = 'cpu', 'FAKE CPU'
    def __init__(self, gate=None, failure=None):
        self.owner = threading.get_ident()
        self.gate, self.failure = gate, failure
        self.started = threading.Event()
        self.values, self.gray_values = [], []
        self.closed = False

    def predict(self, image, geometry, grayscale):
        assert threading.get_ident() == self.owner
        self.values.append(int(image[0,0,0]))
        self.gray_values.append(grayscale)
        self.started.set()
        if self.gate is not None and not self.gate.wait(3):
            raise RuntimeError('fake engine gate timed out')
        if self.failure is not None: raise self.failure
        return {'valid': False, 'status': 'no_face', 'inference_ms': 0}

    def close(self):
        assert threading.get_ident() == self.owner
        self.closed = True


class PipelineTests(unittest.TestCase):
    def make_pipeline(self, gate=None, failure=None, settings=None):
        cameras, engines = [], []
        def camera_factory(options):
            self.assertEqual((options.width, options.height), (640, 480))
            camera = FakeCamera()
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

    def test_latest_pending_frame_replaces_old_frames(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        pipeline, cameras, engines = self.make_pipeline(gate)
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
        self.assertEqual(camera.releases, 1)
        self.assert_workers_stopped()

    def test_pause_resume_recreates_resources_and_close_is_idempotent(self):
        settings = load_settings()
        settings = replace(settings, camera=replace(settings.camera, grayscale=True))
        pipeline, cameras, engines = self.make_pipeline(settings=settings)
        for number in [1,2]:
            pipeline.resume()
            pipeline.start()  # An already running pipeline is not duplicated.
            self.assertEqual(len(cameras), number)
            cameras[-1].send(number)
            self.assertIsNotNone(pipeline.wait_for_result(timeout=3))
            pipeline.pause()
            self.assertFalse(pipeline.running)
            self.assertTrue(engines[-1].closed)
            self.assertEqual(engines[-1].gray_values, [True])
            self.assertEqual(cameras[-1].releases, 1)
            self.assert_workers_stopped()
        pipeline.close()
        pipeline.close()
        with self.assertRaises(RuntimeError): pipeline.resume()

    def test_read_failure_stops_engine_and_releases_camera(self):
        pipeline, cameras, engines = self.make_pipeline()
        pipeline.start()
        cameras[0].frames.put(RuntimeError('fake read error'))
        with self.assertRaisesRegex(RuntimeError, 'fake read error'):
            pipeline.wait_for_result(timeout=3)
        pipeline.close()
        self.assertEqual(cameras[0].releases, 1)
        self.assertTrue(engines[0].closed)
        self.assert_workers_stopped()

    def test_inference_failure_unblocks_camera_and_releases_resources(self):
        pipeline, cameras, engines = self.make_pipeline(failure=ValueError('fake inference error'))
        pipeline.start()
        cameras[0].send(1)
        with self.assertRaisesRegex(RuntimeError, 'fake inference error'):
            pipeline.wait_for_result(timeout=3)
        pipeline.close()
        self.assertEqual(cameras[0].releases, 1)
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
                self.assertTrue(all(c.releases == 1 for c in cameras))
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
        finally:
            gate.set()
            for worker in pipeline._threads: worker.join(3)
            pipeline.close()
        self.assertEqual(cameras[0].releases, 1)
        self.assertTrue(engines[0].closed)
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
        self.assertEqual(report['predicted_frames'], 1)
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

    def test_opencv_request_and_release_on_open_failure(self):
        settings = load_settings().camera
        import cv2
        camera = Mock()
        camera.isOpened.return_value = True
        with patch.object(cv2, 'VideoCapture', return_value=camera) as factory:
            self.assertIs(open_camera(settings), camera)
        factory.assert_called_once_with(0, cv2.CAP_ANY)
        self.assertIn(((cv2.CAP_PROP_FRAME_WIDTH, 640),), [(c.args,) for c in camera.set.call_args_list])
        self.assertIn(((cv2.CAP_PROP_FRAME_HEIGHT, 480),), [(c.args,) for c in camera.set.call_args_list])
        camera.isOpened.return_value = False
        with patch.object(cv2, 'VideoCapture', return_value=camera):
            with self.assertRaises(RuntimeError): open_camera(settings)
        camera.release.assert_called_once()
        with patch('gaze_core.capture.sys.platform', 'win32'), \
             patch.object(cv2, 'VideoCapture', return_value=camera) as factory:
            camera.isOpened.return_value = True
            open_camera(replace(settings, backend='dshow'))
        factory.assert_called_once_with(0, cv2.CAP_DSHOW)
        with patch('gaze_core.capture.sys.platform', 'darwin'):
            with self.assertRaises(ValueError): open_camera(replace(settings, backend='dshow'))


if __name__ == '__main__': unittest.main()
