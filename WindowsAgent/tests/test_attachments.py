import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from attachments import UploadStore,UploadError,MAX_FILE,ClipboardBridge,ClipboardUnavailable
from agent import AgentState,MockBackend


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=UploadStore(self.root/'uploads')
    def tearDown(self):self.tmp.cleanup()

    @unittest.skipUnless(os.name == 'nt', 'Windows clipboard helper')
    def test_clipboard_helper_reuses_one_process_and_restores_after_each_paste(self):
        process = Mock()
        process.poll.return_value = None
        process.stdin = io.StringIO()
        process.stdout = io.StringIO('{"ready":true}\n{"restored":true}\n' * 2)
        bridge = ClipboardBridge()
        calls = []
        with patch('attachments.subprocess.Popen', return_value=process) as launcher:
            try:
                bridge.warm()
                bridge.paste({'text': '中文\n😀'}, lambda: calls.append('first'))
                bridge.paste({'text': 'second'}, lambda: calls.append('second'))
                self.assertEqual(calls, ['first', 'second'])
                self.assertEqual(launcher.call_count, 1)
                self.assertEqual(Path(launcher.call_args.kwargs['env']['TEMP']).anchor, Path(__file__).resolve().anchor)
                self.assertTrue(bridge.last_restored)
                lines = process.stdin.getvalue().splitlines()
                self.assertEqual(json.loads(lines[0]), {'text': '中文\n😀'})
                self.assertEqual(lines[1::2], ['done', 'done'])
            finally:
                bridge.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows clipboard helper')
    def test_clipboard_is_restored_when_validation_rejects_a_paste(self):
        process = Mock()
        process.poll.return_value = None
        process.stdin = io.StringIO()
        process.stdout = io.StringIO('{"ready":true}\n{"restored":true}\n')
        bridge = ClipboardBridge()
        callback = Mock(side_effect=UploadError('input changed'))
        with patch('attachments.subprocess.Popen', return_value=process):
            try:
                with self.assertRaises(UploadError):
                    bridge.paste({'text': 'retained'}, callback)
                callback.assert_called_once()
                self.assertTrue(bridge.last_restored)
                self.assertTrue(process.stdin.getvalue().endswith('done\n'))
            finally:
                bridge.close()

    def test_names_limits_ownership_and_expired_cleanup(self):
        for name in ('../file.txt','C:\\private.txt','CON.txt','x.','..','a\n.txt'):
            with self.subTest(name=name),self.assertRaises(UploadError):self.store.receive('phone',name,3,io.BytesIO(b'abc'))
        with self.assertRaises(UploadError):self.store.receive('phone','large.bin',MAX_FILE+1,io.BytesIO())
        item=self.store.receive('phone','中文.txt',3,io.BytesIO(b'abc'))
        self.assertNotIn('path',item);self.assertNotIn('owner',item)
        with self.assertRaises(UploadError):self.store.resolve('other',[item['id']])
        stored=self.store.resolve('phone',[item['id']])[0]
        self.assertEqual(stored['path'].read_bytes(),b'abc')
        stored['created']=time.time()-86500;self.store.cleanup()
        self.assertFalse(stored['path'].exists())

    def test_clipboard_snapshot_failure_keeps_the_helper_usable_and_never_pastes(self):
        process=Mock();process.poll.return_value=None;process.stdin=io.StringIO()
        process.stdout=io.StringIO('{"ready":false,"stage":"snapshot"}\n{"ready":true}\n{"restored":true}\n')
        bridge=ClipboardBridge();callback=Mock()
        with patch('attachments.subprocess.Popen',return_value=process) as launch:
            try:
                with self.assertRaises(ClipboardUnavailable):bridge.paste({'text':'first'},callback)
                callback.assert_not_called()
                bridge.paste({'text':'second'},callback);callback.assert_called_once();launch.assert_called_once()
                self.assertTrue(bridge.last_restored)
            finally:bridge.close()

    def test_a_callback_failure_is_not_reclassified_as_clipboard_unavailable(self):
        process=Mock();process.poll.return_value=None;process.stdin=io.StringIO()
        process.stdout=io.StringIO('{"ready":true}\n{"restored":true}\n')
        bridge=ClipboardBridge();callback=Mock(side_effect=OSError('native input interrupted'))
        with patch('attachments.subprocess.Popen',return_value=process):
            try:
                with self.assertRaises(OSError):bridge.paste({'text':'once'},callback)
                callback.assert_called_once();self.assertTrue(bridge.last_restored)
            finally:bridge.close()

    def test_truncated_upload_leaves_no_partial_file(self):
        with self.assertRaises(UploadError):self.store.receive('a','example.txt',10,io.BytesIO(b'short'))
        self.assertEqual(list(self.store.root.iterdir()),[])

    def test_attachment_binding_file_only_submit_and_no_duplicate_attach(self):
        backend=MockBackend();backend.select(1);state=AgentState(backend,self.root/'state')
        try:
            item=state.uploads.receive('phone','example.txt',3,io.BytesIO(b'abc'));snap=backend.read_conversation()
            attached=state.attach_uploads('phone',{'ids':[item['id']],'conversationKey':snap['key'],'epoch':snap['epoch']})
            self.assertEqual(attached['state'],'attached')
            self.assertEqual(state.attach_uploads('phone',{'ids':[item['id']],'conversationKey':snap['key'],'epoch':snap['epoch']})['state'],'attached')
            self.assertEqual(len(backend.mock_attachments),1)
            command={'kind':'message','text':'','submit':True,'attachmentIds':[item['id']],'conversationKey':snap['key'],'epoch':snap['epoch']}
            with self.assertRaises(UploadError):state.perform_action({**command,'conversationKey':'changed'},'phone')
            with self.assertRaises(UploadError):state.perform_action(command,'other')
            result=state.perform_action(command,'phone')
            self.assertEqual(result['submission']['state'],'sent')
            self.assertEqual(backend.messages[-2]['attachments'][0]['name'],'example.txt')
            with self.assertRaises(UploadError):state.perform_action(command,'phone')
        finally:state.close()

    def test_unknown_native_attach_is_consumed_without_retry(self):
        backend=MockBackend();backend.select(1);state=AgentState(backend,self.root/'state');calls=[]
        def uncertain(*args):calls.append(args);raise UploadError('unknown')
        backend.attach_files=uncertain
        try:
            item=state.uploads.receive('phone','example.txt',3,io.BytesIO(b'abc'));snap=backend.read_conversation()
            for _ in range(2):
                with self.assertRaises(UploadError):state.attach_uploads('phone',{'ids':[item['id']],'conversationKey':snap['key'],'epoch':snap['epoch']})
            self.assertEqual(len(calls),1)
            self.assertEqual(state.uploads.items[item['id']]['state'],'unknown')
            self.assertTrue(state.uploads.items[item['id']]['path'].exists())
        finally:state.close()

    def test_rejected_focus_keeps_file_available_for_manual_retry(self):
        from windows_control import TargetError
        backend=MockBackend();backend.select(1);state=AgentState(backend,self.root/'state');original=backend.attach_files
        def rejected(*args):raise TargetError('focus rejected','attachment_not_started')
        backend.attach_files=rejected
        try:
            item=state.uploads.receive('phone','example.txt',3,io.BytesIO(b'abc'));snap=backend.read_conversation()
            command={'ids':[item['id']],'conversationKey':snap['key'],'epoch':snap['epoch']}
            with self.assertRaises(TargetError):state.attach_uploads('phone',command)
            self.assertEqual(state.uploads.items[item['id']]['state'],'uploaded')
            self.assertEqual(backend.mock_attachments,[])
            backend.attach_files=original
            self.assertEqual(state.attach_uploads('phone',command)['state'],'attached')
        finally:state.close()
