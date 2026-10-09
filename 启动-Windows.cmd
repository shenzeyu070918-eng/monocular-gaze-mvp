@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先按照 README 安装 Python 3.12，然后运行 py -3.12 setup_mvp.py。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
pause
