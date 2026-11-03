"""Stimulus-only participant deduplication, canonical AOIs and equal person weights."""
import numpy as np
from gaze_core.attention.aoi import AOIStatistics,validate_regions
from gaze_core.heatmap_render import render_heatmap
from .data import LIMITATION
from .participants import participant_id


def aggregate(sessions,*,participant_map=None,canonical_regions=None):
    if not sessions:raise ValueError('请至少选择一个刺激物会话')
    mapping=participant_map or {};people={};sha=None;first=sessions[0]
    if len(first['scenes'])!=1:raise ValueError('刺激物必须有且只有一个场景')
    template=validate_regions(canonical_regions if canonical_regions is not None else first['scenes'][0]['regions'])
    snapshots=[];grids=[]
    for data in sessions:
        m=data['metadata']
        if data['mode']!='stimulus' or len(data['scenes'])!=1:raise ValueError('多人汇总只接受同一图片的刺激物会话')
        digest=m['stimulus']['sha256']
        if sha is None:sha=digest
        if sha!=digest:raise ValueError('刺激物SHA256不同，不能合并')
        identity=data.get('participant_id') or mapping.get(data['session_id'])
        if identity is None:raise ValueError('旧会话缺少匿名编号，请提供参与者事后映射：'+data['session_id'])
        identity=participant_id(identity)
        if identity in people:raise ValueError('参与者重复，请明确选择一次会话：'+identity)
        people[identity]=data['session_id'];scene=data['scenes'][0];size=m['analysis_size']
        stats=AOIStatistics(template,m['kernel'],m.get('attention',{}).get('soft_grid_width',320),size[0]/size[1])
        for segment in sorted(scene['segments'],key=lambda r:r['start_ns']):stats.add(segment)
        result=stats.snapshot();result['scene_start_ns']=scene['scene_start_ns'];result['participant_id']=identity;snapshots.append(result)
        grid=np.asarray(scene['grid'],float)
        if grid.shape!=np.asarray(first['scenes'][0]['grid']).shape:raise ValueError('网格分辨率不同，请使用同一配置离线重新整理')
        if stats.total>0:
            total=float(grid.sum());grids.append(grid/total if total>0 else np.zeros_like(grid))
    valid=[s for s in snapshots if s['valid_fixation_s']>0];rows=[]
    for region in template:
        values=[(s,next(r for r in s['regions'] if r['id']==region['id'])) for s in valid]
        seen=[(s,r) for s,r in values if r['hard_s']>0]
        rows.append(dict(id=region['id'],label=region.get('text') or region['id'],rect=region['rect'],N_total=len(snapshots),N_valid=len(valid),N_seen=len(seen),seen_fraction=len(seen)/len(valid) if valid else None,
                         hard_s=float(np.mean([r['hard_s'] for _,r in values])) if values else None,soft_s=float(np.mean([r['soft_s'] for _,r in values])) if values else None,
                         hard_fraction=float(np.mean([r['hard_fraction'] for _,r in values])) if values else None,soft_fraction=float(np.mean([r['soft_fraction'] for _,r in values])) if values else None,
                         first_fixation_s=float(np.mean([(r['first_fixation_ns']-s['scene_start_ns'])/1e9 for s,r in seen])) if seen else None,
                         entries=float(np.mean([r['entries'] for _,r in values])) if values else None,fixations=float(np.mean([r['fixations'] for _,r in values])) if values else None))
    grid=np.mean(grids,axis=0) if grids else np.zeros_like(first['scenes'][0]['grid']);size=first['metadata']['analysis_size']
    from PIL import Image
    import io
    output=io.BytesIO();Image.fromarray(render_heatmap(grid,size)).save(output,format='PNG')
    background={key:float(np.mean([s[key] for s in valid])) if valid else None for key in ('background_hard_s','background_soft_s','outside_probability_s')}
    scene=dict(first['scenes'][0],stats=dict(background,regions=rows,valid_fixation_s=sum(s['valid_fixation_s'] for s in valid)/len(valid) if valid else 0),regions=template,heat=output.getvalue(),grid=grid,scanpath=[],scanpath_total=0,segments=[])
    durations=[s['summary']['duration_s'] for s in sessions]
    return dict(schema_version=1,kind='aggregate',mode='stimulus',session_id='aggregate',participant_id=None,metadata=dict(first['metadata'],profile=first['metadata'].get('profile'),aggregate_kernels=[s['metadata']['kernel'] for s in sessions]),summary=dict(duration_s=float(np.mean(durations)),valid_bound_fixation_s=scene['stats']['valid_fixation_s'],uncertain_fraction=None),coverage=float(np.mean([s['coverage'] for s in sessions if s['coverage'] is not None])) if any(s['coverage'] is not None for s in sessions) else None,
                participant_statistics=snapshots,participants=people,N_total=len(snapshots),N_valid=len(valid),scenes=[scene],timeline=[],warnings=['每人先按有效注视热量归一化，再等权平均；汇总AOI使用首份会话模板，逐人重新归属。','首次注视时间只对看过者求平均；比例分母为有有效注视者N_valid。'],limitation=LIMITATION,source_version='aggregate',source_hashes={s['session_id']:s['source_hashes'] for s in sessions},source_root=first['source_root'],canonical_aoi_source=first['session_id'])
