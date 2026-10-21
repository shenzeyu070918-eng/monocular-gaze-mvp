"""Own-point affine residuals, never an independent accuracy evaluation."""
from numbers import Integral
import numpy as np
from .devices import finite
from .display import calibration_matrix


def calibration_quality(samples,matrix,geometry,*,screen_size=None,threshold_cm=2.0,round_index=0):
    matrix=calibration_matrix(matrix)
    measured=bool(geometry.measured and geometry.width_cm is not None and geometry.height_cm is not None)
    if not measured and screen_size is None:return None
    scales=np.array([geometry.width_cm,geometry.height_cm] if measured else [screen_size[0]-1,screen_size[1]-1],dtype=float)
    if not np.isfinite(scales).all() or np.any(scales<=0):raise ValueError('残差换算尺寸必须为正数')
    points=[]
    for sample in samples:
        raw=np.asarray(sample['raw'],dtype=float);target=np.asarray(sample['target'],dtype=float)
        if raw.shape!=(2,) or target.shape!=(2,) or not np.isfinite([raw,target]).all():raise ValueError('残差坐标无效')
        corrected=np.append(raw,1.)@matrix
        residual=float(np.linalg.norm((corrected-target)*scales))
        points.append(dict(target_id=sample['target_id'],target=target.tolist(),raw_median=raw.tolist(),
                           corrected=corrected.tolist(),residual=residual,sample_count=int(sample['sample_count']),
                           redo=bool(measured and residual>threshold_cm)))
    result=dict(unit='cm' if measured else 'logical_px',round=round_index,threshold=float(threshold_cm) if measured else None,
                points=points,mean=float(np.mean([p['residual'] for p in points])),maximum=float(max(p['residual'] for p in points)))
    return validate_quality(result)


def validate_quality(value):
    """Strict numeric schema shared by profiles and JSONL (reject images)."""
    if not isinstance(value,dict) or set(value)!={'unit','round','threshold','points','mean','maximum'}:
        raise ValueError('校准残差字段无效')
    if value['unit'] not in ('cm','logical_px') or type(value['round']) is not int or value['round'] not in (0,1):raise ValueError('残差单位或补看轮数无效')
    threshold=finite(value['threshold'],'threshold',positive=True,nullable=True)
    if (value['unit']=='cm') != (threshold is not None):raise ValueError('厘米阈值只用于实测屏幕')
    points=value['points']
    if not isinstance(points,list) or len(points)!=9:raise ValueError('校准残差必须有九点')
    result=[];seen=set()
    for point in points:
        if not isinstance(point,dict) or set(point)!={'target_id','target','raw_median','corrected','residual','sample_count','redo'}:raise ValueError('残差点字段无效')
        ident=point['target_id']
        if not isinstance(ident,str) or not ident or len(ident)>128 or ident in seen:raise ValueError('残差点编号无效')
        seen.add(ident);row=dict(point)
        for key in ('target','raw_median','corrected'):
            array=point[key]
            if not isinstance(array,(list,tuple)) or len(array)!=2:raise ValueError('残差坐标必须为二维')
            row[key]=[finite(v,key) for v in array]
        if isinstance(point['sample_count'],bool) or not isinstance(point['sample_count'],Integral) or point['sample_count']<1:raise ValueError('残差样本数无效')
        row['sample_count']=int(point['sample_count']);row['residual']=finite(point['residual'],'residual')
        if row['residual']<0 or type(row['redo']) is not bool or row['redo'] != bool(threshold is not None and row['residual']>threshold):raise ValueError('残差或补看标记无效')
        result.append(row)
    mean=finite(value['mean'],'mean');maximum=finite(value['maximum'],'maximum')
    if not np.isclose(mean,np.mean([p['residual'] for p in result])) or not np.isclose(maximum,max(p['residual'] for p in result)):raise ValueError('残差汇总不一致')
    return dict(unit=value['unit'],round=value['round'],threshold=threshold,points=result,mean=mean,maximum=maximum)


def quality_text(quality):
    if quality is None:return '校准拟合残差不可用：尚无实际逻辑屏幕尺寸。'
    unit='cm' if quality['unit']=='cm' else '逻辑像素'
    lines=['校准点自身拟合残差（不是独立 B 组精度） · '+('首次' if quality['round']==0 else '补看后')]
    lines.extend(f'{p["target_id"]}：{p["residual"]:.3f} {unit}'+(' · 建议补看' if p['redo'] else '') for p in quality['points'])
    if quality['threshold'] is None:lines.append('屏幕尺寸未实测，只报告逻辑像素，不使用厘米阈值提示补看。')
    elif quality['round']==1:lines.append('已完成唯一一轮补看；残差仍高时可重新开始完整九点。')
    return '\n'.join(lines)
