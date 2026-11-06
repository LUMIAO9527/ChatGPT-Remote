import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\r\n/g,'\n');
const end = '  boot();\n})();';
assert.ok(script.includes(end));
// Focused regression checks for the redesigned mobile page. This is a VM/DOM stub,
// not a real browser, device, accessibility audit, or external-network benchmark.
// The DOM test scaffold follows test_web.mjs so the same production functions run.
const source = script.replace(end, `
  globalThis.pageTest = {state,applyWindowState,hasCapability,renderCapabilities,renderControls,
    actionGate,sendAction,queueScroll,toggleDesktop,bindScreenGestures,statusSentence,renderStatusText,
    computeGate,setPanel,panelPopState,openModelPicker,renderMessageBody,resetRemote,
    setError,clearError,renderConversationState,retryConnection,restorePhonePreferences,enterRemote,stopLive,
    showConnectionHome,openRemoteView,openScreenView,renderOptions,confirmSetting,finishConfirmation};
})();`);

function makePage(fetchImpl = async () => { throw new Error('Unexpected request'); }) {
  const elements = new Map();
  const draws = [];
  const revoked = [];
  let now = 1000;
  let wallOffset = 0;
  let urlCount = 0;
  let decodeFails = false;
  const storage=new Map();
  let imageWidth = 900;
  let imageHeight = 600;
  const element = (tag = 'div') => {
    const node = {
    tagName: tag.toUpperCase(),
    textContent: '', value: '', hidden: false, disabled: false, width: 300, height: 150,
    children: [], parentNode: null, scrollHeight: 500, scrollTop: 0, clientHeight: 400,
    classList: { add() {}, remove() {}, toggle() {} },
    style: { setProperty() {}, removeProperty() {} },
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] ?? null; },
    removeAttribute(name) { delete this[name]; },
    appendChild(child) { this.insertBefore(child, null); },
    insertBefore(child, before) {
      child.remove();
      const index = before ? this.children.indexOf(before) : this.children.length;
      this.children.splice(index, 0, child);
      child.parentNode = this;
    },
    remove() {
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    },
    listeners: new Map(),
    addEventListener(name,handler) { this.listeners.set(name,handler); },
    focus() { if(!this.hidden && !this.disabled)document.activeElement=this; },
    getContext: () => ({ clearRect() {}, drawImage: (...args) => draws.push(args) })
    };
    for(const property of ['hidden','disabled']){
      let value=false;
      Object.defineProperty(node,property,{
        get(){return value;},
        set(next){value=!!next;if(value && document.activeElement===node)document.activeElement=document.body;}
      });
    }
    let content = '';
    Object.defineProperty(node, 'textContent', {
      get() { return content + this.children.map(child => child.textContent).join(''); },
      set(value) { for (const child of this.children) child.parentNode = null; this.children = []; content = value; }
    });
    return node;
  };
  const document = {
    hidden: false, body: element(), querySelectorAll: () => [], createElement: element, createElementNS: (_,tag)=>element(tag),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    }
  };
  const entries=[null];let entryIndex=0;let backCount=0;
  const history={
    get state(){return entries[entryIndex];},
    pushState(value){entries.splice(++entryIndex);entries.push(value);},
    replaceState(value){entries[entryIndex]=value;},
    back(){backCount++;if(entryIndex>0){entryIndex--;queueMicrotask(()=>context.pageTest.panelPopState({state:entries[entryIndex]}));}}
  };
  class PageImage {
    naturalWidth = imageWidth;
    naturalHeight = imageHeight;
    set src(value) {
      queueMicrotask(() => decodeFails ? this.onerror() : this.onload());
    }
  }
  const context = vm.createContext({
    document, location: { protocol: 'http:' }, window: {history}, Image: PageImage,
    performance: { now: () => now }, Date: { now: () => 1_700_000_000_000 + wallOffset },
    requestAnimationFrame: callback => callback(), setInterval, clearInterval,
    URL: class extends URL { static createObjectURL(){return 'blob:test-' + ++urlCount;} static revokeObjectURL(url){revoked.push(url);} },
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    AbortController, setTimeout, clearTimeout, TextDecoder, Uint8Array, DataView, Blob, atob, fetch: fetchImpl
  });
  vm.runInContext(source, context);
  const hooks = context.pageTest;
  Object.assign(hooks.state, { live: true, token: 'synthetic-browser-token', epoch: 7,
    displayedEpoch: 7, status: 'ready', view: 'screen', frameSeen: true, activeChatKey:'chat-a',activeChatEpoch:7 });
  return { hooks, elements, document, draws, revoked, history, storage, historyEntries:entries, backCount:()=>backCount, setNow: value => { now = value; },
    setWallOffset: value => { wallOffset = value; }, failDecode: () => { decodeFails = true; },
    setImageSize: (width, height) => { imageWidth = width; imageHeight = height; },
    setBitmapDecoder: decoder => {context.createImageBitmap=decoder;} };
}

