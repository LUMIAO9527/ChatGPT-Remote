"""Phone connection owned by the ChatGPT plugin; no desktop shell or tasks."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from desktop_control import DesktopClient, DesktopControlError
from windows_control import WindowsBackend


class AppWindowBackend(WindowsBackend):
    """The plugin follows the native ChatGPT window the user last activated."""
    def _enumerate(self):
        return [window for window in super()._enumerate()
                if window.get('exe', '').casefold() == 'chatgpt.exe'
                and window.get('class', '') == 'Chrome_WidgetWin_1']


def bind_app_window(state):
    backend = state.backend
    with state.action_lock, backend.lock:
        windows = backend._enumerate()
        current = int(backend.u32.GetForegroundWindow() or 0)
        chosen = next((window for window in windows if window['id'] == current), None)
        previous = backend.tracker.resolve(windows)
        if chosen is None and not previous and len(windows) == 1:
            chosen = windows[0]
        if chosen is not None and int(chosen['id']) != previous:
            backend.select(int(chosen['id']))


@contextmanager
def local_mutex(folder, purpose, timeout_ms):
    identity = hashlib.sha256(str(folder.resolve()).casefold().encode()).hexdigest()[:32]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, f'Local\\ChatGPTRemote.{purpose}.{identity}')
    acquired = False
    try:
        acquired = bool(handle) and kernel.WaitForSingleObject(handle, timeout_ms) in (0, 0x80)
        yield acquired
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        if handle:
            kernel.CloseHandle(handle)


@contextmanager
def connection_lock(folder):
    with local_mutex(folder, 'Plugin', 20000) as acquired:
        if not acquired:
            raise DesktopControlError('连接组件正在更新，请稍后重试。')
        yield


class PluginRuntime:
    def __init__(self, root: Path, data: Path):
        self.root, self.data = Path(root).resolve(), Path(data).resolve()
        self.client = DesktopClient(self.data)
        self.settings = self.data / 'plugin_settings.json'
        self.child = None
        self.owner_token = secrets.token_hex(24)
        self.closed = threading.Event()
        self.worker = None
        self.error = ''

    def enabled(self):
        try:
            return json.loads(self.settings.read_text(encoding='utf-8')).get('enabled') is not False
        except FileNotFoundError:
            return True
        except (OSError, ValueError, AttributeError):
            raise DesktopControlError('连接设置无法读取，请检查插件数据。') from None

    def _save_enabled(self, enabled):
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.with_name('.plugin-settings-' + secrets.token_hex(6) + '.tmp')
        try:
            temporary.write_text(json.dumps({'schema': 1, 'enabled': bool(enabled)}), encoding='utf-8')
            os.replace(temporary, self.settings)
        finally:
            temporary.unlink(missing_ok=True)

    def running(self):
        try:
            return self.client.request('status', timeout=2)
        except DesktopControlError:
            return None

    def ensure_started(self):
        if self.closed.is_set() or not self.enabled():
            return
        with connection_lock(self.data):
            if self.closed.is_set() or not self.enabled() or self.running():
                return
            environment = dict(os.environ)
            environment.update(CHATGPTREMOTE_HOME=str(self.root), CHATGPTREMOTE_DATA=str(self.data),
                               CHATGPTREMOTE_OWNER_PID=str(os.getpid()), CHATGPTREMOTE_OWNER_TOKEN=self.owner_token,
                               PYTHONDONTWRITEBYTECODE='1')
            # The packaged plugin keeps its Python dependencies beside the
            # server, so the connection never needs the retired desktop EXE.
            vendor = Path(__file__).resolve().parent.parent / 'vendor'
            environment['PYTHONPATH'] = os.pathsep.join([str(vendor), str(Path(__file__).resolve().parent),
                                                        environment.get('PYTHONPATH', '')])
            child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--serve'],
                                     cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.child = child
            deadline = time.monotonic() + 22
            while time.monotonic() < deadline and not self.closed.is_set():
                if self.running():
                    self.error = ''
                    return
                if child.poll() is not None:
                    break
                self.closed.wait(.15)
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise DesktopControlError('手机连接组件启动未完成，请重试。')

    def begin(self):
        if self.worker is not None:
            return
        def keep_connected():
            while not self.closed.is_set():
                try:
                    self.ensure_started()
                except Exception as error:
                    self.error = str(error) if isinstance(error, DesktopControlError) else '手机连接组件暂时不可用。'
                self.closed.wait(2)
        self.worker = threading.Thread(target=keep_connected, name='plugin-connection', daemon=True)
        self.worker.start()

    def service(self, action):
        if action not in ('start', 'stop', 'restart'):
            raise DesktopControlError('请选择启动、停止或重启。')
        if action in ('stop', 'restart'):
            with connection_lock(self.data):
                current = self.running()
                if current:
                    if current.get('managedBy') != 'plugin':
                        raise DesktopControlError('连接组件尚未切换到插件，请先完成更新。')
                    prepared = self.client.request('maintenance.prepare')
                    previous = self.enabled()
                    try:
                        self._save_enabled(False)
                        self.client.request('service.stop', {'token': prepared['token']})
                    except Exception:
                        self._save_enabled(previous)
                        try:
                            self.client.request('maintenance.cancel', {'token': prepared['token']})
                        except DesktopControlError:
                            pass
                        raise
                    deadline = time.monotonic() + 10
                    while self.running() and time.monotonic() < deadline:
                        time.sleep(.1)
                    if self.running():
                        raise DesktopControlError('正在停止连接，请稍后重试。')
                else:
                    self._save_enabled(False)
        if action in ('start', 'restart'):
            self._save_enabled(True)
            self.ensure_started()

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=25)
        child = self.child
        if child is not None and child.poll() is None:
            try:
                self.client.request('service.close', {'owner': self.owner_token}, timeout=2)
                child.wait(timeout=8)
            except (DesktopControlError, subprocess.TimeoutExpired):
                child.terminate()
                child.wait(timeout=5)


def serve_connection():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from agent import (AgentState, FrameHub, MockBackend, SwitchGuard, make_handler,
                       tailscale_dns_name, load_or_create_access_key)
    from app_paths import DATA, ROOT
    from desktop_control import DesktopControl

    mock = os.environ.get('CHATGPTREMOTE_MOCK') == '1'
    port = int(os.environ.get('CHATGPTREMOTE_PORT', '8765'))
    if not 0 <= port <= 65535:
        raise ValueError('Invalid connection port')
    parent = int(os.environ['CHATGPTREMOTE_OWNER_PID'])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    parent_handle = kernel.OpenProcess(0x100000, False, parent)
    if not parent_handle or kernel.WaitForSingleObject(parent_handle, 0) == 0:
        if parent_handle:
            kernel.CloseHandle(parent_handle)
        return
    # Reserve the socket before opening or recovering any persistent data.
    server = ThreadingHTTPServer(('127.0.0.1', port), BaseHTTPRequestHandler)
    server.daemon_threads = True
    host = '' if mock else tailscale_dns_name()
    if not mock and not host:
        server.server_close()
        kernel.CloseHandle(parent_handle)
        raise DesktopControlError('请先连接这台电脑的 Tailscale。')
    state = None
    control = None
    try:
        from lifecycle_storage import secure_path
        existed = DATA.exists()
        DATA.mkdir(parents=True, exist_ok=True)
        if not existed:
            secure_path(DATA)
        backend = MockBackend() if mock else AppWindowBackend()
        guard = SwitchGuard([], 8)
        hub = FrameHub(backend, fps=20, max_width=900, quality=55)
        access_key = None if mock else load_or_create_access_key(DATA)
        state = AgentState(backend, DATA, guard=guard, hub=hub, mock=mock,
                           access_key=access_key, trusted_hosts=(host,) if host else ())
    except Exception:
        server.server_close()
        kernel.CloseHandle(parent_handle)
        raise
    class QuietHandler(make_handler(state)):
        def log_message(self, *_):
            pass
    server.RequestHandlerClass = QuietHandler
    shutdown_requested = threading.Event()
    def shutdown():
        if not shutdown_requested.is_set():
            shutdown_requested.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
    control = DesktopControl(state, DATA, port=server.server_address[1],
                             phone_url=f'https://{host}:8443/' if host else '', shutdown=shutdown,
                             owner_token=os.environ['CHATGPTREMOTE_OWNER_TOKEN'])
    def parent_closed():
        try:
            while not shutdown_requested.is_set():
                if kernel.WaitForSingleObject(parent_handle, 250) == 0:
                    shutdown()
                    return
        finally:
            kernel.CloseHandle(parent_handle)
    def follow_app_window():
        while not shutdown_requested.is_set():
            try:
                bind_app_window(state)
            except Exception:
                pass
            shutdown_requested.wait(.25)
    try:
        if not mock:
            bind_app_window(state)
            threading.Thread(target=follow_app_window, name='plugin-app-window', daemon=True).start()
        hub.start()
        guard.start()
        control.start()
        threading.Thread(target=parent_closed, name='plugin-parent', daemon=True).start()
        server.serve_forever(poll_interval=.1)
    finally:
        shutdown_requested.set()
        control.close()
        server.server_close()
        state.close()


if __name__ == '__main__':
    try:
        serve_connection()
    except Exception as error:
        from app_paths import RUNTIME
        from logging.handlers import RotatingFileHandler
        RUNTIME.mkdir(parents=True, exist_ok=True)
        log = logging.getLogger('remote.plugin')
        handler = RotatingFileHandler(RUNTIME/'connection.log', maxBytes=64000, backupCount=1, encoding='utf-8')
        log.addHandler(handler)
        log.error('connection_start_failed type=%s', type(error).__name__)
        raise SystemExit(1) from None
