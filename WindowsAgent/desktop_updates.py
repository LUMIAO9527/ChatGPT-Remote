"""Remote release actions and public support details."""
import threading
import re
from app_paths import ROOT, HERE, EXECUTABLE
from release_updater import UpdateManager, version_key, startup_ack

REPOSITORY = 'LUMIAO9527/ChatGPT-Remote'
PROJECT_URL = 'https://github.com/' + REPOSITORY
CONTACT_QQ = '210037309'


def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d+\.\d+\.\d+',value):return None
    try: return version_key(value)
    except ValueError: return None
    except RuntimeError: return None


class ReleaseUpdates(UpdateManager):
    def __init__(self,current):
        super().__init__(ROOT,HERE,executable=EXECUTABLE,current=current)
        self.monitor_stop=threading.Event()

    def check(self,force=False,*,background=None):
        super().check(background=not force if background is None else background)
        return self.snapshot()

    def start(self):
        self.start_monitor(self.monitor_stop)
