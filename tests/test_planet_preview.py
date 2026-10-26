"""Visual config + read-only loopback server. No models or input devices."""
from dataclasses import replace
import json
import threading
import tempfile
from pathlib import Path
import unittest
import urllib.error
import urllib.request

from gaze_core.config import load_settings
from gaze_core.galaxy import GalaxySettings
from gaze_core.planet import PlanetVisualSettings,PlanetInteractionSettings
from tools.preview_galaxy import create_server


class PlanetVisualTests(unittest.TestCase):
    def test_visual_counts_and_nested_config_are_separate_from_live_controls(self):
        options=load_settings(use_local=False).galaxy
        self.assertEqual(options.visual.planet_particles,80000);self.assertEqual(options.visual.ring_particles,150000)
        self.assertEqual(options.visual,PlanetVisualSettings())
        self.assertEqual(options.particle_count,50000)  # Live scene awaits visual approval.
        changed=replace(options,visual={'camera_pitch_deg':35,'planet_colors':['#000000','#123456','#ffffff']})
        self.assertEqual(changed.visual.camera_pitch_deg,35);self.assertIsInstance(changed.visual,PlanetVisualSettings)
        self.assertEqual(changed.hand_confidence,options.hand_confidence)

    def test_interaction_config_merges_and_rejects_invalid_limits(self):
        options=load_settings(use_local=False).galaxy
        self.assertEqual(options.interaction,PlanetInteractionSettings())
        self.assertEqual(replace(options,interaction={'corner_ratio':.1}).interaction.corner_ratio,.1)
        for fields in ({'corner_ratio':.6},{'zoom_dwell_ms':0},{'yaw_range_deg':90},{'min_hand_scale':2},{'well_attraction':float('inf')},{'text_yaw_range_deg':46},{'text_pitch_range_deg':-1},{'galaxy_scatter_distance':float('nan')},{'text_scatter_height':-1}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):replace(PlanetInteractionSettings(),**fields)

    def test_bad_visual_geometry_counts_colors_and_nonfinite_values_are_rejected(self):
        for fields in ({'planet_particles':True},{'ring_particles':1.5},{'planet_particles':-1},
                       {'galaxy_close_distance':11},{'text_close_distance':8},{'text_distance':0},{'ring_inner_ratio':.9},{'ring_outer_ratio':1.1},{'close_distance':1.},
                       {'bloom_strength':float('nan')},{'planet_colors':['red']},{'camera_pitch_deg':90},{'ring_gap_width':10},{'ring_gap_depth':1},{'low_particle_count':1}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):replace(PlanetVisualSettings(),**fields)


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.server=create_server(GalaxySettings(),0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_static_preview_config_has_no_private_or_camera_connection(self):
        self.assertEqual(self.server.server_address[0],'127.0.0.1')
        with urllib.request.urlopen(self.origin+'/?preview',timeout=2) as response:
            self.assertIn(b'/assets/entry.mjs',response.read())
            self.assertEqual(response.headers['Permissions-Policy'],'camera=(), microphone=(), display-capture=()')
        with urllib.request.urlopen(self.origin+'/preview-config',timeout=2) as response:options=json.load(response)
        self.assertEqual(set(options),{'interaction','stale_ms','transition_s','text','visual','pixel_ratio','intro_s','low_fps','low_fps_s','min_pixel_ratio'})
        self.assertEqual(options['visual']['planet_particles']+options['visual']['ring_particles'],230000)
        for path in ('/ws','/reference/../config/local.yaml','/assets/../../config/default.yaml','/reference/secrets.json','/render-check/../../config/local.yaml','/render-check/secrets.json'):
            with self.subTest(path=path),self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(self.origin+path,timeout=2)
            self.assertEqual(caught.exception.code,404)
        with urllib.request.urlopen(self.origin+'/assets/planet-renderer.mjs',timeout=2) as response:
            self.assertEqual(response.headers['Content-Type'],'text/javascript')

    def test_only_named_interaction_gallery_files_are_served(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);checks=root/'data/reference/render_check';checks.mkdir(parents=True)
            self.server.RequestHandlerClass.keywords['root']=root
            (checks/'index.html').write_text('synthetic preview',encoding='utf-8')
            (checks/'galaxy-scatter.png').write_bytes(b'fake-png')
            with urllib.request.urlopen(self.origin+'/render-check') as response:self.assertEqual(response.read(),b'synthetic preview')
            with urllib.request.urlopen(self.origin+'/render-check/galaxy-scatter.png') as response:
                self.assertEqual(response.headers['Content-Type'],'image/png');self.assertEqual(response.read(),b'fake-png')
            (checks/'private.json').write_text('{}')
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(self.origin+'/render-check/private.json')
            self.assertEqual(caught.exception.code,404)

    def test_wrong_host_is_rejected(self):
        request=urllib.request.Request(self.origin,headers={'Host':'untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request,timeout=2)
        self.assertEqual(caught.exception.code,403)


if __name__=='__main__':unittest.main()
