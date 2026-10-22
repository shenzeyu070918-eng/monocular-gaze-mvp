"""Atomic numeric-only personal calibration profiles in ignored data/profiles/."""
from datetime import datetime, timezone
import hashlib
import json
from numbers import Integral
from pathlib import Path
import os
import tempfile
from .config import ROOT
from .devices import device_name, finite


COORDINATES = dict(input='unfiltered_unclipped_raw_fullscreen_normalized',output='corrected_fullscreen_normalized',
                   origin='top_left',x='right',y='down',formula='[raw_x, raw_y, 1] @ matrix',clip=False)


def _matrix(value):
    from .display import calibration_matrix
    matrix = calibration_matrix(value)
    if matrix is None:
        raise ValueError('个人档案缺少矩阵')
    return matrix.tolist()


def _atomic_save(data, name, directory):
    """Validate before calling; replace only after the whole document is durable."""
    folder = Path(directory);folder.mkdir(parents=True,exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=name+'-',suffix='.tmp',dir=folder)
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as output:
            json.dump(data,output,ensure_ascii=False,allow_nan=False);output.write('\n');output.flush();os.fsync(output.fileno())
        destination = folder/(name+'.json')
        os.replace(temporary,destination)
        return destination
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def save_profile(matrix, *, name, device_file, screen, calibration_id, directory=None):
    from .recorder import validate_screen, text_value
    name = device_name(name)
    matrix = _matrix(matrix)
    validate_screen(screen)
    if screen.get('fullscreen') is not True:
        raise ValueError('个人校准必须在全屏下完成')
    text_value(calibration_id, 'calibration_id');text_value(device_file,'device_file',nullable=True)
    data = dict(schema_version=1, pipeline='web', device_name=name, matrix=matrix, source='nine_point',group='B',
                created_at=datetime.now(timezone.utc).isoformat(), device_file=device_file,
                calibration_id=calibration_id, screen=screen,
                coordinates=dict(COORDINATES))
    return _atomic_save(data, name, directory or ROOT/'data/profiles')


def load_profile(*, name, device_file, screen, geometry, directory=None):
    """Explicit load only. Refuse wrong device, units or screen conditions."""
    from .display import calibration_matrix
    from .recorder import validate_screen
    name=device_name(name)
    path=Path(directory or ROOT/'data/profiles')/(name+'.json')
    data=_web_profile(json.loads(path.read_text(encoding='utf-8')))
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('pipeline','web')!='web' or data.get('device_name')!=name or data.get('device_file')!=device_file:
        raise ValueError('个人档案与当前设备文件不匹配，请重新九点校准')
    coordinates=data.get('coordinates',{})
    if coordinates != dict(input='unfiltered_unclipped_raw_fullscreen_normalized',output='corrected_fullscreen_normalized',origin='top_left',x='right',y='down',formula='[raw_x, raw_y, 1] @ matrix',clip=False):
        raise ValueError('个人档案坐标定义不匹配，请重新九点校准')
    if data.get('source')!='nine_point' or data.get('group')!='B':raise ValueError('本次只支持九点个人档案')
    saved=data.get('screen');validate_screen(saved);validate_screen(screen)
    if screen.get('fullscreen') is not True or saved.get('fullscreen') is not True:
        raise ValueError('加载个人档案需要全屏')
    for key in ('width_px','height_px'):
        if screen.get(key) is None or saved.get(key)!=screen[key]:raise ValueError('个人档案屏幕逻辑尺寸不匹配')
    for key in ('width_cm','height_cm','measured','horizontal_cm','top_cm'):
        if saved.get(key)!=getattr(geometry,key):raise ValueError('个人档案实测尺寸或相机偏移不匹配')
    matrix=calibration_matrix(data.get('matrix'))
    if matrix is None:raise ValueError('个人档案缺少矩阵')
    return dict(matrix=matrix.tolist(),file=path.name,created_at=data.get('created_at'),source='nine_point',
                device_file=device_file,coordinates=coordinates)


def _fields(value, required, name):
    if not isinstance(value,dict) or set(value)!=set(required):
        raise ValueError(name+' 字段不完整或包含未允许数据（不接受图像/base64）')


def _int(value, name, minimum=0):
    if isinstance(value,bool) or not isinstance(value,Integral) or value<minimum:
        raise ValueError(name+' 必须为有效整数')
    return int(value)


