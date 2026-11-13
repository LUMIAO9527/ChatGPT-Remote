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
from windows_control import TargetError, WindowsBackend


@unittest.skipUnless(os.name == 'nt' and os.environ.get('RUN_UIA_E2E') == '1', 'set RUN_UIA_E2E=1 for native UIA')
class NativeConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = None
        cls.process = None
        cls.work = ROOT / '_wip' / ('AUTO_' + time.strftime('%Y%m%d') + 'NativeUIA') / str(os.getpid())
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.addClassCleanup(cls.cleanup)
        cls.title = f'Remote Reader Fixture {os.getpid()}'
        html = r'''<!doctype html><meta charset="utf-8"><title>TITLE</title>
<style>body{font:16px system-ui;margin:20px}.sr-only{position:absolute;left:-9999px}
.thread-scroll-container{max-height:200px;overflow:auto}.bg-user-message{white-space:pre-wrap}#prompt-textarea{box-sizing:border-box;width:100%;padding:12px;border:1px solid #bbb;min-height:60px;max-height:180px;overflow-y:auto;white-space:pre-wrap}
button{padding:10px}aside{height:45px;overflow:auto}.placeholder::before{content:'使用 ChatGPT Work';pointer-events:none;color:#888}.file-card{height:58px;padding:5px;border:1px solid #aaa;margin:5px}.fixture-composer{padding:8px;border:1px solid #eee}</style>
<aside><ul><li><button class="app-action-sidebar-thread-selected bg-primary-ghost-hover">Fixture conversation</button></li>
<li><button class="app-action-sidebar-thread-selected">模型选择历史</button></li>
<li id="pending-thread" hidden><button class="app-action-sidebar-thread-selected" aria-label="Fixture pending"><span>Fixture pending</span><span class="bg-chart-blue/15"><span>需要用户输入</span></span></button></li></ul></aside>
<main class="thread-scroll-container"><div class="relative shrink-0"><div id="first-marker" class="sr-only select-none">ChatGPT 说：</div><div role="group">HISTORY</div></div>
<div class="relative shrink-0"><h5 class="sr-only">你说：</h5><div class="bg-user-message"><p class="_Paragraph_fixture">继续</p></div><span>星期三20:15</span></div>
<div class="relative shrink-0"><div class="min-w-0 text-size-chat relative overflow-visible py-0">核对当前数据</div></div>
<div class="relative shrink-0"><div class="min-w-0 text-size-chat relative overflow-visible py-0">已处理 1分钟 20秒</div></div>
<div class="relative shrink-0"><div class="outline-none"><div class="group/activity-header"><span>正在读取网页</span></div></div></div>
<div class="relative shrink-0"><h5 class="sr-only">ChatGPT 说：</h5>
<div class="reasoning-content" role="group"><p class="_Paragraph_fixture">检查已有结果</p></div>
<p class="_Paragraph_fixture">完整正文 <strong class="font-semibold">重点</strong>，<a href="https://example.com/docs">查看来源</a>。</p>
<ul><li class="_ListItem_fixture"><strong class="font-semibold">列表前半句</strong>：后半句不能丢失</li></ul>
<button aria-label="复制消息">复制</button></div></main>
<div class="queued-message"><div class="min-w-0 flex-1 self-center text-start select-text" role="group" tabindex="0"><div class="line-clamp-1 max-h-lh min-w-0"><p class="_Paragraph_fixture">电脑排队内容</p></div></div></div>
<div class="fixture-composer flex w-full flex-col gap-2 relative" role="group"><div id="files"></div>
<div class="relative" role="group"><div class="ComposerLayoutBody-fixture LegacyExpandableBody-fixture" role="group">
<div id="prompt-textarea" class="ProseMirror" role="textbox" aria-label="使用 ChatGPT Work" aria-multiline="true" contenteditable="true"><p class="placeholder"><br class="ProseMirror-trailingBreak"></p></div>
</div></div>
<button id="send" aria-label="排队">排队</button><button aria-label="停止">停止</button>
<button id="model" aria-haspopup="listbox" aria-expanded="false">GPT Fixture A</button>
<button id="open-approval">审批测试入口</button><button id="open-choices">工具选择测试入口</button><button id="open-question">工具问卷测试入口</button>
</div>
<div id="models" role="listbox" aria-label="模型" hidden><div role="option" tabindex="0" aria-selected="true">GPT Fixture A</div><div role="option" tabindex="0" aria-selected="false">GPT Fixture B</div></div>
<section id="approval" class="approval-card" hidden style="position:fixed;bottom:15px;background:white;padding:12px;border:1px solid">
<h3>示例命令审批</h3><p>仅列出测试目录</p><button>允许一次</button><button>拒绝</button></section>
<section id="choices" role="dialog" aria-label="选择测试方案" hidden style="position:fixed;bottom:15px;background:white;padding:12px;border:1px solid">
<p>选择下一步</p><button>使用方案 A</button><button>使用方案 B</button></section>
<div id="question" class="@container/request-card" hidden style="position:fixed;bottom:15px;background:white;padding:12px;border:1px solid">
<div id="first-question"><h3>接下来选哪个方案？</h3><div role="radiogroup">
<button role="radio" aria-checked="false">保留原方案</button><button role="radio" aria-checked="false">采用新方案</button></div>
<button id="question-next" data-codex-composer-request-navigation disabled>下一步</button></div>
<div id="last-question" hidden><h3>要附上哪些内容？</h3><div role="group">
<button role="checkbox" aria-checked="false">验证结果</button><button role="checkbox" aria-checked="false">修改记录</button></div>
<button id="question-submit" data-codex-composer-request-navigation disabled>提交 ⏎</button></div></div>
<button id="pointer-zone" hidden style="position:fixed;inset:30% 15%;background:white">点击测试区域</button>
<script>
const edit=document.querySelector('#prompt-textarea'),send=document.querySelector('#send');
const model=document.querySelector('#model'),models=document.querySelector('#models');
for(const id of ['approval','choices']){
  const panel=document.getElementById(id);
  document.getElementById('open-'+id).addEventListener('click',()=>{panel.hidden=false;});
  panel.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{panel.hidden=true;}));
}
const pending=document.querySelector('#pending-thread'),question=document.querySelector('#question');
const next=document.querySelector('#question-next'),submit=document.querySelector('#question-submit');
document.querySelector('#open-question').onclick=()=>{pending.hidden=false;};
pending.querySelector('button').onclick=()=>{
  document.querySelector('aside .bg-primary-ghost-hover').classList.remove('bg-primary-ghost-hover');
  pending.querySelector('button').classList.add('bg-primary-ghost-hover');
  question.hidden=false;
};
const radios=question.querySelectorAll('[role=radio]');
radios.forEach(button=>button.onclick=()=>{radios.forEach(row=>row.setAttribute('aria-checked',String(row===button)));next.disabled=false;});
next.onclick=()=>{document.querySelector('#first-question').hidden=true;document.querySelector('#last-question').hidden=false;};
const checks=question.querySelectorAll('[role=checkbox]');
checks.forEach(button=>button.onclick=()=>{
  button.setAttribute('aria-checked',String(button.getAttribute('aria-checked')!=='true'));
  submit.disabled=!Array.from(checks).some(row=>row.getAttribute('aria-checked')==='true');
});
submit.onclick=()=>{question.hidden=true;pending.querySelector('[class="bg-chart-blue/15"]').remove();};
const pointer=document.querySelector('#pointer-zone');
document.addEventListener('keydown',event=>{
  if(event.key==='F6'){
    const files=document.querySelector('#files');
    files.replaceChildren();
    for(const name of ['sample-a.jpg','sample-b.jpg']){
      const button=document.createElement('button');
      button.setAttribute('aria-label','Remove attachment '+name);
      button.textContent=name;
      button.onclick=()=>button.remove();
      files.appendChild(button);
    }
    return;
  }
  if(event.key==='F7'){pointer.hidden=false;document.title='TITLE Pointer ready';return;}
  const sidebar=document.querySelector('aside');
  if(event.key==='F8'){sidebar.hidden=true;return;}
  if(event.key==='F9'){
    const title=event.shiftKey?'Fixture conversation':'模型选择历史';
    sidebar.querySelectorAll('.app-action-sidebar-thread-selected').forEach(row=>row.classList.toggle('bg-primary-ghost-hover',row.textContent===title));
    sidebar.hidden=false;
  }
});
pointer.onclick=()=>{document.title='TITLE Left';};
pointer.oncontextmenu=event=>{event.preventDefault();document.title='TITLE Right';};
model.addEventListener('click',()=>{models.hidden=!models.hidden;model.setAttribute('aria-expanded',String(!models.hidden));});
models.querySelectorAll('[role=option]').forEach(option=>option.addEventListener('click',()=>{model.textContent=option.textContent;models.querySelectorAll('[role=option]').forEach(row=>row.setAttribute('aria-selected',String(row===option)));models.hidden=true;model.setAttribute('aria-expanded','false');}));
let count=0;edit.addEventListener('input',()=>{if(edit.textContent.trim())edit.querySelectorAll('.placeholder').forEach(p=>p.classList.remove('placeholder'));const marker=document.querySelector('#first-marker');marker.replaceWith(marker.cloneNode(true));});
edit.addEventListener('paste',event=>{if(event.clipboardData.files.length){event.preventDefault();for(const file of event.clipboardData.files){
  const card=document.createElement('div');card.className='file-card';
  if(file.type.startsWith('image/')){const img=new Image(50,50);img.className='object-cover';img.alt='附件预览';img.src=URL.createObjectURL(file);card.appendChild(img);}
  else{const label=document.createElement('span');label.textContent=file.name;card.appendChild(label);}
  document.querySelector('#files').appendChild(card);
}send.disabled=false;}});
send.addEventListener('click',()=>{count++;const row=document.createElement('div');row.className='relative shrink-0';row.innerHTML='<h5 class="sr-only">你说：</h5><div class="bg-user-message"><p class="_Paragraph_fixture"></p></div>';row.querySelector('p').textContent=edit.innerText;document.querySelector('main').appendChild(row);edit.innerHTML='<p class="placeholder"><br class="ProseMirror-trailingBreak"></p>';document.querySelector('#files').textContent='';document.title='TITLE';});
</script>'''.replace('TITLE', cls.title).replace('HISTORY', '<p>旧内容</p>' * 6100)
        page = cls.work / 'fixture.html'
        page.write_text(html, encoding='utf-8')
        edge = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        browser = Path(os.environ.get('REMOTE_UIA_BROWSER', str(edge)))
        env = {**os.environ, 'TEMP': str(cls.work), 'TMP': str(cls.work)}
        cls.process = subprocess.Popen([str(browser), '--user-data-dir=' + str(cls.work / 'browser-profile'),
            '--no-first-run', '--no-default-browser-check', '--force-renderer-accessibility=complete', '--disable-background-mode',
            '--enable-features=UiaProvider',
            '--app=' + page.as_uri(), '--window-size=720,800'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        cls.backend = WindowsBackend([cls.title])
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            windows = cls.backend._enumerate()
            for window in windows:
                if window['title'] != cls.title:
                    continue
                try:
                    cls.backend.select(window['id'])
                    cls.hwnd = window['id']
                    cls.backend._focus(window['id'])
                    observed = cls.backend._reader()._call(operation='observe', hwnd=window['id'], fresh=True)
                    if observed.get('selectedTitle') == 'Fixture conversation' and observed.get('composer', {}).get('available'):
                        return
                except TargetError as error:
                    if error.code not in ('local_input_active', ''):
                        raise
            time.sleep(.25)
        raise RuntimeError('Isolated UIA fixture did not expose its conversation and editor')

    @classmethod
    def cleanup(cls):
        if cls.backend:
            if getattr(cls, 'hwnd', 0) and cls.backend.u32.IsWindow(cls.hwnd):
                cls.backend.u32.PostMessageW(cls.hwnd, 0x0010, 0, 0)
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
        self.assertEqual(before['messages'][1]['phase'], 'reasoning')
        self.assertEqual(before['messages'][1]['blocks'][0]['text'], '核对当前数据')
        self.assertEqual(before['messages'][1]['blocks'][0]['channel'], 'reasoning')
        answer = next(item for item in reversed(before['messages']) if item['role']=='assistant')
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

    def test_native_fallback_without_sidebar_uses_the_same_conversation_for_read_and_send(self):
        reader = self.backend._reader()

        def toggle_sidebar():
            self.backend._focus(self.hwnd)
            self.backend._key(0x77, True)
            self.backend._key(0x77, False)

        toggle_sidebar()
        try:
            deadline = time.monotonic() + 3
            while reader._call(operation='observe', hwnd=self.hwnd, fresh=True).get('selectedTitle'):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            reader.cached = None
            before = self.backend.read_conversation()
            identity = reader.cached[3]
            self.assertIn('|message|', identity)
            self.assertEqual(reader._call(operation='identity', hwnd=self.hwnd, fresh=True)['identity'], identity)
            text = '同一对话的手机发送测试'
            result = self.backend.action({'kind': 'message', 'text': text, 'submit': True, 'epoch': before['epoch'],
                                          'conversationKey': before['key']})
            self.assertIn(result['submission']['state'], ('sent', 'queued'))
            reader.cached = None
            after = self.backend.read_conversation()
            self.assertEqual(sum(item['role'] == 'user' and item['text'] == text for item in after['messages']), 1)
            self.assertFalse(after['composer']['hasDraft'])
            self.backend._key(0x78, True)
            self.backend._key(0x78, False)
            deadline = time.monotonic() + 3
            while reader._call(operation='observe', hwnd=self.hwnd, fresh=True).get('selectedTitle') != '模型选择历史':
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            with self.assertRaises(TargetError) as stale:
                self.backend.action({'kind': 'message', 'text': '旧对话', 'submit': True, 'epoch': before['epoch'],
                                     'conversationKey': before['key']})
            self.assertEqual(stale.exception.code, 'conversation_changed')
            self.assertFalse(reader.draft(self.hwnd)['hasDraft'])
        finally:
            self.backend._key_chord(0x10,0x78)
            reader.cached = None

    def test_native_remove_one_preview_keeps_the_other_attachment(self):
        reader = self.backend._reader()
        self.backend._focus(self.hwnd)
        self.backend._key(0x75, True)
        self.backend._key(0x75, False)
        reader.cached = None
        snapshot = self.backend.read_conversation()
        identity = reader.cached[3]
        deadline = time.monotonic() + 3
        while True:
            before = reader._call(operation='attachments', hwnd=self.hwnd, identity=identity, fresh=True)
            items = before.get('attachments', {}).get('items', [])
            if len(items) == 2:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.05)
        target = next(item for item in items if item['name'] == 'sample-a.jpg')
        self.backend.remove_file(snapshot['key'], snapshot['epoch'], target)
        after = reader._call(operation='attachments', hwnd=self.hwnd, identity=identity, fresh=True)
        self.assertEqual([item['name'] for item in after['attachments']['items']], ['sample-b.jpg'])
        self.backend.remove_file(snapshot['key'], snapshot['epoch'], after['attachments']['items'][0])

    def test_native_x_model_listbox_changes_selection_and_preserves_draft(self):
        reader=self.backend._reader()
        root=self.backend.composer_options()
        self.assertFalse(any(item['title']=='模型选择历史' for item in root['items']))
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

    def test_native_zz_questionnaire_reads_pending_chat_and_submits_one_step_at_a_time(self):
        reader = self.backend._reader()

        def observe():
            return reader._call(operation='observe', hwnd=self.hwnd, fresh=True)

        def invoke(snapshot, label):
            target = next(item for item in snapshot['items'] if item['title'] == label)
            return self.backend.action({'kind': 'composer', 'operation': 'invoke', 'optionsKey': snapshot['key'],
                                        'targetId': target['id'], 'epoch': snapshot['epoch']})['options']

        invoke(self.backend.composer_options(), '工具问卷测试入口')
        before = observe()
        self.assertNotIn('prompt', before)
        [pending] = before['pendingRequests']
        self.assertEqual((pending['title'], pending['pending'], pending['selected']), ('Fixture pending', 'input', False))
        nav = self.backend.navigation()
        entry = next(item for item in nav['items'] if item['id'] == pending['id'])
        self.assertEqual(entry['pending'], 'input')
        self.backend.action({'kind': 'navigate', 'navigationKey': nav['key'],
                             'targetId': entry['id'], 'epoch': nav['epoch']})
        question = self.backend.composer_options()
        self.assertTrue(question['prompt']['questionnaire'])
        self.assertEqual(question['prompt']['title'], '请回答')
        self.assertIn('接下来选哪个方案？', question['prompt']['text'])
        active = observe()
        self.assertTrue(active['prompt']['prompt']['questionnaire'])
        self.assertTrue(active['pendingRequests'][0]['selected'])
        self.assertFalse(next(item for item in question['items'] if item['title'] == '下一步')['enabled'])
        question = invoke(question, '保留原方案')
        self.assertEqual([item['title'] for item in question['items'] if item['selected']], ['保留原方案'])
        question = invoke(question, '采用新方案')
        self.assertEqual([item['title'] for item in question['items'] if item['selected']], ['采用新方案'])
        question = invoke(question, '保留原方案')
        question = invoke(question, '采用新方案')
        question = invoke(question, '下一步')
        self.assertIn('要附上哪些内容？', question['prompt']['text'])
        question = invoke(question, '验证结果')
        question = invoke(question, '修改记录')
        self.assertEqual([item['title'] for item in question['items'] if item['selected']], ['验证结果', '修改记录'])
        question = invoke(question, '验证结果')
        self.assertEqual([item['title'] for item in question['items'] if item['selected']], ['修改记录'])
        self.assertNotIn('prompt', invoke(question, '提交'))
        after = observe()
        self.assertNotIn('prompt', after)
        self.assertEqual(after['pendingRequests'], [])

    def test_native_zzz_window_pointer_posts_left_and_right_to_the_chosen_window(self):
        from unittest.mock import patch
        self.backend._focus(self.hwnd)
        self.backend._key(0x76, True)
        self.backend._key(0x76, False)
        def window_title():
            return next(row['title'] for row in self.backend._enumerate() if row['id'] == self.hwnd)

        deadline = time.monotonic() + 3
        while window_title() != self.title + ' Pointer ready':
            self.assertLess(time.monotonic(), deadline, 'The browser did not open the pointer fixture')
            time.sleep(.06)
        x, y, width, height = self.backend._rect()
        command = {'kind': 'window-pointer', 'epoch': self.backend.tracker.epoch, 'x': .5, 'y': .5,
                   'pointer_target': (self.hwnd, (x, y, width, height))}
        with patch('windows_control._APP_EXES', {'msedge.exe', 'chrome.exe'}):
            with self.assertRaisesRegex(TargetError, '窗口位置'):
                self.backend.action({**command, 'operation': 'left', 'pointer_target': (self.hwnd, (x+10, y, width, height))})
            for operation in ('left', 'right'):
                self.backend.action({**command, 'operation': operation})
                deadline = time.monotonic()+3
                while time.monotonic()<deadline:
                    if window_title() == self.title + ' ' + operation.capitalize():
                        break
                    time.sleep(.06)
                else:
                    self.fail(f'Native {operation} click was not observed')

    def test_native_z_attachment_and_placeholder_with_enabled_queue_button(self):
        from PIL import Image
        reader=self.backend._reader();reader.cached=None
        before=self.backend.read_conversation()
        self.assertEqual(before['title'],'Fixture conversation')
        path=self.work/'手机附件.jpg'
        Image.new('RGB',(64,64),'#4b6584').save(path)
        attached=self.backend.attach_files([path],before['key'],before['epoch'])
        self.assertEqual(attached['state'],'attached')
        draft=reader.draft(self.hwnd)
        self.assertFalse(draft['hasDraft']);self.assertEqual(draft['text'],'')
        message={'kind':'message','text':'使用 ChatGPT Work','submitMethod':'button',
                 'epoch':before['epoch'],'conversationKey':before['key']}
        self.backend.action({**message,'submit':False})
        self.assertEqual(reader.draft(self.hwnd)['text'],message['text'])
        with self.assertRaisesRegex(TargetError,'草稿'):
            self.backend.action({**message,'text':'另一条消息','submit':True})
        self.assertEqual(reader.draft(self.hwnd)['text'],message['text'])
        result=self.backend.action({**message,'submit':True,'attachmentsOnly':True})
        self.assertEqual(result['submission']['state'],'queued')
        reader.cached=None;after=self.backend.read_conversation()
        self.assertEqual(sum(m['role']=='user' and m['text']=='使用 ChatGPT Work' for m in after['messages']),1)
        self.assertFalse(after['composer']['hasDraft'])
        (self.work/'attachment-result.json').write_text(json.dumps({'attached':True,'placeholderIsEmpty':True,
            'differentDraftPreserved':True,'literalPromptSentOnce':True}),encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
