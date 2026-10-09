"""Download model assets from their original sources with SHA-256 checks."""
import hashlib
from pathlib import Path
import ssl
import urllib.request

ROOT = Path(__file__).resolve().parent
MODELS = (
    ('eth-xgaze.safetensors',
     'https://huggingface.co/hysts/ptgaze-eth-xgaze-resnet18/resolve/5b6aebd02c7a612b50afb2e48c453f23533b8de3/model.safetensors',
     'd1c91b2aa6a0c73856c16890d337afdecdb05563ed52182dfdb77742f1c856bc'),
    ('face_landmarker.task',
     'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
     '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'),
)


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    try:
        import certifi
        tls = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        tls = ssl.create_default_context()
    (ROOT / 'models').mkdir(exist_ok=True)
    for name, url, expected in MODELS:
        target = ROOT / 'models' / name
        if target.is_file() and digest(target) == expected:
            print(f'已校验：{name}', flush=True)
            continue
        partial = target.with_suffix(target.suffix + '.part')
        print(f'从原始来源下载：{name}', flush=True)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'gaze-mvp/0.1.0'})
            with urllib.request.urlopen(request, timeout=120, context=tls) as response, partial.open('wb') as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if digest(partial) != expected:
                raise ValueError(f'{name} 的文件校验失败，请重试。')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    print('模型准备完成。', flush=True)


if __name__ == '__main__':
    main()
