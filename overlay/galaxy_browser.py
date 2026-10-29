"""Dedicated Chrome only. Never attach to an existing user profile/window."""
import json
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser


def chrome_executable():
    paths=['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if sys.platform=='darwin' else []
    if sys.platform=='win32':
        paths += [str(Path(os.environ.get(key,''))/'Google/Chrome/Application/chrome.exe') for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')]
    return next((p for p in paths if Path(p).is_file()),None) or shutil.which('google-chrome') or shutil.which('chromium')


class GalaxyBrowser:
    def __init__(self,bridge,screen,options):
        self.bridge,self.screen,self.o=bridge,screen,options;self.error=None;self.fallback=False
        self.process=None;self._stop=threading.Event();self.thread=None;self._counter=0;self._socket=None
    def start(self):
        self.thread=threading.Thread(target=self._run,name='galaxy-browser',daemon=True);self.thread.start()
    def command(self,method,params=None):
        self._counter+=1;ident=self._counter
        self._socket.send(json.dumps(dict(id=ident,method=method,params=params or {})))
        while True:
            result=json.loads(self._socket.recv(timeout=2))
            if result.get('id')==ident:
                if 'error' in result:raise RuntimeError(str(result['error']))
                return result.get('result',{})
    def _run(self):
        temporary=None;launched=False;connections=ExitStack()
        try:
            executable=chrome_executable()
            if executable is None:
                self.fallback=True
                if not webbrowser.open(self.bridge.url,new=1):raise RuntimeError('无法打开默认浏览器')
                return
            temporary=tempfile.TemporaryDirectory(prefix='gaze-galaxy-')
            s=self.screen
            args=[executable,'--user-data-dir='+temporary.name,'--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
                  '--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-background-mode',
                  '--window-position='+str(s.x)+','+str(s.y),'--window-size='+str(s.width)+','+str(s.height),
                  '--app='+self.bridge.url]
            self.process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+self.o.connection_timeout_s;active=Path(temporary.name)/'DevToolsActivePort'
            while not active.is_file():
                if self._stop.wait(.05):return
                if self.process.poll() is not None:raise RuntimeError('Chrome提前退出')
                if time.monotonic()>deadline:raise RuntimeError('Chrome控制通道启动超时')
            lines=active.read_text().splitlines();port=int(lines[0])
            from websockets.sync.client import connect
            self._socket=connections.enter_context(connect(f'ws://127.0.0.1:{port}'+lines[1],open_timeout=3,proxy=None))
            while True:
                targets=self.command('Target.getTargets')['targetInfos']
                target=next((t for t in targets if t['type']=='page' and t['url'].startswith(f'http://127.0.0.1:{self.bridge.port}/')),None)
                if target:break
                if self._stop.wait(.05):return
                if time.monotonic()>deadline:raise RuntimeError('未找到本次Demo页面')
            window=self.command('Browser.getWindowForTarget',dict(targetId=target['targetId']))['windowId']
            self.command('Browser.setWindowBounds',dict(windowId=window,bounds=dict(windowState='normal')))
            self.command('Browser.setWindowBounds',dict(windowId=window,bounds=dict(left=int(s.x),top=int(s.y),width=int(s.width),height=int(s.height))))
            self.command('Browser.setWindowBounds',dict(windowId=window,bounds=dict(windowState='fullscreen')))
            launched=True
            while not self._stop.wait(.25):
                if self.process.poll() is not None:
                    self.bridge.events.put(('stop',None));return
                bounds=self.command('Browser.getWindowBounds',dict(windowId=window))['bounds']
                self.bridge.native_fullscreen=bounds.get('windowState')=='fullscreen'
        except Exception as exc:
            if not self._stop.is_set():
                if launched and self.process is not None and self.process.poll() is not None:self.bridge.events.put(('stop',None))
                else:self.error='Chrome全屏启动失败：'+str(exc)
        finally:
            if self._socket is not None:
                try:self.command('Browser.close')
                except Exception:pass
            connections.close()
            if self.process is not None:
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            if temporary is not None:
                try:temporary.cleanup()
                except OSError:pass
    def close(self):
        self._stop.set()
        if self.thread is not None:self.thread.join(7)
