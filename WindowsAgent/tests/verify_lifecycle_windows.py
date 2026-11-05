"""Run the compiled updater and uninstall worker only in synthetic _wip cases."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid
ROOT=Path(__file__).resolve().parents[2]
AGENT=ROOT/'WindowsAgent'
sys.path.insert(0,str(AGENT))
from lifecycle_processes import application_pids
from desktop_uninstall import owned_targets

HIDDEN=0x08000000
CSC=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
PS=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'

def write(path,value):path.write_text(json.dumps(value),encoding='utf8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def wait_json(path,process,seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if path.is_file():return json.loads(path.read_text(encoding='utf-8-sig'))
        if process.poll() is not None:break
        time.sleep(.04)
    raise AssertionError('missing '+path.name)
def compile(source,output):
    result=subprocess.run([str(CSC),'/nologo','/target:winexe','/platform:x64','/reference:System.Web.Extensions.dll','/out:'+str(output),*map(str,source)],capture_output=True,timeout=30,creationflags=HIDDEN)
    if result.returncode:raise AssertionError(result.stdout.decode(errors='replace'))

FIXTURE=r'''
using System; using System.IO; using System.Diagnostics; using System.Threading;
using System.Collections.Generic; using System.Web.Script.Serialization;
class Fixture {
 static int Main(string[] args) {
  if(args[0]=="--parent"){while(!File.Exists(args[1]))Thread.Sleep(40);return 0;}
  var ack=args[1];var token=args[3];
  var plan=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(Path.GetDirectoryName(ack),"plan.json")));
  const string version="FIXTURE_VERSION";
  if(Environment.GetEnvironmentVariable("RM_FIXTURE_FAIL_NEW")=="1" && version=="0.19.0")return 2;
  if((string)plan["version"]!=version)return 3;
  File.WriteAllText(ack,new JavaScriptSerializer().Serialize(new {ready=true,version=version,token=token,pid=Process.GetCurrentProcess().Id,launcher_pid=Process.GetCurrentProcess().Id,payload_id=""}));
  Thread.Sleep(60000);return 0;
 }
}'''

def update_case(work,name,old,new,helper):
    app=work/name;app.mkdir();target=app/'ChatGPTRemote.exe';shutil.copyfile(old,target)
    data=app/'_data';data.mkdir();(data/'paired_devices.json').write_bytes(b'synthetic-pairing');(app/'notes.txt').write_bytes(b'user-file')
    batch=app/'_wip/update'/uuid.uuid4().hex;batch.mkdir(parents=True)
    stage=batch/'ChatGPTRemote.new.exe';shutil.copyfile(new,stage);worker=batch/'ChatGPTRemote.Update.exe';shutil.copyfile(helper,worker)
    stop=app/'parent-stop';parent=subprocess.Popen([str(target),'--parent',str(stop)],creationflags=HIDDEN)
    extra=None;foreign=None
    try:
        extras=[]
        if name=='wait-all-processes':
            extra=subprocess.Popen([str(target),'--parent',str(app/'extra-stop')],creationflags=HIDDEN);extras=[extra.pid]
        elif name=='foreign-process':
            foreign_path=app/'other.exe';shutil.copyfile(old,foreign_path)
            foreign=subprocess.Popen([str(foreign_path),'--parent',str(app/'foreign-stop')],creationflags=HIDDEN);extras=[foreign.pid]
        plan={'schema':2,'root':str(app),'executable':str(target),'parent_pid':parent.pid,'worker_pid':parent.pid,
              'extra_pids':extras,'version':'0.19.0','sha256':sha(stage),'size':stage.stat().st_size,'token':uuid.uuid4().hex,
              'previous_version':'0.18.0','previous_sha256':sha(target),'previous_size':target.stat().st_size,'previous_payload_id':''}
        if name=='checksum':plan['sha256']='0'*64
        write(batch/'plan.json',plan)
        environment={**os.environ,'RM_FIXTURE_FAIL_NEW':'1' if name=='rollback' else '0'}
        process=subprocess.Popen([str(worker),'-Plan',str(batch/'plan.json')],env=environment,creationflags=HIDDEN)
        if name!='checksum':
            ready=wait_json(batch/'prepared.json',process);assert ready['token']==plan['token']
        stop.touch();parent.wait(timeout=5)
        if extra:
            time.sleep(.25);assert sha(target)==sha(old);(app/'extra-stop').touch();extra.wait(timeout=5)
        result=wait_json(batch/'result.json',process);process.wait(timeout=5)
        expected='rolled_back' if name=='rollback' else 'failed' if name in ('checksum','foreign-process') else 'complete'
        assert result['status']==expected,(name,result)
        assert sha(target)==sha(new if expected=='complete' else old)
        assert (data/'paired_devices.json').read_bytes()==b'synthetic-pairing'
        assert (app/'notes.txt').read_bytes()==b'user-file'
        return {'case':name,'status':result['status'],'dataPreserved':True}
    finally:
        if ROOT/'_wip' not in app.resolve().parents:raise AssertionError('unsafe fixture')
        for pid in application_pids(target):
            try:os.kill(pid,signal.SIGTERM)
            except OSError:pass
        for child in (parent,extra,foreign):
            if child and child.poll() is None:child.terminate();child.wait(timeout=5)

def uninstall_case(work,name,old):
    app=work/name;app.mkdir();target=app/'ChatGPTRemote.exe';shutil.copyfile(old,target)
    data=app/'_data';data.mkdir();(data/'paired_devices.json').write_bytes(b'synthetic-pairing');(app/'notes.txt').write_bytes(b'user-file')
    (app/'_wip/desktop-runtime').mkdir(parents=True);(app/'_wip/desktop-runtime/cache.tmp').write_bytes(b'cache')
    ident=uuid.uuid4().hex;write(app/'.chatgptremote-install.json',{'app':'lumiao.chatgpt.remote','schema':1,'root':str(app),'id':ident})
    batch=app/'_wip/uninstall'/uuid.uuid4().hex;batch.mkdir(parents=True)
    for f in ('uninstall.ps1','manage_frozen.ps1'):shutil.copyfile(AGENT/f,batch/f)
    keep=name!='clear-data';token=uuid.uuid4().hex
    targets=[str(v) for v in owned_targets(app,keep)]
    if name=='invalid-target':targets[0]=str(work/'outside.txt');(work/'outside.txt').write_bytes(b'outside')
    write(batch/'plan.json',{'schema':1,'root':str(app),'executable':str(target),'sha256':sha(target),'id':ident,'token':token,'keep':keep,'targets':targets,'pids':[],'quiet':True})
    # Shadow the task lookup only in this isolated PowerShell test process.
    # Any attempted task mutation is a test failure; real user tasks are untouched.
    script="function global:Get-ScheduledTask { param($TaskName,$ErrorAction) return $null };function global:Unregister-ScheduledTask { throw 'unexpected_task_mutation' }; & '"+str(batch/'uninstall.ps1')+"' -Plan '"+str(batch/'plan.json')+"'"
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    process=subprocess.Popen([str(PS),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',encoded],creationflags=HIDDEN,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if name=='invalid-target':
        result=wait_json(batch/'result.json',process);process.wait(timeout=5)
        assert result['stage']=='validate' and target.is_file();assert (work/'outside.txt').read_bytes()==b'outside'
    else:
        ready=wait_json(batch/'prepared.json',process);assert ready['token']==token
        write(batch/'commit.json',{'token':token});assert process.wait(timeout=15)==0
        assert not target.exists();assert data.exists()==keep
        assert (app/'.chatgptremote-install.json').exists()==keep
        if keep:assert (data/'paired_devices.json').read_bytes()==b'synthetic-pairing'
    assert (app/'notes.txt').read_bytes()==b'user-file'
    return {'case':name,'status':'passed','foreignFilesPreserved':True}

def main():
    if os.name!='nt':raise SystemExit('Windows required')
    work=ROOT/'_wip/windows-lifecycle'/uuid.uuid4().hex;work.mkdir(parents=True)
    helper=work/'ChatGPTRemote.Update.exe';compile([AGENT/'update_common.cs',AGENT/'update_helper.cs'],helper)
    fixtures=[]
    for version in ('0.18.0','0.19.0'):
        source=work/(version+'.cs');source.write_text(FIXTURE.replace('FIXTURE_VERSION',version),encoding='utf8')
        output=work/(version+'.exe');compile([source],output);fixtures.append(output)
    results=[update_case(work,name,*fixtures,helper) for name in ('success','rollback','checksum','wait-all-processes','foreign-process')]
    results += [uninstall_case(work,name,fixtures[0]) for name in ('keep-data','clear-data','invalid-target')]
    write(work/'results.json',results);print(json.dumps({'cases':results,'directory':str(work)}))

if __name__=='__main__':main()
