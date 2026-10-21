"""Per-result numeric delivery and matrix snapshots using camera/engine doubles."""
from dataclasses import replace
import threading
import time
import unittest

import numpy as np

from gaze_core.capture import CameraPipeline, NumericResultBuffer, Prediction
from gaze_core.camera import CameraAdapter
from gaze_core.config import load_settings
from gaze_core.profiles import build_conditions
from overlay.mapping import ScreenRect


class Camera:
    def __init__(self):
        self.released = None
    def isOpened(self): return True
    def set(self, *args): return True
    def getBackendName(self): return 'SYNTHETIC'
    def read(self):
        time.sleep(.005)
        return True, np.full((6,8,3), 50, dtype=np.uint8)
    def release(self): self.released = threading.current_thread().name


class Engine:
    device = 'cpu'; device_name = 'synthetic'
    def predict(self, *args):
        return dict(valid=True, status='tracking', raw=[np.float64(.2),np.float32(.7)],
                    inference_ms=np.float64(1), landmarks=np.zeros((2,2)))
    def close(self): pass


def sample(i):
    return Prediction(i, i*100, i*100+1, dict(valid=True,status='tracking',raw=[np.float64(.2),.7]))


class CaptureNumericTests(unittest.TestCase):
    def pipeline(self, sink, *, display=None, capacity=128):
        settings = load_settings(use_local=False)
        settings = replace(settings, recording=replace(settings.recording, queue_size=capacity))
        camera = Camera()
        pipeline = CameraPipeline(settings, camera_factory=lambda _:camera, engine_factory=Engine,
                                  result_sink=sink, display_pipeline=display)
        self.addCleanup(pipeline.close)
        return pipeline, camera

    def wait_count(self, pipeline, count):
        deadline = time.monotonic()+3
        while pipeline.report()['predicted_frames'] < count:
            if time.monotonic() > deadline:
                self.fail('synthetic engine did not produce frames')
            pipeline.wait_for_result(timeout=.1)

    def test_every_result_delivered_once_with_original_clock_and_no_landmarks(self):
        delivered = []
        pipeline, camera = self.pipeline(lambda p:delivered.append(p))
        pipeline.start(); self.wait_count(pipeline, 8); pipeline.close()
        self.assertEqual(len(delivered), pipeline.report()['predicted_frames'])
        self.assertEqual([p.sequence for p in delivered], sorted(set(p.sequence for p in delivered)))
        for prediction in delivered:
            self.assertNotIn('landmarks', prediction.data)
            self.assertIs(type(prediction.data['raw'][0]), float)
            self.assertLess(prediction.captured_ns, prediction.completed_ns)
        self.assertEqual(camera.released, 'gaze-capture')

    def test_blocked_receiver_does_not_block_inference_and_overflow_is_explicit(self):
        gate = threading.Event(); entered = threading.Event()
        def sink(prediction):
            entered.set()
            if not gate.wait(3): raise RuntimeError('test gate timed out')
        pipeline, _ = self.pipeline(sink, capacity=2)
        pipeline.start(); self.assertTrue(entered.wait(3))
        try:
            self.wait_count(pipeline, 8)
            self.assertTrue(pipeline.running)
            self.assertIsNone(pipeline.error)
            self.assertIn('队列已满', pipeline.result_sink_error)
        finally:
            gate.set()
        pipeline.close()

    def test_receiver_error_does_not_stop_inference(self):
        def sink(_): raise ValueError('synthetic sink failure')
        pipeline, _ = self.pipeline(sink)
        pipeline.start(); self.wait_count(pipeline, 8)
        self.assertIsNone(pipeline.error)
        self.assertIn('synthetic sink failure', pipeline.result_sink_error)
        pipeline.close()

    def test_matrix_is_copied_and_only_processed_on_inference_thread(self):
        class Display:
            def __init__(self): self.calls = []
            def reset(self): pass
            def process(self, data, **kwargs):
                self.calls.append((threading.current_thread().name,kwargs))
                return dict(position=data['raw'])
        display = Display()
        pipeline, _ = self.pipeline(lambda _:None, display=display)
        matrix = [[1.,0.],[0.,1.],[.2,.3]]
        pipeline.set_display_matrix(matrix,revision=17)
        matrix[2][0] = 99
        pipeline.start(); self.wait_count(pipeline, 3); pipeline.pause()
        first = list(display.calls)
        self.assertEqual(pipeline._latest.data['display']['matrix_revision'],17)
        self.assertTrue(first)
        self.assertTrue(all(name=='gaze-inference' and args['matrix'][2][0]==.2 for name,args in first))
        pipeline.set_display_matrix(None,revision=23)
        pipeline.resume(); self.wait_count(pipeline, 3); pipeline.close()
        self.assertTrue(all(args['matrix'] is None for _,args in display.calls[len(first):]))
        self.assertEqual(pipeline._latest.data['display']['matrix_revision'],23)

    def test_inflight_display_keeps_matrix_and_revision_from_the_same_snapshot(self):
        entered=threading.Event();release=threading.Event()
        class Display:
            def reset(self): pass
            def process(self,data,**kwargs):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(3):raise RuntimeError('display test gate timed out')
                return dict(position=list(kwargs['matrix'][2]))
        pipeline,_=self.pipeline(lambda _:None,display=Display())
        pipeline.set_display_matrix([[1,0],[0,1],[.2,.3]],revision=4)
        pipeline.start()
        try:
            self.assertTrue(entered.wait(3))
            pipeline.set_display_matrix([[1,0],[0,1],[.8,.9]],revision=5)
            release.set()
            first=pipeline.wait_for_result(timeout=3)
            if first.data['display']['matrix_revision']==4:
                self.assertEqual(first.data['display']['position'],[.2,.3])
            self.wait_count(pipeline,3)
            pipeline.pause()
            self.assertEqual(pipeline._latest.data['display']['matrix_revision'],5)
            self.assertEqual(pipeline._latest.data['display']['position'],[.8,.9])
            for value in (-1,True,1.5):
                with self.assertRaises(ValueError):pipeline.set_display_matrix(None,revision=value)
        finally:release.set()

    def test_numeric_buffer_reports_overflow_and_retains_copied_frames(self):
        buffer = NumericResultBuffer(2)
        first = sample(1)
        self.assertTrue(buffer.push(first))
        first.data['raw'][0] = 10
        self.assertTrue(buffer.push(sample(2)))
        self.assertFalse(buffer.push(sample(3)))
        self.assertIn('队列已满', buffer.error)
        values = buffer.drain()
        self.assertEqual([p.sequence for p in values], [1,2])
        self.assertEqual(values[0].data['raw'], [.2,.7])
        self.assertIsNotNone(buffer.error)
        buffer.clear(); self.assertIsNone(buffer.error)
        bad = sample(4); bad.data['image'] = np.zeros((2,2))
        self.assertFalse(buffer.push(bad))
        self.assertEqual(buffer.drain(), [])

    def test_numeric_copy_preserves_integer_camera_conditions_including_nested_resolution(self):
        settings = load_settings(use_local=False)
        camera = CameraAdapter().prepare((640,480)).metadata
        camera.update(camera_index=np.int64(1),calibration_resolution=[np.int64(640),np.int32(480)])
        value = sample(1); value.data['camera'] = camera
        buffer = NumericResultBuffer(2)
        self.assertTrue(buffer.push(value))
        copied = buffer.drain()[0]
        metadata = copied.data['camera']
        self.assertIs(type(metadata['width_px']),int)
        self.assertIs(type(metadata['height_px']),int)
        self.assertIs(type(metadata['camera_index']),int)
        self.assertTrue(all(type(v) is int for v in metadata['calibration_resolution']))
        conditions = build_conditions(settings,metadata,ScreenRect(0,0,1512,982,2.))
        self.assertEqual((conditions['camera']['width_px'],conditions['camera']['height_px']),(640,480))
        self.assertEqual(conditions['camera']['index'],1)


if __name__ == '__main__':
    unittest.main()
