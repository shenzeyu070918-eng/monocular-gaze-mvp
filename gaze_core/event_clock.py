"""Native mouse clocks mapped to the same perf_counter_ns epoch as capture_ns.

No event arrival time is passed off as an exact device time. Clock reads are
bracketed; bad brackets, clock jumps and implausible delivery order fall back
to callback arrival with an explicitly unknown delay. This module has no OS
dependencies and never requests any permission.
"""
from dataclasses import dataclass
import math
from numbers import Integral, Real
import time


def _real(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('原生事件时间必须为有限非负数。')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('原生事件时间必须为有限非负数。')
    return value


@dataclass(frozen=True)
class EventTime:
    event_ns: int
    received_ns: int
    native_timestamp: float | None
    timestamp_source: str
    callback_delay_estimate_ns: int | None
    clock_uncertainty_ns: int | None

    def fields(self):
        return dict(vars(self))


def callback_time(received_ns, native_timestamp=None, *, uncertainty_ns=None):
    """Honest fallback: absence of a usable native clock does not mean zero lag."""
    if isinstance(received_ns, bool) or not isinstance(received_ns, Integral) or received_ns < 0:
        raise ValueError('收到事件的单调时间无效。')
    return EventTime(int(received_ns), int(received_ns), native_timestamp,
                     'callback_received', None, uncertainty_ns)


def expand_windows_ticks(timestamp_ms, uptime_ms):
    """Expand a DWORD event tick near GetTickCount64, including the 49-day wrap.

    The event is expected to be recent; choose the closest 32-bit epoch. A
    future/old result is still checked by NativeClockMapper before admission.
    """
    for value in (timestamp_ms, uptime_ms):
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
            raise ValueError('Windows 鼠标时间必须为非负整数毫秒。')
    if timestamp_ms > 0xffffffff:
        raise ValueError('Windows 鼠标时间超出 DWORD 范围。')
    wrap = 1 << 32
    base = int(uptime_ms) & ~(wrap - 1)
    candidates = [base + int(timestamp_ms) + delta for delta in (-wrap, 0, wrap)]
    return min((value for value in candidates if value >= 0), key=lambda value: abs(value - uptime_ms))


class NativeClockMapper:
    """A bounded-uncertainty offset, periodically renewed outside disk work.

    native_now returns seconds (NSEvent uptime) or milliseconds (GetTickCount64).
    ns_per_unit and native_resolution_ns explicitly define that clock. A jump
    invalidates the event at the boundary and requires a second stable bracket.
    """
    def __init__(self, native_now, *, ns_per_unit=1_000_000_000,
                 native_resolution_ns=1, clock_ns=time.perf_counter_ns,
                 resample_ns=10_000_000_000, max_uncertainty_ns=5_000_000,
                 max_drift_ns=20_000_000, max_delay_ns=10_000_000_000):
        self.native_now = native_now
        self.clock_ns = clock_ns
        self.ns_per_unit = int(_real(ns_per_unit))
        if not self.ns_per_unit:
            raise ValueError('原生时钟单位必须为正。')
        self.resolution_ns = int(_real(native_resolution_ns))
        self.resample_ns = int(_real(resample_ns))
        self.max_uncertainty_ns = int(_real(max_uncertainty_ns))
        self.max_drift_ns = int(_real(max_drift_ns))
        self.max_delay_ns = int(_real(max_delay_ns))
        self.offset_ns = None
        self.uncertainty_ns = None
        self.sampled_ns = None
        self.native_sample = None
        self.trusted = False

    def refresh(self):
        before = int(self.clock_ns())
        try:
            native = _real(self.native_now())
        except (TypeError, ValueError, OSError, RuntimeError):
            self.trusted = False
            self.uncertainty_ns = None
            return False
        after = int(self.clock_ns())
        if after < before:
            self.trusted = False
            self.uncertainty_ns = None
            return False
        uncertainty = (after - before + self.resolution_ns + 1) // 2
        offset = (before + after) // 2 - round(native * self.ns_per_unit)
        stable = self.offset_ns is None or abs(offset - self.offset_ns) <= self.max_drift_ns
        self.offset_ns = offset
        self.uncertainty_ns = uncertainty
        self.sampled_ns = after
        self.native_sample = native
        self.trusted = uncertainty <= self.max_uncertainty_ns and stable
        return self.trusted

    def map(self, native_timestamp, received_ns=None):
        received = int(self.clock_ns()) if received_ns is None else received_ns
        fallback = callback_time(received)
        try:
            native = _real(native_timestamp)
        except (TypeError, ValueError):
            return fallback
        if (not self.trusted or self.sampled_ns is None or
                received - self.sampled_ns >= self.resample_ns):
            self.refresh()
        if not self.trusted:
            return callback_time(received, native, uncertainty_ns=self.uncertainty_ns)
        mapped = round(native * self.ns_per_unit) + self.offset_ns
        delay = int(received) - mapped
        if mapped < 0 or delay < 0 or delay > self.max_delay_ns:
            return callback_time(received, native, uncertainty_ns=self.uncertainty_ns)
        return EventTime(int(mapped), int(received), native, 'native_mapped',
                         int(delay), self.uncertainty_ns)