def _bool(value, name):
    if type(value) is not bool:raise ValueError(name+' 必须为布尔值')
    return value


def _text(value, name, nullable=False):
    from .recorder import text_value
    text_value(value,name,nullable=nullable)
    return value


def _timestamp(value):
    _text(value,'created_at')
    try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,TypeError) as exc:raise ValueError('created_at 必须为带时区 ISO 时间') from exc
    if stamp.tzinfo is None:raise ValueError('created_at 必须为带时区 ISO 时间')
    return stamp


def _canonical_numbers(value):
    """Only call after a field whitelist; numpy scalars become JSON numbers."""
    if isinstance(value,dict):return {key:_canonical_numbers(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_canonical_numbers(item) for item in value]
    if value is None or isinstance(value,(str,bool)):return value
    if isinstance(value,Integral):return int(value)
    return finite(value,'数值')


def _fingerprint(value):
    encoded=json.dumps(_canonical_numbers(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def device_fingerprint(profile):
    """Hash calibration semantics, never file formatting, date, name or RMS."""
    if profile is None:return None
    return _fingerprint(dict(matrix=profile.matrix,distortion=profile.distortion,resolution=profile.resolution,
                             width_cm=profile.width_cm,height_cm=profile.height_cm,measured=profile.measured,
                             screen_resolution=profile.screen_resolution,horizontal_cm=profile.horizontal_cm,top_cm=profile.top_cm))


def _camera_conditions(value):
    keys={'index','width_px','height_px','grayscale','undistort_enabled','undistort_applied','undistort_alpha',
          'intrinsics_source','intrinsics_reason','calibration_resolution','matrix','distortion','input_matrix','input_distortion'}
    _fields(value,keys,'conditions.camera')
    result=dict(value)
    result['index']=_int(value['index'],'camera.index')
    for key in ('width_px','height_px'):result[key]=_int(value[key],key,1)
    for key in ('grayscale','undistort_enabled','undistort_applied'):result[key]=_bool(value[key],key)
    result['undistort_alpha']=finite(value['undistort_alpha'],'undistort_alpha')
    if not 0<=result['undistort_alpha']<=1:raise ValueError('undistort_alpha 必须在 0 到 1 之间')
    if value['intrinsics_source'] not in {'estimated','calibrated'}:raise ValueError('内参来源无效')
    if value['intrinsics_reason'] not in {None,'no_device_file','aspect_ratio_mismatch'}:raise ValueError('内参原因无效')
    size=value['calibration_resolution']
    if size is not None:
        from .devices import resolution
        result['calibration_resolution']=list(resolution(size))
    for key in ('matrix','input_matrix'):
        rows=value[key]
        if not isinstance(rows,(list,tuple)) or len(rows)!=3 or any(not isinstance(row,(list,tuple)) or len(row)!=3 for row in rows):
            raise ValueError('conditions.camera.'+key+' 必须为 3×3')
        result[key]=[[finite(item,key) for item in row] for row in rows]
    for key in ('distortion','input_distortion'):
        values=value[key]
        if not isinstance(values,(list,tuple)) or len(values)!=5:raise ValueError(key+' 必须有 5 个系数')
        result[key]=[finite(item,key) for item in values]
    return result


def _screen_conditions(value):
    keys={'x','y','width_px','height_px','device_pixel_ratio','width_cm','height_cm','measured','horizontal_cm','top_cm'}
    _fields(value,keys,'conditions.screen')
    result=dict(value)
    for key in ('x','y'):
        if isinstance(value[key],bool) or not isinstance(value[key],Integral):raise ValueError('屏幕 '+key+' 必须为整数')
        result[key]=int(value[key])
    for key in ('width_px','height_px'):result[key]=_int(value[key],key,2)
    result['device_pixel_ratio']=finite(value['device_pixel_ratio'],'device_pixel_ratio',positive=True)
    for key in ('width_cm','height_cm'):result[key]=finite(value[key],key,positive=True,nullable=True)
    result['measured']=_bool(value['measured'],'measured')
    if (result['width_cm'] is None)!=(result['height_cm'] is None) or (result['measured'] and result['width_cm'] is None):
        raise ValueError('屏幕实测宽高必须同时提供')
    for key in ('horizontal_cm','top_cm'):result[key]=finite(value[key],key)
    return result


def _conditions(value):
    _fields(value,{'device_file','device_fingerprint','camera','screen'},'conditions')
    filename=_text(value['device_file'],'device_file',True)
    fingerprint=value['device_fingerprint']
    if fingerprint is not None and (not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint)):
        raise ValueError('设备语义指纹无效')
    if (filename is None)!=(fingerprint is None):raise ValueError('设备文件与语义指纹必须同时提供')
    return dict(device_file=filename,device_fingerprint=fingerprint,camera=_camera_conditions(value['camera']),screen=_screen_conditions(value['screen']))


def build_conditions(settings, camera_metadata, screen):
    """Use negotiated resolution and actual camera preparation, not requested size."""
    from .recorder import validate_camera, validate_screen
    validate_camera(camera_metadata)
    if isinstance(screen,dict):
        validate_screen(screen)
        rect=dict(x=screen.get('screen_x',0),y=screen.get('screen_y',0),width_px=screen.get('width_px'),
                  height_px=screen.get('height_px'),device_pixel_ratio=screen.get('device_pixel_ratio'))
    else:
        rect=dict(x=screen.x,y=screen.y,width_px=screen.width,height_px=screen.height,device_pixel_ratio=screen.dpr)
    geometry=settings.geometry
    rect.update({key:getattr(geometry,key) for key in ('width_cm','height_cm','measured','horizontal_cm','top_cm')})
    metadata=camera_metadata
    camera={key:metadata[key] for key in ('width_px','height_px','intrinsics_source','matrix','distortion')}
    camera.update(index=metadata.get('camera_index',settings.camera.index),grayscale=settings.camera.grayscale,
                  intrinsics_reason=metadata.get('intrinsics_reason'),calibration_resolution=metadata.get('calibration_resolution'),
                  undistort_enabled=metadata.get('undistort_enabled',settings.camera.undistort),
                  undistort_applied=metadata.get('undistort_applied',False),undistort_alpha=settings.camera.undistort_alpha,
                  input_matrix=metadata.get('input_matrix',metadata['matrix']),input_distortion=metadata.get('input_distortion',metadata['distortion']))
    profile=settings.device_profile
    return _conditions(dict(device_file=profile.filename if profile else None,device_fingerprint=device_fingerprint(profile),camera=camera,screen=rect))


def condition_mismatch_reason(saved, current):
    """Return the first concrete mismatch, or None; both objects must be safe."""
    saved,current=_conditions(saved),_conditions(current)
    labels={'device_file':'设备文件','device_fingerprint':'设备文件语义（内参、畸变或屏幕几何）',
            'camera.index':'摄像头编号','camera.width_px':'相机实际宽度','camera.height_px':'相机实际高度',
            'camera.grayscale':'灰度设置','camera.undistort_enabled':'去畸变配置','camera.undistort_applied':'实际去畸变状态',
            'camera.matrix':'实际内参','camera.input_matrix':'原始内参','camera.distortion':'实际畸变系数',
            'camera.input_distortion':'原始畸变系数','screen.device_pixel_ratio':'屏幕缩放 DPR'}
    def compare(a,b,prefix=''):
        for key in a:
            field=prefix+key
            if isinstance(a[key],dict):
                reason=compare(a[key],b[key],field+'.')
                if reason:return reason
            elif a[key]!=b[key]:return '个人档案条件不匹配：'+labels.get(field,field)+'；请重新校准'
        return None
    return compare(saved,current)


def _calibration(value):
    required={'sample_count','estimated_distance_cm_median'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'target_count','quality'}:
        raise ValueError('calibration 只能包含 sample_count、estimated_distance_cm_median、target_count')
    result=dict(sample_count=_int(value['sample_count'],'sample_count',1),
                estimated_distance_cm_median=finite(value['estimated_distance_cm_median'],'estimated_distance_cm_median',positive=True,nullable=True))
    if 'target_count' in value:result['target_count']=_int(value['target_count'],'target_count',1)
    if 'quality' in value:
        from .calibration_quality import validate_quality
        result['quality']=validate_quality(value['quality'])
    return result


def _check(value, profile):
    from .evaluation import validate_summary
    _fields(value,{'created_at','calibration_id','matrix_id','conditions','summary','estimated_distance_cm_median',
                   'estimated_distance_sample_count','task_id'},'latest_check')
    _timestamp(value['created_at'])
    if value['calibration_id']!=profile['calibration_id'] or value['matrix_id']!=profile['matrix_id']:
        raise ValueError('核对结果与当前修正矩阵不匹配')
    reason=condition_mismatch_reason(value['conditions'],profile['conditions'])
    if reason:raise ValueError(reason)
    validate_summary(value['summary'])
    summary=value['summary'];group=summary['groups'].get('B');all_values=group['all'] if group else None
    if not summary['measured'] or not all_values or all_values['complete'] is not True or all_values['expected_targets']<=0 or all_values['expected_targets']!=all_values['valid_targets']:
        raise ValueError('半径只接受实测尺寸下完整的 B 组核对')
    mean=finite(all_values['mean_cm'],'B.mean_cm')
    if mean<0:raise ValueError('B.mean_cm 不能为负数')
    if len(group['points'])!=all_values['expected_targets'] or any(point['error_cm'] is None for point in group['points']):
        raise ValueError('B 组核对缺少有效目标')
    finite(value['estimated_distance_cm_median'],'estimated_distance_cm_median',positive=True,nullable=True)
    _int(value['estimated_distance_sample_count'],'estimated_distance_sample_count')
    _text(value['task_id'],'task_id',True)
    return _canonical_numbers(value)


def _python_profile(data, *, name=None):
    if not isinstance(data,dict) or data.get('pipeline','web')!='python':
        raise ValueError('只自动加载 pipeline=python 的个人档案；缺少 pipeline 的旧档案按 web 处理')
    keys={'schema_version','pipeline','device_name','matrix','matrix_id','source','group','created_at','device_file',
          'calibration_id','coordinates','conditions','calibration','latest_check'}
    _fields(data,keys,'Python 个人档案')
    if type(data['schema_version']) is not int or data['schema_version']!=2 or data['pipeline']!='python':
        raise ValueError('只自动加载 pipeline=python 的新版个人档案；旧档案按 web 处理')
    actual_name=device_name(data['device_name'])
    if name is not None and actual_name!=name:raise ValueError('个人档案设备名不匹配')
    _text(data['calibration_id'],'calibration_id');_timestamp(data['created_at'])
    if data['source']!='nine_point' or data['group']!='B' or data['coordinates']!=COORDINATES:
        raise ValueError('个人档案来源或全屏坐标定义不匹配')
    result=dict(data,matrix=_matrix(data['matrix']),conditions=_conditions(data['conditions']),calibration=_calibration(data['calibration']))
    if result['matrix_id']!=_fingerprint(result['matrix']):raise ValueError('个人档案矩阵 ID 不匹配')
    if result['device_file']!=result['conditions']['device_file']:raise ValueError('个人档案设备文件不匹配')
    if result['latest_check'] is not None:result['latest_check']=_check(result['latest_check'],result)
    return _canonical_numbers(result)


def _python_path(name, directory):
    return Path(directory or ROOT/'data/profiles/python')/(device_name(name)+'.json')


def save_python_profile(matrix, *, name, conditions, calibration_id, calibration, directory=None):
    name=device_name(name);conditions=_conditions(conditions);matrix=_matrix(matrix)
    data=dict(schema_version=2,pipeline='python',device_name=name,matrix=matrix,matrix_id=_fingerprint(matrix),source='nine_point',group='B',
              created_at=datetime.now(timezone.utc).isoformat(),device_file=conditions['device_file'],calibration_id=calibration_id,
              conditions=conditions,coordinates=dict(COORDINATES),calibration=calibration,latest_check=None)
    data=_python_profile(data,name=name)
    return _atomic_save(data,name,_python_path(name,directory).parent)


def load_python_profile(*, name, conditions, directory=None):
    name=device_name(name);path=_python_path(name,directory)
    data=_python_profile(json.loads(path.read_text(encoding='utf-8')),name=name)
    reason=condition_mismatch_reason(data['conditions'],conditions)
    if reason:raise ValueError(reason)
    return data


def save_python_check(summary, *, name, conditions, calibration_id, estimated_distance_cm_median=None,
                      estimated_distance_sample_count=0, task_id=None, directory=None):
    data=load_python_profile(name=name,conditions=conditions,directory=directory)
    check=dict(created_at=datetime.now(timezone.utc).isoformat(),calibration_id=calibration_id,matrix_id=data['matrix_id'],
               conditions=conditions,summary=summary,estimated_distance_cm_median=estimated_distance_cm_median,
               estimated_distance_sample_count=estimated_distance_sample_count,task_id=task_id)
    data['latest_check']=_check(check,data)
    return _atomic_save(_python_profile(data,name=name),device_name(name),_python_path(name,directory).parent)


def radius_from_profile(profile, conditions):
    """Only a complete check of this exact matrix/conditions can define a radius."""
    data=_python_profile(profile)
    if condition_mismatch_reason(data['conditions'],conditions) or data['latest_check'] is None:return None
    return float(data['latest_check']['summary']['groups']['B']['all']['mean_cm'])


def load_manual_web_profile(path, *, confirmed=False):
    if confirmed is not True:raise PermissionError('网页与 Python 链路的裁切、内参和时间戳不同；请明确确认风险后手动加载')
    return _web_profile(json.loads(Path(path).read_text(encoding='utf-8')))


def _web_profile(data):
    keys={'schema_version','pipeline','device_name','matrix','source','group','created_at','device_file','calibration_id','screen','coordinates'}
    if not isinstance(data,dict) or set(data)-keys or keys-{'pipeline'}-set(data):raise ValueError('web 档案字段无效')
    if type(data['schema_version']) is not int or data['schema_version']!=1 or data.get('pipeline','web')!='web':
        raise ValueError('手动加载入口只接受 web 档案，缺少 pipeline 的旧档案按 web 处理')
    if data['source']!='nine_point' or data['group']!='B' or data['coordinates']!=COORDINATES:raise ValueError('web 档案来源或坐标无效')
    from .recorder import validate_screen
    validate_screen(data['screen'])
    if data['screen'].get('fullscreen') is not True:raise ValueError('web 校准档案必须为全屏坐标')
    device_name(data['device_name']);_text(data['device_file'],'device_file',True);_text(data['calibration_id'],'calibration_id');_timestamp(data['created_at'])
    return _canonical_numbers(dict(data,pipeline='web',matrix=_matrix(data['matrix'])))


def conditions_fingerprint(conditions):
    return _fingerprint(_conditions(conditions))


def click_profile_path(name,directory=None):
    return Path(directory or ROOT/'data/profiles/python')/(device_name(name)+'.click_offset.json')


def _click_profile(data):
    _fields(data,{'schema_version','pipeline','kind','offset_norm','sample_count','updated_at','conditions_fingerprint'},'click_offset')
    if type(data['schema_version']) is not int or data['schema_version']!=1 or data['pipeline']!='python' or data['kind']!='click_offset':
        raise ValueError('粗校准档案类型或版本无效')
    offset=data['offset_norm']
    if not isinstance(offset,(list,tuple)) or len(offset)!=2:raise ValueError('offset_norm 必须为全屏归一化二维偏移')
    offset=[finite(v,'offset_norm') for v in offset]
    count=_int(data['sample_count'],'sample_count',1);_timestamp(data['updated_at'])
    fingerprint=data['conditions_fingerprint']
    if not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('粗校准条件指纹无效')
    return dict(data,offset_norm=offset,sample_count=count)


def save_click_profile(offset,*,name,count,conditions,directory=None,updated_at=None):
    data=_click_profile(dict(schema_version=1,pipeline='python',kind='click_offset',offset_norm=offset,
                            sample_count=count,updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
                            conditions_fingerprint=conditions_fingerprint(conditions)))
    path=click_profile_path(name,directory)
    _atomic_save(data,path.stem,path.parent)
    return data


def load_click_profile(*,name,conditions,directory=None):
    data=_click_profile(json.loads(click_profile_path(name,directory).read_text(encoding='utf-8')))
    if data['conditions_fingerprint']!=conditions_fingerprint(conditions):
        raise ValueError('粗校准档案与实际设备/相机/屏幕条件不匹配')
    return data


def profile_age_text(profile, now=None):
    """Informational age only; distance and age do not invalidate a calibration."""
    stamp=_timestamp(profile['created_at'] if isinstance(profile,dict) else profile)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('now 必须带时区')
    seconds=max(0,(now-stamp).total_seconds())
    if seconds<60:return '刚刚'
    if seconds<3600:return f'{int(seconds//60)} 分钟前'
    if seconds<86400:return f'{int(seconds//3600)} 小时前'
    return f'{int(seconds//86400)} 天前'
