"""Interactive local chessboard calibration. Space captures; Enter fits; R resets."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import yaml

from gaze_core.calibration import board_points, calibrate_views, diverse_enough, view_descriptor
from gaze_core.config import ROOT, load_settings, print_configuration
from gaze_core.devices import device_name, parse_device
from gaze_core.camera_io import read_camera_frames


def image_directory(path):
    """Optional image persistence is confined to the repository's ignored data/."""
    if path is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = ROOT/path
    path = path.resolve()
    if not path.is_relative_to(ROOT.resolve()/'data'):
        raise ValueError('图像只能保存在仓库 data/ 下（不允许目录或符号链接越界）')
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_device(name, camera, args, *, directory=None):
    name = device_name(name)
    camera = dict(camera, calibrated_at=datetime.now(timezone.utc).isoformat())
    data = dict(schema_version=1, name=name, camera=camera,
                screen=dict(width_cm=args.screen_width_cm, height_cm=args.screen_height_cm,
                            measured=args.screen_width_cm is not None and args.screen_height_cm is not None,
                            resolution=args.screen_resolution),
                camera_offset_cm=dict(horizontal=args.horizontal_cm, vertical=args.top_cm))
    parse_device(data, name+'.yaml')
    directory = Path(directory or ROOT/'config/devices')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/(name+'.yaml')
    temporary = path.with_suffix('.yaml.tmp')
    comments = ('# 水平 horizontal：用户面对屏幕时，摄像头在可视区域中线右侧为正（cm）。\n'
                '# 垂直 vertical：摄像头在可视区域上沿上方为正（cm），与 top_cm 一致。\n'
                '# resolution 在 camera 中是实际采集像素，在 screen 中是系统逻辑像素。\n')
    temporary.write_text(comments+yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    temporary.replace(path)
    return path


def main(argv=None):
    settings = load_settings()
    parser = argparse.ArgumentParser(description='本地 OpenCV 棋盘格标定；默认不保存图像。')
    parser.add_argument('--device', required=True, help='设备名，输出 config/devices/<设备名>.yaml')
    parser.add_argument('--corners', nargs=2, type=int, required=True, metavar=('COLS', 'ROWS'))
    parser.add_argument('--square-mm', type=float, required=True)
    parser.add_argument('--views', type=int, default=settings.calibration.views)
    parser.add_argument('--max-error-px', type=float, default=settings.calibration.max_error_px)
    parser.add_argument('--width', type=int, default=settings.camera.width)
    parser.add_argument('--height', type=int, default=settings.camera.height)
    parser.add_argument('--camera-index', type=int, default=settings.camera.index)
    parser.add_argument('--backend', choices=['auto', 'dshow'], default=settings.camera.backend)
    parser.add_argument('--screen-width-cm', type=float)
    parser.add_argument('--screen-height-cm', type=float)
    parser.add_argument('--screen-resolution', nargs=2, type=int, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--horizontal-cm', type=float, default=settings.geometry.horizontal_cm)
    parser.add_argument('--top-cm', type=float, default=settings.geometry.top_cm)
    parser.add_argument('--save-images', metavar='DATA_DIRECTORY', help='仅允许 data/ 下；默认不保存任何图像')
    args = parser.parse_args(argv)
    cli = list(argv) if argv is not None else sys.argv[1:]
    provenance = dict(settings.key_sources or {})
    for flag, key in [('--camera-index','index'),('--width','width'),('--height','height'),('--backend','backend')]:
        if any(a==flag or a.startswith(flag+'=') for a in cli): provenance['camera.'+key] = '命令行'
    settings = replace(settings, camera=replace(settings.camera,index=args.camera_index,width=args.width,height=args.height,backend=args.backend),key_sources=provenance)
    print_configuration(settings)
    camera = None
    try:
        device_name(args.device)
        board_points(args.corners, args.square_mm)
        if args.views < 3 or not np.isfinite(args.max_error_px) or args.max_error_px <= 0 or min(args.width, args.height) <= 0 or args.camera_index < 0:
            raise ValueError('采集数量至少为 3；误差阈值及分辨率须为正，设备编号须非负')
        # Validate measurements before opening hardware or writing optional images.
        placeholder = dict(matrix=[[1,0,0],[0,1,0],[0,0,1]], distortion=[0]*5,
                           resolution=[args.width,args.height], reprojection_error_px=0,
                           calibrated_at=datetime.now(timezone.utc).isoformat())
        parse_device(dict(schema_version=1, name=args.device, camera=placeholder,
                          screen=dict(width_cm=args.screen_width_cm, height_cm=args.screen_height_cm,
                                      measured=args.screen_width_cm is not None, resolution=args.screen_resolution),
                          camera_offset_cm=dict(horizontal=args.horizontal_cm, vertical=args.top_cm)), args.device+'.yaml')
        images = image_directory(args.save_images)
        if args.backend == 'dshow' and sys.platform != 'win32':
            raise ValueError('DirectShow 仅限 Windows')
        camera = cv2.VideoCapture(args.camera_index, cv2.CAP_DSHOW) if args.backend == 'dshow' else cv2.VideoCapture(args.camera_index)
        if not camera.isOpened():
            raise RuntimeError('摄像头无法打开；请检查权限并关闭占用摄像头的其他程序')
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        views, descriptors, size = [], [], None
        limits = replace(settings.calibration, views=args.views, max_error_px=args.max_error_px)
        print('空格：采集角点；Enter：计算并保存；R：重新采集；Esc/Q：退出。请移动棋盘到不同位置、距离并倾斜。')
        for image, _ in read_camera_frames(camera, replace(settings.camera, index=args.camera_index)):
            actual = (image.shape[1], image.shape[0])
            if size is None:
                size = actual
                print(f'请求分辨率 {args.width}×{args.height}；实际采集分辨率 {size[0]}×{size[1]}')
            if actual != size:
                raise RuntimeError('采集分辨率中途变化；请重新开始标定')
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            found, points = cv2.findChessboardCorners(gray, tuple(args.corners), cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
            preview = image.copy()
            if found:
                window = limits.refine_window_px
                points = cv2.cornerSubPix(gray, points, (window,window), (-1,-1),
                                         (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, limits.refine_max_iterations, limits.refine_epsilon))
                cv2.drawChessboardCorners(preview, tuple(args.corners), points, found)
            cv2.putText(preview, f'{size[0]}x{size[1]} | {len(views)}/{args.views} | SPACE capture / ENTER fit / R reset / Q quit',
                        (8,24), cv2.FONT_HERSHEY_SIMPLEX, .45, (0,255,0), 1)
            cv2.imshow('Camera calibration (local preview only)', preview)
            key = cv2.waitKey(1) & 255
            if key in (27, ord('q')):
                return 0
            if key == ord('r'):
                views.clear(); descriptors.clear()
                print('已清除角点，请重新采集。')
            elif key == 32:
                if not found:
                    print('未检测到完整棋盘格，请调整。'); continue
                descriptor = view_descriptor(points, args.corners, size, args.square_mm)
                if descriptors and min(np.linalg.norm(descriptor-d) for d in descriptors) < limits.min_view_distance:
                    print('与已有视图过于相似，请移动位置、改变距离或倾斜。'); continue
                if images is not None:
                    # Timestamp prevents overwriting images after R/repeated runs.
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
                    if not cv2.imwrite(str(images/f'{stamp}.png'), image):
                        raise OSError('可选标定图像写入失败')
                views.append(points.copy()); descriptors.append(descriptor)
                print(f'已采集 {len(views)} 张有效视图。')
            elif key in (10, 13):
                if not diverse_enough(descriptors, limits):
                    print('数量或覆盖不足：需覆盖不同横向/纵向位置及倾斜角度，请继续采集。'); continue
                result, errors = calibrate_views(views, args.corners, args.square_mm, size)
                print(f'重投影 RMS：{result["reprojection_error_px"]:.6f} px；每视图 RMS：{errors}')
                if result['reprojection_error_px'] > limits.max_error_px:
                    print('重投影误差过大；未保存参数。请按 R 重新采集，检查棋盘尺寸与清晰度。'); continue
                path = save_device(args.device, result, args)
                print(f'已保存参数：{path}\n请在 config/local.yaml 中设置 device_file: devices/{path.name}')
                return 0
    except (ValueError, OSError, RuntimeError, cv2.error) as exc:
        print(f'标定失败：{exc}', file=sys.stderr)
        return 1
    finally:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    raise SystemExit(main())
