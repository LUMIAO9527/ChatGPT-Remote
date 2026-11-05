"""Device-bound send receipts and read-only recovery. Nothing is written to disk."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Callable

from windows_control import TargetError


def normalized(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def evidence(before: dict, after: dict, text: str, attachments: bool = False) -> str:
    """An old identical message must never acknowledge a new send."""
    if before.get('epoch') != after.get('epoch'):
        return ''
    old = [m for m in before.get('messages', []) if m.get('role') == 'user']
    new = [m for m in after.get('messages', []) if m.get('role') == 'user']
    old_sources = {m['sourceId'] for m in before.get('messages', []) if m.get('sourceId')}
    new_sources = {m['sourceId'] for m in after.get('messages', []) if m.get('sourceId')}
    same_chat = before.get('key') == after.get('key') or bool(old_sources & new_sources)
    # A first message creates the previously empty conversation's identity.
    if not same_chat and before.get('messages'):
        return ''
    def matches(message):
        return normalized(str(message.get('text', ''))) == normalized(text) and (
            bool(normalized(text)) or (attachments and bool(message.get('attachments'))))
    candidates = [m for m in new if matches(m)]
    if candidates:
        if old_sources and any(m.get('sourceId') and m['sourceId'] not in old_sources for m in candidates):
            return 'sent'
        if sum(matches(m) for m in new) > sum(matches(m) for m in old):
            return 'sent'
    if same_chat:
        old_queue = {q.get('id') for q in before.get('queued', [])}
        if any(q.get('id') not in old_queue and normalized(str(q.get('text', ''))) == normalized(text)
               for q in after.get('queued', []) if normalized(text)):
            return 'queued'
    return ''


class DeliveryStore:
    def __init__(self, action: Callable, read: Callable) -> None:
        self.action, self.read = action, read
        self.lock = threading.RLock()
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.stop = threading.Event()
        self.instance = secrets.token_hex(12)

    @staticmethod
    def owner(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def public(record: dict) -> dict:
        return {key: record[key] for key in ('id', 'state', 'error', 'code') if key in record}

    def make_room(self) -> None:
        now = time.monotonic()
        for old_key, value in list(self.records.items()):
            if value['state'] in ('sent', 'queued', 'failed') and now - value['at'] > 3600:
                del self.records[old_key]
        if len(self.records) >= 256:
            terminal = [k for k, v in self.records.items() if v['state'] in ('sent', 'queued', 'failed')]
            if not terminal:
                raise TargetError('发送任务正在处理中', 'delivery_busy')
            del self.records[terminal[0]]

    def submit(self, command: dict, token: str) -> dict:
        ident = command.get('messageId')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', ident):
            raise TargetError('发送编号无效', 'delivery_invalid')
        owner = self.owner(token)
        fingerprint = hashlib.sha256(json.dumps(command, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            key = owner, ident
            existing = self.records.get(key)
            if existing:
                if existing.get('cancelled'):
                    return self.public(existing)
                if existing['fingerprint'] != fingerprint:
                    raise TargetError('发送编号已对应另一条消息', 'delivery_conflict')
                return self.public(existing)
            if command.get('deliveryInstance') and command['deliveryInstance'] != self.instance:
                raise TargetError('连接已更新', 'delivery_instance')
            self.make_room()
            if any(k[0] == owner and v['state'] in ('sending', 'confirming') for k, v in self.records.items()):
                raise TargetError('上一条消息正在发送', 'delivery_busy')
            record = {'id': ident, 'state': 'sending', 'fingerprint': fingerprint, 'at': time.monotonic(),
                      'command': dict(command), 'before': None}
            self.records[key] = record
            threading.Thread(target=self._deliver, args=(record, token), name='message-delivery', daemon=True).start()
            return self.public(record)

    def get(self, token: str, ident: str) -> dict | None:
        with self.lock:
            record = self.records.get((self.owner(token), ident))
            return self.public(record) if record else None

    def resolve(self, token: str, ident: str, instance: str) -> dict:
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', ident):
            raise TargetError('发送编号无效', 'delivery_invalid')
        with self.lock:
            key = self.owner(token), ident
            if key in self.records:
                return self.public(self.records[key])
            if instance != self.instance:
                return {'id': ident, 'state': 'confirming'}
            # Seal this ID before reporting a request as not received. A delayed
            # POST bearing this ID can no longer invoke the desktop afterward.
            self.make_room()
            record = {'id': ident, 'state': 'failed', 'cancelled': True,
                      'at': time.monotonic(), 'error': '连接中断，点发送重试。', 'code': 'send_not_received'}
            self.records[key] = record
            return self.public(record)

    @staticmethod
    def settle(record: dict, state: str) -> None:
        record['state'] = state
        record.pop('before', None)
        record.pop('command', None)

    def _confirm(self, record: dict, snapshot: dict) -> bool:
        if not record.get('before') or record['state'] not in ('sending', 'confirming'):
            return False
        command = record['command']
        state = evidence(record['before'], snapshot, command.get('text', ''), bool(command.get('attachmentIds')))
        if state:
            self.settle(record, state)
            record.pop('error', None)
            return True
        return False

    def observe(self, snapshot: dict) -> None:
        with self.lock:
            for record in self.records.values():
                self._confirm(record, snapshot)

    def _deliver(self, record: dict, token: str) -> None:
        command = record['command']
        try:
            before = self.read()
            if before.get('epoch') != command['epoch'] or before.get('key') != command.get('conversationKey'):
                raise TargetError('对话已切换，请在当前对话重新发送', 'conversation_changed')
            with self.lock:
                record['before'] = before
            if self.stop.is_set():
                return
            result = self.action(command, token) or {}
            submission = result.get('submission', {})
            with self.lock:
                if submission.get('state') in ('sent', 'queued'):
                    self.settle(record, submission['state'])
                elif record['state'] == 'sending':
                    record['state'] = 'confirming'
        except Exception as exc:
            with self.lock:
                if getattr(exc, 'code', '') != 'send_pending':
                    # A pre-invocation rejection is a known failure, not a retry.
                    if record['state'] not in ('sent', 'queued'):
                        self.settle(record, 'failed')
                        record.update(error=str(exc), code=getattr(exc, 'code', ''))
                    return
                if record['state'] == 'sending':
                    record['state'] = 'confirming'
        # A lost UIA reply is recovered by observing; the action is never repeated.
        delay = .25
        while not self.stop.is_set():
            with self.lock:
                if record['state'] not in ('sending', 'confirming'):
                    return
            try:
                snapshot = self.read()
                with self.lock:
                    if self._confirm(record, snapshot):
                        return
            except Exception:
                pass
            if self.stop.wait(delay):
                return
            delay = min(5, delay * 1.7)

    def close(self) -> None:
        self.stop.set()
