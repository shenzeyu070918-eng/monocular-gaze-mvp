"""Strict numeric results for the star Demo, without visual or gaze snapshots.

The model emits numeric codes rather than constellation names.  These payloads
deliberately cannot contain a trajectory, particle arrays, communication text,
images, or arbitrary extra metadata.  Only the existing Demo run/group identity
is added when forwarding model events into an already active recorder.
"""
import math
from numbers import Integral

from .devices import finite


STARFIELD_EVENT_FIELDS = frozenset({
    'run_id', 'group', 'event_ns', 'event_code', 'constellation_id',
    'firework_count', 'found_count', 'elapsed_ms',
})
STARFIELD_SUMMARY_FIELDS = frozenset({
    'run_id', 'group', 'event_ns', 'firework_count', 'found_count',
    'constellation_ids', 'elapsed_ms', 'completed', 'reason_code',
})
STARFIELD_FIELDS = {
    'starfield_event': STARFIELD_EVENT_FIELDS,
    'starfield_summary': STARFIELD_SUMMARY_FIELDS,
}


def _integer(value, name, *, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, Integral) or value < minimum
            or (maximum is not None and value > maximum)):
        raise ValueError(f'星空 {name} 必须为允许范围内的整数')
    try:
        representable = math.isfinite(float(value))
    except (ValueError, OverflowError):
        representable = False
    if not representable:
        raise ValueError(f'星空 {name} 必须为有限整数')
    return int(value)


def validate_starfield_event(kind, fields):
    allowed = STARFIELD_FIELDS.get(kind)
    if allowed is None or not isinstance(fields, dict) or set(fields) != allowed:
        raise ValueError('星空记录字段不完整或包含未允许的数据')
    run_id = fields['run_id']
    if (not isinstance(run_id, str) or len(run_id) != 32
            or any(c not in '0123456789abcdef' for c in run_id)):
        raise ValueError('星空 run_id 必须为 32 位十六进制编号')
    group = fields['group']
    if not isinstance(group, str) or group not in {'B', 'C'}:
        raise ValueError('星空分组必须为 B 或 C')
    _integer(fields['event_ns'], 'event_ns')
    fireworks = _integer(fields['firework_count'], 'firework_count')
    found = _integer(fields['found_count'], 'found_count', maximum=5)
    if finite(fields['elapsed_ms'], '星空 elapsed_ms') < 0:
        raise ValueError('星空 elapsed_ms 必须非负')
    if kind == 'starfield_event':
        code = _integer(fields['event_code'], 'event_code', minimum=1, maximum=3)
        ident = _integer(fields['constellation_id'], 'constellation_id', minimum=-1, maximum=4)
        if (code == 2 and (ident < 0 or found < 1)) or (code != 2 and ident != -1):
            raise ValueError('星空事件代码与星座编号/计数不一致')
        if code == 1 and fireworks < 1:
            raise ValueError('烟花事件必须包含已经发生的烟花计数')
    else:
        ids = fields['constellation_ids']
        if not isinstance(ids, (list, tuple)):
            raise ValueError('星空 constellation_ids 必须为编号数组')
        values = [_integer(v, 'constellation_ids', maximum=4) for v in ids]
        if len(set(values)) != len(values) or len(values) != found:
            raise ValueError('星空星座编号不得重复，且必须与 found_count 一致')
        completed = _integer(fields['completed'], 'completed', maximum=1)
        reason = _integer(fields['reason_code'], 'reason_code', maximum=10)
        if completed and reason != 0:
            raise ValueError('星空已完成标记与结束原因不一致')


def starfield_record(kind, payload, *, run_id, group):
    """Add only a run identity and group; never accept or strip unknown fields."""
    if not isinstance(payload, dict) or {'run_id', 'group'} & set(payload):
        raise ValueError('星空模型事件不得自行提供会话元数据')
    result = dict(payload, run_id=run_id, group=group)
    validate_starfield_event(kind, result)
    return result
