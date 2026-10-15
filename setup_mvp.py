"""Create a repository-local environment and fetch verified public weights."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def installation_commands(executable, torch_build):
    pip = [str(executable), '-m', 'pip', 'install']
    if torch_build == 'cu130':
        # Install the selected torch pair before timm can pull a default build.
        return [pip + ['-r', str(ROOT / 'requirements-cu130.txt')],
                pip + ['-r', str(ROOT / 'requirements-runtime.txt')]]
    return [pip + ['-r', str(ROOT / 'requirements.txt')]]


def choose_torch_build(profile, requested=None):
    build = requested or (profile.read_text().strip() if profile.exists() else 'default')
    # Upgrade the previously saved CUDA profile to the matching public versions.
    if build == 'cu128':
        build = 'cu130'
    if build not in {'default', 'cu130'}:
        raise ValueError('安装配置无效，请明确指定 --torch-build default 或 --torch-build cu130。')
    if build == 'cu130' and sys.platform != 'win32':
        raise ValueError('本项目的 cu130 安装入口仅用于 Windows；macOS 请使用默认安装。')
    return build


def main(argv=None):
    parser = argparse.ArgumentParser(description='安装项目运行环境和模型。')
    parser.add_argument('--torch-build', choices=['default', 'cu130'], default=None,
                        help='Windows RTX 5070 使用 cu130；省略时沿用此环境上次选择。')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('请使用 Python 3.12：Windows 用 py -3.12 setup_mvp.py；macOS 用 python3.12 setup_mvp.py。')
    executable = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    profile = ROOT / '.venv' / '.torch-build'
    torch_build = choose_torch_build(profile, args.torch_build)
    if not executable.exists():
        print('创建项目运行环境…', flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    # Preserve the selection even if a download fails partway through setup.
    profile.write_text(torch_build + '\n')
    for command in installation_commands(executable, torch_build):
        subprocess.run(command, check=True)
    # Other ptgaze detector backends are unused; avoid face-alignment/dlib
    # and a second OpenCV distribution sharing the cv2 import directory.
    subprocess.run([str(executable), '-m', 'pip', 'install', '--no-deps', str(ROOT / 'vendor/ptgaze')], check=True)
    subprocess.run([str(executable), str(ROOT / 'download_models.py')], check=True)
    print('安装完成。下一步运行：.venv 中的 python run.py', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise SystemExit(f'安装未完成，请保留错误信息按 README 排查：{exc}')
