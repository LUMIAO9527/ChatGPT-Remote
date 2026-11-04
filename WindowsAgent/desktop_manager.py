"""Small local-only bridge used by the desktop window and its tray host."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import webbrowser

from desktop_control import DesktopClient, DesktopControl, DesktopControlError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / '_wip' / 'desktop-runtime'
PREFERENCES = HERE / 'data' / 'desktop_settings.json'
sys.path.insert(0, str(RUNTIME / 'libs'))


def powershell() -> str:
    command = shutil.which('pwsh')
    if command:
        return command
    # The Codex runtime is optional; a normal PowerShell 7 install is preferred.
    candidates = [Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'PowerShell/7/pwsh.exe',
                  Path(os.environ.get('USERPROFILE', '')) / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe']
    for path in candidates:
        if path.is_file():
            return str(path)
    raise DesktopControlError('请安装 PowerShell 7 后启动服务')


def task_command(action: str, *, enabled: bool | None = None) -> dict:
    if action not in {'Status', 'Install', 'Start', 'Stop', 'AutoStart', 'Recovery'}:
        raise DesktopControlError('不支持的服务操作')
    args = [powershell(), '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
            str(HERE / 'manage_background.ps1'), '-Action', action, '-Json']
    if action == 'Install':
        args += ['-PythonPath', sys.executable.replace('pythonw.exe', 'python.exe')]
    if enabled is not None:
        args += ['-Enabled', '1' if enabled else '0']
    try:
        result = subprocess.run(args, cwd=HERE, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        raise DesktopControlError('服务管理未完成，请稍后重试') from None
    if result.returncode:
        raise DesktopControlError('无法管理后台服务，请检查 Windows 计划任务')
    try:
        value = json.loads(result.stdout.lstrip('\ufeff').strip())
    except ValueError:
        raise DesktopControlError('无法读取后台服务状态') from None
    return value


def copy_text(value: str) -> None:
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    data = (value + '\0').encode('utf-16-le')
    block = kernel.GlobalAlloc(0x0042, len(data))
    if not block:
        raise DesktopControlError('无法复制链接')
    pointer = kernel.GlobalLock(block)
    if not pointer:
        kernel.GlobalFree(block)
        raise DesktopControlError('无法复制链接')
    ctypes.memmove(pointer, data, len(data))
    kernel.GlobalUnlock(block)
    opened = False
    for _ in range(10):
        if user.OpenClipboard(None):
            opened = True
            break
        time.sleep(.02)
    if not opened:
        kernel.GlobalFree(block)
        raise DesktopControlError('剪贴板正在使用，请重试')
    try:
        if not user.EmptyClipboard() or not user.SetClipboardData(13, block):
            kernel.GlobalFree(block)
            raise DesktopControlError('无法复制链接')
    finally:
        user.CloseClipboard()


class DesktopManager:
    def __init__(self, folder: Path | None = None):
        self.client = DesktopClient(folder or HERE / 'data')
        self.operation_lock = threading.Lock()
        self.preference_lock = threading.Lock()
        self.window = None
        self.settings_path = PREFERENCES
        self.last_status = None
        self.panel = None
        from agent import VERSION
        from desktop_updates import ReleaseUpdates
        self.updates = ReleaseUpdates(VERSION)

    def preferences(self) -> dict:
        try:
            value = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {'theme': value.get('theme') if value.get('theme') in ('system', 'light', 'dark') else 'system',
                'autoHide': value.get('autoHide') if type(value.get('autoHide')) is bool else True,
                'material': value.get('material') if value.get('material') in ('glass','solid') else 'glass',
                'motion': value.get('motion') if type(value.get('motion')) is bool else True}

    def status(self) -> dict:
        try:
            result = self.client.request('status', timeout=2)
            self.last_status = result
            return result
        except DesktopControlError as exc:
            if str(exc) == '后台暂时没有响应' and self.last_status:
                return dict(self.last_status)
            from agent import VERSION
            devices = []
            try:
                entries = json.loads((self.client.folder / 'paired_devices.json').read_text(encoding='utf-8'))
                devices = [{'id': DesktopControl.device_id(digest), 'name': entry.get('name', '手机'),
                            'created': entry.get('created', 0)} for digest, entry in entries.items()]
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            return {'running': False, 'version': VERSION, 'windows': [], 'devices': devices, 'phoneUrl': ''}

    def service(self, action: str) -> dict:
        with self.operation_lock:
            if action in ('stop', 'restart'):
                task_command('Stop')
                deadline = time.monotonic() + 8
                while self.status()['running'] and time.monotonic() < deadline:
                    time.sleep(.1)
                if self.status()['running']:
                    raise DesktopControlError('后台服务尚未停止')
            if action in ('start', 'restart'):
                info = task_command('Status')
                task_command('Start' if info['Installed'] else 'Install')
                deadline = time.monotonic() + 16
                while time.monotonic() < deadline:
                    status = self.status()
                    if status['running']:
                        return status
                    time.sleep(.2)
                raise DesktopControlError('后台启动失败，请检查 Tailscale 是否已登录')
            return self.status()

    def call(self, action: str, payload: dict | None = None) -> dict:
        try:
            if not isinstance(action, str) or payload is not None and not isinstance(payload, dict):
                raise DesktopControlError('操作参数无效')
            return {'ok': True, **self._call(action, payload or {})}
        except DesktopControlError as exc:
            return {'ok': False, 'error': str(exc)}
        except Exception:
            return {'ok': False, 'error': '操作未完成，请重试'}

    def _call(self, action: str, payload: dict) -> dict:
        if action == 'status':
            return {**self.status(), 'computerName':socket.gethostname(), 'preferences': self.preferences(), 'update': self.updates.snapshot()}
        if action == 'settings.get':
            return {'preferences': self.preferences(), 'autoStart': bool(task_command('Status').get('AutoStart'))}
        if action in ('settings.theme', 'settings.autohide', 'settings.material', 'settings.motion'):
            key = {'settings.theme':'theme','settings.autohide':'autoHide','settings.material':'material','settings.motion':'motion'}[action]
            if key == 'theme' and payload.get(key) not in ('light', 'dark', 'system'):
                raise DesktopControlError('请选择外观')
            if key == 'autoHide' and type(payload.get(key)) is not bool:
                raise DesktopControlError('收起选项无效')
            if key == 'material' and payload.get(key) not in ('glass','solid'):
                raise DesktopControlError('请选择窗口材质')
            if key == 'motion' and type(payload.get(key)) is not bool:
                raise DesktopControlError('动画选项无效')
            with self.preference_lock:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.settings_path.with_suffix('.tmp')
                preferences = self.preferences()
                preferences[key] = payload[key]
                temporary.write_text(json.dumps(preferences), encoding='utf-8')
                os.replace(temporary, self.settings_path)
            return {'preferences': self.preferences()}
        if action == 'updates.check':
            return {'update':self.updates.check(force=True)}
        if action == 'support.copy':
            from desktop_updates import CONTACT_QQ
            copy_text(CONTACT_QQ)
            return {}
        if action == 'links.open':
            from desktop_updates import PROJECT_URL
            paths={'github':'','issues':'/issues/new/choose','releases':'/releases/latest'}
            if payload.get('page') not in paths:raise DesktopControlError('链接无效')
            webbrowser.open(PROJECT_URL+paths[payload['page']])
            return {}
        if action == 'settings.autostart':
            if type(payload.get('enabled')) is not bool:
                raise DesktopControlError('启动选项无效')
            with self.operation_lock:
                info = task_command('Status')
                if not info['Installed']:
                    raise DesktopControlError('请先启动服务')
                result = task_command('AutoStart', enabled=payload['enabled'])
                return {'autoStart': bool(result['AutoStart'])}
        if action in ('service.start', 'service.stop', 'service.restart'):
            return self.service(action.split('.')[1])
        if action in ('device.rename', 'device.remove', 'target.select', 'target.pause'):
            return self.client.request(action, payload)
        if action == 'pair':
            import qrcode
            result = self.client.request('pair', {'renew': bool(payload.get('renew'))})
            image = qrcode.make(result['url'], border=2, box_size=6)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return {**result, 'qr': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}
        if action in ('link.copy', 'link.open'):
            link = self.client.request('access_link')['url']
            parsed = urlparse(link)
            if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
                raise DesktopControlError('连接地址无效')
            if action == 'link.copy':
                copy_text(link)
            else:
                webbrowser.open(link)
            return {}
        if action == 'diagnostics.copy':
            status = self.status()
            copy_text('\n'.join(['ChatGPT Remote ' + status['version'],
                                 '服务：' + ('运行中' if status['running'] else '已停止'),
                                 '已配对设备：' + str(len(status['devices'])),
                                 '目标状态：' + status.get('targetStatus', '')]))
            return {}
        if action == 'window.close':
            if self.panel:
                self.panel.hide()
            elif self.window:
                self.window.destroy()
            return {}
        if action == 'window.minimize':
            if self.panel:
                self.panel.hide()
            elif self.window:
                self.window.minimize()
            return {}
        if action == 'window.ready':
            if self.panel:self.panel.present()
            return {}
        if action == 'window.appearance':
            if payload.get('theme') not in ('light','dark'):raise DesktopControlError('外观无效')
            if payload.get('material') not in ('glass','solid'):raise DesktopControlError('窗口材质无效')
            if self.panel:self.panel.appearance_changed(payload['theme'],payload['material'])
            return {}
        if action == 'window.page':
            from desktop_platform import PANEL_SIZES
            if payload.get('page') not in PANEL_SIZES:raise DesktopControlError('页面无效')
            if self.panel:self.panel.resize(payload['page'])
            return {}
        if action == 'window.interaction':
            if type(payload.get('busy',False)) is not bool or type(payload.get('modal',False)) is not bool:
                raise DesktopControlError('交互状态无效')
            if self.panel:
                self.panel.busy=payload.get('busy',False);self.panel.modal=payload.get('modal',False)
            return {}
        raise DesktopControlError('不支持的管理操作')
