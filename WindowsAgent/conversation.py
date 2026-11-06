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
from urllib.parse import urlsplit

from windows_control import TargetError
from local_history import LocalHistory, UUID

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
                    public = {k: v for k, v in block.items() if not k.startswith('_') and k != 'runs'}
                    public['text'] = block['text'][:40000-used]
                    if block.get('runs'):
                        left, runs = len(public['text']), []
                        for run in block['runs']:
                            if left <= 0: break
                            runs.append({**run, 'text': run['text'][:left]})
                            left -= len(run['text'])
                        public['runs'] = runs
                    visible.append(public)
                    used += len(block["text"])
                message = {"id": f"m{len(messages)}", "role": role, "text": text[:40000], "blocks": visible,
                                 **({'sourceId':source_id} if source_id else {}),
                                 "complete": role == "user" or ended or completed or not bool(raw.get("generating")),
                                 **({"attachments": list(attachments)} if attachments else {}),
                                 **({'duration':duration} if duration else {}),
                                 **({"timestamp": timestamp} if timestamp else {}),
                                 **({"phase": phase} if phase != "answer" else {})}
                if (messages and role == 'assistant' and phase in ('activity', 'reasoning')
                        and messages[-1].get('phase') == phase and messages[-1]['role'] == role):
                    previous = messages[-1]
                    previous['text'] += ('\n\n' if previous['text'] and message['text'] else '') + message['text']
                    previous['blocks'].extend(message['blocks'])
                    previous['complete'] = message['complete']
                    for key in ('duration', 'timestamp'):
                        if key in message: previous[key] = message[key]
                else:
                    messages.append(message)

    for node in raw.get("nodes", []):
        name = str(node.get("name") or "")
        marker = ROLE_MARKERS.get(name.strip()) if node.get("marker") else None
        # Native user bubbles and activity containers carry their own role.
        # Tool status between turns must not inherit the preceding user's role.
        native_role = node.get('role')
        if not marker and not node.get('queued') and native_role in ('user', 'assistant') and native_role != role:
            finish(ended=True)
            role, blocks, previous_rect, timestamp = native_role, [], None, ''
            source_id, attachments, completed = str(node.get('sourceId') or '')[:160], [], False
            duration = pending_duration if role == 'assistant' else ''
            if role == 'assistant': pending_duration = ''
            phase = node.get('channel') if role == 'assistant' and node.get('channel') in ('activity', 'reasoning') else 'answer'
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
                kind = node.get('kind') if node.get('kind') in ('paragraph', 'list', 'heading', 'code') else (
                    "list" if node.get("type") == "ListItem" else (
                    "heading" if node.get("type") == "Header" else ("code" if node.get("code") else "paragraph"))
                )
                channel = node.get("channel") if role == "assistant" and node.get("channel") in ("activity", "reasoning") else phase
                rect = node.get("rect")
                block_id = str(node.get('blockId') or '')
                inline = (blocks and blocks[-1]['kind'] == kind and blocks[-1].get('channel', 'answer') == channel
                          and ((block_id and blocks[-1].get('_source') == block_id) or (
                              not block_id and kind in ('paragraph', 'code') and rect and previous_rect
                              and rect['top'] <= previous_rect['bottom'] + 3
                              and rect['bottom'] >= previous_rect['top'] - 3)))
                def text_run(value):
                    run = {'text': str(value.get('text') or ''), **{k: True for k in ('strong', 'emphasis', 'inlineCode') if value.get(k)}}
                    href = str(value.get('href') or '')[:8000]
                    try:
                        parsed = urlsplit(href)
                        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
                            run['href'] = href
                    except ValueError:
                        pass
                    return run
                supplied = node.get('runs')
                runs = ([text_run(run) for run in supplied] if isinstance(supplied, list) and supplied
                        and all(isinstance(run, dict) and isinstance(run.get('text'), str) for run in supplied) else [])
                if not runs or ''.join(run['text'] for run in runs) != name:
                    runs = [text_run({**node, 'text': name})]
                formatted = any(len(run) > 1 for run in runs)
                if inline:
                    if formatted or blocks[-1].get('runs'):
                        blocks[-1].setdefault('runs', [{'text': blocks[-1]['text']}]).extend(runs)
                    blocks[-1]["text"] += name
                    if rect and previous_rect:
                        previous_rect = {"top": min(previous_rect["top"], rect["top"]),
                                         "bottom": max(previous_rect["bottom"], rect["bottom"])}
                else:
                    if not block_id and blocks and kind == "code" and blocks[-1]["kind"] == "code" and blocks[-1].get("channel", "answer") == channel:
                        blocks[-1]["text"] += "\n" + name
                    else:
                        blocks.append({"kind": kind, "text": name, **({'_source': block_id} if block_id else {}),
                                       **({'runs': runs} if formatted else {}),
                                       **({"channel": channel} if channel != "answer" else {})})
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
        self.prompt_attempts: dict[str, float] = {}
        self.queue_catalogs: dict[str, tuple[float, int, dict]] = {}
        self.queue_used: dict[tuple[str,str], float] = {}
        self.prepared_generating = False
        self.prepared_text_present = False
        self.history = LocalHistory()

    def _remember_queue(self, hwnd: int, raw: dict) -> dict:
        if not raw.get('ok') or not isinstance(raw.get('identity'),str):
            raise TargetError('暂时无法读取电脑队列，请刷新')
        snapshot={field:raw.get(field,default) for field,default in (
            ('identity',''),('items',[]),('actions',[]),('paused',False),('hasDraft',False))}
        key=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()[:24]
        now=time.monotonic()
        self.queue_used={used:at for used,at in self.queue_used.items() if now-at<120}
        self.queue_catalogs[key]=(now,hwnd,snapshot)
        while len(self.queue_catalogs)>8:self.queue_catalogs.pop(next(iter(self.queue_catalogs)))
        def pending_action(action):
            return {**action,'enabled':False} if (key,action['id']) in self.queue_used else action
        return {'key':key,**{k:v for k,v in snapshot.items() if k not in ('identity','items','actions')},
                'items':[{**row,'actions':[pending_action(action) for action in row.get('actions',[])]}
                         for row in snapshot['items']],
                'actions':[pending_action(action) for action in snapshot['actions']],'available':True}

    def queue(self, hwnd: int) -> dict:
        with self.lock:
            return self._remember_queue(hwnd,self._call(operation='queue',hwnd=hwnd))

    def queue_action(self, hwnd: int, key: str, target: str) -> dict:
        with self.lock:
            reference=self.queue_catalogs.get(key)
            if not reference or reference[1]!=hwnd or time.monotonic()-reference[0]>120:
                raise TargetError('队列已变化，请刷新后重新选择','queue_changed')
            snapshot=reference[2]
            choices=[(None,a) for a in snapshot['actions']]
            choices += [(row,a) for row in snapshot['items'] for a in row.get('actions',[])]
            matches=[(row,a) for row,a in choices if a.get('id')==target and a.get('enabled')]
            if len(matches)!=1 or (key,target) in self.queue_used:
                raise TargetError('该队列操作已处理或已变化，请先检查电脑状态','queue_changed')
            row,action=matches[0]
            self.queue_used={k:v for k,v in self.queue_used.items() if time.monotonic()-v<120}
            self.queue_used[key,target]=time.monotonic()
            try:
                reply=self._call(operation='queue_invoke',hwnd=hwnd,identity=snapshot['identity'],
                                 target=target,action=action['operation'],
                                 item=row['id'] if row else '',text=row['text'] if row else '')
            except TargetError:
                raise TargetError('操作结果待核对，请查看队列。','queue_pending') from None
            finally:
                self.cached=None
            if not reply.get('ok'):
                reason=reply.get('reason','')
                if reason=='queue draft exists':
                    self.queue_used.pop((key,target),None)
                    raise TargetError('电脑输入框已有草稿，请先处理后再编辑队列','draft_exists')
                if reason in ('queue changed','queue unavailable','queue edit not started'):
                    self.queue_used.pop((key,target),None)
                    if reason=='queue edit not started':
                        raise TargetError('未打开队列编辑，请重试。','queue_edit_not_started')
                    raise TargetError('队列已变化，请刷新后重新选择。','queue_changed')
                raise TargetError('队列操作未确认，请检查电脑状态后刷新','queue_pending')
            result={'queue':self._remember_queue(hwnd,reply.get('queue') or self._call(operation='queue',hwnd=hwnd))}
            def confirmed(current):
                if self.queue_catalogs[current['key']][2]['identity']!=snapshot['identity']:return False
                if action['operation'] in ('delete','steer'):
                    return (not any(item['id']==row['id'] for item in current['items'])
                            and sum(item['text']==row['text'] for item in current['items'])
                            <sum(item['text']==row['text'] for item in snapshot['items']))
                if action['operation']=='resume':return not current['paused']
                if action['operation']=='stop':return not any(a['operation']=='stop' for a in current['actions'])
                return True
            for _ in range(5):
                if confirmed(result['queue']):break
                time.sleep(.08)
                result['queue']=self._remember_queue(hwnd,self._call(operation='queue',hwnd=hwnd))
            if not confirmed(result['queue']):
                raise TargetError('操作结果仍在确认，请刷新队列或查看电脑画面','queue_pending')
            if reply.get('edited'):
                result['queueDraft']=draft_snapshot(reply)
            return result

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
            if raw.get('category')!='prompt' and (INFO_OPTION.fullmatch(item['title'].strip()) or
                    (raw.get('category')=='permission' and PERMISSION_INFO.fullmatch(item['title'].strip()))):
                continue
            title = item["title"][:500]
            kind = str(item.get("kind") or "option")
            option = {"id": item["id"], "title": title, "kind": kind,
                      "enabled": bool(item.get("enabled")), "selected": bool(item.get("selected")),
                      **option_labels(title, str(raw.get("category") or ""))}
            if kind in ('prompt-radio','prompt-check') and isinstance(item.get('description'),str) and item['description']:
                option['description']=item['description'][:800]
            if kind == "effort-step":
                level, current, count = (item.get(field) for field in ("level", "currentLevel", "levelCount"))
                if not (all(type(value) is int for value in (level, current, count)) and
                        2 <= count <= 12 and 1 <= level <= count and 1 <= current <= count):
                    continue
                option.update(level=level, currentLevel=current, levelCount=count,
                              label=str(item.get("label") or title)[:500])
            items.append(option)
        snapshot = {"items": items, "menu": bool(raw.get("menu")), "context": str(raw.get("context") or ""),
                    "identity": str(raw.get("identity") or ""),"category":str(raw.get('category') or '')}
        prompt = raw.get('prompt')
        if isinstance(prompt, dict) and snapshot['context'].startswith('prompt:'):
            snapshot['menu'] = False
            snapshot['prompt'] = {
                'title': str(prompt.get('title') or '电脑待选项')[:240],
                'text': str(prompt.get('text') or '')[:8000],
                'approval': bool(prompt.get('approval')),
                **({'questionnaire': True} if prompt.get('questionnaire') else {}),
                'truncated': bool(prompt.get('truncated')),
            }
        key = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:24]
        now = time.monotonic()
        if 'prompt' in snapshot:
            self.prompt_attempts = {ident: at for ident, at in self.prompt_attempts.items() if now - at < 120}
            items = [
                {**item, 'enabled': False}
                if key in self.prompt_attempts or f"{key}:{item['id']}" in self.prompt_attempts else item
                for item in items
            ]
        self.option_catalogs[key] = (now, hwnd, snapshot)
        while len(self.option_catalogs) > 8:
            self.option_catalogs.pop(next(iter(self.option_catalogs)))
        scope = hashlib.sha256(snapshot["identity"].encode()).hexdigest()[:24]
        return {"key": key, "scope": scope, "items": items, "menu": snapshot["menu"], "available": bool(items),"category":snapshot['category'],
                **({'prompt': snapshot['prompt']} if 'prompt' in snapshot else {})}

    def composer_action(self, hwnd: int, key: str, target: str | None, dismiss: bool = False, refresh: bool = False) -> bool | dict:
        """Invoke one observed control, or validate an open menu before Escape."""
        with self.lock:
            reference = self.option_catalogs.get(key)
            if not reference or reference[1] != hwnd or time.monotonic() - reference[0] > 120:
                raise TargetError("输入框选项已变化，请刷新后重新选择")
            snapshot = reference[2]
            prompt = snapshot.get('prompt')
            if prompt:
                cutoff = time.monotonic() - 120
                if dismiss or any(self.prompt_attempts.get(attempt, float('-inf')) > cutoff
                                  for attempt in (key, f'{key}:{target}')):
                    raise TargetError('电脑请求仍在回读，请刷新查看。', 'prompt_pending')
            matches = [item for item in snapshot["items"] if item["id"] == target and item["enabled"]]
            if (dismiss and not snapshot["menu"]) or (not dismiss and len(matches) != 1):
                raise TargetError("选项已变化或不可用，请刷新后重新选择")
            # Consume before invocation. An uncertain action is never repeated.
            self.option_catalogs.pop(key, None)
            attempt = key
            if prompt and prompt.get('questionnaire'):
                item = matches[0]
                if item['kind'] in ('prompt-radio', 'prompt-check') or re.fullmatch(
                        r'继续|下一步|下一个问题|上一个问题|Continue|Next|Next question|Previous question', item['title'], re.I):
                    attempt = f"{key}:{item['id']}"
            if prompt:
                self.prompt_attempts[attempt] = time.monotonic()
            try:
                reply = self._call(operation="composer_dismiss" if dismiss else "composer_invoke", hwnd=hwnd,
                                   identity=snapshot["identity"], context=snapshot["context"],
                                   item=matches[0] if not dismiss else None, refresh=refresh)
            except TargetError:
                raise TargetError("选项操作结果未确认，请刷新查看；没有自动重试") from None
            finally:
                self.cached = None
            if not reply.get("ok"):
                if prompt and reply.get('reason') in ('options changed', 'option changed', 'option unavailable'):
                    self.prompt_attempts.pop(attempt, None)
                raise TargetError("电脑选项或当前对话已变化，请刷新后重新选择")
            if refresh:
                updated = reply.get('next')
                result = {"escape": bool(dismiss and reply.get("escape"))}
                if isinstance(updated, dict):
                    if prompt and updated.get('identity') != snapshot['identity']:
                        raise TargetError("电脑对话已变化，请重新查看。")
                    result['options'] = self._remember_options(hwnd, updated)
                    if attempt != key and result['options']['key'] != key:
                        self.prompt_attempts.pop(attempt, None)
                return result
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
        raw = self._merge_navigation(raw)
        return self._remember_navigation(hwnd, raw), raw

    def _merge_navigation(self, raw: dict) -> dict:
        selected=[row.get('title','') for row in raw.get('items',[]) if row.get('kind')=='thread' and row.get('selected')]
        local=self.history.catalog(selected[0]) if len(selected)==1 and self.history.resolve(selected[0]) else None
        if local:
            local_titles={row['title'] for row in local if row['kind']=='thread'}
            # Visible native entries remain usable, including ChatGPT cloud chats.
            for row in raw.get('items',[]):
                if row.get('kind')=='thread':
                    if row.get('title') not in local_titles:
                        local.append(row)
                    elif row.get('pending') in ('input','approval'):
                        matches=[item for item in local if item['kind']=='thread' and
                                 (item['title'],item['projectTitle'])==(row.get('title'),row.get('projectTitle',''))]
                        if len(matches)==1:matches[0]['pending']=row['pending']
                        else:local.append(row)
                elif row.get('kind')=='project' and row.get('pending') in ('input','approval'):
                    # Retain the real project button so the phone can reveal
                    # the pending children of a collapsed desktop group.
                    matches=[item for item in local if item['kind']=='project' and item['title']==row.get('title')]
                    if len(matches)==1:
                        matches[0].update(id=row['id'],nativeProject=row.get('project',''),
                                          pending=row['pending'],expanded=bool(row.get('expanded')))
                    else:local.append(row)
            projects={row['title']:row['project'] for row in local if row['kind']=='project'}
            for row in raw.get('items',[]):
                if row.get('kind')=='new':
                    row=dict(row);row['nativeProject']=row.get('project','');row['project']=projects.get(row.get('projectTitle'),row.get('project',''));local.append(row)
            raw={**raw,'items':local,'complete':True}
        return raw

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
            if item.get('pending') in ('input','approval'):entry['pending']=item['pending']
            if isinstance(item.get('threadId'),str) and UUID.fullmatch(item['threadId']):entry['threadId']=item['threadId']
            if 'nativeProject' in item:entry['nativeProject']=item['nativeProject']
            items.append(entry)
        key = hashlib.sha256(json.dumps([raw.get("identity"), items], sort_keys=True).encode()).hexdigest()[:24]
        snapshot={"items": [{k:v for k,v in row.items() if k!='nativeProject'} for row in items], "key": key, "source":hashlib.sha256(str(raw.get('identity') or '').encode()).hexdigest()[:24],"available": bool(items),
                  "complete":bool(raw.get('complete')),
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
            if item.get('threadId'):
                current=self._call(operation='observe',hwnd=hwnd)
                resolved=self.history.resolve(str(current.get('selectedTitle') or '')) if current.get('ok') else None
                if resolved and resolved['id']==item['threadId']:return {'kind':'thread','unchanged':True}
                # The installed desktop advertises codex://threads/<UUID>.
                # Open the indexed conversation through its native route.
                os.startfile('codex://threads/'+item['threadId'])
                self.cached=None
                for _ in range(20):
                    time.sleep(.07)
                    after=self._call(operation='observe',hwnd=hwnd)
                    resolved=self.history.resolve(str(after.get('selectedTitle') or '')) if after.get('ok') else None
                    if resolved and resolved['id']==item['threadId']:
                        return {'kind':'thread'} if refresh else 'thread'
                raise TargetError('对话正在打开，请稍候','navigation_pending')
            before = self._call(operation="identity", hwnd=hwnd) if item["kind"] in ('thread','new') else None
            reply = self._call(operation="navigate", hwnd=hwnd, identity=reference[2],
                               target=target, kind=item["kind"], title=item["title"], project=item.get('nativeProject',item['project']),
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
                                       project=matches[0].get('nativeProject',item['project']), projectTitle=item['projectTitle'],
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
                return {'kind':item['kind'],'unchanged':bool(reply.get('unchanged')), **({'navigation':self._remember_navigation(hwnd,self._merge_navigation(reply['next']))} if isinstance(reply.get('next'),dict) else {})}
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
        starting = self.process is None or self.process.poll() is not None
        if starting:
            self._stop()
            self._start()
        self.sequence += 1
        request["request_id"] = self.sequence
        try:
            self.process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
            self.process.stdin.flush()
            # Loading Windows PowerShell and UIAutomation happens only once.
            # Keep that startup budget separate from subsequent native actions.
            reply = self.replies.get(timeout=10 if starting or request['operation'] == 'read' else 5)
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
            observed=self._call(operation='observe',hwnd=hwnd)
            result=self.history.snapshot(observed) if observed.get('ok') else None
            raw=observed
            if result is None:raw = self._call(operation="read", hwnd=hwnd)
            if not raw.get("ok"):
                raise TargetError("当前窗口暂时无法读取对话，请切换画面操作")
            if result is None:result = parse_snapshot(raw)
            if isinstance(observed.get('queue'),dict):
                result['queue']=self._remember_queue(hwnd,observed['queue'])
            if isinstance(observed.get('prompt'), dict):
                result['prompt'] = self._remember_options(hwnd, observed['prompt'])
            result['pendingRequests'] = [
                {'id': row['id'], 'title': row['title'][:240], 'kind': row['kind'], 'pending': row['pending']}
                for row in observed.get('pendingRequests', [])[:40]
                if (isinstance(row, dict) and isinstance(row.get('id'), str) and isinstance(row.get('title'), str)
                    and row.get('kind') in ('thread','project') and row.get('pending') in ('input','approval')
                    and not row.get('selected'))
            ]
            root_options = result.get('composer', {}).get('options')
            if isinstance(root_options, dict):
                result['composer'] = {**result['composer'], 'options': self._remember_options(hwnd, root_options)}
            self.cached = (hwnd, time.monotonic(), result, raw["identity"])
            return dict(result)

    def _identity(self, hwnd: int, key: str | None = None) -> dict:
        raw = self._call(operation="identity", hwnd=hwnd)
        def matches():
            return raw.get('ok') and (key is None or hashlib.sha256(str(raw.get('identity') or '').encode()).hexdigest()[:24] == key)
        if not matches():
            deadline = time.monotonic() + .5
            while True:
                raw = self._call(operation="identity", hwnd=hwnd, fresh=True)
                if matches() or (raw.get('ok') and not str(raw.get('identity') or '').endswith('|')) or time.monotonic() >= deadline:
                    break
                time.sleep(.04)
        if not raw.get("ok"):
            raise TargetError("暂时无法读取电脑输入框", "composer_unavailable")
        return raw

    def prepare(self, hwnd: int, key: str | None, reuse_text: str | None = None) -> str:
        # Always inspect again before input, including the existing draft.
        with self.lock:
            raw = self._identity(hwnd, key)
            observed_key = hashlib.sha256(str(raw.get("identity") or "").encode()).hexdigest()[:24]
            if key is not None and observed_key != key:
                raise TargetError("电脑已切换对话，请等手机更新后再发送")
            reply = self._call(operation="prepare", hwnd=hwnd, identity=raw["identity"], text=reuse_text)
            self.prepared_generating = bool(reply.get("generating"))
            self.prepared_text_present = reply.get("present") is True
            self.cached = None
            if not reply.get("ok"):
                reasons = {"draft exists": "电脑输入框已有草稿，请在“电脑草稿”中取回、清空或替换发送",
                           "draft changed": "电脑草稿已变化，手机草稿已保留",
                           "composer focus rejected": "无法聚焦电脑输入框，请切换画面后重试",
                           "conversation changed": "电脑已切换对话，请等手机更新后再发送"}
                raise TargetError(reasons.get(reply.get("reason"), "无法定位消息输入框，请切换画面操作"),
                                  "draft_exists" if reply.get("reason") == "draft exists" else "")
            return observed_key

    def confirm_input(self, hwnd: int, key: str, text: str, submit: bool = False) -> None:
        """Wait for native input to reach the editor before releasing it."""
        with self.lock:
            raw = self._identity(hwnd, key)
            if hashlib.sha256(str(raw.get("identity") or "").encode()).hexdigest()[:24] != key:
                raise TargetError("电脑已切换对话，手机草稿已保留", "conversation_changed")
            reply = self._call(operation="input_ready", hwnd=hwnd, identity=raw["identity"], text=text, submit=submit)
            self.cached = None
            self.prepared_generating = bool(reply.get('generating'))
            if not reply.get("ok"):
                reasons = {"draft changed": "电脑草稿已被修改，手机草稿已保留",
                           "conversation changed": "电脑已切换对话，手机草稿已保留",
                           "send button unavailable": "电脑发送按钮尚未就绪，手机草稿已保留"}
                raise TargetError(reasons.get(reply.get("reason"), "文字尚未完整填入，手机草稿已保留"), "send_not_started")

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

    def attachments_ready(self, hwnd: int, before: dict, count: int) -> bool:
        with self.lock:
            raw=self._call(operation='attachments',hwnd=hwnd,identity=before['identity'])
            if not raw.get('ok'):raise TargetError('电脑已切换对话，请返回附加文件的对话','attachment_context_changed')
            current=raw.get('attachments',{})
            return bool(current.get('ready') and current.get('count',0)>=before.get('count',0)+count)

    def submit(self, hwnd: int, key: str, text: str) -> dict:
        with self.lock:
            raw = self._identity(hwnd, key)
            if hashlib.sha256(str(raw.get("identity") or "").encode()).hexdigest()[:24] != key:
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
            self.prompt_attempts.clear()
            self._stop()
