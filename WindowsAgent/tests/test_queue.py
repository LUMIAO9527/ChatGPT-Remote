"""Queue actions remain tied to a fresh, observed native conversation."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from conversation import ConversationReader
from windows_control import TargetError


def native():
    return {'ok':True,'identity':'window|chat','paused':True,'hasDraft':False,
            'items':[{'id':'row-a','text':'待处理消息','actions':[
                {'id':'delete-a','operation':'delete','enabled':True},
                {'id':'edit-a','operation':'edit','enabled':True}]}],
            'actions':[{'id':'resume-a','operation':'resume','enabled':True}]}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.reader=ConversationReader()
        self.calls=[]
        self.raw=native()
        def call(**command):
            self.calls.append(command)
            if command['operation']=='queue':return copy.deepcopy(self.raw)
            if command.get('action')=='delete':self.raw['items']=[]
            if command.get('action')=='resume':self.raw['paused']=False
            return {'ok':True}
        self.reader._call=call

    def test_native_snapshot_is_opaque_and_never_exports_raw_identity(self):
        queue=self.reader.queue(42)
        self.assertNotIn('identity',queue)
        self.assertEqual(queue['items'][0]['text'],'待处理消息')
        self.assertTrue(queue['paused'])

    def test_delete_is_invoked_once_and_replay_is_rejected(self):
        queue=self.reader.queue(42)
        self.reader.queue_action(42,queue['key'],'delete-a')
        with self.assertRaises(TargetError):
            self.reader.queue_action(42,queue['key'],'delete-a')
        invokes=[x for x in self.calls if x['operation']=='queue_invoke']
        self.assertEqual(len(invokes),1)
        self.assertEqual(invokes[0]['identity'],'window|chat')
        self.assertEqual(invokes[0]['item'],'row-a')
        self.assertEqual(invokes[0]['text'],'待处理消息')

    def test_another_window_unknown_action_and_expired_catalog_never_invoke(self):
        queue=self.reader.queue(42)
        for hwnd,target in [(43,'delete-a'),(42,'unknown')]:
            with self.assertRaises(TargetError):self.reader.queue_action(hwnd,queue['key'],target)
        at,hwnd,raw=self.reader.queue_catalogs[queue['key']]
        self.reader.queue_catalogs[queue['key']]=(at-121,hwnd,raw)
        with self.assertRaises(TargetError):self.reader.queue_action(42,queue['key'],'delete-a')
        self.assertFalse(any(x['operation']=='queue_invoke' for x in self.calls))

    def test_unknown_native_outcome_is_never_replayed(self):
        queue=self.reader.queue(42)
        self.reader._call=lambda **_: {'ok':False,'reason':'queue pending'}
        with self.assertRaises(TargetError):self.reader.queue_action(42,queue['key'],'delete-a')
        with self.assertRaises(TargetError):self.reader.queue_action(42,queue['key'],'delete-a')

    def test_edit_returns_the_actual_native_draft_revision(self):
        queue=self.reader.queue(42)
        original=self.reader._call
        self.reader._call=lambda **c: ({'ok':True,'edited':True,'identity':'window|chat','text':'原生完整消息','title':'测试'}
                                      if c['operation']=='queue_invoke' else original(**c))
        result=self.reader.queue_action(42,queue['key'],'edit-a')
        self.assertEqual(result['queueDraft']['text'],'原生完整消息')
        self.assertTrue(result['queueDraft']['revision'])
        self.assertTrue(result['queueDraft']['canClear'])

    def test_resume_has_no_fabricated_message_target(self):
        queue=self.reader.queue(42)
        self.reader.queue_action(42,queue['key'],'resume-a')
        invoke=next(x for x in self.calls if x['operation']=='queue_invoke')
        self.assertEqual(invoke['item'],'')
        self.assertEqual(invoke['action'],'resume')


if __name__=='__main__':unittest.main()
