from dataclasses import replace
import threading
import time
import unittest
from gaze_core.power import NoFacePacing
from gaze_core.capture import CameraPipeline
from gaze_core.config import load_settings
from tests.test_capture import FakeCamera,FakeEngine


class PowerTests(unittest.TestCase):
    def test_only_continuous_no_face_triggers_pacing(self):
        p=NoFacePacing(3,5)
        p.observe('no_face',0);p.observe('no_face',2_999_999_999);self.assertFalse(p.idle)
        p.observe('no_face',3_000_000_000);self.assertTrue(p.idle);self.assertEqual(p.period_ns,200_000_000)
        for status in ('tracking','blink','prediction_error'):
            p.observe(status,4_000_000_000);self.assertFalse(p.idle)
            p.observe('no_face',5_000_000_000);self.assertFalse(p.idle)
        p.reset();self.assertIsNone(p.since_ns)

    def test_idle_read_wait_wakes_on_stop_and_recovery(self):
        settings=load_settings(use_local=False)
        for recover in (False,True):
            p=CameraPipeline(settings,idle_after_s=1,idle_fps=.01)
            p._pacing.idle=True;p._last_capture_ns=time.perf_counter_ns()
            entered=threading.Event();done=threading.Event()
            def wait():entered.set();p._wait_capture_slot();done.set()
            worker=threading.Thread(target=wait);worker.start();self.assertTrue(entered.wait(3))
            with p._condition:
                if recover:p._pacing.observe('tracking',time.perf_counter_ns())
                else:p._stop.set()
                p._condition.notify_all()
            self.assertTrue(done.wait(1));worker.join(1);p.close()

    def test_fake_camera_throttles_then_recovers_and_releases_on_capture(self):
        settings=load_settings(use_local=False);camera=FakeCamera();engine=FakeEngine()
        engine.predict=lambda image,*_: dict(valid=bool(image[0,0,0]),status='tracking' if image[0,0,0] else 'no_face',
                                              raw=[.5,.5] if image[0,0,0] else None)
        p=CameraPipeline(settings,camera_factory=lambda _:camera,engine_factory=lambda:engine,idle_after_s=.05,idle_fps=2)
        try:
            p.start();cursor=0;deadline=time.monotonic()+3
            while not p.report()['software_idle_pacing']:
                r=p.wait_for_result(cursor,timeout=.1)
                if r:cursor=r.sequence
                self.assertLess(time.monotonic(),deadline)
            camera.send(200)
            deadline=time.monotonic()+3
            while True:
                r=p.wait_for_result(cursor,timeout=.1)
                if r:
                    cursor=r.sequence
                    if r.data['status']=='tracking':break
                self.assertLess(time.monotonic(),deadline)
            self.assertFalse(p.report()['software_idle_pacing'])
            p.pause();self.assertEqual(camera.release_threads,['gaze-capture'])
            self.assertFalse(any(camera.release_during_read));self.assertFalse(engine.closed)
        finally:p.close()
        self.assertTrue(engine.closed)


if __name__=='__main__':unittest.main()
