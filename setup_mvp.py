"""Create a repository-local environment and fetch verified public weights."""
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('请使用 Python 3.12：Windows 用 py -3.12 setup_mvp.py；macOS 用 python3.12 setup_mvp.py。')
    executable = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        print('创建项目运行环境…', flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    subprocess.run([str(executable), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    # Other ptgaze detector backends are unused; avoid face-alignment/dlib
    # and a second OpenCV distribution sharing the cv2 import directory.
    subprocess.run([str(executable), '-m', 'pip', 'install', '--no-deps', str(ROOT / 'vendor/ptgaze')], check=True)
    subprocess.run([str(executable), str(ROOT / 'download_models.py')], check=True)
    print('安装完成。下一步运行：.venv 中的 python run.py', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise SystemExit(f'安装未完成，请保留错误信息按 README 排查：{exc}')
