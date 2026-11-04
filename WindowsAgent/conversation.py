"""Read only the selected window's loaded conversation through Windows UIA.

The UIA provider lives in an isolated, restartable process so a blocked app
cannot stop capture or the HTTP server. Conversation text is never saved.
"""
from __future__ import annotations

import ctypes
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any

from windows_control import TargetError

ROLE_MARKERS = {"你说：": "user", "你说:": "user", "You said:": "user",
                "ChatGPT 说：": "assistant", "ChatGPT 说:": "assistant",
                "ChatGPT said:": "assistant", "用户：": "user", "助手：": "assistant"}

INFO_OPTION = re.compile(r"^(了解更多|了解详情|查看更多说明|Learn more|More info)(?:[\s….。!！?？:].*)?$", re.I)
PERMISSION_INFO = re.compile(r"^(Help|帮助|Documentation|文档|Feedback|反馈)(?:[\s….。!！?？:].*)?$", re.I)
PERMISSION_LABELS = ('自定义 (config.toml)', '完全访问权限', '请求批准', '帮我批准', '工作区内修改', '只读',
                     'Custom (config.toml)', 'Full access', 'Request approval', 'Default permissions', 'Read only')


def option_labels(title: str, category: str) -> dict:
    if category == 'permission':
        for label in PERMISSION_LABELS:
            if title == label or (title.startswith(label) and title[len(label):len(label)+1].isspace()):
                return {'label':label,'description':title[len(label):].strip()}
    return {'label':title,'description':''}


def draft_snapshot(raw: dict) -> dict:
    text = raw.get("text")
    if not raw.get("ok") or not isinstance(text, str):
        raise TargetError("暂时无法读取电脑草稿，请稍后刷新")
    identity = str(raw.get("identity") or "")
    truncated = bool(raw.get("truncated")) or len(text) > 32000
    return {"conversationKey": hashlib.sha256(identity.encode()).hexdigest()[:24],
            "revision": hashlib.sha256((identity + "\0" + text).encode()).hexdigest(),
            "title": str(raw.get("title") or "当前对话"), "text": text[:32000],
            "hasDraft": bool(text), "truncated": truncated, "canClear": not truncated}


