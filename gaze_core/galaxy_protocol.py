"""Loopback protocol whitelist and browser client geometry, all math in Python."""
import math
from numbers import Real,Integral
from .devices import finite
from .demo_coordinates import DemoCoordinates

GEOMETRY={'screenX','screenY','innerWidth','innerHeight','outerWidth','outerHeight','dpr',
          'screenWidth','screenHeight','fullscreen','clientX','clientY','eventScreenX','eventScreenY','scale'}
FRAME={'v','seq','capture_us','completed_us','viewport','gaze_valid','gaze_state','fixation','xy','radius',
       'control','hands','distance','pattern','intro','hand_ms','hand_available','hand_detected','hand_openings','hand_error'}
TELEMETRY={'fps','p90_ms','frames','long_frames','particles','pixel_ratio','downgrades','render_median_ms','render_p90_ms'}


def numbers(value):
    if isinstance(value,(list,tuple)):
        return [numbers(v) for v in value]
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
        raise ValueError('仅允许有限数值，不接受图像、文本或bool')
    return int(value) if isinstance(value,Integral) else float(value)


def validate_frame(frame):
    if not isinstance(frame,dict) or set(frame)!=FRAME:raise ValueError('星系帧字段不完整或含未知数据')
    result={k:numbers(v) for k,v in frame.items()}
    for key in FRAME-{'xy','radius','hands','distance','hand_ms','hand_openings'}:
        if type(result[key]) is not int or result[key]<0:raise ValueError('星系状态/时间必须为非负整数')
    if result['v']!=2 or any(result[k] not in (0,1) for k in ('gaze_valid','control','intro','hand_available')):raise ValueError('星系版本/标记无效')
    if result['pattern']>2 or result['gaze_state']>5 or result['fixation']>2:raise ValueError('星系状态无效')
    if not 0<=result['hand_detected']<=2 or not 0<=result['hand_error']<=2:raise ValueError('手势诊断状态无效')
    if not isinstance(result['hand_openings'],list) or len(result['hand_openings'])!=result['hand_detected'] or any(isinstance(v,list) or not 0<=v<=1 for v in result['hand_openings']):raise ValueError('手势诊断张开度无效')
    if result['completed_us']<result['capture_us']:raise ValueError('采集/完成时钟次序无效')
    if any(not isinstance(result[k],list) or len(result[k])!=2 or any(isinstance(v,list) for v in result[k]) for k in ('xy','radius')) or min(result['radius'])<=0:raise ValueError('坐标/半径无效')
    if not isinstance(result['hands'],list) or len(result['hands'])>2 or any(not isinstance(h,list) or len(h)!=7 or any(isinstance(v,list) for v in h) or not 0<=h[2]<=1 or h[3]<0 or h[6] not in (-1,0,1) for h in result['hands']):raise ValueError('手势字段无效')
    if any(isinstance(result[k],list) or result[k]<0 for k in ('distance','hand_ms')):raise ValueError('手势尺度/耗时无效')
    return result


def viewport(screen,fields):
    if not isinstance(fields,dict) or set(fields)!=GEOMETRY:raise ValueError('视口元数据字段无效')
    f={k:finite(v,k) for k,v in fields.items()}
    if f['fullscreen']!=1 or f['scale']!=1:raise ValueError('请在内置屏全屏，页面缩放保持100%后点击进入')
    if f['dpr']<=0 or f['innerWidth']<2 or f['innerHeight']<2:raise ValueError('视口尺寸无效')
    if not math.isclose(f['dpr'],screen.dpr,rel_tol=.001,abs_tol=.001):raise ValueError('浏览器像素比与内置屏不一致，请移回内置屏并将页面缩放恢复100%')
    if not 0<=f['clientX']<f['innerWidth'] or not 0<=f['clientY']<f['innerHeight']:raise ValueError('需要页面内的真实点击核对视口')
    # Mouse screen/client coordinates describe the SAME event. Window.screenY
    # alone describes the outside frame and cannot prove the client origin.
    origin=(f['eventScreenX']-f['clientX'],f['eventScreenY']-f['clientY'])
    mapping=DemoCoordinates(screen,window_origin=origin,window_size=(f['innerWidth'],f['innerHeight']),fullscreen=True)
    if not mapping.ready:raise ValueError(mapping.reason)
    if abs(f['screenWidth']-screen.width)>1 or abs(f['screenHeight']-screen.height)>1:
        raise ValueError('浏览器不在当前内置屏，或浏览器屏幕尺寸与系统不一致')
    return mapping


def telemetry(fields):
    if not isinstance(fields,dict) or set(fields)!=TELEMETRY:raise ValueError('性能字段无效')
    r={k:finite(v,k) for k,v in fields.items()}
    if any(v<0 for v in r.values()) or not r['particles'].is_integer() or not 1<=r['particles']<=1_000_000 or r['fps']>1000:raise ValueError('性能数值超出范围')
    return r
