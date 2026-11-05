"""Build only a synthetic report and the camera-free galaxy dependency closure."""
from pathlib import Path
from dataclasses import asdict
import argparse,json,re,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from gaze_core.config import load_settings

def build(output):
    output=Path(output).resolve()
    if output==ROOT or output in ROOT.parents:raise ValueError('输出不能覆盖源码根目录')
    if output.exists() and any(output.iterdir()):raise ValueError('输出目录必须为空，避免混入旧数据')
    output.mkdir(parents=True,exist_ok=True)
    galaxy=output/'galaxy';galaxy.mkdir(exist_ok=True)
    source=ROOT/'static/galaxy';todo=['preview.mjs'];seen=set()
    while todo:
        name=todo.pop()
        if name in seen:continue
        path=(source/name).resolve()
        if path.parent!=source.resolve() or name in {'app.mjs','entry.mjs'}:raise ValueError('非预览模块')
        text=path.read_text()
        if any(x in text for x in ('getUserMedia','new WebSocket','displayMedia')):raise ValueError('设备或实时连接代码')
        seen.add(name)
        todo.extend(re.findall(r"(?:from\s*|import\s*\(?)['\"]\./([^'\"]+\.mjs)['\"]",text))
        if name=='preview.mjs':text=text.replace("'/preview-config'","'./preview-config.json'")
        (galaxy/name).write_text(text)
    shutil.copytree(source/'vendor',galaxy/'vendor',dirs_exist_ok=True)
    for name in ['style.css','preview.css']:shutil.copyfile(source/name,galaxy/name)
    html=(source/'index.html').read_text().replace('/assets/','./').replace('./entry.mjs','./preview.mjs')
    html=html.replace('<meta charset="utf-8">','<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\' \'unsafe-inline\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; connect-src \'self\'">')
    (galaxy/'index.html').write_text(html)
    o=load_settings(use_local=False).galaxy
    config={k:getattr(o,k) for k in ['stale_ms','transition_s','transition_scatter_s','text','pixel_ratio','intro_s','low_fps','low_fps_s','min_pixel_ratio']}
    config.update(interaction=asdict(o.interaction),visual=asdict(o.visual))
    (galaxy/'preview-config.json').write_text(json.dumps(config,ensure_ascii=False,allow_nan=False))
    report=(ROOT/'docs/demo/synthetic-report.html').read_text()
    if not '合成' in report or any(x in report for x in ['/Users/','127.0.0.1','localhost','file://']):raise ValueError('报告不是可公开合成版本')
    (output/'report.html').write_text(report)
    (output/'.nojekyll').write_text('')
    (output/'index.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>视线实验台 · 公开演示</title><style>body{background:#0b1420;color:#edf5ff;font:20px/1.7 system-ui;max-width:850px;margin:10vh auto;padding:24px}h1{font-size:44px}a{display:block;background:#18344a;color:#9be6ff;padding:22px;margin:20px 0;border-radius:12px}small{color:#b8c7d7}</style><h1>视线实验台</h1><p>屏幕注意力分析助手 · 无需摄像头的公开演示</p><a href="./galaxy/?preview">进入眼手星系预览 →</a><a href="./report.html">查看合成海报示例报告 →</a><small>星系使用鼠标与按钮模拟输入，不访问摄像头、麦克风、屏幕或本地实时桥。报告全部为合成示例，非真人实测；定位仅作块级结论。M10分析报告不包含在v1.0.0中。</small></html>''')
    files=[str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()]
    (output/'manifest.json').write_text(json.dumps(dict(synthetic=True,modules=sorted(seen),files=sorted(files)),indent=2))
    return seen
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print('预览模块数：',len(build(args.output)))
