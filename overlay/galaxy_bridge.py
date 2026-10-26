"""Temporary loopback-only HTTP/WS host. No camera, model, or public listener."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import queue
import secrets
import socket
import threading
import time
from gaze_core.config import ROOT
from gaze_core.galaxy_scene import PatternCycle
from gaze_core.galaxy_protocol import validate_frame,viewport,telemetry,scene_request


class GalaxyBridge:
    def __init__(self,options,screen,*,clock_ns=time.perf_counter_ns):
        self.options,self.screen,self.clock=options,screen,clock_ns
        self.token=secrets.token_urlsafe(32);self.port=None;self.error=None;self.events=queue.SimpleQueue()
        self.mapping=None;self.revision=0;self.started_ns=None;self.last_seen=None
        self._lock=threading.Lock();self._latest=None;self.dropped=0;self._serial=0;self._client=False
        self.server=None;self.thread=None;self.closed=False;self.telemetry=[]
        self._scene=PatternCycle(options)
        self.native_fullscreen=False
        self.ui_focused=False

    @property
    def url(self):return None if self.port is None else f'http://127.0.0.1:{self.port}/'+('?debug' if getattr(self,'debug',False) else '')+'#'+self.token

    def authenticated(self,token,origin):
        return isinstance(token,str) and token.isascii() and bool(self.token) and secrets.compare_digest(token,self.token) and origin==f'http://127.0.0.1:{self.port}'

    def publish(self,frame):
        frame=validate_frame(frame)
        with self._lock:
            if self.closed:return False
            if self._latest is not None:self.dropped+=1
            self._serial+=1;self._latest=(self._serial,frame)
        return True

    def context(self):
        with self._lock:return self.mapping,self.revision,self.started_ns

    def scene_feed(self,point,radii,capture_ns,valid,*,viewport_revision):
        with self._lock:
            if self.mapping is not None and viewport_revision==self.revision:
                self._scene.feed(point,radii,capture_ns,valid,viewport_size=self.mapping.window_size)
            return self._scene.snapshot(capture_ns)

    def step_scene(self,direction,viewport_revision):
        with self._lock:
            if self.mapping is None or viewport_revision!=self.revision:return None
            return dict(self._scene.advance(direction,self.clock()),viewport=self.revision)

    def invalidate(self):
        with self._lock:self.mapping=None;self.revision+=1;self.started_ns=None

    def start(self):
        self.thread=threading.Thread(target=self._run,name='galaxy-loopback',daemon=True);self.thread.start()

    def _run(self):
        sock=None
        try:
            import uvicorn
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(8)
            self.port=sock.getsockname()[1]
            if self.closed:sock.close();return
            self.server=uvicorn.Server(uvicorn.Config(self.application(),host='127.0.0.1',log_level='error',access_log=False,
                ws='websockets-sansio',ws_max_size=self.options.max_message_bytes,timeout_graceful_shutdown=1))
            self.server.should_exit=self.closed
            try:self.server.run(sockets=[sock])
            finally:sock.close()
        except Exception as exc:self.error='星系本地连接无法启动：'+str(exc)
        finally:
            if sock is not None:sock.close()

    def application(self):
        from fastapi import FastAPI,WebSocket
        from fastapi.responses import FileResponse,Response
        from fastapi.staticfiles import StaticFiles
        app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        root=ROOT/'static/galaxy'
        @app.middleware('http')
        async def headers(request,call_next):
            if request.headers.get('host')!=f'127.0.0.1:{self.port}':return Response(status_code=403)
            response=await call_next(request)
            response.headers['Content-Security-Policy']="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; connect-src 'self' ws://127.0.0.1:"+str(self.port)+"; img-src 'self' data:; worker-src 'self'"
            response.headers['Permissions-Policy']='camera=(), microphone=(), display-capture=()'
            response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer'
            return response
        @app.get('/')
        async def index():return FileResponse(root/'index.html')
        @app.websocket('/ws')
        async def websocket(ws:WebSocket):
            if self.closed or self._client or ws.headers.get('origin')!=f'http://127.0.0.1:{self.port}':
                await ws.close(code=1008);return
            await ws.accept()
            claimed=False
            try:
                auth=await asyncio.wait_for(ws.receive_json(),timeout=3)
                if not isinstance(auth,dict) or set(auth)!={'type','token'} or auth['type']!='auth' or not self.authenticated(auth['token'],ws.headers.get('origin')):
                    await ws.close(code=1008);return
                if self._client:await ws.close(code=1008);return
                self._client=True;claimed=True;self.last_seen=self.clock()
                config=asdict(self.options);config.pop('hand_model')
                await ws.send_json(dict(type='config',options=config))
                async def send():
                    previous=None
                    while not self.closed:
                        if previous!=self.native_fullscreen:
                            previous=self.native_fullscreen
                            await ws.send_json(dict(type='host',fullscreen=int(previous)))
                        with self._lock:latest,self._latest=self._latest,None
                        if latest is not None:await ws.send_json(dict(type='frame',data=latest[1]))
                        await asyncio.sleep(.005)
                sender=asyncio.create_task(send())
                try:
                    while not self.closed:
                        message=await asyncio.wait_for(ws.receive_json(),timeout=self.options.heartbeat_timeout_s)
                        if not isinstance(message,dict):raise ValueError('消息必须为对象')
                        self.last_seen=self.clock();kind=message.pop('type',None)
                        if kind=='geometry':
                            try:mapping=viewport(self.screen,message)
                            except ValueError as exc:
                                self.invalidate();self.events.put(('warning',str(exc)))
                                await ws.send_json(dict(type='geometry_error',code=1));continue
                            with self._lock:
                                self.mapping=mapping;self.revision+=1;self.started_ns=self.clock();self._scene=PatternCycle(self.options)
                            self.events.put(('geometry',dict(origin=mapping.window_origin,size=mapping.window_size)))
                            await ws.send_json(dict(type='ready',viewport=self.revision))
                        elif kind=='scene_step':
                            direction,view=scene_request(message)
                            status=self.step_scene(direction,view)
                            if status is not None:await ws.send_json(dict(type='scene',data=status))
                        elif kind=='invalidate' and not message:self.invalidate()
                        elif kind=='performance':
                            row=telemetry(message);self.telemetry.append(row)
                            if len(self.telemetry)>600:self.telemetry.pop(0)
                        elif kind in ('stop','fallback') and not message:self.events.put((kind,None));return
                        elif kind=='heartbeat' and not message:pass
                        elif kind=='focus' and set(message)=={'active'} and type(message['active']) is int and message['active'] in (0,1):self.ui_focused=bool(message['active'])
                        elif kind=='error' and set(message)=={'code'} and type(message['code']) is int:
                            self.events.put(('error',message['code']))
                        else:raise ValueError('未知星系消息')
                finally:sender.cancel();await asyncio.gather(sender,return_exceptions=True)
            except ValueError as exc:
                self.events.put(('warning',str(exc)))
                try:await ws.close(code=1008)
                except Exception:pass
            except Exception:
                if not self.closed:self.events.put(('disconnected',None))
            finally:
                if claimed:self._client=False;self.ui_focused=False;self.invalidate()
        app.mount('/assets',StaticFiles(directory=root),name='galaxy-assets')
        return app

    def drain(self):
        result=[]
        while True:
            try:result.append(self.events.get_nowait())
            except queue.Empty:return result

    def close(self):
        self.closed=True;self.ui_focused=False;self.invalidate()
        if self.server is not None:self.server.should_exit=True
        if self.thread is not None:self.thread.join(3)
        self.token=''
