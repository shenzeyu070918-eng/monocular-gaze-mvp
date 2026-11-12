"""Atomic versioned local artifacts; never overwrite an M9 input or verdict."""
from dataclasses import dataclass
from datetime import datetime,timezone
import json,os,time,uuid
from pathlib import Path
from .data import public_data,safe_file
from .render import render_html
from .settings import ReportSettings


@dataclass
class ReportArtifact:
    directory: Path
    data: dict
    html: Path
    options: ReportSettings


def atomic(path,raw):
    path=Path(path);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def build(data,*,options=None,output_root=None,cancelled=lambda:False):
    options=options or ReportSettings();began=time.perf_counter();root=Path(output_root) if output_root else safe_file(data['source_root'],'reports')
    if not Path(data['source_root']).is_dir():raise ValueError('来源会话已删除')
    root.mkdir(parents=True,exist_ok=True);directory=root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex[:8]);directory.mkdir()
    stamp=datetime.now(timezone.utc).isoformat();local=public_data(data)
    manifest=dict(schema_version=1,generated_at=stamp,source_version=data['source_version'],source_hashes=data['source_hashes'],generation_ms=None,browser_open_requested_ns=None,browser_presented='未实测；打开请求成功不代表完整呈现')
    try:
        body=render_html(data,options=options,generated_at=stamp)
        if cancelled():raise InterruptedError('报告已取消')
        atomic(directory/'data.json',(json.dumps(local,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode())
        atomic(directory/'report.html',body.encode())
        if data['kind']=='session':
            from .editor import render_editor
            atomic(directory/'aoi-editor.html',render_editor(data,options=options).encode())
        manifest['generation_ms']=(time.perf_counter()-began)*1000
        atomic(directory/'manifest.json',json.dumps(manifest,ensure_ascii=False,allow_nan=False,indent=2).encode())
    except BaseException:
        import shutil
        shutil.rmtree(directory,ignore_errors=True);raise
    return ReportArtifact(directory,data,directory/'report.html',options)


def publish_ai(artifact,result,*,deadline,clock=time.monotonic,cancelled=lambda:False):
    if cancelled() or clock()>=deadline:raise TimeoutError('AI总预算超时，保留统计报告')
    html=render_html(artifact.data,options=artifact.options,ai=result,generated_at=datetime.now(timezone.utc).isoformat())
    if cancelled() or clock()>=deadline:raise TimeoutError('AI校验/渲染超时，保留统计报告')
    # Stage all bytes before replacing: no partial AI HTML on failures.
    info=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2).encode()
    if not artifact.directory.is_dir():raise InterruptedError('报告已删除')
    ai_path=artifact.directory/'ai.json';previous_ai=ai_path.read_bytes() if ai_path.exists() else None
    original=artifact.html.read_bytes()
    try:
        atomic(ai_path,info)
        if cancelled() or clock()>=deadline:raise TimeoutError('AI写入超时')
        atomic(artifact.html,html.encode())
        if cancelled() or clock()>=deadline:raise TimeoutError('AI更新超时')
    except BaseException:
        # Failed retries must also preserve an older successful interpretation.
        atomic(artifact.html,original)
        if previous_ai is None:ai_path.unlink(missing_ok=True)
        else:atomic(ai_path,previous_ai)
        raise
    return artifact.html


def write_ai_status(artifact,reason):
    """Worker-only fallback: keep any successful previous interpretation intact."""
    if not artifact.directory.is_dir():return
    atomic(artifact.directory/'ai-status.json',json.dumps(dict(reason=reason),ensure_ascii=False).encode())
    previous=artifact.directory/'ai.json'
    ai=json.loads(previous.read_text(encoding='utf-8')) if previous.exists() else None
    atomic(artifact.html,render_html(artifact.data,options=artifact.options,ai=ai,ai_reason=reason).encode())
