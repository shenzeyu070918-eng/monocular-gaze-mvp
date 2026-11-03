"""Deterministic offline binding/AOI/export, shared by session finalization."""
from dataclasses import replace
from collections import deque
import csv,io,json,time,hashlib
from pathlib import Path
import numpy as np
from PIL import Image
from gaze_core.config import ScreenGeometry,FixationSettings,HeatmapSettings
from gaze_core.heatmap import HeatmapAccumulator
from gaze_core.heatmap_render import render_heatmap
from .binding import Timeline,extract_fixations
from .aoi import blocks,AOIStatistics
from .storage import read_jsonl
from .ocr import OCRProcess


def process_session(store,options,*,reocr=True,prefix='',ocr_function=None,cancelled=lambda:False):
    metadata=store.metadata;root=store.path;warnings=[]
    events=list(read_jsonl(root/'scenes.jsonl',warnings));start=metadata['start_ns'];end=metadata['end_ns']
    timeline=Timeline(events,start,end);scenes=timeline.scenes
    geometry=ScreenGeometry(**metadata['geometry']);fixation=FixationSettings(**metadata['fixation']);heat=HeatmapSettings(**metadata['heatmap'])
    scale=metadata['kernel'];matrix=metadata['profile']['matrix'];mode=metadata['mode'];size=tuple(metadata['analysis_size'])
    cached={}
    if (root/'aoi.json').exists():cached=json.loads((root/'aoi.json').read_text(encoding='utf-8')).get('scenes',{})
    manual=[]
    if options.manual_aoi:
        from gaze_core.config import ROOT
        manual_path=Path(options.manual_aoi)
        if not manual_path.is_absolute():manual_path=ROOT/manual_path
        manual=json.loads(manual_path.read_text(encoding='utf-8'))
    pool=None
    if reocr and ocr_function is None and scenes:
        pool=OCRProcess()
    aoi={};stats={};maps={};ocr_times=[]
    def check():
        if cancelled():raise InterruptedError('会话已取消')
    try:
        for scene in scenes:
            check();name=scene['scene_id'];path=store.destination(scene['image']);image_bytes=path.read_bytes()
            if hashlib.sha256(image_bytes).hexdigest()!=scene['sha256']:raise ValueError('场景PNG校验和不匹配，拒绝错误绑定：'+name)
            pixels=np.asarray(Image.open(io.BytesIO(image_bytes)).convert('RGB'))
            if name in cached and not reocr:
                lines=cached[name].get('ocr_lines',[])
                selected=(manual.get(name,[]) if isinstance(manual,dict) else manual) or [r for r in cached.get(name,{}).get('regions',[]) if r.get('source')=='manual']
                regions=blocks(lines,pixels,scale,options,selected) if options.manual_aoi else cached[name]['regions']
            else:
                began=time.perf_counter_ns();lines=[]
                try:
                    if not reocr:raise RuntimeError('没有缓存OCR；可用 --reocr 补跑，当前仅使用视觉/手动候选')
                    lines=(ocr_function(path,options.ocr_languages) if ocr_function else pool.recognize(path,options.ocr_languages,options.ocr_timeout_s,cancelled))
                except InterruptedError:raise
                except Exception as exc:warnings.append('场景 '+name+' OCR未完成：'+str(exc))
                ocr_times.append((time.perf_counter_ns()-began)/1e6);check()
                selected=(manual.get(name,[]) if isinstance(manual,dict) else manual) or [r for r in cached.get(name,{}).get('regions',[]) if r.get('source')=='manual']
                regions=blocks(lines,pixels,scale,options,selected)
            aoi[name]=dict(regions=regions,ocr_lines=lines,coordinate_space='image_normalized' if mode=='stimulus' else 'full_screen_normalized',image_sha256=scene['sha256'])
            stats[name]=AOIStatistics(regions,scale,options.soft_grid_width,size[0]/size[1])
            maps[name]=HeatmapAccumulator(heat,size,decay=False,start_s=0.)
    finally:
        if pool is not None:pool.close()
    state_durations={};excluded={};bound_count=0
    latencies=deque(maxlen=10000);frame_count=0
    def frames():
        nonlocal frame_count
        for row in read_jsonl(root/'gaze.jsonl',warnings):
            frame_count+=1
            if row.get('completed_ns') is not None:latencies.append((row['completed_ns']-row['capture_ns'])/1e6)
            yield row
    for kind,item in extract_fixations(frames(),fixation,geometry,matrix,start):
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
        scene_start=next(s['start_ns'] for s in scenes if s['scene_id']==name)
        for row in result['regions']:row['first_fixation_s']=None if row['first_fixation_ns'] is None else (row['first_fixation_ns']-scene_start)/1e9
        store.write_json(prefix+f'stats/{name}.json',result)
        output=io.StringIO();fields=['id','hard_s','soft_s','hard_fraction','soft_fraction','first_fixation_ns','entries','fixations','first_fixation_s']
        writer=csv.DictWriter(output,fields);writer.writeheader();writer.writerows(result['regions'])
        store.write_bytes(prefix+f'stats/{name}.csv',output.getvalue().encode('utf-8-sig'))
        grid=maps[name].total
        store.write_bytes(prefix+f'heatmaps/{name}.csv',('\n'.join(','.join(format(v,'.12g') for v in row) for row in grid)+'\n').encode())
        rgba=render_heatmap(grid,size,max_opacity=heat.max_opacity,alpha_threshold=heat.alpha_threshold,render_scale=heat.render_scale,dither_strength=heat.dither_strength)
        store.write_image(prefix+f'heatmaps/{name}.png',rgba);results[name]=result
    store.write_json(prefix+'aoi.json',dict(schema_version=1,scenes=aoi))
    summary=dict(timeline.summary(),synthetic_input=bool(metadata.get('synthetic',False)),scene_count=len(scenes),bound_fixation_parts=bound_count,
                 valid_bound_fixation_s=sum(s['valid_fixation_s'] for s in results.values()),excluded_fixation_s=excluded,
                 frame_interval_state_s=state_durations,ocr_ms=ocr_times,warnings=warnings,
                 interpretation='块级注意力；AOI为候选，不做逐行逐字结论；未归属、背景和屏外概率不丢弃。')
    api_ms=[(e['received_ns']-e['request_ns'])/1e6 for e in events if e.get('type')=='screen_sample']
    summary['screen_request_ms']=dict(count=len(api_ms),median_ms=float(np.median(api_ms)) if api_ms else None,p90_ms=float(np.percentile(api_ms,90)) if api_ms else None,definition='截图API请求至回调；不含PNG/OCR/写盘，不是屏幕呈现延迟')
    summary['read_to_result_latency']=dict(frame_count=frame_count,sample_count=len(latencies),median_ms=float(np.median(latencies)) if latencies else None,p90_ms=float(np.percentile(latencies,90)) if latencies else None,definition='最后至多10000个已完成帧，OpenCV read返回至推理完成；不含曝光、驱动缓冲或显示')
    if any(e.get('type')=='acceptance' for e in events):
        from .acceptance import score
        summary['acceptance']=score(list(read_jsonl(root/(prefix+'fixations.jsonl'))),events,metadata['analysis_size'])
    store.write_json(prefix+'stats/session.json',summary)
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['scene_id','valid_fixation_s'])
    for name,row in results.items():writer.writerow([name,row['valid_fixation_s']])
    store.write_bytes(prefix+'stats/session.csv',output.getvalue().encode())
    return summary
