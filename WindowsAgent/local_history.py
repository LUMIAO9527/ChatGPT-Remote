"""Bounded, read-only access to the desktop's structured local conversation data.

Only user-visible messages and reasoning summaries leave this adapter. Database
paths, tool arguments/outputs and private reasoning content are never exported.
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

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
INLINE = re.compile(r'(`+)(.+?)\1|\*\*(.+?)\*\*|__(.+?)__|\[([^\]\n]+)\]\((https?://[^\s)]+)\)|(?<!\*)\*([^*\n]+)\*(?!\*)')


def inline_runs(text: str) -> list[dict]:
    runs=[];offset=0
    for match in INLINE.finditer(text):
        if match.start()>offset:runs.append({'text':text[offset:match.start()]})
        if match[1]:run={'text':match[2],'inlineCode':True}
        elif match[3] or match[4]:run={'text':match[3] or match[4],'strong':True}
        elif match[5]:
            run={'text':match[5]};url=urlsplit(match[6])
            if url.scheme in ('http','https') and url.hostname and not url.username and not url.password:run['href']=match[6]
        else:run={'text':match[7],'emphasis':True}
        runs.append(run);offset=match.end()
    if offset<len(text):runs.append({'text':text[offset:]})
    return runs


def markdown_blocks(text: str, channel: str = '') -> list[dict]:
    blocks=[];paragraph=[];code=None;code_lines=[]
    def add(kind,value):
        block={'kind':kind,'text':value}
        if kind!='code':
            runs=inline_runs(value)
            if any(len(r)>1 for r in runs):block.update(text=''.join(r['text'] for r in runs),runs=runs)
        if channel:block['channel']=channel
        blocks.append(block)
    def flush():
        if paragraph:add('paragraph','\n'.join(paragraph));paragraph.clear()
    for line in text.replace('\r\n','\n').split('\n'):
        fence=re.match(r'^\s*(`{3,}|~{3,})(.*)$',line)
        if code:
            if fence and fence[1][0]==code[0] and len(fence[1])>=len(code):add('code','\n'.join(code_lines));code=None;code_lines=[]
            else:code_lines.append(line)
        elif fence:flush();code=fence[1]
        elif not line.strip():flush()
        elif re.match(r'^#{1,6}\s+',line):flush();add('heading',re.sub(r'^#{1,6}\s+','',line))
        elif re.match(r'^\s*(?:[-+*]|\d+[.)])\s+',line):flush();add('list',re.sub(r'^\s*(?:[-+*]|\d+[.)])\s+','',line))
        else:paragraph.append(line)
    flush()
    if code:add('code','\n'.join(code_lines))
    return blocks


def user_content(content) -> tuple[str,list[dict]]:
    text=[];attachments=[]
    for part in content if isinstance(content,list) else []:
        if not isinstance(part,dict):continue
        kind=part.get('type')
        if kind in ('text','input_text') and isinstance(part.get('text'),str):text.append(part['text'])
        elif kind in ('image','localImage','input_image'):
            attachments.append({'id':'attachment-'+str(len(attachments)),'kind':'image'})
        elif kind in ('file','localFile','input_file'):
            name=ntpath.basename(str(part.get('name') or part.get('path') or part.get('filename') or '文件'))
            attachments.append({'id':'attachment-'+str(len(attachments)),'kind':'file','name':name[:180]})
    return '\n'.join(text),attachments[:20]


class LocalHistory:
    def __init__(self, home: Path | None = None):
        self.home=Path(home) if home else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')

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

    def resolve(self, title: str, thread_id: str = '') -> dict | None:
        path=self.database('state')
        if not path or (not title and not UUID.fullmatch(thread_id)):return None
        with closing(self.connect(path)) as db:
            columns={r['name'] for r in db.execute('PRAGMA table_info(threads)')}
            if not {'id','title','archived'}<=columns:return None
            display="coalesce(nullif(name,''), title)" if 'name' in columns else 'title'
            fields='id, '+display+' as display_title'
            fields+=(',agent_path' if 'agent_path' in columns else '')+(',source' if 'source' in columns else '')
            if UUID.fullmatch(thread_id):rows=db.execute('SELECT '+fields+' FROM threads WHERE id=?',(thread_id,)).fetchall()
            else:rows=db.execute('SELECT '+fields+' FROM threads WHERE archived=0 AND '+display+'=? LIMIT 2',(title,)).fetchall()
            rows=[dict(row) for row in rows if self.user_thread(dict(row))]
            return {'id':rows[0]['id'],'display_title':rows[0]['display_title']} if len(rows)==1 else None

    def snapshot(self, observed: dict) -> dict | None:
        try:
            thread=self.resolve(str(observed.get('selectedTitle') or ''))
            path=self.database('thread_history')
            if not thread or not path:return None
            with closing(self.connect(path)) as db:
                turns={r['turn_id']:dict(r) for r in db.execute(
                    'SELECT turn_id,status,duration_ms FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 80',(thread['id'],))}
                # Reasoning summaries are public; the separate content field is
                # intentionally excluded before decoding any reasoning item.
                rows=list(db.execute("""SELECT item_id,turn_id,created_at_ms,item_type,
                    CASE WHEN item_type='reasoning' THEN json_object('type','reasoning','summary',json_extract(item_json,'$.summary')) ELSE item_json END AS body
                    FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','agentMessage','reasoning','webSearch')
                    AND (item_type!='reasoning' OR json_array_length(item_json,'$.summary')>0)
                    ORDER BY rollout_ordinal DESC LIMIT 240""",(thread['id'],)))
            if not rows:return None
            messages=[];activities={}
            def activity_for(turn_id,turn,complete):
                if turn_id not in activities:
                    status='working' if turn.get('status')=='inProgress' else ('stopped' if turn.get('status')=='interrupted' else 'worked' if complete else 'unknown')
                    work={'status':status}
                    if complete and isinstance(turn.get('duration_ms'),(int,float)):
                        work['durationMs']=max(0,round(turn['duration_ms']))
                    activity={'id':'activity-'+turn_id,'sourceId':'activity-'+turn_id,'turnId':turn_id,
                              'role':'assistant','phase':'activity','text':'','blocks':[],'complete':complete,'work':work}
                    activities[turn_id]=activity;messages.append(activity)
                return activities[turn_id]
            for row in reversed(rows):
                item=json.loads(row['body']);kind=row['item_type'];turn=turns.get(row['turn_id'],{})
                if kind=='agentMessage' and item.get('phase')=='analysis':continue
                complete=turn.get('status') in ('completed','failed','interrupted')
                if kind=='userMessage':
                    text,attachments=user_content(item.get('content'))
                    message={'id':row['item_id'],'sourceId':row['item_id'],'turnId':row['turn_id'],'role':'user','text':text[:40000],
                             'blocks':[{'kind':'paragraph','text':text[:40000]}] if text else [],'complete':True}
                    if attachments:message['attachments']=attachments
                    if row['created_at_ms']:message['timestamp']=datetime.fromtimestamp(row['created_at_ms']/1000).strftime('%H:%M')
                    if text or attachments:messages.append(message)
                elif kind=='agentMessage' and item.get('phase') not in ('analysis',):
                    text=str(item.get('text') or '')[:40000]
                    if text:
                        commentary=item.get('phase')=='commentary'
                        if turn.get('status')=='inProgress' or isinstance(turn.get('duration_ms'),(int,float)):
                            activity_for(row['turn_id'],turn,complete)
                        messages.append({'id':row['item_id'],'sourceId':row['item_id'],'turnId':row['turn_id'],'role':'assistant','text':text,
                                         'blocks':markdown_blocks(text),'complete':complete,
                                         **({'phase':'commentary'} if commentary else {})})
                else:
                    channel='activity';text=''
                    if kind=='agentMessage':text=str(item.get('text') or '')[:8000]
                    elif kind=='reasoning':
                        channel='reasoning'
                        text='\n\n'.join(s if isinstance(s,str) else str(s.get('text') or '') for s in item.get('summary',[]) if isinstance(s,(str,dict)))[:8000]
                    elif kind=='webSearch':text='正在网络上搜索 '+str(item.get('query') or '')[:1000] if item.get('query') else '正在搜索网页'
                    if not text:continue
                    activity=activity_for(row['turn_id'],turn,complete)
                    if sum(len(b['text']) for b in activity['blocks'])<12000:
                        activity['blocks'].extend([{'kind':'search','text':text,'channel':'activity'}] if kind=='webSearch' else markdown_blocks(text,channel))
                        activity['text']+=(('\n\n' if activity['text'] else '')+text)
            kept=[];budget=160000
            for message in reversed(messages[-80:]):
                if len(message['text'])>budget:break
                kept.append(message);budget-=len(message['text'])
            generating=next(iter(turns.values()),{}).get('status')=='inProgress'
            return {'available':True,'title':thread['display_title'],'threadId':thread['id'],
                    'key':hashlib.sha256(str(observed['identity']).encode()).hexdigest()[:24],
                    'messages':list(reversed(kept)),'composer':observed.get('composer',{'available':False,'hasDraft':False}),
                    'generating':generating or bool(observed.get('generating')),
                    'queued':self.queued(thread['id']) or [
                        {'id':item['id'],'text':item['text']} for item in observed.get('queue',{}).get('items',[])],
                    'loadedOnly':False,'truncated':len(rows)>=240 or len(messages)>len(kept),'source':'local-history','notice':''}
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

    def catalog(self, selected_title: str) -> list[dict] | None:
        path=self.database('state')
        if not path:return None
        try:
            with closing(self.connect(path)) as db:
                projects=[dict(r) for r in db.execute('SELECT id,name FROM projects ORDER BY position')]
                roots=[dict(r) for r in db.execute('SELECT project_id,path FROM project_roots')]
                columns={r['name'] for r in db.execute('PRAGMA table_info(threads)')}
                source=',source' if 'source' in columns else ''
                rows=[dict(r) for r in db.execute("""SELECT id,coalesce(nullif(name,''),title) AS title,cwd,project_id,agent_path"""+source+"""
                    FROM threads WHERE archived=0 AND (agent_path IS NULL OR agent_path='/root')
                    ORDER BY coalesce(recency_at_ms,updated_at_ms,updated_at*1000) DESC LIMIT 800""")]
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
            return items
        except (OSError,sqlite3.Error,ValueError,TypeError):return None
