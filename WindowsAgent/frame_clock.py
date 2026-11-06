"""Interruptible frame pacing without changing the system timer resolution."""
import ctypes
from ctypes import wintypes
import math
import os
import threading


class FrameClock:
    def __init__(self):
        self.fallback = threading.Event()
        self.kernel = None
        self.timer = self.event = None
        self.closed = False
        if os.name != 'nt': return
        try:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            kernel.CreateWaitableTimerExW.restype = wintypes.HANDLE
            kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateEventW.restype = wintypes.HANDLE
            kernel.SetWaitableTimer.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong), wintypes.LONG,
                                                ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL]
            kernel.SetWaitableTimer.restype = wintypes.BOOL
            kernel.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
            kernel.WaitForMultipleObjects.restype = wintypes.DWORD
            for name in ('SetEvent', 'ResetEvent', 'CloseHandle'):
                getattr(kernel, name).argtypes = [wintypes.HANDLE]
                getattr(kernel, name).restype = wintypes.BOOL
            self.kernel = kernel
            # CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, Windows 10 1803+.
            # https://learn.microsoft.com/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw
            self.timer = kernel.CreateWaitableTimerExW(None, None, 2, 0x00100002)
            self.event = kernel.CreateEventW(None, True, False, None)
            if not self.timer or not self.event: self._release()
        except (OSError, AttributeError): self._release()

    @property
    def precise(self): return bool(self.timer and self.event)

    def set(self):
        self.fallback.set()
        if self.event:self.kernel.SetEvent(self.event)

    def clear(self):
        self.fallback.clear()
        if self.event:self.kernel.ResetEvent(self.event)

    def wait(self, seconds):
        seconds = max(0, float(seconds))
        if not self.precise: return self.fallback.wait(seconds)
        due = ctypes.c_longlong(-max(1, round(seconds * 10_000_000)))
        if not self.kernel.SetWaitableTimer(self.timer, ctypes.byref(due), 0, None, None, False):
            return self.fallback.wait(seconds)
        handles = (wintypes.HANDLE * 2)(self.event, self.timer)
        result = self.kernel.WaitForMultipleObjects(2, handles, False, math.ceil(seconds*1000)+1000)
        if result == 0xFFFFFFFF:return self.fallback.wait(seconds)
        return result == 0

    def _release(self):
        if self.kernel:
            for handle in (self.timer,self.event):
                if handle:self.kernel.CloseHandle(handle)
        self.timer = self.event = None

    def close(self):
        self._release()
        self.closed = True
