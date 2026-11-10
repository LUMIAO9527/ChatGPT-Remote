"""Exercise the shipping Windows UIA picker against a browser we own."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'WindowsAgent'))
from model_catalog import Account, ModelCatalog
from windows_control import TargetError, WindowsBackend, VK_MENU


HTML = r'''<!doctype html><meta charset="utf-8"><title>FIXTURE_TITLE</title>
<style>body{font:16px system-ui;margin:18px}button{padding:9px}
#prompt-textarea{border:1px solid #999;min-height:44px}.sr-only{position:absolute;left:-9999px}</style>
<aside><button class="app-action-sidebar-thread-selected bg-primary-ghost-hover">Fixture discussion</button></aside>
<main class="thread-scroll-container"><div class="relative shrink-0"><h5 class="sr-only">ChatGPT 说：</h5><p>Fixture reply</p></div></main>
<div class="fixture-composer flex w-full flex-col gap-2 relative" role="group">
  <div id="prompt-textarea" class="ProseMirror" role="textbox" aria-label="使用 ChatGPT Work" aria-multiline="true" contenteditable="true"><p>Keep this fixture draft</p></div>
  <button id="model" aria-haspopup="menu" aria-expanded="false">GPT Fixture Sol Max</button>
  <button aria-label="发送" onclick="throw Error('Unintended message')">发送</button>
</div>
<div id="picker" role="menu" class="Menu-fixture" aria-label="模型" hidden></div>
<div id="approval" role="alertdialog" class="approval-card" aria-label="Ultra 测试确认" hidden>
  <p>需要确认测试档位</p><button>允许一次</button><button>拒绝</button>
</div>
<script>
const el=id=>document.getElementById(id), names={sol:'GPT Fixture Sol',luna:'GPT Fixture Luna'};
const labels={low:'轻度',medium:'中',high:'高',xhigh:'极高',max:'Max',ultra:'Ultra'};
const announcement={medium:'标准',high:'深度'};
let model='sol', committed='max', preview=committed, quick=false;
function tiers(){return Object.keys(labels).slice(0,model==='sol'?6:5);}
function button(parent,text,role,action,className='',checked=null){
  const item=document.createElement('button');item.textContent=text;item.setAttribute('role',role);
  item.className=className;item.onclick=action;if(checked!==null)item.setAttribute('aria-checked',String(checked));
  parent.appendChild(item);return item;
}
function close(){el('picker').hidden=true;el('model').setAttribute('aria-expanded','false');}
function renderTrigger(){el('model').textContent=names[model]+' '+labels[committed];}
function simple(){
  const root=el('picker');root.replaceChildren();
  const view=document.createElement('div');view.className='SimpleView-fixture';root.appendChild(view);
  button(view,'选择模型','menuitem',list,'ViewToggle-fixture');
  button(view,quick?'关闭快速模式':'启用快速模式','menuitemcheckbox',()=>{quick=!quick;simple();},'FastModeToggle-fixture',quick);
  const status=document.createElement('span');status.id='position';status.className='sr-only';
  status.textContent=names[model]+' '+(announcement[preview]||labels[preview])+'，第 '+(tiers().indexOf(preview)+1)+' 项，共 '+tiers().length+' 项。';
  view.appendChild(status);
  const level=button(view,'能力','menuitem',()=>{},'SliderKeyboardControl-fixture');
  level.setAttribute('aria-describedby','position');
  level.onkeydown=event=>{
    if(event.key==='Enter'){event.preventDefault();committed=preview;renderTrigger();close();return;}
    if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
    event.preventDefault();const all=tiers();
    const next=all[Math.max(0,Math.min(all.length-1,all.indexOf(preview)+(event.key==='ArrowRight'?1:-1)))];
    if(next==='ultra'){close();el('approval').hidden=false;return;}
    preview=next;setTimeout(simple,40);
  };
}
function list(){
  const root=el('picker');root.replaceChildren();
  button(root,'默认 推荐模型集','menuitemradio',()=>{},'',false);
  for(const id of Object.keys(names))button(root,names[id],'menuitemradio',()=>{
    simple();setTimeout(()=>{model=id;if(!tiers().includes(committed))committed='max';preview=committed;renderTrigger();simple();},210);
  },'',model===id);
}
el('model').onclick=()=>{if(!el('picker').hidden){close();return;}el('model').setAttribute('aria-expanded','true');el('picker').hidden=false;preview=committed;simple();};
document.addEventListener('keydown',event=>{if(event.key==='Escape'){preview=committed;close();}});
</script>'''


@unittest.skipUnless(os.name == 'nt' and os.environ.get('RUN_MODEL_PICKER_E2E') == '1',
                     'set RUN_MODEL_PICKER_E2E=1 for isolated model picker')
class NativeModelPickerTests(unittest.TestCase):
    def test_catalog_and_model_effort_apply_in_one_owned_window(self):
        work = Path(os.environ['RM_MODEL_PICKER_TEST_WORK']).resolve()
        self.assertTrue(work.is_relative_to((ROOT / '_wip').resolve()))
        work.mkdir(parents=True, exist_ok=True)
        title = f'Rm isolated model fixture {os.getpid()}'
        page = work / 'native-picker.html'
        page.write_text(HTML.replace('FIXTURE_TITLE', title), encoding='utf-8')
        edge = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        process = subprocess.Popen([str(edge), '--user-data-dir=' + str(work / ('profile-' + str(os.getpid()))),
            '--disk-cache-dir=' + str(work / 'cache'), '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--disable-background-networking', '--disable-sync',
            '--force-renderer-accessibility=complete', '--enable-features=UiaProvider',
            '--app=' + page.as_uri(), '--window-size=730,550'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, 'TEMP': str(work), 'TMP': str(work)})
        def close_fixture():
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(close_fixture)
        backend = WindowsBackend([title])
        self.addCleanup(backend.close)
        resolve, activate, press_key = backend._resolve_target, backend._focus, backend._key
        def owned(hwnd):
            pid = wintypes.DWORD()
            backend.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            self.assertEqual(pid.value, process.pid)
            return hwnd
        def focus(hwnd):
            owned(hwnd)
            activate(hwnd)
        def guarded_key(key, down):
            if key == VK_MENU:
                return  # Never use the generic foreground-release keystroke in this test.
            owned(int(backend.u32.GetForegroundWindow() or 0))
            press_key(key, down)
        backend._resolve_target = lambda: owned(resolve())
        backend._focus = focus
        backend._key = guarded_key
        for _ in range(80):
            windows = backend._enumerate()
            if len(windows) == 1:
                hwnd = owned(windows[0]['id'])
                backend.select(hwnd)
                break
            time.sleep(.1)
        else:
            self.fail('The isolated browser did not appear')

        effort = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
        account = Account('fixture', 'fixture-owner', 'model', {'sol':'GPT Fixture Sol', 'luna':'GPT Fixture Luna'},
                          '0.1.0', 'fixture-account', 'fixture-token')
        def fetch(_account, kind):
            if kind == 'settings':
                return {'settings': {'model_picker_reasoning_effort_visibility': {'enabled': effort}}}
            return {'models': [{'slug': name, 'visibility': 'list',
                                'supported_reasoning_levels': [{'effort': e} for e in levels]}
                               for name, levels in [('sol', effort), ('luna', effort[:-1])]]}
        reader = backend._reader()
        native_call = reader._call
        trace = []
        def traced_call(**request):
            result = native_call(**request)
            trace.append({'operation':request['operation'], 'item':request.get('item'),
                          'result':result})
            (work / 'native-picker-trace.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        reader._call = traced_call
        catalog = ModelCatalog(work, fetch=fetch)
        catalog.account = lambda: account
        reader.model_catalog = catalog
        raw = reader.composer_options(hwnd)
        before = reader.draft(hwnd)['text']
        scope, epoch = raw['scope'], backend.tracker.epoch
        def read_catalog():
            return backend.action({'kind':'composer', 'operation':'catalog', 'conversationKey':scope, 'epoch':epoch})['catalog']
        def apply(picker, **choice):
            return backend.action({'kind':'composer', 'operation':'apply-model', 'epoch':epoch,
                                   'optionsKey':picker['selection']['key'], **choice})
        initial = read_catalog()
        self.assertEqual(initial['selection']['current']['effort'], 'max')
        self.assertEqual(len(initial['selection']['profiles']['sol']), 6)
        self.assertEqual(len(initial['selection']['profiles']['luna']), 5)
        self.assertEqual({item.get('modelKey') for item in initial['models']['items'] if item['kind']=='option'}, {'sol', 'luna'})
        reader.last_model_power = None  # Simulate restarting Remote with the model list already open.
        reread = read_catalog()
        self.assertEqual(reread['selection']['current']['effort'], 'max')
        with self.assertRaisesRegex(TargetError, '没有所选思考档位'):
            apply(reread, model='luna', effort='ultra')
        self.assertEqual(read_catalog()['selection']['current']['model'], 'sol')
        started = time.monotonic()
        changed = apply(initial, model='luna', effort='high', quick=True)
        self.assertEqual(changed['modelApply'], {'state':'applied', 'model':'luna', 'effort':'high'})
        self.assertTrue(next(i for i in changed['catalog']['power']['items'] if i['kind']=='quick')['selected'])
        self.assertEqual(reader.draft(hwnd)['text'], before)
        with self.assertRaisesRegex(TargetError, '过期'):
            apply(initial, effort='low')
        elapsed = round(time.monotonic() - started, 2)
        model_only = apply(changed['catalog'], model='sol')
        self.assertEqual(model_only['modelApply']['model'], 'sol')
        modal = apply(model_only['catalog'], effort='ultra')
        self.assertEqual(modal['modelApply']['state'], 'prompt')
        self.assertTrue(modal['options']['prompt']['approval'])
        self.assertEqual(reader.draft(hwnd)['text'], before)
        (work / 'native-picker-result.json').write_text(json.dumps({'combinedApplySeconds': elapsed,
            'reopenPreservedTiers': True, 'unsupportedTierRejected': True, 'modelOnlyConfirmed': True,
            'nativeRemountReadback': True, 'approvalLeftPending': True, 'draftPreserved': True}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
