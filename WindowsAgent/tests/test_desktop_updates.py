"""Release checks use public metadata only, with no network in the tests."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_updates import ReleaseUpdates,version_tuple


class UpdateTests(unittest.TestCase):
    def test_semantic_versions_reject_non_release_input(self):
        self.assertEqual(version_tuple('v0.16.0'),(0,16,0))
        for value in ('v1.2',None,'v1.2.3-beta','<script>'):
            self.assertIsNone(version_tuple(value))

    def response(self,version='v0.17.0',**extra):
        return io.BytesIO(json.dumps({'tag_name':version,'body':'新版说明',**extra}).encode())

    def test_new_release_is_available_and_uses_project_release_url(self):
        updates=ReleaseUpdates('0.16.0')
        with patch('desktop_updates.urllib.request.urlopen',return_value=self.response()) as read:
            result=updates.check(True)
        self.assertEqual(result['status'],'available')
        self.assertTrue(result['url'].endswith('/ChatGPT-Remote/releases/tag/v0.17.0'))
        self.assertEqual(read.call_args.args[0].headers['User-agent'],'ChatGPT-Remote/0.16.0')

    def test_same_release_is_current_and_recent_result_is_reused(self):
        updates=ReleaseUpdates('0.16.0')
        with patch('desktop_updates.urllib.request.urlopen',return_value=self.response('v0.16.0')) as read:
            self.assertEqual(updates.check(True)['status'],'current')
            self.assertEqual(updates.check()['status'],'current')
            read.assert_called_once()

    def test_first_background_check_runs_even_just_after_boot(self):
        updates=ReleaseUpdates('0.16.0')
        with patch('desktop_updates.time.monotonic',return_value=10), \
             patch('desktop_updates.urllib.request.urlopen',return_value=self.response('v0.16.0')) as read:
            self.assertEqual(updates.check()['status'],'current')
            read.assert_called_once()

    def test_failure_retains_release_identity_and_supports_explicit_retry(self):
        updates=ReleaseUpdates('0.16.0')
        with patch('desktop_updates.urllib.request.urlopen',side_effect=OSError('offline')):
            self.assertEqual(updates.check(True)['status'],'error')
        with patch('desktop_updates.urllib.request.urlopen',return_value=self.response('v0.16.0')):
            self.assertEqual(updates.check(True)['status'],'current')

    def test_preview_or_invalid_release_cannot_trigger_download(self):
        for options in ({'prerelease':True},{'draft':True},{'tag_name':'invalid'}):
            response={'tag_name':'v0.17.0',**options}
            with patch('desktop_updates.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
                self.assertEqual(ReleaseUpdates('0.16.0').check(True)['status'],'error')
