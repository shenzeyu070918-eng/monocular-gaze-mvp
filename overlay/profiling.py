"""Opt-in main-thread callback profiling; saves statistics, never arguments/images."""
import cProfile
from datetime import datetime,timezone
import io
import json
import math
from pathlib import Path
import pstats
import sys
import threading
import time

from gaze_core.capture import timing_summary
from gaze_core.config import ROOT


class MainThreadProfile(cProfile.Profile):
    """CPython 3.12's PEP 669 callbacks are process-wide, so filter BEFORE C.

    These four hooks are selected by _lsprof.enable before it enables events:
    https://github.com/python/cpython/blob/v3.12.14/Modules/_lsprof.c
    Older thread-local cProfile implementations do not call these hooks.
    """
    def __init__(self,owner):
        super().__init__();self.owner=owner

    def _pystart_callback(self,*args):
        if threading.get_ident()==self.owner:return super()._pystart_callback(*args)

    def _pyreturn_callback(self,*args):
        if threading.get_ident()==self.owner:return super()._pyreturn_callback(*args)

    def _ccall_callback(self,*args):
        if threading.get_ident()==self.owner:return super()._ccall_callback(*args)

    def _creturn_callback(self,*args):
        if threading.get_ident()==self.owner:return super()._creturn_callback(*args)


class CallbackProfiler:
    def __init__(self,seconds,*,ready=lambda:True,context=lambda:{},directory=None,
                 clock_ns=time.perf_counter_ns,stream=None):
        if isinstance(seconds,bool) or not math.isfinite(seconds) or seconds<=0:
            raise ValueError('profile-seconds 必须为有限正数')
        if threading.get_ident()!=threading.main_thread().ident:
            raise ValueError('回调剖析器只能在主线程构造')
        self.seconds=float(seconds);self.ready=ready;self.context=context
        self.directory=Path(directory or ROOT/'data/performance')
        self.clock_ns=clock_ns;self.stream=stream or sys.stdout
        self.owner=threading.get_ident();self.profile=MainThreadProfile(self.owner)
        self.started_ns=None;self.finished=False
        self.timings={};self._inside=False
        self.paths=None;self.summary=None

    def wrap(self,callback,name):
        def measured(*args,**kwargs):
            if self.finished or self._inside or threading.get_ident()!=self.owner:
                return callback(*args,**kwargs)
            if self.started_ns is None:
                if not self.ready():return callback(*args,**kwargs)
                self.started_ns=self.clock_ns()
                print(f'主线程剖析开始：{self.seconds:g} 秒（仅定时回调，含 cProfile 开销）。',file=self.stream,flush=True)
            self._inside=True
            started=self.clock_ns()
            try:self.profile.enable()
            except Exception as exc:
                self.finished=True;self._inside=False
                print('主线程剖析启动失败：'+str(exc),file=self.stream,flush=True)
                return callback(*args,**kwargs)
            try:return callback(*args,**kwargs)
            finally:
                self.profile.disable();ended=self.clock_ns();self._inside=False
                self.timings.setdefault(name,[]).append((ended-started)/1e6)
                if ended-self.started_ns>=self.seconds*1e9:self.finish(completed=True)
        return measured

    def finish(self,*,completed=False):
        if self.finished or self.started_ns is None:return
        self.finished=True
        try:self._finish(completed)
        except Exception as exc:
            print('主线程剖析保存失败：'+str(exc),file=self.stream,flush=True)

    def _finish(self,completed):
        all_times=[v for values in self.timings.values() for v in values]
        def stats(values):return dict(count=len(values),**timing_summary(values))
        self.summary=dict(requested_seconds=self.seconds,elapsed_seconds=(self.clock_ns()-self.started_ns)/1e9,
                          completed=completed,callback_ms=stats(all_times),
                          callbacks={name:stats(values) for name,values in self.timings.items()},
                          context=self.context(),note='主线程回调墙钟耗时，含 cProfile 开销；不等于进程 CPU 百分比，不含摄像头/人脸/桌面图像。')
        output=io.StringIO()
        pstats.Stats(self.profile,stream=output).sort_stats('cumulative').print_stats(30)
        table=output.getvalue()
        print(table,file=self.stream)
        for name,values in self.summary['callbacks'].items():
            print(f'回调 {name}：{values["count"]} 次；中位 {values["median"]:.6f} ms；P90 {values["p90"]:.6f} ms',file=self.stream)
        self.directory.mkdir(parents=True,exist_ok=True)
        import os
        stem=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+f'-{os.getpid()}-main'
        base=self.directory/stem
        self.paths={suffix:str(base.with_suffix(base.suffix+'.'+suffix)) for suffix in ('prof','txt','json')}
        self.profile.dump_stats(self.paths['prof'])
        Path(self.paths['txt']).write_text(table,encoding='utf-8')
        Path(self.paths['json']).write_text(json.dumps(self.summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print('主线程剖析完成：'+self.paths['json']+('（提前结束，样本不足指定时长）' if not completed else ''),file=self.stream,flush=True)
