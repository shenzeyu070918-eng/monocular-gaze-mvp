"""Synthetic display fields, not captured screen/camera content."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from gaze_core.config import HeatmapSettings,load_settings
from gaze_core.heatmap import rgba_grid
from gaze_core.heatmap_render import render_heatmap,RenderLog


class HeatmapRenderTests(unittest.TestCase):
    def test_resize_float_before_color_no_mutation_and_numpy(self):
        grid=np.array([[0,0,1],[0,.3,0]],np.float64);before=grid.copy()
        high=cv2.resize(grid.astype(np.float32),(120,80),interpolation=cv2.INTER_CUBIC)
        high=np.clip(high,0,grid.max())
        image=render_heatmap(grid,(240,160),dither_strength=0)
        np.testing.assert_array_equal(image,rgba_grid(high,.4,.15))
        np.testing.assert_array_equal(grid,before)
        self.assertEqual(image.shape,(80,120,4));self.assertEqual(image.dtype,np.uint8)

    def test_dither_is_static_sub_lsb_transparent_and_bounded(self):
        grid=np.tile(np.linspace(0,1,400),(100,1))
        plain=render_heatmap(grid,(800,200),dither_strength=0)
        image=render_heatmap(grid,(800,200),dither_strength=np.float64(.5))
        np.testing.assert_array_equal(image,render_heatmap(grid,(800,200),dither_strength=.5))
        np.testing.assert_array_equal(image[...,:3],plain[...,:3])
        self.assertLessEqual(np.abs(image[...,3].astype(int)-plain[...,3]).max(),1)
        self.assertLessEqual(image[...,3].max(),102)
        self.assertTrue((image[:,:60,3]==0).all())
        self.assertTrue(np.any(image[...,3]!=plain[...,3]))
        self.assertTrue((render_heatmap(np.zeros((2,3)),(20,16))[...,3]==0).all())

    def test_dither_alpha_is_monotone_at_fixed_tile_phase(self):
        # Spatial dither need not be monotone across adjacent Bayer phases.
        # At the same pixel phase, increasing heat must never decrease alpha.
        alphas=[]
        for level in np.linspace(.14,.31,150):
            grid=np.array([[level,1.],[level,1.]])
            alphas.append(rgba_grid(grid,.4,.15,dither_strength=.5)[0,0,3])
        self.assertTrue(np.all(np.diff(np.array(alphas,int))>=0))
        self.assertLessEqual(np.max(np.diff(np.array(alphas,int))),1)

    def test_options_and_local_override_validate(self):
        options=load_settings(use_local=False).heatmap
        self.assertEqual(options.render_scale,.5);self.assertEqual(options.dither_strength,.5)
        for kwargs in (dict(render_scale=0),dict(render_scale=2),dict(dither_strength=-.1),dict(dither_strength=2),dict(dither_strength=True),dict(dither_strength=np.nan)):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):HeatmapSettings(**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            from gaze_core import config
            default=Path(tmp)/'default.yaml';default.write_text(config.DEFAULT_CONFIG.read_text())
            default.with_name('local.yaml').write_text('heatmap:\n  dither_strength: 0\n')
            with patch.object(config,'DEFAULT_CONFIG',default):self.assertEqual(load_settings(default).heatmap.dither_strength,0)

    def test_numeric_render_log_is_lazy_and_writes_each_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'render';log=RenderLog(folder)
            self.assertFalse(folder.exists());self.assertIsNone(log.path)
            image=render_heatmap(np.ones((3,4)),(40,30))
            for t in (.1,.2):log.record(t,image,HeatmapSettings())
            rows=[json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertEqual(len(rows),2);self.assertEqual(rows[1]['render_ms'],.2)
            self.assertEqual(set(rows[0]),{'render_ns','render_ms','width','height','render_scale','dither_strength'})
            self.assertEqual(log.count,2);log.close()

    def test_compare_tool_emits_three_versions_same_crop_from_final_check_readonly(self):
        from gaze_core.config import ROOT
        from tools.compare_heatmap_render import main
        from test_session_heatmap import recording
        import io
        from contextlib import redirect_stdout
        output_root=ROOT/'data/heatmaps';output_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_root) as folder:
            source=Path(folder)/'synthetic.jsonl';recording(source);before=source.read_bytes()
            destination=Path(folder)/'compare'
            with redirect_stdout(io.StringIO()):self.assertEqual(main([str(source),'--output',str(destination)]),0)
            self.assertEqual(source.read_bytes(),before)
            manifest=json.loads((destination/'manifest.json').read_text())
            self.assertEqual(manifest['targets'],14)
            for name in ('synthetic','recorded_final_check'):
                info=json.loads((destination/name/'comparison.json').read_text())
                self.assertEqual(info['dither_strength'],.5)
                for version in ('before','after_no_dither','after_dither'):
                    self.assertTrue((destination/name/(version+'_crop_4x.png')).exists())
                self.assertTrue((destination/name/'comparison.png').exists())


if __name__=='__main__':unittest.main()
