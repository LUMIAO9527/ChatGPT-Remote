"""Read public release metadata; opening and downloading stay user actions."""
from __future__ import annotations
import json
import re
import threading
import time
import urllib.error
import urllib.request

REPOSITORY = 'LUMIAO9527/ChatGPT-Remote'
PROJECT_URL = 'https://github.com/' + REPOSITORY
CONTACT_QQ = '210037309'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    return tuple(map(int, match.groups())) if match else None


class ReleaseUpdates:
    def __init__(self, current):
        self.current=current;self.lock=threading.Lock();self.checked_at=None
        self.state={'status':'idle','version':current,'url':PROJECT_URL+'/releases/latest','notes':''}

    def snapshot(self):
        with self.lock:return dict(self.state)

    def check(self, force=False):
        with self.lock:
            if self.state['status']=='checking' or not force and self.checked_at is not None and time.monotonic()-self.checked_at<21600:
                return dict(self.state)
            self.state={**self.state,'status':'checking'}
        try:
            request=urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/releases/latest',
                                          headers={'User-Agent':'ChatGPT-Remote/'+self.current,'Accept':'application/vnd.github+json'})
            with urllib.request.urlopen(request,timeout=7) as response:
                data=json.loads(response.read(131073))
            version=version_tuple(data.get('tag_name'))
            if version is None or data.get('draft') or data.get('prerelease'):
                raise ValueError('invalid release')
            available=version>version_tuple(self.current)
            result={'status':'available' if available else 'current','version':data['tag_name'].lstrip('v'),
                    'url':PROJECT_URL+'/releases/tag/'+data['tag_name'],'notes':str(data.get('body') or '')[:5000]}
            delay=21600
        except Exception:
            result={**self.snapshot(),'status':'error','message':'无法连接 GitHub'};delay=900
        with self.lock:
            self.state=result;self.checked_at=time.monotonic()-(21600-delay)
            return dict(result)

    def start(self):
        thread=threading.Thread(target=self.check,daemon=True,name='release-check')
        thread.start()
