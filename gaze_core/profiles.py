"""Atomic numeric-only personal calibration profiles in ignored data/profiles/."""
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import tempfile
import numpy as np
from .config import ROOT
from .devices import device_name


def save_profile(matrix, *, name, device_file, screen, calibration_id, directory=None):
    from .recorder import validate_screen, text_value
    name = device_name(name)
    array = np.asarray(matrix, dtype=float)
    if array.shape != (3,2) or not np.isfinite(array).all():
        raise ValueError('个人修正矩阵必须为有限 3×2')
    validate_screen(screen)
    if screen.get('fullscreen') is not True:
        raise ValueError('个人校准必须在全屏下完成')
    text_value(calibration_id, 'calibration_id');text_value(device_file,'device_file',nullable=True)
    data = dict(schema_version=1, device_name=name, matrix=array.tolist(), source='nine_point',group='B',
                created_at=datetime.now(timezone.utc).isoformat(), device_file=device_file,
                calibration_id=calibration_id, screen=screen,
                coordinates=dict(input='unfiltered_unclipped_raw_fullscreen_normalized',output='corrected_fullscreen_normalized',
                                 origin='top_left',x='right',y='down',formula='[raw_x, raw_y, 1] @ matrix',clip=False))
    folder = Path(directory or ROOT/'data/profiles');folder.mkdir(parents=True,exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=name+'-',suffix='.tmp',dir=folder)
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as output:
            json.dump(data,output,ensure_ascii=False,allow_nan=False);output.write('\n');output.flush();os.fsync(output.fileno())
        destination = folder/(name+'.json')
        os.replace(temporary,destination)
        return destination
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
