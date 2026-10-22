"""Background profile I/O and condition matching; no Qt or camera handles."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from gaze_core.config import ROOT
from gaze_core.devices import load_device
from gaze_core.profiles import (build_conditions, device_fingerprint, load_python_profile,
                                save_python_profile, save_python_check, radius_from_profile,
                                load_manual_web_profile, profile_age_text)


def conditions_id(conditions):
    return hashlib.sha256(json.dumps(conditions,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _stamp(path):
    try:
        stat=path.stat();return stat.st_ino,stat.st_size,stat.st_mtime_ns
    except FileNotFoundError:return None


class ProfileManager:
    def __init__(self,settings,screen,*,on_matrix,clock=time.monotonic,directory=None):
        self.settings,self.screen,self.on_matrix=settings,screen,on_matrix
        self.name=settings.device_profile.name if settings.device_profile else settings.overlay.profile_name
        self.directory=Path(directory or ROOT/'data/profiles/python')
        self.path=self.directory/(self.name+'.json')
        self.device_path=None
        if settings.device_file:
            source=(settings.key_sources or {}).get('device_file',settings.config_sources[0] if settings.config_sources else str(ROOT/'config/default.yaml'))
            self.device_path=Path(settings.device_file)
            if not self.device_path.is_absolute():self.device_path=Path(source).parent/self.device_path
        self.clock=clock;self.conditions=None;self.profile=None
        self.reason='等待实际相机条件后匹配 Python 档案';self.revision=0
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='overlay-profiles')
        self._pending=None;self._next_poll=0.;self._signature=object();self._suppressed=False
        self.last_operation=None;self.last_error=None;self._matrix_key=None
        self._camera_snapshot=None;self._radius=None
        self._mode_text='未校准';self._mode_refresh_at=None
        self._device_fingerprint=device_fingerprint(settings.device_profile)
        self.screen_mismatch=False

    @property
    def busy(self):return self._pending is not None

    @property
    def matrix(self):return None if self.profile is None else self.profile['matrix']

    @property
    def calibration_id(self):return 'none' if self.profile is None else self.profile['calibration_id']

    @property
    def mode(self):
        if self.profile is None:return '未校准'
        now=self.clock()
        if self._mode_refresh_at is None or now>=self._mode_refresh_at:
            source=' · web 档案（手动）' if self.profile.get('pipeline','web')=='web' else ''
            self._mode_text='九点 · '+profile_age_text(self.profile)+source
            self._mode_refresh_at=now+1.
        return self._mode_text

    @property
    def radius(self):
        return self._radius

    def _activate(self,profile,reason):
        # Loaded profiles are immutable during display. Validate the complete
        # check once per activation, rather than on every GUI timer callback.
        radius=radius_from_profile(profile,self.conditions) if profile is not None and profile.get('pipeline')=='python' else None
        self.profile=profile;self.reason=reason;self.revision+=1
        if profile is not None:self.screen_mismatch=False
        self._radius=radius;self._mode_refresh_at=None
        key=None if profile is None else (profile.get('matrix_id'),profile['calibration_id'],tuple(tuple(r) for r in profile['matrix']))
        if key!=self._matrix_key:
            self._matrix_key=key;self.on_matrix(None if profile is None else deepcopy(profile['matrix']))

    def observe(self,camera):
        if self._camera_snapshot is not None and camera==self._camera_snapshot:return False
        conditions=build_conditions(self.settings,camera,self.screen)
        # Keep our own value snapshot: providers may reuse a dict and mutate a
        # nested matrix, so identity alone cannot establish unchanged inputs.
        self._camera_snapshot=deepcopy(camera)
        if self.conditions==conditions:return False
        self.conditions=conditions;self._pending=None;self._suppressed=False
        self._activate(None,'实际相机/屏幕条件变化，正在重新匹配档案')
        self._signature=object();self._next_poll=0.
        return True

    def _submit(self,kind,function):
        self.last_error=None;self.last_operation=None
        signature=self._file_signature()
        self._pending=(kind,self._executor.submit(function),signature)

    def poll(self,*,frozen=False):
        if frozen:return
        if self._pending is not None:
            kind,future,signature=self._pending
            if not future.done():return
            self._pending=None;self.last_operation=kind
            try:
                profile=future.result()
                if kind=='load' and signature!=self._file_signature():
                    self._signature=object();self._next_poll=0.
                    self._activate(None,'档案文件仍在变化，等待稳定后重新匹配')
                    self.poll();return
            except Exception as exc:
                self.last_error=str(exc)
                if kind=='load' and ('screen.' in str(exc) or '屏幕' in str(exc)):
                    from .screens import SCREEN_CHANGED
                    self.screen_mismatch=True;self.last_error=SCREEN_CHANGED+'；'+str(exc)
                if kind=='load':self._activate(None,'档案未加载：'+str(exc))
                else:self.reason='档案操作失败：'+str(exc);self.revision+=1
            else:
                if kind in {'calibration','check'}:
                    # A writer may replace the file after our save returned.
                    # Activate only through a fresh, signature-checked read.
                    self._signature=object();self._next_poll=0.
                else:
                    self._suppressed=kind=='web'
                    self._activate(profile,'已加载手动 web 档案；链路差异风险已确认' if kind=='web' else '已匹配 Python 九点档案')
            if kind not in {'calibration','check'}:
                self._signature=signature if kind=='load' else self._file_signature()
        if self.conditions is None or self._pending is not None or self.clock()<self._next_poll:return
        self._next_poll=self.clock()+self.settings.overlay.profile_poll_s
        signature=self._file_signature()
        if signature==self._signature:return
        self._signature=signature;self._suppressed=False
        conditions=deepcopy(self.conditions)
        def load():
            self.validate_device()
            return load_python_profile(name=self.name,conditions=conditions,directory=self.directory)
        self._submit('load',load)

    def _file_signature(self):
        return _stamp(self.path),_stamp(self.device_path) if self.device_path is not None else None

    def device_signature(self):
        return _stamp(self.device_path) if self.device_path is not None else None

    def change_screen(self,screen):
        from .screens import SCREEN_CHANGED
        self.screen=screen;self._camera_snapshot=None;self.conditions=None
        if self._pending is not None:self._pending[1].cancel()
        self._pending=None;self._signature=object();self._next_poll=0.;self._suppressed=False
        self.screen_mismatch=True;self._activate(None,SCREEN_CHANGED)

    def validate_device(self):
        if self.device_path is not None and device_fingerprint(load_device(self.device_path))!=self._device_fingerprint:
            raise ValueError('设备文件的标定/几何参数已改变，请重新启动后匹配档案')

    def reload(self):
        self._suppressed=False;self._signature=object();self._next_poll=0.;self.poll()

    def disable(self):
        self._pending=None;self._suppressed=True;self._signature=self._file_signature()
        self._activate(None,'已手动停用九点修正；档案文件变化或手动加载后重新匹配')

    def save_calibration(self,result):
        conditions=deepcopy(self.conditions)
        calibration=dict(sample_count=result['sample_count'],target_count=9,
                         estimated_distance_cm_median=result['estimated_distance_cm_median'])
        if result.get('quality') is not None:calibration['quality']=deepcopy(result['quality'])
        def save():
            save_python_profile(result['matrix'],name=self.name,conditions=conditions,
                                calibration_id=result['calibration_id'],calibration=calibration,directory=self.directory)
            return load_python_profile(name=self.name,conditions=conditions,directory=self.directory)
        self._submit('calibration',save)

    def save_check(self,result):
        if self.profile is None or self.profile.get('pipeline')!='python':return False
        conditions=deepcopy(self.conditions)
        def save():
            save_python_check(result['summary'],name=self.name,conditions=conditions,
                              calibration_id=result['calibration_id'],estimated_distance_cm_median=result['estimated_distance_cm_median'],
                              estimated_distance_sample_count=result['estimated_distance_sample_count'],task_id=result['task_id'],directory=self.directory)
            return load_python_profile(name=self.name,conditions=conditions,directory=self.directory)
        self._submit('check',save);return True

    def load_web(self,path,*,confirmed=False):
        if confirmed is not True:raise PermissionError('必须先确认网页与 Python 链路差异风险')
        self._submit('web',lambda:load_manual_web_profile(path,confirmed=True))

    def close(self):self._executor.shutdown(wait=True,cancel_futures=True)
