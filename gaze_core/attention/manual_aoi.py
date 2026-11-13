"""Versioned manual rectangles and SHA256-scoped stimulus templates, local only."""
import hashlib,json,os,re,uuid
from pathlib import Path
from .aoi import validate_regions,primary_regions

MANUAL_SCHEMA_VERSION=2


def manual_regions(rows):
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('手动AOI必须是矩形列表，最多1000个')
    result=[]
    for row in validate_regions(rows):
        name=row.get('name',row.get('text',''))
        if not isinstance(name,str) or len(name)>80 or len(row['id'])>80:raise ValueError('AOI名称/id长度须不超过80字')
        result.append(dict(id=row['id'],rect=row['rect'],name=' '.join(name.split()),source='manual',role='primary',ranked=True))
    return result


def select_document(document,scene_id,metadata):
    """None means absent; [] is an intentional empty replacement, never fallback."""
    if isinstance(document,list):return manual_regions(document)
    if not isinstance(document,dict):raise ValueError('手动AOI JSON必须为矩形列表或版本化文档')
    if 'schema_version' not in document:
        return manual_regions(document[scene_id]) if scene_id in document else None
    if document['schema_version']!=MANUAL_SCHEMA_VERSION:raise ValueError('不支持的手动AOI版本')
    expected='image_normalized' if metadata['mode']=='stimulus' else 'full_screen_normalized'
    if document.get('coordinate_space')!=expected:raise ValueError('AOI坐标空间与会话不一致')
    digest=document.get('stimulus_sha256')
    if digest is not None and (metadata['mode']!='stimulus' or digest!=metadata.get('stimulus',{}).get('sha256')):raise ValueError('刺激物SHA256不同，拒绝套用AOI模板')
    if 'regions' in document:
        if metadata['mode']!='stimulus' or digest is None:raise ValueError('全局模板必须注明刺激物SHA256')
        return manual_regions(document['regions'])
    mapping=document.get('scenes')
    if not isinstance(mapping,dict):raise ValueError('手动AOI需要scenes映射')
    return manual_regions(mapping[scene_id]) if scene_id in mapping else None


def template_path(root,digest):
    if not isinstance(digest,str) or re.fullmatch(r'[0-9a-f]{64}',digest) is None:return None
    p=Path(root)/'aoi-templates'/(digest+'.json')
    if p.is_symlink() or not p.resolve().is_relative_to(Path(root).resolve()):raise ValueError('AOI模板路径不可为符号链接或越界')
    return p


def read_template(root,digest):
    path=template_path(root,digest)
    if path is None or not path.exists():return None
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('stimulus_sha256')!=digest:raise ValueError('AOI模板SHA256校验失败')
    select_document(value,'',dict(mode='stimulus',stimulus=dict(sha256=digest)))
    return value


def save_template(root,digest,regions):
    path=template_path(root,digest)
    if path is None:raise ValueError('刺激物SHA256无效')
    value=dict(schema_version=MANUAL_SCHEMA_VERSION,coordinate_space='image_normalized',stimulus_sha256=digest,regions=manual_regions(regions))
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2).encode();path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return path


def resolve_manual(metadata,scene_id,document,cached,root):
    selected=select_document(document,scene_id,metadata) if document is not None else None;source=None
    if selected is not None:source='manual_document'
    elif metadata['mode']=='stimulus':
        template=read_template(root,metadata['stimulus']['sha256'])
        if template is not None:selected=select_document(template,scene_id,metadata);source='stimulus_template'
    if selected is None:
        old=[r for r in primary_regions(cached.get('regions',[])) if r.get('source')=='manual']
        if old or cached.get('aoi_source')=='manual':selected=manual_regions(old);source='cached_manual'
    return selected,source
