"""Synthetic + final recorded check: old RGBA upscale / new float upscale, no captures."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import cv2
import numpy as np

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gaze_core.config import ROOT,load_settings
from gaze_core.heatmap import rgba_grid
from gaze_core.heatmap_render import render_heatmap
from gaze_core.session_heatmap import session_heatmap,_checks
from tools.replay import read_session


def composite(rgba,size):
    """Simulate smooth premultiplied Qt enlargement over a fixed blank background."""
    values=rgba.astype(np.float32)/255
    alpha=values[...,3:4];premult=values[...,:3]*alpha
    alpha=cv2.resize(alpha,size,interpolation=cv2.INTER_LINEAR)[...,None]
    premult=cv2.resize(premult,size,interpolation=cv2.INTER_LINEAR)
    return np.rint(255*np.clip(premult+.94*(1-alpha),0,1)).astype(np.uint8)


def comparison(grid,screen_size,options,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    grid=np.array(grid,np.float64,copy=True);before=grid.copy()
    # Pick one outer fade-band location from the INPUT field, shared by all
    # versions. No result-dependent crop or different zoom for each image.
    peak=grid.max();relative=grid/peak if peak>0 else grid
    y,x=np.unravel_index(np.abs(relative-options.alpha_threshold*1.5).argmin(),grid.shape)
    width,height=screen_size
    cx=round((x+.5)*width/grid.shape[1]);cy=round((y+.5)*height/grid.shape[0])
    crop_w=min(width,160);crop_h=min(height,120)
    left=max(0,min(width-crop_w,cx-crop_w//2));top=max(0,min(height-crop_h,cy-crop_h//2))
    rows=[];times={}
    for name,strength in (('before',None),('after_no_dither',0.),('after_dither',options.dither_strength)):
        started=time.perf_counter_ns()
        if strength is None:rgba=rgba_grid(grid,options.max_opacity,options.alpha_threshold)
        else:rgba=render_heatmap(grid,screen_size,max_opacity=options.max_opacity,alpha_threshold=options.alpha_threshold,
                                 render_scale=options.render_scale,dither_strength=strength)
        times[name]=(time.perf_counter_ns()-started)/1e6
        image=composite(rgba,screen_size)
        crop=image[top:top+crop_h,left:left+crop_w]
        enlarged=cv2.resize(crop,None,fx=4,fy=4,interpolation=cv2.INTER_NEAREST)
        for suffix,data in (('',image),('_crop_4x',enlarged)):
            if not cv2.imwrite(str(directory/(name+suffix+'.png')),cv2.cvtColor(data,cv2.COLOR_RGB2BGR)):
                raise OSError('对照PNG写入失败')
        rows.append((name,image,enlarged))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig=Figure(figsize=(15,8),facecolor='white');FigureCanvasAgg(fig)
    for i,(name,image,crop) in enumerate(rows):
        ax=fig.add_subplot(2,3,i+1);ax.imshow(image,interpolation='nearest');ax.set_title(name);ax.axis('off')
        from matplotlib.patches import Rectangle
        ax.add_patch(Rectangle((left,top),crop_w,crop_h,fill=False,color='#202020',linewidth=1))
        ax=fig.add_subplot(2,3,i+4);ax.imshow(crop,interpolation='nearest');ax.set_title('Same crop, 4x nearest zoom');ax.axis('off')
    fig.suptitle('Same float heat grid / same blank background / same crop. No screen or camera images.')
    fig.tight_layout();fig.savefig(directory/'comparison.png',dpi=130)
    np.testing.assert_array_equal(grid,before)
    info=dict(screen_size=list(screen_size),grid_shape=list(grid.shape),
              grid_sha256=hashlib.sha256(grid.tobytes()).hexdigest(),crop_logical_px=[left,top,crop_w,crop_h],zoom=4,
              render_scale=options.render_scale,dither_strength=options.dither_strength,
              dither_unit='alpha LSB peak-to-peak; fixed Bayer tile',offline_render_ms=times,
              note='Offline single-call timings, not live overlay/WindowServer performance. Visual acceptance remains pending.')
    (directory/'comparison.json').write_text(json.dumps(info,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return info


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path)
    parser.add_argument('--output',type=Path,required=True,help='New child directory under data/heatmaps/')
    args=parser.parse_args(argv)
    try:
        output=args.output.resolve();root=(ROOT/'data/heatmaps').resolve()
        if not output.is_relative_to(root) or output==root:raise ValueError('输出只能放data/heatmaps/的新子目录')
        before=hashlib.sha256(args.recording.read_bytes()).hexdigest()
        settings=load_settings(use_local=False);session=read_session(args.recording)
        check=_checks(session.records,None)[-1]
        snapshot=session_heatmap(args.recording,settings,task_id=check['task_id'])
        size=(int(snapshot['screen']['width_px']),int(snapshot['screen']['height_px']))
        width=settings.heatmap.grid_width;height=round(width*size[1]/size[0])
        yy,xx=np.mgrid[0:height,0:width]
        synthetic=np.exp(-((xx-width*.45)**2+(yy-height*.5)**2)/(2*(width*.085)**2))
        output.mkdir(parents=True,exist_ok=False)
        synthetic_info=comparison(synthetic,size,settings.heatmap,output/'synthetic')
        recorded_info=comparison(snapshot['grid'],size,settings.heatmap,output/'recorded_final_check')
        after=hashlib.sha256(args.recording.read_bytes()).hexdigest()
        if before!=after:raise RuntimeError('源录制发生变化，不可将对照视为同源')
        manifest=dict(input_file=args.recording.name,input_sha256=before,task_id=check['task_id'],
                      targets=len(snapshot['targets']),start_ns=check['start_ns'],end_ns=check['end_ns'],
                      synthetic=synthetic_info,recorded=recorded_info,notice=snapshot['notice'])
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print('已生成合成与真实末尾核对三版本对照：'+str(output));return 0
    except (ValueError,OSError,KeyError,cv2.error) as exc:
        print('生成失败：'+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
