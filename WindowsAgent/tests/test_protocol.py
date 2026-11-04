"""HTTP protocol tests against the mock backend.

These exercise pairing, auth, window selection/rebinding, long-poll frames,
freeze semantics, session persistence, switch guard, and the LAN safety rails.
They never capture or control a real desktop window.
"""
import http.client
import base64
import socket
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
import struct
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import (AgentState, FrameEncoder, FrameHub, MockBackend, StreamFlow, SwitchGuard, TokenStore, RemoteError,
                   VERSION, allowed_ip, make_handler, resolve_profile)
from windows_control import WindowTracker, window_matches


class ProtocolTests(unittest.TestCase):
    def test_send_receipt_recovers_lost_response_and_is_bound_to_paired_device(self):
        self.pair();epoch=self.select()['epoch'];snapshot=self.backend.read_conversation()
        command={'kind':'message','text':'继续','submit':True,'epoch':epoch,
                 'conversationKey':snapshot['key'],'messageId':'send_protocol_123456789'}
        status,_,_=self.req('GET','/api/delivery?id='+command['messageId'])
        self.assertEqual(status,401)
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,200);self.assertIn('delivery',json.loads(body))
        for _ in range(50):
            status,_,body=self.req('GET','/api/delivery?id='+command['messageId'],authorized=True)
            receipt=json.loads(body)['delivery']
            if receipt['state']=='sent':break
            time.sleep(.01)
        self.assertEqual(receipt['state'],'sent')
        self.assertNotIn('text',receipt)
        previous=len(self.backend.messages)
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['delivery']['state'],'sent')
        self.assertEqual(len(self.backend.messages),previous)
        second=self.state.tokens.issue('another phone')
        status,_,_=self.req('GET','/api/delivery?id='+command['messageId'],headers={'Authorization':'Bearer '+second})
        self.assertEqual(status,404)
        status,_,_=self.req('POST','/api/action',dict(command,text='changed'),authorized=True)
        self.assertEqual(status,409)

    def test_resolving_a_request_that_never_arrived_prevents_late_submission(self):
        self.pair();info=self.select();snapshot=self.backend.read_conversation();before=list(self.backend.messages)
        ident='send_not_received_12345678'
        status,_,body=self.req('POST','/api/delivery/resolve',{'id':ident,'instance':info['instance']},authorized=True)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['delivery']['code'],'send_not_received')
        command={'kind':'message','text':'must not be late','submit':True,'epoch':info['epoch'],
                 'conversationKey':snapshot['key'],'messageId':ident,'deliveryInstance':info['instance']}
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['delivery']['state'],'failed')
        self.assertEqual(self.backend.messages,before)

    def test_binary_phone_upload_requires_pairing_and_keeps_device_ownership(self):
        from urllib.parse import quote
        def upload(authorized):
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
            headers={'Content-Type':'application/octet-stream','X-File-Name':quote('手机.txt')}
            if authorized:headers['Authorization']='Bearer '+self.token
            conn.request('POST','/api/uploads',b'phone file',headers)
            reply=conn.getresponse();status=reply.status;body=reply.read();conn.close()
            return status,json.loads(body)
        self.assertEqual(upload(False)[0],401)
        self.pair();self.select();snapshot=self.backend.read_conversation()
        status,result=upload(True);self.assertEqual(status,200)
        item=result['file'];self.assertEqual(item['name'],'手机.txt');self.assertNotIn('path',item)
        other=self.state.tokens.issue('Other phone')
        status,_,_=self.req('POST','/api/uploads/discard',{'ids':[item['id']]},headers={'Authorization':'Bearer '+other})
        self.assertEqual(status,409)
        status,_,body=self.req('POST','/api/uploads/attach',{'ids':[item['id']],'conversationKey':snapshot['key'],'epoch':snapshot['epoch']},authorized=True)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['state'],'attached')
        status,_,body=self.req('POST','/api/action',{'kind':'message','text':'','submit':True,'attachmentIds':[item['id']],
            'conversationKey':snapshot['key'],'epoch':snapshot['epoch']},authorized=True)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['submission']['state'],'sent')
        self.assertEqual(self.backend.messages[-2]['attachments'][0]['name'],'手机.txt')

    def test_messages_during_generation_queue_once_in_order(self):
        self.pair();self.select();self.backend.generating=True
        snapshot=self.backend.read_conversation();before=list(snapshot['messages'])
        for text in ('first queued','second queued'):
            status,_,body=self.req('POST','/api/action',{'kind':'message','text':text,'submit':True,
                'conversationKey':snapshot['key'],'epoch':snapshot['epoch']},authorized=True)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['submission'],{'state':'queued'})
        status,_,body=self.req('GET','/api/conversation',authorized=True)
        result=json.loads(body)
        self.assertEqual(status,200);self.assertTrue(result['generating'])
        self.assertEqual(result['messages'],before)
        self.assertEqual([item['text'] for item in result['queued']],['first queued','second queued'])
        self.assertFalse(result['composer']['hasDraft'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.backend = MockBackend()
        self.guard = SwitchGuard([])
        self.hub = FrameHub(self.backend, fps=12)
        self.state = AgentState(self.backend, self.data_dir, guard=self.guard, hub=self.hub)
        self.hub.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = ""

    def tearDown(self):
        self.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def req(self, method, path, data=None, authorized=False, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        request_headers = {"Content-Type": "application/json"}
        if authorized:
            request_headers["Authorization"] = "Bearer " + self.token
        request_headers.update(headers or {})
        body = json.dumps(data).encode() if data is not None else None
        conn.request(method, path, body, request_headers)
        resp = conn.getresponse()
        status, resp_headers, payload = resp.status, dict(resp.getheaders()), resp.read()
        conn.close()
        return status, resp_headers, payload

    def pair(self):
        status, _, body = self.req("POST", "/api/pair",
                                   {"pin": self.state.pin, "name": "Mate"})
        self.assertEqual(status, 200)
        self.token = json.loads(body)["token"]

    def select(self):
        status, _, body = self.req("POST", "/api/select", {"id": 1}, authorized=True)
        self.assertEqual(status, 200)
        return json.loads(body)

    # -- pairing / auth ----------------------------------------------------
    def test_composer_options_auth_navigation_freeze_and_stale_catalog(self):
        self.assertEqual(self.req('GET','/api/composer')[0],401)
        self.assertEqual(self.req('GET','/api/composer/surface?key=unknown&target=s')[0],401)
        self.pair();epoch=self.select()['epoch']
        status,_,body=self.req('GET','/api/composer',authorized=True)
        self.assertEqual(status,200);root=json.loads(body)
        command={'kind':'composer','operation':'invoke','optionsKey':root['key'],'targetId':'model','epoch':epoch}
        self.assertEqual(self.req('POST','/api/action',command,authorized=True)[0],200)
        self.assertEqual(self.req('POST','/api/action',command,authorized=True)[0],409)
        _,_,body=self.req('GET','/api/composer',authorized=True);menu=json.loads(body)
        self.assertTrue(menu['menu']);self.assertTrue(any(item['selected'] for item in menu['items']))
        choose={**command,'optionsKey':menu['key'],'targetId':'choice-1'}
        self.assertEqual(self.req('POST','/api/action',choose,authorized=True)[0],200)
        self.assertEqual(self.backend.option_values['model'],'示例模型 B')
        self.backend.freeze()
        self.assertEqual(self.req('POST','/api/action',choose,authorized=True)[0],409)
        self.assertEqual(self.req('GET','/api/composer/surface?key=unknown&target=s',authorized=True)[0],409)

    def test_explicit_draft_read_clear_and_conflict_do_not_persist_draft_text(self):
        self.backend.draft='Private unsent example 你好 😄'
        self.assertEqual(self.req('GET','/api/draft')[0],401)
        self.pair();epoch=self.select()['epoch']
        status,_,body=self.req('GET','/api/draft',authorized=True)
        self.assertEqual(status,200,body)
        snapshot=json.loads(body)
        self.assertEqual(snapshot['text'],self.backend.draft)
        self.assertEqual(snapshot['epoch'],epoch)
        self.assertTrue(snapshot['hasDraft'])
        self.assertTrue(snapshot['canClear'])
        command={'kind':'draft','operation':'clear','conversationKey':snapshot['conversationKey'],
                 'draftRevision':snapshot['revision'],'epoch':epoch}
        self.backend.draft='Newly edited on computer'
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,409)
        self.assertEqual(json.loads(body)['code'],'draft_changed')
        self.assertEqual(self.backend.draft,'Newly edited on computer')
        _,_,body=self.req('GET','/api/draft',authorized=True)
        command['draftRevision']=json.loads(body)['revision']
        self.assertEqual(self.req('POST','/api/action',command,authorized=True)[0],200)
        self.assertEqual(self.backend.draft,'')
        for path in self.data_dir.rglob('*'):
            if path.is_file():self.assertNotIn('Private unsent example',path.read_text(encoding='utf-8'))

    def test_replace_and_send_is_one_action_and_stale_versions_keep_both_drafts(self):
        self.pair();epoch=self.select()['epoch'];self.backend.draft='Old desktop draft'
        _,_,body=self.req('GET','/api/draft',authorized=True);snapshot=json.loads(body)
        command={'kind':'message','text':'Mobile replacement','submit':True,'epoch':epoch,
                 'conversationKey':snapshot['conversationKey'],'draftRevision':snapshot['revision']}
        previous=len(self.backend.messages)
        status,_,body=self.req('POST','/api/action',{**command,'draftRevision':'stale'},authorized=True)
        self.assertEqual(status,409)
        self.assertEqual(json.loads(body)['code'],'draft_changed')
        self.assertEqual(self.backend.draft,'Old desktop draft')
        self.assertEqual(len(self.backend.messages),previous)
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,200,body)
        self.assertEqual(self.backend.draft,'')
        self.assertEqual(len(self.backend.messages),previous+2)
        self.assertEqual(self.backend.messages[-2]['text'],'Mobile replacement')
        self.backend.draft='Another draft'
        _,_,body=self.req('GET','/api/draft',authorized=True);snapshot=json.loads(body)
        self.assertEqual(self.req('POST','/api/action',{'kind':'draft','operation':'clear',
            'conversationKey':snapshot['conversationKey'],'draftRevision':snapshot['revision'],'epoch':epoch-1},authorized=True)[0],409)
        self.assertEqual(self.backend.draft,'Another draft')
        status,_,body=self.req('POST','/api/action',{'kind':'message','text':'Normal send','submit':True,
            'conversationKey':snapshot['conversationKey'],'epoch':epoch},authorized=True)
        self.assertEqual(status,409)
        self.assertEqual(json.loads(body)['code'],'draft_exists')
        self.assertEqual(self.req('POST','/api/action',{'kind':'draft','operation':'clear','epoch':epoch},authorized=True)[0],400)

    def test_project_navigation_auth_expansion_and_old_message_rejection(self):
        status,_,_=self.req('GET','/api/navigation')
        self.assertEqual(status,401)
        self.pair();epoch=self.select()['epoch']
        status,_,body=self.req('GET','/api/navigation',authorized=True)
        self.assertEqual(status,200,body)
        nav=json.loads(body)
        status,_,body=self.req('POST','/api/action',{'kind':'navigate','targetId':'other',
            'navigationKey':nav['key'],'epoch':epoch},authorized=True)
        self.assertEqual(status,200,body)
        next_epoch=json.loads(body)['epoch'];self.assertGreater(next_epoch,epoch)
        self.assertEqual(self.backend.current_chat,'other')
        status,_,_=self.req('POST','/api/action',{'kind':'message','text':'old target','submit':True,
            'conversationKey':f'mock-{epoch}','epoch':epoch},authorized=True)
        self.assertEqual(status,409)
        status,_,body=self.req('GET','/api/navigation',authorized=True)
        nav=json.loads(body)
        status,_,body=self.req('POST','/api/action',{'kind':'navigate','targetId':'project-demo',
            'navigationKey':nav['key'],'expanded':False,'epoch':next_epoch},authorized=True)
        self.assertEqual(status,200,body)
        self.assertFalse(self.backend.navigation_expanded)
        self.assertFalse(any(item['kind']=='thread' for item in self.backend.navigation()['items']))
        self.assertEqual(self.backend.tracker.epoch,next_epoch)

    def test_new_chat_is_bound_to_observed_project_and_does_not_send_a_message(self):
        self.pair();epoch=self.select()['epoch'];nav=self.backend.navigation();old=list(self.backend.messages)
        command={'kind':'navigate','targetId':'new-demo','navigationKey':nav['key'],'epoch':epoch}
        self.backend.freeze()
        self.assertEqual(self.req('POST','/api/action',command,authorized=True)[0],409)
        self.backend.unfreeze()
        command['epoch']=self.backend.tracker.epoch
        status,_,body=self.req('POST','/api/action',command,authorized=True)
        self.assertEqual(status,200);self.assertGreater(json.loads(body)['epoch'],epoch)
        self.assertEqual(self.backend.messages,[]);self.assertEqual(self.backend.chat_store['main'],old)
        self.assertTrue(self.backend.current_chat.startswith('new-'))
        self.assertEqual(self.req('POST','/api/action',command,authorized=True)[0],409)

    def test_conversation_read_send_and_draft_boundaries(self):
        status, _, _ = self.req('GET', '/api/conversation')
        self.assertEqual(status, 401)
        self.pair()
        epoch = self.select()['epoch']
        status, _, payload = self.req('GET', '/api/conversation', authorized=True)
        self.assertEqual(status, 200)
        snapshot = json.loads(payload)
        self.assertEqual([m['role'] for m in snapshot['messages']], ['user', 'assistant'])
        command = {'kind': 'message', 'text': 'Unicode 你好 😄', 'submit': True,
                   'epoch': epoch, 'conversationKey': snapshot['key']}
        status, _, body = self.req('POST', '/api/action', command, authorized=True)
        self.assertEqual(status, 200, body)
        self.assertIn('actionSequence', json.loads(body))
        self.assertEqual(self.backend.messages[-2]['text'], command['text'])
        command['conversationKey'] = 'old-conversation'
        status, _, _ = self.req('POST', '/api/action', command, authorized=True)
        self.assertEqual(status, 409)
        command['conversationKey'] = snapshot['key']
        self.backend.draft = 'unsent draft'
        status, _, _ = self.req('POST', '/api/action', command, authorized=True)
        self.assertEqual(status, 409)
        self.assertEqual(self.backend.draft, 'unsent draft')

    def test_successful_posts_reuse_the_connection(self):
        self.pair()
        epoch = self.select()['epoch']
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        try:
            original = None
            for _ in range(2):
                conn.request('POST', '/api/action', json.dumps({'kind': 'key', 'key': 'TAB', 'epoch': epoch}), headers)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                self.assertIsNotNone(conn.sock)
                if original is None: original = conn.sock
                self.assertIs(conn.sock, original)
        finally: conn.close()

    def test_websocket_auth_ordered_actions_and_stale_epoch(self):
        from test_conversation import masked
        self.pair()
        epoch = self.select()['epoch']
        sock = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=5)
        reader = sock.makefile('rb')
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f'GET /api/control HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                          f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                          'Sec-WebSocket-Version: 13\r\n\r\n').encode())
            self.assertIn(b' 101 ', reader.readline())
            while reader.readline() != b'\r\n': pass
            def receive():
                head = reader.read(2)
                length = head[1] & 127
                if length == 126: length = struct.unpack('!H', reader.read(2))[0]
                if length == 127: length = struct.unpack('!Q', reader.read(8))[0]
                return json.loads(reader.read(length))
            sock.sendall(masked({'type': 'auth', 'token': self.token}))
            self.assertTrue(receive()['ok'])
            for ident, text in [(1, 'one'), (2, 'two')]:
                sock.sendall(masked({'id': ident, 'type': 'action', 'payload': {'kind': 'text', 'text': text, 'epoch': epoch, 'submit': True}}))
            replies = [receive(), receive()]
            self.assertEqual([r['id'] for r in replies], [1, 2])
            self.assertTrue(all(r['ok'] for r in replies))
            self.assertEqual(self.backend.last_action, 'text:two + BUTTON')
            self.backend.draft='Draft over WebSocket'
            snapshot=self.backend.read_draft()
            sock.sendall(masked({'id':3,'type':'action','payload':{'kind':'message','text':'blocked by draft',
                'submit':True,'epoch':epoch,'conversationKey':snapshot['conversationKey']}}))
            conflict=receive()
            self.assertEqual(conflict['status'],409)
            self.assertEqual(conflict['code'],'draft_exists')
            sock.sendall(masked({'id':4,'type':'action','payload':{'kind':'draft','operation':'clear',
                'epoch':epoch,'conversationKey':snapshot['conversationKey'],'draftRevision':snapshot['revision']}}))
            self.assertTrue(receive()['ok'])
            self.assertEqual(self.backend.draft,'')
            self.backend.freeze()
            sock.sendall(masked({'id': 5, 'type': 'action', 'payload': {'kind': 'text', 'text': 'blocked', 'epoch': epoch}}))
            self.assertEqual(receive()['status'], 409)
            self.assertEqual(self.backend.last_action, 'text:two + BUTTON')
        finally:
            reader.close()
            sock.close()

    def test_conversation_stream_starts_with_snapshot_and_sends_delta(self):
        self.pair()
        self.select()
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request('GET', '/api/conversation/stream', headers={'Authorization': 'Bearer ' + self.token})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            def line():
                data = bytearray()
                while not data.endswith(b'\n'): data.extend(response.read(1))
                return json.loads(data)
            first = line()
            self.assertTrue(first['replace'])
            self.backend.messages[-1]['text'] += ' changed'
            event = line()
            self.assertFalse(event['replace'])
            self.assertEqual([m['id'] for m in event['messages']], ['m1'])
        finally: conn.close()

    def test_pair_code_is_single_use_even_after_unpairing(self):
        original_pin = self.state.pin
        self.pair()
        for pin in ("", original_pin):
            with self.subTest(pin=pin):
                status, _, body = self.req("POST", "/api/pair", {"pin": pin})
                self.assertEqual(status, 403, body)
                self.assertNotIn("token", json.loads(body))
                self.assertEqual(self.state.tokens.count(), 1)
        status, _, _ = self.req("GET", "/api/windows", authorized=True)
        self.assertEqual(status, 200)
        status, _, _ = self.req("POST", "/api/unpair", {}, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(self.state.tokens.count(), 0)
        for pin in ("", original_pin):
            status, _, body = self.req("POST", "/api/pair", {"pin": pin})
            self.assertEqual(status, 403, body)
            self.assertEqual(self.state.tokens.count(), 0)

    def test_pair_requires_six_ascii_digits(self):
        self.state.pin = "012345"
        for pin in ("", "12345", "1234567", "12 345", "abcdef",
                    "１２３４５６", "١٢٣٤٥٦", "12345\n"):
            with self.subTest(pin=pin):
                # Isolate each malformed value from the separate rate-limit test.
                self.state.failed.clear()
                status, _, body = self.req("POST", "/api/pair", {"pin": pin})
                self.assertEqual(status, 403, body)
                self.assertNotIn("token", json.loads(body))
                self.assertEqual(self.state.tokens.count(), 0)
        self.state.failed.clear()
        self.pair()  # A leading zero remains valid.
        self.assertTrue(self.state.tokens.valid(self.token))

    def test_expired_pair_code_cannot_issue_tokens(self):
        original_pin = self.state.pin
        self.state.pin_expires = time.monotonic() - 1
        for pin in (original_pin, ""):
            status, _, body = self.req("POST", "/api/pair", {"pin": pin})
            self.assertEqual(status, 403, body)
            self.assertNotIn("token", json.loads(body))
        self.assertEqual(self.state.tokens.count(), 0)

    def test_pair_rate_limit_counts_malformed_codes(self):
        original_pin = self.state.pin
        for _ in range(5):
            status, _, _ = self.req("POST", "/api/pair", {"pin": ""})
            self.assertEqual(status, 403)
        status, _, body = self.req("POST", "/api/pair", {"pin": original_pin})
        self.assertEqual(status, 429, body)
        self.assertNotIn("token", json.loads(body))
        self.assertEqual(self.state.tokens.count(), 0)

    def test_concurrent_pair_requests_only_enroll_one_device(self):
        original_pin = self.state.pin
        barrier = threading.Barrier(2)

        def pair_once():
            barrier.wait(timeout=5)
            return self.req("POST", "/api/pair", {"pin": original_pin})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(pair_once) for _ in range(2)]
            replies = [future.result(timeout=8) for future in futures]
        self.assertEqual(sorted(reply[0] for reply in replies), [200, 403])
        self.assertEqual(self.state.tokens.count(), 1)
        for status, _, body in replies:
            data = json.loads(body)
            if status == 200:
                self.assertTrue(self.state.tokens.valid(data["token"]))
            else:
                self.assertNotIn("token", data)

    def test_pair_select_frame_and_revoke(self):
        status, _, _ = self.req("GET", "/api/frame")
        self.assertEqual(status, 401)
        status, _, _ = self.req("POST", "/api/pair", {"pin": "000bad", "name": "x"})
        self.assertEqual(status, 403)
        self.pair()
        self.assertTrue(self.state.tokens.valid(self.token))
        status, _, body = self.req("GET", "/api/windows", authorized=True)
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual(info["version"], VERSION)
        self.assertEqual(info["windows"][0]["id"], 1)
        status, _, _ = self.req("GET", "/api/frame", authorized=True)
        self.assertEqual(status, 409)
        status, _, _ = self.req("POST", "/api/select", {"id": 99}, authorized=True)
        self.assertEqual(status, 400)
        info = self.select()
        self.assertTrue(info["windows"][0]["selected"])
        self.assertEqual(info["status"], "ready")
        status, headers, body = self.req("GET", "/api/frame", authorized=True)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\xff\xd8"))
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertIn("X-Frame-Id", headers)
        self.assertIn("X-Target-Epoch", headers)
        self.assertIn("X-Captured-Ms", headers)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "shell", "command": "whoami"}, authorized=True)
        self.assertEqual(status, 400)
        status, _, _ = self.req("POST", "/api/unpair", {}, authorized=True)
        self.assertEqual(status, 200)
        status, _, _ = self.req("GET", "/api/windows", authorized=True)
        self.assertEqual(status, 401)

    def test_health_and_page_headers(self):
        status, headers, body = self.req("GET", "/api/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health["version"], VERSION)
        self.assertTrue(health["mock"])
        status, headers, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_polling_reuses_connection_without_console_output(self):
        output = io.StringIO()
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        try:
            with contextlib.redirect_stdout(output):
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertFalse(response.will_close)
                first_socket = conn.sock
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertIs(conn.sock, first_socket)
            self.assertEqual(output.getvalue(), "")
        finally:
            conn.close()

    def test_rejects_dns_rebinding_and_cross_site_origin(self):
        self.pair()
        status, _, _ = self.req("GET", "/api/windows", authorized=True,
                                headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.req("GET", "/api/windows", authorized=True,
                                headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        host = f"127.0.0.1:{self.server.server_port}"
        status, _, _ = self.req("GET", "/api/windows", authorized=True,
                                headers={"Origin": "http://" + host})
        self.assertEqual(status, 200)

    # -- window lifecycle --------------------------------------------------
    def test_window_restart_and_stale_commands(self):
        self.token = self.state.tokens.issue("Mate")
        first = self.select()
        first_epoch = first["epoch"]
        self.backend.simulate_restart(2048)
        status, _, body = self.req("GET", "/api/windows", authorized=True)
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual(info["windows"][0]["id"], 2048)
        self.assertTrue(info["windows"][0]["selected"])
        self.assertGreater(info["epoch"], first_epoch)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "text", "text": "wrong-account",
                                 "epoch": first_epoch}, authorized=True)
        self.assertEqual(status, 409)
        self.assertNotIn("wrong-account", self.backend.last_action)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "text", "text": "new-window",
                                 "epoch": info["epoch"]}, authorized=True)
        self.assertEqual(status, 200)
        self.assertIn("new-window", self.backend.last_action)
        status, _, body = self.req("POST", "/api/desktop", {}, authorized=True)
        self.assertEqual(status, 200)
        desktop = json.loads(body)
        self.assertEqual(desktop["mode"], "desktop")
        status, _, body = self.req("GET", "/api/frame", authorized=True)
        self.assertEqual(status, 200)
        status, _, body = self.req("POST", "/api/action",
                                   {"kind": "tap", "x": 0.5, "y": 0.5,
                                    "epoch": desktop["epoch"]}, authorized=True)
        self.assertEqual(status, 200)
        status, _, body = self.req("POST", "/api/window", {}, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "window")
        self.assertTrue(json.loads(body)["windows"][0]["selected"])

    def test_ambiguous_window_never_guessed(self):
        def w(window_id, pid=10, name="ChatGPT.exe"):
            return {"id": window_id, "title": "ChatGPT", "pid": pid,
                    "exe_path": "C:/Program Files/ChatGPT/" + name,
                    "class": "Chrome_WidgetWin_1"}
        tracker = WindowTracker()
        tracker.choose(w(1))
        self.assertEqual(tracker.resolve([w(1)]), 1)
        self.assertEqual(tracker.resolve([w(2, 20), w(3, 30)]), 0)
        self.assertEqual(tracker.status, "ambiguous")
        self.assertEqual(tracker.resolve([w(3, 30)]), 3)
        self.assertEqual(tracker.status, "rebound")
        tracker.choose(w(4, 40, "ChatGPT.exe"))
        self.assertEqual(tracker.resolve([w(4, 41)]), 4)
        self.assertEqual(tracker.status, "rebound")

    def test_rebind_is_stable_and_does_not_inflate_epoch(self):
        """After a rebind the adopted pid must keep status ready and epoch fixed."""
        def w(window_id, pid):
            return {"id": window_id, "title": "ChatGPT", "pid": pid,
                    "exe_path": "C:/Program Files/ChatGPT/ChatGPT.exe",
                    "class": "Chrome_WidgetWin_1"}
        tracker = WindowTracker()
        tracker.choose(w(1, 10))
        epoch0 = tracker.epoch
        self.assertEqual(tracker.resolve([w(2, 20)]), 2)
        self.assertEqual(tracker.status, "rebound")
        epoch1 = tracker.epoch
        self.assertGreater(epoch1, epoch0)
        for _ in range(5):
            self.assertEqual(tracker.resolve([w(2, 20)]), 2)
            self.assertEqual(tracker.status, "ready")
        self.assertEqual(tracker.epoch, epoch1)
        # Another real restart still bumps exactly once and rebinds.
        self.assertEqual(tracker.resolve([w(3, 30)]), 3)
        self.assertEqual(tracker.status, "rebound")
        self.assertEqual(tracker.epoch, epoch1 + 2)
        self.assertEqual(tracker.resolve([w(3, 30)]), 3)
        self.assertEqual(tracker.epoch, epoch1 + 2)

    # -- persistence / freeze / long poll ----------------------------------
    def test_saved_device_survives_agent_restart(self):
        token = self.state.tokens.issue("Mate 90")
        another = TokenStore(self.data_dir / "paired_devices.json")
        self.assertTrue(another.valid(token))
        self.assertFalse(another.valid("wrong"))

    def test_session_fingerprint_rebinds_after_agent_restart(self):
        self.token = self.state.tokens.issue("Mate")
        self.select()
        session = json.loads((self.data_dir / "session.json").read_text(encoding="utf-8"))
        self.assertTrue(session["fingerprint"]["path"].lower().endswith("chatgpt.exe"))
        backend2 = MockBackend()
        hub2 = FrameHub(backend2, fps=12)
        state2 = AgentState(backend2, self.data_dir, hub=hub2)
        try:
            info = state2.info()
            self.assertTrue(info["windows"][0]["selected"])
            self.assertIn(info["status"], ("ready", "rebound"))
            self.assertGreater(info["epoch"], 0)
        finally:
            state2.close()

    def test_freeze_blocks_actions_and_bumps_epoch(self):
        self.token = self.state.tokens.issue("Mate")
        info = self.select()
        epoch = info["epoch"]
        status, _, body = self.req("POST", "/api/freeze", {"frozen": True}, authorized=True)
        self.assertEqual(status, 200)
        frozen = json.loads(body)
        self.assertTrue(frozen["frozen"]["active"])
        self.assertGreater(frozen["epoch"], epoch)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "text", "text": "old", "epoch": epoch},
                                authorized=True)
        self.assertEqual(status, 409)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "text", "text": "blocked",
                                 "epoch": frozen["epoch"]}, authorized=True)
        self.assertEqual(status, 409)
        status, _, body = self.req("POST", "/api/freeze", {"frozen": False}, authorized=True)
        self.assertEqual(status, 200)
        unfrozen = json.loads(body)
        self.assertFalse(unfrozen["frozen"]["active"])
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "text", "text": "allowed",
                                 "epoch": unfrozen["epoch"]}, authorized=True)
        self.assertEqual(status, 200)

    def test_long_poll_returns_newer_frame_then_204(self):
        self.token = self.state.tokens.issue("Mate")
        self.select()
        status, headers, _ = self.req("GET", "/api/frame", authorized=True)
        self.assertEqual(status, 200)
        first_id = int(headers["X-Frame-Id"])
        status, headers, body = self.req("GET", f"/api/frame?since={first_id}",
                                         authorized=True)
        self.assertEqual(status, 200, body[:200])
        self.assertGreater(int(headers["X-Frame-Id"]), first_id)
        self.assertTrue(body.startswith(b"\xff\xd8"))
        status, _, _ = self.req("GET", "/api/frame?since=999999999999999999", authorized=True)
        self.assertEqual(status, 204)

    def test_idle_frame_is_skipped_and_capture_errors_take_priority(self):
        hub = FrameHub(self.backend, fps=3)
        stale = hub._publish({"jpeg": b"old", "epoch": 1})
        stale["published_at"] -= 30
        self.assertIsNone(hub.wait(0, timeout=0.01))
        fresh = hub._publish({"jpeg": b"new", "epoch": 1})
        self.assertIs(hub.wait(stale["id"], timeout=0.01), fresh)
        hub._publish_error("window closed")
        self.assertEqual(hub.wait(stale["id"], timeout=0.01), {"error": "window closed"})

    def test_stream_sends_full_then_patch_and_honors_token_revocation(self):
        status, _, _ = self.req("GET", "/api/stream")
        self.assertEqual(status, 401)
        self.pair()
        epoch = self.select()["epoch"]
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)

        def read_packet(response):
            meta_length, image_length = struct.unpack("!II", response.read(8))
            return json.loads(response.read(meta_length)), response.read(image_length)

        try:
            conn.request("GET", "/api/stream", headers={"Authorization": "Bearer " + self.token})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.chunked)
            meta, image = read_packet(response)
            self.assertEqual(meta["kind"], "full")
            self.assertTrue(image.startswith(b"\xff\xd8"))
            status, _, _ = self.req("POST", "/api/stream/ack",
                                    {"streamId": meta["streamId"], "frameId": meta["frameId"]}, authorized=True)
            self.assertEqual(status, 204)
            self.backend.action({"kind": "text", "text": "stream-delta", "epoch": epoch})
            for _ in range(5):
                patch, image = read_packet(response)
                if patch["kind"] != "idle":
                    break
            self.assertEqual(patch["kind"], "patch")
            self.assertGreater(patch["frameId"], meta["frameId"])
            self.assertTrue(image.startswith(b"\x89PNG"))
            self.state.tokens.revoke(self.token)
            response.read()
        finally:
            conn.close()

    def test_stream_flow_limits_backlog_and_rejects_foreign_acknowledgments(self):
        flow = StreamFlow("synthetic-owner")
        for frame_id in (1, 2, 3):
            flow.sent(frame_id)
        self.assertFalse(flow.ready())
        with self.assertRaises(RemoteError) as foreign:
            flow.acknowledge("synthetic-other", 2)
        self.assertEqual(foreign.exception.status, 403)
        self.assertEqual(flow.pending, [1, 2, 3])
        with self.assertRaises(RemoteError):
            flow.acknowledge("synthetic-owner", 4)
        flow.acknowledge("synthetic-owner", 2)
        self.assertEqual(flow.pending, [3])
        self.assertTrue(flow.ready())
        self.assertIsNotNone(flow.snapshot()["image_ack_roundtrip_ms"])
        self.assertEqual(flow.snapshot()["unconfirmed_frames"], 1)
        flow.last_ack -= 60
        self.assertFalse(flow.stalled())
        flow.sent_at[3] -= 9
        self.assertTrue(flow.stalled())

    def test_slow_stream_pauses_at_three_images_and_resumes_with_latest_frame(self):
        self.pair()
        self.select()
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        counter = [0]
        original_capture = self.backend.capture_meta

        def moving_frame(*args):
            with self.backend.lock:
                counter[0] += 1
                self.backend.last_action = f"moving frame {counter[0]}"
                return original_capture(*args)

        self.backend.capture_meta = moving_frame

        def read_packet(response):
            meta_length, image_length = struct.unpack("!II", response.read(8))
            meta = json.loads(response.read(meta_length))
            response.read(image_length)
            return meta

        try:
            conn.request("GET", "/api/stream", headers={"Authorization": "Bearer " + self.token})
            response = conn.getresponse()
            images = [read_packet(response) for _ in range(3)]
            stream_id = images[-1]["streamId"]
            with self.state.lock:
                flow = self.state.streams[stream_id]
            with ThreadPoolExecutor(max_workers=1) as pool:
                fourth = pool.submit(read_packet, response)
                time.sleep(0.35)  # Several new frames are captured while this phone withholds its confirmation.
                self.assertEqual(flow.snapshot()["unconfirmed_frames"], 3)
                self.assertFalse(fourth.done())
                status, _, _ = self.req("POST", "/api/stream/ack",
                                        {"streamId": stream_id, "frameId": images[-1]["frameId"]}, authorized=True)
                self.assertEqual(status, 204)
                resumed = fourth.result(timeout=5)
                self.assertGreater(resumed["frameId"], images[-1]["frameId"] + 1)
            self.state.tokens.revoke(self.token)
            response.read()
        finally:
            conn.close()

    def test_frame_encoder_reconstructs_changes_and_resets_for_epoch_and_size(self):
        from PIL import Image, ImageChops, ImageDraw

        def frame(image, frame_id, epoch=1):
            data = io.BytesIO()
            image.save(data, format="JPEG", quality=85)
            return {"id": frame_id, "epoch": epoch, "captured_ms": 0, "jpeg": data.getvalue()}

        def unpack(packet):
            size, length = struct.unpack("!II", packet[:8])
            self.assertEqual(len(packet), 8 + size + length)
            return json.loads(packet[8:8 + size]), packet[8 + size:]

        base = Image.new("RGB", (800, 500), "white")
        encoder = FrameEncoder()
        initial = frame(base, 1)
        meta, image = unpack(encoder.encode(initial))
        self.assertEqual(meta["kind"], "full")
        canvas = Image.open(io.BytesIO(image)).convert("RGB")
        self.assertIsNone(encoder.encode(frame(base, 2)))
        ImageDraw.Draw(base).rectangle((60, 50, 180, 90), fill="black")
        changed = frame(base, 3)
        meta, image = unpack(encoder.encode(changed))
        self.assertEqual(meta["kind"], "patch")
        self.assertLess(len(image), len(changed["jpeg"]))
        canvas.paste(Image.open(io.BytesIO(image)), (meta["x"], meta["y"]))
        expected = Image.open(io.BytesIO(changed["jpeg"])).convert("RGB")
        difference = ImageChops.difference(canvas, expected).convert("L")
        self.assertIsNone(difference.point(lambda value: 255 if value > 8 else 0).getbbox())
        self.assertEqual(unpack(encoder.encode(frame(base, 4, epoch=2)))[0]["kind"], "full")
        self.assertEqual(unpack(encoder.encode(frame(base.resize((600, 400)), 5, epoch=2)))[0]["kind"], "full")

    def test_patch_baseline_retains_unsent_pixels_until_their_change_is_visible(self):
        from PIL import Image, ImageDraw

        def frame(level, frame_id, patch=False):
            image = Image.new("RGB", (200, 120), (level,) * 3)
            if patch:
                ImageDraw.Draw(image).rectangle((20, 20, 40, 40), fill="black")
            data = io.BytesIO()
            image.save(data, format="JPEG", quality=100)
            return {"id": frame_id, "epoch": 1, "captured_ms": 0, "jpeg": data.getvalue()}

        encoder = FrameEncoder()
        encoder.encode(frame(100, 1))
        changed = encoder.encode(frame(103, 2, patch=True))
        meta_length = struct.unpack("!I", changed[:4])[0]
        self.assertEqual(json.loads(changed[8:8 + meta_length])["kind"], "patch")
        self.assertEqual(encoder.previous.getpixel((190, 110)), (100, 100, 100))
        self.assertIsNotNone(encoder.encode(frame(110, 3, patch=True)))

    def test_switch_guard_freezes_on_signal_file_change(self):
        signal = self.data_dir / "switch.signal"
        signal.write_text("idle", encoding="utf-8")
        guard = SwitchGuard([str(signal)], freeze_seconds=30)
        guard.on_change = lambda path: self.state.freeze_for(30, f"test:{path.name}")
        self.assertFalse(guard.poll_once())  # baseline
        time.sleep(0.01)
        signal.write_text("switching", encoding="utf-8")
        self.assertTrue(guard.poll_once())
        frozen = self.backend.frozen_info()
        self.assertTrue(frozen["active"])
        self.assertTrue(guard.snapshot()["active"])
        self.assertGreater(guard.snapshot()["last_change_ms"], 0)

    def test_action_parameter_validation(self):
        self.token = self.state.tokens.issue("Mate")
        epoch = self.select()["epoch"]
        cases = [
            {"kind": "text", "text": "x" * 6001, "epoch": epoch},
            {"kind": "text", "text": "invalid method", "submitMethod": "AUTO_ENTER", "epoch": epoch},
            {"kind": "key", "key": "ALT+F4", "epoch": epoch},
            {"kind": "scroll", "delta": 5000, "epoch": epoch},
            {"kind": "tap", "x": 1.5, "y": 0.5, "epoch": epoch},
        ]
        for case in cases:
            status, _, _ = self.req("POST", "/api/action", case, authorized=True)
            self.assertEqual(status, 400, case)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "right", "x": 0.2, "y": 0.2,
                                 "epoch": epoch}, authorized=True)
        self.assertEqual(status, 200)

        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "key", "key": "CTRL+ENTER", "epoch": epoch},
                                authorized=True)
        self.assertEqual(status, 200)
        status, _, _ = self.req("POST", "/api/action",
                                {"kind": "key", "key": "CTRL+A", "epoch": epoch},
                                authorized=True)
        self.assertEqual(status, 200)

    def test_info_snapshot_after_desktop_and_window_mode(self):
        self.token = self.state.tokens.issue("Mate")
        self.select()
        status, _, body = self.req("POST", "/api/desktop", {}, authorized=True)
        info = json.loads(body)
        self.assertEqual(info["status"], "desktop")
        self.assertEqual(info["mode"], "desktop")
        self.assertIn("guard", info)
        self.assertIn("paired_count", info)
        self.assertEqual(info["paired_count"], 1)
        status, _, body = self.req("GET", "/api/windows", authorized=True)
        self.assertEqual(json.loads(body)["mode"], "desktop")

    def test_window_target_filter(self):
        keywords = ["chatgpt", "codex"]
        self.assertTrue(window_matches("ChatGPT", r"C:\Apps\ChatGPT.exe",
                                       custom_keywords=False, keywords=keywords))
        self.assertTrue(window_matches("Codex", r"C:\Apps\Codex.exe",
                                       custom_keywords=False, keywords=keywords))
        self.assertTrue(window_matches("ChatGPT - Google Chrome", r"C:\Chrome\chrome.exe",
                                       custom_keywords=False, keywords=keywords))
        self.assertFalse(window_matches("ChatGPT Remote · 手机控制台",
                                        r"C:\Chrome\chrome.exe",
                                        custom_keywords=False, keywords=keywords))
        self.assertFalse(window_matches("ChatGPTnx Corner Edge", r"C:\Python\python.exe",
                                        custom_keywords=False, keywords=keywords))
        self.assertFalse(window_matches("ChatGPT is using your computer",
                                        r"C:\Codex\codex-computer-use.exe",
                                        custom_keywords=False, keywords=keywords))
        self.assertFalse(window_matches("ChatGPT", r"C:\Windows\cmd.exe",
                                        custom_keywords=False, keywords=keywords))
        self.assertTrue(window_matches("Harmony E2E 123", r"C:\Python\python.exe",
                                       custom_keywords=True, keywords=["harmony e2e"]))
        self.assertFalse(window_matches("ChatGPT Remote", r"C:\Chrome\chrome.exe",
                                        custom_keywords=True, keywords=["chatgpt remote"]))
        self.assertFalse(window_matches("ChatGPT Harmony Remote", r"C:\Chrome\chrome.exe",
                                        custom_keywords=False, keywords=keywords))

    def test_private_network_only(self):
        for ip in ["127.0.0.1", "10.0.0.7", "172.16.0.1", "192.168.99.2"]:
            self.assertTrue(allowed_ip(ip), ip)
        for ip in ["8.8.8.8", "169.254.2.1", "100.64.0.1", "1.2.3.4"]:
            self.assertFalse(allowed_ip(ip), ip)
        self.assertTrue(allowed_ip("::1"))  # loopback IPv6 is accepted explicitly


