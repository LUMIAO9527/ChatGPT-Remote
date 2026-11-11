"""Host-independent management of the installed Remote service.

The plugin and desktop shell share the same local control protocol. This layer
does not import a desktop UI, read conversations or open model menus.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from app_version import APP_VERSION
from desktop_control import DesktopClient, DesktopControl, DesktopControlError
from plugin_runtime import PluginRuntime


class RemoteController:
    def __init__(self, root: Path, data: Path | None = None):
        self.root = Path(root).resolve()
        self.data = Path(data) if data else self.root / '_data'
        self.client = DesktopClient(self.data)
        self.lock = threading.Lock()
        self.runtime = PluginRuntime(self.root, self.data)

    def status(self) -> dict:
        try:
            result = self.client.request('status', timeout=3)
        except DesktopControlError:
            result = {'running': False, 'version': APP_VERSION, 'devices': self._saved_devices(), 'windows': []}
        result['computerName'] = self._computer_name()
        result['pluginVersion'] = APP_VERSION
        result['runtimeError'] = self.runtime.error
        return result

    def _saved_devices(self):
        try:
            entries = json.loads((self.data / 'paired_devices.json').read_text(encoding='utf-8'))
            return [{'id': DesktopControl.device_id(digest), 'name': entry.get('name', '手机'),
                     'created': entry.get('created', 0)} for digest, entry in entries.items() if isinstance(entry, dict)]
        except (OSError, ValueError, AttributeError):
            return []

    def _computer_name(self):
        try:
            value = json.loads((self.data / 'desktop_settings.json').read_text(encoding='utf-8'))
            return value.get('computerName') or os.environ.get('COMPUTERNAME', '我的电脑')
        except (OSError, ValueError, AttributeError):
            return os.environ.get('COMPUTERNAME', '我的电脑')

    def service(self, action: str) -> dict:
        if action not in ('start', 'stop', 'restart'):
            raise DesktopControlError('请选择启动、停止或重启')
        with self.lock:
            self.runtime.service(action)
            return self.status()

    def call(self, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        if action == 'status':
            return self.status()
        if action.startswith('service.'):
            return self.service(action.split('.', 1)[1])
        if action == 'pair':
            return self.client.request('pair', {'renew': bool(payload.get('renew')), 'renderQr': True})
        if action in ('target.pause', 'device.rename', 'device.remove'):
            return self.client.request(action, payload)
        raise DesktopControlError('不支持的 Remote 操作')
