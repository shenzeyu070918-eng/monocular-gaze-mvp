"""Read-only M9 data adapter, explicit source versions and display-only scanpaths."""
import hashlib,json,math,io
from pathlib import Path
import numpy as np
from PIL import Image
from gaze_core.attention.storage import read_jsonl,numeric_json
from gaze_core.attention.binding import Timeline
from .settings import ReportSettings
from .labels import region_rows
from gaze_core.attention.aoi import primary_regions,AOIStatistics,blocks,AOI_ALGORITHM_VERSION
from gaze_core.attention.manual_aoi import read_template,select_document

LIMITATION='经验精度约1.3–2.5 cm；M9四轮硬归属66.7%–85.3%。只做块级结论，不做逐行逐字结论。未记录到注视不等于未看见或未理解。'


def safe_file(root,name):
    root=Path(root).resolve();p=root/name
    if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('文件路径超出会话目录')
    return p


class Source:
    def __init__(self,path,derived=None):
        self.root=Path(path).resolve();self.hashes={};self.warnings=[]
        if derived is not None and (not str(derived).startswith('derived/') or not safe_file(self.root,str(derived)).is_dir()):raise ValueError('请选择会话中的derived/版本目录')
        self.version=str(derived).rstrip('/') if derived else 'original'
        self.prefix='' if derived is None else str(derived).rstrip('/')+'/'
    def read(self,name):
        p=safe_file(self.root,name);raw=p.read_bytes();self.hashes[name]=hashlib.sha256(raw).hexdigest();return raw
    def json(self,name):return json.loads(self.read(name))
    def rows(self,name):
        # Hash exactly the bytes read; never race a second read of live data.
        raw=self.read(name);result=[]
        for i,line in enumerate(raw.decode('utf-8').splitlines(),1):
            try:result.append(json.loads(line))
            except ValueError:self.warnings.append(f'{name}:{i} 无效/截断行已跳过')
        return result
    def processed_json(self,name):return self.json(self.prefix+name)


