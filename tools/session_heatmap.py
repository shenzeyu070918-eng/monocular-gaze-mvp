"""Generate heatmaps from completed Python overlay accuracy checks only."""
import argparse
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import load_settings,DEFAULT_CONFIG
from gaze_core.session_heatmap import session_heatmap
from gaze_core.heatmap_export import export_heatmap


def main(argv=None):
    parser=argparse.ArgumentParser(description='只读 Python 精度核对录制；仅分析完整核对时段，不读取屏幕内容')
    parser.add_argument('recording',type=Path)
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    parser.add_argument('--task-id',help='只分析指定核对任务；默认包含所有完整核对（含点击会话末尾核对）')
    parser.add_argument('--output',type=Path,help='data/heatmaps/ 下尚不存在的子目录')
    args=parser.parse_args(argv)
    try:
        result=session_heatmap(args.recording,load_settings(args.config),task_id=args.task_id)
        directory=export_heatmap(result,directory=args.output)
    except (ValueError,OSError,TypeError,KeyError) as exc:
        print('热力图生成失败：'+str(exc),file=sys.stderr);return 1
    print(result['notice']);print('已导出：'+str(directory))
    print(f"完整核对 {len(result['completed_checks'])} 次，目标 {len(result['targets'])} 个；有效注视 {result['regions']['total_fixation_s']:.6f} 秒")
    return 0


if __name__=='__main__':raise SystemExit(main())
