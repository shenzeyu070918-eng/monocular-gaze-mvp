from dataclasses import replace
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from gaze_core.config import load_settings,OverlaySettings
import gaze_core.config as config
from overlay.mapping import ScreenRect


class MappingTests(unittest.TestCase):
    def test_full_screen_corners_with_all_dpi_scales(self):
        for scale in (1,1.25,1.5,2):
            screen=ScreenRect(-1512,30,1512,982,scale)
            self.assertEqual(screen.global_position((0,0)),(-1512,30))
            self.assertEqual(screen.global_position((1,1)),(-1,1011))
            self.assertEqual(screen.local((1,0)),(1511,0))
            self.assertEqual(screen.local((0,1)),(0,981))
            self.assertEqual(screen.local((np.float64(.5),np.float64(.5))),(755.5,490.5))

    def test_unclipped_coordinates_and_physical_radius(self):
        screen=ScreenRect(0,0,1500,1000,2)
        self.assertLess(screen.local((-.1,1.2))[0],0)
        self.assertGreater(screen.local((-.1,1.2))[1],999)
        g=replace(load_settings(use_local=False).geometry,width_cm=30,height_cm=20,measured=True)
        self.assertEqual(screen.radii(2,g,28),(100,100))
        self.assertEqual(screen.radii(2,replace(g,measured=False),28),(28,28))
        for point in ([True,0],[np.nan,0],[0,np.inf]):
            with self.assertRaises(ValueError):screen.local(point)

    def test_options_validate_and_local_override_is_allowed(self):
        for values in (dict(opacity=2),dict(stale_ms=0),dict(refresh_ms=True),dict(idle_fps=-1),dict(color='red'),
                       dict(label_position='center'),dict(label_autohide_s=-1),dict(label_autohide_s=True),
                       dict(label_autohide_s=float('nan')),dict(label_bottom_inset_px=-1),dict(label_padding_px=-1),
                       dict(label_debounce_s=-1),dict(label_debounce_s=True),dict(label_debounce_s=float('inf')),
                       dict(task_radius_px=0),dict(profile_poll_s=0),dict(profile_name='../bad'),
                       dict(tray_check_delay_ms=True),dict(web_url='https://example.com')):
            with self.assertRaises(ValueError):OverlaySettings(**values)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'default.yaml';path.write_text(config.DEFAULT_CONFIG.read_text())
            path.with_name('local.yaml').write_text('overlay:\n  radius_cm: 3.5\n  idle_fps: 2.0\n  label_position: bottom_right\n  label_autohide_s: 0\n  label_debounce_s: 0\n')
            with patch.object(config,'DEFAULT_CONFIG',path):
                settings=load_settings(path)
            self.assertEqual(settings.overlay.radius_cm,3.5)
            self.assertEqual(settings.overlay.idle_fps,2.)
            self.assertEqual(settings.overlay.stale_ms,300.)
            self.assertEqual(settings.overlay.label_position,'bottom_right')
            self.assertEqual(settings.overlay.label_autohide_s,0.)
            self.assertEqual(settings.overlay.label_debounce_s,0.)


if __name__=='__main__':unittest.main()
