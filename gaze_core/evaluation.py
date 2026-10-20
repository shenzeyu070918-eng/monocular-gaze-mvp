"""Unfiltered, unclipped, equal-target evaluation and camera-space ray errors.

Camera axes follow geometry.py: X camera-right (viewer-left), Y down, Z
out of the screen. Screen points lie on Z=0. Positive yaw error is toward
the viewer's right; positive pitch error is down. Components are differences
of ray headings/elevations, not atan(position_error / estimated_distance).
"""
import math
from collections import defaultdict
import numpy as np
from .devices import finite


def screen_to_camera(point, geometry):
    if not geometry.measured or geometry.width_cm is None or geometry.height_cm is None:
        raise ValueError('角度与厘米统计需要实测屏幕尺寸')
    u, v = (finite(value, 'screen coordinate') for value in point)
    return np.array([(geometry.width_cm/2+geometry.horizontal_cm-u*geometry.width_cm)/100,
                     (v*geometry.height_cm+geometry.top_cm)/100, 0.], dtype=float)


def screen_ray(origin_m, point, geometry):
    """Unit direction from the actual face origin to an unbounded screen point."""
    if not isinstance(origin_m, (list, tuple, np.ndarray)) or len(origin_m) != 3:
        raise ValueError('origin_m 必须为三维面部中心')
    origin = np.array([finite(v, 'origin_m') for v in origin_m])
    if origin[2] <= 0:
        raise ValueError('origin_m 必须位于屏幕前方')
    direction = screen_to_camera(point,geometry)-origin
    return direction/np.linalg.norm(direction)


def ray_angle(a,b):
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a,b))),float(np.dot(a,b))))


def ray_errors(origin_m, predicted, target, geometry):
    p,t = (screen_ray(origin_m,point,geometry) for point in (predicted,target))
    angle = ray_angle(t,p)
    yaw = lambda r: math.degrees(math.atan2(-r[0], -r[2]))
    pitch = lambda r: math.degrees(math.atan2(r[1], math.hypot(r[0],r[2])))
    return dict(angle_deg=angle, yaw_deg=(yaw(p)-yaw(t)+180)%360-180, pitch_deg=pitch(p)-pitch(t))


def _median(values):
    return float(np.median(values)) if values else None


def _slope(targets, predictions):
    if len(targets) < 2:
        return None
    x, y = np.asarray(targets), np.asarray(predictions)
    delta = x-x.mean()
    denominator = float(delta@delta)
    return float(delta@(y-y.mean())/denominator) if denominator > 0 else None


def _aggregate(points, expected):
    available = [p for p in points if p['error_cm'] is not None]
    radial = [p['error_cm'] for p in available]
    angular = [p for p in points if p['angle_deg'] is not None]
    return dict(expected_targets=expected, valid_targets=len(available), complete=len(available)==expected,
                frame_count=sum(p['frame_count'] for p in points),
                valid_frame_count=sum(p['valid_frame_count'] for p in points),
                missing_origin_frames=sum(p['missing_origin_frames'] for p in points),
                invalid_origin_frames=sum(p['invalid_origin_frames'] for p in points),
                angle_frame_count=sum(p['angle_frame_count'] for p in points),
                angle_target_count=len(angular),
                mean_cm=float(np.mean(radial)) if radial else None,
                p90_cm=float(np.percentile(radial,90)) if radial else None,
                within15=float(np.mean(np.asarray(radial)<=1.5)) if radial else None,
                within2=float(np.mean(np.asarray(radial)<=2)) if radial else None,
                horizontal_cm=float(np.mean([p['horizontal_cm'] for p in available])) if available else None,
                vertical_cm=float(np.mean([p['vertical_cm'] for p in available])) if available else None,
                angle_deg=float(np.mean([p['angle_deg'] for p in angular])) if angular else None,
                yaw_deg=float(np.mean([p['yaw_deg'] for p in angular])) if angular else None,
                pitch_deg=float(np.mean([p['pitch_deg'] for p in angular])) if angular else None,
                horizontal_scale=_slope([p['target'][0] for p in points if p['prediction'] is not None],
                                        [p['prediction'][0] for p in points if p['prediction'] is not None]),
                vertical_scale=_slope([p['target'][1] for p in points if p['prediction'] is not None],
                                      [p['prediction'][1] for p in points if p['prediction'] is not None]))


