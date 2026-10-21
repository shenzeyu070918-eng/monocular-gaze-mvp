"""Same in-memory camera frames / normalized patches; numeric output only."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from gaze_core.config import load_settings,print_configuration
from gaze_core.capture import open_camera,timing_summary
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


def compare(settings,images,*,warmup=3,threads=(2,4),devices=('cpu','mps'),engine_factory=None):
    """Images never leave memory. Full engine and fixed-patch model tests differ."""
    import torch
    from gaze_core.engine import Engine
    engine_factory=engine_factory or Engine
    if not images:raise ValueError('没有可比较输入')
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
                sync=torch.mps.synchronize if device=='mps' else lambda:None
                for i in range(warmup):engine.predict(images[i%len(images)],settings.geometry,settings.camera.grayscale)
                original=engine.model._run_face_model
                gather=reference is None
                if gather:patches.clear()
                full_ms=[];values=[];statuses={}
                for image in images:
                    result,elapsed=measured_call(lambda:engine.predict(image,settings.geometry,settings.camera.grayscale),synchronize=sync)
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
                        for image in images:engine.predict(image,settings.geometry,settings.camera.grayscale)
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
            except Exception as exc:
                reports.append(dict(requested_device=device,num_threads=count,status='failed',reason=str(exc)))
            finally:
                if engine is not None:engine.close()
    return dict(note='同一组内存输入；只报告实测，不表示精度。engine_ms 包含 MediaPipe/几何；model_forward_ms 不含预处理/传输；fixed_patch_path_ms 包含它们。MPS 计时前后同步。',
                camera_index=settings.camera.index,requested_resolution=[settings.camera.width,settings.camera.height],
                actual_resolution=[images[0].shape[1],images[0].shape[0]],grayscale=settings.camera.grayscale,
                torch_version=torch.__version__,warmup=warmup,results=reports)


def main(argv=None):
    parser=argparse.ArgumentParser(description='CPU/MPS 同输入实测；不保存摄像头或人脸图像')
    parser.add_argument('--config',default=None)
    parser.add_argument('--frames',type=int,default=20)
    parser.add_argument('--warmup',type=int,default=3)
    parser.add_argument('--threads',type=int,nargs='+',default=[2,4])
    parser.add_argument('--devices',nargs='+',choices=['cpu','mps'],default=['cpu','mps'])
    args=parser.parse_args(argv)
    if not 1<=args.frames<=120 or args.warmup<0 or any(v<=0 for v in args.threads) or 'cpu' not in args.devices:
        parser.error('frames 须在 1–120，warmup 非负，threads 正数，并包含 cpu 基准')
    try:
        settings=load_settings() if args.config is None else load_settings(args.config)
        print_configuration(settings)
        camera=open_camera(settings.camera);images=[]
        try:
            # Same owner opens, reads and finally releases. Full warmup also
            # allows existing brightness protection to reject a black device.
            deadline=time.monotonic()+settings.camera.warmup_timeout_s
            for image,_ in read_camera_frames(camera,settings.camera):
                if time.monotonic()<deadline or float(image.mean())<=settings.camera.black_threshold:continue
                images.append(image.copy())
                if len(images)>=args.frames:break
        finally:camera.release()
        report=compare(settings,images,warmup=args.warmup,threads=args.threads,devices=args.devices)
        print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        return 1 if any(r['status']=='failed' for r in report['results']) else 0
    except Exception as exc:
        print('对比失败：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
