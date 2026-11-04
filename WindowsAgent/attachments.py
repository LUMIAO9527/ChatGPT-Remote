"""Short-lived, paired-device uploads and a single native clipboard paste.

File bytes never enter JSON, logs, conversation polling or the control socket.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import queue
import re
import secrets
import subprocess
import threading
import time

MAX_FILE = 25 * 1024 * 1024
MAX_DEVICE = 50 * 1024 * 1024


class UploadError(Exception):
    pass


class UploadStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        self.stop_event = threading.Event()
        self.cleaner = None

    def start_cleanup(self) -> None:
        def clean():
            while not self.stop_event.wait(600):
                try:
                    with self.lock:self.cleanup()
                except OSError:pass
        self.cleaner=threading.Thread(target=clean,daemon=True)
        self.cleaner.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.cleaner:self.cleaner.join(timeout=2)

    @staticmethod
    def owner(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def filename(value: str) -> str:
        if not value or len(value) > 180 or any(c in value for c in '<>:"/\\|?*\0') or any(ord(c) < 32 for c in value):
            raise UploadError('文件名不可用，请重命名后再选择')
        if value != value.strip(' .') or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
            raise UploadError('文件名不可用，请重命名后再选择')
        return value

    def _remove(self, item: dict) -> None:
        path = item['path']
        if path.resolve().parent.parent == self.root and not path.is_symlink():
            path.unlink(missing_ok=True)
            try: path.parent.rmdir()
            except OSError: pass

    def cleanup(self) -> None:
        now=time.time()
        for ident,item in list(self.items.items()):
            if now-item['created']>86400:
                self._remove(item);self.items.pop(ident,None)
        # Recover only the generated UUID directory layout after a restart.
        if self.root.exists():
            for folder in self.root.iterdir():
                if folder.is_symlink() or folder.resolve().parent!=self.root or not folder.is_dir() or not re.fullmatch('[0-9a-f]{32}',folder.name):continue
                for path in folder.iterdir():
                    if path.is_file() and not path.is_symlink() and now-path.stat().st_mtime>86400:
                        path.unlink()
                try:folder.rmdir()
                except OSError:pass

    def receive(self, token: str, name: str, size: int, stream) -> dict:
        name=self.filename(name)
        if type(size) is not int or not 0<size<=MAX_FILE:raise UploadError('每个文件需大于 0 字节，且不超过 25 MB')
        owner=self.owner(token)
        with self.lock:
            self.cleanup()
            own=[item for item in self.items.values() if item['owner']==owner and item['state']!='submitted']
            if len(own)>=5 or sum(item['size'] for item in own)+size>MAX_DEVICE:raise UploadError('最多同时添加 5 个文件，总大小不超过 50 MB')
            if sum(item['size'] for item in self.items.values())+size>256*1024*1024:raise UploadError('电脑临时空间已满，请稍后再试')
            ident=secrets.token_hex(16);folder=self.root/ident;folder.mkdir(parents=True,exist_ok=False)
            item={'id':ident,'owner':owner,'name':name,'size':size,'path':folder/name,'created':time.time(),'state':'uploaded'}
            try:
                with item['path'].open('xb') as output:
                    remaining=size
                    while remaining:
                        chunk=stream.read(min(256*1024,remaining))
                        if not chunk:raise UploadError('上传中断，请重新选择文件')
                        output.write(chunk);remaining-=len(chunk)
            except Exception:
                self._remove(item);raise
            self.items[ident]=item
            return self.public(item)

    @staticmethod
    def public(item: dict) -> dict:
        return {key:item[key] for key in ('id','name','size','state')}

    def resolve(self, token: str, ids: list, state: str | None = None) -> list[dict]:
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or len(set(str(i) for i in ids))!=len(ids):raise UploadError('附件列表无效')
        result=[]
        for ident in ids:
            item=self.items.get(ident) if isinstance(ident,str) else None
            if not item or item['owner']!=self.owner(token) or (state and item['state']!=state):raise UploadError('附件已失效，请重新选择')
            result.append(item)
        return result

    def discard(self, token: str, ids: list) -> None:
        with self.lock:
            for item in self.resolve(token,ids):
                if item['state'] not in ('uploaded','failed'):raise UploadError('附件可能已在电脑输入框中，请在画面中移除')
                self._remove(item);self.items.pop(item['id'],None)


_CLIPBOARD = r'''
$ErrorActionPreference='Stop';[Console]::InputEncoding=New-Object Text.UTF8Encoding($false);[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type 'using System;using System.Runtime.InteropServices;public static class ClipSeq {[DllImport("user32.dll")]public static extern uint GetClipboardSequenceNumber();}'
$saved=New-Object System.Windows.Forms.DataObject;$old=[Windows.Forms.Clipboard]::GetDataObject()
if($old){foreach($format in $old.GetFormats($false)){$saved.SetData($format,$false,$old.GetData($format,$false))}}
$request=[Console]::ReadLine()|ConvertFrom-Json
$files=New-Object Collections.Specialized.StringCollection;foreach($path in $request.paths){[void]$files.Add([string]$path)}
try {
 $drop=New-Object Windows.Forms.DataObject;$drop.SetFileDropList($files)
 [Windows.Forms.Clipboard]::SetDataObject($drop,$true);$sequence=[ClipSeq]::GetClipboardSequenceNumber()
 [Console]::WriteLine('{"ready":true}');[Console]::Out.Flush()
 $wait=[Console]::In.ReadLineAsync();$timer=[Diagnostics.Stopwatch]::StartNew()
 while(!$wait.IsCompleted -and $timer.ElapsedMilliseconds -lt 12000){[Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 20}
} finally {
 $restored=$false
 if([ClipSeq]::GetClipboardSequenceNumber() -eq $sequence){
  if($old){[Windows.Forms.Clipboard]::SetDataObject($saved,$true)}else{[Windows.Forms.Clipboard]::Clear()}
  $restored=$true
 }
 [Console]::WriteLine(('{"restored":'+$restored.ToString().ToLower()+'}'));[Console]::Out.Flush()
}
'''


def paste_files(paths: list[Path], paste) -> dict:
    """Keep the previous clipboard in memory, paste once, restore if still ours."""
    script=base64.b64encode(_CLIPBOARD.encode('utf-16-le')).decode()
    process=subprocess.Popen(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Sta','-EncodedCommand',script],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
    replies=queue.Queue()
    def read():
        try:
            for line in process.stdout:replies.put(json.loads(line))
        except (OSError,ValueError):pass
        finally:replies.put(None)
    threading.Thread(target=read,daemon=True).start()
    invoked=False
    try:
        process.stdin.write(json.dumps({'paths':[str(p) for p in paths]})+'\n');process.stdin.flush()
        ready=replies.get(timeout=5)
        if not ready or not ready.get('ready'):raise UploadError('暂时无法附加文件，请稍后再试')
        invoked=True;result=paste()
        return result
    except queue.Empty:
        raise UploadError('附加结果未确认，请检查电脑输入框；没有自动重试' if invoked else '电脑剪贴板暂不可用，请稍后再试') from None
    finally:
        try:
            if process.poll() is None:
                process.stdin.write('done\n');process.stdin.flush();process.wait(timeout=3)
        except (OSError,subprocess.TimeoutExpired):
            process.terminate();process.wait(timeout=2)
        for pipe in (process.stdin,process.stdout):
            if pipe:pipe.close()
