@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PIP_NO_CACHE_DIR=1"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 pause & exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" agent.py --mock --bind 0.0.0.0 --fps 8 %*
pause