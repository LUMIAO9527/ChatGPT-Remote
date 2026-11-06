"""Status freshness at the real desktop bridge boundary, without Windows IPC."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_control import DesktopControl, DesktopControlError
from desktop_manager import DesktopManager


class DesktopStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.manager = object.__new__(DesktopManager)
        self.manager.client = Mock(folder=self.folder)
        self.manager.last_status = None
        self.manager.installation = Mock()
        self.manager.installation.status.return_value = {'firstRun': False}
        self.manager.preferences = Mock(return_value={'theme': 'system'})
        self.manager.updates = Mock()
        self.manager.updates.snapshot.return_value = {'status': 'idle'}
        data = patch('desktop_manager.DATA', self.folder)
        data.start()
        self.addCleanup(data.stop)
        self.live = {'running': True, 'version': '0.16.0',
                     'windows': [{'id': 1, 'title': 'ChatGPT', 'selected': True}],
                     'devices': [{'id': 'phone', 'name': 'Phone'}],
                     'phoneUrl': 'https://computer.example.test:8443/'}

    def test_update_status_reads_the_updater_without_contacting_the_agent(self):
        self.manager.client.request.side_effect = DesktopControlError('后台暂时没有响应')
        self.manager.updates.snapshot.return_value = {'status': 'available', 'version': '1.0.1'}
        self.assertEqual(self.manager.call('updates.status'), {
            'ok': True, 'update': {'status': 'available', 'version': '1.0.1'}})
        self.manager.client.request.assert_not_called()

    def test_bridge_marks_timeout_cache_and_clears_staleness_after_recovery(self):
        recovered = {**self.live, 'paused': True,
                     'windows': [{'id': 2, 'title': 'New conversation', 'selected': True}]}
        self.manager.client.request.side_effect = [
            self.live, DesktopControlError('后台暂时没有响应'),
            DesktopControlError('后台暂时没有响应'), recovered,
            DesktopControlError('后台暂时没有响应')]

        first = self.manager.call('status')
        self.assertTrue(first['ok'])
        self.assertIs(first['stale'], False)
        snapshot = self.manager.last_status
        for _ in range(2):
            cached = self.manager.call('status')
            self.assertTrue(cached['ok'])  # IPC fallback is still a successful bridge call.
            self.assertIs(cached['stale'], True)
            self.assertTrue(cached['running'])
            self.assertEqual(cached['windows'], first['windows'])
            self.assertEqual(cached['devices'], first['devices'])
            self.assertIs(self.manager.last_status, snapshot)
            self.assertIs(snapshot['stale'], False)
            self.assertIs(first['stale'], False)

        fresh = self.manager.call('status')
        self.assertTrue(fresh['ok'])
        self.assertIs(fresh['stale'], False)
        self.assertEqual(fresh['windows'][0]['id'], 2)
        self.assertTrue(fresh['paused'])
        self.assertIs(self.manager.last_status['stale'], False)
        cached_again = self.manager.call('status')
        self.assertIs(cached_again['stale'], True)
        self.assertEqual(cached_again['windows'], fresh['windows'])
        self.assertTrue(cached_again['paused'])
        self.assertIs(self.manager.last_status['stale'], False)
        self.manager.client.request.assert_called_with('status', timeout=2)

    def test_connection_failures_use_offline_devices_instead_of_the_live_cache(self):
        digest = 'd' * 64
        (self.folder / 'paired_devices.json').write_text(json.dumps({
            digest: {'name': 'Offline phone', 'created': 123}}), encoding='utf-8')
        for error in ('后台服务尚未启动', '后台服务尚未连接', '无法验证本机管理服务'):
            with self.subTest(error=error):
                self.manager.last_status = {**self.live, 'stale': False}
                self.manager.client.request.side_effect = DesktopControlError(error)
                offline = self.manager.call('status')
                self.assertTrue(offline['ok'])
                self.assertFalse(offline['running'])
                self.assertFalse(offline.get('stale', False))
                self.assertEqual(offline['windows'], [])
                self.assertEqual(offline['phoneUrl'], '')
                self.assertEqual(offline['devices'], [{
                    'id': DesktopControl.device_id(digest),
                    'name': 'Offline phone', 'created': 123}])
                self.assertNotIn(digest, json.dumps(offline))

    def test_first_timeout_without_cache_does_not_fabricate_a_live_snapshot(self):
        self.manager.client.request.side_effect = DesktopControlError('后台暂时没有响应')
        result = self.manager.call('status')
        self.assertTrue(result['ok'])
        self.assertFalse(result['running'])
        self.assertEqual(result['windows'], [])
        self.assertEqual(result['devices'], [])
        self.assertEqual(result['phoneUrl'], '')
        self.assertIsNone(self.manager.last_status)


if __name__ == '__main__':
    unittest.main()
