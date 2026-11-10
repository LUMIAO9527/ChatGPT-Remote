"""Device-bound receipts with hash-only persistence and read-only recovery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable

from windows_control import TargetError


CONFIRM_TIMEOUT_SECONDS = 45.0
STALE_RECEIPT_SECONDS = 300.0
UNCONFIRMED_ERROR = '无法确认是否已发送，请在电脑端核对后重试。'


def normalized(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalized(text).encode('utf-8')).hexdigest()


def recovery_proof(before: dict, text: str, attachments: bool) -> dict:
    return {'key': before.get('key'), 'textHash': text_hash(text), 'attachments': bool(attachments),
            'sources': [m['sourceId'] for m in before.get('messages', []) if m.get('sourceId')],
            'matches': sum(m.get('role') == 'user' and text_hash(m.get('text', '')) == text_hash(text)
                           for m in before.get('messages', [])),
            'queueIds': [q.get('id') for q in before.get('queued', [])]}


def recovered_evidence(proof: dict, after: dict) -> str:
    sources = set(proof.get('sources', []))
    same = proof.get('key') == after.get('key') or bool(sources & {
        m.get('sourceId') for m in after.get('messages', []) if m.get('sourceId')})
    if not same:
        return ''
    matches = [m for m in after.get('messages', []) if m.get('role') == 'user' and
               text_hash(m.get('text', '')) == proof['textHash'] and
               (normalized(m.get('text', '')) or proof.get('attachments') and m.get('attachments'))]
    if len(matches) > proof.get('matches', 0) or (sources and any(
            m.get('sourceId') and m['sourceId'] not in sources for m in matches)):
        return 'sent'
    if any(q.get('id') not in proof.get('queueIds', []) and normalized(q.get('text', '')) and
           text_hash(q.get('text', '')) == proof['textHash'] for q in after.get('queued', [])):
        return 'queued'
    return ''


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
    def __init__(self, action: Callable, read: Callable, path: Path | None = None, draft: Callable | None = None) -> None:
        self.action, self.read = action, read
        self.lock = threading.RLock()
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.stop = threading.Event()
        self.instance = secrets.token_hex(12)
        self.path = path
        self.draft = draft
        self.recovery_thread = None
        if path and path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                for row in data.get('receipts', [])[:256]:
                    if (not isinstance(row, dict) or time.time() - row.get('savedAt', 0) > 86400 or
                            not re.fullmatch('[a-f0-9]{64}', row.get('owner', '')) or
                            not re.fullmatch('[A-Za-z0-9_-]{16,80}', row.get('id', '')) or
                            row.get('state') not in ('sending', 'confirming', 'sent', 'queued', 'failed')):
                        continue
                    owner = row.pop('owner')
                    age = time.time() - row.get('savedAt', 0)
                    row['at'] = time.monotonic()
                    if row['state'] in ('sending', 'confirming'):
                        if age > STALE_RECEIPT_SECONDS:
                            row.update(state='failed', code='send_unconfirmed', error=UNCONFIRMED_ERROR)
                        elif row.get('recovery'):
                            row['state'] = 'confirming'
                            row['restarted'] = True
                        else:
                            row.update(state='failed', code='send_not_received', error='消息未发送，请重试。')
                    self.records[(owner, row['id'])] = row
            except (OSError, ValueError, TypeError):
                pass
        if any(row.get('recovery') and row['state'] == 'confirming' for row in self.records.values()):
            self._start_recovery()

    def _persist(self) -> bool:
        if not self.path:
            return True
        try:
            rows = [{**{k: v for k, v in record.items() if k in (
                'id', 'state', 'error', 'code', 'fingerprint', 'recovery', 'cancelled')},
                'owner': owner, 'savedAt': time.time()} for (owner, _), record in self.records.items()]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'version': 1, 'receipts': rows}), encoding='utf-8')
            temporary.replace(self.path)
            return True
        except OSError:
            return False

    def _start_recovery(self) -> None:
        if self.recovery_thread and self.recovery_thread.is_alive():
            return
        def recover():
            while not self.stop.is_set():
                with self.lock:
                    for record in self.records.values():
                        self._expire(record)
                    if not any(r.get('recovery') and r['state'] == 'confirming' for r in self.records.values()):
                        return
                try:
                    snapshot = self.read()
                    self.observe(snapshot)
                    if self.draft:
                        draft = self.draft()
                        with self.lock:
                            for record in self.records.values():
                                proof = record.get('recovery')
                                if (proof and record['state']=='confirming' and record.get('restarted') and
                                        draft.get('conversationKey')==proof['key'] and not draft.get('truncated') and
                                        draft.get('hasDraft') and text_hash(draft.get('text',''))==proof['textHash']):
                                    record['draftChecks']=record.get('draftChecks',0)+1
                                    if record['draftChecks']>=3:
                                        record.update(error='消息未发送，请重试。',code='send_not_started')
                                        self.settle(record,'failed')
                                elif record.get('restarted'):
                                    record['draftChecks']=0
                except Exception:
                    pass
                if self.stop.wait(1):
                    return
        self.recovery_thread = threading.Thread(target=recover, name='delivery-recovery', daemon=True)
        self.recovery_thread.start()

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
                if existing.get('fingerprint') and existing['fingerprint'] != fingerprint:
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
            if not self._persist():
                del self.records[key]
                raise TargetError('电脑暂时无法保存发送记录，消息未发送。', 'send_not_started')
            threading.Thread(target=self._deliver, args=(record, token), name='message-delivery', daemon=True).start()
            return self.public(record)

    def _expire(self, record: dict) -> bool:
        if (record.get('state') in ('sending', 'confirming') and
                time.monotonic() - record.get('at', 0) > CONFIRM_TIMEOUT_SECONDS):
            record.update(state='failed', code='send_unconfirmed', error=UNCONFIRMED_ERROR)
            record.pop('before', None)
            record.pop('command', None)
            record.pop('recovery', None)
            self._persist()
            return True
        return False

    def get(self, token: str, ident: str) -> dict | None:
        with self.lock:
            record = self.records.get((self.owner(token), ident))
            if record:
                self._expire(record)
            return self.public(record) if record else None

    def resolve(self, token: str, ident: str, instance: str, recovery: dict | None = None) -> dict:
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', ident):
            raise TargetError('发送编号无效', 'delivery_invalid')
        with self.lock:
            key = self.owner(token), ident
            if key in self.records:
                return self.public(self.records[key])
            if instance != self.instance:
                if isinstance(recovery, dict) and isinstance(recovery.get('text'), str) and len(recovery['text']) <= 6000:
                    sources, queue_ids = recovery.get('sources'), recovery.get('queueIds')
                    if (isinstance(recovery.get('key'), str) and isinstance(sources, list) and len(sources) <= 2000 and
                            all(isinstance(s, str) and len(s) <= 500 for s in sources) and isinstance(queue_ids, list) and
                            len(queue_ids) <= 200 and type(recovery.get('matches')) is int and recovery['matches'] >= 0):
                        self.make_room()
                        record = {'id': ident, 'state': 'confirming', 'at': time.monotonic(), 'recovery': {
                            'key': recovery['key'], 'textHash': text_hash(recovery['text']), 'sources': sources,
                            'queueIds': queue_ids, 'matches': recovery['matches'], 'attachments': bool(recovery.get('attachments'))}}
                        record['restarted']=True
                        self.records[key] = record
                        self._persist()
                        self._start_recovery()
                        return self.public(record)
                return {'id': ident, 'state': 'confirming'}
            # Seal this ID before reporting a request as not received. A delayed
            # POST bearing this ID can no longer invoke the desktop afterward.
            self.make_room()
            record = {'id': ident, 'state': 'failed', 'cancelled': True,
                      'at': time.monotonic(), 'error': '连接中断，点发送重试。', 'code': 'send_not_received'}
            self.records[key] = record
            self._persist()
            return self.public(record)

    def settle(self, record: dict, state: str) -> None:
        record['state'] = state
        record.pop('before', None)
        record.pop('command', None)
        record.pop('recovery', None)
        self._persist()

    def _confirm(self, record: dict, snapshot: dict) -> bool:
        if record['state'] not in ('sending', 'confirming'):
            return False
        if record.get('before') and record.get('command'):
            command = record['command']
            state = evidence(record['before'], snapshot, command.get('text', ''), bool(command.get('attachmentIds')))
        elif record.get('recovery'):
            state = recovered_evidence(record['recovery'], snapshot)
        else:
            return False
        if state:
            record.pop('error', None)
            self.settle(record, state)
            return True
        return False

    def observe(self, snapshot: dict) -> None:
        with self.lock:
            for record in self.records.values():
                if not self._expire(record):
                    self._confirm(record, snapshot)

    def needs_observation(self) -> bool:
        with self.lock:
            return any(record['state'] in ('sending', 'confirming') for record in self.records.values())

    def _deliver(self, record: dict, token: str) -> None:
        command = record['command']
        try:
            before = self.read()
            if before.get('epoch') != command['epoch'] or before.get('key') != command.get('conversationKey'):
                raise TargetError('对话已切换，请在当前对话重新发送', 'conversation_changed')
            with self.lock:
                record['before'] = before
                record['recovery'] = recovery_proof(before, command.get('text', ''), bool(command.get('attachmentIds')))
                if not self._persist():
                    raise TargetError('电脑暂时无法保存发送记录，消息未发送。', 'send_not_started')
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
                        self._persist()
                    return
                if record['state'] == 'sending':
                    record['state'] = 'confirming'
        # A lost UIA reply is recovered by observing; the action is never repeated.
        delay = .25
        while not self.stop.is_set():
            with self.lock:
                if record['state'] not in ('sending', 'confirming') or self._expire(record):
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
