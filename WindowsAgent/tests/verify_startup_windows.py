"""Compile a tiny core to exercise the real launcher without user data or tasks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'WindowsAgent'))
from build_launcher import build


def main():
    work = ROOT/'_wip/startup-checks'/uuid.uuid4().hex
    core = work/'core'
    core.mkdir(parents=True)
    source = work/'fixture.cs'
    source.write_text(r'''
using System; using System.IO; using System.Diagnostics; using System.Web.Script.Serialization; using System.Threading;
class Fixture {
 static int Main(string[] args) {
  var index=Array.IndexOf(args,"--report");
  File.WriteAllText(args[index+1],new JavaScriptSerializer().Serialize(new {
   launcher=Environment.GetEnvironmentVariable("CHATGPTREMOTE_LAUNCHER_EXE"),
   cwd=Environment.CurrentDirectory,temp=Path.GetTempPath(),
   pid=Process.GetCurrentProcess().Id,payload=Environment.GetEnvironmentVariable("CHATGPTREMOTE_PAYLOAD_ID")
  })); if(Array.IndexOf(args,"--hold")>=0) Thread.Sleep(60000); return 0;
 }
}''', encoding='utf-8')
    compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/reference:System.Web.Extensions.dll',
                    '/out:'+str(core/'ChatGPTRemote.Core.exe'), str(source)], check=True)
    (core/'fixture.dat').write_bytes(b'verified fixture component')
    app = work/'installed 程序'
    app.mkdir()
    launcher = build(core, app/'ChatGPTRemote.exe')
    record = {'schema':1,'app':'lumiao.chatgpt.remote','root':str(app),'id':uuid.uuid4().hex,'version':'fixture'}
    (app/'.chatgptremote-install.json').write_text(json.dumps(record), encoding='utf-8')
    elsewhere = work/'unrelated working directory'
    elsewhere.mkdir()
    reports = [work/f'run-{i}.json' for i in range(2)]
    processes = [subprocess.Popen([str(launcher),'--self-test','--report',str(report)],
                                 cwd=elsewhere, creationflags=0x08000000) for report in reports]
    for process, report in zip(processes, reports):
        assert process.wait(timeout=35) == 0
        result = json.loads(report.read_text(encoding='utf-8-sig'))
        assert Path(result['launcher']) == launcher
        assert Path(result['cwd']) == app
        assert app/'_wip/run' in Path(result['temp']).parents
        assert not Path(result['temp']).exists(), 'per-run temp was not collected'
    assert list(elsewhere.iterdir()) == [], 'launcher wrote into caller working directory'
    runtimes = list((app/'_wip/runtime').iterdir())
    assert len(runtimes) == 1, 'concurrent launches must reuse one sealed core'
    manifest = runtimes[0]/'CORE-MANIFEST.json'
    original = manifest.read_bytes()
    manifest.write_bytes(b'{"files":[]}')
    failed = subprocess.run([str(launcher),'--self-test','--report',str(work/'tampered.json')],
                            cwd=elsewhere, creationflags=0x08000000, timeout=15)
    assert failed.returncode == 1 and not (work/'tampered.json').exists()
    manifest.write_bytes(original)
    holding = work/'holding.json'
    parent = subprocess.Popen([str(launcher),'--self-test','--hold','--report',str(holding)],
                               cwd=elsewhere,creationflags=0x08000000)
    try:
        deadline=time.monotonic()+10
        while not holding.is_file() and time.monotonic()<deadline:time.sleep(.05)
        child=json.loads(holding.read_text(encoding='utf-8-sig'))['pid']
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,child)
        assert handle, 'fixture core was not running'
        try:
            assert kernel.WaitForSingleObject(handle,0)==258
            parent.terminate();parent.wait(timeout=5)
            assert kernel.WaitForSingleObject(handle,5000)==0, 'stopping launcher left an orphan core'
        finally:kernel.CloseHandle(handle)
    finally:
        if parent.poll() is None:parent.terminate();parent.wait(timeout=5)
    print(json.dumps({'workingDirectoryIndependent':True,'concurrentCoreReuse':True,
                      'temporaryFilesCollected':True,'tamperedManifestRejected':True,
                      'stoppedLauncherStopsCore':True,'directory':str(work)}))


if __name__ == '__main__': main()
