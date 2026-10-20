"""Chessboard fits and tool lifecycle use synthetic views and a fake camera."""
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from gaze_core.calibration import board_points, calibrate_views, diverse_enough, view_descriptor
from gaze_core.config import load_settings
from gaze_core.devices import load_device
from tools import calibrate_camera as tool


class CalibrationTests(unittest.TestCase):
    def run_camera_reads(self, reads=None):
        """Exercise the actual preview loop and shared reader, not a reader stub."""
        settings = load_settings(use_local=False)
        settings = replace(settings, camera=replace(settings.camera, warmup_timeout_s=.1,
                           read_retry_interval_s=.002, read_retry_count=2))
        camera = Mock()
        camera.isOpened.return_value = True
        if reads is None:
            camera.read.return_value = (False,None)
        else:
            camera.read.side_effect = reads
        output, error = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(tool,'load_settings',return_value=settings))
            stack.enter_context(patch.object(tool.cv2,'VideoCapture',return_value=camera))
            stack.enter_context(patch.object(tool.cv2,'findChessboardCorners',return_value=(False,None)))
            preview = stack.enter_context(patch.object(tool.cv2,'imshow'))
            stack.enter_context(patch.object(tool.cv2,'putText'))
            stack.enter_context(patch.object(tool.cv2,'waitKey',side_effect=[-1,ord('q')]))
            stack.enter_context(patch.object(tool.cv2,'destroyAllWindows'))
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(error))
            code = tool.main(['--device','synthetic','--corners','9','6','--square-mm','20'])
        camera.release.assert_called_once()
        return code, camera, preview.call_count, error.getvalue()

    def test_initial_and_intermittent_failed_reads_recover_in_preview(self):
        frame = np.zeros((480,640,3),np.uint8)
        code,camera,previews,error = self.run_camera_reads(
            [(False,None)]*2+[(True,frame)]+[(False,None)]*2+[(True,frame)])
        self.assertEqual(code,0,error)
        self.assertEqual(previews,2)
        self.assertEqual(camera.read.call_count,6)

    def test_never_a_frame_reports_permissions_and_occupancy_after_warmup(self):
        code,camera,previews,error = self.run_camera_reads()
        self.assertEqual(code,1)
        self.assertIn('从未读到画面',error)
        self.assertIn('终端的权限',error)
        self.assertIn('占用摄像头',error)
        self.assertNotIn('运行中途',error)
        self.assertEqual(previews,0)
        self.assertGreater(camera.read.call_count,1)

    def test_persistent_runtime_read_failure_has_distinct_error(self):
        frame = np.zeros((480,640,3),np.uint8)
        code,camera,previews,error = self.run_camera_reads([(True,frame)]+[(False,None)]*3)
        self.assertEqual(code,1)
        self.assertIn('运行中途读帧失败',error)
        self.assertNotIn('从未读到画面',error)
        self.assertEqual(previews,1)
        self.assertEqual(camera.read.call_count,4)

    def test_black_camera_preview_stops_with_selection_hint(self):
        settings = load_settings(use_local=False)
        settings = replace(settings,camera=replace(settings.camera,warmup_timeout_s=.001,black_frame_count=2))
        camera=Mock();camera.isOpened.return_value=True
        def read():
            # Fake elapsed exposure time without a real camera.
            import time
            time.sleep(.002)
            return True,np.zeros((6,8,3),np.uint8)
        camera.read.side_effect=read
        with patch.object(tool,'load_settings',return_value=settings),patch.object(tool.cv2,'VideoCapture',return_value=camera), \
             patch.object(tool.cv2,'findChessboardCorners',return_value=(False,None)),patch.object(tool.cv2,'imshow'), \
             patch.object(tool.cv2,'putText'),patch.object(tool.cv2,'waitKey',return_value=-1), \
             patch.object(tool.cv2,'destroyAllWindows'),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()) as error:
            result=tool.main(['--device','synthetic','--corners','9','6','--square-mm','20','--camera-index','1'])
        self.assertEqual(result,1);self.assertIn('index 1',error.getvalue());self.assertIn('全黑',error.getvalue())
        camera.release.assert_called_once()

    def synthetic_views(self):
        points = board_points((9,6),np.float64(20))
        matrix = np.array([[620.,0,320],[0,625,240],[0,0,1.]])
        distortion = np.array([.01,-.005,.001,0.,.0002])
        views = []
        for i in range(12):
            rotation = np.array([-.35+.06*i, .25*np.sin(i), .03*i])
            translation = np.array([-100+12*i,-80+8*(i%4),500+20*i],float)
            image, _ = cv2.projectPoints(points,rotation,translation,matrix,distortion)
            views.append(image)
        return views, matrix

    def test_synthetic_calibration_recovers_intrinsics_and_reports_pixel_rms(self):
        views, expected = self.synthetic_views()
        result, errors = calibrate_views(views,(9,6),np.float64(20),(640,480))
        np.testing.assert_allclose(result['matrix'],expected,rtol=1e-4,atol=.01)
        self.assertLess(result['reprojection_error_px'],.001)  # Synthetic, noiseless fixture only.
        self.assertEqual(len(errors),12)
        self.assertEqual(result['resolution'],[640,480])
        self.assertTrue(all(type(v) is float for row in result['matrix'] for v in row))

    def test_diversity_rejects_duplicates_and_requires_position_and_tilt_coverage(self):
        views,_ = self.synthetic_views()
        descriptor = view_descriptor(views[0],(9,6),(640,480),20)
        settings = replace(load_settings(use_local=False).calibration,views=3)
        self.assertFalse(diverse_enough([descriptor]*3,settings))
        self.assertFalse(diverse_enough([[0,0,.2,0,0],[1,1,.2,0,0],[.5,.5,.2,0,0]],settings))
        self.assertTrue(diverse_enough([[0,0,.2,0,0],[1,1,.2,.4,.4],[.5,.5,.2,0,0]],settings))

    def test_bad_board_and_invalid_corners_rejected(self):
        for corners,square in [((2,6),20),((9,6),True),((9,6),float('nan'))]:
            with self.assertRaises(ValueError):
                board_points(corners,square)
        with self.assertRaises(ValueError):
            calibrate_views([[[float('nan'),0]]]*3,(9,6),20,(640,480))

    def test_save_only_parameters_and_offset_comments(self):
        views,_ = self.synthetic_views()
        result,_ = calibrate_views(views,(9,6),20,(640,480))
        args = Namespace(screen_width_cm=30.,screen_height_cm=20.,screen_resolution=[1440,900],
                         horizontal_cm=2.,top_cm=1.)
        with tempfile.TemporaryDirectory() as directory:
            path = tool.save_device('synthetic',result,args,directory=directory)
            profile = load_device(path)
            self.assertEqual(profile.resolution,(640,480))
            self.assertEqual(profile.horizontal_cm,2)
            self.assertEqual(profile.top_cm,1)
            self.assertTrue(profile.measured)
            self.assertIn('右侧为正',path.read_text())
            self.assertIn('上沿上方为正',path.read_text())
            self.assertEqual([p.name for p in Path(directory).iterdir()],['synthetic.yaml'])

    def test_optional_images_only_under_data_and_no_default_directory(self):
        self.assertIsNone(tool.image_directory(None))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'只能保存'):
                tool.image_directory(directory)
            root = Path(directory)/'repo';root.mkdir()
            with patch.object(tool,'ROOT',root):
                self.assertEqual(tool.image_directory('data/calibration'),(root/'data/calibration').resolve())
                (root/'data/link').symlink_to(Path(directory))
                with self.assertRaises(ValueError):
                    tool.image_directory('data/link/leaked')

    def test_preview_actual_resolution_failed_fit_no_image_write_and_release(self):
        views,_ = self.synthetic_views()
        fake = Mock()
        fake.isOpened.return_value = True
        fake.read.return_value = (True,np.zeros((480,640,3),np.uint8))
        out = io.StringIO()
        with patch.object(tool.cv2,'VideoCapture',return_value=fake), \
             patch.object(tool.cv2,'findChessboardCorners',return_value=(True,views[0])), \
             patch.object(tool.cv2,'cornerSubPix',side_effect=lambda gray,points,*a:points), \
             patch.object(tool.cv2,'drawChessboardCorners'), patch.object(tool.cv2,'putText'), \
             patch.object(tool.cv2,'imshow'),patch.object(tool.cv2,'destroyAllWindows'), \
             patch.object(tool.cv2,'waitKey',side_effect=[32,32,32,13,ord('q')]), \
             patch.object(tool,'view_descriptor',side_effect=[np.zeros(5),np.ones(5),np.ones(5)*2]), \
             patch.object(tool,'calibrate_views',return_value=(dict(reprojection_error_px=2),[2]*3)), \
             patch.object(tool,'save_device') as save, patch.object(tool.cv2,'imwrite') as write, \
             redirect_stdout(out):
            code = tool.main(['--device','synthetic','--corners','9','6','--square-mm','20',
                              '--views','3','--width','1280','--height','960'])
        self.assertEqual(code,0)
        self.assertIn('实际采集分辨率 640×480',out.getvalue())
        self.assertIn('误差过大',out.getvalue())
        save.assert_not_called();write.assert_not_called();fake.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