class AccessAndProxyTests(unittest.TestCase):
    """Public HTTPS reverse-proxy support: trusted Host/Origin and access key."""

    ACCESS_KEY = "test-access-key-123456"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.backend = MockBackend()
        self.hub = FrameHub(self.backend, fps=12)
        self.state = AgentState(self.backend, self.data_dir, hub=self.hub,
                                access_key=self.ACCESS_KEY,
                                trusted_hosts=("example.ts.net",))
        self.hub.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = ""

    def tearDown(self):
        self.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def req(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        body = json.dumps(data).encode() if data is not None else None
        conn.request(method, path, body, request_headers)
        resp = conn.getresponse()
        status, resp_headers, payload = resp.status, dict(resp.getheaders()), resp.read()
        conn.close()
        return status, resp_headers, payload

    def test_access_key_required_and_cookie_exchange(self):
        status, _, body = self.req("GET", "/")
        self.assertEqual(status, 401)
        self.assertIn("需要访问密钥", body.decode("utf-8"))
        status, _, _ = self.req("GET", "/?k=wrong")
        self.assertEqual(status, 401)

        status, headers, _ = self.req("GET", "/?k=" + self.ACCESS_KEY)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/")
        cookie = headers["Set-Cookie"]
        self.assertIn("hr_access=", cookie)
        self.assertIn("HttpOnly", cookie)
        cookie_pair = cookie.split(";", 1)[0]

        status, _, body = self.req("GET", "/", headers={"Cookie": cookie_pair})
        self.assertEqual(status, 200)
        self.assertIn("ChatGPT Remote", body.decode("utf-8"))
        status, _, _ = self.req("GET", "/api/health")
        self.assertEqual(status, 401)
        status, _, body = self.req("GET", "/api/health", headers={"Cookie": cookie_pair})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], VERSION)

        status, _, body = self.req("POST", "/api/pair",
                                   {"pin": self.state.pin, "name": "Phone"},
                                   headers={"Cookie": cookie_pair})
        self.assertEqual(status, 200)
        token = json.loads(body)["token"]
        status, _, _ = self.req("GET", "/api/windows",
                                headers={"Cookie": cookie_pair,
                                         "Authorization": "Bearer " + token})
        self.assertEqual(status, 200)

    def test_header_key_works_for_api_clients(self):
        status, _, _ = self.req("GET", "/api/health",
                                headers={"X-Access-Key": self.ACCESS_KEY})
        self.assertEqual(status, 200)
        status, _, _ = self.req("GET", "/api/health",
                                headers={"X-Access-Key": "wrong"})
        self.assertEqual(status, 401)

    def test_access_key_query_is_not_logged(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status, _, _ = self.req("GET", "/?k=" + self.ACCESS_KEY)
        self.assertEqual(status, 302)
        self.assertNotIn(self.ACCESS_KEY, output.getvalue())

    def test_trusted_host_and_https_origin(self):
        access = {"X-Access-Key": self.ACCESS_KEY}
        status, _, _ = self.req("GET", "/api/health",
                                headers={"Host": "example.ts.net", **access})
        self.assertEqual(status, 200)
        status, _, _ = self.req("GET", "/api/health",
                                headers={"Host": "evil.example", **access})
        self.assertEqual(status, 403)

        self.token = self.state.tokens.issue("Phone")
        auth = {"Authorization": "Bearer " + self.token}
        status, _, _ = self.req("GET", "/api/windows",
                                headers={"Host": "example.ts.net:8443",
                                         "Origin": "https://example.ts.net:8443",
                                         **access, **auth})
        self.assertEqual(status, 200)
        status, _, _ = self.req("GET", "/api/windows",
                                headers={"Host": "example.ts.net:8443",
                                         "Origin": "https://evil.example",
                                         **access, **auth})
        self.assertEqual(status, 403)
        status, _, _ = self.req("GET", "/api/windows",
                                headers={"Host": "example.ts.net:8443",
                                         "Origin": "http://example.ts.net:8443",
                                         **access, **auth})
        self.assertEqual(status, 200)

    def test_profile_presets(self):
        self.assertEqual(resolve_profile("lan", None, None, None), (8.0, 1280, 65))
        self.assertEqual(resolve_profile("remote", None, None, None), (12.0, 900, 55))
        self.assertEqual(resolve_profile("remote", 5, 1000, 60), (5.0, 1000, 60))


if __name__ == "__main__":
    unittest.main()
