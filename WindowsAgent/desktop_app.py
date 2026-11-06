"""Native Rm tray application with a prewarmed, retained WebView2 panel."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading
import traceback

sys.dont_write_bytecode=True
from app_paths import HERE, ROOT, RUNTIME, DATA, FROZEN, launch_command, child_environment
sys.path.insert(0,str(RUNTIME/'libs'))


class Signals:
    """Only an owner creates the mutex; senders open existing named events."""
    def __init__(self,role,*,sender=False):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,args,result in [
            ('CreateMutexW',[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            ('CreateEventW',[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            ('OpenEventW',[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR],wintypes.HANDLE),
            ('SetEvent',[wintypes.HANDLE],wintypes.BOOL),('ResetEvent',[wintypes.HANDLE],wintypes.BOOL),
            ('WaitForSingleObject',[wintypes.HANDLE,wintypes.DWORD],wintypes.DWORD),
            ('WaitForMultipleObjects',[wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE),wintypes.BOOL,wintypes.DWORD],wintypes.DWORD),
            ('CloseHandle',[wintypes.HANDLE],wintypes.BOOL)]:
            function=getattr(self.kernel,name);function.argtypes=args;function.restype=result
        identity=hashlib.sha256(str(ROOT if FROZEN else HERE).casefold().encode()).hexdigest()[:24]
        prefix=f'Local\\ChatGPTRemote.{identity}.{role}'
        self.handles=[];self.mutex=None;self.existing=False
        if not sender:
            ctypes.set_last_error(0)
            self.mutex=self.kernel.CreateMutexW(None,False,prefix)
            error=ctypes.get_last_error()
            if not self.mutex:raise ctypes.WinError(error)
            self.handles.append(self.mutex);self.existing=error==183
        try:
            for name,manual in [('open',False),('close',False),('desired',True),('visible',True)]:
                label=prefix+'.'+name
                handle=(self.kernel.OpenEventW(0x00100002,False,label) if sender else
                        self.kernel.CreateEventW(None,manual,False,label))
                if not handle:raise ctypes.WinError(ctypes.get_last_error())
                setattr(self,name,handle);self.handles.append(handle)
        except Exception:
            self.dispose();raise

    @classmethod
    def connect(cls,role):
        try:return cls(role,sender=True)
        except OSError:return None

    def signal(self,*,close=False,hide=False):
        if close:self.kernel.SetEvent(self.close);return
        (self.kernel.ResetEvent if hide else self.kernel.SetEvent)(self.desired)
        self.kernel.SetEvent(self.open)

    def wants_visible(self):return self.kernel.WaitForSingleObject(self.desired,0)==0
    def set_desired(self,value):
        (self.kernel.SetEvent if value else self.kernel.ResetEvent)(self.desired)
    def is_visible(self):return self.kernel.WaitForSingleObject(self.visible,0)==0
    def set_visible(self,value):
        (self.kernel.SetEvent if value else self.kernel.ResetEvent)(self.visible)

    def wait(self,*,timeout=250):
        handles=(wintypes.HANDLE*2)(self.open,self.close)
        result=self.kernel.WaitForMultipleObjects(2,handles,False,timeout)
        return int(result) if result<2 else None

    def dispose(self):
        for handle in self.handles:self.kernel.CloseHandle(handle)
        self.handles=[]


def logger(name):
    log=logging.getLogger('remote.'+name);log.setLevel(logging.INFO)
    handler=RotatingFileHandler(RUNTIME/(name+'.log'),maxBytes=64000,backupCount=1,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));log.addHandler(handler)
    return log


def message(text):ctypes.windll.user32.MessageBoxW(None,text,'ChatGPT Remote',0x10)


def configure_dpi():
    try:
        user=ctypes.WinDLL('user32',use_last_error=True)
        function=user.SetProcessDpiAwarenessContext
        function.argtypes=[ctypes.c_void_p];function.restype=wintypes.BOOL
        if function(ctypes.c_void_p(-4)) or ctypes.get_last_error()==5:return
    except (AttributeError,OSError):pass
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError,OSError):pass


def window_main(args):
    signals=Signals('Window')
    if signals.existing:
        signals.signal();signals.dispose();return
    log=logger('desktop')
    try:
        import webview
        from desktop_manager import DesktopManager
        from desktop_platform import NativePanel,initial_theme,PANEL_SIZES
        from desktop_ui import render_html
        manager=DesktopManager();theme=initial_theme(manager.preferences())
        if args.update_ack:
            manager.restore_service=os.environ.get('CHATGPTREMOTE_RESTORE_SERVICE')=='1'
            manager.update_ack=(args.update_ack,args.update_token)
        html=render_html(theme)
        class Bridge:
            def call(self,action,payload=None):return manager.call(action,payload)
        webview.settings['ALLOW_DOWNLOADS']=False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
        width,height=PANEL_SIZES['home']
        window=webview.create_window('ChatGPT Remote',html=html,js_api=Bridge(),width=width,height=height,
                                    min_size=(312,300),frameless=True,easy_drag=False,resizable=False,
                                    hidden=True,shadow=True,text_select=True,on_top=False,
                                    background_color='#1d1d1d' if theme=='dark' else '#ffffff')
        manager.window=window
        identity=(args.tray_window,args.tray_id) if args.tray_window else None
        panel=NativePanel(window,manager,signals,args.anchor,log,tray_identity=identity);manager.panel=panel
        panel.show_requested=args.show
        closed=threading.Event();window.events.closed+=closed.set
        def events():
            while not closed.is_set():
                event=signals.wait()
                if event==0:panel.show() if signals.wants_visible() else panel.hide()
                elif event==1:panel.close()
        log.info('window_start pid=%s',os.getpid())
        if not args.setup:
            update_timer=threading.Timer(12,manager.updates.start);update_timer.daemon=True;update_timer.start()
        webview.start(events,gui='edgechromium',debug=False,private_mode=False,
                      storage_path=str(RUNTIME/'webview'),icon=str(HERE/'desktop.ico'))
        log.info('window_closed pid=%s',os.getpid())
    finally:
        signals.set_visible(False);signals.dispose()
        if 'manager' in locals() and manager.relaunch_executable:
            from desktop_installation import Installation
            Installation.open_installed(manager.relaunch_executable)


class PanelHost:
    """Warm once; deliver each visibility request once, including during startup."""
    def __init__(self,anchor,notify,identity=lambda:None,update_args=()):
        self.anchor,self.notify=anchor,notify
        self.identity=identity
        self.update_args=update_args
        self.child=None;self.pending=None;self.opened=False;self.lock=threading.RLock()

    def prepare(self):
        with self.lock:
            sender=Signals.connect('Window')
            if sender:sender.dispose();return
            if self.child is not None and self.child.poll() is None:return
            arguments=launch_command('window')
            arguments+=list(self.update_args)
            rect=self.anchor()
            if rect:arguments+=['--anchor',*[str(value) for value in rect]]
            identity=self.identity()
            if identity:arguments+=['--tray-window',str(identity[0]),'--tray-id',str(identity[1])]
            self.child=subprocess.Popen(arguments,cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)

    def request(self,visible=True):
        with self.lock:
            self.pending=visible
            if visible:self.opened=True
            sender=Signals.connect('Window')
            if sender:
                if visible and self.child is not None and self.child.poll() is None:
                    ctypes.windll.user32.AllowSetForegroundWindow(self.child.pid)
                sender.signal(hide=not visible);sender.dispose();self.pending=None
            elif visible:self.prepare()

    def toggle(self):
        with self.lock:
            sender=Signals.connect('Window')
            visible=sender.wants_visible() if sender else self.pending is True
            if sender:sender.dispose()
            self.request(not visible)

    def poll(self):
        with self.lock:
            if self.update_args and Path(self.update_args[1]).is_file():self.update_args=[]
            if self.child is not None and self.child.poll() is not None:
                failed=bool(self.child.returncode)
                self.child=None
                if self.pending is True or failed and self.opened:self.notify()
                self.pending=None
            if self.pending is not None:
                sender=Signals.connect('Window')
                if sender:
                    if self.pending and self.child is not None:
                        ctypes.windll.user32.AllowSetForegroundWindow(self.child.pid)
                    sender.signal(hide=not self.pending);sender.dispose();self.pending=None

    def close(self):
        with self.lock:
            self.pending=None
            sender=Signals.connect('Window')
            if sender:sender.signal(close=True);sender.dispose()
            elif self.child is not None and self.child.poll() is None:self.child.terminate()
            if self.child is not None:
                try:self.child.wait(timeout=3)
                except subprocess.TimeoutExpired:self.child.terminate()


def tray_main(args):
    signals=Signals('Tray')
    if signals.existing:
        if not args.background:signals.signal()
        signals.dispose();return
    from desktop_tray import TrayIcon
    log=logger('tray')
    stopped=threading.Event()
    icon=None
    def open_window():
        panel_host.request(True)
    def stop():
        stopped.set();panel_host.close()
        icon.stop()
    def menu():
        return [{'label':'打开连接面板','action':open_window},
                {'label':'退出管理程序','action':stop}]
    panel_host=PanelHost(lambda:icon.rect() if icon else None,
                         lambda:icon.notify('连接面板启动失败，点击重新打开。','ChatGPT Remote',action=open_window),
                         identity=lambda:icon.identity() if icon else None,
                         update_args=['--update-ack',args.update_ack,'--update-token',args.update_token] if args.update_ack else [])
    icon=TrayIcon({'light':str(HERE/'desktop-dark.ico'), 'dark':str(HERE/'desktop-light.ico')},
                  'ChatGPT Remote · Rm',panel_host.toggle,menu,log=log)
    icon.run_detached();log.info('tray_ready pid=%s',os.getpid())
    panel_host.prepare()
    if args.show or not args.background or args.update_ack:open_window()
    try:
        while not stopped.is_set():
            event=signals.wait()
            if event==0:open_window()
            elif event==1:stop();break
            panel_host.poll()
    finally:panel_host.close();icon.stop();signals.dispose()


def main():
    from desktop_runtime import ensure_desktop_runtime
    ensure_desktop_runtime()
    parser=argparse.ArgumentParser();parser.add_argument('--window',action='store_true')
    parser.add_argument('--background',action='store_true');parser.add_argument('--show',action='store_true')
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--update-ack');parser.add_argument('--update-token')
    parser.add_argument('--anchor',nargs=4,type=int)
    parser.add_argument('--tray-window',type=int);parser.add_argument('--tray-id',type=int,default=1)
    args=parser.parse_args()
    (RUNTIME/'temp').mkdir(parents=True,exist_ok=True)
    os.environ['TEMP']=os.environ['TMP']=str(RUNTIME/'temp')
    os.environ['WEBVIEW2_USER_DATA_FOLDER']=str(RUNTIME/'webview');os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['PSModuleAnalysisCachePath']=str(RUNTIME/'powershell-analysis.cache')
    configure_dpi()
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('lumiao.chatgpt.remote')
    try:window_main(args) if args.window else tray_main(args)
    except Exception as exc:
        log=logger('desktop')
        locations=';'.join(f'{Path(frame.filename).name}:{frame.lineno}' for frame in traceback.extract_tb(exc.__traceback__))
        log.error('desktop_start_error=%s code=%s locations=%s',type(exc).__name__,getattr(exc,'winerror',None),locations)
        if not args.window:message('启动失败，请运行 WindowsAgent 中的 setup_desktop.ps1 检查组件。')
        raise SystemExit(1) from None


if __name__=='__main__':main()
