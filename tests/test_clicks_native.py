"""Native APIs are injected fakes: no permissions, real hooks or clicks."""
import ctypes as C
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock

from gaze_core.event_clock import callback_time
from overlay.clicks_macos import MacMouseDown, cocoa_to_logical
from overlay.clicks_windows import (WindowsMouseDown, MouseHookData, Point, Rect,
                                   WH_MOUSE_LL, DOWN_MESSAGES, physical_to_logical)
from overlay.mapping import ScreenRect


def fake_mac():
    native_events = NS(addGlobalMonitorForEventsMatchingMask_handler_=Mock(return_value='global'),
                       addLocalMonitorForEventsMatchingMask_handler_=Mock(return_value='local'),
                       removeMonitor_=Mock())
    screen = NS(frame=lambda: NS(origin=NS(x=0, y=0), size=NS(width=100, height=80)))
    return NS(NSEvent=native_events, NSScreen=NS(screens=lambda: [screen]),
              NSEventMaskLeftMouseDown=2, NSEventMaskRightMouseDown=8,
              NSEventMaskOtherMouseDown=32)


def fake_windows():
    user_names = ('SetWindowsHookExW', 'UnhookWindowsHookEx', 'CallNextHookEx', 'GetMessageW',
                  'PeekMessageW', 'PostThreadMessageW', 'TranslateMessage', 'DispatchMessageW',
                  'SetThreadDpiAwarenessContext', 'EnumDisplayMonitors')
    user_names += ('WindowFromPoint', 'GetAncestor')
    user = NS(**{name: Mock(return_value=1) for name in user_names})
    kernel = NS(**{name: Mock(return_value=1) for name in
                   ('GetTickCount64', 'GetCurrentThreadId', 'GetModuleHandleW', 'GetSystemTimeAdjustment')})
    def resolution(a, increment, disabled):
        increment._obj.value = 156250  # Synthetic OS response, not a measured claim.
        return 1
    kernel.GetSystemTimeAdjustment.side_effect = resolution
    return user, kernel


class MacMouseTests(unittest.TestCase):
    def test_exact_mouse_masks_original_point_local_passthrough_and_cleanup(self):
        kit = fake_mac()
        rows = []
        mapper = Mock()
        mapper.map.return_value = callback_time(200, 1.)
        backend = MacMouseDown(ScreenRect(0, 0, 100, 80, dpr=2.), rows.append,
                               appkit=kit, foundation=NS(), clock_ns=lambda: 200, mapper=mapper)
        backend.start()
        backend.start()
        global_mask, global_handler = kit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.call_args.args
        local_mask, local_handler = kit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_.call_args.args
        self.assertEqual(global_mask, 2 | 8 | 32)
        self.assertEqual(local_mask, global_mask)
        value = NS(locationInWindow=lambda: NS(x=12., y=21.), buttonNumber=lambda: 1,
                   timestamp=lambda: 1., window=lambda: None)
        global_handler(value)
        self.assertEqual((rows[0].x, rows[0].y, rows[0].button, rows[0].own_ui), (12., 59., 'right', False))
        window = NS(convertPointToScreen_=lambda pt: NS(x=pt.x + 10, y=pt.y + 20))
        value.window = lambda: window
        self.assertIs(local_handler(value), value)
        self.assertEqual((rows[1].x, rows[1].y, rows[1].own_ui), (22., 39., True))
        backend.stop()
        backend.stop()
        global_handler(value)
        self.assertEqual(len(rows), 2)
        self.assertEqual([call.args[0] for call in kit.NSEvent.removeMonitor_.call_args_list], ['global', 'local'])

    def test_local_registration_failure_removes_already_created_global(self):
        kit = fake_mac()
        kit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_.return_value = None
        backend = MacMouseDown(ScreenRect(0, 0, 100, 80), Mock(), appkit=kit,
                               foundation=NS(), mapper=Mock())
        with self.assertRaisesRegex(RuntimeError, '监视器未能创建'):
            backend.start()
        kit.NSEvent.removeMonitor_.assert_called_once_with('global')

    def test_native_callback_exception_does_not_swallow_local_click(self):
        kit = fake_mac()
        backend = MacMouseDown(ScreenRect(0, 0, 100, 80), Mock(side_effect=RuntimeError('fake failure')),
                               appkit=kit, foundation=NS(), mapper=Mock())
        backend.start()
        handler = kit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_.call_args.args[1]
        value = NS(locationInWindow=lambda: NS(x=1, y=1), buttonNumber=lambda: 0,
                   timestamp=lambda: 1., window=lambda: None)
        self.assertIs(handler(value), value)
        self.assertIn('鼠标按下处理失败', backend.error)
        backend.stop()

    def test_background_cleanup_is_scheduled_on_main_thread(self):
        kit = fake_mac()
        scheduled = []
        backend = MacMouseDown(ScreenRect(0, 0, 100, 80), Mock(), appkit=kit,
                               foundation=NS(), mapper=Mock(), call_after=scheduled.append)
        backend.start()
        worker = threading.Thread(target=backend.stop)
        worker.start()
        worker.join()
        kit.NSEvent.removeMonitor_.assert_not_called()
        scheduled[0]()
        self.assertEqual(kit.NSEvent.removeMonitor_.call_count, 2)

    def test_cocoa_coordinates_keep_full_logical_screen_including_top(self):
        self.assertEqual(cocoa_to_logical((0, 982), 982), (0., 0.))
        self.assertEqual(cocoa_to_logical((1511, 1), 982), (1511., 981.))


