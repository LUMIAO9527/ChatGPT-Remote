"""Exercise the real local pipe with an isolated, synthetic backend."""
import contextlib
import hashlib
import json
import math
from multiprocessing.connection import Client
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentState, MockBackend
from desktop_control import (DesktopClient, DesktopControl, DesktopControlError,
                             _mac, _wire, local_secret, pipe_address)
from desktop_manager import DesktopManager, task_command


@unittest.skipUnless(os.name == 'nt', 'Windows local management')
class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.backend = MockBackend()
        self.state = AgentState(self.backend, self.folder, access_key='fixture-access-key')
        self.control = DesktopControl(self.state, self.folder, phone_url='https://remote.example.test:8443/')
        self.control.start()
        self.client = DesktopClient(self.folder)

    def tearDown(self):
        self.control.close()
        self.state.close()
        self.temporary.cleanup()

    def packet(self, body, *, ident=None, key=None):
        ident = ident or secrets.token_hex(16)
        return {'id': ident, 'body': body, 'auth': _mac(key or self.control.key, ident, body)}

    def raw(self, packet):
        with Client(pipe_address(self.folder), family='AF_PIPE') as connection:
            connection.send_bytes(_wire(packet))
            try:
                self.assertTrue(connection.poll(3))
                return json.loads(connection.recv_bytes(65536))
            except (EOFError, BrokenPipeError):
                return None

    def test_dpapi_key_is_persistent_encrypted_and_not_silently_rotated(self):
        key = local_secret(self.folder)
        encrypted = (self.folder / 'desktop_control.dat').read_bytes()
        self.assertEqual(key, self.control.key)
        self.assertNotEqual(encrypted, key)
        self.assertNotIn(key, encrypted)
        self.assertEqual(local_secret(self.folder), key)
        (self.folder / 'desktop_control.dat').write_bytes(b'broken')
        with self.assertRaises(DesktopControlError):
            local_secret(self.folder, create=True)
        self.assertEqual((self.folder / 'desktop_control.dat').read_bytes(), b'broken')

    def test_status_does_not_read_content_or_reveal_credentials(self):
        token = self.state.tokens.issue('Phone')
        with patch.object(self.backend, 'read_conversation', side_effect=AssertionError('content read')):
            status = self.client.request('status')
        self.assertTrue(status['running'])
        encoded = json.dumps(status)
        for value in (token, hashlib.sha256(token.encode()).hexdigest(), self.state.pin, 'fixture-access-key'):
            self.assertNotIn(value, encoded)
        self.assertEqual(status['devices'][0]['name'], 'Phone')

    def test_authentication_expiry_nan_and_replay_are_rejected(self):
        body = {'operation': 'status', 'payload': {}, 'at': time.time()}
        self.assertIsNone(self.raw(self.packet(body, key=b'x'*32)))
        self.assertIsNone(self.raw(self.packet(dict(body, at=time.time()-40))))
        self.assertIsNone(self.raw(self.packet(dict(body, at=math.nan))))
        packet = self.packet(body)
        reply = self.raw(packet)
        self.assertTrue(reply['body']['ok'])
        self.assertEqual(reply['auth'], _mac(self.control.key, packet['id'], reply['body']))
        self.assertIsNone(self.raw(packet))
        self.assertIsNone(self.raw([]))
        self.assertTrue(self.client.request('status')['running'])

    def test_pairing_is_on_demand_single_use_and_keeps_existing_devices(self):
        token = self.state.tokens.issue('Existing phone')
        result = self.client.request('pair')
        self.assertRegex(result['pin'], r'^[0-9]{6}$')
        self.assertEqual(result['pin'], self.client.request('pair')['pin'])
        self.assertIn('?k=fixture-access-key', result['url'])
        self.assertGreater(result['expiresAt'], time.time()+890)
        paired = self.state.pair('127.0.0.1', result['pin'], 'New phone')
        self.assertTrue(self.state.tokens.valid(paired))
        self.assertTrue(self.state.tokens.valid(token))
        with self.assertRaises(Exception):
            self.state.pair('127.0.0.1', result['pin'], 'Duplicate')
        next_pair = self.client.request('pair')
        self.assertTrue(self.state.tokens.valid(token))
        self.assertRegex(next_pair['pin'], r'^[0-9]{6}$')

    def test_device_management_revokes_only_selected_device_and_persists(self):
        first = self.state.tokens.issue('First')
        second = self.state.tokens.issue('Second')
        devices = self.client.request('status')['devices']
        self.client.request('device.rename', {'id': devices[0]['id'], 'name': 'My phone'})
        entries = json.loads((self.folder / 'paired_devices.json').read_text())
        self.assertEqual(entries[hashlib.sha256(first.encode()).hexdigest()]['name'], 'My phone')
        self.client.request('device.remove', {'id': devices[0]['id']})
        self.assertFalse(self.state.tokens.valid(first))
        self.assertTrue(self.state.tokens.valid(second))
        self.assertEqual(self.client.request('status')['devices'][0]['id'], devices[1]['id'])

    def test_selection_and_pause_use_backend_guard_and_save_target(self):
        before = self.backend.tracker.epoch
        result = self.client.request('target.select', {'id': 1})
        self.assertTrue(result['target']['selected'])
        self.assertTrue((self.folder / 'session.json').exists())
        self.assertGreater(self.backend.tracker.epoch, before)
        result = self.client.request('target.pause', {'paused': True})
        self.assertTrue(result['paused'])
        self.assertFalse(self.client.request('target.pause', {'paused': False})['paused'])
        for operation, payload in [('target.select', {'id': '1'}), ('target.pause', {'paused': 'yes'}),
                                   ('device.rename', {'id': 'x', 'name': 'a\nb'}), ('unknown', {})]:
            with self.subTest(operation=operation), self.assertRaises(DesktopControlError):
                self.client.request(operation, payload)

    def test_desktop_bridge_qr_settings_and_explicit_action_boundary(self):
        manager = DesktopManager(self.folder)
        manager.settings_path = self.folder / 'desktop_settings.json'
        result = manager.call('pair')
        self.assertTrue(result['ok'])
        self.assertTrue(result['qr'].startswith('data:image/png;base64,'))
        self.assertTrue(manager.call('settings.theme', {'theme': 'dark'})['ok'])
        self.assertEqual(manager.call('status')['preferences']['theme'], 'dark')
        for action, payload in [('shell', {'cmd': 'anything'}), ('settings.theme', {'theme': '<script>'}),
                                ('settings.autostart', {'enabled': 'yes'}), ('target.select', {'id': []})]:
            with self.subTest(action=action):
                self.assertFalse(manager.call(action, payload)['ok'])
        with patch('desktop_manager.subprocess.run', side_effect=AssertionError('unexpected command')):
            self.assertTrue(manager.call('status')['ok'])
            with self.assertRaises(DesktopControlError):
                task_command('Remove')

    def test_management_is_not_exposed_on_phone_http(self):
        import http.client
        from http.server import ThreadingHTTPServer
        import threading
        from agent import make_handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        token = self.state.tokens.issue('Phone')
        try:
            for endpoint in ('/api/desktop/status', '/api/desktop/pair', '/api/desktop/device.remove'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                connection.request('POST', endpoint, b'{}', {'X-Access-Key':'fixture-access-key',
                                   'Authorization': 'Bearer '+token, 'Content-Type':'application/json'})
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read();connection.close()
        finally:
            server.shutdown();server.server_close()

    def test_stopped_service_keeps_paired_device_list_without_exposing_digests(self):
        token = self.state.tokens.issue('Offline phone')
        manager = DesktopManager(self.folder)
        with patch.object(manager.client, 'request', side_effect=DesktopControlError('后台服务尚未连接')):
            result = manager.status()
        self.assertFalse(result['running'])
        self.assertEqual(result['devices'][0]['name'], 'Offline phone')
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(hashlib.sha256(token.encode()).hexdigest(), json.dumps(result))


if __name__ == '__main__':
    unittest.main()
