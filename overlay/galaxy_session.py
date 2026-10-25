"""Nonblocking Qt-side Galaxy lifecycle. Browser/model work stays off Qt."""
from datetime import datetime,timezone
import json
import threading
import time
from gaze_core.config import ROOT
from .galaxy_bridge import GalaxyBridge
from .galaxy_browser import GalaxyBrowser
from .galaxy_processor import GalaxyProcessor


class GalaxySession:
    def __init__(self,session,radii,offset,*,debug=False,bridge_factory=GalaxyBridge,browser_factory=GalaxyBrowser):
        self.session=session;self.o=session.settings.galaxy;self.debug=debug
        self.bridge=bridge_factory(self.o,session.screen);self.browser_factory=browser_factory;self.browser=None
        self.bridge.debug=debug
        self.processor=GalaxyProcessor(self.bridge,self.o,radii,offset)
        self.closed=False;self.error=None;self.started=time.monotonic();self._warned=False;self._hand_error=None
        self._waiting_since=self.started;self._disconnected_at=None
        self.bridge.start();self.cleanup=None

    def poll(self):
        if self.closed:return []
        if self.bridge.error:self.error=self.bridge.error
        if self.browser is None and self.bridge.server is not None and self.bridge.server.started:
            self.browser=self.browser_factory(self.bridge,self.session.screen,self.o)
            self.browser.start()
            self.session.controller.demo_frames.set(self.processor)
        now=time.monotonic();events=[]
        for event in self.bridge.drain():
            if event[0]=='disconnected':self._disconnected_at=now
            else:events.append(event)
        if self.browser is not None:
            if self.browser.error:self.error=self.browser.error
            if self.browser.fallback and not self._warned:
                self._warned=True;events.append(('warning','未找到Chrome，已用默认浏览器。请将页面移至内置屏，进入全屏后点击开始；退出时可能需手动关页。'))
        if self.processor.error!=self._hand_error:
            self._hand_error=self.processor.error
            if self._hand_error:events.append(('warning',self._hand_error))
        if self._disconnected_at is not None:
            process=getattr(self.browser,'process',None)
            exited=process is not None and process.poll() is not None
            if exited or now-self._disconnected_at>=self.o.disconnect_grace_s:
                events.append(('stop' if exited else 'disconnected',None));self._disconnected_at=None
                if exited:self.error=None
        if self.bridge.started_ns is None:
            if self._waiting_since is None:self._waiting_since=now
            if now-self._waiting_since>self.o.connection_timeout_s:
                self.error='等待星系连接/点击进入超时，请重试或使用Qt星空'
        else:self._waiting_since=None
        if self.error:events.append(('error',self.error));self.error=None
        return events

    def close(self):
        if self.closed:return
        self.closed=True;self.processor.stop();self.session.controller.demo_frames.set(None)
        def finish():
            self.bridge.close()
            if self.browser is not None:self.browser.close()
            try:
                folder=ROOT/'data/performance';folder.mkdir(parents=True,exist_ok=True)
                path=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-galaxy.json')
                report=dict(version=1,processor=self.processor.report(),browser_samples=self.bridge.telemetry,
                    dropped_messages=self.bridge.dropped,closed=True)
                path.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
                print('星系数值性能记录：'+str(path),flush=True)
            except Exception as exc:print('星系性能日志写入失败：'+str(exc),flush=True)
        self.cleanup=threading.Thread(target=finish,name='galaxy-cleanup',daemon=False);self.cleanup.start()