class WindowsMouseTests(unittest.TestCase):
    def backend(self, callback=None, *, physical_rect=(0, 0, 200, 160), screen=None, mapper=None):
        user, kernel = fake_windows()
        mapped = mapper or Mock(map=Mock(return_value=callback_time(4000, 1.)))
        backend = WindowsMouseDown(screen or ScreenRect(0, 0, 100, 80, dpr=2.), callback or Mock(),
                                   user32=user, kernel32=kernel, physical_rect=physical_rect,
                                   clock_ns=lambda: 4000, mapper=mapped)
        return backend, user, kernel

    def test_only_down_messages_original_event_coordinates_and_chain_always_called(self):
        rows = []
        backend, user, kernel = self.backend(rows.append)
        user.CallNextHookEx.return_value = 42
        data = MouseHookData(Point(40, 60), 0, 0, 1, 0)
        for message in (0x0200, 0x0202, 0x0205, 0x0208, 0x020c, 0x0100):
            self.assertEqual(backend._event(0, message, C.addressof(data)), 42)
        self.assertFalse(rows)
        for message, button in DOWN_MESSAGES.items():
            self.assertEqual(backend._event(0, message, C.addressof(data)), 42)
            self.assertEqual((rows[-1].x, rows[-1].y, rows[-1].button), (20., 30., button))
            self.assertEqual(rows[-1].timing.native_timestamp, 1.)
        self.assertEqual(backend._event(-1, 0x0201, 0), 42)
        self.assertEqual(user.CallNextHookEx.call_count, 11)

    def test_callback_errors_still_pass_to_next_hook(self):
        backend, user, kernel = self.backend(Mock(side_effect=RuntimeError('writer fail')))
        data = MouseHookData(Point(40, 60), 0, 0, 1, 0)
        backend._event(0, 0x0201, C.addressof(data))
        user.CallNextHookEx.assert_called_once()
        self.assertIn('鼠标按下处理失败', backend.error)

    def test_only_mouse_hook_installed_on_dedicated_thread_and_uninstalled(self):
        backend, user, kernel = self.backend()
        ready = threading.Event()
        native_thread = []
        def install(kind, callback, module, thread):
            native_thread.append(threading.current_thread().name)
            self.assertEqual((kind, thread), (WH_MOUSE_LL, 0))
            return 99
        def read(*a):
            ready.wait(2)
            return 0
        user.SetWindowsHookExW.side_effect = install
        user.GetMessageW.side_effect = read
        user.PostThreadMessageW.side_effect = lambda *a: (ready.set() or 1)
        backend.start()
        backend.stop()
        backend.stop()
        self.assertEqual(native_thread, ['gaze-mouse-hook'])
        user.UnhookWindowsHookEx.assert_called_once_with(99)
        self.assertEqual(user.PostThreadMessageW.call_args.args[1], 0x0012)

    def test_registration_failure_is_reported(self):
        backend, user, kernel = self.backend()
        user.SetWindowsHookExW.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'WH_MOUSE_LL 注册失败'):
            backend.start()
        user.UnhookWindowsHookEx.assert_not_called()

    def test_unique_monitor_required_and_dpi_mismatch_rejected(self):
        user, kernel = fake_windows()
        def enumerate_monitors(_, __, callback, ___):
            first = Rect(0, 0, 200, 160)
            second = Rect(200, 0, 400, 160)
            callback(None, None, C.pointer(first), 0)
            callback(None, None, C.pointer(second), 0)
            return 1
        user.EnumDisplayMonitors.side_effect = enumerate_monitors
        with self.assertRaisesRegex(RuntimeError, '唯一启用'):
            WindowsMouseDown(ScreenRect(0, 0, 100, 80, 2.), Mock(), user32=user, kernel32=kernel)
        with self.assertRaisesRegex(RuntimeError, 'DPI 不一致'):
            self.backend(physical_rect=(0, 0, 1920, 1080))

    def test_scaling_100_125_150_percent_and_foreign_screen_drop(self):
        for dpr in (1., 1.25, 1.5, 2.):
            screen = ScreenRect(10, 20, 100, 80, dpr)
            physical = (300, 400, 300 + 100 * dpr, 400 + 80 * dpr)
            self.assertEqual(physical_to_logical((300, 400), physical, screen), (10., 20.))
            self.assertEqual(physical_to_logical((300 + 99 * dpr, 400 + 79 * dpr), physical, screen), (109., 99.))
            self.assertIsNone(physical_to_logical((physical[2], 450), physical, screen))

    def test_clock_uses_measured_os_increment_not_millisecond_unit_as_precision(self):
        user, kernel = fake_windows()
        backend = WindowsMouseDown(ScreenRect(0, 0, 100, 80, 2.), Mock(), user32=user,
                                   kernel32=kernel, physical_rect=(0, 0, 200, 160), clock_ns=lambda: 1000000000)
        self.assertEqual(backend.mapper.resolution_ns, 15_625_000)
        kernel.GetSystemTimeAdjustment.return_value = 0
        kernel.GetSystemTimeAdjustment.side_effect = None
        backend = WindowsMouseDown(ScreenRect(0, 0, 100, 80, 2.), Mock(), user32=user,
                                   kernel32=kernel, physical_rect=(0, 0, 200, 160), clock_ns=lambda: 1000000000)
        result = backend.mapper.map(1, 1000000000)
        self.assertEqual(result.timestamp_source, 'callback_received')
        self.assertIsNone(result.callback_delay_estimate_ns)

    def test_cached_own_hwnd_identity_without_foreign_text_or_names(self):
        backend, user, kernel = self.backend()
        rows = []
        backend.callback = rows.append
        backend.own_handles = lambda: frozenset((99,))
        user.WindowFromPoint.return_value = 55
        user.GetAncestor.return_value = 99
        data = MouseHookData(Point(40, 60), 0, 0, 1, 0)
        backend._event(0, 0x0201, C.addressof(data))
        self.assertIs(rows[-1].own_ui, True)
        user.GetAncestor.return_value = 100
        backend._event(0, 0x0201, C.addressof(data))
        self.assertIs(rows[-1].own_ui, False)

    def test_configured_clock_quality_options_are_forwarded(self):
        user, kernel = fake_windows()
        backend = WindowsMouseDown(ScreenRect(0, 0, 100, 80, 2.), Mock(), user32=user,
                                   kernel32=kernel, physical_rect=(0, 0, 200, 160),
                                   clock_options=dict(resample_ns=123, max_uncertainty_ns=456,
                                                      max_drift_ns=789, max_delay_ns=1000))
        self.assertEqual((backend.mapper.resample_ns, backend.mapper.max_uncertainty_ns,
                          backend.mapper.max_drift_ns, backend.mapper.max_delay_ns), (123, 456, 789, 1000))


if __name__ == '__main__':
    unittest.main()
