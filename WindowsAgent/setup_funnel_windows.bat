@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PIP_NO_CACHE_DIR=1"
set "PYTHONDONTWRITEBYTECODE=1"
where tailscale >nul 2>nul || set "PATH=%PATH%;C:\Program Files\Tailscale"
echo === 配置 Tailscale Funnel（外网 HTTPS 入口）===
echo 目标: https://^<本机 Tailscale 域名^>:8443/ -^> http://127.0.0.1:8765/
echo 已有的 443 入口（DSH-Connect）不会被改动。
echo.
tailscale funnel --bg --https=8443 http://127.0.0.1:8765
if errorlevel 1 (
  echo.
  echo 配置失败：请确认 Tailscale 已登录、tailnet 已启用 Funnel，且 8443 未被其他服务占用。
  pause
  exit /b 1
)
echo.
echo 当前 Funnel 配置：
tailscale funnel status
echo.
echo 配置完成。之后双击 start_remote_windows.bat 启动 Agent，
echo 控制台会打印带访问密钥的完整外网链接。
pause