"""Short-lived, paired-device uploads and a single native clipboard paste.

File bytes never enter JSON, logs, conversation polling or the control socket.
"""
from __future__ import annotations
import base64
import atexit
import os
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


class UploadMissing(UploadError):
    code = 'upload_missing'


class ClipboardUnavailable(UploadError):
    """The input callback has not run; a text input route can still be chosen."""


class UploadStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        self.removed: dict[str, dict] = {}
        self.stop_event = threading.Event()
        self.cleaner = None
        manifest = self.root/'manifest.json'
        if manifest.is_file():
            try:
                saved=json.loads(manifest.read_text(encoding='utf-8'))
                for row in saved.get('removed',[])[:256]:
                    if isinstance(row,dict) and re.fullmatch('[a-f0-9]{32}',row.get('id','')) and re.fullmatch('[a-f0-9]{64}',row.get('owner','')) and time.time()-row.get('at',0)<86400:
                        self.removed[row['id']]=row
                for row in saved.get('files', [])[:128]:
                    if (not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{32}', row.get('id','')) or
                            not re.fullmatch('[0-9a-f]{64}', row.get('owner','')) or
                            row.get('state') not in ('uploaded','attaching','attached','unknown','removing','submitted','failed')):
                        continue
                    name=self.filename(row.get('name',''))
                    path=self.root/row['id']/name
                    if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or path.stat().st_size!=row.get('size'):
                        continue
                    if time.time()-row.get('created',0)>86400:
                        continue
                    self.items[row['id']]={**row,'path':path}
            except (OSError, ValueError, TypeError, UploadError):
                pass

    def save(self) -> bool:
        if not self.root.exists():return True
        rows=[{key:value for key,value in item.items() if key in ('id','owner','name','size','created','state',
            'conversationKey','epoch','nativeBefore','batchIds','nativeAttachment','removalProof')} for item in self.items.values()]
        temporary=self.root/'manifest.tmp'
        try:
            temporary.write_text(json.dumps({'version':1,'files':rows,'removed':list(self.removed.values())[-256:]},ensure_ascii=False),encoding='utf-8')
            temporary.replace(self.root/'manifest.json')
            return True
        except OSError:
            return False

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
        self.save()

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
            self.save()
            return self.public(item)

    @staticmethod
    def public(item: dict) -> dict:
        return {key:item[key] for key in ('id','name','size','state')}

    def resolve(self, token: str, ids: list, state: str | None = None) -> list[dict]:
        if not isinstance(ids,list) or not 1<=len(ids)<=5 or len(set(str(i) for i in ids))!=len(ids):raise UploadError('附件列表无效')
        result=[]
        for ident in ids:
            item=self.items.get(ident) if isinstance(ident,str) else None
            if not item or item['owner']!=self.owner(token):
                raise UploadMissing('附件已失效，请重新选择')
            if state and item['state']!=state:
                raise UploadError('附件状态已变化，请刷新后再试')
            result.append(item)
        return result

    def discard(self, token: str, ids: list) -> None:
        with self.lock:
            if not isinstance(ids,list) or not 1<=len(ids)<=5:raise UploadError('附件列表无效')
            pending=[ident for ident in ids if not self.was_removed(token,ident)]
            if not pending:return
            items=self.resolve(token,pending)
            if any(item['state'] not in ('uploaded','failed') for item in items):
                raise UploadError('附件可能已在电脑输入框中，请在画面中移除')
            for item in items:
                if item['state'] not in ('uploaded','failed'):raise UploadError('附件可能已在电脑输入框中，请在画面中移除')
                self._remove(item);self.remember_removed(item)
            self.save()

    def remember_removed(self, item: dict) -> None:
        self.items.pop(item['id'],None)
        self.removed[item['id']]={'id':item['id'],'owner':item['owner'],'at':time.time()}

    def was_removed(self, token: str, ident: str) -> bool:
        row=self.removed.get(ident) if isinstance(ident,str) else None
        return bool(row and row['owner']==self.owner(token) and time.time()-row['at']<86400)


