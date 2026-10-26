"""Galaxy integration seams: synthetic camera, model, browser and Qt window."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import test_demo_practice
from test_capture_numeric import Camera,Engine
from gaze_core.capture import CameraPipeline
from gaze_core.config import load_settings
from gaze_core.demo_frame import DemoFrameSlot
from overlay.galaxy_session import GalaxySession
from overlay.galaxy_bridge import GalaxyBridge
from overlay.galaxy_browser import GalaxyBrowser
from overlay.mapping import ScreenRect
from overlay.state import RingView


class GalaxyLifecycleTests(unittest.TestCase):
    def fake_galaxy(self):
        s=SimpleNamespace(settings=load_settings(use_local=False),screen=ScreenRect(0,0,1512,982),
                          controller=SimpleNamespace(demo_frames=Mock()))
        bridge=Mock();bridge.server=SimpleNamespace(started=True);bridge.error=None;bridge.started_ns=1
        bridge.telemetry=[];bridge.dropped=0;bridge.drain.return_value=[]
        browser=Mock();browser.error=None;browser.fallback=False;browser.process=None
        galaxy=GalaxySession(s,(40,40),(0,0),bridge_factory=lambda *_:bridge,browser_factory=lambda *_:browser)
        def close():
            with tempfile.TemporaryDirectory() as directory,patch('overlay.galaxy_session.ROOT',Path(directory)):
                galaxy.close();galaxy.cleanup.join(3)
        self.addCleanup(close)
        return galaxy,bridge,browser

    def test_demo_debug_reaches_dedicated_chrome_url_query_before_token_removal(self):
        from urllib.parse import urlsplit,parse_qs
        for debug in (False,True):
            with self.subTest(debug=debug):
                options=load_settings(use_local=False).galaxy;screen=ScreenRect(0,0,1512,982)
                bridge=GalaxyBridge(options,screen);bridge.port=8765;bridge.debug=debug
                browser=GalaxyBrowser(bridge,screen,options);browser._stop.set()
                process=Mock();process.wait.return_value=0
                with patch('overlay.galaxy_browser.chrome_executable',return_value='/synthetic/chrome'),patch('overlay.galaxy_browser.subprocess.Popen',return_value=process) as launch:
                    browser._run()
                url=next(a.removeprefix('--app=') for a in launch.call_args.args[0] if a.startswith('--app='))
                parsed=urlsplit(url);self.assertEqual(parsed.fragment,bridge.token)
                self.assertEqual('debug' in parse_qs(parsed.query,keep_blank_values=True),debug)

    def test_resize_long_after_start_gets_fresh_geometry_timeout(self):
        now=[0.]
        with patch('overlay.galaxy_session.time.monotonic',side_effect=lambda:now[0]):
            galaxy,bridge,_=self.fake_galaxy();self.assertEqual(galaxy.poll(),[])
            now[0]=100.;bridge.started_ns=None
            self.assertEqual(galaxy.poll(),[])
            now[0]=129.;self.assertEqual(galaxy.poll(),[])
            now[0]=131.;self.assertIn('超时',galaxy.poll()[0][1])

    def test_disconnect_waits_for_owned_browser_exit_else_offers_fallback(self):
        for normal in (False,True):
            now=[0.]
            with patch('overlay.galaxy_session.time.monotonic',side_effect=lambda:now[0]):
                galaxy,bridge,browser=self.fake_galaxy();galaxy.poll()
                bridge.drain.return_value=[('disconnected',None)]
                self.assertEqual(galaxy.poll(),[]);bridge.drain.return_value=[]
                if normal:
                    browser.process=Mock();browser.process.poll.return_value=0
                now[0]=.51
                self.assertEqual(galaxy.poll(),[('stop' if normal else 'disconnected',None)])

    def test_same_camera_capture_clock_display_and_worker_only_model_release(self):
        camera=Camera();slot=DemoFrameSlot();calls=[]
        class Processor:
            error=None
            def process(self,frame,data):
                calls.append((threading.current_thread().name,frame.captured_ns,frame.image.shape,data))
            def close(self):calls.append(('close',threading.current_thread().name))
        display=Mock();display.process.side_effect=lambda data,**kw:dict(position=[.4,.5],state='fixation')
        processor=Processor();slot.set(processor);factory=Mock(return_value=camera);raw=[]
        settings=load_settings(use_local=False)
        pipeline=CameraPipeline(settings,camera_factory=factory,engine_factory=Engine,display_pipeline=display,
                                result_sink=raw.append,demo_processor=slot)
        self.addCleanup(pipeline.close)
        pipeline.start();deadline=time.monotonic()+3
        while pipeline.report()['predicted_frames']<3:
            self.assertLess(time.monotonic(),deadline);pipeline.wait_for_result(timeout=.1)
        pipeline.pause();factory.assert_called_once()
        self.assertEqual(calls[-1],('close','gaze-inference'))
        for name,stamp,shape,data in calls[:-1]:
            self.assertEqual(name,'gaze-inference');self.assertGreater(stamp,0)
            self.assertEqual(shape,(6,8,3));self.assertEqual(data['display']['position'],[.4,.5])
        self.assertTrue(raw);self.assertTrue(all('display' not in p.data for p in raw))
        self.assertEqual(camera.released,'gaze-capture')

    def manager(self):
        return test_demo_practice.DemoPracticeTests.make(self)

    def test_practice_before_browser_repractice_and_exit_restore(self):
        manager,s,overlay,panel,now=self.manager()
        manager.start('galaxy',object());window=manager.window;window.hide=Mock()
        with patch('overlay.galaxy_session.GalaxySession') as factory:
            test_demo_practice.DemoPracticeTests.practice(self,manager,now)
            factory.assert_not_called()
            now[0]=1_600_000_000
            manager.tick(None,RingView(None,0.,'',False),now[0],active=True,eligible=False)
            factory.assert_called_once();window.hide.assert_called_once()
            for actual,expected in zip(factory.call_args.args[2],(.02,-.03)):self.assertAlmostEqual(actual,expected)
            browser=factory.return_value;browser.poll.return_value=[]
            manager.tick(None,RingView(None,0.,'',False),now[0],active=True,eligible=False)
            browser.poll.assert_called_once()
            manager.repractice();browser.close.assert_called_once();self.assertEqual(manager.run.phase,'practice')
            self.assertIsNone(manager.galaxy);window.open.assert_called()
            manager.cancel();self.assertFalse(manager.active);overlay.setVisible.assert_called_with(True)
            s.set_demo_active.assert_called_with(False);s.controller.set_matrix.assert_not_called()

    # The existing practice helper calls these methods on its test instance.
    center=test_demo_practice.DemoPracticeTests.center
    tick=test_demo_practice.DemoPracticeTests.tick

    def test_browser_exit_and_condition_change_restore_without_profile_write(self):
        for cause in ('stop','conditions_changed'):
            manager,s,overlay,panel,now=self.manager();manager.start('galaxy',None)
            browser=manager.galaxy=Mock();browser.poll.return_value=[('stop',None)]
            if cause=='conditions_changed':s.display_revision+=1
            manager.tick(None,RingView(None,0.,'',False),0,active=True,eligible=False)
            self.assertFalse(manager.active);browser.close.assert_called_once()
            overlay.setVisible.assert_called_with(True);s.controller.set_matrix.assert_not_called()

    def test_connection_error_closes_browser_and_offers_qt_fallback(self):
        manager,s,overlay,panel,now=self.manager();manager.start('galaxy',None)
        browser=manager.galaxy=Mock();browser.poll.return_value=[('disconnected',None)]
        with patch('PySide6.QtWidgets.QMessageBox') as message:
            box=message.return_value
            manager.tick(None,RingView(None,0.,'',False),0,active=True,eligible=False)
            browser.close.assert_called_once();panel.present.assert_called_once();box.open.assert_called_once()
            self.assertIn('连接已断开',box.setText.call_args.args[0])
            manager.cancel();self.assertIsNone(manager._galaxy_dialog);box.close.assert_called_once()

    def test_browser_fallback_restores_then_starts_qt_backup(self):
        manager,*_=self.manager();manager.start('galaxy',None)
        manager.galaxy=Mock();manager.galaxy.poll.return_value=[('fallback',None)]
        with patch.object(manager,'start') as start:
            manager.tick(None,RingView(None,0.,'',False),0,active=True,eligible=False)
        start.assert_called_once_with('stars',None);self.assertFalse(manager.active)

    def test_session_starts_only_when_host_ready_and_detaches_before_async_cleanup(self):
        s=SimpleNamespace(settings=load_settings(use_local=False),screen=ScreenRect(0,0,1512,982),
                          controller=SimpleNamespace(demo_frames=Mock()))
        bridge=Mock();bridge.server=None;bridge.error=None;bridge.started_ns=1;bridge.telemetry=[];bridge.dropped=0;bridge.drain.return_value=[]
        browser=Mock();browser.error=None;browser.fallback=False
        session=GalaxySession(s,(40,40),(0,0),bridge_factory=lambda *_:bridge,browser_factory=lambda *_:browser)
        self.assertEqual(session.poll(),[]);browser.start.assert_not_called()
        bridge.server=SimpleNamespace(started=True);session.poll();browser.start.assert_called_once()
        s.controller.demo_frames.set.assert_called_with(session.processor)
        with tempfile.TemporaryDirectory() as directory,patch('overlay.galaxy_session.ROOT',Path(directory)):
            session.close();s.controller.demo_frames.set.assert_called_with(None)
            self.assertTrue(session.processor.stopped.is_set());session.cleanup.join(3)
            self.assertFalse(session.cleanup.is_alive());bridge.close.assert_called_once();browser.close.assert_called_once()
            self.assertEqual(len(list(Path(directory).rglob('*-galaxy.json'))),1)
        session.close();browser.close.assert_called_once()


if __name__=='__main__':unittest.main()
