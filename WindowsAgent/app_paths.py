"""Keep bundled resources separate from the user's persistent application files."""
from pathlib import Path
import os
import subprocess
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
HERE = Path(__file__).resolve().parent
_launcher = os.environ.get('CHATGPTREMOTE_LAUNCHER_EXE') if FROZEN else None
EXECUTABLE = Path(_launcher or sys.executable).resolve() if FROZEN else Path(sys.executable)
ROOT = EXECUTABLE.parent if FROZEN else HERE.parent
DATA = ROOT / '_data' if FROZEN else HERE / 'data'
RUNTIME = ROOT / '_wip' / 'desktop-runtime'


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


def external_environment():
    """Windows tools use the host's module paths rather than our Python bundle."""
    environment = child_environment()
    for name in list(environment):
        if name.startswith('_PYI_') or name in ('_MEIPASS2', 'PYTHONHOME', 'PYTHONPATH'):
            environment.pop(name, None)
    # PowerShell 7 passes its own PSModulePath to children. Windows PowerShell
    # needs to construct its normal inbox-module path for ScheduledTasks/CIM.
    environment.pop('PSModulePath', None)
    return environment


def launch(role='tray', *arguments):
    return subprocess.Popen(launch_command(role, *arguments), cwd=ROOT,
                            env=child_environment(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
