"""Camera- and Qt-independent lifetime for the overlay's status label."""

import math
from numbers import Real
import time


def _timing(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite real number')
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} must be a finite real number') from exc
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        bound = 'positive' if positive else 'non-negative'
        raise ValueError(f'{name} must be finite and {bound}')
    return value


class LabelVisibility:
    """Hold each new text at full opacity, then fade it using a monotonic clock."""

    def __init__(self, text, autohide_s, fade_ms, *, diagnostic=False,
                 clock=time.monotonic):
        self._autohide_s = _timing(autohide_s, 'autohide_s')
        self._fade_ms = _timing(fade_ms, 'fade_ms', positive=True)
        self._diagnostic = diagnostic
        self._clock = clock
        self._text = text
        self._shown_at = clock()

    @property
    def text(self):
        return self._text

    def set_text(self, text):
        """Return whether the text changed, restarting visibility only on change."""
        if text == self._text:
            return False
        self._text = text
        self._shown_at = self._clock()
        return True

    def opacity(self):
        if self._diagnostic or self._autohide_s == 0:
            return 1.0
        fade_elapsed_s = self._clock() - (self._shown_at + self._autohide_s)
        if fade_elapsed_s <= 0:
            return 1.0
        return max(0.0, 1.0 - fade_elapsed_s * 1000 / self._fade_ms)
