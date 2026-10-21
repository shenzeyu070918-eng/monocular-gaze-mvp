"""Same in-memory camera frames / normalized patches; numeric output only."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from gaze_core.config import ROOT,load_settings,print_configuration
from gaze_core.capture import open_camera,configure_camera,timing_summary
from gaze_core.camera import CameraAdapter
from gaze_core.camera_io import read_camera_frames


def measured_call(function,*,synchronize=lambda:None,clock=time.perf_counter_ns):
    synchronize();start=clock()
    value=function()
    synchronize();elapsed=(clock()-start)/1e6
    return value,elapsed


def difference(first,second):
    if not first and not second:return dict(pairs=0,max_abs=None,mean_abs=None)
    a,b=np.asarray(first,dtype=float),np.asarray(second,dtype=float)
    if a.shape!=b.shape or not np.isfinite([a,b]).all():raise ValueError('对比输出形状不同或含无效数值')
    delta=np.abs(a-b)
    return dict(pairs=len(first),max_abs=float(delta.max()),mean_abs=float(delta.mean()))


class ComparisonCameraError(ValueError):
    """Invalid camera conditions abort the whole comparison, not just one row."""


def check_resolution(settings, image):
    actual=(image.shape[1],image.shape[0])
    requested=(settings.camera.width,settings.camera.height)
    if actual!=requested:
        raise ComparisonCameraError(f'实际分辨率 {actual[0]}×{actual[1]} 与配置 {requested[0]}×{requested[1]} 不同；停止对比。请检查摄像头编号、后端和分辨率设置。')
    return actual


def check_intrinsics(settings, metadata):
    if not isinstance(metadata,dict) or metadata.get('intrinsics_source')!='calibrated' or metadata.get('intrinsics_reason') is not None:
        reason=(metadata.get('intrinsics_reason') or metadata.get('intrinsics_source') or 'missing_camera_metadata') if isinstance(metadata,dict) else 'missing_camera_metadata'
        raise ComparisonCameraError(f'内参未使用标定值（{reason}）；停止对比。请指定匹配的设备文件和相机分辨率，不使用估计内参继续测速。')
    actual=(metadata.get('width_px'),metadata.get('height_px'))
    if actual!=(settings.camera.width,settings.camera.height):
        raise ComparisonCameraError('推理内参分辨率与配置不同；停止对比。')
    return metadata


def comparison_camera(settings):
    adapter=CameraAdapter(settings.device_profile,undistort=settings.camera.undistort,alpha=settings.camera.undistort_alpha)
    return check_intrinsics(settings,adapter.prepare((settings.camera.width,settings.camera.height),settings.geometry.focal_scale).metadata)


def collect_images(settings,count):
    comparison_camera(settings)  # Refuse fallback before opening a camera/model.
    camera=open_camera(settings.camera);images=[]
    try:
        configure_camera(camera,settings.camera)
        deadline=time.monotonic()+settings.camera.warmup_timeout_s
        for image,_ in read_camera_frames(camera,settings.camera):
            check_resolution(settings,image)  # Also check frames during warmup.
            if time.monotonic()<deadline or float(image.mean())<=settings.camera.black_threshold:continue
            images.append(image.copy())
            if len(images)>=count:break
    finally:camera.release()
    return images


def output_path(value):
    path=Path(value).expanduser().resolve()
    data=(ROOT/'data').resolve()
    if path==data or not path.is_relative_to(data) or path.is_dir():
        raise ValueError('--output 必须为本项目 data/ 下的文件路径（该目录不进 Git）')
    return path


def write_report(path,report):
    encoded=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,suffix='.tmp',delete=False) as target:
            temporary=Path(target.name);target.write(encoded)
        temporary.replace(path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def summary_table(report):
    def timing(row,key):
        value=row.get(key,{})
        return '—' if value.get('median') is None else f'{value["median"]:.3f} / {value["p90"]:.3f}'
    lines=['设备 | 线程数 | engine 中位/P90 (ms) | model_forward 中位/P90 (ms) | 有效帧数 | 状态']
    for row in report['results']:
        valid=f'{row["valid"]}/{row["inputs"]}' if row['status']=='measured' else '—'
        lines.append(' | '.join((row.get('actual_device',row['requested_device']),str(row['num_threads']),
                               timing(row,'engine_ms'),timing(row,'model_forward_ms'),valid,
                               row['status']+('：'+row['reason'] if row.get('reason') else ''))))
    return '\n'.join(lines)


def compare(settings,images,*,warmup=3,threads=(2,4),devices=('cpu','mps'),engine_factory=None):
    """Images never leave memory. Full engine and fixed-patch model tests differ."""
    if not images:raise ValueError('没有可比较输入')
    for image in images:check_resolution(settings,image)
    camera=comparison_camera(settings)
    import torch
    from gaze_core.engine import Engine
    engine_factory=engine_factory or Engine
    if 'cpu' not in devices:raise ValueError('必须包含 cpu 作为输出差异基准')
    patches=[];reference=None;patch_reference=None;reports=[]
    # CPU first: supply identical normalized face patches to every model.
    for device in sorted(set(devices),key=lambda x:x!='cpu'):
        for count in threads:
            if device=='mps' and not torch.backends.mps.is_available():
                reports.append(dict(requested_device=device,num_threads=count,status='skipped',reason='当前 PyTorch / 系统没有可用 MPS'));continue
            engine=None
            try:
                engine=engine_factory(replace(settings,inference=replace(settings.inference,device=device,cpu_threads=count,max_fps=None)))
                if engine.device!=device:raise RuntimeError('实际设备不是请求设备，不计为该设备的测量')
                def predict(image):
                    result=engine.predict(image,settings.geometry,settings.camera.grayscale)
                    check_intrinsics(settings,result.get('camera'))
                    return result
                sync=torch.mps.synchronize if device=='mps' else lambda:None
                for i in range(warmup):predict(images[i%len(images)])
                original=engine.model._run_face_model
                gather=reference is None
                if gather:patches.clear()
                full_ms=[];values=[];statuses={}
                for image in images:
                    result,elapsed=measured_call(lambda:engine.predict(image,settings.geometry,settings.camera.grayscale),synchronize=sync)
                    check_intrinsics(settings,result.get('camera'))
                    full_ms.append(elapsed);values.append(result)
                    status=result['status'];statuses[status]=statuses.get(status,0)+1
                if gather:
                    # A separate untimed CPU pass creates common patches.
                    # Observing/copying them cannot inflate full-engine times.
                    def collect(image):
                        patches.append(image.copy())
                        return original(image)
                    engine.model._run_face_model=collect
                    try:
                        for image in images:predict(image)
                    finally:engine.model._run_face_model=original
                # Preprocessing and transfers are measured separately from
                # network forward via module hooks, on the exact same patches.
                for i in range(warmup):
                    if patches:original(patches[i%len(patches)])
                forward_ms=[];patch_ms=[];outputs=[];start=[None]
                def before(module,args):
                    sync();start[0]=time.perf_counter_ns()
                def after(module,args,output):
                    sync();forward_ms.append((time.perf_counter_ns()-start[0])/1e6)
                model=engine.model._gaze_estimation_model
                hooks=[model.register_forward_pre_hook(before),model.register_forward_hook(after)]
                try:
                    for patch in patches:
                        output,elapsed=measured_call(lambda:original(patch),synchronize=sync)
                        outputs.append(np.asarray(output,dtype=float).tolist());patch_ms.append(elapsed)
                finally:
                    for hook in hooks:hook.remove()
                if reference is None:reference=values;patch_reference=outputs
                paired=[(a['raw'],b['raw']) for a,b in zip(reference,values) if a.get('valid') and b.get('valid')]
                reports.append(dict(requested_device=device,actual_device=engine.device,num_threads=count,status='measured',
                    inputs=len(images),valid=sum(bool(v.get('valid')) for v in values),statuses=statuses,
                    engine_ms=timing_summary(full_ms),model_forward_ms=timing_summary(forward_ms),
                    fixed_patch_path_ms=timing_summary(patch_ms),fixed_patch_count=len(patches),
                    raw_normalized_difference=difference([a for a,b in paired],[b for a,b in paired]),
                    fixed_patch_output_radians_difference=difference(patch_reference,outputs)))
            except ComparisonCameraError:
                raise
            except Exception as exc:
                reports.append(dict(requested_device=device,num_threads=count,status='failed',reason=str(exc)))
            finally:
                if engine is not None:engine.close()
    return dict(note='同一组内存输入；只报告实测，不表示精度。engine_ms 包含 MediaPipe/几何；model_forward_ms 不含预处理/传输；fixed_patch_path_ms 包含它们。MPS 计时前后同步。',
                camera_index=settings.camera.index,requested_resolution=[settings.camera.width,settings.camera.height],
                actual_resolution=[images[0].shape[1],images[0].shape[0]],grayscale=settings.camera.grayscale,
                camera=camera,torch_version=torch.__version__,warmup=warmup,results=reports)


def main(argv=None):
    parser=argparse.ArgumentParser(description='CPU/MPS 同输入实测；不保存摄像头或人脸图像')
    parser.add_argument('--config',default=None)
    parser.add_argument('--frames',type=int,default=20)
    parser.add_argument('--warmup',type=int,default=3)
    parser.add_argument('--threads',type=int,nargs='+',default=[2,4])
    parser.add_argument('--devices',nargs='+',choices=['cpu','mps'],default=['cpu','mps'])
    parser.add_argument('--output',help='将数值 JSON 保存到本项目 data/ 下，例如 data/inference-comparison.json')
    args=parser.parse_args(argv)
    if not 1<=args.frames<=120 or args.warmup<0 or any(v<=0 for v in args.threads) or 'cpu' not in args.devices:
        parser.error('frames 须在 1–120，warmup 非负，threads 正数，并包含 cpu 基准')
    try:
        destination=output_path(args.output) if args.output else None
        settings=load_settings() if args.config is None else load_settings(args.config)
        print_configuration(settings)
        images=collect_images(settings,args.frames)
        report=compare(settings,images,warmup=args.warmup,threads=args.threads,devices=args.devices)
        if destination is not None:
            write_report(destination,report)
            print('数值报告已保存：'+str(destination))
        else:print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        print(summary_table(report),file=sys.stdout if destination is not None else sys.stderr)
        return 1 if any(r['status']=='failed' for r in report['results']) else 0
    except Exception as exc:
        print('对比失败：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