const publicCapabilities = {scope:'chatgpt-window',desktop:false,rawInput:false,windowView:true};
const currentInfo = {ok:true,epoch:7,status:'ready',mode:'window',windows:[],capabilities:publicCapabilities};
const pause = milliseconds => new Promise(resolve=>setTimeout(resolve,milliseconds));
const style = html.match(/<style>([\s\S]*?)<\/style>/)[1].replace(/\/\*[\s\S]*?\*\//g,'');
const rule = selector => {
  const escaped=selector.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const match=style.match(new RegExp('(?:^|})\\s*'+escaped+'\\s*\\{([^}]+)\\}'));
  assert.ok(match,'CSS rule '+selector+' exists');
  return match[1];
};

test('public capability state hides desktop and raw controls and labels the screen honestly',()=>{
  const {hooks,elements}=makePage();
  elements.get('rawControls').open=true;
  hooks.applyWindowState(currentInfo);
  assert.equal(elements.get('desktopControl').hidden,true);
  assert.equal(elements.get('desktopBtn').disabled,true);
  assert.equal(elements.get('rawControls').hidden,true);
  assert.equal(elements.get('rawControls').open,false);
  assert.equal(elements.get('screenTitle').textContent,'ChatGPT 窗口');
  assert.equal(elements.get('desktopPromptBtn').hidden,true);
  assert.equal(elements.get('optionsScreenBtn').textContent,'查看电脑画面');
  assert.equal(elements.get('screenViewBtn').hidden,false);
  assert.equal(elements.get('screen').style.touchAction,'pan-x pan-y pinch-zoom');
  assert.equal(elements.get('statusText').textContent,'已连接到 ChatGPT 窗口，仅供查看。');
  hooks.state.status='rebound';
  assert.equal(hooks.statusSentence(),'已重新连接 ChatGPT 窗口，画面仅供查看。');
});

test('legacy agents without capabilities keep their existing LAN controls',()=>{
  const {hooks,elements}=makePage();
  hooks.applyWindowState({ok:true,epoch:7,status:'ready',mode:'window',windows:[]});
  assert.equal(hooks.hasCapability('rawInput'),true);
  assert.equal(elements.get('desktopControl').hidden,false);
  assert.equal(elements.get('rawControls').hidden,false);
  assert.equal(elements.get('screenTitle').textContent,'ChatGPT 窗口');
  assert.equal(elements.get('optionsScreenBtn').textContent,'在画面中操作');
});

test('stale epochs cannot restore denied controls and partial state updates preserve capabilities',()=>{
  const {hooks,elements}=makePage();
  hooks.applyWindowState(currentInfo);
  hooks.applyWindowState({epoch:6,capabilities:{rawInput:true,desktop:true}});
  assert.equal(hooks.hasCapability('rawInput'),false);
  hooks.applyWindowState({epoch:7,status:'ready'});
  assert.equal(hooks.hasCapability('desktop'),false);
  hooks.applyWindowState({epoch:7,capabilities:{...publicCapabilities,windowView:false}});
  assert.equal(elements.get('screenViewBtn').hidden,true);
  assert.equal(elements.get('optionsScreenBtn').hidden,true);
});

test('public action gate blocks raw events while allowing structured actions with conversation identity',()=>{
  const {hooks}=makePage();hooks.state.capabilities={...publicCapabilities};
  for(const kind of ['text','key','tap','right','scroll']){
    assert.equal(hooks.actionGate({kind}).ok,false,kind);
  }
  assert.equal(hooks.actionGate({kind:'composer',operation:'tap'}).ok,false);
  for(const kind of ['navigate','draft','queue'])assert.equal(hooks.actionGate({kind}).ok,true,kind);
  assert.equal(hooks.actionGate({kind:'composer',operation:'invoke'}).ok,true);
  assert.equal(hooks.actionGate({kind:'composer',operation:'dismiss'}).ok,true);
  assert.equal(hooks.actionGate({kind:'message',conversationKey:'chat-a'}).ok,true);
  assert.equal(hooks.actionGate({kind:'message',conversationKey:'chat-old'}).ok,false);
});

test('public raw actions and screen gestures generate no requests or local gesture suppression',async()=>{
  let requests=0,prevented=0;
  const {hooks,elements}=makePage(async()=>{requests++;throw new Error('Unexpected public raw request');});
  hooks.state.capabilities={...publicCapabilities};
  for(const kind of ['text','key','tap','right','scroll'])assert.equal(await hooks.sendAction({kind}),false);
  await hooks.toggleDesktop();
  hooks.queueScroll(240);
  hooks.bindScreenGestures();
  const event={pointerType:'touch',pointerId:1,isPrimary:true,clientX:20,clientY:20,deltaY:120,preventDefault(){prevented++;}};
  for(const name of ['pointerdown','pointermove','pointerup','contextmenu','wheel']){
    elements.get('screen').listeners.get(name)(event);
  }
  await pause(55);
  assert.equal(requests,0);
  assert.equal(prevented,0);
});

test('a capability downgrade cancels a raw scroll queued before the new policy arrived',async()=>{
  let requests=0;
  const {hooks}=makePage(async()=>{requests++;throw new Error('Stale scroll must not send');});
  hooks.queueScroll(240);
  hooks.state.capabilities={...publicCapabilities};
  await pause(55);
  assert.equal(requests,0);
});

test('unreadable conversation never suggests raw interaction in a public screen',()=>{
  const {hooks}=makePage();
  Object.assign(hooks.state,{capabilities:{...publicCapabilities},view:'chat',chatConnected:true,chat:{epoch:7,available:false}});
  assert.equal(hooks.computeGate().reason,'当前页面暂不可读取，请查看画面并在电脑上继续。');
});

test('model page returns focus to the model entry and resets its expanded state',async()=>{
  const {hooks,elements,document}=makePage();
  hooks.state.optionsRoot={key:'composer-root',epoch:7,menu:false,items:[]};
  hooks.state.optionsRootAt=1000;
  document.activeElement=elements.get('modelPickerBtn');
  await hooks.openModelPicker();
  assert.equal(hooks.state.panel,'options');
  assert.equal(document.activeElement,elements.get('optionsCloseBtn'));
  assert.equal(elements.get('modelPickerBtn').getAttribute('aria-expanded'),'true');
  hooks.setPanel('');await Promise.resolve();
  assert.equal(document.activeElement,elements.get('modelPickerBtn'));
  assert.equal(elements.get('modelPickerBtn').getAttribute('aria-expanded'),'false');
});

test('browser back returns focus to the observed settings opener',async()=>{
  const {hooks,elements,document,history}=makePage();
  document.activeElement=elements.get('settingsToggleBtn');
  hooks.setPanel('settings');
  assert.equal(document.activeElement,elements.get('settingsCloseBtn'));
  history.back();await Promise.resolve();
  assert.equal(hooks.state.panel,'');
  assert.equal(document.activeElement,elements.get('settingsToggleBtn'));
});

test('Chinese multiline paragraphs and code retain text and use separate whitespace rules',()=>{
  const {hooks,document}=makePage();const body=document.createElement('div');
  const prose='第一天 · 城市漫步\n上午抵达后，步行前往街区。\n下午沿河散步。';
  const code='const text = "早上";\n  console.log(text);';
  hooks.renderMessageBody(body,[{kind:'paragraph',text:prose},{kind:'code',text:code}],'answer',{});
  assert.equal(body.children[0].tagName,'P');
  assert.equal(body.children[0].textContent,prose);
  assert.equal(body.children[1].tagName,'PRE');
  assert.equal(body.children[1].textContent,code);
  assert.match(rule('.chat-message-body p,.chat-message-body li'),/white-space:pre-wrap/);
  assert.match(rule('.chat-message-body pre'),/white-space:pre;/);
  assert.match(rule('.chat-message-body pre'),/overflow-x:auto/);
});

test('reported small touch targets have explicit 44px boxes, including short viewports',()=>{
  assert.match(rule('.screen-return'),/min-height:44px/);
  assert.match(rule('.screen-return'),/min-width:44px/);
  assert.match(rule('.chat-copy'),/height:44px/);
  assert.match(rule('.chat-copy'),/width:44px/);
  assert.match(rule('.queue-actions .btn'),/min-height:44px/);
  assert.match(rule('.queue-actions .btn'),/min-width:44px/);
  assert.match(rule('.composer-model'),/min-height:44px/);
  assert.match(rule('.setting-switch'),/height:44px/);
  assert.match(style,/@media\(max-height:520px\)[\s\S]*?\.composer-model\{min-height:44px/);
});


test('failed connection replaces indefinite loading and exposes a direct retry',()=>{
  const {hooks,elements}=makePage();
  Object.assign(hooks.state,{view:'chat',chat:null,chatConnected:false,networkTrouble:true});
  hooks.setError('暂时无法连接，请检查手机网络和电脑上的 Rm。','network');
  assert.equal(elements.get('chatStatus').textContent,'等待连接恢复');
  assert.equal(elements.get('chatEmpty').textContent,'等待连接恢复');
  assert.equal(elements.get('chatFeedback').hidden,false);
  assert.equal(elements.get('chatRetryBtn').hidden,false);
  assert.equal(elements.get('chatRetryBtn').disabled,false);
  assert.equal(elements.get('chatRetryBtn').textContent,'重试连接');
  assert.doesNotMatch(elements.get('chatError').textContent,/电脑离线|TLS|证书/);
});

test('conversation disconnection keeps prior messages and phone draft visible',()=>{
  const {hooks,elements}=makePage();
  const chat={epoch:7,available:true,messages:[{id:'stable-message',text:'原来的对话'}],composer:{available:true}};
  Object.assign(hooks.state,{view:'chat',chat,chatConnected:false,networkTrouble:false});
  elements.get('message').value='未发送的手机草稿\n第二行';
  elements.get('chatMessages').textContent='原来的对话';
  hooks.setError('对话连接中断，正在重连。','conversation');
  assert.equal(elements.get('chatStatus').textContent,'等待连接恢复');
  assert.equal(elements.get('chatMessages').hidden,false);
  assert.equal(elements.get('chatMessages').textContent,'原来的对话');
  assert.equal(elements.get('chatEmpty').hidden,true);
  assert.equal(elements.get('message').value,'未发送的手机草稿\n第二行');
  assert.equal(hooks.state.chat,chat);
  hooks.state.chatConnected=true;hooks.state.gate={ok:true,reason:''};hooks.clearError();
  assert.equal(elements.get('chatRetryBtn').hidden,true);
  assert.equal(elements.get('chatFeedback').hidden,true);
});

test('manual connection retry uses existing reads once and never sends a message',async()=>{
  const paths=[];let rejectWindows;
  const {hooks,elements}=makePage((path,options)=>{
    paths.push(path);
    return new Promise((resolve,reject)=>{
      if(path==='/api/windows')rejectWindows=reject;
      else options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'})),{once:true});
    });
  });
  const chat={epoch:7,available:true,messages:[{id:'stable-message',text:'原来的对话'}],composer:{available:true}};
  Object.assign(hooks.state,{view:'chat',chat,chatConnected:false,networkTrouble:true});
  elements.get('message').value='未发送';
  hooks.setError('暂时无法连接。','network');
  const pending=hooks.retryConnection();
  hooks.retryConnection();
  assert.deepEqual(paths,['/api/conversation/stream','/api/windows']);
  assert.equal(elements.get('chatRetryBtn').disabled,true);
  assert.equal(elements.get('chatRetryBtn').textContent,'正在重试…');
  rejectWindows(new Error('Synthetic unavailable transport'));await pending;
  assert.equal(elements.get('chatRetryBtn').disabled,false);
  assert.equal(hooks.state.chat,chat);
  assert.equal(elements.get('message').value,'未发送');
  hooks.state.live=false;hooks.state.chatAbort.abort();await Promise.resolve();
});


test('paired devices default to the previous primary view while opt-out starts on the computer home',async()=>{
  for(const autoResume of [true,false]){
    const paths=[];
    const {hooks,elements,storage,history}=makePage((path,options)=>{
      paths.push(path);
      if(path==='/api/windows')return Promise.resolve({ok:true,status:200,json:async()=>currentInfo});
      return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'})),{once:true}));
    });
    storage.set('chatgpt-remote-phone-surface',JSON.stringify({autoResume,view:'chat'}));
    hooks.state.live=false;
    hooks.restorePhonePreferences();
    try{
      hooks.enterRemote();
      assert.equal(hooks.state.homeActive,!autoResume);
      assert.equal(elements.get('homeView').hidden,autoResume);
      assert.equal(elements.get('remoteView').hidden,!autoResume);
      assert.equal(history.state.harmonyRemoteSurface,autoResume?'remote':'home');
      assert.equal(paths.includes('/api/conversation/stream'),autoResume);
      assert.equal(paths.some(path=>path.startsWith('/api/frame')||path.startsWith('/api/stream')),false);
    }finally{hooks.stopLive();await Promise.resolve();}
  }
});

test('entering chat from a home startup retains a browser-back destination in this app',async()=>{
  const {hooks,elements,history}=makePage();
  hooks.state.homeActive=true;
  elements.get('pairView').hidden=true;
  elements.get('homeView').hidden=false;
  elements.get('remoteView').hidden=true;
  elements.get('message').value='仍在编辑的手机草稿';
  const messages=elements.get('chatMessages');messages.textContent='已读到的回复';messages.scrollTop=128;
  history.replaceState({harmonyRemoteSurface:'home'},'');
  hooks.openRemoteView();
  assert.equal(hooks.state.homeActive,false);
  assert.equal(history.state.harmonyRemoteSurface,'remote');
  history.back();await Promise.resolve();
  assert.equal(hooks.state.homeActive,true);
  assert.equal(elements.get('homeView').hidden,false);
  assert.equal(elements.get('message').value,'仍在编辑的手机草稿');
  assert.equal(elements.get('chatMessages'),messages);
  assert.equal(messages.scrollTop,128);
});

test('help opens before pairing and browser back restores the actual opener without a network request',async()=>{
  let requests=0;
  const {hooks,elements,document,history}=makePage(async()=>{requests++;throw new Error('Help is local');});
  hooks.state.live=false;hooks.state.token='';
  elements.get('pairView').hidden=false;
  elements.get('homeView').hidden=true;
  elements.get('remoteView').hidden=true;
  document.activeElement=elements.get('pairHelpBtn');
  hooks.setPanel('help',elements.get('pairHelpBtn'));
  assert.equal(elements.get('helpPanel').hidden,false);
  assert.equal(elements.get('pairView').inert,true);
  assert.equal(document.activeElement,elements.get('helpCloseBtn'));
  history.back();await Promise.resolve();
  assert.equal(hooks.state.panel,'');
  assert.equal(elements.get('helpPanel').hidden,true);
  assert.equal(elements.get('pairView').inert,false);
  assert.equal(elements.get('pairIntro').hidden,false);
  assert.equal(document.activeElement,elements.get('pairHelpBtn'));
  assert.equal(requests,0);
});

test('help from the paired home returns to the same device page',async()=>{
  const {hooks,elements,document,history}=makePage();
  hooks.showConnectionHome();
  hooks.setPanel('help',elements.get('homeHelpBtn'));
  assert.equal(hooks.state.homeActive,true);
  assert.equal(elements.get('homeView').inert,true);
  history.back();await Promise.resolve();
  assert.equal(hooks.state.panel,'');
  assert.equal(hooks.state.homeActive,true);
  assert.equal(elements.get('homeView').inert,false);
  assert.equal(document.activeElement,elements.get('homeHelpBtn'));
});

test('screen entry from home settings waits for panel history then preserves the chat draft',async()=>{
  const {hooks,elements,history}=makePage();
  hooks.state.live=false;hooks.state.view='chat';elements.get('pairView').hidden=true;
  hooks.showConnectionHome();
  elements.get('message').value='不要丢失的输入';
  const messages=elements.get('chatMessages');messages.scrollTop=96;
  hooks.setPanel('settings',elements.get('homeSettingsBtn'));
  hooks.openScreenView();
  assert.equal(hooks.state.remoteRequested,'screen');
  await Promise.resolve();
  assert.equal(hooks.state.remoteRequested,'');
  assert.equal(hooks.state.panel,'');
  assert.equal(hooks.state.homeActive,false);
  assert.equal(hooks.state.view,'screen');
  assert.equal(history.state.harmonyRemoteSurface,'remote');
  assert.equal(elements.get('remoteView').inert,false);
  assert.equal(elements.get('message').value,'不要丢失的输入');
  assert.equal(elements.get('chatMessages'),messages);
  assert.equal(messages.scrollTop,96);
});


test('native model submenus add Back while the shared close button stays on the right',()=>{
  const {hooks,elements,document}=makePage();
  hooks.setPanel('options',elements.get('modelPickerBtn'));
  hooks.state.optionsIntent='model';
  hooks.state.options={key:'model-menu',epoch:7,menu:true,category:'model',items:[]};
  hooks.state.optionsDepth=1;hooks.renderOptions();
  assert.equal(elements.get('optionsBackBtn').hidden,true);
  assert.equal(elements.get('optionsCloseBtn').hidden,false);
  hooks.state.optionsDepth=2;hooks.renderOptions();
  assert.equal(elements.get('optionsBackBtn').hidden,false);
  assert.equal(elements.get('optionsCloseBtn').hidden,false);
  elements.get('optionsBackBtn').focus();
  hooks.state.options={key:'composer-root',epoch:7,menu:false,items:[]};
  hooks.state.optionsDepth=0;
  hooks.renderOptions();
  assert.equal(elements.get('optionsCloseBtn').hidden,false);
  assert.equal(elements.get('optionsBackBtn').hidden,true);
  assert.equal(document.activeElement,elements.get('optionsCloseBtn'));
});

test('a settings confirmation reuses the current sheet history and acts only when accepted',()=>{
  const {hooks,elements,historyEntries}=makePage();
  let actions=0;
  hooks.setPanel('settings');const opener=elements.get('unpairBtn');
  function open(){hooks.confirmSetting('解除配对','需要重新配对。','解除配对',opener,()=>actions++);}
  open();
  assert.equal(hooks.state.panel,'confirm');
  assert.equal(historyEntries.length,2);
  hooks.finishConfirmation(false);
  assert.equal(hooks.state.panel,'settings');
  assert.equal(actions,0);
  open();hooks.finishConfirmation(true);
  assert.equal(actions,1);
});

test('reopening options clears a stale hidden Close and disabled Back before focusing',()=>{
  const {hooks,elements,document}=makePage();
  elements.get('optionsCloseBtn').hidden=true;
  elements.get('optionsBackBtn').hidden=false;
  elements.get('optionsBackBtn').disabled=true;
  hooks.setPanel('options',elements.get('composerOptionsBtn'));
  assert.equal(document.activeElement,elements.get('optionsCloseBtn'));
  assert.equal(document.activeElement.hidden,false);
  hooks.state.options={key:'composer-root',epoch:7,menu:false,items:[]};
  hooks.renderOptions();
  assert.equal(document.activeElement,elements.get('optionsCloseBtn'));
  assert.equal(document.activeElement.hidden,false);
});
