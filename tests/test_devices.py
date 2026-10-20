"""Synthetic calibration values only: no real device or claimed accuracy."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import yaml

from gaze_core.config import DEFAULT_CONFIG, load_settings
from gaze_core.devices import parse_device, load_device, scale_intrinsics
from gaze_core.geometry import intersect_screen


def synthetic_device():
    return dict(schema_version=1, name='synthetic-test',
                camera=dict(matrix=[[610,0,315],[0,620,238],[0,0,1]], distortion=[.08,-.01,.002,-.003,.001],
                            resolution=[640,480], reprojection_error_px=.25, calibrated_at='2026-10-15T00:00:00+00:00'),
                screen=dict(width_cm=30., height_cm=20., measured=True, resolution=[1440,900]),
                camera_offset_cm=dict(horizontal=2., vertical=1.))


class DeviceTests(unittest.TestCase):
    def test_load_and_resolve_relative_to_config_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'devices').mkdir()
            path = root/'devices/synthetic-test.yaml'
            path.write_text(yaml.safe_dump(synthetic_device()), encoding='utf-8')
            profile = load_device(path)
            self.assertEqual(profile.filename, path.name)
            self.assertEqual(profile.resolution, (640,480))
            config = yaml.safe_load(DEFAULT_CONFIG.read_text())
            config['device_file'] = 'devices/synthetic-test.yaml'
            settings_path = root/'settings.yaml'
            settings_path.write_text(yaml.safe_dump(config))
            settings = load_settings(settings_path)
            self.assertEqual(settings.device_profile, profile)
            self.assertEqual((settings.geometry.width_cm, settings.geometry.height_cm), (30,20))
            self.assertTrue(settings.geometry.measured)
            self.assertEqual((settings.geometry.horizontal_cm, settings.geometry.top_cm), (2,1))
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                load_settings(settings_path)  # Configured-but-missing is not an estimated fallback.

    def test_no_device_has_no_fabricated_screen_size(self):
        settings = load_settings(use_local=False)
        self.assertIsNone(settings.device_profile)
        self.assertIsNone(settings.geometry.width_cm)
        self.assertIsNone(settings.geometry.height_cm)
        self.assertFalse(settings.geometry.measured)

    def test_invalid_device_data_rejected(self):
        cases = [('camera','matrix',[[0,0,0],[0,1,0],[0,0,1]]),
                 ('camera','distortion',[0,0,float('nan'),0,0]),
                 ('camera','distortion',[0]*4), ('camera','resolution',[640,True]),
                 ('camera','calibrated_at','not-a-date'), ('camera','reprojection_error_px',-1),
                 ('screen','width_cm',None), ('screen','measured',1),
                 ('screen','resolution',[0,900]), ('camera_offset_cm','horizontal',True)]
        for section, field, value in cases:
            data = synthetic_device(); data[section][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_device(data, 'test.yaml')

    def test_numpy_and_proportional_intrinsics_scaling(self):
        data = synthetic_device()
        data['camera']['matrix'][0][0] = np.float64(610)
        data['camera']['resolution'] = [np.int64(640),np.int64(480)]
        profile = parse_device(data, 'numpy-test.yaml')
        actual = scale_intrinsics(profile.matrix, profile.resolution, [np.int64(1280),np.int64(960)])
        expected = np.array([[1220,0,630],[0,1240,476],[0,0,1]])
        np.testing.assert_allclose(actual, expected)
        self.assertEqual(profile.matrix[0][0], 610)  # Input not mutated.
        with self.assertRaisesRegex(ValueError, 'aspect_ratio_mismatch'):
            scale_intrinsics(profile.matrix, profile.resolution, [640,360])

    def test_offset_signs_and_unclipped_camera_axis_intersection(self):
        origin = np.array([0.,0.,.5])
        ray = np.array([0.,0.,-1.])
        raw = intersect_screen(origin, ray, 30, 20, top_cm=2, horizontal_cm=np.float64(2))
        self.assertAlmostEqual((raw[0]-.5)*30, 2)  # User-facing right.
        self.assertAlmostEqual(raw[1]*20, -2)  # Above the top edge, unclipped.
        opposite = intersect_screen(origin, ray, 30,20,top_cm=-2,horizontal_cm=-2)
        self.assertAlmostEqual((opposite[0]-.5)*30, -2)
        self.assertAlmostEqual(opposite[1]*20, 2)
        self.assertIsNone(intersect_screen(origin,ray,None,None))


if __name__ == '__main__':
    unittest.main()
