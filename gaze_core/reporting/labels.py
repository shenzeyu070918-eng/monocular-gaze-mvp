"""One short label for figures, rankings and headline cards."""
import re
from gaze_core.attention.aoi import primary_regions


def region_label(region,number,chars=8):
    if region.get('source')=='manual' and region.get('name'):return region['name']
    text=re.sub(r'\s+','',region.get('text',''))
    if region.get('image_like'):return '图片：'+text[:chars]+('…' if len(text)>chars else '') if text else f'图片区域 {number}'
    return f'区域 {number}'+('：'+text[:chars]+('…' if len(text)>chars else '') if text else '')


def region_rows(regions,stats,chars=8):
    definitions={r['id']:r for r in primary_regions(regions)};numbers={k:i for i,k in enumerate(definitions,1)};rows=[]
    for row in stats:
        definition=definitions.get(row['id'])
        if definition is None:continue
        rows.append(dict(row,label=region_label(definition,numbers[row['id']],chars),number=numbers[row['id']],rect=definition['rect'],source='manual' if definition.get('source')=='manual' else 'auto'))
    return rows
