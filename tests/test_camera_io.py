"""Deterministic retry budgets using a fake clock and in-memory frames."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from gaze_core.camera_io import read_camera_frames
from gaze_core.config import load_settings


class FakeTimeStop:
    def __init__(self):
        self.now, self.stopped, self.delays = 0., False, []

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.delays.append(delay)
        self.now += delay
        return self.stopped


class CameraReadTests(unittest.TestCase):
    def setUp(self):
        self.options = replace(load_settings(use_local=False).camera, warmup_timeout_s=3,
                               read_retry_interval_s=.5, read_retry_count=2)
        self.clock = FakeTimeStop()
        self.image = np.ones((6,8,3), dtype=np.uint8)

    def stream(self, reads):
        self.camera = SimpleNamespace(read=Mock(side_effect=reads), release=Mock())
        return read_camera_frames(self.camera, self.options, stop=self.clock,
                                  clock_ns=lambda: round(self.clock.now*1e9),
                                  monotonic=lambda: self.clock.now)

    def test_initial_failures_then_success_preserve_successful_read_timestamp(self):
        stream = self.stream([(False,None)]*3+[(True,self.image)])
        image, timestamp = next(stream)
        self.assertIs(image,self.image)
        self.assertEqual(timestamp,1_500_000_000)
        self.assertEqual(self.clock.delays,[.5]*3)
        self.camera.release.assert_not_called()

    def test_never_a_frame_expires_without_an_extra_read_at_deadline(self):
        stream = self.stream([(False,None)]*6)
        with self.assertRaisesRegex(RuntimeError,'从未读到画面.*终端.*权限.*占用'):
            next(stream)
        self.assertEqual(self.clock.now,3)
        self.assertEqual(self.camera.read.call_count,6)
        self.camera.release.assert_not_called()

    def test_success_resets_runtime_retry_count_and_does_not_wait(self):
        stream = self.stream([(True,self.image)]+[(False,None)]*2+[(True,self.image)]
                             +[(False,None)]*2+[(True,self.image)])
        self.assertIs(next(stream)[0],self.image)
        self.assertEqual(self.clock.delays,[])
        self.assertIs(next(stream)[0],self.image)
        self.assertIs(next(stream)[0],self.image)
        self.assertEqual(self.clock.delays,[.5]*4)

    def test_runtime_failure_budget_applies_even_within_warmup_window(self):
        stream = self.stream([(True,self.image)]+[(False,None)]*3)
        next(stream)
        with self.assertRaisesRegex(RuntimeError,'运行中途读帧失败.*连续 3 次'):
            next(stream)
        self.assertEqual(self.camera.read.call_count,4)
        self.assertEqual(self.clock.now,1)
        self.camera.release.assert_not_called()

    def test_zero_runtime_retries_is_supported(self):
        self.options = replace(self.options,read_retry_count=0)
        stream = self.stream([(False,None),(True,self.image),(False,None)])
        next(stream)  # First-frame warmup still retries with a zero runtime budget.
        with self.assertRaisesRegex(RuntimeError,'运行中途读帧失败'):
            next(stream)

    def test_none_and_empty_frames_are_retried(self):
        stream = self.stream([(True,None),(True,np.empty((0,0,3))),(True,self.image)])
        self.assertIs(next(stream)[0],self.image)
        self.assertEqual(self.camera.read.call_count,3)

    def test_stop_interrupts_retry_wait_without_releasing_camera(self):
        stream = self.stream([(False,None)])
        def wait(delay):
            self.clock.stopped = True
            return True
        self.clock.wait = wait
        with self.assertRaises(StopIteration):
            next(stream)
        self.camera.read.assert_called_once()
        self.camera.release.assert_not_called()

    def test_stop_during_read_discards_frame_and_never_retries(self):
        def read():
            self.clock.stopped = True
            return True,self.image
        stream = self.stream(read)
        with self.assertRaises(StopIteration):
            next(stream)
        self.camera.read.assert_called_once()
        self.assertEqual(self.clock.delays,[])
        self.camera.release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
