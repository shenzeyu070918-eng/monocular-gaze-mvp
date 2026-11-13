"""Deterministic poster + synthetic fixation session for offline report visual QA.

All generated data are marked synthetic, never presented as participant results.
No camera, screen capture, network OCR or AI provider is used.
"""
import argparse,hashlib,json,sys,uuid
from pathlib import Path
from dataclasses import asdict,replace
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageFilter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import ROOT,load_settings
from gaze_core.attention.storage import SessionStore
from gaze_core.attention.processing import process_session
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build


def poster(path):
    """Product poster with a locally drawn headphone hero, no external assets."""
    w,h=1200,1600;image=Image.new('RGB',(w,h),'#f3f7fb');d=ImageDraw.Draw(image);ink='#102f50'
    font_path=next((p for p in ['/System/Library/Fonts/STHeiti Medium.ttc','/System/Library/Fonts/STHeiti Light.ttc','C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'] if Path(p).exists()),None)
    if font_path is None:raise RuntimeError('需要本机中文字体来生成海报示意')
    def text(at,value,size,color=ink):d.text(at,value,font=ImageFont.truetype(font_path,size),fill=color)
    text((72,65),'声屿 AIR',34);text((72,145),'留一点安静',94);text((76,266),'把世界调成你喜欢的音量。',32,'#4e657a')
    # A visibly bounded product-image area, like a real poster hero/photo card.
    d.rounded_rectangle((68,360,1132,1110),radius=20,fill='#dce8f1',outline='#b7cddb',width=3)
    shadow=Image.new('RGBA',(w,h));sd=ImageDraw.Draw(shadow);sd.ellipse((300,970,950,1040),fill=(14,44,68,75));image=Image.alpha_composite(image.convert('RGBA'),shadow.filter(ImageFilter.GaussianBlur(22))).convert('RGB');d=ImageDraw.Draw(image)
    # Glossy dark-blue headphones, thick layered arcs and soft ear cushions.
    d.arc((320,435,890,1055),180,360,fill='#102f50',width=92);d.arc((328,442,882,1047),183,357,fill='#416985',width=14)
    d.arc((350,465,860,1050),180,360,fill='#e2edf5',width=26)
    d.rounded_rectangle((302,704,423,959),radius=54,fill='#102f50');d.rounded_rectangle((322,719,408,947),radius=42,fill='#294f6d');d.rounded_rectangle((338,738,402,929),radius=31,fill='#18374f')
    d.rounded_rectangle((790,704,911,959),radius=54,fill='#102f50');d.rounded_rectangle((802,719,890,947),radius=42,fill='#416d88');d.rounded_rectangle((807,738,872,929),radius=31,fill='#18374f')
    d.line((849,765,849,901),fill='#71a6bd',width=3);d.ellipse((840,916,850,926),fill='#ff9b68')
    text((112,1012),'轻一点。静一点。',28,'#4e657a')
    text((76,1166),'自适应降噪  /  40 小时续航',28,'#4e657a')
    d.rounded_rectangle((68,1235,545,1422),radius=12,fill='#fff',outline='#9db8cf',width=3);text((96,1257),'新品体验价',27,'#4e657a');text((92,1301),'¥ 699',76)
    d.rounded_rectangle((590,1235,1132,1422),radius=12,fill='#ed713e');text((706,1291),'立即体验',48,'#fff')
    text((76,1486),'示意海报 · 产品与价格均为演示内容',24,'#4e657a');image.save(path)
    return [dict(id='title',text='留一点安静',confidence=.99,rect=[.06,.09,.50,.16]),dict(id='subtitle',text='把世界调成你喜欢的音量',confidence=.99,rect=[.06,.16,.58,.20]),dict(id='price',text='新品体验价699',confidence=.99,rect=[.08,.79,.37,.87]),dict(id='cta',text='立即体验',confidence=.99,rect=[.58,.81,.85,.85])]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vision',action='store_true',help='用macOS Vision实际读取示意海报（仍为合成注视）');parser.add_argument('--output',type=Path,default=ROOT/'data/report-preview/poster-check');args=parser.parse_args(argv);out=args.output.resolve()
    if not out.is_relative_to((ROOT/'data').resolve()):parser.error('输出必须在data/下')
    out.mkdir(parents=True,exist_ok=True);image_path=out/'poster.png';lines=poster(image_path);settings=load_settings(use_local=False)
    # Simulate a short title → hero → price → CTA scan. This is NOT accuracy data.
    points=[(.3,.13),(.5,.46),(.24,.83),(.73,.83)];scale=dict(sigma_x=.045,sigma_y=.035,sigma_cm=2.,source='synthetic_example',sigma_px=None)
    metadata=dict(synthetic=True,participant_id='SYNTHETIC',mode='stimulus',start_ns=1_000_000_000,end_ns=13_000_000_000,geometry=asdict(settings.geometry),fixation=asdict(settings.fixation),heatmap=asdict(settings.heatmap),attention=asdict(settings.attention),analysis_size=[1200,1600],kernel=scale,profile=dict(matrix=[[1,0],[0,1],[0,0]],calibration_id='synthetic-not-a-person'),stimulus=dict(filename=image_path.name,sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),screen_rect=[0,0,1,1]))
    run_root=out/'runs'/uuid.uuid4().hex[:12]
    store=SessionStore(run_root,metadata,200*1024*1024)
    try:
        digest=store.write_bytes('scenes/000001.png',image_path.read_bytes());store.append('scenes.jsonl',dict(type='scene',scene_id='000001',start_ns=metadata['start_ns'],image='scenes/000001.png',sha256=digest,app_name='合成海报'))
        for i in range(121):
            point=points[min(3,i//30)];store.append('gaze.jsonl',dict(type='frame',capture_ns=metadata['start_ns']+i*100_000_000,valid=True,status='tracking',raw=list(point)))
        recognizer=(__import__('gaze_core.attention.ocr',fromlist=['recognize_file']).recognize_file if args.vision else lambda *_:lines)
        summary=process_session(store,settings.attention,ocr_function=recognizer)
        if args.vision and any('OCR未完成' in w for w in summary['warnings']):
            store.finish(metadata['end_ns'],'visual_check_failed',error='local_ocr_failed');raise RuntimeError('本地Vision未完成，不能将缺失文字的流程当作检查通过')
        from gaze_core.attention.aoi import primary_regions
        regions=primary_regions(json.loads((store.path/'aoi.json').read_text(encoding='utf-8'))['scenes']['000001']['regions'])
        if any(not any(r['rect'][0]<=x<=r['rect'][2] and r['rect'][1]<=y<=r['rect'][3] for r in regions) for x,y in points):
            store.finish(metadata['end_ns'],'visual_check_failed',error='missing_poster_primary_region');raise AssertionError('标题、主图、价格或按钮有未识别的主区域')
        store.finish(metadata['end_ns'],'synthetic_visual_check')
        artifact=build(load_session(store.path),options=settings.report);(out/'latest.json').write_text(json.dumps(dict(session=str(store.path),report=str(artifact.html),poster=str(image_path)),indent=2),encoding='utf-8')
        print('示意海报：'+str(image_path));print('合成刺激物报告：'+str(artifact.html));print('合成数据，不是实机精度或性能结果。')
    finally:store.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