_CLIPBOARD = r'''
$ErrorActionPreference='Stop';[Console]::InputEncoding=New-Object Text.UTF8Encoding($false);[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type 'using System;using System.Runtime.InteropServices;public static class ClipSeq {[DllImport("user32.dll")]public static extern uint GetClipboardSequenceNumber();[DllImport("user32.dll")]static extern bool OpenClipboard(IntPtr h);[DllImport("user32.dll")]static extern bool CloseClipboard();[DllImport("user32.dll")]static extern int CountClipboardFormats();public static int FormatCount(){if(!OpenClipboard(IntPtr.Zero))throw new ExternalException("Clipboard busy");try{return CountClipboardFormats();}finally{CloseClipboard();}}}'
function Invoke-Clipboard([scriptblock]$action) {
 $timer=[Diagnostics.Stopwatch]::StartNew()
 while($true){
  try{return ,(& $action)}catch{
   if($_.Exception.GetBaseException() -isnot [Runtime.InteropServices.ExternalException] -or $timer.ElapsedMilliseconds -ge 1200){throw}
   [Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 30
  }
 }
}
while($null -ne ($line=[Console]::ReadLine())) {
$sequence=$null;$ready=$false;$stop=$false;$stage='snapshot';$restored=$false
try {
 $request=$line|ConvertFrom-Json
 $snapshotTimer=[Diagnostics.Stopwatch]::StartNew()
 do{
  $before=[ClipSeq]::GetClipboardSequenceNumber()
  $count=Invoke-Clipboard {[ClipSeq]::FormatCount()}
  $saved=New-Object System.Windows.Forms.DataObject
  $old=Invoke-Clipboard {$value=[Windows.Forms.Clipboard]::GetDataObject();if($null -eq $value -and $count -gt 0){throw (New-Object Runtime.InteropServices.ExternalException('Clipboard busy'))};$value}
  if($old){foreach($format in $old.GetFormats($false)){$data=Invoke-Clipboard {$old.GetData($format,$false)};$saved.SetData($format,$false,$data)}}
  $consistent=$before -eq [ClipSeq]::GetClipboardSequenceNumber()
  if(!$consistent -and $snapshotTimer.ElapsedMilliseconds -ge 1500){throw 'clipboard changed'}
 }while(!$consistent)
 $stage='write'
 $drop=New-Object Windows.Forms.DataObject
 if($null -ne $request.text){$drop.SetText([string]$request.text,[Windows.Forms.TextDataFormat]::UnicodeText)}
 else{$files=New-Object Collections.Specialized.StringCollection;foreach($path in $request.paths){[void]$files.Add([string]$path)};$drop.SetFileDropList($files)}
 [void](Invoke-Clipboard {[Windows.Forms.Clipboard]::SetDataObject($drop,$true)});$sequence=[ClipSeq]::GetClipboardSequenceNumber()
 $ready=$true
 [Console]::WriteLine('{"ready":true}');[Console]::Out.Flush()
 $wait=[Console]::In.ReadLineAsync();$timer=[Diagnostics.Stopwatch]::StartNew()
 while(!$wait.IsCompleted -and $timer.ElapsedMilliseconds -lt 20000){[Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 10}
 if(!$wait.IsCompleted){$stop=$true}
} catch {
 # A failed snapshot is reported before any native input. Keep the helper alive.
} finally {
 try{
  if($null -ne $sequence -and [ClipSeq]::GetClipboardSequenceNumber() -eq $sequence){
   if($old){[void](Invoke-Clipboard {[Windows.Forms.Clipboard]::SetDataObject($saved,$true)})}else{[void](Invoke-Clipboard {[Windows.Forms.Clipboard]::Clear()})}
   $restored=$true
  }
 }catch{$stop=$true}
 if($ready){[Console]::WriteLine(('{"restored":'+$restored.ToString().ToLower()+'}'))}
 else{[Console]::WriteLine(('{"ready":false,"stage":"'+$stage+'"}'))}
 [Console]::Out.Flush()
}
$request=$null;$saved=$null;$old=$null;$drop=$null;$line=$null;$data=$null
if($stop){break}
}
'''


def paste_files(paths: list[Path], paste) -> dict:
    """Keep the previous clipboard in memory, paste once, restore if still ours."""
    return _paste({'paths': [str(p) for p in paths]}, paste)


def paste_text(text: str, paste):
    return _paste({'text': text.replace('\r\n', '\n').replace('\r', '\n')}, paste)


def _paste(payload: dict, paste):
    return _bridge.paste(payload, paste)


class ClipboardBridge:
    """Reuse one STA helper; each paste owns and restores its own clipboard."""
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.replies = queue.Queue()
        self.last_restored = None

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        self._close()
        shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = base64.b64encode(_CLIPBOARD.encode('utf-16-le')).decode()
        from app_paths import RUNTIME
        runtime = RUNTIME.parent / 'clipboard-runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, 'TEMP': str(runtime), 'TMP': str(runtime)}
        self.process = subprocess.Popen([str(shell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Sta', '-EncodedCommand', script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
        self.replies = queue.Queue()
        process, replies = self.process, self.replies
        def read():
            try:
                for line in process.stdout:
                    replies.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                replies.put(None)
        threading.Thread(target=read, name='clipboard-bridge', daemon=True).start()

    def paste(self, payload, paste):
        with self.lock:
            self.last_restored = None
            ready = False
            rejected = False
            invoked = False
            try:
                self._start()
                self.process.stdin.write(json.dumps(payload) + '\n')
                self.process.stdin.flush()
                reply = self.replies.get(timeout=5)
                if not reply or not reply.get('ready'):
                    rejected=isinstance(reply,dict) and reply.get('ready') is False
                    raise ClipboardUnavailable('电脑剪贴板暂不可用，请稍后再试')
                ready = True
                invoked = True
                return paste()
            except (queue.Empty, OSError):
                if invoked:raise
                raise ClipboardUnavailable('电脑剪贴板暂不可用，手机草稿已保留') from None
            finally:
                try:
                    if ready and self.process and self.process.poll() is None:
                        self.process.stdin.write('done\n')
                        self.process.stdin.flush()
                        restored = self.replies.get(timeout=3)
                        self.last_restored = restored.get('restored') if isinstance(restored, dict) else None
                        if not restored or 'restored' not in restored:
                            self._close()
                    elif not rejected:
                        self._close()
                except (queue.Empty, OSError):
                    self._close()

    def _close(self):
        process, self.process = self.process, None
        if not process:
            return
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            process.wait(timeout=2)
        finally:
            process.stdout.close()

    def close(self):
        with self.lock:
            self._close()

    def warm(self):
        with self.lock:
            try:
                self._start()
            except OSError:
                self._close()


_bridge = ClipboardBridge()
atexit.register(_bridge.close)


def close_clipboard():
    _bridge.close()


def warm_clipboard():
    _bridge.warm()
