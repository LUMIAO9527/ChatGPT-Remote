"""Bounded, read-only access to the desktop's structured local conversation data.

Only user-visible messages, reasoning summaries and generic activity labels leave
this adapter. Database paths and raw tool data stay local.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from message_content import markdown_blocks, inline_runs, display_user_text
from message_resources import MessageResources

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
HISTORY_TURNS = 8
ACTIVITY_LABELS = {
    'commandExecution': ('terminal', '运行命令', '运行了命令'),
    'fileChange': ('edit', '编辑文件', '编辑了文件'),
    'mcpToolCall': ('tools', '使用工具', '使用了工具'),
    'imageView': ('image', '查看图片', '查看了图片'),
}
def user_content(content, resources=None, scope="") -> tuple[str,list[dict]]:
    text=[];attachments=[]
    for part in content if isinstance(content,list) else []:
        if not isinstance(part,dict):continue
        kind=part.get('type')
        if kind in ('text','input_text') and isinstance(part.get('text'),str):text.append(part['text'])
        elif kind in ('image','localImage','input_image'):
            ref=resources.reference(str(part.get('path') or part.get('url') or ''),scope,image=True) if resources else None
            attachments.append({'id':'attachment-'+str(len(attachments)),'kind':'image',**({'resource':ref,'name':ref['name']} if ref else {})})
        elif kind in ('file','localFile','input_file'):
            name=ntpath.basename(str(part.get('name') or part.get('path') or part.get('filename') or '文件'))
            ref=resources.reference(str(part.get('path') or ''),scope,name=name) if resources else None
            attachments.append({'id':'attachment-'+str(len(attachments)),'kind':'file','name':name[:180],**({'resource':ref} if ref else {})})
    return display_user_text('\n'.join(text)),attachments[:20]


class LocalHistory:
    def __init__(self, home: Path | None = None):
        self.home=Path(home) if home else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        self.resources=MessageResources()

    def database(self, stem: str) -> Path | None:
        paths=[]
        for path in self.home.glob(stem+'_*.sqlite'):
            version=path.stem.rsplit('_',1)[-1]
            if version.isdigit() and path.is_file():paths.append((int(version),path))
        return max(paths,default=(0,None))[1]

    @staticmethod
    def connect(path: Path):
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA temp_store=MEMORY')
        return db

    @staticmethod
    def user_thread(row) -> bool:
        """Internal agents may have no agent_path; source is authoritative."""
        if row.get('agent_path') not in (None, '', '/root'):
            return False
        source = row.get('source')
        if isinstance(source, str):
            try: source = json.loads(source)
            except ValueError: pass
        return not (isinstance(source, dict) and any(
            key in source for key in ('subagent', 'guardian', 'review')))

    def resolve(self, title: str, thread_id: str = '', account: tuple[str, str] | None = None) -> dict | None:
        path=self.database('state')
        if not path or (not title and not UUID.fullmatch(thread_id)):return None
        with closing(self.connect(path)) as db:
            columns={r['name'] for r in db.execute('PRAGMA table_info(threads)')}
            if not {'id','title','archived'}<=columns:return None
            display="coalesce(nullif(name,''), title)" if 'name' in columns else 'title'
            fields='id, '+display+' as display_title'
            fields+=(',agent_path' if 'agent_path' in columns else '')+(',source' if 'source' in columns else '')
            if account and not {'creator_user_id', 'creator_account_id'} <= columns:
                return None
            where = 'id=?' if UUID.fullmatch(thread_id) else 'archived=0 AND '+display+'=?'
            args = (thread_id,) if UUID.fullmatch(thread_id) else (title,)
            if account:
                where += ' AND creator_user_id=? AND creator_account_id=?'
                args += account
            rows=db.execute('SELECT '+fields+' FROM threads WHERE '+where+' LIMIT 2',args).fetchall()
            rows=[dict(row) for row in rows if self.user_thread(dict(row))]
            return {'id':rows[0]['id'],'display_title':rows[0]['display_title']} if len(rows)==1 else None

    def match_visible(self, title: str, messages: list[dict]) -> str | None:
        """Recognize an already open, uniquely titled local conversation."""
        def comparable(text: str) -> str:
            return ''.join(text.split())

        displayed = {comparable(row['text']): index for index, row in enumerate(messages)
                     if row['role'] == 'user' and len(comparable(row['text'])) >= 24}
        if len(displayed) < 2:
            return None
        try:
            thread = self.resolve(title)
            path = self.database('thread_history')
            if not thread or not path:
                return None
            with closing(self.connect(path)) as db:
                rows = db.execute('''SELECT item_json FROM thread_items WHERE thread_id=? AND item_type='userMessage'
                                     ORDER BY rollout_ordinal DESC LIMIT 80''', (thread['id'],)).fetchall()
            matches = []
            for row in reversed(rows):
                text, _ = user_content(json.loads(row['item_json']).get('content'))
                index = displayed.get(comparable(text))
                if index is not None:
                    matches.append(index)
            if len(matches) >= 2 and matches == sorted(set(matches)):
                return thread['id']
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            pass
        return None

    def snapshot(self, observed: dict, *, account=None, before: int | None = None) -> dict | None:
        try:
            thread=self.resolve(str(observed.get('selectedTitle') or observed.get('title') or ''),
                                str(observed.get('threadId') or ''), account)
            path=self.database('thread_history')
            if not thread or not path:return None
            upper = before if before is not None else 2**63-1
            with closing(self.connect(path)) as db:
                turn_rows = list(db.execute(
                    '''SELECT turn_id,status,duration_ms,rollout_ordinal FROM thread_turns
                       WHERE thread_id=? AND rollout_ordinal<? ORDER BY rollout_ordinal DESC LIMIT ?''',
                    (thread['id'], upper, HISTORY_TURNS+1)))
                older = len(turn_rows) > HISTORY_TURNS
                turns = {row['turn_id']:dict(row) for row in turn_rows[:HISTORY_TURNS]}
                if not turns:
                    return None
                names = ','.join('?' for _ in turns)
                # Keep the message window independent of tool volume. Select
                # public summaries and activity states without reading raw tool data.
                tool_types = ','.join('?' for _ in ACTIVITY_LABELS)
                rows=list(db.execute(f"""WITH visible AS (
                    SELECT item_id,turn_id,created_at_ms,item_type,rollout_ordinal,
                        CASE item_type
                            WHEN 'reasoning' THEN json_object('summary',json_extract(item_json,'$.summary'))
                            WHEN 'webSearch' THEN json_object('query',json_extract(item_json,'$.query'))
                            ELSE item_json END AS body
                    FROM thread_items WHERE thread_id=? AND rollout_ordinal<? AND turn_id IN ({names})
                        AND item_type IN ('userMessage','agentMessage','reasoning','webSearch')
                        AND (item_type!='reasoning' OR json_array_length(item_json,'$.summary')>0)
                        AND (item_type!='agentMessage' OR json_extract(item_json,'$.phase') IS NOT 'analysis')
                    ORDER BY (item_type IN ('userMessage','agentMessage')) DESC, rollout_ordinal DESC LIMIT 240
                ), tools AS (
                    SELECT item_id,turn_id,created_at_ms,item_type,rollout_ordinal,
                        json_object('status',json_extract(item_json,'$.status')) AS body
                    FROM thread_items WHERE thread_id=? AND rollout_ordinal<? AND turn_id IN ({names}) AND item_type IN ({tool_types})
                        AND rollout_ordinal >= coalesce((SELECT min(rollout_ordinal) FROM visible),0)
                    ORDER BY rollout_ordinal DESC LIMIT 120
                )
                SELECT * FROM visible UNION ALL SELECT * FROM tools ORDER BY rollout_ordinal
                """,(thread['id'],upper,*turns,thread['id'],upper,*turns,*ACTIVITY_LABELS)))
                first_dialogue = min((row['rollout_ordinal'] for row in rows if row['item_type'] in ('userMessage','agentMessage')),
                                     default=None)
                more_dialogue = first_dialogue is not None and len(rows) >= 240 and db.execute(f'''
                    SELECT 1 FROM thread_items WHERE thread_id=? AND turn_id IN ({names}) AND rollout_ordinal<?
                    AND (item_type='userMessage' OR (item_type='agentMessage' AND json_extract(item_json,'$.phase') IS NOT 'analysis'))
                    LIMIT 1''', (thread['id'], *turns, first_dialogue)).fetchone()
            if not rows:return None
            messages=[];activities={};boundary='start'; message_ordinals={}
            self.resources.scope=thread['id']
            def render(text,channel=''):return markdown_blocks(text,channel,self.resources,thread['id'])
            def activity_for(turn_id,turn,complete,ordinal):
                segment=(turn_id,boundary)
                if segment not in activities:
                    status='working' if turn.get('status')=='inProgress' else ('stopped' if turn.get('status')=='interrupted' else 'worked' if complete else 'unknown')
                    work={'status':status}
                    if complete and isinstance(turn.get('duration_ms'),(int,float)):
                        work['durationMs']=max(0,round(turn['duration_ms']))
                    identifier='activity-'+turn_id+'-'+boundary
                    if before is not None and boundary=='start':
                        identifier+='-'+str(ordinal)
                    activity={'id':identifier,'sourceId':identifier,'turnId':turn_id,
                              'role':'assistant','phase':'activity','text':'','blocks':[],'complete':complete,'work':work}
                    activities[segment]=activity;messages.append(activity)
                    message_ordinals[activity['id']]=ordinal
                return activities[segment]
            for row in rows:
                item=json.loads(row['body']);kind=row['item_type'];turn=turns.get(row['turn_id'],{})
                ordinal=row['rollout_ordinal']
                complete=turn.get('status') in ('completed','failed','interrupted')
                if kind=='userMessage':
                    boundary=row['item_id']
                    text,attachments=user_content(item.get('content'),self.resources,thread['id'])
                    message={'id':row['item_id'],'sourceId':row['item_id'],'turnId':row['turn_id'],'role':'user','text':text[:40000],
                             'blocks':[{'kind':'paragraph','text':text[:40000]}] if text else [],'complete':True}
                    if attachments:message['attachments']=attachments
                    if row['created_at_ms']:message['timestamp']=datetime.fromtimestamp(row['created_at_ms']/1000).strftime('%H:%M')
                    if text or attachments:
                        messages.append(message);message_ordinals[message['id']]=ordinal
                elif kind=='agentMessage' and item.get('phase')!='commentary':
                    text=str(item.get('text') or '')[:40000]
                    if text:
                        if any(key[0]==row['turn_id'] for key in activities) or turn.get('status')=='inProgress' or isinstance(turn.get('duration_ms'),(int,float)):
                            activity=activity_for(row['turn_id'],turn,complete,ordinal)
                            if not complete:activity['work']['hasAnswer']=True
                        messages.append({'id':row['item_id'],'sourceId':row['item_id'],'turnId':row['turn_id'],'role':'assistant','text':text,
                                         'blocks':render(text),'complete':complete})
                        message_ordinals[row['item_id']]=ordinal
                else:
                    if kind=='agentMessage':
                        blocks=render(str(item.get('text') or '')[:8000],'commentary')
                    elif kind=='reasoning':
                        summary=item.get('summary')
                        text='\n\n'.join(s if isinstance(s,str) else str(s.get('text') or '')
                                        for s in summary if isinstance(s,(str,dict))) if isinstance(summary,list) else ''
                        blocks=render(text[:8000],'reasoning')
                    elif kind=='webSearch':
                        query=str(item.get('query') or '')[:1000]
                        blocks=[{'kind':'search','text':'搜索网页'+(' · '+query if query else ''),'channel':'activity'}]
                    else:
                        icon,running,done=ACTIVITY_LABELS[kind]
                        status=item.get('status')
                        text=running+'中' if status=='inProgress' and not complete else running+'失败' if status=='failed' else done
                        blocks=[{'kind':'operation','icon':icon,'text':text,'channel':'activity'}]
                    if blocks:activity_for(row['turn_id'],turn,complete,ordinal)['blocks'].extend(blocks)
            process_truncated=False
            latest_by_turn={segment[0]:activity for segment,activity in activities.items()}
            for segment,activity in activities.items():
                if latest_by_turn[segment[0]] is not activity:
                    activity['work'].pop('durationMs',None)
                    if activity['work']['status']=='working':activity['work']['status']='worked'
            for activity in activities.values():
                remaining=12000
                blocks=[]
                for block in reversed(activity['blocks']):
                    size=len(block['text'])+2
                    if size>remaining:
                        process_truncated=True
                        break
                    blocks.append(block);remaining-=size
                activity['blocks']=list(reversed(blocks))
                activity['text']='\n\n'.join(block['text'] for block in activity['blocks'])
            kept=[];budget=160000
            for message in reversed(messages[-80:]):
                if len(message['text'])>budget:break
                kept.append(message);budget-=len(message['text'])
            omitted = len(messages)>len(kept)
            cursor = turn_rows[HISTORY_TURNS-1]['rollout_ordinal'] if older else None
            if omitted and kept:
                cursor = message_ordinals[kept[-1]['id']]
            elif more_dialogue:
                cursor = first_dialogue
            generating=next(iter(turns.values()),{}).get('status')=='inProgress'
            return {'available':True,'title':thread['display_title'],'threadId':thread['id'],
                    'key':hashlib.sha256(str(observed['identity']).encode()).hexdigest()[:24],
                    'messages':list(reversed(kept)),'composer':observed.get('composer',{'available':False,'hasDraft':False}),
                    'generating':generating or bool(observed.get('generating')),
                    'queued':self.queued(thread['id']) or [
                        {'id':item['id'],'text':item['text']} for item in observed.get('queue',{}).get('items',[])],
                    'loadedOnly':False,'truncated':process_truncated or
                        older or sum(row['item_type'] not in ACTIVITY_LABELS for row in rows)>=240 or omitted,
                    **({'historyBefore': cursor} if cursor is not None else {}),
                    'source':'local-history','notice':''}
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,OverflowError):return None

    def queued(self, thread_id: str) -> list[dict]:
        path=self.database('queue')
        if not path:return []
        try:
            with closing(self.connect(path)) as db:
                rows=db.execute('SELECT id,payload_json FROM queued_items WHERE thread_id=? ORDER BY queue_order LIMIT 20',(thread_id,)).fetchall()
            result=[]
            for row in rows:
                payload=json.loads(row['payload_json'])
                for field in ('params','message','item'):
                    if isinstance(payload.get(field),dict):payload=payload[field]
                content=payload.get('input') or payload.get('inputs') or payload.get('content') or []
                text,attachments=user_content(content)
                if not text and isinstance(payload.get('text'),str):text=payload['text']
                if text or attachments:result.append({'id':row['id'],'text':text[:6000] or f'附件 · {len(attachments)} 个'})
            return result
        except (OSError,sqlite3.Error,ValueError,TypeError):return []

    def catalog(self, selected_title: str, account: tuple[str, str] | None = None) -> list[dict] | None:
        path=self.database('state')
        if not path:return None
        try:
            with closing(self.connect(path)) as db:
                projects=[dict(r) for r in db.execute('SELECT id,name FROM projects ORDER BY position')]
                roots=[dict(r) for r in db.execute('SELECT project_id,path FROM project_roots')]
                columns={r['name'] for r in db.execute('PRAGMA table_info(threads)')}
                source=',source' if 'source' in columns else ''
                if account and not {'creator_user_id', 'creator_account_id'} <= columns:
                    return None
                scope = ' AND creator_user_id=? AND creator_account_id=?' if account else ''
                rows=[dict(r) for r in db.execute("""SELECT id,coalesce(nullif(name,''),title) AS title,cwd,project_id,agent_path"""+source+"""
                    FROM threads WHERE archived=0 AND (agent_path IS NULL OR agent_path='/root')"""+scope+"""
                    ORDER BY coalesce(recency_at_ms,updated_at_ms,updated_at*1000) DESC LIMIT 800""", account or ())]
                rows=[row for row in rows if self.user_thread(row)]
            explicit={};legacy={}
            try:
                saved=json.loads((self.home/'.codex-global-state.json').read_text(encoding='utf-8'))
                explicit=saved.get('thread-project-assignments',{})
                for mapping in saved.get('app-server-project-id-by-legacy-project-id-by-host',{}).values():
                    if isinstance(mapping,dict):legacy.update(mapping)
            except (OSError,ValueError,TypeError):pass
            names={p['id']:p['name'] for p in projects};items=[]
            for p in projects:items.append({'id':'local-project-'+p['id'],'kind':'project','title':p['name'],
                                           'project':'local-project-'+p['id'],'projectTitle':p['name'],'expanded':False})
            for row in rows:
                project=row['project_id'];assignment=explicit.get(row['id'])
                if isinstance(assignment,dict) and assignment.get('projectKind')=='local':project=legacy.get(assignment.get('projectId'),project)
                if project not in names:
                    matches=[r['project_id'] for r in roots if ntpath.normcase(str(r['path']))==ntpath.normcase(str(row['cwd']))]
                    project=matches[0] if len(set(matches))==1 else ''
                group='local-project-'+project if project in names else ''
                items.append({'id':'local-thread-'+row['id'],'threadId':row['id'],'kind':'thread','title':row['title'],
                              'project':group,'projectTitle':names.get(project,''),'selected':row['title']==selected_title})
            if account:
                owned_projects = {row['project'] for row in items if row['kind'] == 'thread'}
                items = [row for row in items if row['kind'] != 'project' or row['id'] in owned_projects]
            return items
        except (OSError,sqlite3.Error,ValueError,TypeError):return None
