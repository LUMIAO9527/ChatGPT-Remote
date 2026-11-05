"""Native queue controls in an owned Chromium fixture; never a user's chat."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'WindowsAgent'))
from windows_control import WindowsBackend

HTML=r'''<!doctype html><meta charset="utf-8"><title>TITLE</title>
<style>body{font:16px system-ui;padding:18px}.composer-layout{border:1px solid #ccc;padding:12px}
.queue-row{display:flex;gap:10px;padding:10px}.min-w-0{min-width:100px}button{padding:12px}
.sr-only{position:absolute;left:-9999px}textarea{display:block;width:500px;height:70px}[hidden]{display:none!important}</style>
<aside><button class="app-action-sidebar-thread-selected bg-primary-ghost-hover">Queue fixture conversation</button></aside>
<main class="thread-scroll-container"><h5 class="sr-only">你说：</h5><div class="bg-user-message"><p>独立测试</p></div></main>
<div class="composer-layout">
<div id="pause"><span>由于你中断了当前响应，队列已暂停</span><button onclick="pause.hidden=true;stopButton.hidden=false">继续</button></div>
<div id="queue"></div><div id="menu" role="menu" hidden><button role="menuitem" onclick="beginEdit()">编辑消息</button></div>
<textarea id="prompt-textarea" aria-label="使用 ChatGPT Work" placeholder="使用 ChatGPT Work"></textarea>
<button id="send" disabled>排队</button><button id="stopButton" hidden onclick="stopButton.hidden=true;pause.hidden=false">停止</button>
</div>
<script>
let rows=[{id:'a',text:'待编辑的原生消息'},{id:'b',text:'待删除的原生消息'},{id:'c',text:'待引导的原生消息'}],selected='',editing='';
function paint(){
 queue.textContent='';
 rows.forEach(item=>{
  let row=document.createElement('div');row.className='queue-row';
  let preview=document.createElement('div');preview.className='min-w-0 flex-1 self-center text-start select-text';preview.setAttribute('aria-label','Preview queued message 1: '+item.text);preview.textContent=item.text;row.appendChild(preview);
  function button(name,action){let b=document.createElement('button');b.setAttribute('aria-label',name);b.textContent=name;b.onclick=action;row.appendChild(b);}
  button('引导',()=>{rows=rows.filter(r=>r!==item);paint();});
  button('删除排队消息',()=>{rows=rows.filter(r=>r!==item);paint();});
  button('排队消息操作',()=>{selected=item.id;menu.hidden=false;});
  queue.appendChild(row);
 });
}
function beginEdit(){let item=rows.find(r=>r.id===selected);editing=item.id;document.getElementById('prompt-textarea').value=item.text;
 rows=rows.filter(r=>r!==item);menu.hidden=true;send.disabled=false;paint();}
document.getElementById('prompt-textarea').addEventListener('input',()=>{send.disabled=!document.getElementById('prompt-textarea').value.trim();});
send.onclick=()=>{let input=document.getElementById('prompt-textarea');rows.unshift({id:editing||'new',text:input.value});editing='';input.value='';send.disabled=true;paint();};
paint();
</script>'''


@unittest.skipUnless(os.name=='nt' and os.environ.get('RUN_UIA_E2E')=='1','set RUN_UIA_E2E=1 for owned native queue fixture')
class NativeQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend=None;cls.process=None;cls.addClassCleanup(cls.cleanup)
        cls.work=ROOT/'_wip/native-queue-regression';cls.work.mkdir(parents=True,exist_ok=True)
        cls.title='Remote Queue Fixture '+str(os.getpid())
        page=cls.work/'fixture.html';page.write_text(HTML.replace('TITLE',cls.title),encoding='utf8')
        edge=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
        cls.process=subprocess.Popen([str(edge),'--user-data-dir='+str(cls.work/'profile'),'--no-first-run',
            '--no-default-browser-check','--force-renderer-accessibility','--app='+page.as_uri(),'--window-size=900,760'],
            env={**os.environ,'TEMP':str(cls.work),'TMP':str(cls.work)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.backend=WindowsBackend([cls.title])
        facts={}
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            windows=cls.backend._enumerate()
            facts={'windowCount':len(windows)}
            if len(windows)==1:
                cls.hwnd=windows[0]['id'];cls.backend.select(cls.hwnd)
                try:
                    result=cls.backend.queue()
                    facts['queue']=result
                    if len(result['items'])==3:return
                except Exception as exc:
                    facts['error']=str(exc)
                    facts['raw']=cls.backend._reader()._call(operation='queue',hwnd=cls.hwnd)
            time.sleep(.15)
        (cls.work/'startup-result.json').write_text(json.dumps(facts,ensure_ascii=False),encoding='utf8')
        if getattr(cls,'hwnd',None):
            (cls.work/'startup.jpg').write_bytes(cls.backend.capture())
            script=(ROOT/'WindowsAgent/uia_reader.ps1').read_text(encoding='utf8').split('while ($null -ne ($line = [Console]::ReadLine()))')[0]
            script+='\n$t=Get-WindowTree '+str(cls.hwnd)+'; @($t.nodes | ForEach-Object {@{name=$_.Cached.Name;class=$_.Cached.ClassName;type=$_.Cached.ControlType.ProgrammaticName}}) | ConvertTo-Json -Compress -Depth 4'
            debug=cls.work/'inspect-tree.ps1';debug.write_text(script,encoding='utf-8-sig')
            import base64
            bootstrap="& ([scriptblock]::Create([IO.File]::ReadAllText('"+str(debug).replace("'","''")+"',[Text.Encoding]::UTF8)))"
            capture=subprocess.run(['powershell.exe','-NoProfile','-Mta','-EncodedCommand',
                base64.b64encode(bootstrap.encode('utf-16le')).decode()],capture_output=True,timeout=15)
            (cls.work/'tree-debug.txt').write_bytes(capture.stdout+capture.stderr)
        raise RuntimeError('Owned native queue fixture did not expose three queued messages: '+json.dumps(facts,ensure_ascii=False))

    @classmethod
    def cleanup(cls):
        if cls.backend:cls.backend.close()
        if cls.process:
            if cls.process.poll() is None:
                subprocess.run(['taskkill','/PID',str(cls.process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            cls.process.wait(timeout=10)

    def invoke(self,queue,action):
        return self.backend.action({'kind':'queue','queueKey':queue['key'],'targetId':action['id'],'epoch':queue['epoch']})

    def test_native_resume_stop_edit_delete_and_steer(self):
        queue=self.backend.queue();self.assertTrue(queue['paused'])
        resume=next(a for a in queue['actions'] if a['operation']=='resume')
        result=self.invoke(queue,resume);self.assertFalse(result['queue']['paused'])
        queue=result['queue'];stop=next(a for a in queue['actions'] if a['operation']=='stop')
        result=self.invoke(queue,stop);self.assertTrue(result['queue']['paused'])
        queue=result['queue'];item=queue['items'][0]
        edited=self.invoke(queue,next(a for a in item['actions'] if a['operation']=='edit'))
        self.assertEqual(edited['queueDraft']['text'],'待编辑的原生消息')
        self.assertEqual(len(edited['queue']['items']),2)
        draft=edited['queueDraft']
        self.backend.action({'kind':'message','text':'修改后的原生消息','submit':True,'submitMethod':'button',
            'conversationKey':draft['conversationKey'],'draftRevision':draft['revision'],'epoch':draft['epoch']})
        queue=self.backend.queue();self.assertEqual(queue['items'][0]['text'],'修改后的原生消息')
        item=next(x for x in queue['items'] if x['text']=='待删除的原生消息')
        result=self.invoke(queue,next(a for a in item['actions'] if a['operation']=='delete'))
        self.assertFalse(any(x['text']=='待删除的原生消息' for x in result['queue']['items']))
        queue=result['queue'];item=next(x for x in queue['items'] if x['text']=='待引导的原生消息')
        result=self.invoke(queue,next(a for a in item['actions'] if a['operation']=='steer'))
        self.assertEqual([x['text'] for x in result['queue']['items']],['修改后的原生消息'])
        (self.work/'result.json').write_text(json.dumps({'nativeControls':['resume','stop','edit','save','delete','steer'],'passed':True}),encoding='utf8')
