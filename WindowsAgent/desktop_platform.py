"""Native panel primitives adapted from ChatGPTnx v1.6.0 desktop lifecycle.

The notification icon and placement calculation derive from the owner's source;
Remote keeps its backend independent and retains a quiet, hidden panel.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as wt

PANEL_SIZE = (312, 458)
PANEL_SIZES = dict.fromkeys(('home', 'settings', 'support', 'support-qr', 'update', 'install', 'uninstall', 'first-use'), PANEL_SIZE)
BLUR_CHECK_MS = 60


def popup_position(icon, work, monitor, size, gap=8, margin=8):
    """Physical-pixel tray docking, including vertical and secondary taskbars."""
    il,it,ir,ib=icon;wl,wt_,wr,wb=work;ml,mt,mr,mb=monitor;width,height=size
    if wb<mb:edge='bottom'
    elif wt_>mt:edge='top'
    elif wl>ml:edge='left'
    elif wr<mr:edge='right'
    else:
        cx,cy=(il+ir)/2,(it+ib)/2
        edge=min({'left':abs(cx-ml),'right':abs(mr-cx),'top':abs(cy-mt),'bottom':abs(mb-cy)},
                 key=lambda key: {'left':abs(cx-ml),'right':abs(mr-cx),'top':abs(cy-mt),'bottom':abs(mb-cy)}[key])
    if edge=='bottom':x,y=round((il+ir-width)/2),it-gap-height
    elif edge=='top':x,y=round((il+ir-width)/2),ib+gap
    elif edge=='left':x,y=ir+gap,round((it+ib-height)/2)
    else:x,y=il-gap-width,round((it+ib-height)/2)
    return max(wl+margin,min(x,wr-width-margin)),max(wt_+margin,min(y,wb-height-margin))


class MonitorInfo(ctypes.Structure):
    _fields_=[('cbSize',wt.DWORD),('rcMonitor',wt.RECT),('rcWork',wt.RECT),('flags',wt.DWORD)]


class NativePanel:
    def __init__(self, window, manager, signals, anchor=None, log=None, *, tray_identity=None):
        self.window,self.manager,self.signals,self.anchor,self.log=window,manager,signals,anchor,log
        self.tray_identity=tray_identity
        self.page='home';self.visible=False;self.busy=False;self.modal=False
        self.generation=0;self.bound=False;self.ready=False;self.blur_timer=None
        self.show_requested=False;self.closed=False
        self.material=None;self.appearance=None;self.material_choice=None
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.MonitorFromRect.argtypes=[ctypes.POINTER(wt.RECT),wt.DWORD]
        self.user.MonitorFromRect.restype=wt.HMONITOR
        self.user.GetMonitorInfoW.argtypes=[wt.HMONITOR,ctypes.POINTER(MonitorInfo)]
        self.user.GetWindowRect.argtypes=[wt.HWND,ctypes.POINTER(wt.RECT)]
        self.user.SetWindowPos.argtypes=[wt.HWND,wt.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.UINT]
        self.user.GetForegroundWindow.restype=wt.HWND
        self.user.GetAncestor.argtypes=[wt.HWND,wt.UINT];self.user.GetAncestor.restype=wt.HWND
        self.user.GetAsyncKeyState.argtypes=[ctypes.c_int];self.user.GetAsyncKeyState.restype=ctypes.c_short
        self.user.GetDpiForWindow.argtypes=[wt.HWND];self.user.GetDpiForWindow.restype=wt.UINT
        self.user.SetForegroundWindow.argtypes=[wt.HWND]
        self.user.GetWindowLongW.argtypes=[wt.HWND,ctypes.c_int]
        self.user.GetWindowLongW.restype=wt.LONG

    def form(self):
        from webview.platforms.winforms import BrowserView
        return BrowserView.instances.get(self.window.uid)

    def dispatch(self, action):
        """BeginInvoke avoids a cross-thread Invoke/GIL circular wait."""
        from System import Action
        form=self.form()
        if not form or form.IsDisposed:return False
        def apply():
            if not self.closed:
                try:action(form)
                except Exception as exc:
                    if self.log:self.log.warning('panel_dispatch_failed type=%s',type(exc).__name__)
        form.BeginInvoke(Action(apply));return True

    def place(self, form):
        hwnd=int(form.Handle.ToInt64());point=wt.POINT()
        if self.tray_identity:
            from desktop_tray import tray_rect
            icon=tray_rect(*self.tray_identity)
        else:icon=self.anchor
        if not icon:
            self.user.GetCursorPos(ctypes.byref(point));icon=(point.x-8,point.y-8,point.x+8,point.y+8)
        self.anchor=icon
        box=wt.RECT(*icon);monitor=self.user.MonitorFromRect(ctypes.byref(box),2)
        info=MonitorInfo();info.cbSize=ctypes.sizeof(info)
        if not monitor or not self.user.GetMonitorInfoW(monitor,ctypes.byref(info)):return
        dpi=self.user.GetDpiForWindow(hwnd) or 96
        try:
            shcore=ctypes.WinDLL('shcore')
            shcore.GetDpiForMonitor.argtypes=[wt.HMONITOR,ctypes.c_int,ctypes.POINTER(wt.UINT),ctypes.POINTER(wt.UINT)]
            shcore.GetDpiForMonitor.restype=ctypes.c_long
            dx,dy=wt.UINT(),wt.UINT()
            if shcore.GetDpiForMonitor(monitor,0,ctypes.byref(dx),ctypes.byref(dy))==0 and dx.value:dpi=dx.value
        except (AttributeError,OSError):pass
        width,height=(round(n*dpi/96) for n in PANEL_SIZE)
        work=info.rcWork;screen=info.rcMonitor
        width=min(width,work.right-work.left-16);height=min(height,work.bottom-work.top-16)
        x,y=popup_position(icon,(work.left,work.top,work.right,work.bottom),
                            (screen.left,screen.top,screen.right,screen.bottom),(width,height))
        if not self.user.SetWindowPos(hwnd,None,x,y,width,height,0x0004|0x0010):
            raise ctypes.WinError(ctypes.get_last_error())
        self.round_window(hwnd,width,height,dpi)

    def appearance_changed(self,appearance=None,material=None):
        if appearance in ('light','dark'):self.appearance=appearance
        if material in ('glass','solid'):self.material_choice=material
        if self.ready:self.dispatch(self.apply_material)

    def apply_material(self,form):
        from desktop_material import WindowMaterial
        from pathlib import Path
        hwnd=int(form.Handle.ToInt64())
        pref=self.manager.preferences()
        dark=(self.appearance or initial_theme(pref))=='dark'
        if self.material is None:
            self.material=WindowMaterial(self.log,Path(__file__).resolve().parent/'assets')
            self.material.bind_policy_changes(form,lambda:self.dispatch(self.apply_material))
        mode=self.material.apply(form,hwnd,dark=dark,requested=(self.material_choice or pref.get('material','glass'))=='glass')
        self.post(form,'window.remoteBackdrop && window.remoteBackdrop("'+mode+'")')
        dpi=self.user.GetDpiForWindow(hwnd) or 96
        box=wt.RECT()
        if not self.user.GetWindowRect(hwnd,ctypes.byref(box)):raise ctypes.WinError(ctypes.get_last_error())
        self.round_window(hwnd,box.right-box.left,box.bottom-box.top,dpi)
        if self.log:self.log.info('panel_material mode=%s radius=%s',mode,round(16*dpi/96))

    def topmost(self,form,value):
        form.TopMost=value
        hwnd=int(form.Handle.ToInt64())
        if not self.user.SetWindowPos(hwnd,wt.HWND(-1 if value else -2),0,0,0,0,0x0001|0x0002|0x0010):
            raise ctypes.WinError(ctypes.get_last_error())

    def record(self,form,event,started=None):
        if not self.log:return
        import time
        hwnd=int(form.Handle.ToInt64());box=wt.RECT()
        self.user.GetWindowRect(hwnd,ctypes.byref(box))
        elapsed=round((time.perf_counter()-started)*1000,1) if started else 0
        self.log.info('%s native_visible=%s topmost=%s bounds=%sx%s dispatch_ms=%s',event,
                      bool(form.Visible),bool(self.user.GetWindowLongW(hwnd,-20)&8),
                      box.right-box.left,box.bottom-box.top,elapsed)

    def round_window(self,hwnd,width,height,dpi):
        try:
            dwm=ctypes.WinDLL('dwmapi')
            dwm.DwmSetWindowAttribute.argtypes=[wt.HWND,wt.DWORD,ctypes.c_void_p,wt.DWORD]
            choice=ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd,33,ctypes.byref(choice),ctypes.sizeof(choice))
        except (AttributeError,OSError):pass
        gdi=ctypes.WinDLL('gdi32')
        gdi.CreateRoundRectRgn.argtypes=[ctypes.c_int]*6;gdi.CreateRoundRectRgn.restype=wt.HRGN
        gdi.DeleteObject.argtypes=[wt.HGDIOBJ]
        self.user.SetWindowRgn.argtypes=[wt.HWND,wt.HRGN,wt.BOOL]
        radius=round(16*dpi/96)
        region=gdi.CreateRoundRectRgn(0,0,width+1,height+1,2*radius,2*radius)
        if not region:raise ctypes.WinError(ctypes.get_last_error())
        if not self.user.SetWindowRgn(hwnd,region,True):
            gdi.DeleteObject(region)
            raise ctypes.WinError(ctypes.get_last_error())

    def bind(self):
        def apply(form):
            if not self.bound:
                from System.Windows.Forms import Timer
                form.ShowInTaskbar=False
                self.topmost(form,self.visible)
                self.blur_timer=Timer()
                self.blur_timer.Interval=BLUR_CHECK_MS
                self.blur_timer.Tick+=lambda *_:self.check_blur(form)
                form.Deactivate+=self.on_blur
                form.Activated+=self.on_activate
                self.bound=True
            self.place(form)
            self.apply_material(form)
        self.dispatch(apply)

    def on_activate(self,*_):
        if self.blur_timer:self.blur_timer.Stop()

    def on_blur(self,*_):
        if self.visible and self.blur_timer:self.blur_timer.Start()

    def check_blur(self, form):
        if not self.visible or not self.signals.wants_visible():
            self.blur_timer.Stop()
            return
        if self.busy or self.modal:
            return
        hwnd=int(form.Handle.ToInt64())
        foreground=self.user.GetForegroundWindow()
        if foreground==hwnd:
            self.blur_timer.Stop()
            return
        if foreground and self.user.GetAncestor(foreground,3)==hwnd:
            return
        # Let the tray's button-up event finish toggling a panel that was open.
        if self.anchor and any(self.user.GetAsyncKeyState(button)&0x8000 for button in (1,2)):
            point=wt.POINT()
            if self.user.GetCursorPos(ctypes.byref(point)):
                l,t,r,b=self.anchor
                if l<=point.x<r and t<=point.y<b:return
        self.blur_timer.Stop()
        if self.manager.preferences()['autoHide']:self.hide()

    def show(self):
        import time
        started=time.perf_counter()
        self.show_requested=True
        self.signals.set_desired(True)
        if not self.ready:return
        self.generation+=1;self.visible=True
        self.signals.set_visible(True)
        token=self.generation
        def apply(form):
            if token!=self.generation:return
            if self.blur_timer:self.blur_timer.Stop()
            self.place(form);self.apply_material(form);self.topmost(form,True);form.Show();form.Activate()
            self.user.SetForegroundWindow(int(form.Handle.ToInt64()))
            self.post(form,'window.remoteShown && window.remoteShown()')
            self.record(form,'panel_shown',started)
        self.dispatch(apply)

    def hide(self):
        self.generation+=1;self.show_requested=False;self.visible=False
        self.signals.set_desired(False)
        self.signals.set_visible(False)
        token=self.generation
        def apply(form):
            if token!=self.generation:return
            if self.blur_timer:self.blur_timer.Stop()
            form.Hide();self.topmost(form,False)
            self.post(form,'window.remoteHidden && window.remoteHidden()')
            self.record(form,'panel_hidden')
        self.dispatch(apply)

    def present(self):
        self.ready=True;self.bind()
        if self.log:self.log.info('panel_ready')
        if self.show_requested:self.show()

    def resize(self,page):
        if page not in PANEL_SIZES:return
        self.page=page

    @staticmethod
    def post(form,script):
        if getattr(form,'browser',None) and getattr(form.browser,'webview',None):
            form.browser.webview.ExecuteScriptAsync(script)

    def close(self):
        self.generation+=1;self.show_requested=False
        self.signals.set_desired(False)
        self.visible=False;self.signals.set_visible(False)
        def apply(form):
            if self.blur_timer:
                self.blur_timer.Dispose()
                self.blur_timer=None
            self.topmost(form,False)
            self.closed=True;form.Close()
        self.dispatch(apply)


def initial_theme(preferences):
    choice=preferences.get('theme','system')
    if choice!='system':return choice
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return 'light' if winreg.QueryValueEx(key,'AppsUseLightTheme')[0] else 'dark'
    except OSError:return 'light'
