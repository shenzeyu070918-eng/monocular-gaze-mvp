"""Full logical-screen Demo paint/lifecycle with synthetic data, no camera."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from gaze_core.config import DemoSettings,load_settings
from gaze_core.starfield import StarfieldSettings,StarfieldModel
from overlay.demo_window import DemoWindow
from overlay.mapping import ScreenRect
from overlay.star_run import StarDemoRun
from overlay.starfield import _batches,_polygon


class StaticWorker:
    def __init__(self,width,height,radii,settings,**kwargs):
        self.model=StarfieldModel(width,height,radii,settings,seed=42)
        self.error=None;self.started=False;self.closed=False;self.version=0
        self.publish(0)
    def publish(self,now):
        self.model.update(now);self.version+=1;self.snapshot=self.model.snapshot()
        self.snapshot.update(version=self.version,update_ms=0.,draw_batches=_batches(self.snapshot),
                             replay_polygon=_polygon(self.snapshot['replay_trajectory']))
    def start(self):self.started=True
    def presented(self,now):self.model.presented(now);self.publish(now)
    def pause(self,now):self.model.pause(now);self.publish(now)
    def resume(self,now):self.model.resume(now);self.publish(now)
    def ingest(self,point,capture,now,valid=True):self.model.ingest(point,capture,now,valid);self.publish(now)
    def latest(self):return self.snapshot
    def drain_events(self):return self.model.drain_events()
    def performance_report(self):return {'synthetic':1}
    def close(self,reason='escape',now_ns=0):self.model.close(now_ns,reason);self.closed=True


class StarDemoQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def make(self,practice=False):
        now=[0];options=replace(DemoSettings(),practice=practice)
        run=StarDemoRun(options,1512,982,(90,90),group='B',settings=StarfieldSettings(),
                        clock_ns=lambda:now[0],worker_factory=StaticWorker)
        window=DemoWindow(run,self.app.primaryScreen(),options,screen_rect=ScreenRect(0,0,1512,982,2),clock_ns=lambda:now[0])
        window.resize(1512,982)
        return now,run,window

    def test_complete_paint_start_and_repaint_only_new_snapshot(self):
        now,run,window=self.make()
        try:
            self.assertIsNone(run.started_ns)
            image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied)
            window.render(image)
            self.assertEqual(run.started_ns,0);self.assertEqual(run.worker.latest()['phase'],'playing')
            self.assertEqual(window.frame_timer.report()['count'],1)
            with patch.object(window,'update') as update:
                window._star_tick();update.assert_called_once()
                update.reset_mock();window._star_tick();update.assert_not_called()
            self.assertEqual(image.pixelColor(0,0).name(),'#040713')
            self.assertEqual(len(run.worker.latest()['positions']),2000)
            window.refresh((500,400),.7,0);self.assertTrue(window.star_timer.isActive())
            run.repractice(1)
            window.refresh((500,400),.7,1);self.assertFalse(window.star_timer.isActive())
            self.assertTrue(run.worker.model.paused)
        finally:
            run.close(2)
            with patch('overlay.starfield.save_starfield_performance',return_value={'paint_event':{'milliseconds':{'median':None,'p90':None}}}):window.dispose()
            self.app.processEvents()
        self.assertTrue(run.worker.closed);self.assertFalse(window.star_timer.isActive())

    def test_practice_has_no_animation_and_settings_merge(self):
        _,run,window=self.make(practice=True)
        try:
            window.refresh(None,0,0);self.assertFalse(window.star_timer.isActive())
            image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied);window.render(image)
            self.assertEqual(window.frame_timer.report()['count'],0)
            self.assertIsNone(run.started_ns)
            self.assertEqual(len(run.buttons),1)
        finally:
            run.close(2)
            with patch('overlay.starfield.save_starfield_performance',return_value={'paint_event':{'milliseconds':{'median':None,'p90':None}}}):window.dispose()
            self.app.processEvents()
        import gaze_core.config as config
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'default.yaml';path.write_text(config.DEFAULT_CONFIG.read_text())
            path.with_name('local.yaml').write_text('starfield:\n  constellation_count: 5\n  spring_hz: 4\n')
            with patch.object(config,'DEFAULT_CONFIG',path):settings=load_settings(path)
        self.assertEqual(settings.starfield.constellation_count,5)
        self.assertEqual(settings.starfield.spring_hz,4)
        self.assertEqual(settings.starfield.particle_count,2000)


if __name__=='__main__':unittest.main()
