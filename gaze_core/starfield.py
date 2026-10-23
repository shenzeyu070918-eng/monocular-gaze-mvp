"""Deterministic, UI-free star Demo; sensor evidence never follows animation.

One worker owns this model. ``ingest`` accepts real filtered capture samples;
``update`` advances visual physics only. Snapshots contain detached read-only
arrays, so the Qt thread can paint without sharing mutable NumPy state.
"""
from dataclasses import dataclass, fields
import math
from numbers import Integral

import numpy as np

from .devices import finite


@dataclass(frozen=True)
class StarfieldSettings:
    particle_count: int = 2000
    firework_pool: int = 400
    firework_particles: int = 100
    animation_fps: float = 60.
    frame_budget_ms: float = 8.  # Paint-event target, not a measured result.
    long_frame_ms: float = 1000. / 60.
    round_s: float = 60.
    replay_s: float = 5.
    halo_radius_factor: float = 1.5
    stable_radius_factor: float = 1.
    bloom_ms: float = 1500.
    cooldown_ms: float = 1000.
    stale_ms: float = 300.
    max_capture_gap_ms: float = 300.
    spring_hz: float = 3.
    halo_fade_s: float = .25
    attraction_strength: float = 12.
    return_strength: float = 5.
    particle_drag: float = 4.
    max_animation_step_s: float = .05
    base_brightness: float = .22
    brightness_gain: float = .75
    min_star_px: float = 1.
    max_star_px: float = 3.
    firework_life_s: float = 1.2
    firework_speed_px_s: float = 170.
    firework_drag: float = 1.
    firework_size_px: float = 3.
    mask_columns: int = 160
    constellation_footprint_px: float = 12.
    reveal_threshold: float = .7
    reveal_s: float = 1.
    constellation_count: int = 4
    color_palette: tuple = ((190, 215, 255), (255, 190, 140),
                            (255, 155, 210), (150, 235, 255))

    def __post_init__(self):
        integers = {'particle_count', 'firework_pool', 'firework_particles',
                    'mask_columns', 'constellation_count'}
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == 'color_palette':
                if (not isinstance(value, (tuple, list)) or len(value) < 2 or
                        any(not isinstance(color, (tuple, list)) or len(color) != 3 or
                            any(isinstance(v, bool) or not isinstance(v, Integral) or not 0 <= v <= 255
                                for v in color) for color in value)):
                    raise ValueError('starfield.color_palette 必须至少包含两个 RGB 颜色')
                continue
            if field.name in integers:
                if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                    raise ValueError(f'starfield.{field.name} 必须为正整数')
            else:
                finite(value, 'starfield.' + field.name, positive=True)
        if not 3 <= self.constellation_count <= 5:
            raise ValueError('starfield.constellation_count 必须介于 3 与 5 之间')
        if self.firework_pool >= self.particle_count - 30:
            raise ValueError('starfield 必须为星座和背景保留至少 31 个星点')
        if self.firework_particles > self.firework_pool:
            raise ValueError('starfield.firework_particles 不能超过固定烟花池')
        if self.mask_columns < 16:
            raise ValueError('starfield.mask_columns 不得小于 16')
        if not 0 < self.reveal_threshold < 1:
            raise ValueError('starfield.reveal_threshold 必须介于 0 与 1 之间')
        if not 0 < self.base_brightness <= 1 or self.brightness_gain > 1:
            raise ValueError('starfield 亮度参数必须在 (0, 1] 内')
        if self.max_star_px < self.min_star_px:
            raise ValueError('starfield.max_star_px 不能小于 min_star_px')


# Normalized vector artwork, not astronomical coordinates or image assets.
# Footprints below are fixed unions of star disks and edge corridors. Empty
# space in the enclosing rectangle never contributes to the denominator.
_CONSTELLATIONS = (
    ('北斗七星', ((.11, .20), (.17, .18), (.24, .24), (.31, .25),
                 (.36, .20), (.40, .31), (.32, .35)),
     ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 3))),
    ('猎户座', ((.68, .16), (.85, .18), (.72, .31), (.77, .32),
               (.82, .33), (.67, .47), (.86, .46)),
     ((0, 1), (0, 2), (1, 4), (2, 3), (3, 4), (2, 5), (4, 6))),
    ('仙后座', ((.10, .67), (.18, .82), (.24, .67), (.32, .83), (.41, .66)),
     ((0, 1), (1, 2), (2, 3), (3, 4))),
    ('天鹅座', ((.74, .57), (.75, .72), (.76, .89), (.59, .74), (.91, .70)),
     ((0, 1), (1, 2), (1, 3), (1, 4))),
    ('仙女座', ((.47, .12), (.52, .22), (.48, .34), (.55, .45),
               (.50, .56), (.59, .29)),
     ((0, 1), (1, 2), (2, 3), (3, 4), (2, 5))),
)


