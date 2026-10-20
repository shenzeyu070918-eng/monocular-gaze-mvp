#!/bin/zsh
OVERLAY_APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$OVERLAY_APP_DIR" || exit 1
if [ ! -x .venv/bin/python ]; then
  print "请先使用 Python 3.12 运行 setup_mvp.py --overlay。"
  read -k 1
  exit 1
fi
.venv/bin/python overlay.py "$@"
