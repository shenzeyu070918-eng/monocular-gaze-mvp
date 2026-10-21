"""Pure clock tests: no real monitoring and no wall-clock timing claims."""
import unittest
import numpy as np

from gaze_core.event_clock import NativeClockMapper, callback_time, expand_windows_ticks


class ClockTests(unittest.TestCase):
    def test_bracketed_mapping_shares_perf_counter_epoch(self):
        values = iter([1_000_000_000, 1_000_000_200])
        mapper = NativeClockMapper(lambda: 100., clock_ns=lambda: next(values))
        self.assertTrue(mapper.refresh())
        result = mapper.map(np.float64(99.99), np.int64(1_020_000_000))
        self.assertEqual(result.event_ns, 990_000_100)
        self.assertEqual(result.callback_delay_estimate_ns, 29_999_900)
        self.assertEqual(result.clock_uncertainty_ns, 101)
        self.assertEqual(result.timestamp_source, 'native_mapped')
        self.assertIs(type(result.event_ns), int)
        self.assertIs(type(result.received_ns), int)

    def test_bad_slow_bracket_falls_back_with_unknown_delay(self):
        values = iter([1_000_000_000, 1_020_000_000])
        mapper = NativeClockMapper(lambda: 100., clock_ns=lambda: next(values))
        result = mapper.map(99., 1_000_000_000)
        self.assertEqual(result.timestamp_source, 'callback_received')
        self.assertEqual(result.event_ns, result.received_ns)
        self.assertIsNone(result.callback_delay_estimate_ns)
        self.assertGreater(result.clock_uncertainty_ns, mapper.max_uncertainty_ns)

    def test_future_native_time_never_fabricates_zero_lag(self):
        mapper = NativeClockMapper(lambda: 1., clock_ns=lambda: 1_000_000_000)
        mapper.refresh()
        result = mapper.map(2., 1_000_000_000)
        self.assertEqual(result.timestamp_source, 'callback_received')
        self.assertIsNone(result.callback_delay_estimate_ns)

    def test_clock_jump_requires_second_stable_bracket(self):
        perf = [1_000_000_000]
        native = [1.]
        mapper = NativeClockMapper(lambda: native[0], clock_ns=lambda: perf[0], resample_ns=10)
        mapper.refresh()
        perf[0] += 100_000_000
        native[0] += 10.
        self.assertEqual(mapper.map(native[0], perf[0]).timestamp_source, 'callback_received')
        self.assertEqual(mapper.map(native[0], perf[0]).timestamp_source, 'native_mapped')

    def test_bad_native_clock_and_timestamps_use_honest_fallback(self):
        mapper = NativeClockMapper(lambda: None, clock_ns=lambda: 200)
        result = mapper.map(1., 200)
        self.assertEqual(result, callback_time(200, 1.))
        for bad in (None, True, float('nan'), float('inf'), -1, '1'):
            self.assertEqual(mapper.map(bad, 200), callback_time(200))
        with self.assertRaises(ValueError):
            callback_time(True)

    def test_windows_tick_wrap_and_old_event_before_wrap(self):
        wrap = 1 << 32
        self.assertEqual(expand_windows_ticks(5, wrap + 10), wrap + 5)
        self.assertEqual(expand_windows_ticks(wrap - 2, wrap + 10), wrap - 2)
        self.assertEqual(expand_windows_ticks(123, 200), 123)
        for bad in (True, -1, 1.5, 1 << 32):
            with self.assertRaises(ValueError):
                expand_windows_ticks(bad, wrap + 10)


if __name__ == '__main__':
    unittest.main()