def evaluate(frames, targets, geometry, matrix=None):
    """Frames are already selected by capture-time stable windows, not response time.

    Keep missing targets explicit. Radial cm errors and signed/angle components
    each use a per-target median before equal-target means. Missing origin only
    excludes angular statistics, never position error or frame coverage.
    """
    ids = set()
    for target in targets:
        if target['target_id'] in ids or target['region'] not in {'center','edge'}:
            raise ValueError('目标 ID 必须唯一且 region 必须为 center/edge')
        ids.add(target['target_id'])
        if len(target['target']) != 2: raise ValueError('目标必须为二维')
        for value in target['target']: finite(value, 'target')
    lookup = defaultdict(list)
    for frame in frames:
        if frame['target_id'] not in ids: raise ValueError('帧不属于本次评估目标')
        lookup[frame['target_id']].append(frame)
    if matrix is not None:
        array = np.asarray(matrix, dtype=float)
        if array.shape != (3,2) or not np.isfinite(array).all(): raise ValueError('矩阵必须为有限 3×2')
    groups = {}
    measured = bool(geometry.measured and geometry.width_cm and geometry.height_cm)
    for group in (['A','B'] if matrix is not None else ['A']):
        points = []
        for target in targets:
            rows = lookup[target['target_id']]
            predictions, errors, xs, ys, angles, yaws, pitches = [], [], [], [], [], [], []
            missing = invalid = angle_count = 0
            for frame in rows:
                if not frame['valid'] or frame.get('raw') is None: continue
                raw = np.array([finite(v,'raw') for v in frame['raw']])
                if raw.shape != (2,): raise ValueError('raw 必须为二维')
                predicted = np.append(raw,1)@array if group=='B' else raw
                predictions.append(predicted)
                if measured:
                    dx = float((predicted[0]-target['target'][0])*geometry.width_cm)
                    dy = float((predicted[1]-target['target'][1])*geometry.height_cm)
                    errors.append(math.hypot(dx,dy));xs.append(dx);ys.append(dy)
                if frame.get('origin_m') is None:
                    missing += 1
                elif measured:
                    try:
                        angular = ray_errors(frame['origin_m'], predicted, target['target'], geometry)
                    except (ValueError, TypeError): invalid += 1
                    else:
                        angle_count += 1
                        angles.append(angular['angle_deg']);yaws.append(angular['yaw_deg']);pitches.append(angular['pitch_deg'])
            points.append(dict(target_id=target['target_id'],region=target['region'], target=list(target['target']),
                               frame_count=len(rows),valid_frame_count=len(predictions),
                               prediction=np.median(predictions,axis=0).tolist() if predictions else None,
                               error_cm=_median(errors),horizontal_cm=_median(xs),vertical_cm=_median(ys),
                               angle_deg=_median(angles),yaw_deg=_median(yaws),pitch_deg=_median(pitches),
                               missing_origin_frames=missing,invalid_origin_frames=invalid,angle_frame_count=angle_count))
        regions = {region:_aggregate([p for p in points if p['region']==region], sum(t['region']==region for t in targets)) for region in ('center','edge')}
        groups[group] = dict(points=points, all=_aggregate(points,len(targets)), **regions)
    return dict(measured=measured, groups=groups, aggregation='median_per_target_then_equal_target_summary',
                coordinates='full_screen_normalized_unfiltered_unclipped', angle_definition='camera_space_origin_to_prediction_vs_target; yaw_right_positive; pitch_down_positive')


def validate_summary(summary):
    """Strict numeric result whitelist; recording cannot become an image container."""
    from .recorder import number, text_value, vector
    root = {'measured','groups','aggregation','coordinates','angle_definition'}
    if not isinstance(summary,dict) or set(summary)!=root or type(summary['measured']) is not bool:
        raise ValueError('评估汇总字段无效')
    for key in root-{'measured','groups'}: text_value(summary[key],key)
    groups=summary['groups']
    if not isinstance(groups,dict) or set(groups) not in ({'A'},{'A','B'}): raise ValueError('评估分组无效')
    aggregate_keys=set(_aggregate([],0))
    point_keys={'target_id','region','target','frame_count','valid_frame_count','prediction','error_cm',
                'horizontal_cm','vertical_cm','angle_deg','yaw_deg','pitch_deg','missing_origin_frames','invalid_origin_frames','angle_frame_count'}
    for group in groups.values():
        if not isinstance(group,dict) or set(group)!={'points','all','center','edge'} or not isinstance(group['points'],list):raise ValueError('评估区域字段无效')
        for region in ('all','center','edge'):
            values=group[region]
            if not isinstance(values,dict) or set(values)!=aggregate_keys: raise ValueError('评估指标字段无效')
            for key,value in values.items():
                if key=='complete':
                    if type(value) is not bool: raise ValueError('完整性字段无效')
                else:number(value,key,nullable=True)
        for point in group['points']:
            if not isinstance(point,dict) or set(point)!=point_keys:raise ValueError('单点评估字段无效')
            text_value(point['target_id'],'target_id')
            if point['region'] not in {'center','edge'}:raise ValueError('region 无效')
            vector(point['target'],2,'target');vector(point['prediction'],2,'prediction',nullable=True)
            for key in point_keys-{'target_id','region','target','prediction'}: number(point[key],key,nullable=True)
