"""Connect the Windows popover to the same local controller as the plugin."""
from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlencode

from app_paths import RUNTIME
from app_version import APP_VERSION
from desktop_control import DesktopControlError
from desktop_runtime import ensure_netfx_runtime, verify_bundle
from plugin_runtime import local_mutex

PLUGIN_ID = 'chatgpt-remote@chatgpt-remote-local'


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('Invalid plugin version')
    return tuple(map(int, value.split('.')))


def chatgpt_cli(executable):
    store = Path(os.environ['ProgramFiles']) / 'WindowsApps'
    try:
        exe = Path(executable).resolve()
        package, directory, name = exe.relative_to(store).parts
    except (TypeError, ValueError):
        raise DesktopControlError('找不到 ChatGPT 的本机插件管理组件') from None
    if (not re.fullmatch(r'OpenAI\.Codex_[^\\/]+_2p2nqsd0c76g0', package, re.IGNORECASE)
            or directory.casefold() != 'app' or name.casefold() != 'chatgpt.exe'):
        raise DesktopControlError('找不到 ChatGPT 的本机插件管理组件')
    return exe.parent / 'resources' / 'codex.exe'


class NativePopover:
    def __init__(self, controller, assembly: Path):
        self.controller = controller
        self.assembly = assembly
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, name='remote-popover', daemon=True)

    def run(self):
        try:
            while not self.stopping.is_set():
                with local_mutex(self.controller.data, 'Popover', 0) as acquired:
                    if acquired and not self.stopping.is_set():
                        self._run_native()
                        return
                self.stopping.wait(1)
        except Exception as error:
            logging.getLogger(__name__).error('popover_start_failed type=%s', type(error).__name__)

    def _run_native(self):
        ensure_netfx_runtime(self.assembly.parents[1], 'runtime-manifest.json', 'core/netfx.config')
        import clr
        ctypes.WinDLL(str(self.assembly.with_name('WebView2Loader.dll')))
        for name in ('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll'):
            clr.AddReference(str(self.assembly.with_name(name)))
        clr.AddReference(str(self.assembly))
        from ChatGPTRemote.Native import PluginPopover
        from System import Boolean, Func, String
        profile = RUNTIME / 'popover-webview'
        PluginPopover.Run(Func[String, String, String](self.request), Func[Boolean](self.stopping.is_set), str(profile))

    def close(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)

    def request(self, action, payload):
        """Only the in-process popover receives the pairing code or QR."""
        try:
            arguments = json.loads(payload) or {}
            if action.startswith('settings.'):
                result = self.settings(action, arguments)
            else:
                value = self.controller.call(action, arguments)
                if action == 'pair':
                    result = {'Pin': value['pin'], 'Qr': value['qr'].partition(',')[2], 'Url': value['url'],
                              'ExpiresAt': int(value['expiresAt'])}
                else:
                    result = {
                        'Running': value.get('running', False),
                        'Paused': value.get('paused', False),
                        'Devices': [{'Id': device['id'], 'Name': device['name']} for device in value.get('devices', [])],
                    }
        except DesktopControlError as error:
            result = {'Error': str(error)}
        except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
            result = {'Error': '手机连接暂时不可用，请重试'}
        return json.dumps(result, ensure_ascii=False)

    def settings(self, action, payload):
        root = self.controller.root
        if action == 'settings.manage':
            catalog = root / '.agents/plugins/marketplace.json'
            if not catalog.is_file():
                raise DesktopControlError('本机插件库暂时不可用')
            os.startfile('codex://plugins/chatgpt-remote?' + urlencode({'marketplacePath': str(catalog)}))
            return {}
        if action == 'settings.remove':
            command = chatgpt_cli(payload.get('executable'))
            try:
                subprocess.run([str(command), 'plugin', 'remove', PLUGIN_ID], cwd=root,
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                               timeout=90, check=True)
            except (OSError, subprocess.SubprocessError):
                raise DesktopControlError('卸载未完成，请重试') from None
            threading.Thread(target=self.controller.runtime.close, name='remote-uninstall', daemon=True).start()
            return {'Message': '已卸载'}
        if action not in ('settings.check', 'settings.update'):
            raise DesktopControlError('不支持的插件设置操作')
        source = root / 'plugin'
        manifest = source / '.codex-plugin/plugin.json'
        try:
            release = json.loads(manifest.read_text(encoding='utf-8'))
            version = release['version']
            candidate = version_key(version)
            if release['name'] != 'chatgpt-remote':
                raise ValueError('Unexpected plugin')
        except (OSError, ValueError, TypeError, KeyError):
            raise DesktopControlError('本机插件库暂时不可用') from None
        if candidate <= version_key(APP_VERSION):
            return {'Message': '当前已是最新版本'}
        codex = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        cache = codex / 'plugins/cache/chatgpt-remote-local/chatgpt-remote'
        installed = cache / version / '.codex-plugin/plugin.json'
        if installed.is_file():
            return {'Message': f'已安装 {version}，下次打开时生效'}
        if action == 'settings.check':
            return {'UpdateVersion': version, 'Message': f'{version} 可安装'}
        if payload.get('version') != version:
            return {'UpdateVersion': version, 'Message': '版本有更新，请再次确认'}
        active = self.controller.runtime.running()
        if active and version_key(active['version']) < (2, 3, 0):
            raise DesktopControlError('重新打开 ChatGPT 后可安装更新')
        if json.loads((source / 'runtime-manifest.json').read_text(encoding='utf-8'))['version'] != version:
            raise DesktopControlError('此版本的插件包尚未构建完成')
        verify_bundle(source, 'runtime-manifest.json', 'core/netfx.config')
        command = chatgpt_cli(payload.get('executable'))
        try:
            completed = subprocess.run([str(command), 'plugin', 'add', PLUGIN_ID, '--json'], cwd=root,
                                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=90, check=True)
            installed_result = json.loads(completed.stdout)
            if installed_result.get('version') != version or not installed.is_file():
                raise DesktopControlError('更新后未读到预期版本，请重试')
        except (OSError, ValueError, subprocess.SubprocessError):
            raise DesktopControlError('安装更新失败，请重试') from None
        return {'Message': f'已安装 {version}，下次打开时生效'}
