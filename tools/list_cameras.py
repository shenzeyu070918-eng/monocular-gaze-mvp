"""Sequential local camera diagnostics; never saves images."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import time
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaze_core.config import load_settings, print_configuration
from gaze_core.capture import open_camera
from gaze_core.camera_io import read_camera_frames


def probe_camera(options, *, camera_factory=open_camera, monotonic=time.monotonic):
    camera = None
    report = {'index': options.index, 'opened': False, 'resolution': None, 'mean_brightness': None, 'error': None}
    try:
        camera = camera_factory(options)
        report['opened'] = bool(camera.isOpened())
        if not report['opened']:
            report['error'] = '无法打开'
            return report
        import cv2
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, options.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, options.height)
        deadline = monotonic()+options.warmup_timeout_s
        values = []
        for image, _ in read_camera_frames(camera, options, check_brightness=False):
            report['resolution'] = [int(image.shape[1]), int(image.shape[0])]
            if monotonic() >= deadline:
                values.append(float(image.mean()))
                if len(values) >= options.black_frame_count:
                    break
        if values:
            report['mean_brightness'] = sum(values)/len(values)
    except (RuntimeError, OSError) as exc:
        report['error'] = str(exc)
    finally:
        if camera is not None:
            camera.release()
    return report


def main(argv=None):
    settings = load_settings()
    parser = argparse.ArgumentParser(description='列出相机打开状态、实际分辨率、预热后平均亮度；不保存图像')
    parser.add_argument('--indices', nargs='+', type=int, required=True, help='要检查的编号，例如 0 1 2')
    args = parser.parse_args(argv)
    if any(i < 0 for i in args.indices):
        parser.error('编号必须非负')
    print_configuration(settings)
    for index in args.indices:
        report = probe_camera(replace(settings.camera, index=index))
        print(f"index {index}: 打开={report['opened']} 实际分辨率={report['resolution']} 平均亮度(0–255)={report['mean_brightness']} 错误={report['error']}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
