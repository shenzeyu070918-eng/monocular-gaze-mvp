"""Overlay-only startup diagnostics; configuration never floods the terminal."""
from dataclasses import asdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
from gaze_core.config import ROOT,configuration_info


class StartupLog:
    def __init__(self,*,verbose=False,directory=None,stream=None):
        self.verbose=verbose;self.stream=stream or sys.stderr;self.file=None;self.path=None
        try:
            folder=Path(directory or ROOT/'data/logs');folder.mkdir(parents=True,exist_ok=True)
            self.path=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+f'-{os.getpid()}-overlay.log')
            self.file=self.path.open('x',encoding='utf-8')
            print('启动日志：'+str(self.path),file=self.stream,flush=True)
        except OSError as exc:print('启动日志无法写入：'+str(exc),file=self.stream,flush=True)

    def configuration(self,settings):
        info=configuration_info(settings)
        info.update(overlay=asdict(settings.overlay),demo=asdict(settings.demo))
        # Communication labels are configurable user text, not diagnostics.
        info['demo'].pop('labels',None)
        self.detail('实际配置：'+json.dumps(info,ensure_ascii=False,allow_nan=False))

    def detail(self,message):
        if self.file is not None:
            try:self.file.write(message+'\n');self.file.flush()
            except OSError as exc:
                print('启动日志写入失败：'+str(exc),file=self.stream,flush=True)
                self.file.close();self.file=None
        if self.verbose:print(message,file=self.stream,flush=True)

    def key(self,message):
        self.detail(message)
        if not self.verbose:print(message,file=self.stream,flush=True)

    def __enter__(self):return self
    def __exit__(self,*_):
        if self.file is not None:self.file.close();self.file=None
