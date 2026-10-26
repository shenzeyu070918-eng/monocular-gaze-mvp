"""Real loopback host/WS with synthetic numeric inputs; no camera or browser."""
import hashlib
import json
from pathlib import Path
import socket
import time
import unittest
import urllib.error
import urllib.request

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from gaze_core.config import ROOT
from gaze_core.galaxy import GalaxySettings
from overlay.galaxy_bridge import GalaxyBridge
from overlay.mapping import ScreenRect
from test_galaxy import geometry


def packet(seq=0):
    return dict(v=3,seq=seq,capture_us=0,completed_us=0,viewport=1,gaze_valid=1,gaze_state=0,
                fixation=1,xy=[10.,20.],radius=[50.,50.],control=0,hands=[],distance=0.,
                pattern=0,scene_revision=0,corner=-1,corner_dwell_ms=0.,corner_progress=0.,corner_state=0,corner_reset=0,intro=1,hand_ms=0.,hand_available=0,hand_detected=0,hand_openings=[],hand_error=0)


class GalaxyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge=GalaxyBridge(GalaxySettings(),ScreenRect(0,0,1512,982,2))
        self.bridge.start();self.addCleanup(self.bridge.close)
        deadline=time.monotonic()+5
        while self.bridge.server is None or not self.bridge.server.started:
            if self.bridge.error:raise AssertionError(self.bridge.error)
            if time.monotonic()>deadline:raise AssertionError('loopback server not started')
            time.sleep(.01)
        self.origin=f'http://127.0.0.1:{self.bridge.port}'

    def client(self,token=None):
        ws=self.enterContext(connect(self.origin.replace('http:','ws:')+'/ws',origin=self.origin,proxy=None,open_timeout=2))
        ws.send(json.dumps(dict(type='auth',token=self.bridge.token if token is None else token)))
        return ws

    def receive(self,ws,kind):
        for _ in range(12):
            row=json.loads(ws.recv(timeout=2))
            if row['type']==kind:return row
        self.fail('missing '+kind)

    def test_loopback_only_host_headers_local_assets_and_shutdown(self):
        with urllib.request.urlopen(self.origin,timeout=2) as r:
            self.assertEqual(r.status,200);self.assertIn(b'/assets/entry.mjs',r.read())
            self.assertEqual(r.headers['Permissions-Policy'],'camera=(), microphone=(), display-capture=()')
            self.assertIn("connect-src 'self' ws://127.0.0.1:",r.headers['Content-Security-Policy'])
        request=urllib.request.Request(self.origin,headers={'Host':'untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request,timeout=2)
        self.assertEqual(caught.exception.code,403)
        with urllib.request.urlopen(self.origin+'/assets/vendor/three/build/three.module.js',timeout=2) as r:self.assertEqual(r.status,200)
        port=self.bridge.port;self.bridge.close()
        self.assertFalse(self.bridge.thread.is_alive());self.assertFalse(self.bridge.token)
        with socket.socket() as sock:self.assertNotEqual(sock.connect_ex(('127.0.0.1',port)),0)

    def test_token_origin_and_second_client_rejected_without_disturbing_first(self):
        self.assertFalse(self.bridge.authenticated('不合法',self.origin))
        with self.assertRaises(InvalidStatus):
            connect(self.origin.replace('http:','ws:')+'/ws',origin='https://example.org',proxy=None,open_timeout=2)
        bad=self.client('wrong')
        with self.assertRaises(ConnectionClosed):bad.recv(timeout=2)
        good=self.client();self.receive(good,'config')
        with self.assertRaises(InvalidStatus):self.client()
        good.send(json.dumps(dict(type='geometry',**geometry())))
        self.receive(good,'ready');self.assertIsNotNone(self.bridge.context()[0])
        other=GalaxyBridge(GalaxySettings(),self.bridge.screen)
        self.assertNotEqual(other.token,self.bridge.token)

    def test_keyboard_scene_step_and_dwell_use_one_owner_and_geometry_resets(self):
        now=[0];self.bridge.clock=lambda:now[0]
        ws=self.client();self.receive(ws,'config');ws.send(json.dumps(dict(type='geometry',**geometry())))
        revision=self.receive(ws,'ready')['viewport']
        ws.send(json.dumps(dict(type='scene_step',direction=1,viewport=revision)))
        status=self.receive(ws,'scene')['data'];self.assertEqual(status['pattern'],1)
        self.assertEqual(status['scene_revision'],1)
        for ms in range(1200,2701,100):
            current=self.bridge.scene_feed([-20,20],[50,50],ms*1_000_000,True,viewport_revision=revision)
        self.assertEqual(current['pattern'],2);self.assertEqual(current['corner_progress'],1)
        now[0]=3_000_000_000
        ws.send(json.dumps(dict(type='scene_step',direction=-1,viewport=revision)))
        self.assertEqual(self.receive(ws,'scene')['data']['pattern'],1)
        ws.send(json.dumps(dict(type='geometry',**geometry())))
        new_revision=self.receive(ws,'ready')['viewport']
        self.assertIsNone(self.bridge.step_scene(1,revision))
        current=self.bridge.scene_feed([10,10],[50,50],4_000_000_000,True,viewport_revision=new_revision)
        self.assertEqual(current['pattern'],0);self.assertEqual(current['scene_revision'],0)
        # Old inference completion cannot mutate the new viewport's state.
        old=self.bridge.scene_feed([10,10],[50,50],9_000_000_000,True,viewport_revision=revision)
        self.assertEqual(old['pattern'],0)
        ws.send(json.dumps(dict(type='scene_step',direction=1,viewport=new_revision,key='ArrowRight')))
        with self.assertRaises(ConnectionClosed):ws.recv(timeout=2)

    def test_notch_geometry_latest_frame_and_numeric_whitelist(self):
        ws=self.client();config=self.receive(ws,'config')
        self.assertNotIn('hand_model',config['options'])
        ws.send(json.dumps(dict(type='geometry',**geometry(fullscreen=0))))
        self.receive(ws,'geometry_error');self.assertIsNone(self.bridge.context()[0])
        ws.send(json.dumps(dict(type='geometry',**geometry())))
        ready=self.receive(ws,'ready');self.assertGreater(ready['viewport'],0)
        self.assertEqual(self.bridge.context()[0].window_origin,(0.,32.))
        for i in range(200):self.bridge.publish(packet(i))
        latest=self.receive(ws,'frame')['data']
        while latest['seq']!=199:latest=self.receive(ws,'frame')['data']
        self.assertGreater(self.bridge.dropped,0)
        self.assertNotIn('image',latest)
        ws.send(json.dumps(dict(type='focus',active=1)))
        with self.assertRaises(ValueError):self.bridge.publish(dict(packet(),image='data:image/png'))
        ws.send(json.dumps(dict(type='performance',fps=60,p90_ms=17,frames=60,long_frames=1,particles=50000,pixel_ratio=1,downgrades=0,render_median_ms=.2,render_p90_ms=.3)))
        ws.send(json.dumps(dict(type='geometry',**geometry())))
        self.receive(ws,'ready');self.assertEqual(len(self.bridge.telemetry),1)
        self.assertTrue(self.bridge.ui_focused)
        ws.send(json.dumps(dict(type='focus',active=0)))
        ws.send(json.dumps(dict(type='geometry',**geometry())))
        self.receive(ws,'ready');self.assertFalse(self.bridge.ui_focused)
        ws.send(json.dumps(dict(type='heartbeat',keyboard=123)))
        with self.assertRaises(ConnectionClosed):ws.recv(timeout=2)


class GalaxyAssetTests(unittest.TestCase):
    def test_pinned_vendor_hashes_and_browser_has_no_camera_api_or_remote_import(self):
        root=ROOT/'static/galaxy';vendor=root/'vendor/three'
        manifest=json.loads((vendor/'manifest.json').read_text())
        self.assertEqual(manifest['version'],'0.180.0');self.assertEqual(manifest['license'],'MIT')
        for name,expected in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((vendor/name).read_bytes()).hexdigest(),expected,name)
        self.assertIn('MIT License',(vendor/'LICENSE').read_text())
        for name in ('app.mjs','logic.mjs','renderer.mjs','planet-renderer.mjs','planet-controls.mjs','scene-controls.mjs','corner-indicator.mjs','planet-shaders.mjs','particle-fields.mjs','index.html'):
            source=(root/name).read_text()
            self.assertNotIn('getUserMedia',source);self.assertNotIn('https://',source)


if __name__=='__main__':unittest.main()
