"""Local diagnostics for model responses rejected by M10 validation."""
import json
import math
import re

from .build import atomic


_REDACTED = '[已脱敏]'
_PRIVATE_FIELDS = {
    'apikey', 'authorization', 'bearer', 'token', 'accesstoken',
    'refreshtoken', 'secret', 'password', 'credential', 'credentials',
    'cookie', 'setcookie', 'contenttype', 'xapikey', 'proxyauthorization',
    'header', 'headers', 'request', 'requestheader', 'requestheaders',
    'requestbody', 'requestpayload', 'body', 'payload', 'messages',
    'input', 'instructions', 'prompt', 'systemprompt', 'userprompt',
    'imageurl', 'inputimage', 'imagebase64', 'imagebytes', 'reasoningcontent',
}
_COUNTERS = {
    'input_tokens', 'output_tokens', 'total_tokens', 'prompt_tokens',
    'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
}
_COUNTER_DETAILS = {
    'input_tokens_details': {'cached_tokens', 'audio_tokens'},
    'prompt_tokens_details': {'cached_tokens', 'audio_tokens'},
    'output_tokens_details': {'reasoning_tokens', 'audio_tokens'},
    'completion_tokens_details': {
        'reasoning_tokens', 'audio_tokens', 'accepted_prediction_tokens',
        'rejected_prediction_tokens',
    },
}
_SECRET = re.compile(r"\bBearer\s+[^\s\"'<>;,}\]]+|\bsk-[A-Za-z0-9_-]+", re.I)
_MEDIA = re.compile(r"data:[^,\s]{1,256},[^\s\"'<>]+", re.I)
_HEADER_LINE = re.compile(
    r'\b(?:authorization|proxy-authorization|x-api-key|api[-_ ]?key|content-type|'
    r'cookie|set-cookie|access[-_ ]?token|password)\s*[:=]', re.I
)
_INLINE_REQUEST = re.compile(
    r'["\'](?:request(?:[ _-]?(?:body|headers?|payload))?|headers?|messages|'
    r'instructions|input|authorization|api[_-]?key|content-type)["\']\s*:', re.I
)


def _content(raw, provider):
    if not isinstance(raw, dict):
        return None
    if provider == 'responses':
        output = raw.get('output')
        if not isinstance(output, list):
            return None
        chunks = []
        for message in output:
            if not isinstance(message, dict) or message.get('type') != 'message':
                continue
            blocks = message.get('content')
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'output_text' and isinstance(block.get('text'), str):
                    chunks.append(block['text'])
        return ''.join(chunks) if chunks else None
    choices = raw.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get('message')
    return message.get('content') if isinstance(message, dict) else None


def _request_echoes(prepared_body):
    """Match full prompt blocks and image payloads, not individual metric values."""
    if not isinstance(prepared_body, str) or not prepared_body:
        return ()
    try:
        request = json.loads(prepared_body)
    except (ValueError, RecursionError):
        return (prepared_body,)
    if not isinstance(request, dict):
        return (prepared_body,)
    echoes = {prepared_body}
    if isinstance(request.get('instructions'), str):
        echoes.add(request['instructions'])
    for name in ('messages', 'input'):
        rows = request.get(name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = row.get('content')
            if isinstance(content, str):
                echoes.add(content)
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if isinstance(block.get('text'), str):
                        echoes.add(block['text'])
                    image = block.get('image_url')
                    if isinstance(image, dict):
                        image = image.get('url')
                    if isinstance(image, str) and image.startswith('data:image/') and ',' in image:
                        echoes.add(image)
                        echoes.add(image.split(',', 1)[1])
    return tuple(sorted((value for value in echoes if value), key=len, reverse=True))


def _scrub_text(value, secret_forms, echoes):
    for sensitive in secret_forms:
        value = value.replace(sensitive, _REDACTED)
    for echoed in echoes:
        value = value.replace(echoed, _REDACTED)
    if _HEADER_LINE.search(value) or _INLINE_REQUEST.search(value):
        return _REDACTED
    return _MEDIA.sub(_REDACTED, _SECRET.sub(_REDACTED, value))


def _scrub_value(value, secret_forms, echoes, structured_echoes, depth=0):
    if depth > 100:
        return _REDACTED
    if isinstance(value, str):
        return _scrub_text(value, secret_forms, echoes)
    if isinstance(value, (list, dict)) and any(value == echoed for echoed in structured_echoes):
        return _REDACTED
    if isinstance(value, list):
        return [_scrub_value(item, secret_forms, echoes, structured_echoes, depth+1) for item in value]
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if not isinstance(name, str) or re.sub(r'[^a-z0-9]', '', name.casefold()) in _PRIVATE_FIELDS:
                continue
            safe_name = _scrub_text(name, secret_forms, echoes)
            result[safe_name] = _scrub_value(item, secret_forms, echoes, structured_echoes, depth+1)
        return result
    if value is None or isinstance(value, (bool, int)) or isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _counters(value, names):
    if not isinstance(value, dict):
        return {}
    return {name: count for name, count in value.items() if name in names and type(count) is int and count >= 0}


def _usage(raw):
    value = raw.get('usage') if isinstance(raw, dict) else None
    usage = _counters(value, _COUNTERS)
    if isinstance(value, dict):
        for field, counters in _COUNTER_DETAILS.items():
            details = _counters(value.get(field), counters)
            if details:
                usage[field] = details
    return usage


def save_rejected(artifact, raw, provider, key, *, prepared_body):
    """Persist scrubbed model content and usage without the provider envelope."""
    value = _content(raw, provider)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            pass
    forms = {key, json.dumps(key, ensure_ascii=True)[1:-1], json.dumps(key, ensure_ascii=False)[1:-1]} if key else set()
    echoes = _request_echoes(prepared_body)
    structured_echoes = []
    for text in echoes:
        if not text.lstrip().startswith(('{', '[')):
            continue
        try:
            structured_echoes.append(json.loads(text))
        except (ValueError, RecursionError):
            pass
    output = _scrub_value(value, sorted(forms, key=len, reverse=True), echoes, structured_echoes)
    diagnostic = dict(output=output, usage=_usage(raw))
    path = artifact.directory/'ai-rejected.json'
    atomic(path, (json.dumps(diagnostic, ensure_ascii=False, allow_nan=False, indent=2)+'\n').encode())
    return path
