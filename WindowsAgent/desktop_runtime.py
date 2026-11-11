"""Configure this process before CLR starts, after checking its bundled files.

Windows can classify a local installation as an Internet location without an
individual Zone.Identifier stream. The CLR host API supplies the app's own
configuration; Windows zone rules and other applications stay independent.
https://learn.microsoft.com/dotnet/framework/unmanaged-api/hosting/iclrruntimeinfo-setdefaultstartupflags-method
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import re
import uuid
from app_paths import HERE,FROZEN
from desktop_installation import plain_path

_configured=False

class GUID(ctypes.Structure):
    _fields_=[('data',ctypes.c_ubyte*16)]
    def __init__(self,value):super().__init__((ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le))

def _call(pointer,index,signature,*arguments):
    table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*signature)(table[index])
    result=function(pointer,*arguments)
    if result<0:raise OSError('CLR host error '+hex(result&0xffffffff))

def verify_bundle(folder, manifest_name='netfx-bundle.json', config='netfx.config'):
    folder=plain_path(folder)
    manifest=json.loads(plain_path(folder/manifest_name).read_text(encoding='utf8'))
    if manifest.get('schema')!=1 or not isinstance(manifest.get('files'),dict) or not manifest['files']:
        raise OSError('桌面组件清单缺失，请重新下载程序。')
    for name,expected in manifest['files'].items():
        if not isinstance(name,str) or not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise OSError('桌面组件清单无效。')
        target=plain_path(folder/name)
        if folder not in target.parents or not target.is_file():raise OSError('桌面组件位置无效。')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise OSError('桌面组件校验失败，请重新下载程序。')
    if config not in manifest['files']:raise OSError('桌面组件配置缺失。')

def ensure_desktop_runtime():
    if FROZEN:
        ensure_netfx_runtime(HERE)

def ensure_netfx_runtime(folder, manifest_name='netfx-bundle.json', config='netfx.config'):
    global _configured
    if _configured:return
    verify_bundle(folder,manifest_name,config)
    library=ctypes.WinDLL('mscoree')
    library.CLRCreateInstance.argtypes=[ctypes.POINTER(GUID),ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
    library.CLRCreateInstance.restype=ctypes.c_long
    host=ctypes.c_void_p();runtime=ctypes.c_void_p()
    result=library.CLRCreateInstance(ctypes.byref(GUID('9280188d-0e8e-4867-b30c-7fa83884e8de')),ctypes.byref(GUID('d332db9e-b9b3-4125-8207-a14884f53216')),ctypes.byref(host))
    if result<0:raise OSError('无法初始化 Windows 桌面组件。')
    try:
        _call(host,3,[wintypes.LPCWSTR,ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)],'v4.0.30319',ctypes.byref(GUID('bd39d1d2-ba2f-486a-89b0-b4b0cb466891')),ctypes.byref(runtime))
        _call(runtime,11,[wintypes.DWORD,wintypes.LPCWSTR],0,str(folder/config))
        _configured=True
    finally:
        if runtime:_call(runtime,2,[])
        if host:_call(host,2,[])
