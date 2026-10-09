#!/bin/zsh
MVP_APP_DIR="$(cd "$(dirname "$0")" && pwd)"
MVP_PYTHON="$MVP_APP_DIR/.venv/bin/python"
if [ ! -x "$MVP_PYTHON" ]; then
  MVP_PYTHON="$MVP_APP_DIR/../../work/.venv/bin/python"
fi
if [ ! -x "$MVP_PYTHON" ]; then
  print "未找到运行环境，请参照 README.md 重新安装。"
  read -k 1
  exit 1
fi
cd "$MVP_APP_DIR" || exit 1
if curl --fail --silent --max-time 2 http://127.0.0.1:8765/api/status >/dev/null; then
  open -a 'Google Chrome' http://127.0.0.1:8765
  exit 0
fi
"$MVP_PYTHON" -m uvicorn app:app --host 127.0.0.1 --port 8765 --no-access-log &
MVP_SERVER_PID=$!
trap 'kill "$MVP_SERVER_PID" 2>/dev/null' EXIT INT TERM
for MVP_ATTEMPT in {1..120}; do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8765/api/status >/dev/null; then
    open -a 'Google Chrome' http://127.0.0.1:8765
    wait "$MVP_SERVER_PID"
    exit $?
  fi
  if ! kill -0 "$MVP_SERVER_PID" 2>/dev/null; then
    print "启动失败，请查看上方错误信息。"
    read -k 1
    exit 1
  fi
  sleep 1
done
print "启动超时，请查看上方错误信息。"
read -k 1
exit 1
