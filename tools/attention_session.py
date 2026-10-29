"""Rebind/re-OCR local M9 sessions. Never starts capture or opens a camera."""
import argparse,json,sys,uuid
from pathlib import Path
from dataclasses import replace
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import load_settings,ROOT
from gaze_core.attention.settings import AttentionSettings
from gaze_core.attention.storage import SessionStore,read_jsonl,delete_session
from gaze_core.attention.processing import process_session


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('session',type=Path)
    parser.add_argument('--rebind',action='store_true');parser.add_argument('--reocr',action='store_true')
    parser.add_argument('--manual-aoi',type=str);parser.add_argument('--delete',action='store_true',help='删除这个会话整个目录，不能撤销')
    args=parser.parse_args(argv);settings=load_settings();root=Path(settings.attention.output_dir)
    if not root.is_absolute():root=ROOT/root
    if args.session.is_symlink():parser.error('不处理符号链接会话目录')
    path=args.session.resolve()
    if path.parent!=root.resolve():parser.error('会话必须位于配置的 data/attention 根目录下')
    if args.delete:delete_session(root,path);print('已删除整个会话');return 0
    store=SessionStore.existing(path,settings.attention.max_disk_mb*1024*1024)
    try:
        events=list(read_jsonl(path/'scenes.jsonl'))
        interrupted=not any(e.get('type')=='end' for e in events)
        if interrupted:print('警告：会话未正常结束；只处理已完整写入的数据。',file=sys.stderr)
        if 'end_ns' not in store.metadata:
            store.metadata['end_ns']=max([store.metadata['start_ns']]+[r['capture_ns'] for r in read_jsonl(path/'gaze.jsonl')]+[e.get('confirmed_ns',e.get('time_ns',0)) for e in events])
        if args.rebind or args.reocr:
            original=AttentionSettings(**store.metadata.get('attention',{}))
            options=replace(original,manual_aoi=args.manual_aoi or original.manual_aoi)
            version=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
            prefix=f'derived/{version}/'
            summary=process_session(store,options,reocr=args.reocr,prefix=prefix)
            summary.update(interrupted=interrupted,derived_prefix=prefix);store.write_json(prefix+'stats/session.json',summary)
        else:
            summary=dict(session_id=store.id,state=store.metadata.get('state'),mode=store.metadata['mode'],synthetic_input=bool(store.metadata.get('synthetic',False)),
                         scene_count=sum(e['type']=='scene' for e in events),interrupted=interrupted,
                         stimulus_sha256=store.metadata.get('stimulus',{}).get('sha256'))
        print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
    finally:store.close()

if __name__=='__main__':raise SystemExit(main())
