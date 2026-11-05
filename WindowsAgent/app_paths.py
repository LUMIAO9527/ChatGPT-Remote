"""Keep bundled resources separate from the user's persistent application files."""
from pathlib import Path
import os
import subprocess
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
HERE = Path(__file__).resolve().parent
ROOT = Path(sys.executable).resolve().parent if FROZEN else HERE.parent
DATA = ROOT / '_data' if FROZEN else HERE / 'data'
RUNTIME = ROOT / '_wip' / 'desktop-runtime'
EXECUTABLE = ROOT / 'ChatGPTRemote.exe' if FROZEN else Path(sys.executable)


def launch_command(role='tray', *arguments):
    if FROZEN:
        return [str(EXECUTABLE), '--' + role, *arguments]
    interpreter = Path(sys.executable).with_name('pythonw.exe')
    if not interpreter.is_file(): interpreter = Path(sys.executable)
    script = 'background_agent.py' if role == 'agent' else 'desktop_app.py'
    flags = ['--window'] if role == 'window' else []
    return [str(interpreter), '-B', str(HERE / script), *flags, *arguments]


def child_environment():
    environment = dict(os.environ)
    environment.pop('CHATGPTREMOTE_SETUP_RUNTIME', None)
    if FROZEN:
        environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return environment


def launch(role='tray', *arguments):
    return subprocess.Popen(launch_command(role, *arguments), cwd=ROOT,
                            env=child_environment(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
