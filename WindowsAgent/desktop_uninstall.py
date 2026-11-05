"""Allowlisted uninstall with a prepared/commit handoff to an independent worker."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from app_paths import ROOT, HERE, EXECUTABLE, FROZEN
from desktop_installation import installation_record, plain_path, atomic_json


def owned_targets(root, keep):
    root=plain_path(root)
    targets=[root/'ChatGPTRemote.exe',root/'_wip/desktop-runtime',root/'_wip/update',root/'_wip/clipboard-runtime']
    if not keep:targets += [root/'_data',root/'.chatgptremote-install.json']
    for target in targets:plain_path(target)
    return targets


def prepare(keep, *, pids=(), root=ROOT, executable=EXECUTABLE, resources=HERE,
            frozen=FROZEN, popen=subprocess.Popen):
    if type(keep) is not bool:raise ValueError('请选择是否保留配对与设置。')
    if not frozen:raise ValueError('源码模式请使用项目的移除说明。')
    root=plain_path(root);executable=plain_path(executable)
    if executable!=root/'ChatGPTRemote.exe' or not installation_record(root):
        raise ValueError('无法确认当前程序的安装位置。')
    batch=plain_path(root/'_wip/uninstall'/uuid.uuid4().hex)
    batch.mkdir(parents=True)
    for name in ('uninstall.ps1','manage_frozen.ps1'):
        (batch/name).write_bytes((resources/name).read_bytes())
    token=uuid.uuid4().hex
    plan={'schema':1,'root':str(root),'executable':str(executable),'keep':keep,
          'token':token,'id':installation_record(root)['id'],
          'sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
          'targets':[str(path) for path in owned_targets(root,keep)],
          'pids':list(dict.fromkeys([os.getpid(),*pids]))}
    atomic_json(batch/'plan.json',plan)
    powershell=Path(os.environ.get('WINDIR',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    process=popen([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(batch/'uninstall.ps1'),'-Plan',str(batch/'plan.json')],
                  cwd=batch,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline and process.poll() is None:
        try:
            ready=json.loads((batch/'prepared.json').read_text(encoding='utf-8-sig'))
            if ready.get('token')==token and ready.get('pid')==process.pid:
                return batch,token
        except (OSError,ValueError):pass
        time.sleep(.05)
    if process.poll() is None:process.terminate()
    raise ValueError('卸载组件未能就绪，当前程序保持运行，请重试。')


def commit(batch,token):
    atomic_json(plain_path(batch)/'commit.json',{'token':token})
