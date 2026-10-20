"""Capture-clock calibration/check tasks, independent of Qt and persistence.

Only original numeric predictions enter this module. Target changes are dated
by the window's paint feedback; late inference results are assigned by their
capture time, rather than whichever target happens to be visible on receipt.
"""
from copy import deepcopy
from numbers import Integral
import time
import uuid

import numpy as np

from gaze_core.devices import finite
from gaze_core.display import calibration_matrix
from gaze_core.evaluation import evaluate, evaluation_targets
from gaze_core.geometry import fit_affine


def _nanoseconds(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError('任务时间必须为非负单调时钟纳秒整数')
    return int(value)


class TaskRunner:
    """One task; caller owns raw-result delivery, cancellation and file writes.

    ``advance`` schedules transitions; ``presented`` acknowledges the target
    drawn by Qt. ``ingest`` consumes an ordered, deduplicated numeric Prediction
    stream. The first prediction captured after the final cutoff is a watermark
    proving earlier inference results were delivered, even when it is invalid.
    No profile is written or activated here.
    """
    def __init__(self, kind, settings, *, matrix=None, calibration_id='none',
                 clock_ns=time.perf_counter_ns, task_id=None, run_id=None, rng=None):
        if kind not in {'calibrate', 'evaluate'}:
            raise ValueError('任务类型必须为 calibrate 或 evaluate')
        self.kind, self.settings, self.geometry = kind, settings.evaluation, settings.geometry
        self._clock = clock_ns
        self.task_id = task_id or uuid.uuid4().hex
        self.run_id = run_id or uuid.uuid4().hex
        self.calibration_id = uuid.uuid4().hex if kind == 'calibrate' else calibration_id
        array = calibration_matrix(matrix)
        self.matrix = None if array is None else array.tolist()
        self.targets = (evaluation_targets(settings.evaluation, settings.jump.margin, rng=rng)
                        if kind == 'evaluate' else
                        [dict(target_id=f'calibration-{i+1}', target=list(p), region='center')
                         for i, p in enumerate(self.settings.calibration_points)])
        self.state, self.reason, self.result, self.error = 'idle', None, None, None
        self.index = 0
        self._events, self._windows, self._frames, self._distances = [], [], [], []
        self._seen = set()
        self._cutoff_ns = None
        self._last_capture_ns = None
        self._began_ns = None

    @property
    def active(self):
        return self.state in {'presenting', 'sampling', 'finishing'}

    @property
    def current_target(self):
        if self.state not in {'presenting', 'sampling'}:
            return None
        return dict(index=self.index, **deepcopy(self.targets[self.index]))

    def begin(self):
        if self.state != 'idle':
            raise ValueError('任务只能开始一次')
        self._began_ns = _nanoseconds(self._clock())
        self.state = 'presenting'

    def presented(self, event_ns=None):
        """Acknowledge a newly painted point; subsequent repaints do nothing."""
        if self.state != 'presenting':
            return False
        now = _nanoseconds(self._clock() if event_ns is None else event_ns)
        if self._windows and now < self._windows[-1]['start_ns']:
            raise ValueError('目标显示时间必须单调递增')
        if self._windows:
            self._windows[-1]['end_ns'] = now
        self._windows.append(dict(index=self.index, start_ns=now, end_ns=None, frames=[]))
        point = self.targets[self.index]
        self._events.append(dict(type='target', action='appear' if self.index == 0 else 'switch',
                                 task_type=self.kind, task_id=self.task_id, index=self.index,
                                 target=deepcopy(point['target']), target_id=point['target_id'],
                                 **({'region': point['region']} if self.kind == 'evaluate' else {}),
                                 stage_target=None, event_ns=now, browser_time_ms=None))
        self.state = 'sampling'
        return True

    def ingest(self, prediction):
        if not self.active:
            return False
        sequence = prediction.sequence
        if isinstance(sequence, bool) or not isinstance(sequence, Integral) or sequence < 0:
            raise ValueError('帧编号必须是非负整数')
        if int(sequence) in self._seen:
            return False
        capture_ns = _nanoseconds(prediction.captured_ns)
        if self._last_capture_ns is not None and capture_ns <= self._last_capture_ns:
            return False
        self._seen.add(int(sequence))
        self._last_capture_ns = capture_ns
        data = prediction.data
        for window in reversed(self._windows):
            if capture_ns < window['start_ns']:
                continue
            if window['end_ns'] is not None and capture_ns >= window['end_ns']:
                break
            age_ms = (capture_ns-window['start_ns'])/1e6
            if age_ms < self.settings.settle_ms:
                break
            if self.kind == 'evaluate' and age_ms > self.settings.dwell_ms:
                break
            valid = bool(data.get('valid')) and data.get('raw') is not None
            raw = None
            if valid:
                values = np.asarray(data['raw'], dtype=float)
                if values.shape != (2,) or not np.isfinite(values).all():
                    raise ValueError('校准/核对原始坐标必须为有限二维数值')
                raw = values.tolist()  # Never filter or clip these positions.
            row = dict(target_id=self.targets[window['index']]['target_id'], valid=valid,
                       raw=raw, origin_m=deepcopy(data.get('origin_m')))
            window['frames'].append(row)
            self._frames.append(row)
            if valid and data.get('distance_cm') is not None:
                distance = finite(data['distance_cm'], 'distance_cm')
                self._distances.append(distance)
            break
        if self.state == 'finishing' and capture_ns >= self._cutoff_ns:
            self._complete()
        return True

    def advance(self, now_ns=None):
        if not self.active:
            return
        now = _nanoseconds(self._clock() if now_ns is None else now_ns)
        if self.state == 'presenting':
            reference = self._windows[-1]['start_ns'] if self._windows else self._began_ns
            if now-reference > self.settings.max_dwell_ms*1e6:
                self.cancel('target_not_presented', now_ns=now)
            return
        if self.state == 'finishing':
            if now-self._cutoff_ns > self.settings.max_dwell_ms*1e6:
                self.cancel('prediction_timeout', now_ns=now)
            return
        window = self._windows[-1]
        age_ms = (now-window['start_ns'])/1e6
        enough = sum(row['valid'] for row in window['frames']) >= self.settings.calibration_min_samples
        if age_ms > self.settings.dwell_ms and (self.kind == 'evaluate' or enough):
            if self.index+1 < len(self.targets):
                self.index += 1
                self.state = 'presenting'
            else:
                window['end_ns'] = now
                self._cutoff_ns = now
                self.state = 'finishing'
                if self._last_capture_ns is not None and self._last_capture_ns >= now:
                    self._complete()
        elif age_ms > self.settings.max_dwell_ms:
            self.cancel('insufficient_samples', now_ns=now)

    def _target_end(self, reason, now):
        if not self._windows:
            return
        displayed = self._windows[-1]
        point = self.targets[displayed['index']]
        self._events.append(dict(type='target', action='end', task_type=self.kind, task_id=self.task_id,
                                 index=displayed['index'], target=deepcopy(point['target']),
                                 target_id=point['target_id'],
                                 **({'region': point['region']} if self.kind == 'evaluate' else {}),
                                 stage_target=None, reason=reason, event_ns=now, browser_time_ms=None))

    def _complete(self):
        result = dict(kind=self.kind, task_id=self.task_id, run_id=self.run_id,
                      calibration_id=self.calibration_id,
                      sample_count=sum(row['valid'] for row in self._frames),
                      estimated_distance_cm_median=float(np.median(self._distances)) if self._distances else None,
                      estimated_distance_sample_count=len(self._distances))
        if self.kind == 'calibrate':
            samples = [dict(target=self.targets[w['index']]['target'],
                            raw=np.median([r['raw'] for r in w['frames'] if r['valid']], axis=0).tolist())
                       for w in self._windows]
            try:
                result['matrix'] = fit_affine(samples)
            except ValueError as exc:
                self.error = str(exc)
                self.cancel('calibration_failed')
                return
        else:
            result['matrix'] = deepcopy(self.matrix)
            result['summary'] = evaluate(self._frames, self.targets, self.geometry, self.matrix)
        self._target_end('completed', self._cutoff_ns)
        self.result, self.state, self.reason = result, 'completed', 'completed'

    def cancel(self, reason='cancelled', *, now_ns=None):
        if not self.active:
            return False
        now = _nanoseconds(self._clock() if now_ns is None else now_ns)
        self._target_end(reason, self._cutoff_ns if self.state == 'finishing' else now)
        self.state, self.reason, self.result = 'cancelled', str(reason), None
        self._frames.clear()
        self._distances.clear()
        for window in self._windows:
            window['frames'].clear()
        return True

    def drain_events(self):
        events, self._events = self._events, []
        return events
