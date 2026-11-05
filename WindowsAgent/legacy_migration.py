"""Explicit import of a stopped source installation; originals stay in place."""
import json
import os
from pathlib import Path
import subprocess
import uuid
from app_paths import ROOT, HERE, DATA, FROZEN
from desktop_installation import plain_path, installation_record
from lifecycle_storage import atomic_bytes, secure_path

FILES=('access_key.txt','paired_devices.json','session.json','desktop_settings.json','desktop_control.dat')


def import_legacy(folder, *, root=ROOT, data=DATA, frozen=FROZEN):
    if not frozen or not installation_record(root):raise ValueError('请先完成独立程序的首次使用。')
    legacy=plain_path(folder);source=plain_path(legacy/'WindowsAgent/data')
    if legacy==plain_path(root) or not source.is_dir():raise ValueError('请选择旧版 Remote 的项目文件夹。')
    paths=[plain_path(source/name) for name in FILES if (source/name).is_file()]
    if not paths:raise ValueError('该文件夹没有可导入的配对与设置。')
    if any(path.stat().st_size>4*1024*1024 for path in paths):raise ValueError('旧版数据大小异常，请检查后重试。')
    data=plain_path(data)
    if any((data/name).exists() for name in FILES):raise ValueError('当前程序已有配对或设置，不能覆盖导入。')
    batch=plain_path(root/'_wip/legacy-migration'/uuid.uuid4().hex);batch.mkdir(parents=True)
    snapshot=batch/'tasks.json'
    powershell=Path(os.environ.get('WINDIR',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    def tasks(action):
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(HERE/'legacy_tasks.ps1'),'-LegacyRoot',str(legacy),'-Action',action,'-Snapshot',str(snapshot)],capture_output=True,timeout=35,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('请先停止旧版 Remote 的后台服务并退出托盘，再导入。请确认所选目录正确。')
    tasks('Inspect')
    copied=[]
    try:
        data.mkdir(parents=True,exist_ok=True);secure_path(data)
        for path in paths:
            target=data/path.name
            if target.exists():raise ValueError('当前数据已经改变，导入已取消。')
            content=path.read_bytes();atomic_bytes(target,content,private=True);copied.append((target,content))
            if target.read_bytes()!=content:raise ValueError('导入校验失败。')
        tasks('Remove')
    except Exception:
        for target,content in copied:
            if target.is_file() and target.read_bytes()==content:target.unlink()
        if snapshot.is_file():tasks('Restore')
        raise
    return {'imported':len(copied)}
