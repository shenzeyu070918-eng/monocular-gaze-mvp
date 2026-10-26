"""Loopback visual preview only. No WebSocket, Qt, camera, engine or hand model."""
import argparse
from dataclasses import asdict
from functools import partial
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote,urlsplit

from gaze_core.config import ROOT,DEFAULT_CONFIG,load_settings

RENDER_CHECKS={f'{scene}-{state}.png' for scene in ('planet','galaxy','text') for state in ('base','scatter','well','zoom')}|{'index.html','manifest.json','contact-sheet.png'}
RENDER_CHECKS|={f'{scene}-{name}' for scene in ('galaxy','text') for name in ('scatter-return.gif','scatter-return.png','views.png')}
RENDER_CHECKS|={f'{scene}-view-{edge}-{zoom}.png' for scene in ('galaxy','text') for edge in ('tl','tr','bl','br','top','bottom') for zoom in ('far','near')}

RENDER_CHECKS|={f'galaxy-time-{t}-{view}.png' for t in (8,60,300,600) for view in ('face','oblique')}|{'galaxy-times.png','galaxy-face.png','galaxy-oblique.png','galaxy-stability.json'}

RENDER_CHECKS|={'galaxy-default.png','galaxy-fly-in.png','text-letter-focus.png','active-checks.json','assembly.gif','assembly.png'}
RENDER_CHECKS|={f'{scene}-eye-burst.{suffix}' for scene in ('planet','galaxy','text') for suffix in ('gif','png')}

REFERENCES={'particle_planet_frames.png','particle_planet_closeup.png','render_check.png',
            'render_closeup.png','render_galaxy.png','render_text.png','render_comparison.png','render_comparison.html'}


class PreviewHandler(BaseHTTPRequestHandler):
    def __init__(self,*args,options,root=ROOT,**kwargs):
        self.options=options;self.root=Path(root);super().__init__(*args,**kwargs)

    def log_message(self,*args):pass

    def respond(self,status,body=b'',content_type='text/plain; charset=utf-8'):
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('Permissions-Policy','camera=(), microphone=(), display-capture=()')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(body)

    def do_GET(self):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':self.respond(403);return
        path=unquote(urlsplit(self.path).path)
        if path=='/preview-config':
            data=dict(interaction=asdict(self.options.interaction),stale_ms=self.options.stale_ms,transition_s=self.options.transition_s,transition_scatter_s=self.options.transition_scatter_s,text=self.options.text,pixel_ratio=self.options.pixel_ratio,intro_s=self.options.intro_s,low_fps=self.options.low_fps,low_fps_s=self.options.low_fps_s,min_pixel_ratio=self.options.min_pixel_ratio,visual=asdict(self.options.visual))
            self.respond(200,json.dumps(data,ensure_ascii=False,allow_nan=False).encode(),'application/json; charset=utf-8');return
        assets=self.root/'static/galaxy';target=None
        if path=='/':target=assets/'index.html'
        elif path.startswith('/assets/'):
            candidate=(assets/path.removeprefix('/assets/')).resolve()
            if candidate.is_relative_to(assets.resolve()):target=candidate
        elif path in ('/render-check','/render-check/'):target=self.root/'data/reference/render_check/index.html'
        elif path.startswith('/render-check/') and path.removeprefix('/render-check/') in RENDER_CHECKS:
            target=self.root/'data/reference/render_check'/path.removeprefix('/render-check/')
        elif path=='/comparison':target=self.root/'data/reference/render_comparison.html'
        elif path.startswith('/reference/') and path.removeprefix('/reference/') in REFERENCES:
            target=self.root/'data/reference'/path.removeprefix('/reference/')
        if target is None or not target.is_file():self.respond(404,b'Not found');return
        content_type='text/javascript' if target.suffix in {'.js','.mjs'} else mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.respond(200,target.read_bytes(),content_type)


def create_server(options,port=8768,*,root=ROOT):
    return ThreadingHTTPServer(('127.0.0.1',port),partial(PreviewHandler,options=options,root=root))


def main(argv=None):
    parser=argparse.ArgumentParser(description='仅画面预览，不打开摄像头或数据桥。')
    parser.add_argument('--port',type=int,default=8768);parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    args=parser.parse_args(argv);options=load_settings(args.config).galaxy
    with create_server(options,args.port) as server:
        print(f'画面预览（无摄像头）：http://127.0.0.1:{server.server_port}/?preview',flush=True)
        print('按 Ctrl+C 退出；预览不连接眼动、手势或WebSocket。',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
