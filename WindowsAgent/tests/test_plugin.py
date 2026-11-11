"""End-to-end local MCP calls against an isolated Remote core, without UI input."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentState, MockBackend, RemoteError
from desktop_control import DesktopControl
from app_version import APP_VERSION
from plugin_popover import NativePopover, PLUGIN_ID
from remote_core import RemoteController


class ScanPairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = AgentState(MockBackend(), Path(self.temp.name))

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_ticket_pairs_once_and_displays_a_named_device(self):
        ticket = self.state.pin_ticket
        token = self.state.pair('127.0.0.1', '', '华为手机', ticket=ticket)
        self.assertTrue(self.state.tokens.valid(token))
        self.assertEqual(next(iter(self.state.tokens.entries.values()))['name'], '华为手机')
        with self.assertRaises(RemoteError):
            self.state.pair('127.0.0.1', '', 'Other', ticket=ticket)

    def test_manual_code_and_scan_share_one_pairing(self):
        pin, ticket = self.state.pin, self.state.pin_ticket
        self.state.pair('127.0.0.1', pin, 'Phone')
        with self.assertRaises(RemoteError):
            self.state.pair('127.0.0.1', '', 'Other', ticket=ticket)

    def test_expired_ticket_does_not_pair(self):
        self.state.pin_expires = 0
        with self.assertRaises(RemoteError):
            self.state.pair('127.0.0.1', '', 'Phone', ticket=self.state.pin_ticket)
        self.assertEqual(self.state.tokens.count(), 0)


class PluginSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'plugin'
        self.manifest = self.source / '.codex-plugin/plugin.json'
        self.manifest.parent.mkdir(parents=True)
        self.controller = SimpleNamespace(root=self.root, runtime=SimpleNamespace(
            running=lambda: {'version': APP_VERSION}))
        self.popup = NativePopover(self.controller, self.source / 'core/popover.dll')
        self.release(APP_VERSION)

    def tearDown(self):
        self.temp.cleanup()

    def release(self, version):
        self.manifest.write_text(json.dumps({'name': 'chatgpt-remote', 'version': version}), encoding='utf-8')

    def test_update_check_compares_versions_without_installing(self):
        with patch('plugin_popover.subprocess.run') as install:
            self.assertNotIn('UpdateVersion', self.popup.settings('settings.check', {}))
            self.release('2.10.0')
            self.assertEqual(self.popup.settings('settings.check', {})['UpdateVersion'], '2.10.0')
            self.assertEqual(self.popup.settings('settings.update', {'version': '2.9.0'})['UpdateVersion'], '2.10.0')
            install.assert_not_called()

    def test_manage_routes_to_this_local_plugin_for_user_confirmation(self):
        catalog = self.root / '.agents/plugins/marketplace.json'
        catalog.parent.mkdir(parents=True)
        catalog.write_text('{}', encoding='utf-8')
        with patch('plugin_popover.os.startfile', create=True) as open_page:
            self.assertEqual(self.popup.settings('settings.manage', {}), {})
        url = urlparse(open_page.call_args.args[0])
        self.assertEqual(url.scheme, 'codex')
        self.assertEqual(url.netloc, 'plugins')
        self.assertEqual(url.path, '/chatgpt-remote')
        self.assertEqual(parse_qs(url.query)['marketplacePath'], [str(catalog)])

    @unittest.skipUnless(os.name == 'nt', 'Windows plugin manager')
    def test_remove_targets_only_this_plugin(self):
        from unittest.mock import Mock

        close = Mock()
        self.controller.runtime.close = close
        executable = self.root / 'codex.exe'
        with (patch('plugin_popover.chatgpt_cli', return_value=executable),
              patch('plugin_popover.subprocess.run') as run,
              patch('plugin_popover.threading.Thread') as thread):
            self.assertEqual(self.popup.settings('settings.remove', {}), {'Message': '已卸载'})
        self.assertEqual(run.call_args.args[0], [str(executable), 'plugin', 'remove', PLUGIN_ID])
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)
        self.assertIs(thread.call_args.kwargs['target'], close)
        thread.return_value.start.assert_called_once_with()

    def test_install_uses_current_app_cli_and_confirms_installed_version(self):
        import hashlib
        self.release('2.10.0')
        core = self.source / 'core'
        core.mkdir()
        (core / 'netfx.config').write_text('<configuration/>', encoding='utf-8')
        checksum = hashlib.sha256((core / 'netfx.config').read_bytes()).hexdigest()
        (self.source / 'runtime-manifest.json').write_text(json.dumps({
            'schema': 1, 'version': '2.10.0', 'files': {'core/netfx.config': checksum}}), encoding='utf-8')
        codex = self.root / 'codex'
        program_files = self.root / 'ProgramFiles'
        exe = program_files / 'WindowsApps/OpenAI.Codex_1.2.3.0_x64__2p2nqsd0c76g0/app/ChatGPT.exe'
        installed = codex / 'plugins/cache/chatgpt-remote-local/chatgpt-remote/2.10.0/.codex-plugin/plugin.json'
        def finish_install(command, **options):
            self.assertEqual(command, [str(exe.parent / 'resources/codex.exe'), 'plugin', 'add', PLUGIN_ID, '--json'])
            self.assertEqual(options['cwd'], self.root)
            installed.parent.mkdir(parents=True)
            installed.write_text(self.manifest.read_text(encoding='utf-8'), encoding='utf-8')
            return SimpleNamespace(stdout=json.dumps({'version': '2.10.0'}))
        environment = {'CODEX_HOME': str(codex), 'ProgramFiles': str(program_files)}
        with patch.dict(os.environ, environment), patch('plugin_popover.subprocess.run', side_effect=finish_install):
            updated = self.popup.settings('settings.update', {'version': '2.10.0', 'executable': str(exe)})
        self.assertIn('2.10.0', updated['Message'])
        self.assertTrue(installed.is_file())


@unittest.skipUnless(os.name == 'nt', 'Windows local control pipe')
class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_protocol_panel_pairing_and_device_actions(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        root = Path(__file__).resolve().parents[2]
        plugin = root/'plugin'
        config = json.loads((plugin/'mcp.json').read_text(encoding='utf-8'))['mcpServers']['remote']
        self.assertEqual(config['command'], 'python')
        self.assertEqual(config['cwd'], './')
        self.assertEqual(config['args'], ['-B', './server.py'])
        with tempfile.TemporaryDirectory() as folder:
            state = AgentState(MockBackend(), Path(folder))
            control = DesktopControl(state, Path(folder), phone_url='https://remote.example.test:8443/')
            control.start()
            try:
                env = {**os.environ, **config['env'], 'CHATGPTREMOTE_DATA': folder, 'CHATGPTREMOTE_MOCK': '1'}
                params = StdioServerParameters(command=config['command'], args=config['args'],
                                              cwd=plugin/config['cwd'], env=env)
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as client:
                        initialized = await client.initialize()
                        self.assertEqual(initialized.serverInfo.name, 'chatgpt-remote')
                        tools = await client.list_tools()
                        self.assertEqual({tool.name for tool in tools.tools}, {'remote_status', 'remote_service'})
                        self.assertTrue(all(not tool.meta for tool in tools.tools))
                        status = await client.call_tool('remote_status', {})
                        self.assertTrue(status.structuredContent['running'])
                        self.assertIsNone(status.meta)
                        popup = NativePopover(RemoteController(root, Path(folder)), plugin / 'core/ChatGPTRemote.Popover.dll')
                        def act(operation, **payload):
                            return json.loads(popup.request(operation, json.dumps(payload)))
                        private = act('pair')
                        self.assertTrue(private['Qr'].startswith('iVBOR'))
                        self.assertNotIn('Pin', status.structuredContent)
                        state.pair('127.0.0.1', '', 'Fixture Phone', ticket=state.pin_ticket)
                        status = await client.call_tool('remote_status', {})
                        device = status.structuredContent['devices'][0]
                        renamed = act('device.rename', id=device['id'], name='New Name')
                        self.assertEqual(renamed['Devices'][0]['Name'], 'New Name')
                        self.assertTrue(act('target.pause', paused=True)['Paused'])
                        self.assertEqual(act('device.remove', id=device['id'])['Devices'], [])
                        self.assertIn('Error', act('unknown'))
            finally:
                control.close()
                state.close()
