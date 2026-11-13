"""Anonymous, editable participant identifiers, independent of any UI."""
import json,re
from pathlib import Path


def participant_id(value):
    if not isinstance(value,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}',value) is None:
        raise ValueError('匿名编号请用1–32位字母、数字、下划线或短横线；不要填写姓名')
    return value


def next_participant_id(root):
    maximum=0
    for path in Path(root).glob('*/session.json'):
        if path.is_symlink():continue
        try:value=json.loads(path.read_text(encoding='utf-8')).get('participant_id','')
        except (OSError,ValueError):continue
        match=re.fullmatch(r'P(\d+)',value) if isinstance(value,str) else None
        if match:maximum=max(maximum,int(match[1]))
    return f'P{maximum+1:02d}'
