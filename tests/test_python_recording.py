"""Runtime recording uses synthetic numbers; no cameras or images on disk."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import numpy as np

from gaze_core.capture import Prediction
from gaze_core.config import RecordingSettings
from gaze_core.python_recording import PythonRecording
from gaze_core.recorder import SessionRecorder
from tools.replay import read_session


def metadata():
    return dict(source='python', version='test', commit='test', system='test', device_name='test',
                inference_device='cpu', inference_device_name='test', page_clock_id=None, grayscale=False,
                screen=dict(width_px=1000, height_px=800, measured=False),
                camera=dict(width_px=640, height_px=480, intrinsics_source='estimated',
                            matrix=[[640,0,320],[0,640,240],[0,0,1]], distortion=[0]*5))


def prediction(index):
    return Prediction(index, index*1000000, index*1000000+123,
                      dict(valid=True, status='tracking', raw=[np.float64(.2), np.float32(.7)],
                           origin_m=[0.,0.,.5], distance_cm=np.float32(50), inference_ms=1.))


def wait_for(predicate):
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('synthetic recording did not reach expected state')


class RuntimeRecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = RecordingSettings(self.directory.name, 128, 3)
        self.recorders = []

    def make_recording(self, **kwargs):
        def factory(data):
            recorder = SessionRecorder(data, settings=self.settings)
            self.recorders.append(recorder)
            return recorder
        recording = PythonRecording(self.settings, recorder_factory=factory, **kwargs)
        self.addCleanup(recording.close)
        return recording

    def test_repeated_manual_sessions_record_all_numeric_frames_and_end(self):
        recording = self.make_recording()
        recording.set_metadata(metadata())
        for session_index in range(3):
            self.assertTrue(recording.manual(True))
            for i in range(10):
                self.assertTrue(recording.record_prediction(prediction(i+1)))
            self.assertTrue(recording.manual(False))
        recording.close()
        self.assertEqual(len(self.recorders), 3)
        for recorder in self.recorders:
            session = read_session(recorder.path)
            frames = [row for row in session.records if row['type']=='frame']
            self.assertEqual(len(frames), 10)
            self.assertEqual([row['capture_ns'] for row in frames], [i*1000000 for i in range(1,11)])
            self.assertEqual(frames[0]['raw'], [.2, float(np.float32(.7))])
            self.assertTrue(session.ended_normally)
            self.assertEqual(session.records[-1]['reason'], 'user')
            self.assertNotIn('base64', recorder.path.read_text())

    def test_task_reuses_manual_session_and_automatic_task_closes(self):
        recording = self.make_recording()
        recording.set_metadata(metadata())
        self.assertTrue(recording.manual(True))
        self.assertTrue(recording.begin_task('task-one'))
        self.assertFalse(recording.begin_task('task-two'))
        self.assertTrue(recording.record_prediction(prediction(1)))
        self.assertTrue(recording.end_task('task-one'))
        # Task completion cannot close an independently enabled manual session.
        self.assertTrue(recording.status()['manual'])
        self.assertTrue(recording.record_prediction(prediction(2)))
        self.assertTrue(recording.manual(False))
        self.assertTrue(recording.begin_task('task-three'))
        self.assertTrue(recording.record_prediction(prediction(3)))
        self.assertTrue(recording.end_task('task-three', reason='cancelled'))
        recording.close()
        self.assertEqual(len(self.recorders), 2)
        self.assertEqual([read_session(r.path).summary()['frame_count'] for r in self.recorders], [2,1])
        self.assertEqual(read_session(self.recorders[1].path).records[-1]['reason'], 'cancelled')

    def test_metadata_preparation_is_background_and_cannot_block_producer(self):
        gate = threading.Event()
        entered = threading.Event()
        metadata_threads = []
        def slow_metadata():
            metadata_threads.append(threading.current_thread().name)
            entered.set()
            if not gate.wait(3):
                raise RuntimeError('test gate timed out')
            return metadata()
        recording = self.make_recording(metadata_factory=slow_metadata)
        self.assertTrue(recording.manual(True))
        self.assertTrue(entered.wait(3))
        for i in range(20):
            self.assertTrue(recording.record_prediction(prediction(i+1)))
        self.assertFalse(gate.is_set())
        self.assertTrue(recording.status()['preparing'])
        gate.set()
        recording.close()
        self.assertEqual(metadata_threads, ['gaze-python-recording'])
        self.assertEqual(read_session(self.recorders[0].path).summary()['frame_count'], 20)

    def test_queue_overflow_is_explicit_and_written_prefix_has_failed_end(self):
        gate = threading.Event(); entered = threading.Event()
        def blocked_metadata():
            entered.set()
            if not gate.wait(3):
                raise RuntimeError('test gate timed out')
            return metadata()
        recording = self.make_recording(metadata_factory=blocked_metadata)
        # Fill only the bridge queue, without relying on storage speed.
        recording._queue = __import__('queue').Queue(1)
        self.assertTrue(recording.manual(True))
        self.assertTrue(entered.wait(3))
        self.assertTrue(recording.record_prediction(prediction(1)))
        self.assertFalse(recording.record_prediction(prediction(2)))
        self.assertIn('队列已满', recording.status()['error'])
        self.assertFalse(recording.status()['active'])
        gate.set()
        recording.close()
        import warnings
        with warnings.catch_warnings(record=True):
            session = read_session(self.recorders[0].path)
        self.assertEqual(session.summary()['frame_count'], 1)
        self.assertEqual(session.records[-1]['reason'], 'recording_failed')
        self.assertGreater(session.records[-1]['failed_records'], 0)

    def test_slow_disk_writer_does_not_block_numeric_producers(self):
        gate = threading.Event(); entered = threading.Event()
        class SlowOutput:
            def __init__(self, path, mode, *, encoding):
                self.output = open(path, mode, encoding=encoding)
            def __enter__(self): return self
            def __exit__(self, *args): self.output.close()
            def write(self, text):
                if json.loads(text)['type']=='frame':
                    entered.set()
                    if not gate.wait(3): raise RuntimeError('test gate timed out')
                return self.output.write(text)
            def flush(self): self.output.flush()
        recorders = []
        def factory(data):
            recorder = SessionRecorder(data,settings=self.settings,file_factory=SlowOutput)
            recorders.append(recorder)
            return recorder
        recording = PythonRecording(self.settings,metadata,recorder_factory=factory)
        self.addCleanup(recording.close)
        self.assertTrue(recording.manual(True))
        self.assertTrue(recording.record_prediction(prediction(1)))
        self.assertTrue(entered.wait(3))
        try:
            for i in range(2,21):
                self.assertTrue(recording.record_prediction(prediction(i)))
            self.assertFalse(gate.is_set())
            self.assertIsNone(recording.status()['error'])
        finally:
            gate.set()
        recording.close()
        self.assertEqual(read_session(recorders[0].path).summary()['frame_count'],20)

    def test_rejects_image_containers_before_queue_and_metadata_not_ready(self):
        recording = self.make_recording()
        self.assertFalse(recording.manual(True))
        self.assertIn('实际条件', recording.status()['error'])
        recording.set_metadata(metadata())
        self.assertTrue(recording.manual(True))
        bad = prediction(1)
        bad.data['pixels'] = np.zeros((2,2))
        self.assertFalse(recording.record_prediction(bad))
        self.assertFalse(recording.status()['active'])
        recording.close()
        files = list(Path(self.directory.name).glob('*.jsonl'))
        self.assertEqual(len(files), 1)
        for line in files[0].read_text().splitlines():
            self.assertNotIn('pixels', json.loads(line))

    def test_close_drains_final_frame_and_explicit_python_event_timestamp(self):
        recording = self.make_recording()
        recording.set_metadata(metadata())
        self.assertTrue(recording.begin_task('nine'))
        self.assertTrue(recording.emit('target', action='appear', task_type='calibrate', task_id='nine',
                                       index=0, target=[.5,.5], event_ns=500000))
        self.assertTrue(recording.record_prediction(prediction(1)))
        recording.close(reason='overlay_closed')
        session = read_session(self.recorders[0].path)
        event = next(row for row in session.records if row['type']=='target')
        self.assertEqual(event['event_ns'], 500000)
        self.assertNotIn('browser_time_ms', event)
        self.assertEqual([row['type'] for row in session.records], ['session','target','frame','end'])
        self.assertEqual(session.records[-1]['reason'], 'overlay_closed')


if __name__ == '__main__':
    unittest.main()
