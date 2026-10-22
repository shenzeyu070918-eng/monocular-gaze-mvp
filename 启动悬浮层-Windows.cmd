@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先使用 py -3.12 setup_mvp.py --overlay 安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" overlay.py %*
if not "%errorlevel%"=="0" pause
