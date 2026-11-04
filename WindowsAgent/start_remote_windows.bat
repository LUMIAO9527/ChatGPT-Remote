@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PIP_NO_CACHE_DIR=1"
set "PYTHONDONTWRITEBYTECODE=1"
where tailscale >nul 2>nul || set "PATH=%PATH%;C:\Program Files\Tailscale"
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 正在创建本地 Python 虚拟环境...
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
echo [2/3] 检查依赖...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo [3/3] 启动外网模式（Tailscale Funnel HTTPS + 访问密钥 + 低流量画面）...
echo 若还没配置 Funnel，请先运行 setup_funnel_windows.bat
".venv\Scripts\python.exe" agent.py --bind 127.0.0.1 --profile remote --trusted-host auto --access-key auto %*
pause
exit /b 0
:failed
echo 安装失败。请确认 Windows 已安装 Python 3.10+ 且 py 命令可用。
pause
exit /b 1