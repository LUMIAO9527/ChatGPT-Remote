"""Structured desktop records, isolated from the user's conversation databases."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from local_history import LocalHistory, markdown_blocks

@contextmanager
def fixture_db(path):
    with closing(sqlite3.connect(path)) as db:
        with db:yield db

THREAD='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.reader=LocalHistory(self.home)
        with fixture_db(self.home/'state_5.sqlite') as db:
            db.executescript('''CREATE TABLE threads(id TEXT,name TEXT,title TEXT,archived INTEGER,cwd TEXT,project_id TEXT,
                agent_path TEXT,recency_at_ms INTEGER,updated_at_ms INTEGER,updated_at INTEGER);
                CREATE TABLE projects(id TEXT,name TEXT,position INTEGER);
                CREATE TABLE project_roots(project_id TEXT,path TEXT);''')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)',(THREAD,'当前对话','original',0,r'D:\App',None,'/root',20,20,0))
            db.execute('INSERT INTO projects VALUES(?,?,?)',('project','App Lab',0))
            db.execute('INSERT INTO project_roots VALUES(?,?)',('project',r'D:\App'))
        with fixture_db(self.home/'thread_history_1.sqlite') as db:
            db.executescript('''CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,rollout_ordinal INTEGER,status TEXT,duration_ms INTEGER);
                CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,rollout_ordinal INTEGER,created_at_ms INTEGER,item_type TEXT,item_json TEXT);
                CREATE INDEX page ON thread_items(thread_id,rollout_ordinal);''')
            db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?)',(THREAD,'turn',1,'completed',65000))
        with fixture_db(self.home/'queue_1.sqlite') as db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,queue_order INTEGER)')
        self.observe={'ok':True,'selectedTitle':'当前对话','identity':'native-window-thread','generating':False,
                      'composer':{'available':True,'hasDraft':False}}

    def tearDown(self):self.tmp.cleanup()

    def add(self,ident,kind,**values):
        with fixture_db(self.home/'thread_history_1.sqlite') as db:
            ordinal=db.execute('SELECT count(*) FROM thread_items').fetchone()[0]
            db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?)',(THREAD,'turn',ident,ordinal,1700000000000,kind,
                json.dumps({'id':ident,'type':kind,**values},ensure_ascii=False)))

    def test_roles_phases_images_and_queue_are_native_fields(self):
        self.add('u','userMessage',content=[{'type':'text','text':'使用 ChatGPT Work'},{'type':'localImage','path':'private-image-path'}])
        self.add('p','agentMessage',phase='commentary',text='正在查找来源')
        self.add('r','reasoning',summary=['公开摘要'],content=['PRIVATE_ANALYSIS'])
        self.add('hidden','agentMessage',phase='analysis',text='PRIVATE_ANALYSIS')
        self.add('tool','commandExecution',command='PRIVATE_COMMAND',aggregatedOutput='PRIVATE_OUTPUT')
        self.add('p2','agentMessage',phase='commentary',text='来源已找到，正在核对')
        self.add('web','webSearch',query='公开查询')
        self.add('a','agentMessage',phase='final_answer',text='## 结果\n\n**重点**与[来源](https://example.com)。\n\n- 条目\n\n```py\nprint(1)\n```')
        with fixture_db(self.home/'queue_1.sqlite') as db:
            db.execute('INSERT INTO queued_items VALUES(?,?,?,?)',('queued',THREAD,json.dumps({'input':[{'type':'text','text':'稍后发送'}]}),1))
        result=self.reader.snapshot(self.observe)
        self.assertEqual(result['source'],'local-history');self.assertFalse(result['loadedOnly'])
        user,activity,answer=result['messages']
        self.assertEqual(user['role'],'user');self.assertEqual(user['text'],'使用 ChatGPT Work')
        self.assertEqual(user['attachments'][0]['kind'],'image')
        self.assertEqual(activity['phase'],'activity');self.assertEqual(activity['work'],{'status':'worked','durationMs':65000})
        self.assertEqual({m['turnId'] for m in result['messages']},{'turn'})
        self.assertEqual([(block['channel'],block['text']) for block in activity['blocks']],[
            ('commentary','正在查找来源'),('reasoning','公开摘要'),('activity','运行了命令'),
            ('commentary','来源已找到，正在核对'),('activity','搜索网页 · 公开查询')])
        self.assertEqual(activity['blocks'][-1]['kind'],'search')
        self.assertEqual([b['kind'] for b in answer['blocks']],['heading','paragraph','list','code'])
        self.assertEqual(answer['blocks'][1]['runs'][0],{'text':'重点','strong':True})
        self.assertEqual(result['queued'],[{'id':'queued','text':'稍后发送'}])
        serialized=json.dumps(result,ensure_ascii=False)
        for secret in ('PRIVATE_','private-image-path','稍后发送'):
            if secret=='稍后发送':self.assertNotIn(secret,json.dumps(result['messages'],ensure_ascii=False))
            else:self.assertNotIn(secret,serialized)

    def test_internal_reviews_with_null_agent_path_are_filtered_by_source(self):
        with fixture_db(self.home/'state_5.sqlite') as db:
            db.execute('ALTER TABLE threads ADD COLUMN source TEXT')
            db.execute('INSERT INTO threads(id,title,name,archived,cwd,agent_path,recency_at_ms,source) VALUES(?,?,?,?,?,?,?,?)',
                       (OTHER,'Guardian review','Guardian review',0,r'D:\App',None,30,json.dumps({'subagent':{'other':'guardian'}})))
        rows=self.reader.catalog('当前对话')
        self.assertFalse(any(row.get('threadId')==OTHER for row in rows))
        self.assertTrue(any(row.get('threadId')==THREAD for row in rows))
        self.assertIsNone(self.reader.resolve('Guardian review'))

    def test_ambiguous_title_and_missing_database_do_not_choose_another_chat(self):
        with fixture_db(self.home/'state_5.sqlite') as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)',(OTHER,'当前对话','other',0,r'D:\App',None,'/root',30,30,0))
        self.assertIsNone(self.reader.resolve('当前对话'))
        self.assertIsNone(self.reader.snapshot(self.observe))
        self.assertEqual(self.reader.resolve('',THREAD)['id'],THREAD)
        self.assertIsNone(LocalHistory(self.home/'absent').snapshot(self.observe))
        self.assertFalse((self.home/'absent').exists())

    def test_original_databases_are_opened_read_only(self):
        self.add('u','userMessage',content=[{'type':'text','text':'hello'}])
        files=list(self.home.glob('*.sqlite'));before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        self.reader.snapshot(self.observe);self.reader.catalog('当前对话')
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        with closing(self.reader.connect(files[0])) as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('CREATE TABLE forbidden(id INTEGER)')

    def test_active_turn_has_no_completed_answer_or_elapsed_time(self):
        with fixture_db(self.home/'thread_history_1.sqlite') as db:db.execute("UPDATE thread_turns SET status='inProgress',duration_ms=NULL")
        self.add('p','agentMessage',phase='commentary',text='处理中')
        self.add('t','commandExecution',status='inProgress',command='PRIVATE_COMMAND')
        running=self.reader.snapshot(self.observe)['messages'][0]
        self.assertEqual(running['work'],{'status':'working'})
        self.assertEqual(running['blocks'][-1]['text'],'运行命令中')
        self.add('a','agentMessage',phase='final_answer',text='尚未结束')
        result=self.reader.snapshot(self.observe)
        self.assertTrue(result['generating']);self.assertTrue(all(not m['complete'] for m in result['messages']))
        self.assertNotIn('duration',result['messages'][0])
        self.assertEqual(result['messages'][0]['work'],{'status':'working','hasAnswer':True})

    def test_native_work_status_does_not_become_user_or_answer_text(self):
        self.add('u','userMessage',content=[{'type':'text','text':'已处理 20秒'}])
        self.add('a','agentMessage',phase='final_answer',text='正文')
        user,activity,answer=self.reader.snapshot(self.observe)['messages']
        self.assertEqual(user['text'],'已处理 20秒');self.assertEqual(user['role'],'user')
        self.assertEqual(activity['blocks'],[]);self.assertEqual(activity['text'],'')
        self.assertEqual(activity['work'],{'status':'worked','durationMs':65000})
        self.assertEqual(answer['text'],'正文');self.assertNotIn('work',answer)
        with fixture_db(self.home/'thread_history_1.sqlite') as db:db.execute("UPDATE thread_turns SET status='interrupted'")
        self.assertEqual(self.reader.snapshot(self.observe)['messages'][1]['work']['status'],'stopped')

    def test_catalog_uses_saved_project_membership_and_never_exports_paths(self):
        (self.home/'.codex-global-state.json').write_text(json.dumps({
            'thread-project-assignments':{THREAD:{'projectKind':'local','projectId':'legacy'}},
            'app-server-project-id-by-legacy-project-id-by-host':{'local':{'legacy':'project'}}}),encoding='utf-8')
        items=self.reader.catalog('当前对话')
        self.assertEqual([r['kind'] for r in items],['project','thread'])
        self.assertEqual(items[1]['project'],items[0]['project']);self.assertTrue(items[1]['selected'])
        self.assertNotIn('D:',json.dumps(items))

    def test_latest_messages_and_item_id_survive_bounded_history(self):
        for i in range(260):self.add('u'+str(i),'userMessage',content=[{'type':'text','text':str(i)}])
        result=self.reader.snapshot(self.observe)
        self.assertTrue(result['truncated']);self.assertLessEqual(len(result['messages']),80)
        self.assertEqual(result['messages'][-1]['id'],'u259')
        self.assertEqual(result['messages'][-1]['sourceId'],'u259')

    def test_markdown_keeps_unfinished_code_and_does_not_make_script_links(self):
        blocks=markdown_blocks('[unsafe](javascript:alert(1))\n\n```\ntext')
        self.assertNotIn('runs',blocks[0]);self.assertEqual(blocks[-1],{'kind':'code','text':'text'})

    def test_user_interruption_splits_an_ongoing_process_in_native_order(self):
        self.add('u1','userMessage',content=[{'type':'text','text':'开始优化'}])
        self.add('p1','agentMessage',phase='commentary',text='第一段进度')
        self.add('tool1','commandExecution',status='completed')
        self.add('u2','userMessage',content=[{'type':'text','text':'还要优化速度'}])
        self.add('p2','agentMessage',phase='commentary',text='收到补充，继续处理')
        result=self.reader.snapshot(self.observe)['messages']
        self.assertEqual([m['role'] for m in result],['user','assistant','user','assistant'])
        self.assertIn('第一段进度',result[1]['text'])
        self.assertNotIn('收到补充',result[1]['text'])
        self.assertIn('收到补充',result[3]['text'])
        self.assertNotEqual(result[1]['id'],result[3]['id'])


if __name__=='__main__':unittest.main()
