"""Controlled pixel evidence only; never infer exclusion from an empty marker."""
import json
from pathlib import Path
import numpy as np

COMPONENTS=('ring','heatmap','status','badge','panel','menu','late_window')


def compare_fixture(included,filtered,expected,regions,*,tolerance=3):
    included=np.asarray(included);filtered=np.asarray(filtered);expected=np.asarray(expected)
    if included.shape!=filtered.shape or filtered.shape!=expected.shape or len(filtered.shape)!=3 or filtered.shape[2]!=3:raise ValueError('受控底图尺寸不匹配')
    difference=np.max(np.abs(filtered.astype(int)-expected.astype(int)),axis=2)
    original=np.max(np.abs(included.astype(int)-expected.astype(int)),axis=2)
    visible={}
    for name in COMPONENTS:
        x,y,w,h=regions[name]
        if min(x,y)<0 or min(w,h)<1 or x+w>filtered.shape[1] or y+h>filtered.shape[0]:raise ValueError('测试窗口不在受控底图内')
        visible[name]=int(np.count_nonzero(original[y:y+h,x:x+w]>tolerance))
    residue=int(np.count_nonzero(difference>tolerance))
    return dict(schema_version=1,passed=residue==0 and all(n>25 for n in visible.values()),residue_pixels=residue,
                visible_pixels=visible,channel_tolerance=tolerance,max_channel_difference=int(difference.max()),
                note='残留像素要求为零；3/255通道容差仅用于系统色彩转换，所有窗口须在未过滤图中有实际像素证据。')


def load_evidence(path,screen):
    try:
        result=json.loads(Path(path).read_text(encoding='utf-8'))
        if result.get('schema_version')!=1 or result.get('screen_size')!=[screen.width,screen.height]:return None
        if result.get('component_names')!=list(COMPONENTS) or not result.get('completed_at'):return None
        # Never accept a bare {passed:true}. Both raw inclusion and fixed-image
        # comparisons are required, along with the post-filter late window.
        phases=result['phases']
        passed=bool(result.get('passed') is True and len(phases)==2 and [p.get('phase') for p in phases]==['initial','post_new_window'] and all(p.get('all_visible') is True and p['residue_pixels']==0 and p['channel_tolerance']==3 and all(p['visible_pixels'].get(n,0)>25 for n in COMPONENTS) for p in phases))
        return dict(passed=passed,completed_at=result['completed_at'],path=str(path),screen_size=result['screen_size'],residue_pixels=[p['residue_pixels'] for p in phases])
    except (OSError,ValueError,KeyError,TypeError):return None
