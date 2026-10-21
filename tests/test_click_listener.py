"""Opt-in lifecycle and numeric-only click delivery; fake backends throughout."""
import threading
import tempfile
import unittest
from unittest.mock import Mock
import numpy as np

from gaze_core.event_clock import callback_time
from overlay.click_listener import ClickListener, NativeMouseDown, normalized_screen_point
from overlay.mapping import ScreenRect
from gaze_core.recorder import SessionRecorder
from tests.test_python_event_clock import metadata
from tools.replay import read_session


def event(x=30., y=40., button='left', own=None):
    return NativeMouseDown(x, y, button, callback_time(1_000), own)


class ClickListenerTests(unittest.TestCase):
    def test_default_off_start_stop_restart_and_stale_callbacks(self):
        callbacks = []
        backends = []
        def factory(screen, callback):
            callbacks.append(callback)
            backend = Mock(error=None)
            backends.append(backend)
            return backend
        rows = []
        listener = ClickListener(ScreenRect(0, 0, 100, 80), lambda kind, **row: rows.append((kind, row)),
                                 backend_factory=factory)
        self.assertFalse(listener.status()['active'])
        self.assertFalse(callbacks)
        self.assertTrue(listener.start())
        listener.start()
        self.assertEqual(len(callbacks), 1)
        callbacks[0](event())
        listener.stop()
        listener.stop()
        self.assertEqual(listener.status()['accepted'], 1)
        self.assertEqual(rows[0][0], 'click')
        self.assertEqual(rows[0][1]['timestamp_source'], 'callback_received')
        self.assertIsNone(rows[0][1]['callback_delay_estimate_ns'])
        listener.start()
        callbacks[0](event())
        callbacks[1](event(button='right'))
        listener.stop()
        self.assertEqual(listener.status()['accepted'], 2)
        self.assertEqual([row['button'] for _, row in rows], ['left', 'right'])
        for backend in backends:
            backend.start.assert_called_once()

    def test_outside_is_dropped_and_own_clicks_marked(self):
        rows = []
        callback = []
        listener = ClickListener(ScreenRect(10, 20, 100, 80), lambda kind, **row: rows.append(row),
                                 exclude=lambda point: 'own_panel' if point[0] < 35 else None,
                                 backend_factory=lambda screen, cb: (callback.append(cb) or Mock(error=None)))
        listener.start()
        for value in (event(x=9), event(x=110), event(), event(x=50, own=True), event(x=60)):
            callback[0](value)
        listener.stop()
        status = listener.status()
        self.assertEqual((status['received'], status['outside'], status['excluded'], status['accepted']), (5, 2, 2, 1))
        self.assertEqual([row['exclusion_reason'] for row in rows], ['own_panel', 'own_ui', None])
        for row in rows:
            self.assertEqual(set(row), {'x', 'y', 'button', 'event_ns', 'received_ns', 'native_timestamp',
                                       'timestamp_source', 'callback_delay_estimate_ns', 'clock_uncertainty_ns',
                                       'excluded', 'exclusion_reason'})

    def test_recording_failure_stops_collection(self):
        callback = []
        done = threading.Event()
        backend = Mock(error=None)
        backend.stop.side_effect = done.set
        listener = ClickListener(ScreenRect(0, 0, 100, 80), lambda *a, **kw: False,
                                 backend_factory=lambda screen, cb: (callback.append(cb) or backend))
        listener.start()
        callback[0](event())
        self.assertTrue(done.wait(2))
        self.assertFalse(listener.status()['active'])
        self.assertIn('录制器拒绝', listener.status()['error'])
        listener.stop()

    def test_bounded_queue_never_blocks_native_callback_and_surfaces_overflow(self):
        callback = []
        entered = threading.Event()
        release = threading.Event()
        def emit(*a, **kw):
            entered.set()
            release.wait(2)
        listener = ClickListener(ScreenRect(0, 0, 100, 80), emit, queue_size=1,
                                 backend_factory=lambda screen, cb: (callback.append(cb) or Mock(error=None)))
        listener.start()
        try:
            callback[0](event())
            self.assertTrue(entered.wait(2))
            callback[0](event())
            callback[0](event())
            self.assertFalse(listener.status()['active'])
            self.assertIn('队列已满', listener.status()['error'])
        finally:
            release.set()
            listener.stop()

    def test_backend_start_failure_is_visible_and_disposed(self):
        backend = Mock(error=None)
        backend.start.side_effect = RuntimeError('fake registration')
        listener = ClickListener(ScreenRect(0, 0, 100, 80), Mock(), backend_factory=lambda *a: backend)
        with self.assertRaisesRegex(RuntimeError, '点击采集启动失败'):
            listener.start()
        self.assertFalse(listener.status()['active'])
        self.assertTrue(backend.stop.called)

    def test_logical_coordinates_retina_edges_and_numpy(self):
        screen = ScreenRect(10, 20, 1512, 982, dpr=2.)
        for value in ((0., 0.), (.2, .7), (1., 1.)):
            result = normalized_screen_point(screen, screen.global_position(value))
            np.testing.assert_allclose(result, value)
        self.assertEqual(normalized_screen_point(screen, (1521.5, 1001.5)), (1., 1.))
        self.assertIsNone(normalized_screen_point(screen, (1522, 40)))
        self.assertEqual(normalized_screen_point(screen, (np.float64(10), np.float32(20))), (0., 0.))
        for point in ((float('nan'), 0), (True, 0), (0,)):
            with self.assertRaises(ValueError):
                normalized_screen_point(screen, point)

    def test_native_record_roundtrips_through_real_numeric_recorder_and_replay(self):
        callback = []
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), directory=folder)
            listener = ClickListener(ScreenRect(0, 0, 1000, 800), recorder.emit,
                                     backend_factory=lambda screen, cb: (callback.append(cb) or Mock(error=None)))
            listener.start()
            callback[0](event(np.float64(249.75), np.float32(399.5)))
            listener.stop()
            recorder.close()
            self.assertIsNone(listener.status()['error'])
            click = next(row for row in read_session(recorder.path).records if row['type'] == 'click')
            self.assertEqual((click['x'], click['y']), (.25, .5))
            self.assertEqual(click['event_ns'], 1000)
            self.assertIs(type(click['event_ns']), int)
            self.assertIsNone(click['native_timestamp'])
            self.assertNotIn('image', click)

    def test_twenty_repeated_starts_stops_and_native_failure(self):
        callbacks = []
        native = Mock(error=None)
        listener = ClickListener(ScreenRect(0, 0, 100, 80), Mock(),
                                 backend_factory=lambda screen, cb: (callbacks.append(cb) or native))
        for _ in range(20):
            listener.start()
            callbacks[-1](event())
            listener.stop()
        self.assertEqual(listener.status()['accepted'], 20)
        listener.start()
        native.error = '原生消息失败'
        self.assertFalse(listener.status()['active'])
        self.assertIn('原生消息失败', listener.status()['error'])
        listener.stop()

    def test_exclusion_identity_and_cached_reason_are_snapshotted_before_delivery(self):
        callback = []
        entered = threading.Event()
        release = threading.Event()
        cached = ['own_panel']
        rows = []
        def emit(kind, **row):
            if not rows:
                entered.set()
                release.wait(2)
            rows.append(row)
        listener = ClickListener(ScreenRect(0, 0, 100, 80), emit,
                                 exclude=lambda point: cached[0],
                                 backend_factory=lambda screen, cb: (callback.append(cb) or Mock(error=None)))
        listener.start()
        try:
            callback[0](event(own=False))
            self.assertTrue(entered.wait(2))
            callback[0](event(own=True))
            cached[0] = None  # Own window hides before the worker can drain.
        finally:
            release.set()
            listener.stop()
        self.assertIsNone(rows[0]['exclusion_reason'])
        self.assertEqual(rows[1]['exclusion_reason'], 'own_panel')


if __name__ == '__main__':
    unittest.main()
