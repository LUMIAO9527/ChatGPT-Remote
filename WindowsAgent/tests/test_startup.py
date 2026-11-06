"""Startup remains independent of optional Windows registration writes."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_paths import external_environment
from app_version import APP_VERSION
from desktop_installation import refresh_application
from agent import tailscale_dns_name
from desktop_manager import DesktopManager
from app_paths import ROOT, EXECUTABLE


class StartupTests(unittest.TestCase):
    def readiness_manager(self):
        manager = object.__new__(DesktopManager)
        manager.installation = Mock()
        manager.installation.status.return_value = {'firstRun': False}
        manager.panel = Mock()
        manager.update_ack = (Path('started.json'), 'fixture-token')
        manager.restore_service = True
        manager.service = Mock()
        return manager

    def test_update_ack_waits_for_background_without_blocking_window_ready(self):
        manager = self.readiness_manager()
        work = []
        with patch('desktop_manager.threading.Thread', side_effect=lambda **kw: Mock(start=lambda: work.append(kw['target']))), \
                patch('desktop_updates.startup_ack') as acknowledge:
            self.assertEqual(manager._call('window.ready', {}), {})
            manager.service.assert_not_called()
            acknowledge.assert_not_called()
            self.assertEqual(len(work), 1)
            work[0]()
            manager.service.assert_called_once_with('start')
            acknowledge.assert_called_once_with(ROOT, EXECUTABLE, Path('started.json'), 'fixture-token')
        self.assertIsNone(manager.update_ack)

    def test_failed_background_start_cannot_acknowledge_successful_update(self):
        manager = self.readiness_manager()
        manager.service.side_effect = RuntimeError('fixture')
        work = []
        with patch('desktop_manager.threading.Thread', side_effect=lambda **kw: Mock(start=lambda: work.append(kw['target']))), \
                patch('desktop_updates.startup_ack') as acknowledge, self.assertLogs('remote.desktop', level='ERROR'):
            manager._call('window.ready', {})
            work[0]()
            acknowledge.assert_not_called()

    def registry(self, root, version):
        key = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=key)
        context.__exit__ = Mock(return_value=False)
        return types.SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=Mock(return_value=context),
            QueryValueEx=Mock(side_effect=[(str(root), 1), (version, 1)]))

    def test_matching_registration_does_not_request_write_access(self):
        exe = Path('ChatGPTRemote.exe').resolve()
        with patch.dict(sys.modules, {'winreg':self.registry(exe.parent, APP_VERSION)}), \
                patch('desktop_installation.register_application') as register:
            self.assertTrue(refresh_application(exe))
            register.assert_not_called()

    def test_registration_write_denial_is_deferred_without_aborting_startup(self):
        exe = Path('ChatGPTRemote.exe').resolve()
        log = Mock()
        with patch.dict(sys.modules, {'winreg':self.registry(exe.parent, '0.0.1')}), \
                patch('desktop_installation.register_application', side_effect=PermissionError(5, 'fixture')):
            self.assertFalse(refresh_application(exe, log=log))
            log.warning.assert_called_once()

    def test_another_installation_is_not_silently_reregistered(self):
        exe = Path('ChatGPTRemote.exe').resolve()
        with patch.dict(sys.modules, {'winreg':self.registry(exe.parent/'other', APP_VERSION)}):
            with self.assertRaises(ValueError): refresh_application(exe)

    def test_windows_tools_do_not_inherit_bundle_and_powershell_module_paths(self):
        with patch.dict(os.environ, {'_PYI_APPLICATION_HOME_DIR':'fixture', 'PYTHONPATH':'fixture',
                                     'PSModulePath':'fixture', 'CHATGPTREMOTE_SETUP_RUNTIME':'fixture'}):
            environment = external_environment()
        for name in ('_PYI_APPLICATION_HOME_DIR', 'PYTHONPATH', 'PSModulePath', 'CHATGPTREMOTE_SETUP_RUNTIME'):
            self.assertNotIn(name, environment)

    def test_tailscale_can_be_found_without_an_interactive_shell_path(self):
        result = Mock(returncode=0, stdout='{"Self":{"DNSName":"fixture.ts.net."}}')
        with patch('agent.shutil.which', return_value=None), patch('agent.Path.is_file', return_value=True), \
                patch('agent.subprocess.run', return_value=result) as run:
            self.assertEqual(tailscale_dns_name(), 'fixture.ts.net')
            self.assertTrue(run.call_args.args[0][0].lower().endswith('tailscale.exe'))
            self.assertTrue(Path(run.call_args.args[0][0]).is_absolute())


if __name__ == '__main__': unittest.main()
