"""One Windows program for the tray, panel, background service and first use."""
from pathlib import Path
import json
import os
import sys
import tempfile
import logging
from logging.handlers import RotatingFileHandler
import traceback

sys.dont_write_bytecode=True


def startup_logger():
    from app_paths import RUNTIME
    RUNTIME.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger('remote.startup')
    if not log.handlers:
        handler = RotatingFileHandler(RUNTIME/'startup.log', maxBytes=64000,
                                      backupCount=1, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log

def set_launcher_identity(arguments):
    # The tray's outer bootloader owns the update. Its panel is a separate
    # process and must keep that identity when it acknowledges readiness.
    if '--update-ack' in arguments and '--window' not in arguments:
        os.environ['CHATGPTREMOTE_LAUNCHER_PID']=str(os.getppid())


def main():
    import app_paths
    from app_version import APP_VERSION
    from desktop_installation import Installation, atomic_json, MARKER
    arguments=sys.argv[1:]
    if '--self-test' in arguments:
        required=['desktop.html','desktop.ico','desktop-light.ico','desktop-dark.ico','uia_reader.ps1',
                  'manage_frozen.ps1','ChatGPTRemote.Update.exe','assets/rm-mark.svg','web/index.html']
        missing=[name for name in required if not (app_paths.HERE/name).is_file()]
        result={'app':'ChatGPT Remote','version':APP_VERSION,'missingResources':missing}
        try:
            from desktop_runtime import ensure_desktop_runtime
            ensure_desktop_runtime()
            import qrcode
            import PIL
            import clr
            from webview.platforms import winforms
            import desktop_manager
            result['desktopRuntimeReady']=True
        except Exception as error:
            result['desktopRuntimeReady']=False
            result['runtimeError']=type(error).__name__
            import traceback
            result['runtimeTrace']=traceback.format_exc()
        if '--report' in arguments:
            target=Path(arguments[arguments.index('--report')+1])
            with target.open('x',encoding='utf-8') as stream:json.dump(result,stream)
        if sys.stdout:print(json.dumps(result))
        return 1 if missing or not result['desktopRuntimeReady'] else 0
    installation=Installation()
    redirected=installation.redirected_executable()
    if redirected and not any(flag in arguments for flag in ('--agent','--window')):
        installation.open_installed(redirected,arguments);return 0
    if '--agent' in arguments:
        arguments.remove('--agent');sys.argv=[sys.argv[0],*arguments]
        from background_agent import run
        run();return 0
    if '--tray' in arguments:arguments.remove('--tray')
    if app_paths.FROZEN and installation.status()['firstRun']:
        if '--uninstall' in arguments:raise ValueError('请先完成程序安装。')
        with tempfile.TemporaryDirectory(prefix='rm-setup-',ignore_cleanup_errors=True) as stage:
            app_paths.RUNTIME=Path(stage)
            os.environ['CHATGPTREMOTE_SETUP_RUNTIME']=stage
            sys.argv=[sys.argv[0],'--window','--show','--setup']
            from desktop_app import main as desktop_main
            desktop_main()
        return 0
    if app_paths.FROZEN:
        set_launcher_identity(arguments)
        log = startup_logger()
        log.info('start version=%s role=%s pid=%s', APP_VERSION,
                 'window' if '--window' in arguments else 'tray', os.getpid())
        if not app_paths.DATA.exists():
            from lifecycle_storage import secure_path
            app_paths.DATA.mkdir()
            secure_path(app_paths.DATA)
        from desktop_installation import refresh_application, installation_record
        refresh_application(app_paths.EXECUTABLE, log=log)
        record=installation_record(app_paths.ROOT)
        if record.get('version') != APP_VERSION:
            record['version']=APP_VERSION
            atomic_json(app_paths.ROOT/MARKER,record)
    if '--uninstall' in arguments:
        atomic_json(app_paths.DATA/'uninstall-request.json',{'schema':1})
        arguments.remove('--uninstall')
        if '--show' not in arguments:arguments.append('--show')
    sys.argv=[sys.argv[0],*arguments]
    from desktop_app import main as desktop_main
    desktop_main()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        try:
            locations=';'.join(f'{Path(frame.filename).name}:{frame.lineno}' for frame in traceback.extract_tb(error.__traceback__))
            startup_logger().error('startup_failed type=%s code=%s locations=%s',
                                   type(error).__name__, getattr(error,'winerror',None), locations)
        except OSError:
            pass
        if os.name=='nt' and '--self-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动未完成，请查看程序文件夹的运行日志或重新下载完整程序。','ChatGPT Remote',0x10)
        raise
