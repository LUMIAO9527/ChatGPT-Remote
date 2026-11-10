"""ChatGPT Remote — Windows Agent with paired browser access.

An independent Windows connector paired with a phone browser. It streams the
ChatGPT/Codex window with PrintWindow, uses native input controls, and keeps
the phone paired while ChatGPT restarts or the account changes on Windows.
Identified desktop threads use read-only structured local history; accessibility
handles the native composer and compatible conversation surfaces. External
access uses the existing authenticated HTTPS reverse proxy.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import os
import secrets
import socket
import shutil
import subprocess
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from functools import lru_cache
from collections import deque
from urllib.parse import parse_qs, urlsplit, unquote, urlencode

from windows_control import InputError, TargetError, WindowTracker
from control_channel import ChannelClosed, WebSocket
from conversation import conversation_event, draft_snapshot, normalize_editor_text
from attachments import UploadStore, UploadError, MAX_FILE
from delivery import DeliveryStore
from frame_clock import FrameClock
from remote_policy import RemotePolicy, PolicyError

from app_paths import HERE, DATA
from app_version import APP_VERSION
VERSION = APP_VERSION
DEFAULT_MAX_WIDTH = 1280
DEFAULT_JPEG_QUALITY = 65
COMPRESSIBLE_TYPES = {"text/html", "text/css", "text/plain", "text/javascript",
                      "application/json", "application/javascript", "application/x-ndjson",
                      "image/svg+xml"}
DEFAULT_FPS = 8
PROFILES = {
    "lan": (8.0, 1280, 65),
    "remote": (20.0, 900, 55),
}
ACCESS_COOKIE = "hr_access"
ACCESS_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" type="image/svg+xml" href="/icon.svg?v=rm3">
<link rel="apple-touch-icon" href="/icon.png?v=rm3">
<title>ChatGPT Remote</title><style>
:root{color-scheme:light dark;--bg:#f7f8fa;--surface:#fff;--text:#202329;--muted:#7b808b;--line:#e8ebf1}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;padding:24px 16px;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}
main{width:min(100%,480px);margin:clamp(0px,5vh,48px) auto;padding:clamp(24px,6vw,40px);border:1px solid var(--line);border-radius:26px;background:var(--surface)}
.brand{display:flex;align-items:center;gap:11px;margin-bottom:36px;font-weight:650}.brand img{width:36px;height:36px}h1{font-size:28px;letter-spacing:-.04em;margin:0 0 18px}p{color:var(--muted);margin:0;line-height:1.7}
@media(prefers-color-scheme:dark){:root{--bg:#15171b;--surface:#202329;--text:#f2f4f8;--muted:#a0a6b2;--line:#323741}}
</style></head><body><main>
<div class="brand"><img src="/icon.svg?v=rm3" alt="">ChatGPT Remote</div>
<h1>连接电脑</h1><p>在电脑托盘中打开 ChatGPT Remote，扫描二维码或打开连接链接。</p>
</main></body></html>"""

PAIR_TTL_SECONDS = 60


def computer_name(data_dir: Path) -> str:
    try:
        value=json.loads((data_dir/'desktop_settings.json').read_text(encoding='utf-8')).get('computerName')
        if isinstance(value,str) and 1<=len(value.strip())<=60 and not any(ord(char)<32 for char in value):
            return value.strip()
    except (OSError,ValueError,AttributeError):
        pass
    return socket.gethostname()


@lru_cache(maxsize=9)
def website_icon(kind: str, theme: str = 'auto') -> bytes:
    """Use the same finished Rm artwork for the website and home-screen tile."""
    theme = theme if theme in ('auto', 'light', 'dark') else 'auto'
    if kind == 'svg':
        from desktop_icon import vector
        return vector(theme).encode('utf-8')
    icon = (HERE / 'desktop.ico').read_bytes()
    if kind == 'ico':
        return icon
    count = struct.unpack_from('<H', icon, 4)[0]
    entries = [struct.unpack_from('<4B2H2I', icon, 6 + i * 16) for i in range(count)]
    largest = max(entries, key=lambda entry: (entry[0] or 256) * (entry[1] or 256))
    png = icon[largest[7]:largest[7] + largest[6]]
    if png.startswith(b'\x89PNG\r\n\x1a\n'):
        return png
    from PIL import Image
    output = io.BytesIO()
    with Image.open(io.BytesIO(icon)) as image:
        image.save(output, format='PNG')
    return output.getvalue()


def resolve_profile(name: str, fps: float | None, max_width: int | None,
                    quality: int | None) -> tuple[float, int, int]:
    base = PROFILES.get(name, PROFILES["lan"])
    return (base[0] if fps is None else float(fps),
            base[1] if max_width is None else int(max_width),
            base[2] if quality is None else int(quality))


def normalize_hostname(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit("//" + raw if "://" not in raw else raw)
    return (parsed.hostname or raw).casefold().rstrip(".")


def tailscale_dns_name() -> str:
    """Return this machine's MagicDNS name when the Tailscale CLI is available."""
    try:
        command = shutil.which('tailscale')
        if not command:
            for base in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                         os.environ.get('ProgramW6432', r'C:\Program Files')):
                candidate = Path(base)/'Tailscale/tailscale.exe'
                if candidate.is_file():
                    command = str(candidate)
                    break
        if not command: return ''
        from app_paths import external_environment
        result = subprocess.run([command, "status", "--json"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=8,
                                env=external_environment(),
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout or "{}")
        name = str((data.get("Self") or {}).get("DNSName") or "")
        return name.rstrip(".").casefold()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def load_or_create_access_key(data_dir: Path) -> str:
    path = data_dir / "access_key.txt"
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 16:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(24)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    return key


class AccessGate:
    """Optional pre-auth layer for public HTTPS tunnels.

    The key is exchanged for an HttpOnly cookie by visiting ``/?k=<key>`` once.
    API clients may also send ``X-Access-Key``. This is an additional gate in
    front of the per-device pairing token, not a replacement for it.
    """

    def __init__(self, key: str | None = None) -> None:
        self.key = key or ""
        self.digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest() if self.key else ""

    @property
    def enabled(self) -> bool:
        return bool(self.digest)

    def matches(self, candidate: str) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                                   self.digest)

    def authorized(self, headers: Any) -> bool:
        if not self.enabled:
            return True
        header_key = headers.get("X-Access-Key", "")
        if header_key and self.matches(header_key):
            return True
        for chunk in (headers.get("Cookie", "") or "").split(";"):
            name, _, value = chunk.strip().partition("=")
            if name == ACCESS_COOKIE and hmac.compare_digest(value, self.digest):
                return True
        return False


class RemoteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class TokenStore:
    """Device tokens are stored as SHA-256 digests; raw tokens never persist."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(entries, dict):
                raise ValueError("invalid token file")
            self.entries = entries
        except FileNotFoundError:
            self.entries = {}

    def issue(self, name: str) -> str:
        raw = secrets.token_urlsafe(32)
        with self.lock:
            self.entries[hashlib.sha256(raw.encode()).hexdigest()] = {
                "name": name[:60], "created": int(time.time())
            }
            self._persist()
        return raw

    def valid(self, raw: str) -> bool:
        if not raw or len(raw) > 256:
            return False
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.lock:
            return any(hmac.compare_digest(digest, h) for h in self.entries)

    def revoke(self, raw: str) -> None:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.lock:
            self.entries.pop(digest, None)
            self._persist()

    def count(self) -> int:
        with self.lock:
            return len(self.entries)

    def _persist(self) -> None:
        atomic_write_json(self.path, self.entries)


class MockBackend:
    """Protocol/UI verification with a simulated ChatGPT window.

    It exercises the same interface as WindowsBackend but never captures or
    controls a real desktop window.
    """

    CHAT_TITLES = {'main': '模拟对话', 'other': '另一段对话'}

    def __init__(self) -> None:
        self.tracker = WindowTracker()
        self.mode = "window"
        self.window_id = 1
        self.visible = True
        self.last_action = "尚未接收操作"
        self.lock = threading.RLock()
        self.frozen_until = 0.0
        self.frozen_reason = ""
        self.last_capture_ms = 0
        self.last_capture_method = "mock"
        self.messages = [{"id": "m0", "role": "user", "text": "你好，请介绍一下这个工具。"},
                         {"id": "m1", "role": "assistant", "text": "手机可以直接阅读电脑当前对话，也可以发送消息。需要操作按钮时，切换到画面。"}]
        self.draft = ""
        self.generating = False
        self.queued_messages: list[dict] = []
        self.queue_paused = False
        self.navigation_expanded = True
        self.current_chat = 'main'
        self.chat_store = {'other': [{'id':'m0','role':'assistant','text':'这是另一个项目对话。'}]}
        self.option_menu = ''
        self.option_values = {'model': '示例模型 A', 'permission': '默认权限', 'effort': '标准'}
        self.option_revision = 0
        self.mock_attachments=[]

    def attach_files(self, paths, conversation_key: str, epoch: int) -> dict:
        with self.lock:
            if self.frozen_info_locked()['active'] or self.mode!='window' or epoch!=self.tracker.epoch or conversation_key!=f'mock-{epoch}':
                raise TargetError('窗口或对话已变化，文件尚未附加')
            self.mock_attachments.extend({'id':path.parent.name,'name':path.name,'kind':'image' if path.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.gif','.heic') else 'file'} for path in paths)
            return {'state':'attached', 'nativeAttachments': [{'id':path.parent.name,'name':path.name} for path in paths]}

    def remove_file(self, conversation_key: str, epoch: int, reference: dict, verification: dict | None = None) -> None:
        with self.lock:
            if self.mode != 'window' or epoch != self.tracker.epoch or conversation_key != f'mock-{epoch}':
                raise TargetError('附件所属对话已变化。', 'attachment_context_changed')
            if not reference:raise TargetError('暂时无法定位此附件。', 'attachment_unavailable')
            self.mock_attachments = [item for item in self.mock_attachments if item['id'] != reference.get('id')]

    def files_present(self, conversation_key: str, epoch: int, references: list[dict]) -> bool:
        return all(reference and any(item['id']==reference.get('id') for item in self.mock_attachments) for reference in references)

    def composer_options(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window' or not self.tracker.resolve(self._enumerate()):
                raise TargetError('请先选择电脑窗口')
            if self.option_menu:
                titles = {'model': ['示例模型 A','示例模型 B'], 'permission': ['默认权限','仅查看'],
                          'effort': ['标准','高'], 'tools': ['搜索','添加插件']}[self.option_menu]
                items = [{'id':f'choice-{i}', 'title':title, 'kind':'option', 'enabled':True,
                          'selected':title==self.option_values.get(self.option_menu)} for i,title in enumerate(titles)]
            else:
                items = [{'id':kind, 'kind':kind, 'title':title, 'enabled':True, 'selected':False}
                         for kind,title in [('model',self.option_values['model']),('permission',self.option_values['permission']),
                                            ('effort','思考强度：'+self.option_values['effort']),('tools','添加工具')]]
            return {'available':True,'epoch':self.tracker.epoch,'menu':bool(self.option_menu),
                    'key':f'options-{self.tracker.epoch}-{self.option_revision}', 'items':items,'category':self.option_menu}

    def composer_surface(self, key: str, target_id: str) -> dict[str, Any]:
        raise TargetError('此演示选项不提供局部画面')

    def navigation(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window':
                raise TargetError('请返回窗口模式后切换对话')
            items = [{'id':'project-demo','kind':'project','title':'演示项目','project':'demo',
                      'projectTitle':'演示项目','expanded':self.navigation_expanded},
                     {'id':'new-demo','kind':'new','title':'在 演示项目 中开始新聊天','project':'demo','projectTitle':'演示项目'},
                     {'id':'new-global','kind':'new','title':'新聊天','project':'','projectTitle':''}]
            if self.navigation_expanded:
                items += [{'id':name,'kind':'thread','title':title,'project':'demo','projectTitle':'演示项目',
                           'selected':name==self.current_chat}
                          for name,title in self.CHAT_TITLES.items()]
            return {'available':True,'epoch':self.tracker.epoch,
                    'source':'mock-window','key':'nav-mock','items':items,'notice':''}

    def queue(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window' or not self.tracker.resolve(self._enumerate()):
                raise TargetError('请先选择电脑窗口')
            items=[{**row,'actions':[{'id':row['id']+'|'+op,'operation':op,'enabled':True}
                    for op in ('steer','edit','delete')]} for row in self.queued_messages]
            actions=([{'id':'queue-resume','operation':'resume','enabled':True}] if self.queue_paused else [])
            if self.generating:actions.append({'id':'queue-stop','operation':'stop','enabled':True})
            key=hashlib.sha256(json.dumps([items,actions,self.current_chat,self.tracker.epoch,self.draft],sort_keys=True).encode()).hexdigest()[:24]
            return {'key':key,'epoch':self.tracker.epoch,'items':items,'actions':actions,
                    'paused':self.queue_paused,'hasDraft':bool(self.draft),'available':True}

    def read_draft(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != 'window' or not self.tracker.resolve(self._enumerate()):
                raise TargetError('请先选择电脑窗口')
            identity = f'mock-draft|{self.tracker.epoch}|{self.current_chat}'
            result = draft_snapshot({'ok':True,'identity':identity,'title':self.CHAT_TITLES.get(self.current_chat,'新对话'),'text':self.draft})
            result['conversationKey'] = f'mock-{self.tracker.epoch}'
            return {**result,'epoch':self.tracker.epoch}

    def _clear_draft(self, command: dict) -> None:
        snapshot = self.read_draft()
        if command.get('conversationKey') != snapshot['conversationKey'] or command.get('draftRevision') != snapshot['revision']:
            raise TargetError('电脑草稿或当前对话已经变化，请刷新草稿后重新选择', 'draft_changed')
        if not snapshot['canClear']:
            raise TargetError('草稿超过完整读取上限，请切换画面处理')
        self.draft = ''

    def read_conversation(self) -> dict[str, Any]:
        with self.lock:
            if self.mode != "window" or not self.tracker.resolve(self._enumerate()):
                raise TargetError("请先选择对话窗口")
            return {"available": True, "key": f"mock-{self.tracker.epoch}", "title": self.CHAT_TITLES.get(self.current_chat,'新对话'),
                    "messages": [dict(m) for m in self.messages], "epoch": self.tracker.epoch,
                    "status": self.tracker.status, "mode": self.mode, "frozen": self.frozen_info_locked(),
                    "composer": {"available": True, "hasDraft": bool(self.draft),
                                 **({'options':self.composer_options()} if not self.option_menu else {})},
                    "queued": [dict(item) for item in self.queued_messages],
                    "queue": self.queue(),
                    "loadedOnly": True, "truncated": False, "generating": self.generating, "notice": ""}

    # -- simulated windows -------------------------------------------------
    def _enumerate(self) -> list[dict[str, Any]]:
        if not self.visible:
            return []
        return [{"id": self.window_id, "title": "模拟 ChatGPT 窗口",
                 "exe_path": r"C:\Program Files\ChatGPT\ChatGPT.exe",
                 "class": "Chrome_WidgetWin_1", "exe": "ChatGPT.exe", "pid": 100}]

    def list_windows(self) -> list[dict[str, Any]]:
        with self.lock:
            windows = self._enumerate()
            if self.mode == "window":
                self.tracker.resolve(windows)
            return [{"id": w["id"], "title": w["title"], "exe": w["exe"],
                     "selected": w["id"] == self.tracker.current_id and self.mode == "window"}
                    for w in windows]

    def info(self) -> dict[str, Any]:
        with self.lock:
            return {"windows": self.list_windows(), "mode": self.mode,
                    "status": "desktop" if self.mode == "desktop" else self.tracker.status,
                    "epoch": self.tracker.epoch,
                    "frozen": self.frozen_info_locked(),
                    "capture": {"method": self.last_capture_method,
                                "last_ms": self.last_capture_ms}}

    def select(self, window_id: int) -> None:
        with self.lock:
            w = next((x for x in self._enumerate() if x["id"] == window_id), None)
            if w is None:
                raise InputError("不存在的模拟窗口")
            self.tracker.choose(w)
            self.mode = "window"

    def desktop(self) -> None:
        with self.lock:
            if self.mode != "desktop":
                self.mode = "desktop"
                self.tracker.epoch += 1

    def window_mode(self) -> None:
        with self.lock:
            if self.mode != "window":
                self.mode = "window"
                self.tracker.epoch += 1
            self.tracker.resolve(self._enumerate())

    def simulate_restart(self, window_id: int) -> None:
        with self.lock:
            self.visible = False
            self.list_windows()
            self.window_id = window_id
            self.visible = True
            self.list_windows()

    def simulate_ambiguous(self, extra_ids: list[int]) -> None:
        """Add duplicate-looking windows to check the 'never guess' rule."""
        with self.lock:
            windows = self._enumerate()
            for extra in extra_ids:
                windows.append({**windows[0], "id": extra})
            self.tracker.resolve(windows)

    # -- freeze ------------------------------------------------------------
    def freeze(self, seconds: float = 0.0, reason: str = "manual") -> None:
        with self.lock:
            self.frozen_until = (time.monotonic() + seconds) if seconds > 0 else float("inf")
            self.frozen_reason = reason or "manual"
            self.tracker.epoch += 1

    def unfreeze(self) -> None:
        with self.lock:
            self.frozen_until = 0.0
            self.frozen_reason = ""

    def frozen_info_locked(self) -> dict[str, Any]:
        active = self.frozen_until > 0 and (self.frozen_until == float("inf") or
                                            time.monotonic() < self.frozen_until)
        if self.frozen_until > 0 and not active and self.frozen_until != float("inf"):
            self.frozen_until = 0.0
            self.frozen_reason = ""
        until_ms = 0
        if active and self.frozen_until != float("inf"):
            until_ms = int((time.time() + max(0.0, self.frozen_until - time.monotonic())) * 1000)
        return {"active": bool(active), "until_ms": until_ms,
                "reason": self.frozen_reason if active else ""}

    def frozen_info(self) -> dict[str, Any]:
        with self.lock:
            return self.frozen_info_locked()

    # -- capture / input ---------------------------------------------------
    def capture_meta(self, max_width: int = DEFAULT_MAX_WIDTH,
                     quality: int = DEFAULT_JPEG_QUALITY) -> dict[str, Any]:
        import io

        from PIL import Image, ImageDraw

        with self.lock:
            if self.mode == "window" and not self.tracker.resolve(self._enumerate()):
                raise TargetError("请先选择模拟窗口")
            frozen = self.frozen_info_locked()
            img = Image.new("RGB", (1024, 620), "#f5f7fa")
            ctx = ImageDraw.Draw(img)
            ctx.rounded_rectangle((24, 24, 1000, 596), 18, fill="white",
                                  outline="#cdd6e2", width=3)
            ctx.text((55, 50), "MOCK SCREEN - NOT A REAL CHATGPT WINDOW", fill="#194770")
            ctx.text((55, 118), f"Mode: {self.mode} / Window: {self.window_id}", fill="#172d42")
            ctx.text((55, 172), self.last_action[:95], fill="#225a45")
            ctx.text((55, 210), f"Frozen: {frozen['active']} {frozen['reason']}", fill="#8a3b3b")
            ctx.rounded_rectangle((55, 505, 965, 558), 13, fill="#e9eff5")
            ctx.text((73, 522), "Commands appear here", fill="#657485")
            data = io.BytesIO()
            img.save(data, format="JPEG", quality=max(30, min(90, int(quality))))
            captured_ms = int(time.time() * 1000)
            self.last_capture_ms = captured_ms
            return {"jpeg": data.getvalue(), "epoch": self.tracker.epoch, "mode": self.mode,
                    "captured_ms": captured_ms, "method": "mock",
                    "width": img.width, "height": img.height,
                    "pointer_target": (self.window_id, (0, 0, img.width, img.height)) if self.mode == "window" else None}

    def capture(self) -> bytes:
        return self.capture_meta()["jpeg"]

    def action(self, command: dict[str, Any]) -> dict | None:
        with self.lock:
            if self.frozen_info_locked()["active"]:
                raise TargetError("输入已冻结，请先解冻")
            if self.mode == "window" and not self.tracker.resolve(self._enumerate()):
                raise TargetError("请先选择模拟窗口")
            if command.get("epoch") != self.tracker.epoch:
                raise TargetError("目标窗口已经变化，已阻止旧指令")
            kind = command.get("kind")
            if kind == 'queue':
                queue=self.queue()
                if command.get('queueKey')!=queue['key']:raise TargetError('队列已变化','queue_changed')
                target=command.get('targetId')
                if target=='queue-resume' and self.queue_paused:self.queue_paused=False
                elif target=='queue-stop' and self.generating:self.generating=False;self.queue_paused=True
                else:
                    matches=[(row,a) for row in queue['items'] for a in row['actions'] if a['id']==target]
                    if len(matches)!=1:raise TargetError('队列项已变化','queue_changed')
                    row,a=matches[0]
                    if a['operation']=='edit' and self.draft:raise TargetError('电脑输入框已有草稿','draft_exists')
                    self.queued_messages=[m for m in self.queued_messages if m['id']!=row['id']]
                    if a['operation']=='steer':
                        self.messages.append({'id':'steered-'+row['id'],'role':'user','text':row['text']})
                    elif a['operation']=='edit':self.draft=row['text']
                result={'queue':self.queue()}
                if target and target.endswith('|edit'):result['queueDraft']=self.read_draft()
                return result
            if kind == 'composer':
                snapshot=self.composer_options()
                if command.get('optionsKey') != snapshot['key']:
                    raise TargetError('对话选项已变化，请刷新')
                operation=command.get('operation')
                if operation=='dismiss' and snapshot['menu']:
                    self.option_menu=''
                elif operation=='invoke':
                    matches=[item for item in snapshot['items'] if item['id']==command.get('targetId') and item['enabled']]
                    if len(matches)!=1:
                        raise InputError('选项不存在')
                    if self.option_menu:
                        self.option_values[self.option_menu]=matches[0]['title'];self.option_menu=''
                    else:
                        self.option_menu=matches[0]['id']
                else:
                    raise InputError('选项操作无效')
                self.option_revision+=1
                return {'options':self.composer_options()}
            elif kind == 'draft':
                if (command.get('operation') != 'clear' or not isinstance(command.get('conversationKey'),str)
                        or not isinstance(command.get('draftRevision'),str) or not command['draftRevision']):
                    raise InputError('草稿处理参数无效')
                self._clear_draft(command)
            elif kind == 'navigate':
                if type(command.get('expanded',True)) is not bool:
                    raise InputError('展开状态无效')
                nav = self.navigation()
                if command.get('navigationKey') != nav['key']:
                    raise TargetError('项目与对话列表已变化，请刷新后重新选择')
                target = command.get('targetId')
                if not any(i['id']==target for i in nav['items']):
                    raise InputError('对话不存在')
                if target == 'project-demo':
                    self.navigation_expanded = command.get('expanded',True)
                elif target == self.current_chat:
                    return {'navigationUnchanged':True}
                else:
                    self.chat_store[self.current_chat] = self.messages
                    self.current_chat=target if target not in ('new-demo','new-global') else 'new-'+str(self.tracker.epoch)
                    self.messages = self.chat_store.get(self.current_chat, [])
                    self.tracker.epoch += 1
                return {'navigation':self.navigation()} if target == 'project-demo' else None
            elif kind in ("text", "message"):
                text = command.get("text", "")
                if not isinstance(text, str) or len(text) > 6000 or "\0" in text:
                    raise InputError("请输入不超过 6000 字符的文本")
                if type(command.get("submit", False)) is not bool:
                    raise InputError("submit 必须是布尔值")
                submit_method = command.get("submitMethod", "button")
                if submit_method not in ("button", "CTRL+ENTER", "ENTER"):
                    raise InputError("发送方式无效")
                if 'draftRevision' in command and (not isinstance(command['draftRevision'],str) or not command['draftRevision']
                        or not isinstance(command.get('conversationKey'),str)):
                    raise InputError('缺少已查看的草稿版本，请重新查看电脑草稿')
                if kind == "message" or 'draftRevision' in command:
                    if self.mode != "window" or command.get("conversationKey") != f"mock-{self.tracker.epoch}":
                        raise TargetError("电脑已切换对话，请等手机更新后再发送")
                    if not text.strip() and not command.get('attachmentsOnly'):
                        raise InputError("请输入消息")
                    same_draft = normalize_editor_text(self.draft) == normalize_editor_text(text)
                    if 'draftRevision' in command and not (command.get('submit') and same_draft):
                        self._clear_draft(command)
                    if self.draft and not (command.get('submit') and same_draft):
                        raise TargetError('电脑有不同草稿。', 'draft_exists')
                    if command.get("submit"):
                        if self.generating:
                            self.queued_messages.append({'id':f'q{len(self.queued_messages)}','text':text or ('附件 · '+str(len(self.mock_attachments))+' 个'),
                                **({'attachments':list(self.mock_attachments)} if self.mock_attachments else {})})
                        else:
                            self.messages.append({"id": f"m{len(self.messages)}", "role": "user", "text": text,
                                **({'attachments':list(self.mock_attachments)} if self.mock_attachments else {})})
                            self.messages.append({"id": f"m{len(self.messages)}", "role": "assistant", "text": f"模拟回复：已收到 {text}"})
                    else:
                        self.draft = text
                    if command.get('submit'):self.mock_attachments=[];self.draft=''
                self.last_action = f"text:{text[:60]}"
                if command.get("submit"):
                    self.last_action += " + " + ("BUTTON" if submit_method == "button" else submit_method)
                    return {'submission':{'state':'queued' if self.generating else 'sent'}}
            elif kind in ("tap", "right", "window-pointer"):
                try:
                    fx = float(command["x"])
                    fy = float(command["y"])
                except (KeyError, TypeError, ValueError):
                    raise InputError("点击坐标无效")
                if not (0 <= fx <= 1 and 0 <= fy <= 1):
                    raise InputError("坐标必须在 0~1 之间")
                operation = command['operation'] if kind == 'window-pointer' else kind
                self.last_action = f"{operation}:{fx},{fy}"
            elif kind == "scroll":
                try:
                    delta = int(command.get("delta", 0))
                except (TypeError, ValueError):
                    raise InputError("滚动量无效")
                if abs(delta) > 1200:
                    raise InputError("滚动量过大")
                self.last_action = f"scroll:{delta}"
            elif kind == "key":
                name = str(command.get("key", "")).upper()
                if name not in ("ENTER", "CTRL+ENTER", "ESC", "TAB", "BACKSPACE",
                                "CTRL+A", "CTRL+C", "CTRL+V"):
                    raise InputError("未授权的快捷键")
                self.last_action = f"key:{name}"
            else:
                raise InputError("不支持的操作")


def summarize_ms(values: Any) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0}
    return {"samples": len(ordered), "avg": round(sum(ordered) / len(ordered), 1),
            "max": round(ordered[-1], 1),
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1)}


class FrameHub:
    """Background capture with a sleep-free long-poll queue.

    Capturing only while a phone is polling keeps idle CPU near zero. New
    frames wake waiting requests immediately, so the client sees the desktop
    change without a fixed 650 ms polling delay.
    """

    def __init__(self, backend: Any, fps: float = DEFAULT_FPS,
                 max_width: int = DEFAULT_MAX_WIDTH,
                 quality: int = DEFAULT_JPEG_QUALITY) -> None:
        self.backend = backend
        self.interval = 1.0 / max(1.0, min(20.0, float(fps)))
        self.max_width = int(max_width)
        self.quality = int(quality)
        self.cond = threading.Condition()
        self.stop_event = threading.Event()
        self.wake_capture = FrameClock()
        self.thread: threading.Thread | None = None
        # Seed with wall-clock milliseconds so frame ids keep increasing even
        # if the Agent restarts while a phone still remembers its last id.
        self.frame_counter = int(time.time() * 1000)
        self.latest: dict[str, Any] | None = None
        self.pointer_frames: dict[int, tuple] = {}
        self.error: str | None = None
        self.last_poll = 0.0
        self.action_sequence = 0
        # Frame timing samples answer "where does the 20 fps ceiling come
        # from": capture, encode, or the wait between them.
        self.capture_ms: deque = deque(maxlen=120)
        self.encode_ms: deque = deque(maxlen=120)
        self.period_ms: deque = deque(maxlen=120)
        self.last_capture_started: float | None = None
        self.last_frame_at: float | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        if self.wake_capture.closed:self.wake_capture=FrameClock()
        self.thread = threading.Thread(target=self._run, name="frame-hub", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_capture.set()
        with self.cond:
            self.cond.notify_all()
        if self.thread:
            self.thread.join(timeout=2.5)
            if not self.thread.is_alive():
                self.thread = None
                self.wake_capture.close()
        else:self.wake_capture.close()

    def note_poll(self) -> None:
        with self.cond:
            self.last_poll = time.monotonic()

    def note_capture(self, capture_ms: float, period_ms: float | None) -> None:
        with self.cond:
            self.capture_ms.append(capture_ms)
            if period_ms is not None:
                self.period_ms.append(period_ms)

    def note_encode(self, encode_ms: float) -> None:
        with self.cond:
            self.encode_ms.append(encode_ms)

    def _publish(self, meta: dict[str, Any]) -> dict[str, Any]:
        with self.cond:
            self.frame_counter += 1
            frame = {"id": self.frame_counter, **meta, "published_at": time.monotonic()}
            self.latest = frame
            if frame.get('pointer_target') and frame.get('method') in ('printwindow', 'wgc', 'mock') and frame.get('mode') == 'window':
                self.pointer_frames[frame['id']] = (frame['epoch'], frame['published_at'], frame['pointer_target'])
                while len(self.pointer_frames) > 64:
                    self.pointer_frames.pop(next(iter(self.pointer_frames)))
            self.error = None
            self.last_frame_at = frame["published_at"]
            self.cond.notify_all()
            return frame

    def _publish_error(self, message: str) -> None:
        with self.cond:
            self.error = message
            self.cond.notify_all()

    def capture_now(self) -> dict[str, Any]:
        with self.cond:
            action_sequence = self.action_sequence
        try:
            meta = self.backend.capture_meta(self.max_width, self.quality)
            meta["action_sequence"] = action_sequence
        except TargetError as exc:
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 - surface to the phone as text
            message = f"抓取失败：{exc}"
        else:
            return self._publish(meta)
        self._publish_error(message)
        return {"error": message}

    def wait(self, after_id: int, timeout: float = 1.8) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                if self.error is not None:
                    return {"error": self.error}
                if (self.latest is not None and self.latest["id"] > after_id
                        and time.monotonic() - self.latest["published_at"] <= max(1.0, 2 * self.interval)):
                    return self.latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.cond.wait(remaining)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            with self.cond:
                polled = (time.monotonic() - self.last_poll) < 3.0
            if not polled:
                self.stop_event.wait(0.12)
                continue
            started = time.monotonic()
            period = None if self.last_capture_started is None else (started - self.last_capture_started) * 1000
            self.last_capture_started = started
            with self.cond:
                action_sequence = self.action_sequence
            try:
                meta = self.backend.capture_meta(self.max_width, self.quality)
                meta["action_sequence"] = action_sequence
                self._publish(meta)
                self.note_capture((time.monotonic() - started) * 1000, period)
            except TargetError as exc:
                self._publish_error(str(exc))
            except Exception as exc:  # noqa: BLE001 - keep streaming other errors
                self._publish_error(f"抓取失败：{exc}")
            interval = self.interval
            if getattr(self.backend, "mode", "window") == "desktop":
                interval = max(interval, 0.30)
            self.wake_capture.wait(max(0.01, interval - (time.monotonic() - started)))
            self.wake_capture.clear()


class FrameEncoder:
    """An ordered stream starts with a full image, then sends changed regions."""

    def __init__(self, stream_id: str = "") -> None:
        self.previous = None
        self.epoch: int | None = None
        self.stream_id = stream_id

    def encode(self, frame: dict[str, Any]) -> bytes | None:
        import io
        from PIL import Image, ImageChops

        image = frame.get('image')
        if image is None:image = Image.open(io.BytesIO(frame["jpeg"])).convert("RGB")
        full = (self.previous is None or self.previous.size != image.size
                or self.epoch != frame["epoch"])
        x = y = 0
        payload = frame["jpeg"]
        kind = "full"
        if not full:
            difference = ImageChops.difference(self.previous, image).convert("L")
            box = difference.point(lambda value: 255 if value > 8 else 0).getbbox()
            if box is None:
                return None
            left, top, right, bottom = box
            box = (max(0, left - 2), max(0, top - 2), min(image.width, right + 2),
                   min(image.height, bottom + 2))
            area = (box[2] - box[0]) * (box[3] - box[1])
            if area < image.width * image.height * 0.7:
                cropped = io.BytesIO()
                image.crop(box).save(cropped, format="PNG", compress_level=1)
                if cropped.tell() < len(payload):
                    x, y = box[:2]
                    payload = cropped.getvalue()
                    kind = "patch"
        if kind == "patch":
            self.previous.paste(image.crop(box), (x, y))
        else:
            self.previous = image.copy()
        self.epoch = frame["epoch"]
        meta = {"kind": kind, "frameId": frame["id"], "epoch": frame["epoch"],
                "capturedMs": frame["captured_ms"], "method": frame.get("method", ""),
                "width": image.width, "height": image.height, "x": x, "y": y,
                "actionSequence": frame.get("action_sequence", 0),
                "streamId": self.stream_id}
        return self.pack(meta, payload)

    @staticmethod
    def pack(meta: dict[str, Any], payload: bytes = b"") -> bytes:
        header = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        return struct.pack("!II", len(header), len(payload)) + header + payload


class StreamFlow:
    """Bound unacknowledged images so a slow phone cannot build a frame backlog."""

    def __init__(self, token: str, frame_interval: float = 1/12) -> None:
        self.owner = hashlib.sha256(token.encode()).hexdigest()
        self.pending: list[int] = []
        self.last_sent = 0
        self.last_ack = time.monotonic()
        self.sent_at: dict[int, float] = {}
        self.last_roundtrip_ms: float | None = None
        self.bytes_sent = 0
        self.pending_bytes: dict[int, int] = {}
        self.window_limit = 3
        self.frame_interval = max(.025, min(1, float(frame_interval)))
        self.cond = threading.Condition()

    def ready(self) -> bool:
        with self.cond:
            if len(self.pending) >= self.window_limit or sum(self.pending_bytes.values()) >= 256 * 1024:
                self.cond.wait(0.2)
            return len(self.pending) < self.window_limit and sum(self.pending_bytes.values()) < 256 * 1024

    def stalled(self) -> bool:
        with self.cond:
            return bool(self.pending and time.monotonic() - self.sent_at[self.pending[0]] > 8)

    def sent(self, frame_id: int, byte_count: int = 0) -> None:
        with self.cond:
            self.last_sent = frame_id
            self.pending.append(frame_id)
            self.sent_at[frame_id] = time.monotonic()
            self.bytes_sent += byte_count
            self.pending_bytes[frame_id] = byte_count

    def acknowledge(self, token: str, frame_id: int) -> None:
        if not hmac.compare_digest(self.owner, hashlib.sha256(token.encode()).hexdigest()):
            raise RemoteError("画面连接不属于此设备", 403)
        with self.cond:
            if frame_id > self.last_sent or frame_id < 0:
                raise RemoteError("画面确认编号无效")
            released = [value for value in self.pending if value <= frame_id]
            if released:
                now = time.monotonic()
                self.last_roundtrip_ms = round((now - self.sent_at[released[-1]]) * 1000, 1)
                # A fixed three-frame window caps FPS at 3 / RTT. Allow enough
                # credit for a high-RTT path, with an independent byte bound.
                self.window_limit = max(3, min(12, int(self.last_roundtrip_ms / (self.frame_interval*1000)) + 2))
                self.last_ack = now
            self.pending = [value for value in self.pending if value > frame_id]
            for value in released:
                self.sent_at.pop(value, None)
                self.pending_bytes.pop(value, None)
            self.cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self.cond:
            return {"unconfirmed_frames": len(self.pending), "image_ack_roundtrip_ms": self.last_roundtrip_ms,
                    "bytes_sent": self.bytes_sent, "window_limit": self.window_limit,
                    "unconfirmed_bytes": sum(self.pending_bytes.values())}


class SwitchGuard:
    """Experimental account-switch guard.

    Watches one or more files (for example a tool's auth/state file) and freezes
    remote input for a few seconds whenever their mtime/size changes. Freezing
    bumps the epoch so commands queued for the previous account are rejected.
    Paths are caller-supplied; a missing file never creates a switch signal.
    """

    def __init__(self, paths: list[str] | None = None, freeze_seconds: float = 8.0) -> None:
        self.paths = [Path(os.path.expanduser(p)) for p in (paths or [])]
        self.freeze_seconds = float(freeze_seconds)
        self.lock = threading.Lock()
        self.signatures: dict[str, tuple[int, int] | None] = {}
        self.last_change_ms = 0
        self.cond = threading.Condition()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.on_change: Callable[[Path], None] | None = None

    def start(self) -> None:
        if not self.paths or self.thread and self.thread.is_alive():
            return
        self.poll_once()  # baseline, never triggers
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="switch-guard", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.5)
            self.thread = None

    def poll_once(self) -> bool:
        changed = False
        with self.lock:
            for path in self.paths:
                try:
                    stat = path.stat()
                    signature: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    signature = None
                key = str(path)
                if key in self.signatures and self.signatures[key] != signature:
                    changed = True
                self.signatures[key] = signature
            if changed:
                self.last_change_ms = int(time.time() * 1000)
        if changed and self.on_change:
            for path in self.paths:
                self.on_change(path)
                break
        return changed

    def snapshot(self) -> dict[str, Any]:
        return {"active": bool(self.paths),
                "last_change_ms": self.last_change_ms,
                "paths": [str(p) for p in self.paths],
                "freeze_seconds": self.freeze_seconds}

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - a watcher must never kill the agent
                pass
            self.stop_event.wait(0.7)


class AgentState:
    def __init__(self, backend: Any, data_dir: Path, *, guard: SwitchGuard | None = None,
                 hub: FrameHub | None = None, mock: bool = False,
                 access_key: str | None = None,
                 trusted_hosts: tuple[str, ...] = ()) -> None:
        self.backend = backend
        self.mock = bool(mock) or isinstance(backend, MockBackend)
        self.data_dir = data_dir
        self.tokens = TokenStore(data_dir / "paired_devices.json")
        self.session_path = data_dir / "session.json"
        self.pin = f"{secrets.randbelow(1_000_000):06d}"
        self.pin_expires = time.monotonic() + PAIR_TTL_SECONDS
        self.pin_ticket = secrets.token_urlsafe(18)
        self.pin_ttl = PAIR_TTL_SECONDS
        self.computer_name_dir = data_dir
        self.failed: dict[str, list[float]] = {}
        self.streams: dict[str, StreamFlow] = {}
        self.controls = 0
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.action_lock = threading.RLock()
        self.maintenance_token = ''
        self.maintenance_expires = 0.0
        self.deliveries = DeliveryStore(self._perform_action, self.backend.read_conversation, data_dir/'delivery_receipts.json', self.backend.read_draft)
        self.guard = guard or SwitchGuard([])
        self.hub = hub or FrameHub(backend)
        self.access = AccessGate(access_key)
        self.policy = RemotePolicy(bool(access_key or trusted_hosts))
        upload_root=data_dir.parent.parent/'_wip/mobile-uploads' if data_dir.parent.resolve()==HERE else data_dir/'uploads'
        self.uploads=UploadStore(upload_root)
        self.uploads.start_cleanup()
        self.trusted_hosts = {normalize_hostname(h) for h in trusted_hosts
                              if normalize_hostname(h)}
        self._load_session()
        if not self.mock:
            def warm_reader():
                try:self.backend.read_conversation()
                except (TargetError,OSError):pass
            threading.Thread(target=warm_reader,name='conversation-warmup',daemon=True).start()

    # -- persistence -------------------------------------------------------
    def _load_session(self) -> None:
        try:
            payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        loader = getattr(self.backend.tracker, "load", None)
        if callable(loader):
            loader(payload.get("fingerprint"))

    def save_session(self) -> None:
        saver = getattr(self.backend.tracker, "saved_fingerprint", None)
        fingerprint = saver() if callable(saver) else None
        if not fingerprint:
            return
        try:
            atomic_write_json(self.session_path, {"fingerprint": fingerprint,
                                                  "saved_at_ms": int(time.time() * 1000)})
        except OSError:
            pass

    # -- pairing -----------------------------------------------------------
    def pair(self, ip: str, pin: str, name: str) -> str:
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.failed.get(ip, []) if now - t < 300]
            if len(attempts) >= 5:
                raise RemoteError("配对尝试次数过多，5 分钟后再试", 429)
            if now >= self.pin_expires:
                raise RemoteError("配对码已过期，请使用电脑端的新配对码", 403)
            if not self.pin:
                raise RemoteError("配对码已使用，请使用电脑端的新配对码", 403)
            valid_format = (isinstance(pin, str) and len(pin) == 6
                            and pin.isascii() and pin.isdigit())
            if not valid_format or not hmac.compare_digest(pin, self.pin):
                attempts.append(now)
                self.failed[ip] = attempts
                raise RemoteError("配对码不正确", 403)
            self.pin = ""  # one new device per startup; restart to enroll another
        return self.tokens.issue(name or "手机")

    def pair_ticket_valid(self, ticket: str) -> bool:
        with self.lock:
            return bool(self.pin and time.monotonic()<self.pin_expires and hmac.compare_digest(str(ticket),self.pin_ticket))

    # -- state -------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        data = self.backend.info()
        return {"ok": True, "version": VERSION, "mock": self.mock,
                "computerName": computer_name(self.computer_name_dir),
                'instance': self.deliveries.instance,
                "guard": self.guard.snapshot(),
                "paired_count": self.tokens.count(),
                "capabilities": {**self.policy.capabilities(),'composerCatalog':not self.mock},
                **data}

    def transport_info(self) -> dict[str, Any]:
        with self.lock:
            flows = list(self.streams.values())
        with self.hub.cond:
            capture = list(self.hub.capture_ms)
            encode = list(self.hub.encode_ms)
            period = list(self.hub.period_ms)
            last_frame_at = self.hub.last_frame_at
        return {"active_streams": len(flows), "active_controls": self.controls,
                "streams": [flow.snapshot() for flow in flows],
                "frame": {"capture_ms": summarize_ms(capture), "encode_ms": summarize_ms(encode),
                          "period_ms": summarize_ms(period),
                          "last_frame_ms": None if last_frame_at is None else round((time.monotonic() - last_frame_at) * 1000, 1)}}

    def freeze_for(self, seconds: float, reason: str) -> None:
        self.backend.freeze(seconds, reason)

    def perform_action(self, data: dict[str, Any], token: str = '') -> dict[str, Any]:
        with self.action_lock:
            if self.maintenance_active():raise RemoteError('电脑端正在维护，请稍后重新连接。')
            return self._accept_action(data,token)

    def maintenance_active(self):
        if self.maintenance_token and time.monotonic()>=self.maintenance_expires:self.maintenance_token=''
        return bool(self.maintenance_token)

    def _accept_action(self, data: dict[str, Any], token: str = '') -> dict[str, Any]:
        self.check_public_action(data)
        if data.get('messageId') is not None:
            if data.get('kind') != 'message' or data.get('submit') is not True or type(data.get('epoch')) is not int:
                raise RemoteError('发送任务参数无效')
            receipt = self.deliveries.submit(data, token)
            return {'ok': True, 'epoch': self.backend.tracker.epoch, 'delivery': receipt}
        return self._perform_action(data, token)

    def _perform_action(self, data: dict[str, Any], token: str = '') -> dict[str, Any]:
        with self.action_lock:
            if self.maintenance_active():raise RemoteError('电脑端正在维护，请稍后重新连接。')
            return self._perform_active_action(data,token)

    def check_public_action(self, data: dict) -> None:
        try:
            self.policy.allow_action(data, getattr(self.backend, "mode", "window"))
        except PolicyError as exc:
            raise RemoteError(str(exc), 403) from None

    def require_public_window(self) -> None:
        try:
            self.policy.require_window(getattr(self.backend, "mode", "window"))
        except PolicyError as exc:
            raise RemoteError(str(exc), 403) from None

    def require_public_frame(self, frame: dict[str, Any]) -> None:
        """Validate capture provenance and the live target immediately before send.

        The capture carries its own mode/method/epoch; checking only the live
        mode would permit a cached desktop image after returning to a window.
        Release the backend lock before encoding or writing to a slow socket.
        """
        if not self.policy.public:
            return
        with self.backend.lock:
            self.require_public_window()
            method = frame.get("method")
            safe_method = method in ("printwindow", "wgc") or (method == "mock" and self.mock)
            if frame.get("mode") != "window" or not safe_method:
                raise RemoteError("当前无法安全读取窗口画面，请回到对话或在电脑端检查窗口。", 403)
            if type(frame.get("epoch")) is not int or frame["epoch"] != self.backend.tracker.epoch:
                raise TargetError("目标窗口已经变化，正在同步新画面")

    def _perform_active_action(self, data: dict[str, Any], token: str = '') -> dict[str, Any]:
        self.check_public_action(data)
        if data.get("kind") not in ("tap", "right", "scroll", "text", "key", "message", "navigate", "draft", "composer", "queue", "window-pointer"):
            raise RemoteError("未授权的操作类型")
        if type(data.get("epoch")) is not int:
            raise RemoteError("缺少有效窗口版本")
        data=dict(data);data.pop('attachmentsOnly',None)
        started = time.monotonic()
        if data['kind'] == 'window-pointer':
            if (type(data.get('frameId')) is not int or data.get('operation') not in ('left', 'right') or
                    any(type(data.get(axis)) not in (int, float) or not 0 <= data[axis] <= 1 for axis in ('x', 'y'))):
                raise InputError('鼠标操作参数无效。')
            with self.hub.cond:
                frame = self.hub.pointer_frames.get(data['frameId'])
            if not frame or frame[0] != data['epoch'] or started - frame[1] > 3:
                raise TargetError('画面已更新，请在当前画面重试。', 'pointer_frame_changed')
            data['pointer_target'] = frame[2]
        if data.get('attachmentIds'):
            if data.get('kind')!='message' or data.get('submit') is not True:raise UploadError('附件只能随当前消息发送')
            with self.uploads.lock:
                attached=self.uploads.resolve(token,data['attachmentIds'],'attached')
                if any(item.get('conversationKey')!=data.get('conversationKey') or item.get('epoch')!=data['epoch'] for item in attached):
                    raise UploadError('附件所属对话已变化，请先检查电脑输入框')
                if not self.backend.files_present(data['conversationKey'],data['epoch'],[item.get('nativeAttachment') for item in attached]):
                    raise TargetError('电脑附件已变化，消息未发送。','attachment_changed')
                data['attachmentsOnly']=True
                outcome = self.backend.action(data)
                for item in attached:item['state']='submitted'
                self.uploads.save()
        else:
            if data.get('kind')=='queue' and self.deliveries.needs_observation():
                self.deliveries.observe(self.backend.read_conversation())
            outcome = self.backend.action(data)
        with self.hub.cond:
            self.hub.action_sequence += 1
            action_sequence = self.hub.action_sequence
        self.hub.wake_capture.set()
        return {"ok": True, "epoch": self.backend.tracker.epoch,
                "actionSequence": action_sequence,
                **({"options": outcome['options']} if isinstance(outcome, dict) and 'options' in outcome else {}),
                **({'catalog':outcome['catalog']} if isinstance(outcome,dict) and isinstance(outcome.get('catalog'),dict) else {}),
                **({'modelApply': outcome['modelApply']} if isinstance(outcome, dict) and 'modelApply' in outcome else {}),
                **({"navigation": outcome['navigation']} if isinstance(outcome, dict) and 'navigation' in outcome else {}),
                **({field:outcome[field] for field in ('queue','queueDraft') if field in outcome} if isinstance(outcome,dict) else {}),
                **({'navigationUnchanged':True} if isinstance(outcome,dict) and outcome.get('navigationUnchanged') else {}),
                **({"submission": outcome['submission']} if isinstance(outcome, dict) and isinstance(outcome.get('submission'), dict) else {}),
                "processingMs": round((time.monotonic() - started) * 1000, 1)}

    def attach_uploads(self, token: str, data: dict) -> dict:
        self.require_public_window()
        if type(data.get('epoch')) is not int or not isinstance(data.get('conversationKey'),str):raise UploadError('缺少当前对话信息')
        with self.uploads.lock:
            items=self.uploads.resolve(token,data.get('ids'))
            if any(item['state'] not in ('uploaded','attached','attaching','unknown') for item in items):raise UploadError('附件已使用，请重新选择文件')
            existing=any(item['state']!='uploaded' for item in items)
            if not existing:
                for item in items:item['state']='attaching'
                self.uploads.save()
        if existing:return self.upload_status(token,data)
        try:
            result=self.backend.attach_files([item['path'] for item in items],data['conversationKey'],data['epoch'])
            if result.get('state')!='attached':raise UploadError('附加结果未确认，请在画面中检查')
        except Exception as exc:
            before=getattr(exc,'attachment_before',None)
            if before:
                with self.uploads.lock:
                    for item in items:item.update(state='attaching',conversationKey=data['conversationKey'],epoch=data['epoch'],
                        nativeBefore=before,batchIds=list(data['ids']))
                    self.uploads.save()
                return {'ok':True,'state':'attaching','files':[self.uploads.public(item) for item in items]}
            for item in items:item.update(state='uploaded' if getattr(exc,'code','')=='attachment_not_started' else 'unknown',
                conversationKey=data['conversationKey'],epoch=data['epoch'])
            with self.uploads.lock:self.uploads.save()
            raise
        for item in items:
            item.update(state='attached',conversationKey=data['conversationKey'],epoch=data['epoch'])
        self.bind_uploads(items, result)
        with self.uploads.lock:self.uploads.save()
        return {'ok':True,'state':'attached','files':[self.uploads.public(item) for item in items]}

    @staticmethod
    def bind_uploads(items: list[dict], result: dict) -> None:
        bindings = result.get('nativeAttachments', [])
        if len(bindings) == len(items):
            for item, reference in zip(items, bindings):
                if isinstance(reference, dict) and isinstance(reference.get('id'), str):
                    item['nativeAttachment'] = reference

    def remove_upload(self, token: str, data: dict) -> dict:
        self.require_public_window()
        ids = data.get('ids')
        if not isinstance(ids, list) or len(ids) != 1:
            raise UploadError('请逐个移除附件。')
        with self.uploads.lock:
            if self.uploads.was_removed(token,ids[0]):return {'ok':True}
            item = self.uploads.resolve(token, ids)[0]
            if item['state'] in ('uploaded', 'failed'):
                self.uploads.discard(token, ids)
                return {'ok': True}
            if item['state'] == 'submitted':
                raise UploadError('该附件已发送。')
            if type(data.get('epoch')) is not int or not isinstance(data.get('conversationKey'), str):
                raise UploadError('缺少附件所属对话信息。')
            if item.get('conversationKey') != data['conversationKey']:
                raise TargetError('请返回附件所属对话后移除。', 'attachment_context_changed')
            if item['state'] in ('attaching', 'unknown') or item.get('epoch') != data['epoch']:
                self.upload_status(token, data)
            try:
                self.backend.remove_file(data['conversationKey'], data['epoch'], item.get('nativeAttachment'), item.get('removalProof'))
            except TargetError as exc:
                if getattr(exc, 'attachment_removal', None):
                    item.update(state='removing', removalProof=exc.attachment_removal)
                    self.uploads.save()
                raise
            self.uploads._remove(item)
            self.uploads.remember_removed(item)
            self.uploads.save()
            return {'ok': True}

    def upload_status(self, token: str, data: dict) -> dict:
        with self.uploads.lock:
            items=self.uploads.resolve(token,data.get('ids'))
            if all(item['state']=='uploaded' for item in items):return {'ok':True,'state':'uploaded'}
            if type(data.get('epoch')) is not int or not isinstance(data.get('conversationKey'),str):
                raise UploadError('缺少附件所属对话信息。')
            if any(item.get('conversationKey') not in (None,data.get('conversationKey')) for item in items):
                raise TargetError('附件属于另一个对话，请返回原对话继续发送','attachment_context_changed')
            if all(item['state']=='attached' for item in items):
                if any(item.get('epoch')!=data['epoch'] for item in items):
                    if not self.backend.files_present(data['conversationKey'],data['epoch'],[item.get('nativeAttachment') for item in items]):
                        raise TargetError('电脑附件已变化，请检查输入框。','attachment_changed')
                    for item in items:item['epoch']=data['epoch']
                    self.uploads.save()
                return {'ok':True,'state':'attached'}
            before=items[0].get('nativeBefore');batch=items[0].get('batchIds',[])
            if not before and any(item['state']=='unknown' for item in items):raise UploadError('无法读取电脑附件')
        result = self.backend.attached_files_ready(before,len(batch),data.get('epoch')) if before else None
        if result:
            with self.uploads.lock:
                bound = self.uploads.resolve(token,batch)
                for item in bound:item['state']='attached'
                if isinstance(result, dict):self.bind_uploads(bound, result)
                self.uploads.save()
            return {'ok':True,'state':'attached'}
        return {'ok':True,'state':'attaching'}

    def acknowledge_frame(self, token: str, data: dict[str, Any]) -> None:
        stream_id, frame_id = data.get("streamId"), data.get("frameId")
        if not isinstance(stream_id, str) or type(frame_id) is not int:
            raise RemoteError("画面确认参数无效")
        with self.lock:
            flow = self.streams.get(stream_id)
        if flow is None:
            raise RemoteError("画面连接已结束", 404)
        flow.acknowledge(token, frame_id)

    def conversation(self) -> dict[str, Any]:
        self.require_public_window()
        snapshot = self.backend.read_conversation()
        self.deliveries.observe(snapshot)
        revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        return {"ok": True, **snapshot, "revision": revision, 'instance': self.deliveries.instance}

    def close(self) -> None:
        self.deliveries.close()
        try:
            self.hub.stop()
        finally:
            self.uploads.close()
            self.guard.stop()
            close_backend = getattr(self.backend, "close", None)
            if callable(close_backend):
                close_backend()


def allowed_ip(remote_ip: str) -> bool:
    try:
        ip = ipaddress.ip_address(remote_ip)
        return ip.is_loopback or (
            isinstance(ip, ipaddress.IPv4Address)
            and any(ip in network for network in (
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
            ))
        )
    except ValueError:
        return False


def make_handler(state: AgentState):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ChatGPT-Remote/{VERSION}"
        protocol_version = "HTTP/1.1"
        timeout = 30

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.address_string()}] {fmt % args}")

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # A frame stream must not fill a terminal pipe or log access keys.
            path = urlsplit(self.path).path
            if str(code).startswith(("2", "3")) or path in ("/api/frame", "/api/stream"):
                return
            self.log_message("%s %s %s", self.command, path, code)

        # -- responses -----------------------------------------------------
        def send_bytes(self, data: bytes, code: int = 200,
                       content_type: str = "application/json; charset=utf-8",
                       extra_headers: dict[str, str] | None = None) -> None:
            headers = dict(extra_headers or {})
            body = data
            compressed = False
            if (code == 200 and len(data) >= 512
                    and "gzip" in self.headers.get("Accept-Encoding", "").lower()
                    and content_type.split(";")[0].strip() in COMPRESSIBLE_TYPES
                    and "Content-Encoding" not in headers):
                body = gzip.compress(data, 6)
                compressed = True
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", headers.get('Cache-Control', "no-store, private"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            keep_alive = code in (200, 304)
            self.send_header("Connection", "keep-alive" if keep_alive else "close")
            self.close_connection = not keep_alive
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; "
                             "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            vary = headers.get("Vary", "")
            if compressed:
                vary = (vary + ", " if vary else "") + "Accept-Encoding"
            if vary:
                self.send_header("Vary", vary)
            for key, value in headers.items():
                if key.lower() not in ('cache-control', 'content-encoding', 'vary'):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def send_empty(self, code: int = 204) -> None:
            self.send_response(code)
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "keep-alive")
            self.close_connection = False
            self.end_headers()

        def send_json(self, obj: dict[str, Any], code: int = 200) -> None:
            self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), code)

        def send_redirect(self, location: str,
                          extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Connection", "close")
            self.close_connection = True
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def reject(self, message: str, code: int = 400, error_code: str = "") -> None:
            self.send_json({"ok": False, "error": message, **({"code":error_code} if error_code else {})}, code)

        # -- auth / safety -------------------------------------------------
        def token(self) -> str:
            auth = self.headers.get("Authorization", "")
            return auth[7:] if auth.startswith("Bearer ") else ""

        def send_expired_pair(self) -> None:
            page=ACCESS_PAGE.replace('<h1>连接电脑</h1><p>在电脑托盘中打开 ChatGPT Remote，扫描二维码或打开连接链接。</p>',
                '<h1>二维码已过期</h1><p>请扫描电脑端更新后的二维码。</p>')
            self.send_bytes(page.encode('utf-8'),403,content_type='text/html; charset=utf-8')

        def authorized(self) -> bool:
            if state.tokens.valid(self.token()):
                return True
            self.reject("需要先配对或重新配对", 401, "pairing_required")
            return False

        def require_access(self) -> bool:
            gate = state.access
            if gate.authorized(self.headers):
                return True
            if self.command == "GET" and urlsplit(self.path).path == "/":
                query = parse_qs(urlsplit(self.path).query)
                candidate = query.get("k", [None])[0] or ""
                if candidate and gate.matches(candidate):
                    ticket=query.get('pair',[''])[0]
                    if ticket and not state.pair_ticket_valid(ticket):
                        self.send_expired_pair()
                        return False
                    self.send_redirect('/?'+urlencode({'pair':ticket}) if ticket else '/', {
                        "Set-Cookie": (f"{ACCESS_COOKIE}={gate.digest}; Path=/; "
                                       "HttpOnly; SameSite=Lax; Max-Age=2592000"),
                    })
                    return False
                self.send_bytes(ACCESS_PAGE.encode("utf-8"), 401,
                                content_type="text/html; charset=utf-8")
                return False
            self.reject("需要入口授权：请从电脑托盘扫描连接二维码或复制完整链接", 401, "access_required")
            return False

        def check_ip(self) -> bool:
            if not allowed_ip(self.client_address[0]):
                self.reject("只允许私有局域网或环回地址；外网访问请使用 Tailscale Funnel", 403)
                return False
            host_header = self.headers.get("Host", "")
            host = (urlsplit("//" + host_header).hostname or "").casefold().rstrip(".")
            trusted = host in state.trusted_hosts
            if not trusted and host not in ("localhost",) and not (host and allowed_ip(host)):
                self.reject("Host 不在允许列表；外网访问需用 --trusted-host 配置域名", 403)
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_parts = urlsplit(origin)
                if (origin_parts.scheme not in ("http", "https") or
                        origin_parts.netloc.casefold() != host_header.casefold()):
                    self.reject("拒绝跨站请求", 403)
                    return False
            return True

        def read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65_536:
                    raise RemoteError("请求数据长度非法")
                obj = json.loads(self.rfile.read(length))
                if not isinstance(obj, dict):
                    raise ValueError("object required")
                return obj
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                raise RemoteError("请求必须是 JSON 对象")

        # -- routes --------------------------------------------------------
        def do_GET(self) -> None:
            if not self.check_ip():
                return
            path = urlsplit(self.path).path
            icons = {'/icon.svg': ('svg', 'image/svg+xml'), '/icon.png': ('png', 'image/png'),
                     '/favicon.ico': ('ico', 'image/x-icon')}
            if path in icons:
                kind, content_type = icons[path]
                theme = parse_qs(urlsplit(self.path).query).get('theme', ['auto'])[0]
                self.send_bytes(website_icon(kind, theme), content_type=content_type,
                                extra_headers={'Cache-Control': 'private, max-age=3600'})
                return
            if path == '/computer-cover.svg':
                self.send_bytes((HERE / 'assets' / 'computer-cover.svg').read_bytes(),
                                content_type='image/svg+xml',
                                extra_headers={'Cache-Control': 'private, max-age=3600'})
                return
            if not self.require_access():
                return
            try:
                if path == "/":
                    ticket=parse_qs(urlsplit(self.path).query).get('pair',[''])[0]
                    if ticket and not state.pair_ticket_valid(ticket):
                        self.send_expired_pair()
                        return
                    page = (HERE / "web" / "index.html").read_bytes()
                    etag = '"' + hashlib.sha256(page).hexdigest()[:24] + '"'
                    headers = {"Cache-Control": "no-cache", "ETag": etag}
                    if self.headers.get("If-None-Match") == etag:
                        self.send_bytes(b"", code=304, content_type="text/html; charset=utf-8",
                                        extra_headers=headers)
                    else:
                        self.send_bytes(page, content_type="text/html; charset=utf-8",
                                        extra_headers=headers)
                elif path == "/api/health":
                    self.send_json({"ok": True, "name": "ChatGPT Remote",
                                    "version": VERSION, "mock": state.mock,
                                    "computerName": computer_name(state.computer_name_dir),
                                    "transport": state.transport_info()})
                elif path == "/api/windows":
                    if self.authorized():
                        self.send_json(state.info())
                elif path == "/api/frame":
                    if self.authorized():
                        self._frame()
                elif path == "/api/stream":
                    if self.authorized():
                        self._stream()
                elif path == "/api/control":
                    self._control()
                elif path == "/api/conversation":
                    if self.authorized():
                        self.send_json(state.conversation())
                elif path == "/api/delivery":
                    if self.authorized():
                        ident = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                        receipt = state.deliveries.get(self.token(), ident)
                        if receipt is None:
                            self.reject('发送记录不存在', 404)
                        else:
                            self.send_json({'ok': True, 'delivery': receipt})
                elif path == "/api/navigation":
                    if self.authorized():
                        self.send_json({"ok": True, **state.backend.navigation()})
                elif path == "/api/draft":
                    if self.authorized():
                        self.send_json({"ok": True, **state.backend.read_draft()})
                elif path == "/api/queue":
                    if self.authorized():
                        self.send_json({"ok": True, **state.backend.queue()})
                elif path == "/api/composer":
                    if self.authorized():
                        self.send_json({"ok": True, **state.backend.composer_options()})
                elif path == '/api/resource':
                    if self.authorized():
                        state.require_public_window()
                        query=parse_qs(urlsplit(self.path).query)
                        try:data,mime,name,etag=state.backend.message_resource(query.get('id',[''])[0],query.get('variant',['original'])[0])
                        except (ValueError,OSError):self.reject('原文件暂不可预览',404);return
                        headers={'Cache-Control':'private, max-age=60','ETag':'"'+etag+'"','Vary':'Authorization'}
                        if self.headers.get('If-None-Match')=='"'+etag+'"':self.send_bytes(b'',code=304,content_type=mime,extra_headers=headers)
                        else:self.send_bytes(data,content_type=mime,extra_headers=headers)
                elif path == "/api/composer/surface":
                    if self.authorized():
                        query = parse_qs(urlsplit(self.path).query)
                        surface = state.backend.composer_surface(query.get('key',[''])[0], query.get('target',[''])[0])
                        state.require_public_frame(surface)
                        if self.authorized():
                            self.send_json({"ok": True, **surface})
                elif path == "/api/conversation/stream":
                    if self.authorized():
                        self._conversation_stream()
                else:
                    self.reject("路由不存在", 404)
            except RemoteError as exc:
                self.reject(str(exc), exc.status)
            except TargetError as exc:
                self.reject(str(exc), 409, exc.code)
            except Exception as exc:  # noqa: BLE001 - return a phone-readable error
                self.reject(f"执行失败：{exc}", 500)

        def _control(self) -> None:
            if self.headers.get("Upgrade", "").casefold() != "websocket" or "upgrade" not in self.headers.get("Connection", "").casefold():
                self.reject("需要 WebSocket 控制连接", 400)
                return
            try:
                accept = WebSocket.accept_key(self.headers.get("Sec-WebSocket-Key", ""),
                                              self.headers.get("Sec-WebSocket-Version", ""))
            except ValueError:
                self.reject("控制连接握手无效", 400)
                return
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.close_connection = True
            channel = WebSocket(self.rfile, self.wfile)
            self.connection.settimeout(5)
            authenticated = False
            try:
                first = channel.receive()
                token = first.get("token", "")
                if first.get("type") != "auth" or not isinstance(token, str) or not state.tokens.valid(token):
                    channel.send({"type": "auth", "ok": False, "status": 401})
                    channel.close(1008)
                    return
                channel.send({"type": "auth", "ok": True})
                with state.lock:
                    state.controls += 1
                    authenticated = True
                self.connection.settimeout(30)
                while state.tokens.valid(token) and not state.hub.stop_event.is_set():
                    message = channel.receive()
                    ident = message.get("id")
                    if type(ident) is not int or not (0 <= ident < 2 ** 53):
                        channel.close(1008)
                        return
                    try:
                        if not state.tokens.valid(token):
                            raise RemoteError("需要重新配对", 401)
                        kind = message.get("type")
                        if kind == "action":
                            payload = message.get("payload")
                            if not isinstance(payload, dict):
                                raise RemoteError("操作数据无效")
                            result = state.perform_action(payload,token)
                        elif kind == "ack":
                            state.acknowledge_frame(token, message)
                            result = {"ok": True}
                        elif kind == "ping":
                            result = {"ok": True}
                        else:
                            raise RemoteError("控制消息无效")
                        channel.send({"type": "reply", "id": ident, "ok": True, "data": result})
                    except (RemoteError, TargetError, InputError, UploadError) as exc:
                        status = exc.status if isinstance(exc, RemoteError) else (409 if isinstance(exc, (TargetError,UploadError)) else 400)
                        channel.send({"type": "reply", "id": ident, "ok": False, "status": status, "error": str(exc),
                                      **({"code":exc.code} if isinstance(exc, TargetError) and exc.code else {})})
            except (ChannelClosed, OSError):
                pass
            finally:
                if authenticated:
                    with state.lock:
                        state.controls -= 1

        def _conversation_stream(self) -> None:
            first = state.conversation()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private, no-transform")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.connection.settimeout(5)
            previous = None
            current = first
            last_sent = 0.0
            try:
                while not state.hub.stop_event.is_set() and state.tokens.valid(self.token()):
                    state.require_public_window()
                    event = conversation_event(previous, current)
                    if event is not None:
                        previous = current
                    elif time.monotonic() - last_sent > 1.5:
                        event = {"kind": "idle"}
                    if event is not None:
                        packet = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                        self.wfile.write(f"{len(packet):X}\r\n".encode("ascii") + packet + b"\r\n")
                        self.wfile.flush()
                        last_sent = time.monotonic()
                    if state.hub.stop_event.wait(0.65):
                        break
                    current = state.conversation()
            except (RemoteError, TargetError) as exc:
                status = {"kind": "status", "error": str(exc)}
                if isinstance(exc, TargetError) and exc.code:
                    status["code"] = exc.code
                packet = (json.dumps(status, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    self.wfile.write(f"{len(packet):X}\r\n".encode("ascii") + packet + b"\r\n0\r\n\r\n")
                except OSError:
                    pass
            except OSError:
                pass

        def _stream(self) -> None:
            state.require_public_window()
            state.hub.note_poll()
            result = state.hub.wait(0, timeout=1.8)
            if result is not None and "error" in result:
                self.reject(result["error"], 409)
                return
            if not self.authorized():
                return
            state.require_public_window()
            if result is not None:
                state.require_public_frame(result)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store, private, no-transform")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.connection.settimeout(5)
            stream_id = secrets.token_urlsafe(12)
            flow = StreamFlow(self.token(), state.hub.interval)
            with state.lock:
                state.streams[stream_id] = flow
            encoder = FrameEncoder(stream_id)
            last_id = 0
            last_sent = 0.0
            try:
                while not state.hub.stop_event.is_set() and state.tokens.valid(self.token()):
                    state.require_public_window()
                    if not flow.ready():
                        if flow.stalled():
                            break
                        result = None
                        continue
                    if result is None:
                        state.hub.note_poll()
                        result = state.hub.wait(last_id, timeout=1.0)
                    if result is None:
                        packet = FrameEncoder.pack({"kind": "idle"})
                    elif "error" in result:
                        packet = FrameEncoder.pack({"kind": "status", "error": result["error"]})
                    else:
                        state.require_public_frame(result)
                        last_id = result["id"]
                        encode_started = time.monotonic()
                        packet = encoder.encode(result)
                        state.hub.note_encode((time.monotonic() - encode_started) * 1000)
                        if packet is not None:
                            flow.sent(last_id, len(packet))
                        if packet is None and time.monotonic() - last_sent >= 1.0:
                            packet = FrameEncoder.pack({"kind": "idle", "frameId": last_id, "epoch": result["epoch"]})
                    if packet is not None:
                        if not state.tokens.valid(self.token()):
                            break
                        state.require_public_window()
                        if result is not None and "error" not in result:
                            state.require_public_frame(result)
                        self.wfile.write(f"{len(packet):X}\r\n".encode("ascii") + packet + b"\r\n")
                        self.wfile.flush()
                        last_sent = time.monotonic()
                    if result is not None and "error" in result:
                        break
                    state.hub.note_poll()
                    result = state.hub.wait(last_id, timeout=1.0)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (RemoteError, TargetError) as exc:
                # Headers are already committed: terminate this stream using its
                # own framing, never append a second HTTP response or stale image.
                packet = FrameEncoder.pack({"kind": "status", "error": str(exc)})
                try:
                    self.wfile.write(f"{len(packet):X}\r\n".encode("ascii") + packet + b"\r\n0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError):
                # Closing the page cancels its stream. Nothing is queued for reconnection.
                pass
            finally:
                with state.lock:
                    state.streams.pop(stream_id, None)

        def _frame(self) -> None:
            state.require_public_window()
            query = parse_qs(urlsplit(self.path).query)
            raw_since = query.get("since", [None])[0]
            if raw_since is None or raw_since == "":
                result = state.hub.capture_now()
            else:
                try:
                    since = int(raw_since)
                except (TypeError, ValueError):
                    raise RemoteError("since 必须是整数")
                if since < 0:
                    raise RemoteError("since 不能为负数")
                state.hub.note_poll()
                result = state.hub.wait(since, timeout=1.8)
            if result is None:
                self.send_empty(204)
                return
            if "error" in result:
                self.reject(result["error"], 409)
                return
            if not self.authorized():
                return
            state.require_public_frame(result)
            self.send_bytes(result["jpeg"], content_type="image/jpeg", extra_headers={
                "X-Frame-Id": str(result["id"]),
                "X-Target-Epoch": str(result["epoch"]),
                "X-Captured-Ms": str(result["captured_ms"]),
                "X-Capture-Method": str(result.get("method", "")),
                "X-Action-Sequence": str(result.get("action_sequence", 0)),
            })

        def do_POST(self) -> None:
            if not self.check_ip():
                return
            if not self.require_access():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/pair":
                    data = self.read_json()
                    if not isinstance(data.get("pin"), str):
                        raise RemoteError("缺少配对码")
                    name = data.get("name", "手机")
                    if not isinstance(name, str):
                        raise RemoteError("设备名称无效")
                    token = state.pair(self.client_address[0], data["pin"], name)
                    self.send_json({"ok": True, "token": token})
                    return
                if not self.authorized():
                    return
                if path == '/api/uploads':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=MAX_FILE:raise UploadError('每个文件需大于 0 字节，且不超过 25 MB')
                    name=unquote(self.headers.get('X-File-Name',''),errors='strict')
                    self.connection.settimeout(120)
                    try:result=state.uploads.receive(self.token(),name,size,self.rfile)
                    finally:self.connection.settimeout(10)
                    self.send_json({'ok':True,'file':result})
                elif path == '/api/uploads/attach':
                    self.send_json(state.attach_uploads(self.token(),self.read_json()))
                elif path == '/api/uploads/status':
                    self.send_json(state.upload_status(self.token(),self.read_json()))
                elif path == '/api/uploads/discard':
                    state.uploads.discard(self.token(),self.read_json().get('ids'));self.send_json({'ok':True})
                elif path == '/api/uploads/remove':
                    self.send_json(state.remove_upload(self.token(),self.read_json()))
                elif path == "/api/stream/ack":
                    data = self.read_json()
                    state.acknowledge_frame(self.token(), data)
                    self.send_empty(204)
                elif path == "/api/select":
                    data = self.read_json()
                    if not isinstance(data.get("id"), int):
                        raise RemoteError("窗口 ID 无效")
                    state.backend.select(data["id"])
                    state.save_session()
                    self.send_json(state.info())
                elif path == "/api/desktop":
                    self.read_json()
                    if state.policy.public:
                        raise RemoteError("公网连接不提供 Windows 全桌面控制。", 403)
                    state.backend.desktop()
                    self.send_json(state.info())
                elif path == "/api/window":
                    self.read_json()
                    state.backend.window_mode()
                    state.save_session()
                    self.send_json(state.info())
                elif path == "/api/freeze":
                    data = self.read_json()
                    frozen = data.get("frozen")
                    if not isinstance(frozen, bool):
                        raise RemoteError("frozen 必须是布尔值")
                    if frozen:
                        state.backend.freeze(0.0, "manual")
                    else:
                        state.backend.unfreeze()
                    self.send_json({"ok": True,
                                    "frozen": state.backend.frozen_info(),
                                    "epoch": state.backend.tracker.epoch})
                elif path == "/api/delivery/resolve":
                    data = self.read_json()
                    self.send_json({'ok': True, 'delivery': state.deliveries.resolve(
                        self.token(), data.get('id'), data.get('instance', ''), data.get('recovery'))})
                elif path == "/api/action":
                    data = self.read_json()
                    self.send_json(state.perform_action(data,self.token()))
                elif path == "/api/unpair":
                    self.read_json()
                    state.tokens.revoke(self.token())
                    self.send_json({"ok": True})
                else:
                    self.reject("路由不存在", 404)
            except RemoteError as exc:
                self.reject(str(exc), exc.status)
            except TargetError as exc:
                self.reject(str(exc), 409, exc.code)
            except UploadError as exc:
                self.reject(str(exc),409,getattr(exc,'code',''))
            except InputError as exc:
                self.reject(str(exc), 400)
            except (KeyError, TypeError, ValueError) as exc:
                self.reject(str(exc) or "请求参数无效", 400)
            except Exception as exc:  # noqa: BLE001 - return a phone-readable error
                self.reject(f"执行失败：{exc}", 500)

    return Handler


def local_ipv4_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = item[4][0]
            if allowed_ip(ip) and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def main() -> None:
    parser = argparse.ArgumentParser(description="ChatGPT Remote Windows Agent")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1",
                        help="默认仅本机；启动批处理显式启用局域网绑定")
    parser.add_argument("--mock", action="store_true",
                        help="使用模拟画面验证协议，不控制 Windows")
    parser.add_argument("--window-keyword", action="append",
                        help="筛选窗口标题关键字，可重复（覆盖默认应用白名单）")
    parser.add_argument("--profile", choices=("lan", "remote"), default="lan",
                        help="lan=局域网默认；remote=外网画面（最高20fps/900px/质量55）")
    parser.add_argument("--fps", type=float, default=None,
                        help="覆盖抓取帧率上限")
    parser.add_argument("--max-width", type=int, default=None,
                        help="覆盖画面最大宽度")
    parser.add_argument("--jpeg-quality", type=int, default=None,
                        help="覆盖 JPEG 质量")
    parser.add_argument("--trusted-host", action="append", default=[],
                        help="受信反向代理域名（可重复）；auto 自动读取本机 Tailscale 域名")
    parser.add_argument("--access-key", default=None,
                        help="外网访问密钥；auto 表示生成并保存到 data/access_key.txt")
    parser.add_argument("--watch-file", action="append", default=[],
                        help="切换守卫：监视文件变化并在变化时冻结输入（可重复）")
    parser.add_argument("--freeze-seconds", type=float, default=8.0,
                        help="切换守卫触发后的冻结秒数（默认 8）")
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("port 超出范围")
    fps, max_width, quality = resolve_profile(
        args.profile, args.fps, args.max_width, args.jpeg_quality)
    if not (1 <= max_width <= 2560):
        parser.error("max-width 超出范围")
    if not (1 <= quality <= 90):
        parser.error("jpeg-quality 超出范围")
    if not (0.5 <= fps <= 20):
        parser.error("fps 超出范围")
    trusted_hosts: list[str] = []
    for raw in args.trusted_host:
        if raw.casefold() == "auto":
            name = tailscale_dns_name()
            if not name:
                parser.error("--trusted-host auto 失败：未找到已登录的 Tailscale")
            trusted_hosts.append(name)
        else:
            trusted_hosts.append(raw)
    access_key: str | None = None
    if args.access_key:
        if args.access_key.casefold() == "auto":
            access_key = load_or_create_access_key(DATA)
        else:
            access_key = args.access_key
    if not args.mock and (access_key or trusted_hosts) and args.window_keyword:
        parser.error("公网连接不接受自定义窗口关键字，请使用默认 ChatGPT 窗口范围")
    if args.mock:
        backend: Any = MockBackend()
    else:
        from windows_control import WindowsBackend
        backend = WindowsBackend(args.window_keyword)

    guard = SwitchGuard(args.watch_file, args.freeze_seconds)
    hub = FrameHub(backend, fps=fps, max_width=max_width, quality=quality)
    state = AgentState(backend, DATA, guard=guard, hub=hub, mock=args.mock,
                       access_key=access_key, trusted_hosts=tuple(trusted_hosts))
    if args.watch_file:
        guard.on_change = lambda path: (
            print(f"[guard] 检测到切换信号文件变化：{path}；冻结输入 {args.freeze_seconds:g} 秒"),
            state.freeze_for(args.freeze_seconds, f"切换信号：{path.name}"))
    hub.start()
    guard.start()
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(state))
    server.daemon_threads = True
    from desktop_control import DesktopControl
    phone_url = ''
    if state.trusted_hosts:
        phone_url = f'https://{sorted(state.trusted_hosts)[0]}:8443/'
    elif args.bind != '127.0.0.1':
        addresses = local_ipv4_addresses()
        if addresses:
            phone_url = f'http://{addresses[0]}:{args.port}/'
    desktop = DesktopControl(state, DATA, port=args.port, phone_url=phone_url)
    desktop.start()
    print(f"\n=== ChatGPT Remote v{VERSION} ===")
    print("电脑后台服务 · 手机浏览器远程访问")
    mode_text = "模拟后端（不会控制真实窗口）" if args.mock else "真实窗口控制"
    print(f"模式：{mode_text}；画面 {max_width}px / {fps:g} fps（{args.profile}）")
    print(f"本机访问: http://127.0.0.1:{args.port}/")
    lan_ips = local_ipv4_addresses()
    for ip in lan_ips:
        print(f"局域网访问: http://{ip}:{args.port}/")
    if not lan_ips:
        print("未发现局域网 IPv4 地址：局域网内手机将无法连接")
    if trusted_hosts:
        host = trusted_hosts[0]
        print(f"外网域名: {host}")
        print(f"外网入口需已配置: tailscale funnel --bg --https=8443 http://127.0.0.1:{args.port}")
        if access_key:
            print("外网入口已配置；请从托盘复制连接链接或扫描二维码（日志不输出访问密钥）")
            print("首次用该链接打开后，浏览器会记住访问权限；之后可直接打开同一域名")
        print("公网入口有风险：不用时请关闭 Agent，或执行 tailscale funnel --https=8443 off")
    elif access_key:
        print("访问密钥已启用；请用带 ?k=... 的链接访问")
    print(f"首次配对码: {state.pin}（15 分钟内使用，仅可配对一台新设备）")
    print(f"已配对设备: {state.tokens.count()} 台（令牌保存在 data/paired_devices.json）")
    if args.watch_file:
        print(f"切换守卫: 监视 {', '.join(args.watch_file)}，变化后冻结 {args.freeze_seconds:g} 秒")
    print("局域网使用明文 HTTP；外网仅建议经 Tailscale Funnel HTTPS + 访问密钥")
    print("禁止通过本工具输入密码、验证码、API Key 或支付信息")
    print("Ctrl+C 退出。关闭 Agent 后手机将无法连接。\n")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nAgent 已停止")
    finally:
        desktop.close()
        state.close()
        server.server_close()


if __name__ == "__main__":
    main()
