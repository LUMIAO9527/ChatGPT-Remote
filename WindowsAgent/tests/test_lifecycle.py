"""Installation ownership, retained data and maintenance checks use synthetic files."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_installation import Installation,MARKER,REDIRECT,installation_record,plain_path
from desktop_uninstall import owned_targets
from desktop_control import DesktopControl,DesktopControlError
from agent import AgentState,MockBackend,RemoteError
from desktop_runtime import verify_bundle
from remote_main import set_launcher_identity


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.origin=self.root/'download';self.origin.mkdir()
        self.exe=self.origin/'ChatGPTRemote.exe';self.exe.write_bytes(b'MZ'+b'fixture'*300)
        self.register=Mock();self.install=Installation(self.exe,frozen=True,register=self.register)
    def tearDown(self):self.temp.cleanup()
    def test_inspecting_first_use_does_not_create_data_or_registration(self):
        before=list(self.origin.iterdir());self.assertTrue(self.install.status()['firstRun'])
        self.assertEqual(list(self.origin.iterdir()),before);self.register.assert_not_called()
    def test_install_copies_only_program_and_ownership_record(self):
        destination=self.root/'installed';result=self.install.install(str(destination))
        self.assertEqual(Path(result['executable']).read_bytes(),self.exe.read_bytes())
        self.assertTrue(installation_record(destination));self.assertTrue(self.exe.is_file())
        self.assertEqual(self.install.redirected_executable(),destination/'ChatGPTRemote.exe')
        self.assertEqual({f.name for f in destination.iterdir()},{'ChatGPTRemote.exe',MARKER})
    def test_failed_registration_removes_only_its_new_files(self):
        destination=self.root/'installed';destination.mkdir();note=destination/'notes.txt';note.write_bytes(b'original')
        self.register.side_effect=OSError('fixture failure')
        with self.assertRaises(OSError):self.install.install(str(destination))
        self.assertEqual(list(destination.iterdir()),[note]);self.assertEqual(note.read_bytes(),b'original')
    def test_other_installation_and_data_are_not_overwritten(self):
        destination=self.root/'installed';destination.mkdir();data=destination/'_data';data.mkdir()
        (data/'paired_devices.json').write_bytes(b'original-pairing')
        with self.assertRaises(ValueError):self.install.install(str(destination))
        self.assertEqual((data/'paired_devices.json').read_bytes(),b'original-pairing')
    def test_reinstall_to_retained_owned_data_preserves_bytes_and_identity(self):
        destination=self.root/'installed';self.install.install(str(destination));before=installation_record(destination)
        (destination/'ChatGPTRemote.exe').unlink();data=destination/'_data';data.mkdir();(data/'paired_devices.json').write_bytes(b'private-fixture')
        self.install.install(str(destination))
        self.assertEqual(installation_record(destination)['id'],before['id']);self.assertEqual((data/'paired_devices.json').read_bytes(),b'private-fixture')
    def test_corrupt_or_foreign_redirect_does_not_launch_anything(self):
        (self.origin/REDIRECT).write_text(json.dumps({'app':'other','schema':1,'root':str(self.root),'id':'a'*32}))
        self.assertIsNone(self.install.redirected_executable())
    def test_hardlinked_program_is_rejected(self):
        link=self.origin/'alias.exe';os.link(self.exe,link)
        with self.assertRaises(ValueError):plain_path(link)
    def test_uninstall_keeps_data_by_default_and_never_selects_the_project_folder(self):
        keep=owned_targets(self.origin,True);clear=owned_targets(self.origin,False)
        self.assertNotIn(self.origin,keep);self.assertNotIn(self.origin/'WindowsAgent',clear)
        self.assertNotIn(self.origin/'_data',keep);self.assertIn(self.origin/'_data',clear)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=AgentState(MockBackend(),Path(self.temp.name),mock=True)
        # Dispatch tests need no local pipe or DPAPI key.
        self.control=object.__new__(DesktopControl);self.control.state=self.state
    def tearDown(self):self.state.close();self.temp.cleanup()
    def test_pending_delivery_blocks_maintenance(self):
        for value in ('sending','confirming','unknown'):
            self.state.deliveries.records={('owner','fixture'):{'state':value}}
            with self.assertRaises(DesktopControlError):self.control.dispatch('maintenance.prepare',{})
            self.assertFalse(self.state.maintenance_token)
    def test_handoff_blocks_new_actions_and_cancel_restores_them(self):
        token=self.control.dispatch('maintenance.prepare',{})['token']
        with self.assertRaises(RemoteError):self.state.perform_action({'kind':'fixture'})
        with self.assertRaises(DesktopControlError):self.control.dispatch('maintenance.cancel',{'token':'wrong'})
        self.control.dispatch('maintenance.cancel',{'token':token});self.assertFalse(self.state.maintenance_active())
    def test_lost_handoff_expires_instead_of_permanently_disabling_remote(self):
        self.control.dispatch('maintenance.prepare',{});self.state.maintenance_expires=time.monotonic()-1
        self.assertFalse(self.state.maintenance_active())


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'netfx.config').write_bytes(b'fixture-config');(self.root/'component.dll').write_bytes(b'fixture-component')
        self.manifest={'schema':1,'files':{name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in ('netfx.config','component.dll')}}
        self.save()
    def save(self):
        (self.root/'netfx-bundle.json').write_text(json.dumps(self.manifest),encoding='utf8')
    def tearDown(self):self.temp.cleanup()
    def test_matching_packaged_components_are_accepted(self):verify_bundle(self.root)
    def test_tampered_component_or_configuration_is_rejected(self):
        for name in ('component.dll','netfx.config'):
            old=(self.root/name).read_bytes();(self.root/name).write_bytes(b'changed')
            with self.assertRaises(OSError):verify_bundle(self.root)
            (self.root/name).write_bytes(old)
    def test_manifest_cannot_read_a_path_outside_its_bundle(self):
        self.manifest['files']['../outside.dll']='0'*64;self.save()
        with self.assertRaises(OSError):verify_bundle(self.root)
    def test_runtime_configuration_must_be_in_the_sealed_manifest(self):
        del self.manifest['files']['netfx.config'];self.save()
        with self.assertRaises(OSError):verify_bundle(self.root)


class LauncherIdentityTests(unittest.TestCase):
    def test_panel_acknowledges_the_candidate_tray_bootloader(self):
        with patch.dict(os.environ,{'CHATGPTREMOTE_LAUNCHER_PID':'111'}),patch('remote_main.os.getppid',return_value=222):
            set_launcher_identity(['--window','--update-ack','fixture','--update-token','a'*32])
            self.assertEqual(os.environ['CHATGPTREMOTE_LAUNCHER_PID'],'111')
    def test_candidate_tray_records_its_own_outer_bootloader(self):
        with patch.dict(os.environ,{'CHATGPTREMOTE_LAUNCHER_PID':'111'}),patch('remote_main.os.getppid',return_value=222):
            set_launcher_identity(['--update-ack','fixture','--update-token','a'*32])
            self.assertEqual(os.environ['CHATGPTREMOTE_LAUNCHER_PID'],'222')
