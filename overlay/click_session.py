"""Click experiment ownership and protocol, independent of Qt and native APIs."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
import uuid

from .profiles import conditions_id


class ClickSession:
    def __init__(self, settings, screen, recording, profiles, *, start_task, set_rate,
                 notice, listener_factory=None, exclude=None, clock_ns=time.perf_counter_ns,
                 own_handles=None,cancel_task=None,
                 wall_clock=lambda: datetime.now(timezone.utc)):
        self.settings,self.screen,self.recording,self.profiles=settings,screen,recording,profiles
        self.start_task,self.set_rate,self.notice=start_task,set_rate,notice
        self.cancel_task=cancel_task
        if listener_factory is None:
            from .click_listener import ClickListener
            options=settings.click_session
            clock_options=dict(resample_ns=int(options.clock_resample_ms*1e6),
                               max_uncertainty_ns=int(options.clock_max_uncertainty_ms*1e6),
                               max_drift_ns=int(options.clock_max_drift_ms*1e6),max_delay_ns=int(options.clock_max_delay_ms*1e6))
            listener_factory=lambda *args,**kwargs:ClickListener(*args,queue_size=settings.recording.queue_size,
                                          own_handles=own_handles,clock_options=clock_options,**kwargs)
        self.listener=listener_factory(screen,recording.emit,exclude=exclude)
        self.clock_ns,self.wall_clock=clock_ns,wall_clock
        self.state='idle';self.experiment_id=None;self.baseline=None;self.conditions=None
        self.check_task_id=None;self.collection_start_ns=None;self.collection_end_ns=None
        self._lock=threading.Lock();self._captures=[]
        self._last_error=None

    @property
    def active(self):return self.state in {'baseline','collecting','awaiting_check','checking'}

    def recent_baseline(self):
        profile=self.profiles.profile
        if not profile or profile.get('pipeline')!='python' or profile.get('conditions')!=self.profiles.conditions:return False
        try:age=(self.wall_clock()-datetime.fromisoformat(profile['created_at'])).total_seconds()
        except (KeyError,ValueError,TypeError):return False
        return 0<=age<self.settings.click_session.recent_baseline_max_age_s

    def _event(self,action,**extra):
        fields=dict(action=action,experiment_id=self.experiment_id,
                    conditions_fingerprint=conditions_id(self.conditions),
                    check_task_id=self.check_task_id,event_ns=self.clock_ns())
        if self.baseline is not None:
            fields.update(baseline_matrix=self.baseline['matrix'],baseline_calibration_id=self.baseline['calibration_id'],
                          baseline_created_at=self.baseline['created_at'],baseline_age_s=self.baseline['age_s'])
        fields.update(extra)
        if not self.recording.emit('click_session',**fields):
            raise RuntimeError(self.recording.status().get('error') or '点击会话记录失败')

    def set_capture(self,enabled):
        if enabled:
            if not self.recording.acquire('clicks'):raise ValueError(self.recording.status().get('error') or '无法开启数值录制')
            try:
                if self.listener.start() is False:raise RuntimeError(self.listener.status().get('error') or '鼠标监听无法开启')
                self._last_error=None
            except Exception:
                self.listener.stop();self.recording.release('clicks','click_listener_failed');raise
        else:
            self.listener.stop();self.recording.release('clicks','click_capture_stopped')

    def begin(self,*,reuse_recent=False):
        if self.active:raise ValueError('已有点击采集会话进行中')
        if self.profiles.conditions is None:raise ValueError('请等待相机实际条件就绪')
        if reuse_recent and not self.recent_baseline():raise ValueError('当前 Python 九点档案已超过复用时间，请重做九点')
        if not self.recording.acquire('experiment'):raise ValueError(self.recording.status().get('error') or '录制无法开启')
        self.experiment_id=uuid.uuid4().hex;self.state='baseline';self.baseline=None
        self.conditions=deepcopy(self.profiles.conditions);self.check_task_id=None
        self.collection_start_ns=self.collection_end_ns=None
        with self._lock:self._captures=[]
        try:
            self.set_capture(False)
            self._event('started')
            if reuse_recent:self.baseline_ready(self.profiles.profile)
            else:self.start_task('calibrate')
        except Exception:
            self.abort('start_failed');raise

    def baseline_ready(self,profile):
        if self.state!='baseline':return
        if not profile or profile.get('pipeline')!='python' or profile.get('conditions')!=self.conditions:
            self.abort('baseline_failed');return
        age=(self.wall_clock()-datetime.fromisoformat(profile['created_at'])).total_seconds()
        self.baseline=dict(matrix=deepcopy(profile['matrix']),calibration_id=profile['calibration_id'],
                           created_at=profile['created_at'],age_s=max(0.,age))
        try:
            self._event('baseline')
            self.collection_start_ns=self.clock_ns();self.state='collecting'
            self.set_rate(True,max_fps=self.settings.click_session.max_fps)
            self.set_capture(True)
            self._event('collecting',collection_start_ns=self.collection_start_ns,max_fps=self.settings.click_session.max_fps)
            self.notice('点击会话正在采集；完成自然使用后请选择结束采集并核对。',False)
        except Exception:
            self.abort('collection_failed')
            self.notice('点击采集启动失败，实验已中止。',True)

    def observe(self,prediction):
        # Numeric sink thread; no Qt calls or matrix/geometry work here.
        with self._lock:
            if self.state=='collecting' and prediction.captured_ns>=self.collection_start_ns:
                self._captures.append(prediction.captured_ns)

    def _timings(self):
        with self._lock:captures=tuple(self._captures)
        fps=None
        if len(captures)>1 and captures[-1]>captures[0]:fps=(len(captures)-1)*1e9/(captures[-1]-captures[0])
        return dict(collection_start_ns=self.collection_start_ns,collection_end_ns=self.collection_end_ns,
                    inference_frame_count=len(captures),actual_inference_fps=fps,max_fps=self.settings.click_session.max_fps)

    def end_collection(self):
        if self.state!='collecting':raise ValueError('当前没有自然点击采集进行中')
        self.collection_end_ns=self.clock_ns();self.state='awaiting_check'
        self.set_capture(False);self.set_rate(False)
        try:self._event('collection_ended',**self._timings())
        except Exception:
            self.abort('recording_failed');raise

    def begin_check(self):
        if self.state!='awaiting_check':raise ValueError('请先结束点击采集')
        if self.profiles.profile is None or self.profiles.calibration_id!=self.baseline['calibration_id']:
            self.abort('baseline_changed');raise ValueError('B 基线已改变，本次实验已中止')
        self.state='checking'
        runner=None
        try:
            check_started_ns=self.clock_ns()
            runner=self.start_task('evaluate');self.check_task_id=runner.task_id
            self._event('check_started',event_ns=check_started_ns)
            return runner
        except Exception:
            if runner is not None:
                self.abort('recording_failed')
                if self.cancel_task is not None:self.cancel_task('recording_failed')
            else:self.state='awaiting_check'
            raise

    def completed_check(self,result):
        if self.state!='checking' or result['task_id']!=self.check_task_id:return
        try:self._event('completed',**self._timings())
        except RuntimeError as exc:
            self.abort('recording_failed');self.notice('点击会话收尾失败：'+str(exc),True);return
        self.state='completed';self.set_rate(False)
        self.recording.release('experiment','click_session_completed')
        self.notice('点击采集会话已完成，公共核对已写入同一录制。',False)

    def cancelled_task(self,kind,reason):
        if not self.active:return
        if kind=='evaluate' and self.state=='checking':
            self.state='awaiting_check';self.check_task_id=None
            self.notice('末尾核对未完成；可重试核对或中止点击会话。',False)
        elif kind=='calibrate' and self.state=='baseline':self.abort(reason)

    def abort(self,reason='user_aborted'):
        was_active=self.active
        try:self.set_capture(False)
        finally:self.set_rate(False)
        if was_active:
            try:self._event('aborted',reason=reason)
            except RuntimeError:pass
        self.state='aborted' if was_active else self.state
        self.recording.release('experiment',reason)

    def tick(self):
        status=self.listener.status()
        if status.get('error') and status['error']!=self._last_error:
            error=status['error'];self._last_error=error;self.abort('click_listener_failed')
            self.notice('点击采集失败，已停止：'+error,True)
            return
        if self.active and self.profiles.conditions!=self.conditions:
            self.abort('conditions_changed')
            self.notice('设备条件变化，点击会话已中止。',True)
        elif self.state in {'collecting','awaiting_check','checking'} and (
                self.profiles.profile is None or self.profiles.calibration_id!=self.baseline['calibration_id']):
            self.abort('baseline_changed');self.notice('B 基线改变，点击会话已中止。',True)

    def close(self):self.abort('overlay_closed')
