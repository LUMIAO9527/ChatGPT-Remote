"""Delivery acknowledgement, duplicate suppression and observation recovery."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from delivery import DeliveryStore, evidence
from windows_control import TargetError


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.snapshot={'key':'chat','epoch':7,'messages':[{'role':'user','text':'继续','sourceId':'old'}],'queued':[]}
        self.calls=[]
        self.store=DeliveryStore(self.action,lambda:copy.deepcopy(self.snapshot))
        self.command={'kind':'message','submit':True,'text':'继续','epoch':7,'conversationKey':'chat','messageId':'send_test_1234567890'}

    def tearDown(self):
        self.store.close()

    def action(self,command,token):
        self.calls.append(command)
        self.snapshot['messages'].append({'role':'user','text':command['text'],'sourceId':'new'})
        return {'ok':True,'submission':{'state':'submitted'}}

    def wait_for(self,expected):
        until=time.monotonic()+2
        while time.monotonic()<until:
            result=self.store.get('phone',self.command['messageId'])
            if result and result['state']==expected:return result
            time.sleep(.005)
        self.fail(f'delivery did not reach {expected}')

    def test_concurrent_repeated_request_and_status_queries_send_exactly_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.store.submit(self.command,'phone'),range(8)))
        self.wait_for('sent')
        self.assertEqual(len(self.calls),1)
        self.assertTrue(all(r['id']==self.command['messageId'] for r in results))
        self.assertIsNone(self.store.get('another-phone',self.command['messageId']))
        self.assertNotIn('text',self.store.get('phone',self.command['messageId']))
        with self.assertRaises(TargetError) as raised:
            self.store.submit(dict(self.command,text='different'),'phone')
        self.assertEqual(raised.exception.code,'delivery_conflict')

    def test_lost_native_ack_is_reconciled_with_visible_message(self):
        def action(command,token):
            self.action(command,token)
            raise TargetError('read timed out','send_pending')
        self.store.action=action
        self.store.submit(self.command,'phone')
        self.wait_for('sent');self.assertEqual(len(self.calls),1)

    def test_keyboard_send_without_an_immediate_result_uses_message_readback(self):
        def action(command,token):
            self.action(command,token)
            return None
        self.store.action=action;self.store.submit(self.command,'phone')
        self.wait_for('sent');self.assertEqual(len(self.calls),1)

    def test_old_identical_text_never_confirms_delayed_new_send(self):
        started=threading.Event()
        def action(command,token):
            self.calls.append(command);started.set()
            raise TargetError('read timed out','send_pending')
        self.store.action=action
        self.store.submit(self.command,'phone');self.assertTrue(started.wait(1))
        self.store.observe(copy.deepcopy(self.snapshot))
        self.assertEqual(self.store.get('phone',self.command['messageId'])['state'],'confirming')
        self.snapshot['messages'].append({'role':'user','text':'继续','sourceId':'new'})
        self.store.observe(copy.deepcopy(self.snapshot))
        self.wait_for('sent');self.assertEqual(len(self.calls),1)

    def test_queue_is_confirmed_separately_from_messages(self):
        def action(command,token):
            self.calls.append(command);self.snapshot['queued']=[{'id':'q-new','text':'继续'}]
            return {'ok':True}
        self.store.action=action;self.store.submit(self.command,'phone')
        self.wait_for('queued');self.assertEqual(len(self.snapshot['messages']),1)

    def test_first_message_identity_change_is_legitimate(self):
        self.snapshot['messages']=[]
        def action(command,token):
            self.action(command,token);self.snapshot['key']='created-chat'
            return {'ok':True}
        self.store.action=action;self.store.submit(self.command,'phone');self.wait_for('sent')

    def test_changed_conversation_and_old_queue_cannot_acknowledge(self):
        after=copy.deepcopy(self.snapshot);after['key']='another';after['messages']=[{'role':'user','text':'继续','sourceId':'other'}]
        self.assertEqual(evidence(self.snapshot,after,'继续'),'')
        after=copy.deepcopy(self.snapshot);after['epoch']=8;after['messages'].append({'role':'user','text':'继续'})
        self.assertEqual(evidence(self.snapshot,after,'继续'),'')
        self.snapshot['queued']=[{'id':'q','text':'继续'}]
        self.assertEqual(evidence(self.snapshot,copy.deepcopy(self.snapshot),'继续'),'')

    def test_known_pre_invocation_failure_is_actionable_and_can_be_queried(self):
        def action(command,token):raise TargetError('未发送：电脑发送按钮不可用','send_not_started')
        self.store.action=action;self.store.submit(self.command,'phone')
        result=self.wait_for('failed');self.assertEqual(result['code'],'send_not_started')
        self.assertEqual(len(self.calls),0)

    def test_changed_target_before_execution_does_not_type(self):
        self.command['conversationKey']='old-chat'
        self.store.submit(self.command,'phone');self.wait_for('failed');self.assertFalse(self.calls)

    def test_attachment_only_requires_a_new_native_attachment_message(self):
        before={'key':'chat','epoch':7,'messages':[]}
        after=dict(before,messages=[{'role':'user','text':'','attachments':[{'id':'a','kind':'file'}]}])
        self.assertEqual(evidence(before,after,'',True),'sent')
        self.assertEqual(evidence(before,after,'',False),'')

    def test_missing_request_is_sealed_before_enabling_a_safe_manual_retry(self):
        receipt=self.store.resolve('phone',self.command['messageId'],self.store.instance)
        self.assertEqual(receipt['state'],'failed');self.assertEqual(receipt['code'],'send_not_received')
        self.assertEqual(self.store.submit(self.command,'phone')['state'],'failed')
        self.assertFalse(self.calls)

    def test_another_server_instance_cannot_claim_an_old_send_failed(self):
        self.assertEqual(self.store.resolve('phone',self.command['messageId'],'previous-startup')['state'],'confirming')
        self.assertFalse(self.store.records)
        with self.assertRaises(TargetError):self.store.submit(dict(self.command,deliveryInstance='previous-startup'),'phone')
        self.assertFalse(self.calls)
