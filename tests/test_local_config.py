import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import yaml
import gaze_core.config as config

class LocalConfigurationTests(unittest.TestCase):
    def test_click_session_rate_and_baseline_age_validation(self):
        base=config.load_settings(use_local=False)
        self.assertIsNone(base.click_session.max_fps)
        self.assertEqual(base.click_session.recent_baseline_max_age_s,300)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.yaml'
            data=yaml.safe_load(config.DEFAULT_CONFIG.read_text())
            data['click_session']['max_fps']=23
            path.write_text(yaml.safe_dump(data))
            self.assertEqual(config.load_settings(path).click_session.max_fps,23)
            for value in (True,0,-1,float('nan')):
                data['click_session']['max_fps']=value
                path.write_text(yaml.safe_dump(data))
                with self.assertRaises(ValueError):config.load_settings(path)

    def test_thread_alias_normalizes_each_layer_before_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'default.yaml';path.write_text(config.DEFAULT_CONFIG.read_text())
            local=path.with_name('local.yaml')
            with patch.object(config,'DEFAULT_CONFIG',path):
                for key in ('cpu_threads','num_threads'):
                    local.write_text('inference:\n  '+key+': 4\n  max_fps: 7\n  device: mps\n')
                    settings=config.load_settings(path)
                    self.assertEqual((settings.inference.num_threads,settings.inference.cpu_threads),(4,4))
                    self.assertEqual(settings.inference.max_fps,7)
                    self.assertEqual(settings.inference.device,'mps')
                    self.assertEqual(settings.key_sources['inference.num_threads'],str(local.resolve()))
                local.write_text('inference:\n  max_fps: null\n')
                self.assertIsNone(config.load_settings(path).inference.max_fps)
                local.write_text('inference:\n  cpu_threads: 2\n  num_threads: 4\n')
                with self.assertRaisesRegex(ValueError,'冲突'):config.load_settings(path)
                for key,value in (('num_threads','true'),('max_fps','true'),('max_fps','0'),('max_fps','.nan'),('max_fps','-1')):
                    local.write_text('inference:\n  '+key+': '+value+'\n')
                    with self.assertRaises(ValueError):config.load_settings(path)
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
