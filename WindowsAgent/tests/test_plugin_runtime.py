"""Plugin lifecycle acceptance on isolated ports, data and mock windows."""
import asyncio
import contextlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentState, MockBackend
from desktop_control import DesktopClient, DesktopControl, DesktopControlError
from plugin_runtime import PluginRuntime, AppWindowBackend, bind_app_window
from windows_control import WindowsBackend, WindowTracker


class CurrentAppWindowTests(unittest.TestCase):
    def test_binding_follows_native_chatgpt_and_keeps_it_when_other_apps_are_active(self):
        backend = object.__new__(AppWindowBackend)
        backend.lock = threading.RLock()
        backend.mode = 'window'
        backend.tracker = WindowTracker()
        foreground = [22]
        backend.u32 = SimpleNamespace(GetForegroundWindow=lambda: foreground[0])
        base = {'exe_path': r'C:\Program Files\WindowsApps\OpenAI.Codex\app\ChatGPT.exe',
                'exe': 'ChatGPT.exe', 'class': 'Chrome_WidgetWin_1', 'pid': 44}
        windows = [{**base, 'id': 11, 'title': 'ChatGPT'}, {**base, 'id': 22, 'title': 'ChatGPT'},
                   {**base, 'id': 33, 'title': 'ChatGPTnx preview', 'exe': 'chrome.exe'}]
        state = SimpleNamespace(backend=backend, action_lock=threading.RLock())
        with patch.object(WindowsBackend, '_enumerate', return_value=windows):
            bind_app_window(state)
            self.assertEqual(backend.tracker.current_id, 22)
            epoch = backend.tracker.epoch
            foreground[0] = 33
            bind_app_window(state)
            self.assertEqual(backend.tracker.current_id, 22)
            self.assertEqual(backend.tracker.epoch, epoch)
            self.assertEqual([window['id'] for window in backend.list_windows()], [11, 22])
            foreground[0] = 11
            bind_app_window(state)
            self.assertEqual(backend.tracker.current_id, 11)
            self.assertGreater(backend.tracker.epoch, epoch)


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1', port)) == 0


@unittest.skipUnless(os.name == 'nt', 'Windows local connection')
class PluginRuntimeTests(unittest.TestCase):
    def test_attached_plugin_closing_keeps_primary_connection(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'CHATGPTREMOTE_MOCK': '1', 'CHATGPTREMOTE_PORT': '0'}):
            root = Path(folder)
            primary = PluginRuntime(root, root / '_data')
            attached = PluginRuntime(root, root / '_data')
            try:
                primary.ensure_started()
                port = primary.running()['localPort']
                attached.ensure_started()
                self.assertIsNone(attached.child)
                attached.close()
                self.assertTrue(listening(port))
                self.assertEqual(primary.running()['managedBy'], 'plugin')
            finally:
                attached.close()
                primary.close()
            self.assertFalse(listening(port))

    def test_parent_exit_stops_the_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            environment = dict(os.environ, CHATGPTREMOTE_MOCK='1', CHATGPTREMOTE_PORT='0')
            code = "from pathlib import Path;from plugin_runtime import PluginRuntime;import os;runtime=PluginRuntime(Path(os.environ['TEST_HOME']),Path(os.environ['TEST_HOME'])/'_data');runtime.ensure_started();print(runtime.running()['localPort'],flush=True);os._exit(0)"
            environment['TEST_HOME'] = folder
            environment['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), environment.get('PYTHONPATH', '')])
            result = subprocess.run([sys.executable, '-B', '-c', code], env=environment, capture_output=True,
                                    text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            port = int(result.stdout.strip())
            deadline = time.monotonic() + 8
            while listening(port) and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertFalse(listening(port))

    def test_occupied_port_leaves_device_storage_unopened(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            root = Path(folder)
            environment = dict(os.environ, CHATGPTREMOTE_MOCK='1', CHATGPTREMOTE_PORT=str(occupied.getsockname()[1]),
                               CHATGPTREMOTE_HOME=str(root), CHATGPTREMOTE_DATA=str(root / '_data'),
                               CHATGPTREMOTE_OWNER_PID=str(os.getpid()), CHATGPTREMOTE_OWNER_TOKEN='fixture')
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'plugin_runtime.py'), '--serve'],
                                    env=environment, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / '_data').exists())

    def test_only_maintenance_or_owner_can_shutdown_the_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            state = AgentState(MockBackend(), Path(folder))
            called = []
            control = DesktopControl(state, Path(folder), shutdown=lambda: called.append(True), owner_token='owner-fixture')
            try:
                with self.assertRaises(DesktopControlError):
                    control.dispatch('service.stop', {'token': 'wrong'})
                with self.assertRaises(DesktopControlError):
                    control.dispatch('service.close', {'owner': 'wrong'})
                self.assertEqual(called, [])
                token = control.dispatch('maintenance.prepare', {})['token']
                control.dispatch('service.stop', {'token': token})
                deadline = time.monotonic() + 1
                while not called and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(called, [True])
            finally:
                control.close()
                state.close()


@unittest.skipUnless(os.name == 'nt', 'Windows local connection')
class HostedPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_owns_start_stop_restart_and_preserves_pairing_without_desktop_exe(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        plugin = Path(__file__).resolve().parents[2] / 'plugin'
        config = json.loads((plugin / 'mcp.json').read_text(encoding='utf-8'))['mcpServers']['remote']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / '_data'
            fixture = AgentState(MockBackend(), data)
            fixture.tokens.issue('Existing phone')
            fixture.close()
            before = (data / 'paired_devices.json').read_bytes()
            environment = {**os.environ, **config['env'], 'CHATGPTREMOTE_PROJECT': str(root),
                           'CHATGPTREMOTE_DATA': str(data), 'CHATGPTREMOTE_MOCK': '1', 'CHATGPTREMOTE_PORT': '0'}
            params = StdioServerParameters(command=config['command'], args=config['args'], cwd=plugin, env=environment)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    for _ in range(100):
                        status = await client.call_tool('remote_status', {})
                        if status.structuredContent.get('running'):
                            break
                        await asyncio.sleep(.15)
                    self.assertTrue(status.structuredContent['running'])
                    self.assertEqual(status.structuredContent['managedBy'], 'plugin')
                    port = status.structuredContent['localPort']
                    self.assertTrue(listening(port))
                    tools = await client.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools}, {'remote_status', 'remote_service'})
                    self.assertTrue(all(not tool.meta for tool in tools.tools))
                    self.assertFalse((root / 'ChatGPTRemote.exe').exists())
                    stopped = await client.call_tool('remote_service', {'action': 'stop'})
                    self.assertFalse(stopped.isError, str(stopped.content))
                    self.assertFalse(stopped.structuredContent['running'])
                    self.assertEqual(len(stopped.structuredContent['devices']), 1)
                    self.assertFalse(listening(port))
                    started = await client.call_tool('remote_service', {'action': 'start'})
                    self.assertFalse(started.isError, str(started.content))
                    self.assertTrue(started.structuredContent['running'])
                    restarted = await client.call_tool('remote_service', {'action': 'restart'})
                    self.assertFalse(restarted.isError, str(restarted.content))
                    self.assertTrue(restarted.structuredContent['running'])
                    final_port = restarted.structuredContent['localPort']
            self.assertFalse(listening(final_port))
            self.assertEqual((data / 'paired_devices.json').read_bytes(), before)
