"""Opt-in Chromium/UIA regression using an isolated, owned browser window.

RUN_UIA_E2E=1 enables this test. All artifacts stay in the project's _wip.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'WindowsAgent'))
from windows_control import WindowsBackend


@unittest.skipUnless(os.name == 'nt' and os.environ.get('RUN_UIA_E2E') == '1', 'set RUN_UIA_E2E=1 for native UIA')
class NativeConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = None
        cls.process = None
        cls.work = ROOT / '_wip' / 'native-uia-regression'
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.addClassCleanup(cls.cleanup)
        cls.title = f'Remote Reader Fixture {os.getpid()}'
        html = r'''<!doctype html><meta charset="utf-8"><title>TITLE</title>
<style>body{font:16px system-ui;margin:20px}.sr-only{position:absolute;left:-9999px}
.thread-scroll-container{max-height:200px;overflow:auto}.bg-user-message{white-space:pre-wrap}#prompt-textarea{padding:12px;border:1px solid #bbb;min-height:60px;max-height:180px;overflow-y:auto;white-space:pre-wrap}
button{padding:10px}aside{height:45px;overflow:auto}.placeholder::before{content:'使用 ChatGPT Work';pointer-events:none;color:#888}.file-card{height:58px;padding:5px;border:1px solid #aaa;margin:5px}.composer-layout{padding:8px;border:1px solid #eee}</style>
<aside><ul><li><button class="app-action-sidebar-thread-selected bg-primary-ghost-hover">Fixture conversation</button></li></ul></aside>
<main class="thread-scroll-container"><div class="relative shrink-0"><h5 class="sr-only">ChatGPT 说：</h5>HISTORY</div>
<div class="relative shrink-0"><h5 class="sr-only">你说：</h5><div class="bg-user-message"><p class="_Paragraph_fixture">继续</p></div><span>星期三20:15</span></div>
<div class="relative shrink-0"><div class="min-w-0 text-size-chat relative overflow-visible py-0">已处理 1分钟 20秒</div></div>
<div class="relative shrink-0"><div class="outline-none"><div class="group/activity-header"><span>正在读取网页</span></div></div></div>
<div class="relative shrink-0"><h5 class="sr-only">ChatGPT 说：</h5>
<div class="reasoning-content" role="group"><p class="_Paragraph_fixture">检查已有结果</p></div>
<p class="_Paragraph_fixture">完整正文 <strong class="font-semibold">重点</strong>，<a href="https://example.com/docs">查看来源</a>。</p>
<ul><li class="_ListItem_fixture"><strong class="font-semibold">列表前半句</strong>：后半句不能丢失</li></ul>
<button aria-label="复制消息">复制</button></div></main>
<div class="queued-message"><div class="min-w-0 flex-1 self-center text-start select-text" role="group" tabindex="0"><div class="line-clamp-1 max-h-lh min-w-0"><p class="_Paragraph_fixture">电脑排队内容</p></div></div></div>
<div class="composer-layout" role="group"><div id="files"></div>
<div id="prompt-textarea" class="ProseMirror" role="textbox" aria-label="使用 ChatGPT Work" aria-multiline="true" contenteditable="true"><p class="placeholder"><br class="ProseMirror-trailingBreak"></p></div>
<button id="send" aria-label="排队" disabled>排队</button><button aria-label="停止">停止</button>
<button id="model" aria-haspopup="listbox" aria-expanded="false">GPT Fixture A</button>
<button id="open-approval">审批测试入口</button><button id="open-choices">工具选择测试入口</button>
</div>
<div id="models" role="listbox" aria-label="模型" hidden><div role="option" tabindex="0" aria-selected="true">GPT Fixture A</div><div role="option" tabindex="0" aria-selected="false">GPT Fixture B</div></div>
<section id="approval" class="approval-card" hidden style="position:fixed;bottom:15px;background:white;padding:12px;border:1px solid">
<h3>示例命令审批</h3><p>仅列出测试目录</p><button>允许一次</button><button>拒绝</button></section>
<section id="choices" role="dialog" aria-label="选择测试方案" hidden style="position:fixed;bottom:15px;background:white;padding:12px;border:1px solid">
<p>选择下一步</p><button>使用方案 A</button><button>使用方案 B</button></section>
<script>
const edit=document.querySelector('#prompt-textarea'),send=document.querySelector('#send');
const model=document.querySelector('#model'),models=document.querySelector('#models');
for(const id of ['approval','choices']){
  const panel=document.getElementById(id);
  document.getElementById('open-'+id).addEventListener('click',()=>{panel.hidden=false;});
  panel.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{panel.hidden=true;}));
}
model.addEventListener('click',()=>{models.hidden=!models.hidden;model.setAttribute('aria-expanded',String(!models.hidden));});
models.querySelectorAll('[role=option]').forEach(option=>option.addEventListener('click',()=>{model.textContent=option.textContent;models.querySelectorAll('[role=option]').forEach(row=>row.setAttribute('aria-selected',String(row===option)));models.hidden=true;model.setAttribute('aria-expanded','false');}));
let count=0;edit.addEventListener('input',()=>{if(edit.textContent.trim())edit.querySelectorAll('.placeholder').forEach(p=>p.classList.remove('placeholder'));send.disabled=!edit.textContent.trim()&&!document.querySelector('#files').children.length;document.querySelector('h5.sr-only').outerHTML='<h5 class="sr-only">ChatGPT 说：</h5>';});
edit.addEventListener('paste',event=>{if(event.clipboardData.files.length){event.preventDefault();for(const file of event.clipboardData.files){const card=document.createElement('div');card.className='file-card';card.innerHTML='<span></span><button aria-label="移除附件">×</button>';card.querySelector('span').textContent=file.name;document.querySelector('#files').appendChild(card);}send.disabled=false;}});
send.addEventListener('click',()=>{count++;const row=document.createElement('div');row.className='relative shrink-0';row.innerHTML='<h5 class="sr-only">你说：</h5><div class="bg-user-message"><p class="_Paragraph_fixture"></p></div>';row.querySelector('p').textContent=edit.innerText;document.querySelector('main').appendChild(row);edit.innerHTML='<p class="placeholder"><br class="ProseMirror-trailingBreak"></p>';document.querySelector('#files').textContent='';send.disabled=true;document.title='TITLE';});
</script>'''.replace('TITLE', cls.title).replace('HISTORY', '<p>旧内容</p>' * 6100)
        page = cls.work / 'fixture.html'
        page.write_text(html, encoding='utf-8')
        edge = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        env = {**os.environ, 'TEMP': str(cls.work), 'TMP': str(cls.work)}
        cls.process = subprocess.Popen([str(edge), '--user-data-dir=' + str(cls.work / ('profile-' + str(os.getpid()))),
            '--no-first-run', '--no-default-browser-check', '--force-renderer-accessibility=complete',
            '--enable-features=UiaProvider',
            '--app=' + page.as_uri(), '--window-size=720,800'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        cls.backend = WindowsBackend([cls.title])
        for _ in range(60):
            windows = cls.backend._enumerate()
            if len(windows) == 1:
                cls.hwnd = windows[0]['id']
                cls.backend.select(cls.hwnd)
                cls.backend._focus(cls.hwnd)
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Isolated UIA fixture did not open')
        reader=cls.backend._reader()
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            observed=reader._call(operation='observe',hwnd=cls.hwnd,fresh=True)
            if observed.get('selectedTitle')=='Fixture conversation' and observed.get('composer',{}).get('available'):
                cls.backend._focus(cls.hwnd)
                break
            time.sleep(.15)
        else:
            facts={k:observed.get(k) for k in ('ok','reason','selectedTitle','composer')}
            (cls.work/'startup-result.json').write_text(json.dumps(facts,ensure_ascii=False),encoding='utf-8')
            raise RuntimeError('Isolated UIA fixture did not finish loading: '+json.dumps(facts,ensure_ascii=False))

    @classmethod
    def cleanup(cls):
        if cls.backend:
            cls.backend.close()
        if cls.process:
            if cls.process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(cls.process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            cls.process.wait(timeout=10)

    def test_native_structure_identity_and_single_submission(self):
        reader = self.backend._reader()
        started = time.monotonic()
        before = self.backend.read_conversation()
        (self.work / 'before.json').write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
        full_identity = reader.cached[3]
        quick = reader._call(operation='identity', hwnd=self.hwnd, fresh=True)
        self.assertEqual(full_identity, quick['identity'])
        self.assertIn('|thread|', full_identity)
        self.assertTrue(before['truncated'])
        self.assertEqual(before['messages'][0]['text'], '继续')
        self.assertEqual(before['messages'][0]['timestamp'], '星期三20:15')
        self.assertEqual(before['queued'][0]['text'], '电脑排队内容')
        self.assertFalse(any('电脑排队内容' in m['text'] for m in before['messages']))
        self.assertFalse(any('已处理' in m['text'] for m in before['messages']))
        self.assertEqual(before['messages'][1]['role'], 'assistant')
        self.assertEqual(before['messages'][1]['phase'], 'activity')
        answer = before['messages'][-1]
        self.assertIn('列表前半句：后半句不能丢失', answer['text'])
        self.assertTrue(any(b.get('channel') == 'reasoning' for b in answer['blocks']))
        self.assertTrue(any(r.get('href') == 'https://example.com/docs' for b in answer['blocks'] for r in b.get('runs', [])))
        text = '继续\n手机中文、emoji 🐾，完整文本。'
        trace=[];original=reader.prepare
        def prepare(*args,**kwargs):
            trace.append({'stage':'before_prepare','targetForeground':int(self.backend.u32.GetForegroundWindow() or 0)==self.hwnd})
            result=original(*args,**kwargs)
            trace.append({'stage':'after_prepare','targetForeground':int(self.backend.u32.GetForegroundWindow() or 0)==self.hwnd})
            (self.work/'focus-result.json').write_text(json.dumps(trace),encoding='utf-8')
            return result
        reader.prepare=prepare;self.addCleanup(setattr,reader,'prepare',original)
        result = self.backend.action({'kind': 'message', 'text': text, 'submit': True,
            'submitMethod': 'button', 'epoch': before['epoch'], 'conversationKey': before['key']})
        self.assertEqual(result['submission']['state'], 'queued')
        reader.cached = None
        after = self.backend.read_conversation()
        (self.work / 'after.json').write_text(json.dumps(after, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(sum(m['role'] == 'user' and m['text'] == text for m in after['messages']), 1)
        self.assertFalse(after['composer']['hasDraft'])
        report = {'identityMatches': True, 'structuredContent': True, 'exactlyOneNewMessage': True,
                  'elapsedMs': round((time.monotonic()-started)*1000)}
        (self.work / 'result.json').write_text(json.dumps(report), encoding='utf-8')

    def test_native_x_model_listbox_changes_selection_and_preserves_draft(self):
        reader=self.backend._reader()
        root=self.backend.composer_options()
        trigger=next(item for item in root['items'] if item['kind']=='model')
        draft=reader.draft(self.hwnd)
        opened=reader.composer_action(self.hwnd,root['key'],trigger['id'],refresh=True)['options']
        self.assertTrue(opened['menu'], opened)
        choice=next(item for item in opened['items'] if item['title']=='GPT Fixture B')
        changed=reader.composer_action(self.hwnd,opened['key'],choice['id'],refresh=True)['options']
        self.assertFalse(changed['menu'])
        self.assertTrue(any(item['kind']=='model' and item['title']=='GPT Fixture B' for item in changed['items']), changed)
        self.assertEqual(reader.draft(self.hwnd)['text'],draft['text'])

    def test_native_xx_desktop_approval_and_dialog_choices_can_be_read_and_invoked(self):
        def invoke(snapshot, label):
            target = next(item for item in snapshot['items'] if item['title'] == label)
            return self.backend.action({'kind': 'composer', 'operation': 'invoke', 'optionsKey': snapshot['key'],
                                        'targetId': target['id'], 'epoch': snapshot['epoch']})['options']

        for trigger, text, choice, approval in (
            ('审批测试入口', '仅列出测试目录', '允许一次', True),
            ('工具选择测试入口', '选择下一步', '使用方案 B', False),
        ):
            current = invoke(self.backend.composer_options(), trigger)
            self.assertEqual(current['prompt']['approval'], approval)
            self.assertIn(text, current['prompt']['text'])
            streamed = self.backend.read_conversation()['prompt']
            self.assertEqual(streamed['key'], current['key'])
            next_options = invoke(current, choice)
            self.assertNotIn('prompt', next_options)

    def test_native_y_text_does_not_require_clipboard_access(self):
        import ctypes
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.OpenClipboard.argtypes=[ctypes.c_void_p]
        user32.GetOpenClipboardWindow.restype=ctypes.c_void_p
        reader=self.backend._reader();samples=[]
        for text in ('剪贴板被占用时发送。\n中文与 emoji 🐾',('长文字。中文与 emoji 🐾\n'*90).rstrip('\n')):
            reader.cached=None;before=self.backend.read_conversation();self.backend._focus(self.hwnd)
            self.assertTrue(user32.OpenClipboard(self.hwnd))
            self.assertEqual(user32.GetOpenClipboardWindow(),self.hwnd)
            sequence=user32.GetClipboardSequenceNumber();started=time.monotonic()
            try:
                self.backend.action({'kind':'message','text':text,'submit':True,'submitMethod':'button',
                    'epoch':before['epoch'],'conversationKey':before['key']})
                action_ms=round((time.monotonic()-started)*1000)
                self.assertEqual(user32.GetClipboardSequenceNumber(),sequence)
            finally:user32.CloseClipboard()
            reader.cached=None;after=self.backend.read_conversation()
            self.assertEqual(sum(m['role']=='user' and m['text']==text for m in after['messages']),1)
            self.assertFalse(after['composer']['hasDraft'])
            samples.append({'length':len(text),'actionMs':action_ms,'exactlyOnce':True,'clipboardUnchanged':True})
        (self.work/'clipboard-locked-result.json').write_text(json.dumps({'samples':samples}),encoding='utf-8')

    def test_native_z_attachment_and_placeholder_with_enabled_queue_button(self):
        reader=self.backend._reader();reader.cached=None
        before=self.backend.read_conversation()
        path=self.work/'手机附件.txt';path.write_text('owned fixture',encoding='utf-8')
        attached=self.backend.attach_files([path],before['key'],before['epoch'])
        self.assertEqual(attached['state'],'attached')
        draft=reader.draft(self.hwnd)
        self.assertFalse(draft['hasDraft']);self.assertEqual(draft['text'],'')
        result=self.backend.action({'kind':'message','text':'使用 ChatGPT Work','submit':True,'submitMethod':'button',
                                    'epoch':before['epoch'],'conversationKey':before['key']})
        self.assertEqual(result['submission']['state'],'queued')
        reader.cached=None;after=self.backend.read_conversation()
        self.assertEqual(sum(m['role']=='user' and m['text']=='使用 ChatGPT Work' for m in after['messages']),1)
        self.assertFalse(after['composer']['hasDraft'])
        (self.work/'attachment-result.json').write_text(json.dumps({'attached':True,'placeholderIsEmpty':True,
            'literalPromptSentOnce':True}),encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
