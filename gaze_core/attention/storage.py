"""Session-rooted, quota checked, crash-readable local storage."""
from datetime import datetime,timezone
import hashlib,json,os,shutil,uuid
from pathlib import Path
import numpy as np
from PIL import Image


def numeric_json(value):
    if isinstance(value,np.ndarray):raise ValueError('JSON中不允许图像/数组，请显式转换数值坐标')
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,dict):return {str(k):numeric_json(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [numeric_json(v) for v in value]
    if isinstance(value,float) and not np.isfinite(value):raise ValueError('不允许NaN/Inf')
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('无法序列化的会话字段')


def read_jsonl(path,warnings=None):
    with Path(path).open(encoding='utf-8') as stream:
        for number,line in enumerate(stream,1):
            try:yield json.loads(line)
            except json.JSONDecodeError:
                if warnings is not None:warnings.append(f'{Path(path).name}:{number} 无效/截断行已跳过')


class SessionStore:
    def __init__(self,root,metadata,limit_bytes,session_id=None):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.id=session_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+uuid.uuid4().hex[:8]
        if Path(self.id).name!=self.id or self.id in ('.','..'):raise ValueError('非法会话id')
        self.path=self.root/self.id;self.path.mkdir()
        self.limit=limit_bytes;self.reserve=min(65536,max(1024,limit_bytes//20));self.used=0;self.streams={};self.counts={}
        self.metadata=dict(metadata,session_id=self.id,schema_version=1,state='recording')
        self.write_json('session.json',self.metadata)

    @classmethod
    def existing(cls,path,limit_bytes):
        obj=cls.__new__(cls);obj.path=Path(path).resolve();obj.root=obj.path.parent;obj.id=obj.path.name
        obj.metadata=json.loads((obj.path/"session.json").read_text(encoding="utf-8"))
        obj.limit=limit_bytes;obj.reserve=min(65536,max(1024,limit_bytes//20));obj.streams={};obj.counts={}
        obj.used=sum(p.stat().st_size for p in obj.path.rglob("*") if p.is_file())
        return obj

    def destination(self,name):
        path=self.path/name
        if path.is_symlink() or not path.resolve().is_relative_to(self.path.resolve()):raise ValueError('路径超出会话目录')
        return path

    def _check(self,extra,final=False):
        if self.used+extra>self.limit-(0 if final else self.reserve):raise OSError('会话磁盘上限已到，已停止采集')

    def append(self,name,row,*,final=False):
        data=(json.dumps(numeric_json(row),ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8');self._check(len(data),final)
        if name not in self.streams:self.streams[name]=self.destination(name).open('ab')
        self.streams[name].write(data);self.streams[name].flush();self.used+=len(data);self.counts[name]=self.counts.get(name,0)+1

    def write_bytes(self,name,data,*,final=False):
        target=self.destination(name);old=target.stat().st_size if target.exists() else 0
        # The quota includes both old and temporary files during replacement.
        self._check(len(data),final);target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.tmp')
        try:
            with temp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,target);self.used+=len(data)-old
        finally:
            if temp.exists():temp.unlink()
        return hashlib.sha256(data).hexdigest()

    def write_json(self,name,value,*,final=False):
        return self.write_bytes(name,(json.dumps(numeric_json(value),ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode(),final=final)

    def write_image(self,name,pixels):
        import io
        output=io.BytesIO();Image.fromarray(pixels).save(output,format='PNG')
        return self.write_bytes(name,output.getvalue())

    def finish(self,end_ns,reason,error=None):
        self.metadata.update(state='error' if error else 'ended',end_ns=end_ns,reason=reason,error=error)
        self.write_json('session.json',self.metadata,final=True)
        self.append('scenes.jsonl',dict(type='end',time_ns=end_ns,reason=reason,error=error,written_lines=dict(self.counts)),final=True)
        self.close()

    def close(self):
        for stream in self.streams.values():stream.flush();os.fsync(stream.fileno());stream.close()
        self.streams.clear()


def delete_session(root,path):
    root=Path(root).resolve();path=Path(path)
    if path.is_symlink() or path.resolve().parent!=root:raise ValueError('只允许删除指定根目录的整个会话')
    shutil.rmtree(path)