def parse_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    blocks: list[dict[str, str]] = []
    previous_rect: dict | None = None
    role = ""
    timestamp = ""
    phase = "answer"
    queued: dict[str, dict] = {}
    attachments: list[dict] = []
    completed = False
    duration = ''
    pending_duration = ''
    source_id = ''

    def finish(ended: bool = False) -> None:
        if role:
            text = "\n\n".join(("• " if block["kind"] == "list" else "") + block["text"] for block in blocks).strip()
            if text or attachments or duration:
                used, visible = 0, []
                for block in blocks:
                    if used >= 40000:
                        break
                    visible.append({**block, "text": block["text"][:40000-used]})
                    used += len(block["text"])
                messages.append({"id": f"m{len(messages)}", "role": role, "text": text[:40000], "blocks": visible,
                                 **({'sourceId':source_id} if source_id else {}),
                                 "complete": role == "user" or ended or completed or not bool(raw.get("generating")),
                                 **({"attachments": list(attachments)} if attachments else {}),
                                 **({'duration':duration} if duration else {}),
                                 **({"timestamp": timestamp} if timestamp else {}),
                                 **({"phase": phase} if phase != "answer" else {})})

    for node in raw.get("nodes", []):
        name = str(node.get("name") or "")
        marker = ROLE_MARKERS.get(name.strip()) if node.get("marker") else None
        if marker:
            finish(ended=True)
            role, blocks, previous_rect, timestamp = marker, [], None, ""
            source_id = str(node.get('sourceId') or '')[:160]
            attachments, completed = [], False
            duration = pending_duration if marker == 'assistant' else ''
            if marker == 'assistant':pending_duration = ''
            phase = node.get("channel") if marker == "assistant" and node.get("channel") in ("activity", "reasoning") else "answer"
        elif node.get('duration'):
            if role == 'assistant':duration = name[:100]
            else:pending_duration = name[:100]
        elif role and node.get("type") == "Status" and node.get("complete"):
            completed = True
        elif role and node.get("type") == "Attachment":
            kind = "image" if node.get("kind") == "image" else "file"
            item = {"id": str(node.get("id") or f"a{len(attachments)}")[:160], "kind": kind,
                    **({"name": str(node["name"])[:180]} if kind == "file" and node.get("name") else {})}
            if len(attachments) < 20 and not any(a["id"] == item["id"] for a in attachments):
                attachments.append(item)
        elif node.get("queued") and node.get("type") in ("Text", "Hyperlink", "ListItem"):
            if name.strip():
                queue_id = str(node.get("queueId") or "pending")[:160]
                entry = queued.setdefault(queue_id, {"id": queue_id, "text": ""})
                entry["text"] = (entry["text"] + ("\n" if entry["text"] else "") + name)[:6000]
        elif role and node.get("timestamp"):
            timestamp = name.strip()
        elif node.get("type") == "Edit" and role:
            break  # The composer and its draft are not conversation messages.
        elif role and node.get("type") in ("Text", "Hyperlink", "Header", "ListItem"):
            if node.get("type") == "ListItem" and node.get("leaf") is False and not node.get("complete"):
                continue
            if name.strip() and name.strip() not in ("最新一条回复", "Latest reply"):
                kind = "list" if node.get("type") == "ListItem" else (
                    "heading" if node.get("type") == "Header" else ("code" if node.get("code") else "paragraph"))
                channel = node.get("channel") if role == "assistant" and node.get("channel") in ("activity", "reasoning") else phase
                rect = node.get("rect")
                inline = (blocks and kind in ("paragraph", "code") and blocks[-1]["kind"] == kind
                          and blocks[-1].get("channel", "answer") == channel
                          and rect and previous_rect and rect["top"] <= previous_rect["bottom"] + 3
                          and rect["bottom"] >= previous_rect["top"] - 3)
                if inline:
                    blocks[-1]["text"] += name
                    previous_rect = {"top": min(previous_rect["top"], rect["top"]),
                                     "bottom": max(previous_rect["bottom"], rect["bottom"])}
                else:
                    if blocks and kind == "code" and blocks[-1]["kind"] == "code" and blocks[-1].get("channel", "answer") == channel:
                        blocks[-1]["text"] += "\n" + name
                    else:
                        blocks.append({"kind": kind, "text": name, **({"channel": channel} if channel != "answer" else {})})
                    previous_rect = rect
    finish()
    if pending_duration:
        messages.append({'id':f'm{len(messages)}','role':'assistant','text':'','blocks':[],
                         'phase':'activity','duration':pending_duration,'complete':True})
    truncated = bool(raw.get("truncated")) or len(messages) > 80
    messages = messages[-80:]
    budget = 160000
    kept = []
    for message in reversed(messages):
        if len(message["text"]) > budget:
            truncated = True
            break
        kept.append(message)
        budget -= len(message["text"])
    messages = list(reversed(kept))
    composer = raw.get("composer", {"available": False, "hasDraft": False})
    unstructured = not messages and any(str(node.get("name") or "").strip() in ROLE_MARKERS for node in raw.get("nodes", []))
    available = bool(messages or composer.get("available")) and not unstructured
    identity = str(raw.get("identity") or "")
    return {"available": available, "title": str(raw.get("title") or ""),
            "key": hashlib.sha256(identity.encode()).hexdigest()[:24],
            "messages": messages, "composer": composer,
            "queued": list(queued.values())[:20],
            "generating": bool(raw.get("generating")), "truncated": truncated,
            "loadedOnly": True,
            "notice": "" if available else "当前页面尚未提供可读取的对话，请切换画面操作。"}


