"""Run the Agent with a small, content-free lifecycle log."""
import contextlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import traceback

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '_wip' / 'desktop-runtime' / 'libs'))


def run():
    from agent import VERSION, main
    from app_paths import DATA
    folder = DATA
    folder.mkdir(exist_ok=True)
    log = logging.getLogger('harmony.lifecycle')
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(folder / 'runtime.log', maxBytes=128000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    log.addHandler(handler)
    log.info('start version=%s pid=%s', VERSION, os.getpid())
    def recovery():
        try:
            from desktop_manager import task_command
            task_command('Recovery')
        except Exception:
            log.warning('task_recovery_update_failed')
    threading.Thread(target=recovery, name='task-recovery', daemon=True).start()
    try:
        with open(os.devnull, 'w', encoding='utf-8') as quiet:
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                main()
    except BaseException as exc:
        locations = '; '.join(f'{Path(frame.filename).name}:{frame.lineno}' for frame in traceback.extract_tb(exc.__traceback__))
        log.error('exit error=%s locations=%s', type(exc).__name__, locations)
        raise SystemExit(1) from None
    else:
        log.info('stop cleanly')


if __name__ == '__main__':
    run()
