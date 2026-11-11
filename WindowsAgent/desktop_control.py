"""Local desktop management over an authenticated Windows named pipe.

The phone HTTP server does not expose these operations. The pipe key is sealed
for the current Windows user, and pairing secrets are returned only on demand.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import math
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

MAX_PACKET = 65536


class DesktopControlError(Exception):
    pass


def pipe_address(folder: Path) -> str:
    identity = str(folder.resolve()).casefold().encode()
    return r'\\.\pipe\ChatGPTRemote-' + hashlib.sha256(identity).hexdigest()[:24]


def _crypt(data: bytes, encrypt: bool) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise DesktopControlError('无法打开本机管理凭据')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def local_secret(folder: Path, *, create: bool = False) -> bytes:
    path = folder / 'desktop_control.dat'
    try:
        key = _crypt(path.read_bytes(), False)
    except FileNotFoundError:
        if not create:
            raise DesktopControlError('后台服务尚未启动') from None
        key = secrets.token_bytes(32)
        folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('.desktop-control-' + secrets.token_hex(6) + '.tmp')
        try:
            temporary.write_bytes(_crypt(key, True))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if len(key) != 32:
        raise DesktopControlError('本机管理凭据格式异常')
    return key


def _wire(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _mac(key: bytes, ident: str, body: dict) -> str:
    return hmac.new(key, ident.encode() + b'\0' + _wire(body), hashlib.sha256).hexdigest()


class DesktopClient:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def request(self, operation: str, payload: dict | None = None, *, timeout: float = 4) -> dict:
        key = local_secret(self.folder)
        ident = secrets.token_hex(16)
        body = {'operation': operation, 'payload': payload or {}, 'at': time.time()}
        request = {'id': ident, 'body': body, 'auth': _mac(key, ident, body)}
        try:
            with Client(pipe_address(self.folder), family='AF_PIPE') as connection:
                connection.send_bytes(_wire(request))
                if not connection.poll(timeout):
                    raise DesktopControlError('后台暂时没有响应')
                reply = json.loads(connection.recv_bytes(MAX_PACKET))
        except (OSError, EOFError, ValueError):
            raise DesktopControlError('后台服务尚未连接') from None
        if not isinstance(reply, dict):
            raise DesktopControlError('无法验证本机管理服务')
        result = reply.get('body')
        if (not isinstance(result, dict) or reply.get('id') != ident
                or not hmac.compare_digest(str(reply.get('auth', '')), _mac(key, ident, result))):
            raise DesktopControlError('无法验证本机管理服务')
        if not result.get('ok'):
            raise DesktopControlError(result.get('error') or '操作未完成')
        return result


class DesktopControl:
    def __init__(self, state: Any, folder: Path, *, port: int = 8765, phone_url: str = '', shutdown=None, owner_token: str = '') -> None:
        self.state, self.folder, self.port = state, folder, port
        self.phone_url = phone_url
        self.shutdown, self.owner_token = shutdown, owner_token
        self.key = local_secret(folder, create=True)
        self.listener = None
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(8)
        self.nonce_lock = threading.Lock()
        self.seen: dict[str, float] = {}

    def start(self) -> None:
        self.listener = Listener(pipe_address(self.folder), family='AF_PIPE')
        threading.Thread(target=self._accept, name='desktop-control', daemon=True).start()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                break
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._serve, args=(connection,), name='desktop-request', daemon=True).start()

    def _serve(self, connection) -> None:
        try:
            with connection:
                if not connection.poll(2):
                    return
                request = json.loads(connection.recv_bytes(MAX_PACKET))
                if not isinstance(request, dict):
                    return
                ident, body = request.get('id'), request.get('body')
                if not isinstance(ident, str) or len(ident) != 32 or not isinstance(body, dict):
                    return
                if not hmac.compare_digest(str(request.get('auth', '')), _mac(self.key, ident, body)):
                    return
                at = body.get('at')
                if not isinstance(at, (int, float)) or not math.isfinite(at) or abs(time.time() - at) > 30:
                    return
                with self.nonce_lock:
                    now = time.monotonic()
                    self.seen = {value: stamp for value, stamp in self.seen.items() if now - stamp < 60}
                    if ident in self.seen:
                        return
                    self.seen[ident] = now
                try:
                    payload = body.get('payload', {})
                    if not isinstance(payload, dict):
                        raise DesktopControlError('操作参数无效')
                    result = {'ok': True, **self.dispatch(body.get('operation'), payload)}
                except Exception as exc:
                    result = {'ok': False, 'error': str(exc) if isinstance(exc, DesktopControlError) else '操作未完成'}
                connection.send_bytes(_wire({'id': ident, 'body': result, 'auth': _mac(self.key, ident, result)}))
        except (OSError, EOFError, ValueError, TypeError):
            pass
        finally:
            self.slots.release()

    @staticmethod
    def device_id(digest: str) -> str:
        return hashlib.sha256(('device:' + digest).encode()).hexdigest()[:24]

    def devices(self) -> list[dict]:
        with self.state.tokens.lock:
            return [{'id': self.device_id(digest), 'name': entry.get('name', '手机'),
                     'created': entry.get('created', 0)} for digest, entry in self.state.tokens.entries.items()]

    def _access_link(self) -> str:
        url = self.phone_url or f'http://127.0.0.1:{self.port}/'
        key = self.state.access.key
        return url + ('?' + urlencode({'k': key}) if key else '')

    def dispatch(self, operation: str, payload: dict) -> dict:
        if operation in ('service.stop', 'service.close'):
            if self.shutdown is None:
                raise DesktopControlError('连接组件尚未切换到插件，请先完成更新。')
            if operation == 'service.close':
                valid = self.owner_token and hmac.compare_digest(self.owner_token, str(payload.get('owner', '')))
            else:
                valid = self.state.maintenance_active() and hmac.compare_digest(self.state.maintenance_token, str(payload.get('token', '')))
            if not valid:
                raise DesktopControlError('连接状态已改变，请重试。')
            # Let the authenticated reply leave this pipe before shutdown closes
            # the listener. The HTTP server exits through its normal cleanup.
            threading.Timer(.08, self.shutdown).start()
            return {}
        if operation == 'maintenance.prepare':
            with self.state.action_lock, self.state.deliveries.lock:
                # Recovered receipts only observe history. Their persisted proof
                # survives an update, while active sends must finish first.
                pending = (record for record in self.state.deliveries.records.values()
                           if record['state'] in ('sending', 'confirming', 'unknown'))
                if any(not (record['state'] == 'confirming' and record.get('restarted')
                            and record.get('recovery') and 'command' not in record)
                       for record in pending):
                    raise DesktopControlError('还有消息正在发送或等待确认，请确认发送结果后重试。')
                if self.state.maintenance_active():raise DesktopControlError('另一个维护操作正在进行。')
                self.state.maintenance_token=secrets.token_hex(16)
                self.state.maintenance_expires=time.monotonic()+120
                return {'token':self.state.maintenance_token}
        if operation == 'maintenance.cancel':
            with self.state.action_lock:
                if not hmac.compare_digest(self.state.maintenance_token,str(payload.get('token',''))):
                    raise DesktopControlError('维护操作已改变，请重试。')
                self.state.maintenance_token=''
                return {}
        if operation == 'status':
            info = self.state.info()
            selected = next((window for window in info.get('windows', []) if window.get('selected')), None)
            return {'version': info['version'], 'running': True, 'phoneUrl': self.phone_url,
                    'managedBy': 'plugin' if self.shutdown else 'desktop', 'localPort': self.port,
                    'windows': info.get('windows', []), 'target': selected,
                    'targetStatus': info.get('status', ''), 'mode': info.get('mode', ''),
                    'paused': bool(info.get('frozen', {}).get('active')), 'devices': self.devices()}
        if operation == 'pair':
            with self.state.lock:
                if payload.get('renew') or not self.state.pin or time.monotonic() >= self.state.pin_expires:
                    self.state.pin = f'{secrets.randbelow(1_000_000):06d}'
                    self.state.pin_expires = time.monotonic() + self.state.pin_ttl
                    self.state.pin_ticket = secrets.token_urlsafe(18)
                result = {'pin': self.state.pin, 'expiresAt': time.time() + self.state.pin_expires - time.monotonic(),
                          'url': self._access_link()+('&' if '?' in self._access_link() else '?')+urlencode({'pair':self.state.pin_ticket})}
            if payload.get('renderQr'):
                import base64
                import io
                import qrcode
                image = qrcode.make(result['url'], border=3, box_size=7)
                output = io.BytesIO()
                image.save(output, format='PNG')
                result['qr'] = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
            return result
        if operation == 'access_link':
            return {'url': self._access_link()}
        if operation in ('device.rename', 'device.remove'):
            ident = payload.get('id')
            name = payload.get('name', '')
            if operation == 'device.rename' and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 60
                                                or any(ord(c) < 32 for c in name)):
                raise DesktopControlError('设备名称需为 1–60 个字符')
            with self.state.tokens.lock:
                matches = [digest for digest in self.state.tokens.entries if self.device_id(digest) == ident]
                if len(matches) != 1:
                    raise DesktopControlError('设备已移除')
                if operation == 'device.remove':
                    del self.state.tokens.entries[matches[0]]
                else:
                    self.state.tokens.entries[matches[0]]['name'] = name.strip()
                self.state.tokens._persist()
            return {'devices': self.devices()}
        if operation == 'target.select':
            ident = payload.get('id')
            if type(ident) is not int:
                raise DesktopControlError('请选择一个窗口')
            self.state.backend.select(ident)
            self.state.save_session()
            return self.dispatch('status', {})
        if operation == 'target.pause':
            if type(payload.get('paused')) is not bool:
                raise DesktopControlError('控制状态无效')
            if payload['paused']:
                self.state.backend.freeze(0.0, 'manual')
            else:
                self.state.backend.unfreeze()
            return self.dispatch('status', {})
        raise DesktopControlError('不支持的管理操作')

    def close(self) -> None:
        self.stopped.set()
        if self.listener:
            self.listener.close()