def scanpath(segments,scale,options,*,timeline=None):
    """Circle area encodes dwell; excluded intervals always break connections."""
    nodes=[];previous=None
    for row in sorted(segments,key=lambda r:(r['start_ns'],r['end_ns'])):
        if row.get('excluded_reason') or row.get('scene_id') is None:previous=None;continue
        a,b=int(row['start_ns']),int(row['end_ns']);duration=(b-a)/1e9
        if duration<=0:continue
        center=[float(v) for v in row['center']]
        connect=(previous is not None and previous['scene_id']==row['scene_id'] and 0<=a-previous['end_ns']<=options.scanpath_gap_s*1e9)
        if connect and timeline is not None:
            cuts=[previous['end_ns'],a]+[v for v in timeline.breaks if previous['end_ns']<v<a]
            connect=all(timeline.at((x+y)//2)==(row['scene_id'],None) for x,y in zip(sorted(cuts),sorted(cuts)[1:]) if y>x)
        if connect and options.scanpath_merge_sigma>0:
            last=nodes[-1];distance=math.hypot((center[0]-last['center'][0])/scale['sigma_x'],(center[1]-last['center'][1])/scale['sigma_y'])
            if distance<=options.scanpath_merge_sigma:
                total=last['duration_s']+duration
                last['center']=[(x*last['duration_s']+y*duration)/total for x,y in zip(last['center'],center)]
                last['duration_s']=total;last['end_ns']=b;previous=dict(row);continue
        nodes.append(dict(scene_id=row['scene_id'],start_ns=a,end_ns=b,center=center,duration_s=duration,connect=bool(connect),number=len(nodes)+1))
        previous=dict(row)
    return nodes[:options.scanpath_max_points],len(nodes)


def load_session(path,*,derived=None,options=None):
    options=options or ReportSettings();s=Source(path,derived);m=s.json('session.json')
    if m.get('state') not in ('ended','error'):raise ValueError('请先结束并整理会话，再生成报告')
    try:summary=s.processed_json('stats/session.json');aoi_document=s.processed_json('aoi.json');aoi=aoi_document['scenes'];fixations=s.rows(s.prefix+'fixations.jsonl')
    except FileNotFoundError as exc:raise ValueError('会话尚无完整整理结果；请先用attention_session.py整理，或明确选择derived/恢复版本') from exc
    events=s.rows('scenes.jsonl');start=int(m['start_ns']);end=int(m['end_ns']);timeline=Timeline(events,start,end)
    scenes=[]
    template=read_template(s.root.parent,m.get('stimulus',{}).get('sha256')) if m['mode']=='stimulus' else None
    if template is not None:
        raw=json.dumps(template,ensure_ascii=False,sort_keys=True).encode();s.hashes['shared_aoi_template:'+m['stimulus']['sha256']]=hashlib.sha256(raw).hexdigest()
    template_applied=False;migrated=False
    for event in timeline.scenes:
        name=event['scene_id'];stats=s.processed_json(f'stats/{name}.json');defs=aoi[name]['regions']
        aoi_source=aoi[name].get('aoi_source','manual' if all(r.get('source')=='manual' for r in primary_regions(defs)) and defs else 'auto')
        version=aoi[name].get('algorithm_version',aoi_document.get('algorithm_version','legacy-flat-v1'))
        if template is not None and not (aoi[name].get('manual_source')=='manual_document' and aoi[name].get('manual_scope','session')=='session'):
            template_applied=True
            defs=select_document(template,name,m);aoi_source='manual';version='hierarchical-v2/template'
            reassigned=AOIStatistics(defs,m['kernel'],m.get('attention',{}).get('soft_grid_width',320),m['analysis_size'][0]/m['analysis_size'][1])
            for row in fixations:
                if row.get('scene_id')==name and not row.get('excluded_reason'):reassigned.add(row)
            stats=dict(reassigned.snapshot(),scene_id=name,kernel=m['kernel'])
        image=s.read(event['image'])
        if hashlib.sha256(image).hexdigest()!=event['sha256']:raise ValueError('场景截图校验失败：'+name)
        if version=='legacy-flat-v1':
            from gaze_core.attention.settings import AttentionSettings
            from gaze_core.attention.manual_aoi import manual_regions
            old_manual=[r for r in primary_regions(defs) if r.get('source')=='manual']
            defs=blocks(aoi[name].get('ocr_lines',[]),np.asarray(Image.open(io.BytesIO(image)).convert('RGB')),m['kernel'],AttentionSettings(**m.get('attention',{})),manual_regions(old_manual) if old_manual else None)
            aoi_source='manual' if old_manual else 'auto';version=AOI_ALGORITHM_VERSION+'/report-view';migrated=True
            reassigned=AOIStatistics(defs,m['kernel'],m.get('attention',{}).get('soft_grid_width',320),m['analysis_size'][0]/m['analysis_size'][1])
            for row in fixations:
                if row.get('scene_id')==name and not row.get('excluded_reason'):reassigned.add(row)
            stats=dict(reassigned.snapshot(),scene_id=name,kernel=m['kernel'])
        heat=s.read(s.prefix+f'heatmaps/{name}.png')
        grid=np.loadtxt(io.BytesIO(s.read(s.prefix+f'heatmaps/{name}.csv')),delimiter=',',ndmin=2)
        if not np.isfinite(grid).all() or np.any(grid<0):raise ValueError('热量网格包含无效值')
        rows=region_rows(defs,stats['regions'],options.short_label_chars)
        for r in rows:
            first=r.get('first_fixation_ns');r['first_fixation_s']=None if first is None else (first-event['start_ns'])/1e9
        relevant=[r for r in fixations if r.get('scene_id')==name or r.get('excluded_reason')]
        nodes,total=scanpath(relevant,m['kernel'],options,timeline=timeline)
        scenes.append(dict(id=name,start_s=(event['start_ns']-start)/1e9,app=event.get('app_name') or event.get('app') or event.get('application'),aoi_source=aoi_source,aoi_algorithm_version=version,dynamic=bool(event.get('dynamic')),stats=dict(stats,regions=rows),regions=defs,image=image,heat=heat,grid=grid,scanpath=nodes,scanpath_total=total,segments=[r for r in fixations if r.get('scene_id')==name and not r.get('excluded_reason')],scene_start_ns=event['start_ns']))
    duration=float(summary.get('duration_s',(end-start)/1e9));valid=float(summary.get('valid_bound_fixation_s',0))
    state_intervals=[]
    for i,e in enumerate(timeline.states):
        finish=timeline.states[i+1]['time_ns'] if i+1<len(timeline.states) else end
        state_intervals.append(dict(kind=e['status'],start_s=(e['time_ns']-start)/1e9,end_s=(finish-start)/1e9))
    # M9 detector is the authority for blink/no-face/outside/saccade intervals.
    # It emits totals, so only frame statuses are located here; outside uses the
    # same unfiltered, unclipped calibration matrix and capture-gap rule.
    frames=s.rows('gaze.jsonl');matrix=np.asarray(m['profile']['matrix'],float);max_gap=m['fixation'].get('max_gap_ms',250)*1e6
    for previous,current in zip(frames,frames[1:]):
        a,b=previous['capture_ns'],current['capture_ns'];kind=previous.get('status','invalid')
        if b-a>max_gap:kind='capture_gap'
        elif previous.get('valid') and kind=='tracking' and previous.get('raw') is not None:
            p=np.append(np.asarray(previous['raw'],float),1)@matrix
            kind='outside' if not np.isfinite(p).all() or np.any(p<0) or np.any(p>1) else 'tracking'
        if kind!='tracking':
            interval=dict(kind=kind,start_s=max(0,(a-start)/1e9),end_s=min(duration,(b-start)/1e9))
            if interval['end_s']>interval['start_s']:state_intervals.append(interval)
    for a,b in timeline.uncertain:state_intervals.append(dict(kind='transition_uncertain',start_s=max(0,(a-start)/1e9),end_s=min(duration,(b-start)/1e9)))
    warnings=list(summary.get('warnings',[]))+s.warnings
    if migrated:warnings.append('旧版平面AOI已用缓存OCR与已绑定注视在本报告中重建为hierarchical-v2；源文件未改。可用attention_session.py --rebind保存完整派生版本。')
    if m.get('state')=='error':warnings.append('原会话异常结束：'+str(m.get('error') or m.get('reason')))
    if derived:warnings.append('明确选用离线恢复版本：'+str(derived)+'；未改写原会话及M9验收')
    if not scenes:warnings.append('会话没有可分析场景；无数据不等于零注意')
    data=dict(schema_version=1,kind='session',session_id=m.get('session_id',s.root.name),mode=m['mode'],participant_id=m.get('participant_id'),metadata=m,summary=summary,coverage=valid/duration if duration>0 else None,scenes=scenes,timeline=sorted(state_intervals,key=lambda r:r['start_s']),warnings=warnings,limitation=LIMITATION,source_version=s.version,aoi_template_sha256=None if not template_applied else m['stimulus']['sha256'],source_hashes=s.hashes,source_root=str(s.root))
    return data


def public_data(data):
    """Portable local data JSON, without redundant raster copies or private paths."""
    result={k:v for k,v in data.items() if k not in ('scenes','source_root')}
    result['scenes']=[{k:v for k,v in row.items() if k not in ('image','heat','grid','segments')} for row in data['scenes']]
    return numeric_json(result)
