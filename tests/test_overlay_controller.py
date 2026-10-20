from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch
from gaze_core.config import load_settings
from gaze_core.capture import CameraPipeline,Prediction
from overlay.controller import OverlayController
from tests.test_capture import FakeCamera,FakeEngine


def wait_for(controller,predicate):
    with controller._condition:
        if not controller._condition.wait_for(lambda:predicate(controller.snapshot()),3):
            raise AssertionError(controller.snapshot())


class FakePipeline:
    def __init__(self):
        self.calls=[];self.active=False;self.sequence=0
    def resume(self):self.calls.append(('resume',threading.current_thread().name));self.active=True
    def pause(self):self.calls.append(('pause',threading.current_thread().name));self.active=False
    def close(self):self.calls.append(('close',threading.current_thread().name));self.active=False
    def report(self):return {'software_idle_pacing':False}
    def wait_for_result(self,cursor,timeout):
        time.sleep(.005)
        self.sequence+=1
        return Prediction(self.sequence,time.perf_counter_ns(),0,{'valid':False,'status':'no_face'})


class ControllerTests(unittest.TestCase):
    def test_twenty_pause_resume_cycles_have_one_owner_and_one_pipeline(self):
        settings=load_settings(use_local=False);pipe=FakePipeline();created=[]
        def factory():created.append(1);return pipe
        c=OverlayController(settings,pipeline_factory=factory)
        try:
            for _ in range(20):
                c.request('running');wait_for(c,lambda s:s.status=='running')
                c.request('paused');wait_for(c,lambda s:s.status=='paused')
                self.assertIsNone(c.snapshot().result)
            self.assertEqual(len(created),1)
        finally:c.request('closed');self.assertTrue(c.closed.wait(3));c.thread.join(1)
        self.assertTrue(all(owner=='overlay-control' for _,owner in pipe.calls))
        self.assertEqual([kind for kind,_ in pipe.calls].count('close'),1)

    def test_close_during_slow_start_is_asynchronous_and_has_no_late_result(self):
        started=threading.Event();release=threading.Event();pipe=FakePipeline()
        def resume():started.set();release.wait(3);pipe.active=True
        pipe.resume=resume
        c=OverlayController(load_settings(use_local=False),pipeline_factory=lambda:pipe)
        c.request('running');self.assertTrue(started.wait(3))
        c.request('closed')  # Returns while resume is still blocked.
        self.assertFalse(c.closed.is_set());release.set()
        self.assertTrue(c.closed.wait(3));c.thread.join(1)
        self.assertIsNone(c.snapshot().result);self.assertFalse(pipe.active)

    def test_start_failure_can_resume_and_close_failure_can_retry(self):
        pipe=FakePipeline();original=pipe.resume;attempts=[]
        def resume():
            attempts.append(1)
            if len(attempts)==1:raise RuntimeError('fake permission failure')
            original()
        pipe.resume=resume
        c=OverlayController(load_settings(use_local=False),pipeline_factory=lambda:pipe)
        c.request('running');wait_for(c,lambda s:s.status=='error')
        self.assertIn('fake permission',c.snapshot().error)
        c.request('running');wait_for(c,lambda s:s.status=='running')
        calls=[]
        def close():
            calls.append(1)
            if len(calls)==1:raise RuntimeError('fake close timeout')
        pipe.close=close;c.request('closed');wait_for(c,lambda s:s.status=='error')
        self.assertFalse(c.closed.is_set())
        c.request('closed');self.assertTrue(c.closed.wait(3));c.thread.join(1)

    def test_real_pipeline_bridge_uses_capture_clock_and_never_loads_profile(self):
        settings=load_settings(use_local=False)
        camera=FakeCamera();engine=FakeEngine()
        engine.predict=lambda *_:dict(valid=True,status='tracking',raw=[.4,.6],origin_m=[0,.1,.5])
        created=[]
        from gaze_core.capture import CameraPipeline as RealPipeline
        def factory(*args,**kwargs):
            created.append(kwargs)
            return RealPipeline(*args,**kwargs,camera_factory=lambda _:camera,engine_factory=lambda:engine)
        with patch('gaze_core.capture.CameraPipeline',side_effect=factory), \
             patch('gaze_core.profiles.load_profile',side_effect=AssertionError('M3a must not load profiles')):
            c=OverlayController(settings)
            try:
                c.request('running');wait_for(c,lambda s:s.result is not None)
                data=c.snapshot().result.data
                self.assertEqual(data['display']['timestamp_source'],'capture_ns')
                self.assertIsNone(created[0]['display_pipeline'].matrix)
                self.assertEqual(data['display']['unfiltered'],data['raw'])
            finally:c.request('closed');self.assertTrue(c.closed.wait(3));c.thread.join(1)
        self.assertTrue(engine.closed)
        self.assertEqual(camera.release_threads,['gaze-capture'])
        self.assertFalse(any(camera.release_during_read))


if __name__=='__main__':unittest.main()
