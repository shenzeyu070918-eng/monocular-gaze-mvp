"""A Demo-only, in-memory translation estimated from the practice target.

Input positions have already passed personal correction and M2 filtering, but
must NOT contain this class's translation.  Repeating practice replaces the
translation from those original inputs instead of adding another translation.
This module has no profile, recorder, camera or file-system access.
"""
from collections import deque
from dataclasses import dataclass
from math import hypot, isclose
from numbers import Integral

import numpy as np

from .devices import finite


def _point(value):
    try:
        if len(value) != 2:
            raise ValueError('Demo 位置必须为二维')
        return tuple(finite(v, 'Demo position') for v in value)
    except TypeError as exc:
        raise ValueError('Demo 位置必须为二维') from exc


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError('capture_ns 必须为非负整数')
    return int(value)


@dataclass(frozen=True)
class DriftSettings:
    window_ms: float = 600.
    min_samples: int = 5
    min_span_ms: float = 500.
    max_cm: float = 3.

    def __post_init__(self):
        for name in ('window_ms', 'min_span_ms', 'max_cm'):
            finite(getattr(self, name), name, positive=True)
        if (isinstance(self.min_samples, bool) or not isinstance(self.min_samples, Integral)
                or self.min_samples < 2):
            raise ValueError('min_samples 必须为至少 2 的整数')
        if self.min_span_ms > self.window_ms:
            raise ValueError('漂移取样跨度不能超过取样窗口')


@dataclass(frozen=True)
class DriftEstimate:
    status: str
    reason: str
    offset: tuple
    candidate_offset: tuple | None = None
    magnitude_cm: float | None = None
    sample_count: int = 0
    span_ms: float = 0.
    median: tuple | None = None

    @property
    def applied(self):
        return self.status == 'applied'


class DemoDrift:
    def __init__(self, settings=None, *, width_cm=None, height_cm=None, measured=False):
        self.settings = settings or DriftSettings()
        # Accept the same validated setting attributes from a containing config.
        DriftSettings(self.settings.window_ms, self.settings.min_samples,
                      self.settings.min_span_ms, self.settings.max_cm)
        if type(measured) is not bool:
            raise ValueError('屏幕实测标记必须为布尔值')
        self.width_cm = finite(width_cm, 'screen.width_cm', positive=True, nullable=True)
        self.height_cm = finite(height_cm, 'screen.height_cm', positive=True, nullable=True)
        if measured and (self.width_cm is None or self.height_cm is None):
            raise ValueError('屏幕标记为实测时必须提供宽高厘米数')
        self.measured = measured
        self.offset = (0., 0.)
        self.magnitude_cm = None
        self.has_correction = False
        self._samples = deque()
        self._last_capture_ns = None
        self.restart()

    @property
    def status(self):
        return self.last_result.status

    @property
    def reason(self):
        return self.last_result.reason

    def restart(self):
        """Start collecting again, retaining any previous accepted correction."""
        self._samples.clear()
        self._last_capture_ns = None
        self.last_result = DriftEstimate('pending', '请看着练习按钮中间的点', self.offset)
        return self.last_result

    def _trim(self, capture_ns):
        cutoff = capture_ns - round(self.settings.window_ms * 1_000_000)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def observe(self, position, capture_ns, *, valid=True, now_ns=None):
        """Keep increasing valid capture samples, optionally rejecting stale input.

        ``now_ns`` must be on capture_ns's monotonic clock when supplied. A
        frame older than the sampling window is unusable; duplicate or backward
        capture timestamps never add weight to a median.
        """
        capture_ns = _timestamp(capture_ns)
        now_ns = None if now_ns is None else _timestamp(now_ns)
        if self._last_capture_ns is not None and capture_ns <= self._last_capture_ns:
            return False
        if now_ns is not None and capture_ns > now_ns:
            return False
        self._last_capture_ns = capture_ns
        self._trim(capture_ns if now_ns is None else max(capture_ns, now_ns))
        if (not valid or position is None or
                (now_ns is not None and now_ns - capture_ns > self.settings.window_ms * 1_000_000)):
            return False
        try:
            position = _point(position)
        except ValueError:
            return False
        self._samples.append((capture_ns, position))
        return True

    def estimate(self, target, capture_ns):
        target = _point(target)
        capture_ns = _timestamp(capture_ns)
        self._trim(capture_ns)
        # Future samples must not leak into an estimate at an earlier trigger.
        samples = [(stamp, point) for stamp, point in self._samples if stamp <= capture_ns]
        count = len(samples)
        span_ms = (samples[-1][0] - samples[0][0]) / 1_000_000 if count > 1 else 0.
        if count < self.settings.min_samples or span_ms < self.settings.min_span_ms:
            result = DriftEstimate('pending', '有效取样不足，请继续看着中间的点',
                                   self.offset, sample_count=count, span_ms=span_ms)
        else:
            median = tuple(float(v) for v in np.median([point for _, point in samples], axis=0))
            candidate = tuple(t - p for t, p in zip(target, median))
            if not self.measured:
                limit = f'{self.settings.max_cm:g}'
                result = DriftEstimate('unmeasured', f'屏幕尺寸未实测，无法检查 {limit} cm 上限；未应用漂移修正',
                                       self.offset, candidate, sample_count=count, span_ms=span_ms, median=median)
            else:
                magnitude = hypot(candidate[0] * self.width_cm, candidate[1] * self.height_cm)
                # Permit representational roundoff at exactly the configured boundary.
                exceeds = magnitude > self.settings.max_cm and not isclose(
                    magnitude, self.settings.max_cm, rel_tol=0., abs_tol=1e-12)
                if exceeds:
                    result = DriftEstimate('rejected', '漂移修正超过安全上限，请重新九点校准',
                                           self.offset, candidate, magnitude, count, span_ms, median)
                else:
                    self.offset = candidate
                    self.magnitude_cm = magnitude
                    self.has_correction = True
                    result = DriftEstimate('applied', f'已修正 {magnitude:.1f} cm',
                                           self.offset, candidate, magnitude, count, span_ms, median)
        self.last_result = result
        return result

    def apply(self, position):
        if position is None:
            return None
        point = _point(position)
        # No clipping: outside-screen estimates retain their original meaning.
        return tuple(v + shift for v, shift in zip(point, self.offset))
