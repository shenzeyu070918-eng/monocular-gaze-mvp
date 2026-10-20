from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import numpy as np
from gaze_core.config import load_settings
from gaze_core.camera_io import read_camera_frames
from tools.list_cameras import probe_camera

class CameraSelectionTests(unittest.TestCase):
    def test_black_guard_waits_exposure_then_counts_consecutive_dark_frames(self):
        options = replace(load_settings(use_local=False).camera, index=1, black_frame_count=3)
        now = [0.]
        dark = np.zeros((6,8,3), np.uint8); bright = dark+100
        images = iter([dark,dark,dark,bright,dark,dark,dark])
        def read():
            now[0] += 1
            return True, next(images)
        camera = SimpleNamespace(read=read, release=Mock())
        frames = read_camera_frames(camera, options, monotonic=lambda:now[0])
        for _ in range(6): next(frames)
        with self.assertRaisesRegex(RuntimeError, 'index 1.*全黑.*camera.index'): next(frames)
        camera.release.assert_not_called()

    def test_diagnostic_reports_black_and_releases_camera(self):
        options = replace(load_settings(use_local=False).camera, black_frame_count=2)
        now = [0.]
        def read():
            now[0] += 1
            return True, np.zeros((6,8,3), np.uint8)
        camera = SimpleNamespace(read=read, release=Mock(),set=Mock(),isOpened=lambda:True)
        report = probe_camera(options, camera_factory=lambda _:camera, monotonic=lambda:now[0])
        self.assertEqual(report['resolution'], [8,6]);self.assertEqual(report['mean_brightness'],0)
        camera.release.assert_called_once()

if __name__ == '__main__': unittest.main()
