"""Public capability regression; simulated backend, never Windows input."""
import base64
import http.client
import json
import socket
import struct
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentState, FrameEncoder, MockBackend, RemoteError, make_handler
from windows_control import WindowsBackend


class PublicPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = MockBackend()
        self.backend.select(1)
        self.state = AgentState(self.backend, Path(self.temp.name),
                                access_key='isolated-test-access-key')
        self.token = self.state.tokens.issue('test-device')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.state))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, data):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', path, json.dumps(data), {
                'Content-Type': 'application/json',
                'X-Access-Key': 'isolated-test-access-key',
                'Authorization': 'Bearer ' + self.token,
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_authenticated_desktop_route_is_denied_without_changing_mode(self):
        status, body = self.request('/api/desktop', {})
        self.assertEqual(status, 403)
        self.assertFalse(body['ok'])
        self.assertEqual(self.backend.mode, 'window')

    def test_raw_actions_are_denied_in_shared_http_websocket_dispatcher(self):
        before = self.backend.last_action
        for kind in ('tap', 'right', 'scroll', 'text', 'key'):
            command = {'kind': kind, 'epoch': self.backend.tracker.epoch, 'text': 'test',
                       'key': 'CTRL+V', 'x': .5, 'y': .5, 'delta': 120}
            with self.subTest(kind=kind):
                status, _ = self.request('/api/action', command)
                self.assertEqual(status, 403)
                with self.assertRaises(RemoteError) as error:
                    self.state.perform_action(command, self.token)
                self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.backend.last_action, before)

    def test_structured_message_and_native_queue_still_work(self):
        snapshot = self.backend.read_conversation()
        self.backend.generating = True
        status, result = self.request('/api/action', {'kind': 'message', 'text': '中文\n多行',
            'submit': True, 'conversationKey': snapshot['key'], 'epoch': snapshot['epoch']})
        self.assertEqual(status, 200)
        self.assertEqual(result['submission']['state'], 'queued')
        queue = self.backend.queue()
        action = next(x for x in queue['items'][0]['actions'] if x['operation'] == 'delete')
        status, result = self.request('/api/action', {'kind': 'queue', 'queueKey': queue['key'],
            'targetId': action['id'], 'epoch': snapshot['epoch']})
        self.assertEqual(status, 200)
        self.assertEqual(result['queue']['items'], [])

    def test_capabilities_and_local_compatibility_are_explicit(self):
        capabilities = self.state.info()['capabilities']
        self.assertEqual(capabilities['scope'], 'chatgpt-window')
        self.assertFalse(capabilities['desktop'])
        self.assertFalse(capabilities['rawInput'])
        self.assertTrue(capabilities['windowPointer'])
        with tempfile.TemporaryDirectory() as directory:
            local = AgentState(MockBackend(), Path(directory))
            try:
                self.assertTrue(local.info()['capabilities']['desktop'])
                self.assertTrue(local.info()['capabilities']['rawInput'])
            finally:
                local.close()

    def test_window_pointer_requires_a_recent_frame_of_the_selected_window(self):
        frame = self.state.hub.capture_now()
        command = {'kind': 'window-pointer', 'operation': 'right', 'epoch': frame['epoch'],
                   'frameId': frame['id'], 'x': .25, 'y': .75}
        self.assertEqual(self.request('/api/action', command)[0], 200)
        self.assertEqual(self.backend.last_action, 'right:0.25,0.75')
        self.assertEqual(self.request('/api/action', {**command, 'x': 2})[0], 400)
        self.assertEqual(self.request('/api/action', {**command, 'frameId': frame['id']+999})[0], 409)
        self.backend.freeze(20)
        self.backend.unfreeze()
        self.assertEqual(self.request('/api/action', {**command, 'epoch': self.backend.tracker.epoch})[0], 409)
        self.backend.desktop()
        self.assertEqual(self.request('/api/action', command)[0], 403)
        self.assertEqual(self.backend.last_action, 'right:0.25,0.75')

    def test_desktop_mode_cannot_be_used_to_bypass_public_action_policy(self):
        self.backend.desktop()
        with self.assertRaises(RemoteError):
            self.state.perform_action({'kind': 'message', 'submit': True,
                'text': 'test', 'conversationKey': 'x', 'epoch': self.backend.tracker.epoch}, self.token)
        with self.assertRaises(RemoteError):
            self.state.conversation()

    def test_composer_coordinate_control_denied_but_native_model_menu_works(self):
        options = self.backend.composer_options()
        command = {'kind': 'composer', 'optionsKey': options['key'], 'targetId': 'model',
                   'epoch': options['epoch'], 'operation': 'tap', 'x': .5, 'y': .5}
        self.assertEqual(self.request('/api/action', command)[0], 403)
        command['operation'] = 'invoke'
        status, result = self.request('/api/action', command)
        self.assertEqual(status, 200)
        self.assertTrue(result['options']['menu'])

    def get(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            connection.request('GET', path, headers={
                'X-Access-Key': 'isolated-test-access-key',
                'Authorization': 'Bearer ' + self.token,
            })
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type'), response.read()
        finally:
            connection.close()

    def test_public_frame_still_serves_current_window_capture(self):
        status, content_type, body = self.get('/api/frame')
        self.assertEqual(status, 200)
        self.assertEqual(content_type, 'image/jpeg')
        self.assertTrue(body.startswith(b'\xff\xd8'))

    def test_qr_pair_ticket_expires_without_interrupting_the_regular_entry(self):
        ticket=self.state.pin_ticket
        self.assertEqual(self.get('/?pair='+ticket)[0],200)
        self.state.pin_expires=time.monotonic()-1
        status,kind,body=self.get('/?pair='+ticket)
        self.assertEqual(status,403)
        self.assertIn('text/html',kind)
        self.assertIn('二维码已过期',body.decode())
        self.assertEqual(self.get('/')[0],200)

    def test_wgc_window_frames_pass_http_and_keep_pointer_provenance(self):
        frame=self.state.hub.capture_now()
        frame.update(method='wgc',pointer_target=(123,(10,20,800,600)))
        self.state.mock=False
        with patch.object(self.state.hub,'wait',return_value=frame):
            status,content_type,body=self.get('/api/frame?since=0')
        self.assertEqual(status,200)
        self.assertEqual(content_type,'image/jpeg')
        self.assertTrue(body.startswith(b'\xff\xd8'))
        published=self.state.hub._publish(frame)
        self.assertEqual(self.state.hub.pointer_frames[published['id']][2],frame['pointer_target'])

    def test_capture_finishing_after_mode_change_cannot_return_an_image(self):
        original = self.backend.capture_meta
        def changing_capture(*args):
            image = original(*args)
            self.backend.desktop()
            return image
        with patch.object(self.backend, 'capture_meta', changing_capture):
            status, content_type, body = self.get('/api/frame')
        self.assertEqual(status, 403)
        self.assertIn('application/json', content_type)
        self.assertNotIn(b'\xff\xd8', body)

    def test_cached_desktop_frame_is_rejected_after_returning_to_window_mode(self):
        self.backend.desktop()
        cached = self.state.hub.capture_now()
        self.assertEqual(cached['mode'], 'desktop')
        self.backend.window_mode()
        for path in ('/api/frame?since=0', '/api/stream'):
            with self.subTest(path=path), patch.object(self.state.hub, 'wait', return_value=cached):
                status, content_type, body = self.get(path)
                self.assertEqual(status, 403)
                self.assertIn('application/json', content_type)
                self.assertNotIn(b'\xff\xd8', body)

    def test_cached_window_frame_from_an_old_epoch_is_rejected(self):
        cached = self.state.hub.capture_now()
        self.backend.freeze()
        with patch.object(self.state.hub, 'wait', return_value=cached):
            for path in ('/api/frame?since=0', '/api/stream'):
                with self.subTest(path=path):
                    status, _, body = self.get(path)
                    self.assertEqual(status, 409)
                    self.assertNotIn(b'\xff\xd8', body)

    def test_screen_fallback_unknown_and_unlabelled_frames_fail_closed(self):
        cached = self.state.hub.capture_now()
        for changes in ({'method': 'screen'}, {'method': 'unknown'}, {'method': None}, {'mode': None}):
            frame = dict(cached, **changes)
            with self.subTest(changes=changes), patch.object(self.state.hub, 'wait', return_value=frame):
                status, _, body = self.get('/api/frame?since=0')
                self.assertEqual(status, 403)
                self.assertIn('当前无法安全读取窗口画面', json.loads(body)['error'])
        # A production backend cannot label real pixels as a test capture.
        self.state.mock = False
        with patch.object(self.state.hub, 'wait', return_value=cached):
            self.assertEqual(self.get('/api/frame?since=0')[0], 403)
        # Rejected visual paths do not disable the conversation channel.
        self.assertEqual(self.get('/api/conversation')[0], 200)

    def test_composer_surface_cannot_bypass_capture_method_policy(self):
        surface = {'mode': 'window', 'epoch': self.backend.tracker.epoch,
                   'method': 'screen', 'image': 'data:image/png;base64,unsafe-test-image'}
        with patch.object(self.backend, 'composer_surface', return_value=surface):
            status, _, body = self.get('/api/composer/surface?key=test&target=test')
            self.assertEqual(status, 403)
            self.assertNotIn(b'unsafe-test-image', body)
        surface['method'] = 'printwindow'
        with patch.object(self.backend, 'composer_surface', return_value=surface):
            self.assertEqual(self.get('/api/composer/surface?key=test&target=test')[0], 200)

    def test_stream_revalidates_after_encoding_without_leaking_image_bytes(self):
        self.state.hub.capture_now()
        original = FrameEncoder.encode
        def changing_encode(encoder, frame):
            packet = original(encoder, frame)
            self.backend.desktop()
            self.backend.window_mode()
            return packet
        with patch.object(FrameEncoder, 'encode', changing_encode):
            status, _, body = self.get('/api/stream')
        self.assertEqual(status, 200)
        size, image_size = struct.unpack('!II', body[:8])
        self.assertEqual(image_size, 0)
        self.assertEqual(json.loads(body[8:8+size])['kind'], 'status')
        self.assertEqual(len(body), 8+size)
        self.assertNotIn(b'HTTP/1.1', body)

    def test_open_stream_rejects_a_desktop_frame_from_the_next_wait(self):
        first = self.state.hub.capture_now()
        calls = []
        def next_frame(after_id, timeout):
            calls.append(after_id)
            if len(calls) == 1:
                return first
            self.backend.desktop()
            desktop = self.state.hub.capture_now()
            self.backend.window_mode()
            return desktop
        with patch.object(self.state.hub, 'wait', side_effect=next_frame):
            status, _, body = self.get('/api/stream')
        self.assertEqual(status, 200)
        packets = []
        offset = 0
        while offset < len(body):
            size, image_size = struct.unpack('!II', body[offset:offset+8])
            meta = json.loads(body[offset+8:offset+8+size])
            packets.append((meta, image_size))
            offset += 8+size+image_size
        self.assertEqual([meta['kind'] for meta, _ in packets], ['full', 'status'])
        self.assertGreater(packets[0][1], 0)
        self.assertEqual(packets[1][1], 0)
        self.assertEqual(len(calls), 2)

    def test_revocation_during_frame_wait_does_not_return_the_waited_image(self):
        cached = self.state.hub.capture_now()
        def revoked_wait(*args, **kwargs):
            self.state.tokens.revoke(self.token)
            return cached
        with patch.object(self.state.hub, 'wait', side_effect=revoked_wait):
            status, _, body = self.get('/api/frame?since=0')
        self.assertEqual(status, 401)
        self.assertNotIn(b'\xff\xd8', body)

    def test_mode_change_ends_ndjson_using_a_status_record(self):
        original = self.state.conversation
        calls = []
        def conversation():
            calls.append(True)
            if len(calls) > 1:
                self.backend.desktop()
            return original()
        with patch.object(self.state, 'conversation', side_effect=conversation):
            status, _, body = self.get('/api/conversation/stream')
        self.assertEqual(status, 200)
        records = [json.loads(line) for line in body.splitlines()]
        self.assertEqual([record['kind'] for record in records], ['conversation', 'status'])
        self.assertNotIn(b'HTTP/1.1', body)

    def test_capture_provenance_is_not_taken_from_shared_last_capture_method(self):
        from PIL import Image
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.lock = threading.RLock()
        backend.mode = 'window'
        backend.tracker = SimpleNamespace(epoch=7)
        backend._resolve_target = lambda: 123
        image = Image.new('RGB', (100, 100), 'white')
        image.info['rm_capture_method'] = 'screen'
        backend.last_capture_method = 'printwindow'  # Another concurrent capture.
        backend._grab_window = lambda *args: image
        meta = backend.capture_meta()
        self.assertEqual(meta['method'], 'screen')
        self.assertEqual(meta['mode'], 'window')
        self.assertEqual(meta['epoch'], 7)

    def test_real_websocket_dispatch_denies_raw_input_and_keeps_native_controls(self):
        def masked(value):
            payload = json.dumps(value).encode('utf-8')
            mask = b'RmQA'
            header = bytes([0x81, 0x80 | len(payload)]) if len(payload) < 126 else bytes([0x81, 0xFE]) + struct.pack('!H', len(payload))
            return header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        sock = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=4)
        reader = sock.makefile('rb')
        try:
            key = base64.b64encode(b'test-websocket01').decode('ascii')
            sock.sendall((f'GET /api/control HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                'Sec-WebSocket-Version: 13\r\nX-Access-Key: isolated-test-access-key\r\n\r\n').encode())
            self.assertIn(b' 101 ', reader.readline())
            while reader.readline() != b'\r\n':
                pass
            def receive():
                head = reader.read(2)
                self.assertEqual(len(head), 2)
                length = head[1] & 127
                if length == 126:
                    length = struct.unpack('!H', reader.read(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', reader.read(8))[0]
                return json.loads(reader.read(length))
            def action(ident, payload):
                sock.sendall(masked({'id': ident, 'type': 'action', 'payload': payload}))
                return receive()
            sock.sendall(masked({'type': 'auth', 'token': self.token}))
            self.assertTrue(receive()['ok'])
            epoch = self.backend.tracker.epoch
            self.assertEqual(action(1, {'kind': 'key', 'key': 'CTRL+V', 'epoch': epoch})['status'], 403)
            options = self.backend.composer_options()
            opened = action(2, {'kind': 'composer', 'operation': 'invoke', 'optionsKey': options['key'],
                'targetId': 'model', 'epoch': epoch})
            self.assertTrue(opened['ok'])
            options = opened['data']['options']
            selected = action(3, {'kind': 'composer', 'operation': 'invoke', 'optionsKey': options['key'],
                'targetId': options['items'][-1]['id'], 'epoch': epoch})
            self.assertTrue(selected['ok'])
            self.assertEqual(self.backend.option_values['model'], '示例模型 B')
            self.backend.generating = True
            conversation = self.backend.read_conversation()
            sent = action(4, {'kind': 'message', 'submit': True, 'text': '中文\n多行',
                'conversationKey': conversation['key'], 'epoch': epoch})
            self.assertTrue(sent['ok'])
            self.assertEqual(sent['data']['submission']['state'], 'queued')
            queue = self.backend.queue()
            delete = next(item for item in queue['items'][0]['actions'] if item['operation'] == 'delete')
            deleted = action(5, {'kind': 'queue', 'queueKey': queue['key'], 'targetId': delete['id'], 'epoch': epoch})
            self.assertTrue(deleted['ok'])
            self.assertEqual(deleted['data']['queue']['items'], [])
        finally:
            reader.close()
            sock.close()


if __name__ == '__main__':
    unittest.main()
