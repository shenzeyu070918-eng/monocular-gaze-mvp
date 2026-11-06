"""Synthetic-only report fixtures; never read a user's screen sessions."""
from dataclasses import asdict,replace
from pathlib import Path
import numpy as np
from gaze_core.config import load_settings
from gaze_core.attention.storage import SessionStore
from gaze_core.attention.processing import process_session


def session(root,name='sample',identity='P01',x=.25,mode='stimulus'):
    settings=load_settings(use_local=False);scale=dict(sigma_x=.04,sigma_y=.06,sigma_cm=2.,sigma_px=None,source='configured_cm')
    metadata=dict(synthetic=True,start_ns=1000000000,end_ns=3000000000,mode=mode,participant_id=identity,geometry=asdict(settings.geometry),fixation=asdict(settings.fixation),heatmap=asdict(settings.heatmap),attention=asdict(settings.attention),kernel=scale,profile=dict(matrix=[[1,0],[0,1],[0,0]],calibration_id='synthetic-ninepoint'),analysis_size=[640,400],stimulus=dict(sha256='same-original-image',screen_rect=[0,0,1,1]))
    store=SessionStore(root,metadata,100_000_000,session_id=name)
    try:
        pixels=np.zeros((400,640,3),np.uint8);pixels[:,:320]=[220,233,247];pixels[:,320:]=[247,220,188]
        sha=store.write_image('scenes/a.png',pixels);store.append('scenes.jsonl',dict(type='scene',scene_id='a',start_ns=1000000000,image='scenes/a.png',sha256=sha,app='合成测试页'))
        definitions=[dict(id='left',text='合成标题区',rect=[0,0,.5,1],source='manual',kind='manual'),dict(id='right',text='合成图片区',rect=[.5,0,1,1],source='manual',kind='manual')]
        store.write_json('aoi.json',dict(scenes=dict(a=dict(regions=definitions,ocr_lines=[]))))
        for i in range(21):store.append('gaze.jsonl',dict(capture_ns=1000000000+i*100000000,completed_ns=1000000001+i*100000000,valid=True,status='tracking',raw=[np.float64(x),np.float32(.5)]))
        process_session(store,settings.attention,reocr=False)
        store.finish(3000000000,'synthetic')
        return store.path
    finally:store.close()
