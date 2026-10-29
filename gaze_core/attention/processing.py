"""Deterministic offline binding/AOI/export, shared by session finalization."""
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import csv,io,json,multiprocessing,time
from pathlib import Path
import numpy as np
from PIL import Image
from gaze_core.config import ScreenGeometry,FixationSettings,HeatmapSettings
from gaze_core.heatmap import HeatmapAccumulator
from gaze_core.heatmap_render import render_heatmap
from .binding import Timeline,extract_fixations
from .aoi import blocks,AOIStatistics
from .storage import read_jsonl
from .ocr import recognize_file


def process_session(store,options,*,reocr=True,prefix='',ocr_function=None,cancelled=lambda:False):
    metadata=store.metadata;root=store.path;warnings=[]
    events=list(read_jsonl(root/'scenes.jsonl',warnings));start=metadata['start_ns'];end=metadata['end_ns']
    timeline=Timeline(events,start,end);scenes=timeline.scenes
    geometry=ScreenGeometry(**metadata['geometry']);fixation=FixationSettings(**metadata['fixation']);heat=HeatmapSettings(**metadata['heatmap'])
    scale=metadata['kernel'];matrix=metadata['profile']['matrix'];mode=metadata['mode'];size=tuple(metadata['analysis_size'])
    cached={}
    if not reocr and (root/'aoi.json').exists():cached=json.loads((root/'aoi.json').read_text(encoding='utf-8')).get('scenes',{})
    manual=[]
    if options.manual_aoi:manual=json.loads(Path(options.manual_aoi).read_text(encoding='utf-8'))
    pool=None
    if reocr and ocr_function is None and scenes:
        pool=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'))
    aoi={};stats={};maps={};ocr_times=[]
    def check():
        if cancelled():raise InterruptedError('会话已取消')
    try:
        for scene in scenes:
            check();name=scene['scene_id'];path=store.destination(scene['image']);pixels=np.asarray(Image.open(path).convert('RGB'))
            if name in cached and not reocr:regions=cached[name]['regions'];lines=cached[name].get('ocr_lines',[])
            else:
                began=time.perf_counter_ns();lines=[]
                try:
                    lines=(ocr_function(path,options.ocr_languages) if ocr_function else pool.submit(recognize_file,str(path),options.ocr_languages).result(timeout=120))
                except Exception as exc:warnings.append('场景 '+name+' OCR未完成：'+str(exc))
                ocr_times.append((time.perf_counter_ns()-began)/1e6);check()
                selected=manual.get(name,[]) if isinstance(manual,dict) else manual
                regions=blocks(lines,pixels,scale,options,selected)
            aoi[name]=dict(regions=regions,ocr_lines=lines,coordinate_space='image_normalized' if mode=='stimulus' else 'full_screen_normalized',image_sha256=scene['sha256'])
            stats[name]=AOIStatistics(regions,scale,options.soft_grid_width,size[0]/size[1])
            maps[name]=HeatmapAccumulator(heat,size,decay=False,start_s=0.)
    finally:
        if pool is not None:pool.shutdown(wait=True,cancel_futures=True)
    state_durations={};excluded={};bound_count=0
    for kind,item in extract_fixations(read_jsonl(root/'gaze.jsonl',warnings),fixation,geometry,matrix,start):
        check()
        if kind=='states':state_durations=item;continue
        for segment in timeline.split(item):
            name=segment['scene_id'];reason=segment['excluded_reason']
            if mode=='stimulus' and name is not None:
                a,b,c,d=metadata['stimulus']['screen_rect'];x,y=segment['center'];point=[(x-a)/(c-a),(y-b)/(d-b)]
                segment=dict(segment,screen_center=segment['center'],center=point)
                if not 0<=point[0]<=1 or not 0<=point[1]<=1:reason='outside_stimulus';segment['excluded_reason']=reason
            duration=(segment['end_ns']-segment['start_ns'])/1e9
            if reason:excluded[reason]=excluded.get(reason,0.)+duration
            elif name in stats:
                stats[name].add(segment);bound_count+=1
                maps[name].add(dict(center=segment['center'],start_s=(segment['start_ns']-start)/1e9,end_s=(segment['end_ns']-start)/1e9),scale)
            store.append(prefix+'fixations.jsonl',dict(type='fixation',**segment))
    results={}
    for name in stats:
        check();result=stats[name].snapshot();result.update(scene_id=name,kernel=scale)
        store.write_json(prefix+f'stats/{name}.json',result)
        output=io.StringIO();fields=['id','hard_s','soft_s','hard_fraction','soft_fraction','first_fixation_ns','entries','fixations']
        writer=csv.DictWriter(output,fields);writer.writeheader();writer.writerows(result['regions'])
        store.write_bytes(prefix+f'stats/{name}.csv',output.getvalue().encode('utf-8-sig'))
        grid=maps[name].total
        store.write_bytes(prefix+f'heatmaps/{name}.csv',('\n'.join(','.join(format(v,'.12g') for v in row) for row in grid)+'\n').encode())
        rgba=render_heatmap(grid,size,max_opacity=heat.max_opacity,alpha_threshold=heat.alpha_threshold,render_scale=heat.render_scale,dither_strength=heat.dither_strength)
        store.write_image(prefix+f'heatmaps/{name}.png',rgba);results[name]=result
    store.write_json(prefix+'aoi.json',dict(schema_version=1,scenes=aoi))
    summary=dict(timeline.summary(),scene_count=len(scenes),bound_fixation_parts=bound_count,
                 valid_bound_fixation_s=sum(s['valid_fixation_s'] for s in results.values()),excluded_fixation_s=excluded,
                 frame_interval_state_s=state_durations,ocr_ms=ocr_times,warnings=warnings,
                 interpretation='块级注意力；AOI为候选，不做逐行逐字结论；未归属、背景和屏外概率不丢弃。')
    if any(e.get('type')=='acceptance' for e in events):
        from .acceptance import score
        summary['acceptance']=score(list(read_jsonl(root/(prefix+'fixations.jsonl'))),events,metadata['analysis_size'])
    store.write_json(prefix+'stats/session.json',summary)
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['scene_id','valid_fixation_s'])
    for name,row in results.items():writer.writerow([name,row['valid_fixation_s']])
    store.write_bytes(prefix+'stats/session.csv',output.getvalue().encode())
    return summary