def conversation_event(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
    """Send changed messages, retaining the order and removing unloaded rows."""
    if previous and previous.get("revision") == current.get("revision"):
        return None
    replace = not previous or any(previous.get(key) != current.get(key) for key in ("key", "epoch"))
    before = {} if replace else {m["id"]: m for m in previous.get("messages", [])}
    changed = [m for m in current.get("messages", []) if before.get(m["id"]) != m]
    return {"kind": "conversation", **{k: v for k, v in current.items() if k != "messages"},
            "replace": replace, "messages": changed,
            "order": [m["id"] for m in current.get("messages", [])]}


class ConversationReader:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.replies: queue.Queue = queue.Queue()
        self.sequence = 0
        self.cached: tuple[int, float, dict, str] | None = None
        self.accessible_hwnd = 0
        self.catalogs: dict[str, tuple[float, int, str, list[dict]]] = {}
        self.option_catalogs: dict[str, tuple[float, int, dict]] = {}
        self.prepared_generating = False

    def composer_options(self, hwnd: int) -> dict:
        with self.lock:
            raw = self._call(operation="composer", hwnd=hwnd)
            return self._remember_options(hwnd, raw)

    def _remember_options(self, hwnd: int, raw: dict) -> dict:
        if not raw.get("ok"):
            raise TargetError("暂时无法读取输入框选项，请刷新或切换画面")
        items = []
        for item in raw.get("items", [])[:200]:
            if not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
                continue
            if INFO_OPTION.fullmatch(item['title'].strip()) or (raw.get('category')=='permission' and PERMISSION_INFO.fullmatch(item['title'].strip())):
                continue
            items.append({"id": item["id"], "title": item["title"][:500],
                          "kind": str(item.get("kind") or "option"),
                          "enabled": bool(item.get("enabled")), "selected": bool(item.get("selected")),
                          **option_labels(item['title'][:500],str(raw.get('category') or ''))})
        snapshot = {"items": items, "menu": bool(raw.get("menu")), "context": str(raw.get("context") or ""),
                    "identity": str(raw.get("identity") or ""),"category":str(raw.get('category') or '')}
        key = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:24]
        self.option_catalogs[key] = (time.monotonic(), hwnd, snapshot)
        while len(self.option_catalogs) > 8:
            self.option_catalogs.pop(next(iter(self.option_catalogs)))
        return {"key": key, "items": items, "menu": snapshot["menu"], "available": bool(items),"category":snapshot['category']}

    def composer_action(self, hwnd: int, key: str, target: str | None, dismiss: bool = False, refresh: bool = False) -> bool | dict:
        """Invoke one observed control, or validate an open menu before Escape."""
        with self.lock:
            reference = self.option_catalogs.get(key)
            if not reference or reference[1] != hwnd or time.monotonic() - reference[0] > 120:
                raise TargetError("输入框选项已变化，请刷新后重新选择")
            snapshot = reference[2]
            matches = [item for item in snapshot["items"] if item["id"] == target and item["enabled"]]
            if (dismiss and not snapshot["menu"]) or (not dismiss and len(matches) != 1):
                raise TargetError("选项已变化或不可用，请刷新后重新选择")
            # Consume before invocation. An uncertain action is never repeated.
            self.option_catalogs.pop(key, None)
            try:
                reply = self._call(operation="composer_dismiss" if dismiss else "composer_invoke", hwnd=hwnd,
                                   identity=snapshot["identity"], context=snapshot["context"],
                                   item=matches[0] if not dismiss else None, refresh=refresh)
            except TargetError:
                raise TargetError("选项操作结果未确认，请刷新查看；没有自动重试") from None
            finally:
                self.cached = None
            if not reply.get("ok"):
                raise TargetError("电脑选项或当前对话已变化，请刷新后重新选择")
            if refresh:
                return {"escape": bool(dismiss and reply.get("escape")),
                        **({"options": self._remember_options(hwnd, reply["next"])} if isinstance(reply.get("next"), dict) else {})}
            return bool(dismiss and reply.get("escape"))

    def composer_surface(self, hwnd: int, key: str, target: str, consume: bool = False) -> dict:
        with self.lock:
            reference = self.option_catalogs.get(key)
            if not reference or reference[1] != hwnd or time.monotonic() - reference[0] > 120:
                raise TargetError("输入框选项已变化，请刷新后重新选择")
            snapshot = reference[2]
            matches = [item for item in snapshot['items'] if item['id'] == target and item['kind'] == 'surface' and item['enabled']]
            if len(matches) != 1 or not snapshot['menu']:
                raise TargetError("当前选项不支持局部操作，请刷新后重新选择")
            if consume:
                self.option_catalogs.pop(key, None)
            reply = self._call(operation='composer_surface', hwnd=hwnd, identity=snapshot['identity'],
                               context=snapshot['context'], item=matches[0])
            if not reply.get('ok'):
                raise TargetError("电脑菜单或当前对话已变化，请刷新后重新选择")
            return reply

    def draft(self, hwnd: int) -> dict:
        with self.lock:
            return draft_snapshot(self._call(operation="draft", hwnd=hwnd))

    def clear_draft(self, hwnd: int, key: str, revision: str, for_send: bool = False) -> None:
        with self.lock:
            raw = self._call(operation="draft", hwnd=hwnd)
            snapshot = draft_snapshot(raw)
            if snapshot["conversationKey"] != key or snapshot["revision"] != revision:
                raise TargetError("电脑草稿或当前对话已经变化，请刷新草稿后重新选择", "draft_changed")
            if not snapshot["canClear"]:
                raise TargetError("草稿超过完整读取上限，请切换画面处理")
            try:
                reply = self._call(operation="clear_draft", hwnd=hwnd, identity=raw["identity"],
                                   text=raw["text"], forSend=for_send)
            except TargetError:
                raise TargetError("清空结果未确认，请刷新草稿查看；手机上的文字已保留") from None
            finally:
                self.cached = None
            if not reply.get("ok"):
                reasons = {"draft changed": "电脑草稿已变化，请刷新草稿后重新选择",
                           "conversation changed": "电脑已切换对话，请重新查看草稿",
                           "reply in progress": "电脑仍在回复，请完成后再清空并发送",
                           "clear unavailable": "当前输入框暂不支持直接清空，请切换画面处理",
                           "clear not confirmed": "清空结果未确认，请刷新草稿查看；手机上的文字已保留"}
                raise TargetError(reasons.get(reply.get("reason"), "清空结果未确认，请刷新草稿查看"), "draft_changed")

    def navigation(self, hwnd: int) -> dict:
        with self.lock:
            snapshot, raw = self._navigation(hwnd)
            return snapshot

    def _navigation(self, hwnd: int) -> tuple[dict, dict]:
        raw = self._call(operation="navigation", hwnd=hwnd)
        return self._remember_navigation(hwnd, raw), raw

    def _remember_navigation(self, hwnd: int, raw: dict) -> dict:
        if not raw.get("ok"):
            raise TargetError("暂时无法读取项目与对话列表，请稍后刷新")
        items = []
        for item in raw.get("items", [])[:1000]:
            if item.get("kind") not in ("project", "thread", "more", "section", "sidebar", "new"):
                continue
            entry = {key: item.get(key, "") for key in ("id", "kind", "title", "project", "projectTitle")}
            if "expanded" in item:
                entry["expanded"] = bool(item["expanded"])
            if 'selected' in item:entry['selected']=bool(item['selected'])
            items.append(entry)
        key = hashlib.sha256(json.dumps([raw.get("identity"), items], sort_keys=True).encode()).hexdigest()[:24]
        snapshot={"items": items, "key": key, "source":hashlib.sha256(str(raw.get('identity') or '').encode()).hexdigest()[:24],"available": bool(items),
                  "notice": "" if items else "电脑当前页面没有可读取的对话列表，请展开电脑侧边栏后刷新。"}
        self.catalogs[key] = (time.monotonic(), hwnd, raw["identity"], items)
        while len(self.catalogs) > 32:
            self.catalogs.pop(next(iter(self.catalogs)))
        return snapshot

    def navigate(self, hwnd: int, key: str, target: str, expanded: bool, refresh: bool = False) -> str | dict:
        with self.lock:
            reference = self.catalogs.get(key)
            if not reference or reference[1] != hwnd:
                raise TargetError("列表已更新，请重新选择对话", 'navigation_changed')
            observed = [item for item in reference[3] if item["id"] == target]
            if len(observed) != 1:
                raise TargetError("列表已更新，请重新选择对话", 'navigation_changed')
            item = observed[0]
            before = self._call(operation="identity", hwnd=hwnd) if item["kind"] in ('thread','new') else None
            reply = self._call(operation="navigate", hwnd=hwnd, identity=reference[2],
                               target=target, kind=item["kind"], title=item["title"], project=item['project'],
                               projectTitle=item['projectTitle'], expanded=expanded, refresh=refresh)
            self.cached = None
            if not reply.get('ok') and reply.get('reason') == 'navigation changed':
                # UIA guarantees this rejection happened before invocation.
                # Refresh and rebind the same intent, never repeat an invocation.
                _, fresh = self._navigation(hwnd)
                matches = [row for row in fresh.get('items', []) if all(
                    row.get(field, '') == item.get(field, '') for field in ('kind', 'title', 'project', 'projectTitle'))]
                if fresh.get('identity') == reference[2] and len(matches) == 1:
                    reply = self._call(operation='navigate', hwnd=hwnd, identity=fresh['identity'],
                                       target=matches[0]['id'], kind=item['kind'], title=item['title'],
                                       project=item['project'], projectTitle=item['projectTitle'],
                                       expanded=expanded, refresh=refresh)
                else:
                    raise TargetError('列表已更新，请重新选择对话', 'navigation_changed')
            if not reply.get("ok"):
                raise TargetError('暂时无法打开这个对话', 'navigation_changed' if reply.get('reason') == 'navigation changed' else '')
            if before and not reply.get('unchanged'):
                # UI navigation is asynchronous. Wait for a fresh conversation
                # before allowing subsequent messages to target the new epoch.
                for _ in range(10):
                    time.sleep(0.06)
                    after = self._call(operation="identity", hwnd=hwnd)
                    if not after.get("ok"):
                        raise TargetError("正在打开对话，请等待页面同步后再发送")
                    if after.get("identity") != before.get("identity"):
                        break
            if refresh:
                return {'kind':item['kind'],'unchanged':bool(reply.get('unchanged')), **({'navigation':self._remember_navigation(hwnd,reply['next'])} if isinstance(reply.get('next'),dict) else {})}
            return item["kind"]

    @staticmethod
    def _request_accessibility(hwnd: int) -> None:
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        handles = [hwnd]

        @callback_type
        def visit(child, _):
            name = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(child, name, 128)
            if "Chrome_RenderWidgetHost" in name.value:
                handles.append(int(child))
            return True

        u32.EnumChildWindows(hwnd, visit, 0)
        u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                          wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                          ctypes.POINTER(ctypes.c_size_t)]
        for handle in handles:
            for object_id in (1, -4):
                value = ctypes.c_size_t()
                u32.SendMessageTimeoutW(handle, 0x003D, 0, object_id, 2, 300, ctypes.byref(value))

    def _start(self) -> None:
        shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script_path = str(Path(__file__).with_name("uia_reader.ps1").resolve()).replace("'", "''")
        bootstrap = ("& ([scriptblock]::Create([System.IO.File]::ReadAllText('" + script_path
                     + "', [System.Text.Encoding]::UTF8)))")
        command = base64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
        self.process = subprocess.Popen(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Mta",
             "-EncodedCommand", command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.replies = queue.Queue()
        process, replies = self.process, self.replies

        def read() -> None:
            try:
                for line in process.stdout:
                    replies.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                replies.put(None)

        threading.Thread(target=read, name="conversation-reader", daemon=True).start()

    def _stop(self) -> None:
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()

    def _call(self, **request) -> dict:
        if self.accessible_hwnd != request["hwnd"]:
            self._request_accessibility(request["hwnd"])
            self.accessible_hwnd = request["hwnd"]
        if self.process is None or self.process.poll() is not None:
            self._stop()
            self._start()
        self.sequence += 1
        request["request_id"] = self.sequence
        try:
            self.process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
            self.process.stdin.flush()
            reply = self.replies.get(timeout=5)
            if not reply or reply.get("request_id") != self.sequence:
                raise ValueError("reader stopped")
            return reply
        except (OSError, ValueError, queue.Empty):
            self._stop()
            raise TargetError("读取对话暂时超时，画面模式仍可使用") from None

    def read(self, hwnd: int) -> dict:
        with self.lock:
            if self.cached and self.cached[0] == hwnd and time.monotonic() - self.cached[1] < 0.4:
                return dict(self.cached[2])
            raw = self._call(operation="read", hwnd=hwnd)
            if not raw.get("ok"):
                raise TargetError("当前窗口暂时无法读取对话，请切换画面操作")
            result = parse_snapshot(raw)
            root_options = result.get('composer', {}).get('options')
            if isinstance(root_options, dict):
                result['composer'] = {**result['composer'], 'options': self._remember_options(hwnd, root_options)}
            self.cached = (hwnd, time.monotonic(), result, raw["identity"])
            return dict(result)

    def prepare(self, hwnd: int, key: str | None) -> str:
        # Always inspect again before input, including the existing draft.
        with self.lock:
            raw = self._call(operation="identity", hwnd=hwnd)
            observed_key = hashlib.sha256(str(raw.get("identity") or "").encode()).hexdigest()[:24]
            if not raw.get("ok") or (key is not None and observed_key != key):
                raise TargetError("电脑已切换对话，请等手机更新后再发送")
            reply = self._call(operation="prepare", hwnd=hwnd, identity=raw["identity"])
            self.prepared_generating = bool(reply.get("generating"))
            self.cached = None
            if not reply.get("ok"):
                reasons = {"draft exists": "电脑输入框已有草稿，请在“电脑草稿”中取回、清空或替换发送",
                           "composer focus rejected": "无法聚焦电脑输入框，请切换画面后重试",
                           "conversation changed": "电脑已切换对话，请等手机更新后再发送"}
                raise TargetError(reasons.get(reply.get("reason"), "无法定位消息输入框，请切换画面操作"),
                                  "draft_exists" if reply.get("reason") == "draft exists" else "")
            return observed_key

    def attachment_focus(self, hwnd: int, key: str) -> dict:
        with self.lock:
            raw=self._call(operation='identity',hwnd=hwnd)
            if not raw.get('ok') or hashlib.sha256(str(raw.get('identity') or '').encode()).hexdigest()[:24]!=key:
                raise TargetError('电脑已切换对话，请重新选择附件')
            reply=self._call(operation='attachment_focus',hwnd=hwnd,identity=raw['identity'])
            if not reply.get('ok'):raise TargetError('无法聚焦电脑输入框，附件尚未附加')
            return {'identity':raw['identity'],**reply.get('attachments',{})}

    def confirm_attachments(self, hwnd: int, before: dict, names: list[str]) -> dict:
        with self.lock:
            try:
                for _ in range(12):
                    raw=self._call(operation='attachments',hwnd=hwnd,identity=before['identity'])
                    if not raw.get('ok'):raise TargetError('附加结果未确认，请检查电脑输入框；没有自动重试')
                    current=raw.get('attachments',{})
                    if current.get('ready') and current.get('count',0)>=before.get('count',0)+len(names):
                        return {'state':'attached'}
                    time.sleep(.18)
                raise TargetError('文件已传到电脑，附加结果未确认；请在画面中检查，没有自动重试')
            finally:self.cached=None

    def submit(self, hwnd: int, key: str, text: str) -> dict:
        with self.lock:
            raw = self._call(operation="identity", hwnd=hwnd)
            if not raw.get("ok") or hashlib.sha256(str(raw.get("identity") or "").encode()).hexdigest()[:24] != key:
                raise TargetError("电脑已切换对话，文字仍在输入框中，请先检查")
            try:
                reply = self._call(operation="submit", hwnd=hwnd, identity=raw["identity"], text=text)
            except TargetError:
                raise TargetError("正在确认发送", 'send_pending') from None
            finally:
                self.cached = None
            if not reply.get("ok"):
                reasons = {"send button unavailable": "未发送：电脑发送按钮不可用",
                           "draft changed": "未发送：电脑输入框内容已变化",
                           "conversation changed": "未发送：电脑已切换对话"}
                reason = reply.get('reason')
                raise TargetError(reasons.get(reason, "正在确认发送"),
                                  'send_not_started' if reason in reasons else 'send_pending')
            return {"state": reply.get("submission") if reply.get("submission") in ("sent", "queued") else "submitted"}

    def close(self) -> None:
        with self.lock:
            self.cached = None
            self.catalogs.clear()
            self.option_catalogs.clear()
            self._stop()
