"""Local bindings for region names and fixed report context."""
from collections import Counter, defaultdict
import re
from unicodedata import normalize
from urllib.parse import quote

PRECISION_RANGE = '1.3–2.5 cm'
M9_HARD_RANGE = '66.7%–85.3%'


def region_references(data, selected):
    rows = [(scene['id'], region) for scene in data['scenes'] if scene['id'] in selected
            for region in scene['stats']['regions']]
    counts = Counter(region['id'] for _, region in rows)
    references = {}
    for scene_id, region in rows:
        region_id = quote(region['id'], safe='')
        if counts[region['id']]>1:
            region_id = quote(scene_id, safe='')+'/'+region_id
        references[scene_id, region['id']] = region_id, region.get('label', region['id'])
    return references


def constant_references(sample):
    return {
        'const.precision_range': PRECISION_RANGE,
        'const.m9_hard_range': 'M9 四轮历史硬归属范围 '+M9_HARD_RANGE,
        'const.sample_n': str(sample)+' 名',
    }


def _compact(value):
    offsets = []
    chars = []
    for index, char in enumerate(value):
        for part in normalize('NFKC', char):
            if not part.isspace():
                chars.append(part)
                offsets.append(index)
    return ''.join(chars), offsets


def region_normalizer(aois):
    """Recognize full, unambiguous names before checking the text's numbers."""
    labels = defaultdict(list)
    for key, label in aois.items():
        canonical, _ = _compact(normalize('NFC', label))
        if canonical:
            labels[canonical].append(key)
    alternatives = [r'(?P<placeholder>\{\{[^{}]+\}\})']
    keys = {}
    for label in sorted(labels, key=lambda value: (-len(value), value)):
        if len(labels[label])!=1:
            continue
        escaped = re.escape(label)
        quoted = '|'.join(left+escaped+right for left,right in [('「','」'),('『','』'),('“','”'),('"','"')])
        variants = [r'(?:区域)?(?:'+quoted+')']
        # A numeric label must be quoted to distinguish it from a bare measurement.
        if len(label)>1 and not re.match(r'^[+\-−]?(?:\d|\.\d)',label):
            left = r'(?<![A-Za-z0-9])' if re.match(r'[A-Za-z0-9]',label) else ''
            right = r'(?![A-Za-z0-9])' if re.search(r'[A-Za-z0-9]$',label) else ''
            if label[-1].isdigit():
                right += r'(?![.,]\d|%|秒|厘米|倍)'
            variants.append(left+escaped+right)
        group='aoi'+str(len(keys))
        keys[group]=labels[label][0]
        alternatives.append('(?P<'+group+'>'+ '|'.join(variants)+')')
    pattern=re.compile('|'.join(alternatives))

    def canonicalize(text):
        text=normalize('NFC',text)
        compact, offsets=_compact(text)
        parts=[]
        end=0
        for match in pattern.finditer(compact):
            start=offsets[match.start()]
            parts.append(text[end:start])
            end=offsets[match.end()-1]+1
            if match.lastgroup=='placeholder':
                raw=text[start:end]
                parts.append(raw if raw.startswith('{{') and raw.endswith('}}') else match[0])
            else:
                parts.append('{{aoi:'+keys[match.lastgroup]+'}}')
        parts.append(text[end:])
        return ''.join(parts)

    return canonicalize
