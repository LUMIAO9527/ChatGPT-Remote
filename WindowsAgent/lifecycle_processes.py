"""Discover only processes running this exact installed executable."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def application_pids(executable):
    if os.name!='nt':return [os.getpid()]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                  ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),('name',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    for name in ('Process32FirstW','Process32NextW'):
        getattr(kernel,name).argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==ctypes.c_void_p(-1).value:raise OSError('无法读取程序进程，请稍后重试。')
    result=[];expected=os.path.normcase(str(Path(executable).resolve()))
    try:
        entry=Entry();entry.size=ctypes.sizeof(Entry)
        more=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while more:
            if entry.name.casefold()==Path(executable).name.casefold():
                process=kernel.OpenProcess(0x1000,False,entry.pid)
                if process:
                    try:
                        text=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(text))
                        if kernel.QueryFullProcessImageNameW(process,0,text,ctypes.byref(size)) and os.path.normcase(text.value)==expected:result.append(entry.pid)
                    finally:kernel.CloseHandle(process)
            more=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return result
