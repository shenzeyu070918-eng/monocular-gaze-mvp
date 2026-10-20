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
    """Debounce transient states; recovery updates text without restarting the hold."""

    def __init__(self, text, autohide_s, fade_ms, *, debounce_s=1.0, diagnostic=False,
                 clock=time.monotonic):
        self._autohide_s = _timing(autohide_s, 'autohide_s')
        self._fade_ms = _timing(fade_ms, 'fade_ms', positive=True)
        self._debounce_s = _timing(debounce_s, 'debounce_s')
        self._diagnostic = diagnostic
        self._clock = clock
        self._text = text
        self._kind = 'immediate'
        self._pending = None
        self._shown_at = clock()

    @property
    def text(self):
        return self._text

    @property
    def pending(self):
        return self._pending is not None

    def set_text(self, text, *, kind='immediate', force=False):
        """Observe live status, separate from the text currently being painted.

        immediate: lifecycle/error status, or an explicit user action (force).
        transient: commit only after the same status persists for debounce_s.
        tracking: cancel transient status; never restart visibility on recovery.
        """
        if kind not in {'immediate', 'transient', 'tracking'}:
            raise ValueError('invalid label status kind')
        if kind == 'transient':
            if (text, kind) == (self._text, self._kind):
                self._pending = None
                return False
            now = self._clock()
            if self._pending is None or self._pending[0] != text:
                self._pending = (text, now)
            return self._commit_pending(now)
        self._pending = None
        changed = (text, kind) != (self._text, self._kind)
        if kind == 'immediate' and (changed or force):
            self._shown_at = self._clock()
        self._text, self._kind = text, kind
        return changed or force

    def _commit_pending(self, now):
        if self._pending is None or now - self._pending[1] < self._debounce_s:
            return False
        self._text, self._kind = self._pending[0], 'transient'
        self._pending = None
        self._shown_at = now
        return True

    def opacity(self):
        now = self._clock()
        self._commit_pending(now)
        if self._diagnostic or self._autohide_s == 0:
            return 1.0
        fade_elapsed_s = now - (self._shown_at + self._autohide_s)
        if fade_elapsed_s <= 0:
            return 1.0
        return max(0.0, 1.0 - fade_elapsed_s * 1000 / self._fade_ms)