def _timestamp(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(name + ' 必须为非负单调时钟整数')
    return int(value)


def _point(value):
    if not isinstance(value, (tuple, list, np.ndarray)) or len(value) != 2:
        raise ValueError('position 必须为两个有限逻辑像素坐标')
    return np.array([finite(v, 'position') for v in value], dtype=float)


def _readonly(value):
    result = np.array(value, copy=True)
    result.flags.writeable = False
    return result


class StarfieldModel:
    """Batch particle physics and capture-driven discovery, on logical pixels.

    Event codes: 1 = bloom, 2 = constellation revealed, 3 = replay started.
    Constellations use zero-based numeric IDs in events; their human names are
    UI-only. ``close`` appends a numeric summary, never the in-memory trajectory.
    """

    def __init__(self, width, height, radii, settings=None, *, seed=None):
        self.settings = settings or StarfieldSettings()
        self.width = finite(width, 'width', positive=True)
        self.height = finite(height, 'height', positive=True)
        if self.width < 2 or self.height < 2:
            raise ValueError('星空逻辑屏幕尺寸必须至少为 2×2')
        self.radii = _point(radii)
        if np.any(self.radii <= 0):
            raise ValueError('误差半径必须为正数')
        self.halo_radii = self.radii * self.settings.halo_radius_factor
        self.rng = np.random.default_rng(seed)
        o = self.settings
        self.background_count = o.particle_count - o.firework_pool
        self.positions = self.rng.uniform((0., 0.), (self.width - 1, self.height - 1),
                                          (o.particle_count, 2))
        self.velocities = np.zeros_like(self.positions)
        self.anchors = self.positions[:self.background_count].copy()
        self.sizes = self.rng.uniform(o.min_star_px, o.max_star_px, o.particle_count)
        self.sizes[self.background_count:] = o.firework_size_px
        self.base_brightness = self.rng.uniform(o.base_brightness / 2,
                                               o.base_brightness, self.background_count)
        self.brightness = np.zeros(o.particle_count)
        self.brightness[:self.background_count] = self.base_brightness
        self.color_palette = np.array(o.color_palette, dtype=np.uint8)
        self.color_indices = np.zeros(o.particle_count, dtype=int)
        self.colors = self.color_palette[self.color_indices].copy()
        self.life = np.zeros(o.firework_pool)
        self._firework_cursor = 0
        rows = max(2, int(round(o.mask_columns * self.height / self.width)))
        self.mask = np.zeros((rows, o.mask_columns), dtype=bool)
        self._grid_x, self._grid_y = np.meshgrid(
            (np.arange(o.mask_columns) + .5) * self.width / o.mask_columns,
            (np.arange(rows) + .5) * self.height / rows)
        self.constellations = self._make_constellations()
        anchor_index = 0
        for constellation in self.constellations:
            n = len(constellation['vertices'])
            self.positions[anchor_index:anchor_index + n] = constellation['vertices']
            self.anchors[anchor_index:anchor_index + n] = constellation['vertices']
            self.base_brightness[anchor_index:anchor_index + n] = o.base_brightness
            anchor_index += n
        self.phase = 'ready'
        self.paused = False
        self.started_ns = None
        self._pause_ns = None
        self._paused_ns = 0
        self._now_ns = None
        self._animation_ns = None
        self._capture_barrier = 0
        self._last_capture_ns = None
        self._last_valid_capture_ns = None
        self._last_valid_received_ns = None
        self._tracking = False
        self._stable_anchor = None
        self._stable_ns = 0
        self._cooldown_until_ns = 0
        self.halo_position = None
        self._halo_target = None
        self._halo_velocity = np.zeros(2)
        self.halo_opacity = 0.
        self.firework_count = 0
        self._trajectory = []
        self._events = []
        self._summary_sent = False
        self._reason_code = 0
        self._finished_elapsed_s = None

    def _make_constellations(self):
        result = []
        radius = self.settings.constellation_footprint_px
        for ident, (name, points, edges) in enumerate(_CONSTELLATIONS[:self.settings.constellation_count]):
            vertices = np.array(points) * (self.width - 1, self.height - 1)
            footprint = np.zeros_like(self.mask)
            for x, y in vertices:
                footprint |= np.hypot(self._grid_x - x, self._grid_y - y) <= radius
            for start, end in edges:
                a, b = vertices[start], vertices[end]
                segment = b - a
                t = np.clip(((self._grid_x - a[0]) * segment[0] +
                             (self._grid_y - a[1]) * segment[1]) /
                            float(segment @ segment), 0., 1.)
                footprint |= np.hypot(self._grid_x - a[0] - t * segment[0],
                                      self._grid_y - a[1] - t * segment[1]) <= radius
            # A tiny logical surface must still have a well-defined footprint.
            if not footprint.any():
                for x, y in vertices:
                    row = min(self.mask.shape[0] - 1, int(y / self.height * self.mask.shape[0]))
                    col = min(self.mask.shape[1] - 1, int(x / self.width * self.mask.shape[1]))
                    footprint[row, col] = True
            result.append(dict(id=ident, name=name, vertices=vertices,
                               edges=np.array(edges, dtype=int), footprint=footprint,
                               area=int(footprint.sum()), coverage=0., reveal_alpha=0.,
                               revealed_ns=None))
        return result

    def _observe_now(self, now_ns):
        now_ns = _timestamp(now_ns, 'now_ns')
        if self._now_ns is not None and now_ns < self._now_ns:
            raise ValueError('now_ns 不得倒退')
        self._now_ns = now_ns
        return now_ns

    def presented(self, now_ns):
        now_ns = self._observe_now(now_ns)
        if self.phase != 'ready':
            return
        self.started_ns = now_ns
        self._animation_ns = now_ns
        self._capture_barrier = now_ns
        self.phase = 'playing'

    start = presented

    def _elapsed(self, now_ns):
        if self._finished_elapsed_s is not None:
            return self._finished_elapsed_s
        if self.started_ns is None:
            return 0.
        pending = now_ns - self._pause_ns if self.paused else 0
        return max(0., (now_ns - self.started_ns - self._paused_ns - pending) / 1e9)

    def pause(self, now_ns):
        now_ns = self._observe_now(now_ns)
        if self.paused or self.phase not in {'playing', 'replay'}:
            return
        self._advance_phase(now_ns)
        if self.phase not in {'playing', 'replay'}:
            return
        self.paused = True
        self._pause_ns = now_ns
        self._break_tracking()

    def resume(self, now_ns):
        now_ns = self._observe_now(now_ns)
        if not self.paused:
            return
        self._paused_ns += now_ns - self._pause_ns
        self._pause_ns = None
        self.paused = False
        self._animation_ns = now_ns
        self._capture_barrier = now_ns
        self._break_tracking()

    def _break_tracking(self):
        self._tracking = False
        self._last_valid_capture_ns = None
        self._stable_anchor = None
        self._stable_ns = 0

    def _event(self, code, now_ns, *, constellation_id=-1):
        self._events.append(('starfield_event', dict(event_ns=int(now_ns),
            event_code=int(code), constellation_id=int(constellation_id),
            firework_count=int(self.firework_count), found_count=int(self.found_count),
            elapsed_ms=float(min(self._elapsed(now_ns), self.settings.round_s) * 1000))))

    @property
    def found_count(self):
        return sum(c['revealed_ns'] is not None for c in self.constellations)

    def _advance_phase(self, now_ns):
        if self.paused or self.started_ns is None or self.phase == 'finished':
            return
        elapsed = self._elapsed(now_ns)
        if self.phase == 'playing' and elapsed >= self.settings.round_s:
            self.phase = 'replay'
            self._break_tracking()
            self.halo_opacity = 0.
            self._event(3, now_ns)
        if self.phase == 'replay' and elapsed >= self.settings.round_s + self.settings.replay_s:
            self._finished_elapsed_s = self.settings.round_s + self.settings.replay_s
            self.phase = 'finished'
            self._summary(now_ns)

    def ingest(self, position, capture_ns, now_ns, valid=True):
        """Return whether a fresh valid capture contributed evidence.

        New invalid captures break the stable interval immediately. Old or
        duplicate captures are ignored and cannot renew the stale timeout.
        """
        now_ns = self._observe_now(now_ns)
        capture_ns = _timestamp(capture_ns, 'capture_ns')
        self._advance_phase(now_ns)
        if self.phase != 'playing' or self.paused:
            return False
        if self._last_capture_ns is not None and capture_ns <= self._last_capture_ns:
            return False
        # Invalid clock domains must not poison the high-water mark: a single
        # future timestamp would otherwise suppress all subsequent real frames.
        if capture_ns < self._capture_barrier or capture_ns > now_ns:
            self._break_tracking()
            return False
        self._last_capture_ns = capture_ns
        if not valid or now_ns - capture_ns > self.settings.stale_ms * 1e6:
            self._break_tracking()
            return False
        try:
            point = _point(position)
        except (ValueError, TypeError, OverflowError):
            self._break_tracking()
            return False
        previous = self._last_valid_capture_ns
        gap = 0 if previous is None else capture_ns - previous
        contiguous = previous is not None and gap <= self.settings.max_capture_gap_ms * 1e6
        if not contiguous:
            self._stable_anchor = None
            self._stable_ns = 0
        self._tracking = True
        self._last_valid_capture_ns = capture_ns
        self._last_valid_received_ns = now_ns
        self._halo_target = point.copy()
        if self.halo_position is None:
            self.halo_position = point.copy()
        self._trajectory.append((self._elapsed(capture_ns), float(point[0]), float(point[1])))
        self._illuminate(point, now_ns)
        if capture_ns < self._cooldown_until_ns:
            self._stable_anchor = None
            self._stable_ns = 0
        elif self._stable_anchor is None or math.hypot(*((point - self._stable_anchor) /
                                                    (self.radii * self.settings.stable_radius_factor))) > 1:
            self._stable_anchor = point.copy()
            self._stable_ns = 0
        else:
            self._stable_ns += gap
            if self._stable_ns >= self.settings.bloom_ms * 1e6:
                self._bloom(point, now_ns)
                self._cooldown_until_ns = capture_ns + int(self.settings.cooldown_ms * 1e6)
                self._stable_anchor = None
                self._stable_ns = 0
        return True

    def _illuminate(self, point, now_ns):
        # Ellipses use the same independent x/y scale as the physical error ring.
        with np.errstate(over='ignore'):
            self.mask |= np.hypot((self._grid_x - point[0]) / self.halo_radii[0],
                                  (self._grid_y - point[1]) / self.halo_radii[1]) <= 1.
        for constellation in self.constellations:
            constellation['coverage'] = float(np.count_nonzero(
                self.mask & constellation['footprint']) / constellation['area'])
            if (constellation['revealed_ns'] is None and
                    constellation['coverage'] > self.settings.reveal_threshold):
                constellation['revealed_ns'] = now_ns
                self._event(2, now_ns, constellation_id=constellation['id'])

    def _bloom(self, point, now_ns):
        o = self.settings
        indices = (np.arange(o.firework_particles) + self._firework_cursor) % o.firework_pool
        self._firework_cursor = int((self._firework_cursor + o.firework_particles) % o.firework_pool)
        slots = self.background_count + indices
        angle = self.rng.uniform(0, math.tau, len(indices))
        speed = self.rng.uniform(.35, 1., len(indices)) * o.firework_speed_px_s
        self.positions[slots] = point
        self.velocities[slots] = np.column_stack((np.cos(angle), np.sin(angle))) * speed[:, None]
        self.life[indices] = o.firework_life_s
        self.brightness[slots] = 1.
        self.color_indices[slots] = self.rng.integers(1, len(self.color_palette), len(indices))
        self.colors[slots] = self.color_palette[self.color_indices[slots]]
        self.firework_count += 1
        self._event(1, now_ns)

    def update(self, now_ns):
        """Visual time only: no mask changes, evidence samples or new blooms."""
        now_ns = self._observe_now(now_ns)
        self._advance_phase(now_ns)
        if self.paused or self.phase not in {'playing', 'replay'}:
            return
        if self._animation_ns is None:
            self._animation_ns = now_ns
            return
        elapsed = (now_ns - self._animation_ns) / 1e9
        self._animation_ns = now_ns
        if elapsed <= 0:
            return
        if (self._last_valid_capture_ns is None or
                now_ns - self._last_valid_capture_ns > self.settings.stale_ms * 1e6):
            self._break_tracking()
        o = self.settings
        target_opacity = 1. if self._tracking and self.phase == 'playing' else 0.
        self.halo_opacity = target_opacity + (self.halo_opacity - target_opacity) * math.exp(-elapsed / o.halo_fade_s)
        if self.halo_position is not None and self._halo_target is not None:
            # Exact critically damped solution for a held target; unlike Euler
            # this remains stable when display callback spacing is uneven.
            omega = math.tau * o.spring_hz
            offset = self.halo_position - self._halo_target
            derivative = self._halo_velocity + omega * offset
            decay = math.exp(-omega * elapsed)
            self.halo_position = self._halo_target + (offset + derivative * elapsed) * decay
            self._halo_velocity = (self._halo_velocity - omega * derivative * elapsed) * decay
        dt = min(elapsed, o.max_animation_step_s)
        n = self.background_count
        positions, velocity = self.positions[:n], self.velocities[:n]
        acceleration = (self.anchors - positions) * o.return_strength
        gain = np.zeros(n)
        if self.halo_position is not None and self.halo_opacity > .001:
            delta = self.halo_position - positions
            distance = np.hypot(delta[:, 0] / self.halo_radii[0], delta[:, 1] / self.halo_radii[1])
            gain = np.clip(1. - distance, 0., 1.) * self.halo_opacity
            acceleration += delta * (gain * o.attraction_strength)[:, None]
        velocity[:] = (velocity + acceleration * dt) * math.exp(-o.particle_drag * dt)
        positions += velocity * dt
        self.brightness[:n] = np.clip(self.base_brightness + o.brightness_gain * gain, 0., 1.)
        self.life[:] = np.maximum(0., self.life - elapsed)
        active = self.life > 0
        firework_velocity = self.velocities[n:]
        firework_velocity *= math.exp(-o.firework_drag * dt)
        self.positions[n:][active] += firework_velocity[active] * dt
        self.brightness[n:] = self.life / o.firework_life_s
        for constellation in self.constellations:
            if constellation['revealed_ns'] is not None:
                constellation['reveal_alpha'] = min(1., constellation['reveal_alpha'] + elapsed / o.reveal_s)

    def _summary(self, now_ns):
        if self._summary_sent:
            return
        self._summary_sent = True
        self._events.append(('starfield_summary', dict(event_ns=int(now_ns),
            firework_count=int(self.firework_count), found_count=int(self.found_count),
            constellation_ids=[int(c['id']) for c in self.constellations if c['revealed_ns'] is not None],
            elapsed_ms=float(min(self._elapsed(now_ns), self.settings.round_s) * 1000),
            completed=int(self._reason_code == 0 and self._elapsed(now_ns) >= self.settings.round_s),
            reason_code=int(self._reason_code))))

    def close(self, now_ns, reason='escape'):
        now_ns = self._observe_now(now_ns)
        if self.phase == 'finished':
            return
        # Only numeric reasons go to recording, never UI text or trajectory.
        reasons = {'completed': 0, 'escape': 1, 'window_closed': 2,
                   'fullscreen_exited': 3, 'camera_paused': 4,
                   'prediction_error': 5, 'screen_changed': 6,
                   'conditions_changed': 7, 'overlay_closed': 8,
                   'recording_failed': 9}
        self._reason_code = reasons.get(reason, 10)
        self._finished_elapsed_s = self._elapsed(now_ns)
        self._summary(now_ns)
        self.phase = 'finished'
        self._break_tracking()
        self.halo_opacity = 0.

    def drain_events(self):
        result, self._events = self._events, []
        return result

    def snapshot(self):
        now_ns = self._now_ns if self._now_ns is not None else 0
        elapsed = self._elapsed(now_ns)
        replay_progress = float(np.clip((elapsed - self.settings.round_s) / self.settings.replay_s, 0., 1.))
        trajectory = np.array([(x, y) for _, x, y in self._trajectory], dtype=float).reshape(-1, 2)
        times = np.array([t for t, _, _ in self._trajectory], dtype=float)
        visible = times <= replay_progress * self.settings.round_s if self.phase in {'replay', 'finished'} else np.zeros(len(times), dtype=bool)
        return dict(positions=_readonly(self.positions), brightness=_readonly(self.brightness),
                    sizes=_readonly(self.sizes), colors=_readonly(self.colors),
                    color_palette=_readonly(self.color_palette), color_indices=_readonly(self.color_indices),
                    halo_position=None if self.halo_position is None else tuple(float(v) for v in self.halo_position),
                    halo_opacity=float(self.halo_opacity), halo_radii=tuple(float(v) for v in self.halo_radii),
                    constellations=tuple(dict(id=c['id'], name=c['name'], vertices=_readonly(c['vertices']),
                        edges=_readonly(c['edges']), coverage=float(c['coverage']),
                        reveal_alpha=float(c['reveal_alpha'])) for c in self.constellations),
                    trajectory=_readonly(trajectory), trajectory_times=_readonly(times),
                    replay_trajectory=_readonly(trajectory[visible]), replay_progress=replay_progress,
                    phase=self.phase, paused=self.paused, firework_count=int(self.firework_count),
                    found_count=int(self.found_count), elapsed_s=float(min(elapsed, self.settings.round_s)),
                    remaining_s=float(max(0., self.settings.round_s - elapsed)))
