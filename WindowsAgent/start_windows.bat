@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PIP_NO_CACHE_DIR=1"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] 正在创建本地 Python 虚拟环境...
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
echo [2/2] 检查依赖并启动 Windows Agent...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" agent.py --bind 0.0.0.0 --fps 8 --max-width 1280 %*
pause
exit /b 0
:failed
echo 安装失败。请确认 Windows 已安装 Python 3.10+ 且 py 命令可用。
pause
exit /b 1