import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import yaml
import gaze_core.config as config

class LocalConfigurationTests(unittest.TestCase):
    def test_recursive_merge_scalar_null_and_array(self):
        base = {'camera': {'index': 0, 'width': 640}, 'device_file': 'old', 'values': [1,2]}
        result = config.merge_config(base, {'camera': {'index': np.int64(1)}, 'device_file': None, 'values': [3]})
        self.assertEqual(result, {'camera': {'index': 1, 'width':640}, 'device_file':None, 'values':[3]})
        self.assertEqual(base['camera']['index'], 0)

    def test_local_provenance_and_explicit_config_isolation(self):
        original = config.DEFAULT_CONFIG
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'default.yaml'
            path.write_text(original.read_text())
            path.with_name('local.yaml').write_text('camera:\n  index: 1\n  undistort: false\n')
            with patch.object(config, 'DEFAULT_CONFIG', path):
                settings = config.load_settings(path)
                self.assertEqual(settings.camera.index, 1)
                self.assertFalse(settings.camera.undistort)
                self.assertEqual(settings.camera.width, 640)
                self.assertEqual(settings.key_sources['camera.index'], str(path.with_name('local.yaml').resolve()))
                self.assertEqual(settings.key_sources['camera.width'], str(path.resolve()))
                self.assertEqual(config.load_settings(path, use_local=False).camera.index, 0)
            self.assertEqual(config.load_settings(path).camera.index, 0)

    def test_invalid_merged_value_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'default.yaml';path.write_text(config.DEFAULT_CONFIG.read_text())
            path.with_name('local.yaml').write_text('camera:\n  index: -1\n')
            with patch.object(config, 'DEFAULT_CONFIG', path), self.assertRaises(ValueError):
                config.load_settings(path)

    def test_filter_parameters_merge_and_invalid_units_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'default.yaml';path.write_text(config.DEFAULT_CONFIG.read_text())
            local=path.with_name('local.yaml')
            local.write_text('filter:\n  beta: 0.2\nfixation:\n  angular_threshold_deg_s: 180\n')
            with patch.object(config,'DEFAULT_CONFIG',path):
                settings=config.load_settings(path)
                self.assertEqual(settings.filter.beta,.2);self.assertEqual(settings.filter.min_cutoff,2.)
                self.assertEqual(settings.fixation.angular_threshold_deg_s,180.)
                local.write_text('filter:\n  min_cutoff: 0\n')
                with self.assertRaises(ValueError):config.load_settings(path)
                local.write_text('fixation:\n  fallback_threshold_norm_s: true\n')
                with self.assertRaises(ValueError):config.load_settings(path)

if __name__ == '__main__': unittest.main()
