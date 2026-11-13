"""Text extraction boundaries, incremental updates, and control framing."""
import base64
import io
import json
from pathlib import Path
import struct
import subprocess
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversation import ConversationReader, parse_snapshot, conversation_event, draft_snapshot
from control_channel import WebSocket, ChannelClosed
from windows_control import WindowsBackend, TargetError, InputError, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE


def masked(payload, opcode=1, final=True):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    size = len(payload)
    header = bytes([(128 if final else 0) | opcode, 128 | size]) if size < 126 else bytes([(128 if final else 0) | opcode, 254]) + struct.pack('!H', size)
    mask = b'abcd'
    return header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        # Tests of UI snapshots must never read the operator's account files
        # or make authenticated network requests.
        catalog=Mock()
        catalog.enrich.side_effect=lambda raw:raw
        self.catalog_patch=patch('conversation.ModelCatalog',return_value=catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def test_model_catalog_serializes_menu_reads_with_background_polling(self):
        from concurrent.futures import ThreadPoolExecutor
        import hashlib
        reader=ConversationReader();entered=threading.Event();calls=[]
        root={'ok':True,'identity':'chat','menu':False,'context':'','items':[
            {'id':'model','title':'GPT Fixture','kind':'model','enabled':True}]}
        power={'ok':True,'identity':'chat','menu':True,'context':'power','category':'model','items':[
            {'id':'models','title':'选择模型','kind':'model-list','enabled':True}]}
        models={'ok':True,'identity':'chat','menu':True,'context':'models','category':'model','items':[
            {'id':'one','title':'GPT Fixture','kind':'option','enabled':True,'selected':True}]}
        def call(**request):
            entered.set()
            calls.append((request['operation'], (request.get('item') or {}).get('kind')))
            self.assertNotIn('discover',request)
            if request['operation']=='composer':return root
            return {'ok':True,'next':power if request['item']['kind']=='model' else models}
        reader._call=call
        backend=WindowsBackend.__new__(WindowsBackend);backend.lock=threading.RLock();backend.mode='window'
        backend.tracker=SimpleNamespace(epoch=7,current_id=42)
        backend.frozen_info_locked=lambda:{'active':False}
        backend._resolve_target=lambda:42;backend._reader=lambda:reader
        backend._rect=Mock(side_effect=AssertionError('catalog must not restore the target'))
        backend._focus=Mock(side_effect=AssertionError('catalog must not activate the target'))
        backend._key=Mock(side_effect=AssertionError('catalog must not press any keys'))
        command={'kind':'composer','operation':'catalog','epoch':7,
                 'conversationKey':hashlib.sha256(b'chat').hexdigest()[:24]}
        with ThreadPoolExecutor(max_workers=1) as pool:
            with reader.lock:
                result=pool.submit(backend.action,command)
                self.assertFalse(entered.wait(.05),'catalog touched the worker while a background read owned it')
            catalog=result.result(timeout=2)
        self.assertEqual(calls,[('composer',None),('composer_invoke','model'),('composer_invoke','model-list')])
        self.assertEqual(catalog['catalog']['models']['items'][0]['title'],'GPT Fixture')
        command['conversationKey']='old-chat'
        with self.assertRaisesRegex(TargetError,'电脑对话已变化'):
            backend.action(command)
        self.assertEqual(calls[-1],('composer',None))

    def test_composer_dismiss_reads_through_native_close_animation_without_repeating_escape(self):
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.u32=SimpleNamespace(GetForegroundWindow=lambda:42)
        backend._key=Mock()
        reader=Mock()
        reader.composer_options.side_effect=[{'key':'menu','menu':True},{'key':'root','menu':False}]
        with patch('windows_control.time.sleep'):
            result=backend._dismiss_composer_menu(reader,42,'menu')
        self.assertEqual(result,{'key':'root','menu':False})
        self.assertEqual(backend._key.call_count,2)

    def test_local_project_catalog_keeps_the_native_new_chat_target_private(self):
        reader=ConversationReader();reader.history=Mock()
        reader.history.resolve.return_value={'id':'existing'}
        reader.history.catalog.return_value=[{'id':'local-p','kind':'project','title':'App Lab','project':'local-p','projectTitle':'App Lab'}]
        calls=[];identities=iter(['before','after'])
        def call(**request):
            calls.append(request)
            if request['operation']=='navigation':return {'ok':True,'identity':'nav','items':[
                {'id':'selected','kind':'thread','title':'Current','selected':True},
                {'id':'new','kind':'new','title':'在 App Lab 中开始新聊天','project':'native-p','projectTitle':'App Lab'}]}
            if request['operation']=='identity':return {'ok':True,'identity':next(identities)}
            return {'ok':True}
        reader._call=call;snapshot=reader.navigation(1)
        new=next(row for row in snapshot['items'] if row['kind']=='new')
        self.assertEqual(new['project'],'local-p');self.assertNotIn('nativeProject',new)
        with patch('conversation.time.sleep'):reader.navigate(1,snapshot['key'],'new',False)
        self.assertEqual(next(row for row in calls if row['operation']=='navigate')['project'],'native-p')

    def test_indexed_thread_navigation_uses_one_native_route(self):
        reader=ConversationReader();thread='11111111-1111-4111-8111-111111111111';reader.history=Mock()
        reader._history_account=lambda:('user','account')
        reader.history.resolve.side_effect=[{'id':thread},{'id':'other'}]
        reader.catalogs['catalog']=(0,1,'native',[{'id':'item','kind':'thread','threadId':thread,'title':'title'}])
        observations=iter([{'ok':True,'title':'other','identity':'old'}, {'ok':True,'title':'title','identity':'new'}])
        reader._call=lambda **_: next(observations)
        with patch('conversation.os.startfile') as opened,patch('conversation.time.sleep'):
            self.assertEqual(reader.navigate(1,'catalog','item',False),'thread')
            opened.assert_called_once_with('codex://threads/'+thread)

    def test_first_indexed_thread_switch_returns_the_backend_refresh_contract(self):
        reader=ConversationReader();thread='11111111-1111-4111-8111-111111111111';reader.history=Mock()
        reader._history_account=lambda:('user','account')
        reader.history.resolve.side_effect=[{'id':thread},{'id':'other'},{'id':thread},{'id':thread}]
        reader.catalogs['catalog']=(0,1,'native',[{'id':'item','kind':'thread','threadId':thread,'title':'title'}])
        observations=iter([{'ok':True,'title':'other','identity':'old'}, {'ok':True,'title':'title','identity':'new'},
                           {'ok':True,'title':'title','identity':'new'}])
        reader._call=lambda **_: next(observations)
        reader.read=Mock(return_value={'key':'new','threadId':thread,'messages':[]})
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.lock=threading.RLock();backend.mode='window'
        backend.tracker=SimpleNamespace(epoch=7,current_id=1,status='ready')
        backend.frozen_info_locked=lambda:{'active':False}
        backend._rect=lambda:(0,0,900,600)
        backend._reader=lambda:reader
        backend.read_conversation=Mock()
        command={'kind':'navigate','navigationKey':'catalog','targetId':'item','expanded':False}
        with patch('conversation.os.startfile') as opened,patch('conversation.time.sleep'):
            first=backend.action({**command,'epoch':7})
            self.assertEqual(first,{'conversation':{'key':'new','epoch':8,'threadId':thread,'messages':[],
                'status':'ready','mode':'window','frozen':{'active':False}}})
            self.assertEqual(backend.tracker.epoch,8)
            second=backend.action({**command,'epoch':8})
            self.assertEqual(second,{'navigationUnchanged':True})
            self.assertEqual(backend.tracker.epoch,8)
            opened.assert_called_once_with('codex://threads/'+thread)
            backend.read_conversation.assert_not_called()
    def test_structured_paragraphs_do_not_join_when_their_screen_rectangles_overlap(self):
        result = parse_snapshot({'nodes': [
            {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True},
            {'type': 'Text', 'name': '第一段 ', 'blockId': 'p1', 'rect': {'top': 0, 'bottom': 20}},
            {'type': 'Text', 'name': '强调', 'strong': True, 'blockId': 'p1', 'rect': {'top': 0, 'bottom': 20}},
            {'type': 'Text', 'name': '第二段', 'blockId': 'p2', 'rect': {'top': 0, 'bottom': 20}}]})
        blocks = result['messages'][0]['blocks']
        self.assertEqual([b['text'] for b in blocks], ['第一段 强调', '第二段'])
        self.assertEqual(blocks[0]['runs'], [{'text': '第一段 '}, {'text': '强调', 'strong': True}])
        self.assertFalse(any('_source' in b for b in blocks))

    def test_accessible_full_list_text_keeps_words_missing_from_styled_children(self):
        result = parse_snapshot({'nodes': [
            {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True},
            {'type': 'ListItem', 'kind': 'list', 'name': '完整内容：后半句不能丢失', 'complete': True,
             'runs': [{'text': '完整内容', 'strong': True}, {'text': '：后半句不能丢失'}]},
            {'type': 'Hyperlink', 'name': '来源', 'blockId': 'p2', 'href': 'https://example.com/docs'},
            {'type': 'Text', 'name': '危险地址', 'blockId': 'p3', 'href': 'javascript:alert(1)'}]})
        blocks = result['messages'][0]['blocks']
        self.assertEqual(blocks[0]['text'], '完整内容：后半句不能丢失')
        self.assertEqual(''.join(r['text'] for r in blocks[0]['runs']), blocks[0]['text'])
        self.assertEqual(blocks[1]['runs'][0]['href'], 'https://example.com/docs')
        self.assertNotIn('runs', blocks[2])

    def test_native_activity_after_a_user_message_is_separate_and_groups_consecutive_updates(self):
        result = parse_snapshot({'generating': True, 'nodes': [
            {'type': 'Text', 'name': '你说：', 'marker': True},
            {'type': 'Text', 'name': '请继续'},
            {'type': 'Text', 'name': '读取文件', 'role': 'assistant', 'channel': 'activity'},
            {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True, 'channel': 'activity'},
            {'type': 'Text', 'name': '检查结果', 'channel': 'activity'},
            {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True},
            {'type': 'Text', 'name': '最终正文'}]})
        self.assertEqual([m['role'] for m in result['messages']], ['user', 'assistant', 'assistant'])
        self.assertEqual(result['messages'][0]['text'], '请继续')
        self.assertEqual(result['messages'][1]['phase'], 'activity')
        self.assertEqual(result['messages'][1]['text'], '读取文件\n\n检查结果')
        self.assertEqual(result['messages'][2]['text'], '最终正文')

    def test_attachment_only_messages_and_explicit_completion_are_preserved(self):
        result=parse_snapshot({'generating':True,'nodes':[
            {'type':'Text','name':'你说：','marker':True},
            {'type':'Attachment','kind':'image','id':'img'},
            {'type':'Attachment','kind':'image','id':'img'},
            {'type':'Text','name':'ChatGPT 说：','marker':True},
            {'type':'Text','name':'最终正文'},
            {'type':'Status','complete':True},
            {'type':'Edit','name':'composer'}]})
        self.assertEqual(len(result['messages']),2)
        self.assertEqual(result['messages'][0]['text'],'')
        self.assertEqual(result['messages'][0]['attachments'],[{'id':'img','kind':'image'}])
        self.assertTrue(result['messages'][1]['complete'])
        streaming=parse_snapshot({'generating':True,'nodes':[
            {'type':'Text','name':'ChatGPT 说：','marker':True},{'type':'Text','name':'未完'}]})
        self.assertFalse(streaming['messages'][0]['complete'])

    def test_permission_catalog_filters_information_and_separates_description(self):
        reader=ConversationReader()
        reader._call=lambda **_: {'ok':True,'identity':'a','context':'permissions','menu':True,'category':'permission','items':[
            {'id':'help','title':'了解更多','kind':'option','enabled':True},
            {'id':'docs','title':'Learn more','kind':'option','enabled':True},
            {'id':'permission','title':'请求批准 编辑外部文件和使用互联网时始终询问','kind':'option','enabled':True}]}
        result=reader.composer_options(1)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['category'],'permission')
        self.assertEqual(result['items'][0]['label'],'请求批准')
        self.assertEqual(result['items'][0]['description'],'编辑外部文件和使用互联网时始终询问')
        with self.assertRaises(TargetError):reader.composer_action(1,result['key'],'help')
        tools=reader._remember_options(1,{'ok':True,'category':'tools','items':[
            {'id':'documents','title':'文档','enabled':True},{'id':'help-tool','title':'帮助','enabled':True}]})
        self.assertEqual([item['title'] for item in tools['items']],['文档','帮助'])

    def test_prompt_options_are_bound_to_one_readback_and_never_dismissed_as_a_menu(self):
        reader = ConversationReader()
        raw = {
            'ok': True, 'identity': 'current-chat', 'context': 'prompt:dialog-id:revision',
            'menu': False, 'category': 'prompt',
            'prompt': {'title': 'Approval', 'text': 'Read the fixture directory', 'approval': True},
            'items': [{'id': 'allow', 'title': 'Allow once', 'enabled': True},
                      {'id': 'deny', 'title': 'Deny', 'enabled': True}],
        }
        invoked = []

        def call(**request):
            if request['operation'] == 'composer':
                return raw
            invoked.append(request)
            return {'ok': True, 'next': raw}

        reader._call = call
        observed = reader.composer_options(7)
        self.assertEqual(observed['prompt']['text'], 'Read the fixture directory')
        with self.assertRaises(TargetError):
            reader.composer_action(8, observed['key'], 'allow')
        with self.assertRaises(TargetError):
            reader.composer_action(7, observed['key'], None, dismiss=True)
        result = reader.composer_action(7, observed['key'], 'allow', refresh=True)['options']
        self.assertEqual(invoked[0]['context'], raw['context'])
        self.assertTrue(all(not option['enabled'] for option in result['items']))
        self.assertTrue(all(not option['enabled'] for option in reader.composer_options(7)['items']))
        with self.assertRaises(TargetError):
            reader.composer_action(7, observed['key'], 'deny')
        self.assertEqual(len(invoked), 1)

    def test_queue_and_process_metadata_never_become_reply_body(self):
        result=parse_snapshot({'nodes':[
            {'type':'Text','name':'你说：','marker':True},
            {'type':'Text','name':'正文里提到排队中和思考，应照常显示。'},
            {'type':'Text','name':'ChatGPT 说：','marker':True},
            {'type':'Text','name':'读取状态','channel':'reasoning','rect':{'top':10,'bottom':25}},
            {'type':'Text','name':'正式回复','rect':{'top':10,'bottom':25}},
            {'type':'Text','name':'待发送一','queued':True,'queueId':'q1'},
            {'type':'Text','name':'待发送二','queued':True,'queueId':'q2'},
            {'type':'Text','name':'16:00','timestamp':True},
            {'type':'Edit','name':'手机或电脑草稿'}]})
        self.assertEqual(result['queued'],[{'id':'q1','text':'待发送一'},{'id':'q2','text':'待发送二'}])
        self.assertEqual(result['messages'][1]['blocks'],[
            {'kind':'paragraph','text':'读取状态','channel':'reasoning'},
            {'kind':'paragraph','text':'正式回复'}])
        self.assertEqual(result['messages'][1]['timestamp'],'16:00')
        self.assertNotIn('待发送',result['messages'][1]['text'])
        self.assertIn('排队中',result['messages'][0]['text'])

    def test_queue_only_updates_are_sent_without_replacing_messages(self):
        one={'key':'a','epoch':2,'revision':'1','messages':[], 'queued':[]}
        two={**one,'revision':'2','queued':[{'id':'q','text':'later'}]}
        delta=conversation_event(one,two)
        self.assertFalse(delta['replace']);self.assertEqual(delta['messages'],[])
        self.assertEqual(delta['queued'],two['queued'])

    def test_composer_invocation_returns_and_registers_next_catalog(self):
        reader=ConversationReader();calls=[]
        root={'ok':True,'identity':'chat','context':'','menu':False,'items':[{'id':'model','title':'Model','kind':'model','enabled':True}]}
        menu={'ok':True,'identity':'chat','context':'menu','menu':True,'items':[{'id':'one','title':'One','kind':'option','enabled':True}]}
        def call(**request):
            calls.append(request)
            return root if request['operation']=='composer' else {'ok':True,'next':menu}
        reader._call=call;snapshot=reader.composer_options(123)
        result=reader.composer_action(123,snapshot['key'],'model',refresh=True)
        self.assertTrue(result['options']['menu'])
        self.assertIn(result['options']['key'],reader.option_catalogs)
        self.assertEqual([c['operation'] for c in calls],['composer','composer_invoke'])
        self.assertTrue(calls[-1]['refresh'])

    def test_composer_catalog_rejects_unknown_disabled_and_cross_window_targets(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'window|chat-a','context':'','menu':False,'items':[
            {'id':'model','title':'Current model','kind':'model','enabled':True},
            {'id':'disabled','title':'Unavailable','kind':'tools','enabled':False}]}
        def call(**request):
            calls.append(request);return raw if request['operation']=='composer' else {'ok':True}
        reader._call=call;snapshot=reader.composer_options(123)
        for hwnd,key,target in [(123,'unknown','model'),(124,snapshot['key'],'model'),(123,snapshot['key'],'disabled'),(123,snapshot['key'],'arbitrary')]:
            with self.subTest(target=target,hwnd=hwnd):
                with self.assertRaises(TargetError):reader.composer_action(hwnd,key,target)
        self.assertEqual(len(calls),1)
        reader.composer_action(123,snapshot['key'],'model')
        self.assertEqual(calls[-1]['identity'],'window|chat-a')
        self.assertEqual(calls[-1]['item']['title'],'Current model')
        with self.assertRaises(TargetError):reader.composer_action(123,snapshot['key'],'model')
        self.assertEqual(len(calls),2)

    def test_uncertain_composer_invocation_consumes_its_catalog_without_retry(self):
        reader=ConversationReader();calls=[]
        def call(**request):
            calls.append(request)
            if request['operation']=='composer':return {'ok':True,'identity':'chat','context':'menu','menu':True,'items':[{'id':'m','title':'Model','kind':'option','enabled':True}]}
            raise TargetError('timeout')
        reader._call=call;snapshot=reader.composer_options(123)
        for attempt in range(2):
            with self.assertRaises(TargetError):reader.composer_action(123,snapshot['key'],'m')
        self.assertEqual([c['operation'] for c in calls],['composer','composer_invoke'])

    def test_menu_dismissal_and_surface_reads_require_the_observed_menu(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'chat','context':'menu','menu':True,'items':[{'id':'s','title':'Strength','kind':'surface','enabled':True}]}
        def call(**request):
            calls.append(request)
            if request['operation']=='composer':return raw
            return {'ok':True,'escape':True,'rect':{'x':10,'y':20,'width':100,'height':20},'crop':{'x':0,'y':0,'width':200,'height':60}}
        reader._call=call;snapshot=reader.composer_options(123)
        self.assertEqual(reader.composer_surface(123,snapshot['key'],'s')['rect']['width'],100)
        self.assertEqual(calls[-1]['context'],'menu')
        self.assertTrue(reader.composer_action(123,snapshot['key'],None,dismiss=True))
        with self.assertRaises(TargetError):reader.composer_surface(123,snapshot['key'],'s')
        raw['menu']=False;raw['context']='';snapshot=reader.composer_options(123)
        with self.assertRaises(TargetError):reader.composer_action(123,snapshot['key'],None,dismiss=True)

    def test_native_control_taps_map_only_inside_the_revalidated_control(self):
        backend=WindowsBackend.__new__(WindowsBackend);positions=[];clicks=[]
        backend.lock=threading.RLock();backend.mode='window';backend.tracker=SimpleNamespace(epoch=7,current_id=123)
        backend.frozen_info_locked=lambda:{'active':False};backend._rect=lambda:(100,200,600,400);backend._focus=lambda _:None
        rect={'x':150,'y':300,'width':200,'height':20}
        backend._reader=lambda:SimpleNamespace(composer_surface=lambda *_,**__: {'rect':rect})
        backend.u32=SimpleNamespace(GetForegroundWindow=lambda:123,SetCursorPos=lambda x,y:positions.append((x,y)) or True)
        backend._mouse_input=lambda event:clicks.append(event)
        command={'kind':'composer','operation':'tap','epoch':7,'optionsKey':'key','targetId':'s','x':0.5,'y':0.5}
        backend.action(command);self.assertEqual(positions,[(250,310)]);self.assertEqual(len(clicks),2)
        for value in (-0.1,1.1,float('nan'),True,'0.5'):
            with self.assertRaises(InputError):backend.action({**command,'x':value})
        rect['x']=90
        with self.assertRaises(TargetError):backend.action(command)
        self.assertEqual(len(clicks),2)

    def test_window_pointer_uses_the_hit_child_coordinates_and_rejects_other_windows(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.mode = 'window'
        backend._process_path = lambda pid: r'C:\App\ChatGPT.exe' if pid == 21 else r'C:\App\Other.exe'
        hit_pid = 21
        root = 10
        posts = []

        def process_id(handle, result):
            result._obj.value = 21 if handle == 10 else hit_pid
            return 1

        def client_point(handle, result):
            self.assertEqual(handle, 11)
            result._obj.x -= 300
            result._obj.y -= 160
            return True

        def client_rect(handle, result):
            self.assertEqual(handle, 11)
            result._obj.right, result._obj.bottom = 500, 300
            return True

        backend.u32 = SimpleNamespace(
            GetWindowThreadProcessId=process_id, WindowFromPoint=lambda _: 11,
            GetAncestor=lambda *_: root, ScreenToClient=client_point, GetClientRect=client_rect,
            PostMessageW=lambda *message: posts.append(message) or True,
        )
        bounds = (100, 60, 600, 400)
        command = {'x': .5, 'y': .5, 'operation': 'left', 'pointer_target': (10, bounds)}
        backend._post_window_pointer(10, bounds, command)
        packed = 100 | (100 << 16)
        self.assertEqual(posts, [(11, 0x200, 0, packed), (11, 0x201, 1, packed), (11, 0x202, 0, packed)])
        hit_pid = 22
        with self.assertRaises(TargetError):
            backend._post_window_pointer(10, bounds, command)
        hit_pid = 21
        root = 12
        with self.assertRaises(TargetError):
            backend._post_window_pointer(10, bounds, command)
        self.assertEqual(len(posts), 3)

    def test_draft_revision_is_bound_to_full_text_and_conversation(self):
        source={'ok':True,'identity':'window-a','text':'第一行\nemoji 😄'}
        snapshot=draft_snapshot(source)
        self.assertTrue(snapshot['hasDraft'])
        self.assertTrue(snapshot['canClear'])
        self.assertNotEqual(snapshot['revision'],draft_snapshot({**source,'text':'new draft'})['revision'])
        other=draft_snapshot({**source,'identity':'window-b'})
        self.assertNotEqual(snapshot['revision'],other['revision'])
        self.assertNotEqual(snapshot['conversationKey'],other['conversationKey'])
        truncated=draft_snapshot({**source,'text':'x'*32001})
        self.assertEqual(len(truncated['text']),32000)
        self.assertFalse(truncated['canClear'])
        self.assertFalse(draft_snapshot({**source,'text':''})['hasDraft'])
        with self.assertRaises(TargetError):draft_snapshot({'ok':False})

    def test_clear_checks_revision_before_mutation_and_passes_exact_text(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'window-a','text':'Desktop draft\n你好 😄'}
        def call(**request):
            calls.append(request)
            return raw if request['operation']=='draft' else {'ok':True}
        reader._call=call
        snapshot=reader.draft(123)
        calls.clear()
        reader.clear_draft(123,snapshot['conversationKey'],snapshot['revision'],for_send=True)
        self.assertEqual([r['operation'] for r in calls],['draft','clear_draft'])
        self.assertEqual(calls[-1]['text'],raw['text'])
        self.assertTrue(calls[-1]['forSend'])
        calls.clear();raw['text']='Edited on computer'
        with self.assertRaises(TargetError) as error:reader.clear_draft(123,snapshot['conversationKey'],snapshot['revision'])
        self.assertEqual(error.exception.code,'draft_changed')
        self.assertEqual([r['operation'] for r in calls],['draft'])
        calls.clear();raw['identity']='window-b';raw['text']='Desktop draft\n你好 😄'
        with self.assertRaises(TargetError):reader.clear_draft(123,snapshot['conversationKey'],snapshot['revision'])
        self.assertEqual([r['operation'] for r in calls],['draft'])

    def test_uncertain_clear_is_never_retried(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'window-a','text':'Keep mobile text'}
        snapshot=draft_snapshot(raw)
        def call(**request):
            calls.append(request)
            if request['operation']=='draft':return raw
            raise TargetError('Timed out')
        reader._call=call
        with self.assertRaisesRegex(TargetError,'结果未确认'):
            reader.clear_draft(123,snapshot['conversationKey'],snapshot['revision'])
        self.assertEqual([r['operation'] for r in calls],['draft','clear_draft'])

    def test_message_metadata_does_not_erase_times_written_in_the_body(self):
        result=parse_snapshot({'nodes':[
            {'type':'Text','name':'你说：','marker':True},
            {'type':'Text','name':'12:30'},
            {'type':'Text','name':'11:20','timestamp':True},
            {'type':'Text','name':'ChatGPT 说：','marker':True},
            {'type':'Text','name':'同意 12:30 见面'}]})
        self.assertEqual(result['messages'][0]['text'],'12:30')
        self.assertEqual(result['messages'][0]['timestamp'],'11:20')
        self.assertNotIn('timestamp',result['messages'][1])

    @unittest.skipUnless(sys.platform=='win32','Windows PowerShell is required')
    def test_actual_uia_draft_normalizer_distinguishes_the_prompt_from_content(self):
        script_path=str(Path(__file__).parents[1]/'uia_reader.ps1').replace("'","''")
        source="""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('SCRIPT_PATH',[ref]$tokens,[ref]$errors)
$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Normalize-Message','Get-ComposerDraft')},$true) | ForEach-Object {Invoke-Expression $_.Extent.Text}
$prompt='accessible prompt'
$r=@(
 ((Get-ComposerDraft ($prompt+"`n") $true) -ceq ''),
 ((Get-ComposerDraft $prompt $false) -ceq $prompt),
 ((Get-ComposerDraft ('  '+[char]0x200B+"`r`n") $false) -ceq ''),
 ((Get-ComposerDraft 'real draft' $false) -ceq 'real draft'),
 ((Get-ComposerDraft $null $false) -eq $null),
 ((Normalize-Message "first`r`nsecond`n") -ceq "first`nsecond")
)
ConvertTo-Json -InputObject $r -Compress
""".replace('SCRIPT_PATH',script_path)
        command=base64.b64encode(source.encode('utf-16-le')).decode()
        shell=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
        result=subprocess.run([shell,'-NoProfile','-NonInteractive','-EncodedCommand',command],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True]*6)

    def test_navigation_revalidates_catalog_and_resolves_only_known_items(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'window-root','items':[
            {'id':'project','kind':'project','title':'Demo','project':'group','projectTitle':'Demo','expanded':False}]}
        def call(**request):
            calls.append(request)
            if request['operation']=='navigation':return raw
            return {'ok':raw['items'][0]['title']==request.get('title')}
        reader._call=call
        nav=reader.navigation(123)
        with self.assertRaises(TargetError):reader.navigate(123,'old-key','project',True)
        self.assertFalse(any(c['operation']=='navigate' for c in calls))
        calls.clear()
        raw['items'].append({'id':'another','kind':'thread','title':'Loaded asynchronously'})
        self.assertEqual(reader.navigate(123,nav['key'],'project',True),'project')
        self.assertEqual([c['operation'] for c in calls],['navigate'])
        self.assertEqual(calls[-1]['target'],'project')
        self.assertEqual(calls[-1]['identity'],'window-root')
        self.assertEqual(calls[-1]['project'],'group')
        self.assertEqual(calls[-1]['projectTitle'],'Demo')
        self.assertEqual(calls[-1]['title'],'Demo')
        self.assertTrue(calls[-1]['expanded'])
        calls.clear()
        raw['items'][0]['title']='A different conversation'
        with self.assertRaises(TargetError):reader.navigate(123,nav['key'],'project',True)
        self.assertEqual([c['operation'] for c in calls],['navigate'])

    def test_navigation_catalog_survives_reopen_and_switch_with_live_native_validation(self):
        reader=ConversationReader();calls=[]
        raw={'ok':True,'identity':'same-window','items':[
            {'id':'new-alpha','kind':'new','title':'在 Alpha 中开始新聊天','project':'alpha','projectTitle':'Alpha'}]}
        def call(**request):
            calls.append(request)
            if request['operation']=='navigation':return raw
            if request['operation']=='identity':return {'ok':True,'identity':'before' if len(calls)<4 else 'after'}
            return {'ok':True}
        reader._call=call;nav=reader.navigation(123)
        for _ in range(2):self.assertEqual(reader.navigate(123,nav['key'],'new-alpha',True),'new')
        self.assertEqual(sum(c['operation']=='navigation' for c in calls),1)
        self.assertEqual(sum(c['operation']=='navigate' for c in calls),2)
        self.assertEqual(nav['source'],reader.navigation(123)['source'])

    def test_native_duration_metadata_never_enters_user_body_or_pending_queue(self):
        result=parse_snapshot({'generating':False,'nodes':[
            {'type':'Text','name':'你说：','marker':True},{'type':'Text','name':'请继续'},
            {'type':'Text','name':'已处理 1分钟 27秒','duration':True,'queued':True},
            {'type':'Text','name':'ChatGPT 说：','marker':True},{'type':'Text','name':'完成。'}]})
        self.assertEqual(result['messages'][0]['text'],'请继续')
        self.assertNotIn('duration',result['messages'][0])
        self.assertEqual(result['messages'][1]['duration'],'已处理 1分钟 27秒')
        self.assertEqual(result['messages'][1]['text'],'完成。');self.assertEqual(result['queued'],[])
        body=parse_snapshot({'nodes':[{'type':'Text','name':'你说：','marker':True},{'type':'Text','name':'已处理 1分钟 27秒'}]})
        self.assertEqual(body['messages'][0]['text'],'已处理 1分钟 27秒')
        standalone=parse_snapshot({'nodes':[{'type':'Text','name':'你说：','marker':True},
            {'type':'Text','name':'请继续'},{'type':'Text','name':'已处理 2分钟','duration':True}]})
        self.assertEqual(standalone['messages'][1]['role'],'assistant')
        self.assertEqual(standalone['messages'][1]['phase'],'activity')
        self.assertEqual(standalone['messages'][1]['text'],'')

    def test_reading_excludes_sidebar_and_existing_draft(self):
        raw = {'identity': 'window|conversation', 'title': 'test',
               'composer': {'available': True, 'hasDraft': True}, 'nodes': [
                   {'type': 'Text', 'name': 'sidebar private title'},
                   {'type': 'Text', 'name': '你说：', 'marker': True},
                   {'type': 'Text', 'name': '你好'},
                   {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True},
                   {'type': 'Text', 'name': '正文'},
                   {'type': 'Text', 'name': '你说：'},  # Quoted content is not a role marker.
                   {'type': 'ListItem', 'name': 'duplicate parent', 'leaf': False},
                   {'type': 'Text', 'name': 'list child'},
                   {'type': 'Edit', 'name': 'composer'},
                   {'type': 'Text', 'name': 'unsent private draft'}]}
        result = parse_snapshot(raw)
        self.assertEqual([m['role'] for m in result['messages']], ['user', 'assistant'])
        text = '\n'.join(m['text'] for m in result['messages'])
        for excluded in ('sidebar private title', 'duplicate parent', 'unsent private draft'):
            self.assertNotIn(excluded, text)
        self.assertIn('你说：', result['messages'][1]['text'])
        self.assertTrue(result['composer']['hasDraft'])

    def test_empty_new_chat_is_available_but_unrecognized_page_is_not(self):
        self.assertTrue(parse_snapshot({'composer': {'available': True}, 'nodes': []})['available'])
        self.assertFalse(parse_snapshot({'nodes': [{'type': 'Text', 'name': 'account menu'}]})['available'])
        self.assertFalse(parse_snapshot({'composer': {'available': True}, 'nodes': [
            {'type': 'Text', 'name': '你说：'}, {'type': 'Text', 'name': 'unstructured content'}]})['available'])

    def test_inline_fragments_full_list_items_and_code_keep_their_structure(self):
        result = parse_snapshot({'nodes': [
            {'type': 'Text', 'name': 'ChatGPT 说：', 'marker': True},
            {'type': 'Text', 'name': '已更新到 ', 'rect': {'top': 10, 'bottom': 30}},
            {'type': 'Hyperlink', 'name': 'v0.6.0', 'rect': {'top': 10, 'bottom': 30}},
            {'type': 'Text', 'name': '，可以使用。', 'rect': {'top': 10, 'bottom': 30}},
            {'type': 'ListItem', 'name': '完整项目：包括粗体以外的正文', 'complete': True, 'leaf': False},
            {'type': 'Text', 'name': 'print("one")', 'code': True, 'rect': {'top': 70, 'bottom': 90}},
            {'type': 'Text', 'name': 'print("two")', 'code': True, 'rect': {'top': 95, 'bottom': 115}},
            {'type': 'Edit', 'name': 'unsent draft'}]})
        self.assertEqual(result['messages'][0]['blocks'], [
            {'kind': 'paragraph', 'text': '已更新到 v0.6.0，可以使用。'},
            {'kind': 'list', 'text': '完整项目：包括粗体以外的正文'},
            {'kind': 'code', 'text': 'print("one")\nprint("two")'}])

    def test_direct_send_binds_the_editor_and_never_injects_desktop_input(self):
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.lock=threading.RLock();backend.mode='window'
        backend.tracker=SimpleNamespace(epoch=7,current_id=123)
        backend.frozen_info_locked=lambda:{'active':False}
        backend._rect=lambda:(0,0,900,600)
        backend._focus=Mock(side_effect=AssertionError('Direct send must not take focus'))
        backend.u32=SimpleNamespace(GetForegroundWindow=lambda:123)
        backend._unicode_text=Mock(side_effect=AssertionError('No system keyboard text'))
        backend._key_chord=Mock(side_effect=AssertionError('No system clipboard paste'))
        reader=SimpleNamespace(fill_input=Mock(),submit=Mock(return_value={'state':'sent'}))
        backend._reader=lambda:reader
        for method,text in [('button','你好\n第二行'),('CTRL+ENTER','1. first\n2. second'),('ENTER','长文本'*500),('button','')]:
            reader.fill_input.reset_mock();reader.submit.reset_mock()
            result=backend.action({'kind':'message','text':text,'submit':True,'attachmentsOnly':not text,
                'epoch':7,'conversationKey':'observed-key','submitMethod':method})
            reader.fill_input.assert_called_once_with(123,'observed-key',text,submit=True)
            reader.submit.assert_called_once_with(123,'observed-key',text)
            self.assertEqual(result,{'submission':{'state':'sent'}})
        reader.fill_input.side_effect=TargetError('different draft','draft_exists')
        reader.submit.reset_mock()
        with self.assertRaises(TargetError):
            backend.action({'kind':'message','text':'new','submit':True,'epoch':7,'conversationKey':'observed-key'})
        reader.submit.assert_not_called()

    def test_replacement_and_fill_are_serialized_under_the_window_lock(self):
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.lock=threading.RLock();backend.mode='window'
        backend.tracker=SimpleNamespace(epoch=7,current_id=123)
        backend.frozen_info_locked=lambda:{'active':False};backend._rect=lambda:(0,0,900,600)
        backend.u32=SimpleNamespace(GetForegroundWindow=lambda:123)
        calls=[]
        def record(kind,*args,**kwargs):
            self.assertTrue(backend.lock._is_owned());calls.append((kind,args,kwargs))
        reader=SimpleNamespace(clear_draft=lambda *a,**kw:record('clear',*a,**kw),
            fill_input=lambda *a,**kw:record('fill',*a,**kw),submit=lambda *a:record('submit',*a))
        backend._reader=lambda:reader
        backend.action({'kind':'message','text':'replacement','submit':True,'epoch':7,
            'conversationKey':'chat','draftRevision':'seen'})
        self.assertEqual([row[0] for row in calls],['clear','fill','submit'])
        self.assertEqual(calls[0][2],{'for_send':True,'replacement_text':'replacement'})

    def test_direct_fill_stops_while_the_user_is_typing_in_another_window(self):
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.tracker=SimpleNamespace(current_id=123)
        backend.u32=SimpleNamespace(GetForegroundWindow=lambda:456)
        backend._local_input_recent=lambda:True
        reader=SimpleNamespace(fill_input=Mock(),submit=Mock());backend._reader=lambda:reader
        with self.assertRaises(TargetError) as raised:
            backend._compose_message({'conversationKey':'chat','submit':True},'phone','button')
        self.assertEqual(raised.exception.code,'local_input_active')
        reader.fill_input.assert_not_called();reader.submit.assert_not_called()

    def test_attachment_focus_loss_does_not_recapture_foreground_or_paste(self):
        backend=WindowsBackend.__new__(WindowsBackend)
        backend.lock=threading.RLock();backend.mode='window';backend.tracker=SimpleNamespace(epoch=7)
        backend._resolve_target=lambda:123;backend.frozen_info_locked=lambda:{'active':False}
        foreground=[123]
        backend._focus=Mock()
        def read_focus(hwnd,key):
            if reader.attachment_focus.call_count==2:foreground[0]=456
            return {'identity':key}
        reader=SimpleNamespace(attachment_focus=Mock(side_effect=read_focus),confirm_attachments=Mock())
        backend._reader=lambda:reader;backend.u32=SimpleNamespace(GetForegroundWindow=lambda:foreground[0])
        backend._key_chord=Mock()
        with patch('attachments.paste_files',side_effect=lambda paths,paste:paste()):
            with self.assertRaises(TargetError) as raised:backend.attach_files([Path('phone.jpg')],'chat',7)
        self.assertEqual(raised.exception.code,'attachment_not_started')
        backend._key_chord.assert_not_called();reader.confirm_attachments.assert_not_called()
        self.assertEqual(reader.attachment_focus.call_count,2)

    def test_fill_rejects_changed_conversation_and_reads_native_errors(self):
        reader=ConversationReader();identity='stable';key=parse_snapshot({'identity':identity})['key'];calls=[]
        def call(**request):
            calls.append(request)
            if request['operation']=='identity':return {'ok':True,'identity':identity}
            return {'ok':False,'reason':'draft exists'}
        reader._call=call
        with self.assertRaises(TargetError) as raised:reader.fill_input(123,key,'phone',True)
        self.assertEqual(raised.exception.code,'draft_exists')
        self.assertEqual([r['operation'] for r in calls],['identity','fill'])
        calls.clear()
        with self.assertRaises(TargetError):reader.fill_input(123,'other','phone',True)
        self.assertNotIn('fill',[r['operation'] for r in calls])

    def test_identical_recovered_draft_can_send_without_clearing_a_stale_revision(self):
        reader=ConversationReader();operations=[]
        raw={'ok':True,'identity':'stable','text':'a\u00a0b\r\n第二行\n'}
        def call(**request):
            operations.append(request['operation'])
            return raw
        reader._call=call
        key=draft_snapshot(raw)['conversationKey']
        reader.clear_draft(123,key,'old-revision',for_send=True,replacement_text='a b\n第二行')
        self.assertEqual(operations,['draft'])
        with self.assertRaises(TargetError):
            reader.clear_draft(123,key,'old-revision',for_send=True,replacement_text='不同内容')

    def test_delta_sends_changes_and_resets_on_conversation_or_epoch_switch(self):
        one = {'key': 'a', 'epoch': 2, 'revision': '1', 'messages': [
            {'id': 'm0', 'role': 'user', 'text': 'question'},
            {'id': 'm1', 'role': 'assistant', 'text': 'partial'}]}
        two = {**one, 'revision': '2', 'messages': [one['messages'][0], {**one['messages'][1], 'text': 'partial answer'}]}
        delta = conversation_event(one, two)
        self.assertFalse(delta['replace'])
        self.assertEqual([m['id'] for m in delta['messages']], ['m1'])
        self.assertEqual(delta['order'], ['m0', 'm1'])
        self.assertIsNone(conversation_event(two, two))
        self.assertTrue(conversation_event(two, {**two, 'key': 'b', 'revision': '3'})['replace'])
        self.assertTrue(conversation_event(two, {**two, 'epoch': 3, 'revision': '3'})['replace'])

    def test_incremental_reply_and_progress_send_only_the_changed_content(self):
        text = '历史正文很长' * 500
        first_block = {'kind': 'paragraph', 'text': text}
        progress = {'kind': 'paragraph', 'channel': 'reasoning', 'text': '正在核对'}
        first = {'key': 'a', 'epoch': 7, 'instance': 'live', 'revision': 'r1',
                 'generating': True, 'prompt': {'key': 'old'}, 'messages': [
                     {'id': 'answer', 'role': 'assistant', 'text': text,
                      'blocks': [first_block], 'complete': False},
                     {'id': 'work', 'role': 'assistant', 'phase': 'activity', 'text': progress['text'],
                      'blocks': [progress], 'complete': False}]}
        second = {**first, 'revision': 'r2', 'messages': [
            {**first['messages'][0], 'text': text + '新增😀',
             'blocks': [{**first_block, 'text': text + '新增😀'}]},
            {**first['messages'][1], 'text': '核对完成', 'blocks': [
                {'kind': 'operation', 'channel': 'activity', 'text': '核对完成'}], 'complete': True}]}
        second.pop('prompt')
        delta = conversation_event(first, second, incremental=True)
        self.assertEqual(delta['baseRevision'], 'r1')
        self.assertEqual(delta['unset'], ['prompt'])
        self.assertNotIn('order', delta)
        self.assertNotIn('meta', delta)
        self.assertEqual(delta['patches'][0], {'id': 'answer', 'textAppend': '新增😀',
            'blockAppend': '新增😀', 'blocks': {'keep': 1, 'items': []}})
        self.assertEqual(delta['patches'][1], {'id': 'work', 'text': '核对完成', 'fields': {'complete': True},
            'blocks': {'keep': 0, 'items': second['messages'][1]['blocks']}})
        full = conversation_event(first, second)
        self.assertLess(len(json.dumps(delta)), len(json.dumps(full)) / 20)
        self.assertEqual(first['messages'][0]['blocks'], [first_block])
        self.assertTrue(conversation_event(second, {**second, 'instance': 'restarted'}, incremental=True)['replace'])

    def test_native_active_commentary_requires_completion_before_copy(self):
        def marker(name):
            return {'type': 'Text', 'marker': True, 'name': name}
        def paragraph(name):
            return {'type': 'Text', 'name': name}
        nodes = [marker('你说：'), paragraph('旧问题'), marker('ChatGPT 说：'), paragraph('旧回复'),
                 marker('你说：'), paragraph('当前问题'), marker('ChatGPT 说：'), paragraph('进度一'),
                 marker('ChatGPT 说：'), paragraph('进度二')]
        current = parse_snapshot({'nodes': nodes, 'generating': True})['messages']
        self.assertNotIn('replyComplete', current[1])
        self.assertFalse(current[3]['replyComplete'])
        self.assertFalse(current[4]['replyComplete'])
        finished = parse_snapshot({'nodes': nodes, 'generating': False})['messages']
        self.assertFalse(finished[3]['replyComplete'])
        self.assertNotIn('replyComplete', finished[4])

    def test_repeat_native_reads_reuse_one_tree_for_text_queue_and_prompt(self):
        reader = ConversationReader()
        calls = []
        raw = {'ok': True, 'identity': 'native', 'title': 'Window',
               'nodes': [{'type': 'Text', 'name': '你说：', 'marker': True}, {'type': 'Text', 'name': 'question'}]}
        def read(**request):
            calls.append(request['operation'])
            if request['operation'] == 'observe':
                return {'ok': True, 'identity': 'native'}
            return raw
        reader._call = read
        reader._history_account = lambda: ('user','account')
        reader.history.snapshot = lambda observed, **_: None
        with patch('conversation.time.monotonic', side_effect=[1.0, 1.5, 1.5]):
            first = reader.read(42)
            second = reader.read(42)
        self.assertEqual(calls, ['observe', 'read', 'read'])
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(second['messages'][0]['text'], 'question')

    def test_button_timeout_is_reported_as_uncertain_and_never_retried(self):
        reader = ConversationReader()
        operations = []
        def call(**request):
            operations.append(request['operation'])
            if request['operation'] == 'identity': return {'ok': True, 'identity': 'stable'}
            raise TargetError('reader timeout')
        reader._call = call
        key = parse_snapshot({'identity': 'stable'})['key']
        with self.assertRaises(TargetError) as raised:
            reader.submit(123, key, 'once')
        self.assertEqual(raised.exception.code, 'send_pending')
        self.assertEqual(operations, ['identity', 'submit'])
        self.assertIsNone(reader.cached)

    def test_paste_validation_rejects_changed_text_before_any_submission(self):
        reader = ConversationReader()
        operations = []
        def call(**request):
            operations.append(request['operation'])
            if request['operation'] == 'identity':
                return {'ok': True, 'identity': 'stable'}
            return {'ok': False, 'reason': 'draft changed'}
        reader._call = call
        with self.assertRaises(TargetError) as raised:
            reader.confirm_input(123, parse_snapshot({'identity':'stable'})['key'], 'mobile text', True)
        self.assertEqual(raised.exception.code, 'send_not_started')
        self.assertEqual(operations, ['identity', 'input_ready'])

    def test_transient_identity_read_is_checked_again_before_preparing_input(self):
        reader = ConversationReader()
        replies = iter([{'ok':False}, {'ok':True, 'identity':'stable'}, {'ok':True}])
        operations = []
        def call(**request):
            operations.append(request['operation'])
            return next(replies)
        reader._call = call
        key = parse_snapshot({'identity':'stable'})['key']
        self.assertEqual(reader.prepare(123, key), key)
        self.assertEqual(operations, ['identity', 'identity', 'prepare'])

    def test_navigation_rebinds_changed_runtime_id_before_a_single_invocation(self):
        reader=ConversationReader();calls=[]
        item={'kind':'project','title':'Demo','project':'group','projectTitle':'Demo'}
        old={'ok':True,'identity':'root','items':[dict(item,id='old')]}
        nav=reader._remember_navigation(123,old)
        def call(**request):
            calls.append(request)
            if request['operation']=='navigation':return dict(old,items=[dict(item,id='fresh')])
            if request['target']=='old':return {'ok':False,'reason':'navigation changed'}
            return {'ok':True}
        reader._call=call
        self.assertEqual(reader.navigate(123,nav['key'],'old',True),'project')
        self.assertEqual([c['operation'] for c in calls],['navigate','navigation','navigate'])
        self.assertEqual(calls[-1]['target'],'fresh')

    def test_native_send_returns_after_one_readback_and_reports_invocation(self):
        reader=ConversationReader();calls=[]
        def call(**request):
            calls.append(request['operation'])
            if request['operation']=='identity':return {'ok':True,'identity':'before-first-message'}
            return {'ok':True,'invoked':True,'confirmed':False,'submission':'submitted'}
        reader._call=call
        key=parse_snapshot({'identity':'before-first-message'})['key']
        self.assertEqual(reader.submit(123,key,'继续'),{'state':'submitted'})
        self.assertEqual(calls,['identity','submit'])

    def test_native_activity_label_is_not_added_to_user_body(self):
        result=parse_snapshot({'nodes':[
            {'type':'Text','name':'你说：','marker':True,'sourceId':'user-native-1'},
            {'type':'Text','name':'活动'},
            {'type':'Status','activity':True},
            {'type':'Text','name':'ChatGPT 说：','marker':True,'sourceId':'assistant-native-1'},
            {'type':'Text','name':'收到。'}]})
        self.assertEqual(result['messages'][0]['text'],'活动')
        self.assertEqual(result['messages'][0]['sourceId'],'user-native-1')
        self.assertEqual(result['messages'][1]['text'],'收到。')

    def test_capture_does_not_hold_input_lock_and_rejects_changed_target(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.lock = threading.RLock()
        backend.mode = 'window'
        backend.tracker = SimpleNamespace(epoch=7)
        backend._resolve_target = lambda: 123
        started, resume = threading.Event(), threading.Event()
        class Image:
            width, height = 900, 600
            def save(self, stream, **_): stream.write(b'image')
        def capture(*_):
            started.set()
            resume.wait(2)
            return Image()
        backend._grab_window = capture
        failures = []
        def run():
            try: backend.capture_meta()
            except Exception as exc: failures.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(2))
        acquired = backend.lock.acquire(timeout=0.1)
        try:
            self.assertTrue(acquired, 'capture blocked input/state changes')
            backend.tracker.epoch += 1
        finally:
            if acquired: backend.lock.release()
            resume.set()
            worker.join(3)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], TargetError)

    def test_multiline_unicode_is_one_batch_with_soft_breaks_and_one_submit(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        calls = []
        def send(count, inputs, _):
            calls.append([(inputs[i].u.ki.wVk, inputs[i].u.ki.wScan, inputs[i].u.ki.dwFlags) for i in range(count)])
            return count
        backend.u32 = SimpleNamespace(SendInput=send)
        backend._unicode_text('一\r\n😄\n二', submit=True)
        self.assertEqual(len(calls), 1)
        events = calls[0]
        self.assertEqual([e[0] for e in events if e[0]], [0x10,0x0D,0x0D,0x10,0x10,0x0D,0x0D,0x10,0x0D,0x0D])
        self.assertEqual(events[-2:], [(0x0D,0,0),(0x0D,0,KEYEVENTF_KEYUP)])
        units = [scan for vk, scan, flags in events if flags == KEYEVENTF_UNICODE]
        decoded = b''.join(unit.to_bytes(2, 'little') for unit in units).decode('utf-16-le')
        self.assertEqual(decoded, '一😄二')
        calls.clear()
        backend._unicode_text('Ctrl send', submit=True, submit_key='CTRL+ENTER')
        self.assertEqual(calls[0][-4:], [(0x11,0,0),(0x0D,0,0),
                                      (0x0D,0,KEYEVENTF_KEYUP),(0x11,0,KEYEVENTF_KEYUP)])


class ControlFramingTests(unittest.TestCase):
    def test_rfc_handshake_and_invalid_key(self):
        self.assertEqual(WebSocket.accept_key('dGhlIHNhbXBsZSBub25jZQ==', '13'), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
        for key, version in [('bad', '13'), (base64.b64encode(b'a').decode(), '13'), ('dGhlIHNhbXBsZSBub25jZQ==', '12')]:
            with self.assertRaises(ValueError): WebSocket.accept_key(key, version)

    def test_fragmented_text_and_interleaved_ping(self):
        data = masked(b'{"id":', final=False) + masked(b'ping', opcode=9) + masked(b'7}', opcode=0)
        output = io.BytesIO()
        channel = WebSocket(io.BytesIO(data), output)
        self.assertEqual(channel.receive(), {'id': 7})
        self.assertEqual(output.getvalue(), b'\x8a\x04ping')

    def test_rejects_unmasked_oversized_and_invalid_frames(self):
        cases = [b'\x81\x02{}', b'\x81\xff' + struct.pack('!Q', 65537),
                 masked(b'{}', opcode=0), masked(b'\xff'), masked(b'[]'),
                 masked(b'ping', opcode=9, final=False)]
        for data in cases:
            with self.subTest(data=data[:10]):
                with self.assertRaises(ChannelClosed): WebSocket(io.BytesIO(data), io.BytesIO()).receive()


if __name__ == '__main__':
    unittest.main()
