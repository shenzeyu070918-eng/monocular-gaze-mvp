#!/usr/bin/env python3
"""Build portable offline attention reports. This command never calls AI APIs."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import load_settings
from gaze_core.reporting.data import load_session
from gaze_core.reporting.aggregate import aggregate
from gaze_core.reporting.build import build


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sessions',nargs='+',type=Path)
    parser.add_argument('--derived',help='单会话明确指定derived/版本；不自动猜测')
    parser.add_argument('--aggregate',action='store_true',help='同SHA256刺激物，按匿名编号去重，每人等权')
    parser.add_argument('--participant-map',type=Path,help='旧会话session_id→匿名编号JSON')
    parser.add_argument('--aoi-template',type=Path,help='汇总统一AOI矩形列表JSON；默认首份会话')
    parser.add_argument('--edit',action='store_true',help='打开本机单文件AOI编辑器，支持保存并重新生成')
    parser.add_argument('--pdf',action='store_true');parser.add_argument('--open',action='store_true')
    args=parser.parse_args(argv);options=load_settings().report
    if args.derived and args.aggregate:parser.error('汇总请先分别整理到选定版本；--derived仅用于单会话')
    if not args.aggregate and len(args.sessions)!=1:parser.error('多个会话须指定--aggregate')
    try:
        datasets=[load_session(p,derived=args.derived,options=options) for p in args.sessions]
        data=aggregate(datasets,participant_map=json.loads(args.participant_map.read_text(encoding='utf-8')) if args.participant_map else None,canonical_regions=json.loads(args.aoi_template.read_text(encoding='utf-8')) if args.aoi_template else None) if args.aggregate else datasets[0]
        result=build(data,options=options);print('离线统计报告：'+str(result.html))
        if args.pdf:
            from gaze_core.reporting.pdf import export_pdf
            try:print('PDF：'+str(export_pdf(result)))
            except Exception as exc:print(str(exc),file=sys.stderr)
        if args.edit:
            from gaze_core.reporting.editor import EditorBridge
            import webbrowser,time
            bridge=EditorBridge(result);webbrowser.open(bridge.url);print('本机AOI编辑器已打开；按Ctrl+C关闭辅助服务。')
            try:
                while not bridge.cancel.wait(1):pass
            except KeyboardInterrupt:pass
            finally:bridge.close()
        if args.open:
            import webbrowser
            webbrowser.open(result.html.as_uri())
        return 0
    except Exception as exc:print('报告未生成：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
