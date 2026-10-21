import io
import json
from pathlib import Path
import pstats
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from overlay.profiling import CallbackProfiler


class CallbackProfilingTests(unittest.TestCase):
    def test_cprofile_excludes_background_threads_and_keeps_main_call_counts(self):
        stop=threading.Event();ran=threading.Event()
        def background_only():ran.set()
        def worker():
            while not stop.is_set():background_only();time.sleep(.001)
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(ran.wait(2))
            with tempfile.TemporaryDirectory() as directory:
                profiler=CallbackProfiler(1,directory=directory,stream=io.StringIO())
                def callback():time.sleep(.02)
                wrapped=profiler.wrap(callback,'tick')
                wrapped();wrapped();profiler.finish()
                entries=pstats.Stats(profiler.profile).stats
                self.assertFalse(any(key[2]=='background_only' for key in entries))
                calls=[value[:2] for key,value in entries.items() if key[2]=='callback']
                self.assertEqual(calls,[(2,2)])
        finally:stop.set();thread.join(2)

    def test_main_thread_only_and_profiling_failures_do_not_change_callback(self):
        errors=[]
        def worker():
            try:CallbackProfiler(1)
            except ValueError as exc:errors.append(str(exc))
        thread=threading.Thread(target=worker);thread.start();thread.join(2)
        self.assertTrue(errors)
        profiler=CallbackProfiler(1,stream=io.StringIO())
        profiler.profile=Mock();profiler.profile.enable.side_effect=RuntimeError('tool already active')
        self.assertEqual(profiler.wrap(lambda:42,'tick')(),42)
        self.assertTrue(profiler.finished)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'not-a-directory';path.write_text('synthetic')
            profiler=CallbackProfiler(.001,directory=path,stream=io.StringIO())
            self.assertEqual(profiler.wrap(lambda:time.sleep(.002) or 42,'tick')(),42)
            self.assertTrue(profiler.finished)
    def test_profiles_only_ready_callbacks_for_requested_time_and_saves_statistics(self):
        now=[0];ready=[False];steps=iter([1,3,4,5]);output=io.StringIO()
        def work():
            now[0]+=next(steps)*1_000_000
            return 'returned'
        with tempfile.TemporaryDirectory() as directory:
            profiler=CallbackProfiler(.01,ready=lambda:ready[0],directory=directory,
                                      clock_ns=lambda:now[0],stream=output,context=lambda:dict(source='synthetic'))
            wrapped=profiler.wrap(work,'tick')
            self.assertEqual(wrapped(),'returned')
            self.assertIsNone(profiler.started_ns)
            ready[0]=True
            for _ in range(3):self.assertEqual(wrapped(),'returned')
            self.assertTrue(profiler.finished)
            self.assertEqual(profiler.summary['callbacks']['tick']['count'],3)
            self.assertAlmostEqual(profiler.summary['callbacks']['tick']['median'],4.)
            self.assertAlmostEqual(profiler.summary['callbacks']['tick']['p90'],4.8)
            saved=json.loads(Path(profiler.paths['json']).read_text())
            self.assertTrue(saved['completed']);self.assertEqual(saved['context']['source'],'synthetic')
            self.assertEqual(len(list(Path(directory).iterdir())),3)
            pstats.Stats(profiler.paths['prof'])
            self.assertIn('cumulative',output.getvalue());self.assertIn('主线程剖析完成',output.getvalue())
            profiler.finish();self.assertEqual(len(list(Path(directory).iterdir())),3)

    def test_nested_label_calls_stay_inside_tick_and_callback_exceptions_are_preserved(self):
        now=[0]
        with tempfile.TemporaryDirectory() as directory:
            profiler=CallbackProfiler(1,directory=directory,clock_ns=lambda:now[0],stream=io.StringIO())
            label=profiler.wrap(lambda:None,'label')
            def work():
                label();now[0]+=2_000_000
                raise RuntimeError('synthetic callback error')
            with self.assertRaisesRegex(RuntimeError,'synthetic callback error'):profiler.wrap(work,'tick')()
            profiler.finish()
            self.assertFalse(profiler.summary['completed'])
            self.assertEqual(set(profiler.summary['callbacks']),{'tick'})
            self.assertEqual(profiler.summary['callbacks']['tick']['median'],2.)

    def test_rejects_invalid_seconds_and_no_profile_before_camera_ready(self):
        for seconds in (0,-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):CallbackProfiler(seconds)
        with tempfile.TemporaryDirectory() as directory:
            profiler=CallbackProfiler(1,ready=lambda:False,directory=directory,stream=io.StringIO())
            self.assertEqual(profiler.wrap(lambda:42,'tick')(),42)
            profiler.finish();self.assertEqual(list(Path(directory).iterdir()),[])


if __name__=='__main__':unittest.main()
