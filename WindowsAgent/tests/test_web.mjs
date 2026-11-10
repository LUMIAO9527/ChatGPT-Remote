import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\r\n/g,'\n');
const end = '  boot();\n})();';
assert.ok(script.includes(end));
// Exercise the page's own functions without booting a live browser session.
const source = script.replace(end, `
  globalThis.pageTest = { state, request, requestJson, refreshWindows, displayFrame, renderStats, FrameParser, acknowledgeFrame,
    sendAction, sendText, queueScroll, applyConversationEvent, computeGate, controlRequest, stopControl, recordActionFrame,
    renderNavigation, refreshNavigation, setNavigation, navigateItem, acceptNavigation, openNewChat, applyWindowState, openDraft, clearDraftConflict, resolveDraft, renderDraft, mergeDraftText, renderControls,
    setPanel, panelPopState, openFilePicker, openModelPicker, applyModelChoice, refreshOptions, invokeOption, dismissOptions, openOptionSurface, showPairView,
    renderQueued, renderMessageBody, startMedia, uploadPhoneFiles, openAttachmentPreview, releasePhonePreview, beginPhoneSend, observePhoneSend, awaitPhoneSend, finishPhoneSend, resumePhoneSend,
    showConnectionHome, openRemoteView, returnToComputer, showPairEntry, restorePhonePreferences,
    startControl, stopLive, showOffline, resumeConnection, openDesktopPrompt,
    openScreenView, bindScreenGestures, stageModelChoice, applyModelDraft, confirmModelChoices, modelChoiceConfirmed, openPendingRequests, positionFloatingSurfaces, renderOptions, openMessageResource, resetRemote,
    openQueue, refreshQueue, renderQueue, invokeQueue, invokeComposerAction, renderPhoneFiles, removePhoneFile, savePhoneDraft, switchPhoneDraft, toast };
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
    children: [], dataset: {}, parentNode: null, scrollHeight: 500, scrollTop: 0, clientHeight: 400,
    classList: { add() {}, remove() {}, toggle() {} },
      style: { props:{}, setProperty(key,value){this.props[key]=value;}, removeProperty(key){delete this.props[key];} },
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
    listeners: {},
    addEventListener(name, handler) { (this.listeners[name]??=[]).push(handler); },
    fire(name,event) { (this.listeners[name]||[]).forEach(handler=>handler(event)); },
    focus() {},
    getContext: () => ({ clearRect() {}, drawImage: (...args) => draws.push(args) })
    };
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
    document, location: { protocol: 'http:' }, window: {history,innerHeight:780}, Image: PageImage,
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
  return { hooks, elements, draws, revoked, history, storage, historyEntries:entries, backCount:()=>backCount, setNow: value => { now = value; },
    setWallOffset: value => { wallOffset = value; }, failDecode: () => { decodeFails = true; },
    setImageSize: (width, height) => { imageWidth = width; imageHeight = height; },
    setBitmapDecoder: decoder => {context.createImageBitmap=decoder;},
    setControlEnvironment: controls => {Object.assign(context,controls);} };
}

function frame(id) {
  return { frameId: id, epoch: 7, capturedMs: 1_699_999_960_000, method: 'mock' };
}

function response(data) {
  return { ok: true, status: 200, json: async () => data };
}

const info = { ok: true, epoch: 7, status: 'ready', mode: 'window', windows: [] };

test('computer home uses observed metadata and pauses both heavy readers without clearing work',()=>{
  let requests=0,aborts=0;
  const page=makePage(async()=>{requests++;throw new Error('Home must not load chat or frames');});
  const state=page.hooks.state;
  const files=[{name:'phone.png',id:'attachment-a'}],queued=[{key:'chat-a',text:'下一条',at:1000}];
  Object.assign(state,{computerName:'Example-PC',windows:[{id:1,title:'ChatGPT · App Lab',selected:true}],
    phoneFiles:files,localQueued:queued,mediaStarted:true,chatAbort:{abort(){aborts++;}},frameAbort:{abort(){aborts++;}}});
  page.elements.get('message').value='正在编辑的消息';
  page.hooks.showConnectionHome();page.hooks.startMedia();
  assert.equal(requests,0);assert.ok(aborts>=2);
  assert.equal(page.elements.get('homeComputerName').textContent,'Example-PC');
  assert.equal(page.elements.get('homeWindowName').textContent,'ChatGPT · App Lab');
  assert.equal(page.elements.get('homeStatus').textContent,'已连接');
  assert.equal(page.elements.get('remoteView').hidden,true);
  assert.equal(page.elements.get('homeView').hidden,false);
  assert.equal(page.elements.get('message').value,'正在编辑的消息');
  assert.equal(state.phoneFiles,files);assert.equal(state.localQueued.length,1);assert.equal(state.localQueued[0],queued[0]);
});

test('settings opened from the computer home return there and keep the phone input',async()=>{
  const page=makePage();page.elements.get('message').value='保留输入';
  page.hooks.showConnectionHome();page.hooks.setPanel('settings');
  assert.equal(page.elements.get('remoteView').inert,true);
  page.history.back();await Promise.resolve();
  assert.equal(page.hooks.state.panel,'');assert.equal(page.hooks.state.homeActive,true);
  assert.equal(page.elements.get('homeView').inert,false);
  assert.equal(page.elements.get('remoteView').hidden,true);
  assert.equal(page.elements.get('message').value,'保留输入');
});

test('returning from chat settings opens the computer home after the panel history closes',async()=>{
  const page=makePage();page.hooks.showConnectionHome();page.hooks.openRemoteView();await Promise.resolve();
  page.hooks.state.view='screen';
  page.elements.get('message').value='不要重置';page.hooks.setPanel('settings');
  page.hooks.returnToComputer();await Promise.resolve();
  assert.equal(page.hooks.state.panel,'');assert.equal(page.hooks.state.homeActive,true);
  assert.equal(page.elements.get('remoteView').hidden,true);
  assert.equal(page.elements.get('message').value,'不要重置');
});

test('page restoration opens a primary view and disabling restoration uses conversation',()=>{
  const page=makePage();page.hooks.showConnectionHome();page.hooks.openRemoteView();
  page.hooks.state.view='screen';
  page.hooks.state.autoResume=false;page.hooks.showConnectionHome();
  page.hooks.state.autoResume=true;page.hooks.state.lastSurface='remote';page.hooks.restorePhonePreferences();
  assert.equal(page.hooks.state.autoResume,false);assert.equal(page.hooks.state.lastSurface,'remote');
  assert.equal(page.hooks.state.view,'chat');
  page.storage.set('chatgpt-remote-phone-surface','invalid');page.hooks.restorePhonePreferences();
  assert.equal(page.hooks.state.autoResume,true);assert.equal(page.hooks.state.lastSurface,'remote');
});

test('settings reopen with collapsed choices while keeping the selected send preference',()=>{
  const page=makePage();page.hooks.state.settingChoice='send';page.hooks.state.submitMethod='CTRL+ENTER';
  page.hooks.setPanel('settings');assert.equal(page.hooks.state.settingChoice,'');
  assert.equal(page.hooks.state.submitMethod,'CTRL+ENTER');
});

test('pair code entry has a browser-back route to the connection landing page',()=>{
  const page=makePage();page.hooks.state.live=false;
  page.elements.get('pairView').hidden=false;page.hooks.showPairEntry(true,true);
  assert.equal(page.elements.get('pairEntry').hidden,false);
  page.hooks.panelPopState({state:null});
  assert.equal(page.elements.get('pairEntry').hidden,true);
  assert.equal(page.elements.get('pairIntro').hidden,false);
});

test('round-trip duration and freshness do not include a phone/PC clock difference', async () => {
  const page = makePage(async () => {
    page.setNow(1200);
    return response(info);
  });
  await page.hooks.requestJson('/api/windows');
  page.setNow(1200);
  await page.hooks.displayFrame({}, frame(1), { aborted: false }, 1000);
  assert.equal(page.hooks.state.latency, 200);
  assert.equal(page.elements.get('immersiveLatency').textContent, '往返 200 ms');
  page.setWallOffset(40_000);
  page.hooks.renderStats();
  assert.equal(page.elements.get('immersiveLatency').textContent, '往返 200 ms');
  page.setWallOffset(-40_000);
  page.hooks.renderStats();
  assert.equal(page.elements.get('immersiveLatency').textContent, '往返 200 ms');
});

test('successive frames paint on the same surface and release decoded blobs', async () => {
  const page = makePage();
  const screen = page.elements.get('screen');
  await page.hooks.displayFrame({}, frame(1), { aborted: false }, 900);
  await page.hooks.displayFrame({}, frame(2), { aborted: false }, 950);
  assert.equal(page.elements.get('screen'), screen);
  assert.equal(page.draws.length, 2);
  assert.equal(screen.width, 900);
  assert.equal(screen.height, 600);
  assert.equal(page.revoked.length, 2);
  assert.equal(screen.src, undefined);
});

test('a failed decode keeps the previous picture and releases its blob', async () => {
  const page = makePage();
  await page.hooks.displayFrame({}, frame(1), { aborted: false }, 900);
  page.failDecode();
  await assert.rejects(page.hooks.displayFrame({}, frame(2), { aborted: false }, 950), /解码失败/);
  assert.equal(page.draws.length, 1);
  assert.equal(page.hooks.state.frameSeen, true);
  assert.equal(page.revoked.length, 2);
});

test('a changed region paints at its coordinates without resizing the full canvas', async () => {
  const page = makePage();
  await page.hooks.displayFrame({}, frame(1), { aborted: false });
  page.setImageSize(100, 50);
  await page.hooks.displayFrame({}, { ...frame(2), kind: 'patch', width: 900, height: 600, x: 20, y: 30 }, { aborted: false });
  assert.equal(page.elements.get('screen').width, 900);
  assert.equal(page.elements.get('screen').height, 600);
  assert.equal(page.draws[1][1], 20);
  assert.equal(page.draws[1][2], 30);
});

test('concurrent window checks share the pending refresh', async () => {
  let finish,requests=0;
  const page = makePage(() => new Promise(resolve => {requests++;finish = resolve;}));
  const pending = page.hooks.refreshWindows();
  assert.equal(page.hooks.state.pending.refresh, true);
  await page.hooks.refreshWindows();
  assert.equal(requests,1);
  finish(response(info));
  await pending;
  assert.equal(page.hooks.state.pending.refresh, false);
});

test('request timeout releases a stalled refresh', async () => {
  const page = makePage((path, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  }));
  await assert.rejects(page.hooks.request('/api/windows', { timeoutMs: 15 }), /超时/);
});

test('frame timeout also covers a stalled response body', async () => {
  const page = makePage(async (path, { signal }) => ({
    ok: true, status: 200, headers: new Map(),
    blob: () => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    })
  }));
  await assert.rejects(page.hooks.request('/api/frame', { frame: true, timeoutMs: 15 }), /超时/);
});

test('session cancellation remains an abort and cleans up the request', async () => {
  const page = makePage((path, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  }));
  const controller = new AbortController();
  const pending = page.hooks.request('/api/frame', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
});

test('FPS counts intervals between frames', () => {
  const page = makePage();
  page.setNow(2500);
  Object.assign(page.hooks.state, { frameSeen: true, lastFrameAt: 2500, frameTimes: [1000, 1500, 2000, 2500] });
  page.hooks.renderStats();
  assert.equal(page.elements.get('immersiveFps').textContent, 'FPS 2.0');
});

test('the stream parser handles fragmented and coalesced packets', () => {
  const page = makePage();
  const parser = new page.hooks.FrameParser();
  const metadata = Buffer.from(JSON.stringify({ kind: 'full', frameId: 1 }));
  const prefix = Buffer.alloc(8);
  prefix.writeUInt32BE(metadata.length, 0);
  prefix.writeUInt32BE(3, 4);
  const packet = Buffer.concat([prefix, metadata, Buffer.from([1, 2, 3])]);
  assert.equal(parser.push(packet.subarray(0, 5)).length, 0);
  const packets = parser.push(Buffer.concat([packet.subarray(5), packet]));
  assert.equal(packets.length, 2);
  assert.equal(packets[0].meta.frameId, 1);
  assert.deepEqual(Array.from(packets[0].bytes), [1, 2, 3]);
  assert.equal(parser.buffer.length, 0);
});

test('the stream parser rejects oversized frame declarations', () => {
  const page = makePage();
  const prefix = Buffer.alloc(8);
  prefix.writeUInt32BE(8193, 0);
  assert.throws(() => new page.hooks.FrameParser().push(prefix), /格式异常/);
});

test('frame acknowledgments overlap without waiting for a preceding response', async () => {
  const sent = [];
  const finish = [];
  const page = makePage((path, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise(resolve => { finish.push(() => resolve({ ok: true, status: 204 })); });
  });
  page.hooks.acknowledgeFrame({ streamId: 'synthetic-stream', frameId: 1 });
  page.hooks.acknowledgeFrame({ streamId: 'synthetic-stream', frameId: 2 });
  page.hooks.acknowledgeFrame({ streamId: 'synthetic-stream', frameId: 3 });
  assert.equal(sent.length, 2);
  assert.equal(sent[1].frameId, 2);
  // Reply to the second request first; an older response may still be travelling.
  finish.pop()();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 3);
  assert.equal(sent[2].frameId, 3);
  finish.pop()();
  finish.shift()();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.hooks.state.streamAck.pending, 0);
});

test('a burst of frames keeps two acknowledgments in flight and coalesces the rest', async () => {
  const sent = [];
  const finish = [];
  const page = makePage((path, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise(resolve => { finish.push(() => resolve({ ok: true, status: 204 })); });
  });
  for (let frameId = 1; frameId <= 100; frameId++) {
    page.hooks.acknowledgeFrame({ streamId: 'synthetic-stream', frameId });
  }
  assert.equal(sent.length, 2);
  assert.equal(page.hooks.state.streamAck.pending, 2);
  finish.shift()();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 3);
  assert.equal(sent[2].frameId, 100);
  assert.equal(page.hooks.state.streamAck.pending, 2);
  finish.splice(0).forEach(resolve => resolve());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.hooks.state.streamAck.pending, 0);
});

test('a stale stream completion cannot start more acknowledgments for it', async () => {
  const sent = [];
  const finish = [];
  const page = makePage((path, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise(resolve => { finish.push(() => resolve({ ok: true, status: 204 })); });
  });
  for (const frameId of [1, 2, 3]) page.hooks.acknowledgeFrame({ streamId: 'old-stream', frameId });
  page.hooks.acknowledgeFrame({ streamId: 'new-stream', frameId: 4 });
  finish.shift()();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.filter(item => item.streamId === 'old-stream').length, 2);
  assert.equal(page.hooks.state.streamAck.id, 'new-stream');
  finish.splice(0).forEach(resolve => resolve());
  await new Promise(resolve => setImmediate(resolve));
});

test('failed acknowledgments release their slot and send newer progress', async () => {
  const sent = [];
  const finish = [];
  const page = makePage((path, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise((resolve, reject) => { finish.push({ resolve, reject }); });
  });
  for (const frameId of [1, 2, 3]) page.hooks.acknowledgeFrame({ streamId: 'synthetic-stream', frameId });
  finish.shift().reject(new Error('synthetic network failure'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 3);
  assert.equal(sent[2].frameId, 3);
  assert.equal(page.hooks.state.streamAck.pending, 2);
  finish.splice(0).forEach(item => item.resolve({ ok: true, status: 204 }));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.hooks.state.streamAck.pending, 0);
});

test('eight rapid HTTP fallback actions arrive in order without being discarded', async () => {
  const sent = [];
  let inFlight = 0;
  let maximum = 0;
  const page = makePage(async (path, options) => {
    sent.push(JSON.parse(options.body).text);
    maximum = Math.max(maximum, ++inFlight);
    await new Promise(resolve => setImmediate(resolve));
    inFlight--;
    return response({ ok: true, epoch: 7 });
  });
  const replies = await Promise.all(Array.from({ length: 8 }, (_, index) => page.hooks.sendAction({ kind: 'text', text: String(index) })));
  assert.ok(replies.every(Boolean));
  assert.deepEqual(sent, ['0','1','2','3','4','5','6','7']);
  assert.equal(maximum, 1);
});

test('text and button submission are sent together once and a newly edited phone draft is retained', async () => {
  const sent = [];
  let finish;
  const page = makePage((path, options) => {
    sent.push(JSON.parse(options.body));
    return new Promise(resolve => { finish = () => resolve(response({ ok: true, epoch: 7 })); });
  });
  page.elements.get('message').value = 'first message';
  const pending = page.hooks.sendText(true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'message');
  assert.equal(sent[0].submit, true);
  assert.equal(sent[0].submitMethod, 'button');
  page.elements.get('message').value = 'next draft';
  finish();
  await pending;
  assert.equal(page.elements.get('message').value, 'next draft');
  assert.equal(sent.length, 1);
});

function chatEvent(overrides = {}) {
  return {kind:'conversation',replace:true,key:'chat-a',epoch:7,revision:'1',available:true,
    status:'ready',mode:'window',composer:{available:true,hasDraft:false},title:'Current conversation',
    order:['m0'],messages:[{id:'m0',role:'assistant',text:'<script>literal text</script>'}],...overrides};
}

test('rich text retains safe links and emphasis without interpreting message HTML',()=>{
  const page=makePage();
  const body=page.elements.get('chatMessages');
  page.hooks.renderMessageBody(body,[{kind:'paragraph',text:'重点 <img> 来源',runs:[
    {text:'重点',strong:true},{text:' <img> '},{text:'来源',href:'https://example.com/docs',emphasis:true}
  ]},{kind:'paragraph',text:'危险链接',runs:[{text:'危险链接',href:'javascript:alert(1)'}]}]);
  assert.equal(body.children[0].children[0].tagName,'STRONG');
  assert.equal(body.children[0].children[1].textContent,' <img> ');
  const link=body.children[0].children[2];
  assert.equal(link.tagName,'A');assert.equal(link.href,'https://example.com/docs');
  assert.equal(link.rel,'noopener noreferrer');assert.equal(link.children[0].tagName,'EM');
  assert.equal(body.children[1].children[0].tagName,'SPAN');
});

test('a complete activity message stays collapsed and preserves its open state on updates',()=>{
  const page=makePage();const body=page.elements.get('chatMessages');
  page.hooks.renderMessageBody(body,[{kind:'paragraph',text:'正在读取',channel:'activity'}],'activity');
  assert.equal(body.children[0].tagName,'DETAILS');assert.equal(body.children[0].open,false);
  body.children[0].open=true;
  page.hooks.renderMessageBody(body,[{kind:'paragraph',text:'已完成读取',channel:'activity'}],'activity');
  assert.equal(body.children[0].open,true);
});

test('a message containing only process blocks has no empty copy action',()=>{
  const page=makePage();page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({messages:[{id:'m0',role:'assistant',text:'检查结果',complete:true,
    blocks:[{kind:'paragraph',text:'检查结果',channel:'reasoning'}]}]}));
  assert.equal(page.hooks.state.chatElements.m0.copy.hidden,true);
});

test('conversation text is literal, changed messages keep their element, and switching clears old rows', () => {
  const page = makePage();
  page.hooks.state.view = 'chat';
  page.hooks.applyConversationEvent(chatEvent());
  const row = page.hooks.state.chatElements.m0;
  assert.equal(row.body.textContent, '<script>literal text</script>');
  assert.equal(row.body.children.length, 1);
  assert.equal(row.body.children[0].tagName, 'P');
  assert.equal(row.body.children[0].children.length, 0);
  page.hooks.applyConversationEvent(chatEvent({replace:false,revision:'2',messages:[{id:'m0',role:'assistant',text:'updated reply'}]}));
  assert.equal(page.hooks.state.chatElements.m0, row);
  assert.equal(row.body.textContent, 'updated reply');
  page.hooks.applyConversationEvent(chatEvent({key:'chat-b',revision:'3',messages:[],order:[]}));
  assert.equal(page.elements.get('chatMessages').children.length, 0);
  assert.equal(Object.keys(page.hooks.state.chatElements).length, 0);
});

test('paragraphs, list items, headings and code use safe elements and retain unchanged content', () => {
  const page = makePage();
  const message = { id:'m0',role:'assistant',text:'Plain copy source',blocks:[
    {kind:'heading',text:'小标题'}, {kind:'paragraph',text:'正文 <img onerror=alert(1)>'},
    {kind:'list',text:'第一项'}, {kind:'list',text:'完整的第二项'},
    {kind:'code',text:'const a = "<script>";\nconsole.log(a);'}] };
  page.hooks.state.view = 'chat';
  page.hooks.applyConversationEvent(chatEvent({messages:[message]}));
  const body = page.hooks.state.chatElements.m0.body;
  assert.deepEqual(body.children.map(node => node.tagName), ['H3','P','UL','PRE']);
  assert.equal(body.children[1].children.length, 0);
  assert.equal(body.children[2].children.length, 2);
  assert.equal(body.children[3].children[0].tagName, 'CODE');
  const originalParagraph = body.children[1];
  page.hooks.applyConversationEvent(chatEvent({replace:false,revision:'2',messages:[message]}));
  assert.equal(body.children[1], originalParagraph);
});

test('message timestamps have their own metadata element and never enter copied body text', () => {
  const page = makePage();
  page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({messages:[{id:'m0',role:'user',text:'正文中约定 12:30 见面',timestamp:'11:20'}]}));
  const row=page.hooks.state.chatElements.m0;
  assert.equal(row.timestamp.textContent,'11:20');
  assert.equal(row.body.textContent,'正文中约定 12:30 见面');
  assert.equal(row.text,'正文中约定 12:30 见面');
  assert.equal(row.article.children[0],row.body);
  assert.equal(row.article.children[1],row.timestamp.parentNode);
  assert.equal(row.article.children[1].children[0]['aria-label'],'复制这条消息');
  assert.equal(row.article.children[1].children[0].textContent,'');
  page.elements.get('message').value='手机尚未发送的草稿';
  page.hooks.applyConversationEvent(chatEvent({key:'other-chat'}));
  assert.equal(page.elements.get('message').value,'');
  page.hooks.applyConversationEvent(chatEvent());
  assert.equal(page.elements.get('message').value,'手机尚未发送的草稿');
});

test('active reply copy appears only after generation ends without replacing its body',()=>{
  const page=makePage();page.hooks.applyConversationEvent(chatEvent({generating:true}));
  const row=page.hooks.state.chatElements.m0,body=row.body.children[0];
  assert.equal(row.copy.hidden,true);
  page.hooks.applyConversationEvent(chatEvent({replace:false,generating:false,messages:[],revision:'2'}));
  assert.equal(row.copy.hidden,false);assert.equal(row.body.children[0],body);
});

test('an image-only message renders a small safe placeholder and has no copy action',()=>{
  const page=makePage();page.hooks.applyConversationEvent(chatEvent({messages:[{id:'m0',role:'user',text:'',attachments:[{id:'a',kind:'image'},{id:'b',kind:'image'},{id:'c',kind:'file',name:'<script>.pdf'}]}]}));
  const row=page.hooks.state.chatElements.m0;
  assert.equal(row.copy.hidden,true);assert.ok(row.body.textContent.includes('图片 · 2 张'));
  assert.ok(row.body.textContent.includes('<script>.pdf'));
  assert.equal(row.body.children[0].children[1].tagName,'SPAN');
});

test('a later upload failure preserves already uploaded files',async()=>{
  let uploads=0;
  const page=makePage(async path=>{
    assert.equal(path,'/api/uploads');
    if(++uploads===1)return response({file:{id:'ready-image'}});
    return {ok:false,status:413,json:async()=>({error:'文件过大'})};
  });
  page.hooks.applyConversationEvent(chatEvent());
  await page.hooks.uploadPhoneFiles([{name:'first.jpg',size:8},{name:'second.jpg',size:8}]);
  assert.deepEqual(Array.from(page.hooks.state.phoneFiles,row=>[row.name,row.state,row.id]),[
    ['first.jpg','uploaded','ready-image'],['second.jpg','error',undefined]
  ]);
  assert.equal(page.hooks.state.filesBusy,false);
});

test('phone file bytes upload separately, attach once, and can submit without text',async()=>{
  const requests=[];
  const file={name:'手机.txt',size:4};
  const page=makePage(async(path,options)=>{
    requests.push({path,options});
    if(path==='/api/uploads'){assert.equal(options.body,file);return response({ok:true,file:{id:'file-id',name:file.name,size:4,state:'uploaded'}});}
    if(path==='/api/uploads/attach')return response({ok:true,state:'attached'});
    if(path==='/api/action')return response({ok:true,epoch:7,submission:{state:'sent'}});
    throw new Error('Unexpected request');
  });
  await page.hooks.uploadPhoneFiles([file]);
  assert.equal(page.hooks.state.phoneFiles[0].state,'uploaded');
  assert.deepEqual(requests.map(row=>row.path),['/api/uploads']);
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(await page.hooks.sendText(true),true);
  assert.deepEqual(requests.map(row=>row.path),['/api/uploads','/api/uploads/attach','/api/action']);
  const action=JSON.parse(requests[2].options.body);assert.equal(action.text,'');assert.deepEqual(action.attachmentIds,['file-id']);
  assert.equal(page.hooks.state.phoneFiles.length,0);
});

test('a lost attachment acknowledgement is reconciled before one message send',async()=>{
  let attaches=0,statusReads=0,sends=0;
  const page=makePage(async(path)=>{
    if(path==='/api/uploads')return response({ok:true,file:{id:'f'}});
    if(path==='/api/uploads/attach'){attaches++;throw new Error('Connection lost');}
    if(path==='/api/uploads/status'){statusReads++;return response({ok:true,state:'attached'});}
    if(path==='/api/action'){sends++;return response({ok:true,epoch:7,submission:{state:'sent'}});}
    throw new Error('Unexpected request');
  });
  page.elements.get('message').value='Keep this';await page.hooks.uploadPhoneFiles([{name:'f.txt',size:3}]);
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(attaches,1);assert.equal(statusReads,1);assert.equal(sends,1);
  assert.equal(page.hooks.state.phoneFiles.length,0);assert.equal(page.elements.get('message').value,'');
});

test('pending attachment readback finishes automatically and pastes once',async()=>{
  let attaches=0,reads=0,sends=0;
  const page=makePage(async(path)=>{
    if(path==='/api/uploads')return response({ok:true,file:{id:'f'}});
    if(path==='/api/uploads/attach'){attaches++;return response({ok:true,state:'attaching'});}
    if(path==='/api/uploads/status')return response({ok:true,state:++reads===1?'attaching':'attached'});
    if(path==='/api/action'){sends++;return response({ok:true,epoch:7,submission:{state:'sent'}});}
    throw new Error('Unexpected request');
  });
  page.elements.get('message').value='发送附件';await page.hooks.uploadPhoneFiles([{name:'f.txt',size:3}]);
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(attaches,1);assert.equal(reads,2);assert.equal(sends,1);
});

test('an uploaded file binds to the current observed conversation when attachment starts',async()=>{
  const page=makePage(async(path,options)=>{
    if(path==='/api/uploads/attach'){
      const payload=JSON.parse(options.body);assert.equal(payload.conversationKey,'chat-a');assert.equal(payload.epoch,7);
      return response({ok:true,state:'attached'});
    }
    if(path==='/api/action')return response({ok:true,epoch:7,submission:{state:'sent'}});
    throw new Error('Unexpected request');
  });
  page.hooks.state.phoneFiles=[{name:'f.png',id:'f',state:'uploaded',key:'previous-render',epoch:6}];
  page.elements.get('message').value='当前对话的附件';page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(await page.hooks.sendText(true),true);
});

test('an uploaded image retains its local thumbnail and opens a preview during sending',async()=>{
  const page=makePage(async path=>{
    assert.equal(path,'/api/uploads');
    return response({ok:true,file:{id:'photo-1'}});
  });
  await page.hooks.uploadPhoneFiles([{name:'手机截图.png',type:'image/png',size:8}]);
  const item=page.hooks.state.phoneFiles[0],url=item.previewUrl;
  assert.ok(url.startsWith('blob:'));
  assert.equal(item.file,undefined);
  const chip=page.elements.get('phoneFiles').children[0];
  assert.equal(chip.getAttribute('data-kind'),'image');
  assert.equal(chip.children[0].getAttribute('aria-label'),'预览 手机截图.png');
  page.hooks.state.pending.sendText=true;
  page.hooks.openAttachmentPreview(item,chip.children[0]);
  assert.equal(page.hooks.state.panel,'attachment');
  assert.equal(page.elements.get('attachmentImage').src,url);
  assert.equal(page.elements.get('attachmentCaption').textContent,'手机截图.png');
  page.hooks.releasePhonePreview(item);
  assert.equal(page.revoked.includes(url),false);
  page.hooks.setPanel('');
  assert.equal(page.revoked.includes(url),true);
  assert.equal(page.hooks.state.previewFile,null);
  page.hooks.panelPopState({state:{harmonyRemotePanel:'attachment'}});
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.elements.get('attachmentPanel').hidden,true);
});

test('active image previews retain their safe format boundary and reset releases their URLs',async()=>{
  let count=0;
  const page=makePage(async()=>response({ok:true,file:{id:'file-'+(++count)}}));
  await page.hooks.uploadPhoneFiles([{name:'photo.jpg',type:'image/jpeg',size:8},
    {name:'diagram.svg',type:'image/svg+xml',size:8},{name:'report.pdf',type:'application/pdf',size:8},
    {name:'screenshot.jpg',type:'application/octet-stream',size:8}]);
  const files=page.hooks.state.phoneFiles,url=files[0].previewUrl;
  assert.ok(url);assert.equal(files[1].previewUrl,'');assert.equal(files[2].previewUrl,'');
  assert.ok(files[3].previewUrl);
  page.hooks.openAttachmentPreview(files[0]);
  page.hooks.showPairView('重新配对');
  assert.ok(page.revoked.includes(url));
  assert.equal(page.elements.get('attachmentPanel').hidden,true);
  assert.equal(page.hooks.state.phoneFiles.length,0);
});

test('a file remains removable and can be manually retried when paste never started',async()=>{
  let attaches=0;
  const page=makePage(async(path)=>{
    if(path==='/api/uploads')return response({ok:true,file:{id:'f'}});
    if(path==='/api/uploads/attach'){
      attaches++;
      if(attaches===1)return {ok:false,status:409,json:async()=>({error:'Focus unavailable',code:'attachment_not_started'})};
      return response({ok:true,state:'attached'});
    }
    if(path==='/api/action')return response({ok:true,epoch:7,submission:{state:'sent'}});
    throw new Error('Unexpected request');
  });
  page.elements.get('message').value='Keep this';await page.hooks.uploadPhoneFiles([{name:'f.txt',size:3}]);
  assert.equal(await page.hooks.sendText(true),false);assert.equal(attaches,1);
  assert.equal(page.hooks.state.phoneFiles[0].state,'uploaded');
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.ok(page.elements.get('phoneFiles').textContent.includes('×'));
  assert.equal(page.elements.get('message').value,'Keep this');
  assert.equal(await page.hooks.sendText(true),true);assert.equal(attaches,2);
});

test('project groups and conversation titles are rendered as safe text', () => {
  const page=makePage();
  page.hooks.state.navigation={key:'nav-a',items:[
    {id:'p1',kind:'project',title:'Project <img>',project:'group1',projectTitle:'Project <img>',expanded:true},
    {id:'t1',kind:'thread',title:'Chat <script>',project:'group1',projectTitle:'Project <img>'},
    {id:'p2',kind:'project',title:'Another',project:'group2',projectTitle:'Another',expanded:false}]};
  page.hooks.renderNavigation();
  const list=page.elements.get('navigationList');
  assert.equal(list.children.length,2);
  assert.equal(list.children[0].children.length,2);
  assert.equal(list.children[0].children[1].children[1].textContent,'Chat <script>');
  assert.equal(list.children[0].children[1].children[1].children.length,0);
  page.elements.get('navigationSearch').value='Chat <script>';
  page.hooks.renderNavigation();
  assert.equal(list.children.length,1);
});

test('conversation selection works when its composer cannot be read and old sends stay blocked', async () => {
  const sent=[];
  const page=makePage(async (path,options) => {
    if(path==='/api/action'){sent.push(JSON.parse(options.body));return response({ok:true,epoch:7});}
    return response(info);
  });
  page.hooks.state.view='chat';
  page.hooks.state.chatConnected=false;
  assert.equal(page.hooks.computeGate().ok,false);
  page.hooks.state.navigationBusy=true;
  const ok=await page.hooks.sendAction({kind:'navigate',targetId:'t1',navigationKey:'nav-a',expanded:true});
  assert.equal(ok,true);
  assert.equal(sent[0].kind,'navigate');
  assert.equal(await page.hooks.sendAction({kind:'message',text:'blocked during switch'}),false);
  assert.equal(sent.length,1);
});

test('an explicit Ctrl+Enter preference is included in the single send action', async () => {
  const sent = [];
  const page = makePage(async (_, options) => { sent.push(JSON.parse(options.body)); return response({ok:true,epoch:7}); });
  page.hooks.state.submitMethod = 'CTRL+ENTER';
  page.elements.get('message').value = 'Ctrl send';
  await page.hooks.sendText(true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].submitMethod, 'CTRL+ENTER');
  assert.equal(page.elements.get('message').value, '');
});

test('chat sends use the observed conversation key and cannot send against a stale snapshot', async () => {
  const sent = [];
  const page = makePage(async (path, options) => { sent.push(JSON.parse(options.body)); return response({ok:true,epoch:7}); });
  page.hooks.state.view = 'chat';
  page.hooks.applyConversationEvent(chatEvent());
  page.elements.get('message').value = 'hello';
  await page.hooks.sendText(true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'message');
  assert.equal(sent[0].conversationKey, 'chat-a');
  page.hooks.state.epoch = 8;
  assert.equal(page.hooks.computeGate().ok, false);
  assert.equal(await page.hooks.sendAction({kind:'message',text:'must not send'}), false);
  assert.equal(sent.length, 1);
});

test('a dropped control connection rejects unconfirmed actions without automatic retransmission', async () => {
  const page = makePage();
  const sent = [];
  page.hooks.state.control = {ready:true,pending:{},socket:{readyState:1,bufferedAmount:0,send: text => sent.push(text),close(){}}};
  const action = page.hooks.controlRequest('action',{payload:{kind:'text',text:'once'}});
  page.hooks.stopControl();
  await assert.rejects(action,/连接已断开/);
  assert.equal(sent.length, 1);
});

test('missing WebSocket handshake and heartbeat each reopen control with HTTP available between sockets',async()=>{
  const sockets=[],timers=new Map(),intervals=new Map(),paths=[];
  let sequence=0;
  const schedule=map=>(callback,ms)=>{const id=++sequence;map.set(id,{callback,ms});return id;};
  const fire=ms=>{
    const found=[...timers].find(([,timer])=>timer.ms===ms);
    assert.ok(found,'expected timer at '+ms);
    timers.delete(found[0]);found[1].callback();
  };
  class TestSocket {
    constructor(){this.readyState=0;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
    close(){this.readyState=3;this.onclose?.();}
    send(raw){this.sent.push(JSON.parse(raw));}
  }
  const page=makePage(async(path)=>{paths.push(path);return response({ok:true,epoch:7});});
  page.setControlEnvironment({WebSocket:TestSocket,setTimeout:schedule(timers),clearTimeout:id=>timers.delete(id),
    setInterval:schedule(intervals),clearInterval:id=>intervals.delete(id)});
  const {hooks}=page;
  try{
    hooks.startControl();assert.equal(sockets.length,1);
    fire(5000);assert.equal(sockets[0].readyState,3);assert.equal(hooks.state.control,null);
    await hooks.sendAction({kind:'key',key:'ENTER'});assert.deepEqual(paths,['/api/action']);
    fire(timers.get(hooks.state.controlRetry).ms);
    const live=sockets[1];live.readyState=1;live.onopen();
    live.onmessage({data:JSON.stringify({type:'auth',ok:true})});
    assert.equal(timers.size,0);
    [...intervals.values()][0].callback();
    assert.deepEqual(live.sent.map(item=>item.type),['auth','ping']);
    fire(5000);
    assert.equal(live.readyState,3);assert.equal(intervals.size,0);
    assert.equal(timers.size,1);
    assert.deepEqual(paths,['/api/action']);
  }finally{hooks.stopControl();}
});

test('entry authorization failures keep pairing while a confirmed token rejection clears it',async()=>{
  let code='access_required';
  const page=makePage(async()=>({ok:false,status:401,json:async()=>({code,error:'授权需要恢复'})}));
  const {hooks}=page,token=hooks.state.token;
  await assert.rejects(hooks.requestJson('/api/windows'),error=>error.code==='access_required');
  assert.equal(hooks.state.token,token);
  code='pairing_required';
  await assert.rejects(hooks.requestJson('/api/windows'),error=>error.code==='pairing_required');
  assert.equal(hooks.state.token,'');
  assert.equal(page.elements.get('pairEntry').hidden,false);
});

test('offline cancels the old status request and only the resumed snapshot can update the session',async()=>{
  const pending=[];
  const page=makePage((path,{signal})=>new Promise(resolve=>pending.push({path,signal,resolve})));
  const {hooks,elements}=page;
  hooks.state.homeActive=true;
  elements.get('message').value='未发草稿';
  const old=hooks.refreshWindows();
  hooks.state.offline=true;hooks.showOffline();
  assert.equal(pending[0].signal.aborted,true);
  assert.equal(hooks.state.networkTrouble,true);
  hooks.state.offline=false;hooks.resumeConnection();hooks.resumeConnection();
  assert.equal(pending.length,2);
  pending[1].resolve(response({...info,instance:'after'}));await new Promise(resolve=>setImmediate(resolve));
  pending[0].resolve(response({...info,epoch:100,instance:'before'}));await old;
  assert.equal(hooks.state.instance,'after');
  assert.equal(hooks.state.epoch,7);
  assert.equal(hooks.state.networkTrouble,false);
  assert.equal(elements.get('message').value,'未发草稿');
});

test('a delayed scroll is cancelled when its window epoch changes', async () => {
  const sent = [];
  const page = makePage(async (path, options) => { sent.push(options); return response({ok:true,epoch:7}); });
  page.hooks.queueScroll(120);
  page.hooks.state.epoch = 8;
  await new Promise(resolve => setTimeout(resolve, 55));
  assert.equal(sent.length, 0);
});

test('operation feedback measures until a frame captured after the operation', () => {
  const page = makePage();
  page.hooks.state.actionFeedback.push({sequence:4,started:1000});
  page.hooks.recordActionFrame(3,1050);
  assert.equal(page.hooks.state.responseLatency,null);
  page.hooks.recordActionFrame(4,1280);
  assert.equal(page.hooks.state.responseLatency,280);
});

function draftEvent(overrides={}) {
  return {ok:true,epoch:7,conversationKey:'chat-a',revision:'draft-v1',title:'示例对话',
    text:'电脑原稿 <img onerror=alert(1)>',hasDraft:true,canClear:true,truncated:false,...overrides};
}

test('nonempty phone input can submit when conversation reading is temporarily unavailable', async () => {
  const actions=[];
  const page=makePage(async(path,options)=>{
    actions.push(JSON.parse(options.body));return response({ok:true,epoch:7});
  });
  page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({available:false,composer:{available:false,hasDraft:false}}));
  page.elements.get('message').value='Phone text';
  page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(actions.length,1);
  assert.equal(actions[0].conversationKey,'chat-a');
});

test('a disconnected text stream does not prevent a guarded send to an observed conversation', async () => {
  const actions=[];
  const page=makePage(async(path,options)=>{
    actions.push(JSON.parse(options.body));return response({ok:true,epoch:7});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());
  page.hooks.state.chatConnected=false;page.setNow(15000);
  page.elements.get('message').value='Still sendable';
  page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(actions[0].epoch,7);
});

test('typing before the first snapshot reads the conversation before one guarded send', async () => {
  const calls=[];
  const page=makePage(async(path,options)=>{
    calls.push(path);
    if(path==='/api/conversation')return response({...chatEvent(),messages:[]});
    const action=JSON.parse(options.body);
    assert.equal(action.kind,'message');assert.equal(action.conversationKey,'chat-a');assert.equal(action.epoch,7);
    return response({ok:true,epoch:7});
  });
  page.hooks.state.view='chat';page.hooks.state.activeChatKey='';page.hooks.state.activeChatEpoch=-1;
  page.elements.get('message').value='Typed early';page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(await page.hooks.sendText(true),true);
  assert.deepEqual(calls,['/api/conversation','/api/action']);
});

test('a target change during the first snapshot keeps phone text and sends nothing', async () => {
  let actionCount=0;
  const page=makePage(async(path)=>{
    if(path==='/api/conversation')return response({...chatEvent({epoch:8,key:'new-chat'}),messages:[]});
    actionCount++;return response({ok:true,epoch:8});
  });
  page.hooks.state.view='chat';page.hooks.state.activeChatKey='';page.hooks.state.activeChatEpoch=-1;
  page.elements.get('message').value='Preserved message';
  assert.equal(await page.hooks.sendText(true),false);
  assert.equal(actionCount,0);
  assert.equal(page.elements.get('message').value,'Preserved message');
  assert.equal(page.hooks.state.pending.sendText,false);
  assert.match(page.hooks.state.error,/当前对话已变化/);
});

test('send controls stay disabled while frozen or navigating and recover afterwards', () => {
  const page=makePage();page.elements.get('message').value='Message';
  page.hooks.state.frozen.active=true;page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,true);
  page.hooks.state.frozen.active=false;page.hooks.state.navigationBusy=true;page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,true);
  page.hooks.state.navigationBusy=false;page.hooks.renderControls();
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
});

test('an empty phone retrieves the desktop text with one read and sends edited text against its revision', async () => {
  const paths=[], actions=[];
  const page=makePage(async(path,options)=>{
    paths.push(path);
    if(path==='/api/draft')return response(draftEvent());
    const command=JSON.parse(options.body);actions.push(command);
    return response({ok:true,epoch:7,delivery:{id:command.messageId,state:'sent'}});
  });
  page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({composer:{available:true,hasDraft:true}}));
  assert.equal(page.elements.get('computerDraftBtn').hidden,false);
  await page.hooks.openDraft(true);
  assert.deepEqual(paths,['/api/draft']);
  assert.equal(page.elements.get('message').value,draftEvent().text);
  assert.equal(page.elements.get('draftConflict').hidden,true);
  assert.equal(page.elements.get('computerDraftBtn').hidden,true);
  page.elements.get('message').value+='\n继续编辑';
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(actions.length,1);
  assert.equal(actions[0].draftRevision,'draft-v1');
  assert.equal(actions[0].conversationKey,'chat-a');
  assert.ok(actions[0].text.endsWith('继续编辑'));
});

test('merging a conflict retains both texts and does not clear the desktop editor', async () => {
  const paths=[];
  const page=makePage(async path=>{paths.push(path);return response(draftEvent());});
  page.elements.get('message').value='手机原稿';
  await page.hooks.openDraft(false);
  assert.equal(page.elements.get('draftPreview').textContent,draftEvent().text);
  assert.equal(page.elements.get('draftConflict').hidden,false);
  assert.equal(page.hooks.state.panel,'');
  await page.hooks.resolveDraft('recover');
  assert.deepEqual(paths,['/api/draft']);
  assert.equal(page.elements.get('message').value,'手机原稿\n\n'+draftEvent().text);
  assert.equal(page.elements.get('draftConflict').hidden,true);
  assert.equal(page.elements.get('recoveredUndoBtn').hidden,false);
});

test('replace and send is a single guarded request with the displayed draft revision', async () => {
  const actions=[];
  const page=makePage(async(path,options)=>{
    if(path==='/api/draft')return response(draftEvent());
    const command=JSON.parse(options.body);actions.push(command);
    return response({ok:true,epoch:7,delivery:{id:command.messageId,state:'sent'}});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());
  page.elements.get('message').value='Replacement';
  await page.hooks.openDraft(false);await page.hooks.resolveDraft('send');
  assert.equal(actions.length,1);
  assert.equal(actions[0].kind,'message');
  assert.equal(actions[0].text,'Replacement');
  assert.equal(actions[0].submit,true);
  assert.equal(actions[0].conversationKey,'chat-a');
  assert.equal(actions[0].draftRevision,'draft-v1');
  assert.equal(page.elements.get('message').value,'');
  assert.equal(page.elements.get('draftConflict').hidden,true);
});

test('a newer desktop draft invalidates a bound draft and preserves the phone edit', async () => {
  const paths=[];
  const page=makePage(async(path,options)=>{
    paths.push(path);
    if(path==='/api/draft')return response(paths.length===1?draftEvent():draftEvent({revision:'draft-v2',text:'电脑后来改过的草稿'}));
    return response({ok:true,delivery:{id:JSON.parse(options.body).messageId,state:'failed',code:'draft_changed',error:'电脑草稿已变化'}});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());
  await page.hooks.openDraft(true);
  page.elements.get('message').value='手机改过的草稿';
  assert.equal(await page.hooks.sendText(true),false);
  assert.deepEqual(paths,['/api/draft','/api/action','/api/draft']);
  assert.equal(page.elements.get('message').value,'手机改过的草稿');
  assert.equal(page.hooks.state.boundDraft,null);
  assert.equal(page.elements.get('draftPreview').textContent,'电脑后来改过的草稿');
});

test('stale or truncated desktop drafts offer no replacement actions', async () => {
  const paths=[];
  const page=makePage(async path=>{
    paths.push(path);return response(draftEvent({truncated:true,canClear:false}));
  });
  page.elements.get('message').value='保留手机文字';
  await page.hooks.openDraft(false);
  assert.equal(page.elements.get('draftRecoverBtn').hidden,true);
  assert.equal(page.elements.get('draftSendBtn').hidden,true);
  await page.hooks.resolveDraft('send');
  page.hooks.state.epoch=8;page.hooks.renderDraft();
  assert.equal(page.elements.get('draftConflict').hidden,true);
  assert.deepEqual(paths,['/api/draft']);
  assert.equal(page.elements.get('message').value,'保留手机文字');
});

test('a conflicting desktop draft shows only an inline preview and preserves the phone message', async () => {
  const paths=[];
  const page=makePage(async(path,request)=>{
    paths.push(path);
    if(path==='/api/action')return response({ok:true,delivery:{id:JSON.parse(request.body).messageId,state:'failed',code:'draft_exists',error:'电脑输入框已有草稿'}});
    return response(draftEvent());
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent({composer:{available:true,hasDraft:true}}));
  page.elements.get('message').value='Phone message';
  assert.equal(await page.hooks.sendText(true),false);
  assert.deepEqual(paths,['/api/action','/api/draft']);
  assert.equal(page.elements.get('draftConflict').hidden,false);
  assert.equal(page.elements.get('message').value,'Phone message');
  assert.equal(page.historyEntries.length,1);
});

test('the same text already in the desktop editor can finish sending directly', async () => {
  const paths=[];
  const page=makePage(async(path,request)=>{
    paths.push(path);assert.equal(path,'/api/action');
    assert.equal(JSON.parse(request.body).text,'同一条手机消息');
    return response({ok:true,epoch:7,delivery:{id:JSON.parse(request.body).messageId,state:'sent'}});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent({composer:{available:true,hasDraft:true}}));
  page.elements.get('message').value='同一条手机消息';
  assert.equal(await page.hooks.sendText(true),true);
  assert.deepEqual(paths,['/api/action']);
  assert.equal(page.elements.get('draftConflict').hidden,true);
  assert.equal(page.elements.get('message').value,'');
});

test('desktop drafts use a capsule entry and expose details only inside their panel',async()=>{
  const page=makePage(async()=>response(draftEvent({text:'电脑另外的内容'})));
  page.elements.get('draftPanel').hidden=true;
  page.elements.get('message').value='手机内容';
  await page.hooks.openDraft(false);
  assert.equal(page.elements.get('draftPanel').hidden,true);
  assert.equal(page.elements.get('computerDraftBtn').hidden,false);
  await page.hooks.openDraft(false,true);
  assert.equal(page.hooks.state.panel,'draft');
  assert.equal(page.elements.get('draftPreview').textContent,'电脑另外的内容');
});

test('clearing a draft preview ignores an obsolete in-flight read', async () => {
  let reply;
  const page=makePage(()=>new Promise(resolve=>{reply=resolve;}));
  const opening=page.hooks.openDraft(true);page.hooks.clearDraftConflict();
  reply(response(draftEvent()));await opening;
  assert.equal(page.hooks.state.draft,null);
  assert.equal(page.elements.get('draftPreview').textContent,'');
  assert.equal(page.elements.get('draftConflict').hidden,true);
  assert.equal(page.elements.get('message').value,'');
});

test('panels share one history entry and back restores the main view with its phone draft', async()=>{
  const page=makePage();
  page.elements.get('message').value='Keep this phone draft';
  assert.equal(page.hooks.setPanel('settings'),true);
  assert.equal(page.elements.get('remoteView').inert,true);
  page.hooks.setPanel('navigation');
  assert.equal(page.historyEntries.length,2);
  page.history.back();await Promise.resolve();
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.elements.get('remoteView').inert,false);
  assert.equal(page.elements.get('settingsPanel').hidden,true);
  assert.equal(page.elements.get('navigationPanel').hidden,true);
  assert.equal(page.elements.get('message').value,'Keep this phone draft');
  page.hooks.setPanel('settings');page.hooks.setPanel('');page.hooks.setPanel('');
  await Promise.resolve();assert.equal(page.backCount(),2);
});

const optionsEvent=(overrides={})=>({ok:true,key:'options-a',epoch:7,available:true,menu:false,
  items:[{id:'model',kind:'model',title:'Model <script>',enabled:true,selected:false}],...overrides});

test('the model shortcut opens the native model choices and keeps the phone draft',async()=>{
  const actions=[];
  const page=makePage(async(path,request)=>{
    if(path==='/api/composer')return response(optionsEvent());
    assert.equal(path,'/api/action');const command=JSON.parse(request.body);actions.push(command);
    return response({ok:true,epoch:7,options:command.targetId==='model'
      ?optionsEvent({key:'models',category:'model',menu:true,items:[{id:'choice',kind:'option',title:'Model B',enabled:true}]})
      :optionsEvent({key:'changed',items:[{id:'model',kind:'model',title:'Model B',enabled:true}]})});
  });
  page.elements.get('message').value='保留手机草稿';
  page.hooks.state.optionsRoot=optionsEvent();page.hooks.state.optionsRootAt=1000;
  await page.hooks.openModelPicker();
  assert.equal(page.hooks.state.options.menu,true);assert.equal(actions.length,1);
  await page.hooks.invokeOption(page.hooks.state.options.items[0]);
  assert.equal(actions.length,2);assert.equal(page.elements.get('modelPickerLabel').textContent,'Model B');
  assert.equal(page.elements.get('message').value,'保留手机草稿');
});

test('one model sheet flattens the native model branch, strength positions and fast switch',async()=>{
  let stage='',revision=0,level=5,quick=false,model='GPT Fixture A';
  const actions=[];
  function snapshot(){
    const menu=stage!=='';let items;
    if(stage==='model')items=[
      {id:'model-list',title:'选择模型',kind:'model-list'},
      {id:'quick',title:quick?'禁用快速模式':'启用快速模式',kind:'quick',selected:quick},
      {id:'reset',title:'重置为默认',kind:'reset'},
      ...Array.from({length:6},(_,i)=>({id:'strength-'+(i+1),title:model+' '+['轻度','中',model==='GPT Fixture B'?'High':'高','极高','Max','Ultra'][i],kind:'effort-step',
        label:['轻度','中',model==='GPT Fixture B'?'High':'高','极高','Max','Ultra'][i],effortKey:['low','medium','high','xhigh','max','ultra'][i],
        selected:i+1===level,level:i+1,currentLevel:level,levelCount:6}))
    ];
    else if(stage==='models')items=['GPT Fixture A','GPT Fixture B','默认模型'].map((title,i)=>({id:'m-'+i,title,kind:'option',selected:i===0}));
    else items=[{id:'model',title:model,kind:'model'}];
    return optionsEvent({key:'native-'+revision,scope:'fixture-conversation',category:menu?'model':'',menu,
      items:items.map(item=>({enabled:true,selected:false,...item}))});
  }
  const page=makePage(async(path,request)=>{
    if(path==='/api/composer')return response(snapshot());
    assert.equal(path,'/api/action');
    const command=JSON.parse(request.body), before=snapshot();
    assert.equal(command.optionsKey,before.key);
    actions.push(command);
    if(command.operation==='dismiss')stage='';
    else if(command.targetId==='model')stage='model';
    else if(command.targetId==='model-list')stage='models';
    else if(command.targetId==='quick')quick=!quick;
    else if(command.targetId.startsWith('strength-'))level=Number(command.targetId.split('-')[1]);
    else if(command.targetId==='m-1'){model='GPT Fixture B';stage='';}
    else assert.fail('Unexpected control '+command.targetId);
    revision++;
    return response({ok:true,epoch:7,options:snapshot()});
  });
  page.elements.get('message').value='保留输入';
  await page.hooks.openModelPicker();
  const panel=page.elements.get('optionsList');
  assert.match(panel.textContent,/GPT Fixture AGPT Fixture B/);
  assert.match(panel.textContent,/Max/);
  assert.doesNotMatch(panel.textContent,/推理强度/);
  const fast=panel.children[0].children[0].children.find(node=>node.getAttribute('role')==='switch');
  assert.equal(fast.getAttribute('aria-label'),'快速模式');
  assert.equal(fast.textContent,'');
  assert.equal(page.elements.get('optionsResetBtn').hidden,false);
  assert.doesNotMatch(panel.textContent,/选择模型/);
  assert.doesNotMatch(panel.textContent,/默认模型/);
  assert.equal(page.elements.get('optionsBackBtn').hidden,true);
  const choice=(section,id)=>page.hooks.state.modelChoices.find(row=>row.section===section && row.item.id===id);
  const openedActions=actions.length;
  page.hooks.stageModelChoice(choice('quick','quick'));
  page.hooks.stageModelChoice(choice('effort','strength-3'));
  page.hooks.stageModelChoice(choice('model','m-1'));
  assert.equal(actions.length,openedActions);
  assert.equal(page.elements.get('optionsResetBtn').textContent,'应用');
  assert.equal(page.elements.get('modelFastIcon').hidden,true);
  await page.hooks.applyModelDraft();
  assert.equal(choice('quick','quick').item.selected,true);
  assert.equal(choice('effort','strength-3').item.selected,true);
  assert.equal(choice('model','m-1').item.selected,false);
  assert.equal(page.hooks.modelChoiceConfirmed(choice('model','m-1')),true);
  assert.equal(actions.filter(action=>action.targetId==='quick').length,1);
  assert.equal(actions.filter(action=>action.targetId==='strength-3').length,1);
  assert.equal(actions.filter(action=>action.targetId==='m-1').length,1);
  assert.equal(page.hooks.state.panel,'options');
  assert.equal(page.elements.get('modelFastIcon').hidden,false);
  assert.equal(page.elements.get('message').value,'保留输入');
  page.hooks.setPanel('');await new Promise(resolve=>setImmediate(resolve));
  const closedActions=actions.length;
  await page.hooks.openModelPicker();
  assert.ok(actions.length>closedActions);
  assert.equal(choice('quick','quick').item.selected,true);
});

test('bitmap frames release their image and leave unrelated controls intact',async()=>{
  const page=makePage();let releases=0;
  page.setBitmapDecoder(async()=>({width:900,height:600,close(){releases++;}}));
  await page.hooks.displayFrame({},frame(1),{aborted:false});
  const control=page.elements.get('submitChoices').children[0];
  await page.hooks.displayFrame({},frame(2),{aborted:false});
  assert.equal(releases,2);assert.equal(page.revoked.length,0);
  assert.equal(page.elements.get('submitChoices').children[0],control);
});

test('a late option read cannot reopen a panel after browser back',async()=>{
  let release;
  const page=makePage(()=>new Promise(resolve=>{release=resolve;}));
  const pending=page.hooks.openModelPicker();
  page.history.back();await Promise.resolve();
  release(response(optionsEvent()));await pending;
  assert.equal(page.hooks.state.options,null);
  assert.equal(page.elements.get('optionsPanel').hidden,true);
  assert.equal(page.hooks.state.panel,'');
});

test('session expiry closes the options panel before showing pairing',async()=>{
  const page=makePage();page.hooks.setPanel('options');
  page.hooks.state.live=false;page.hooks.state.token='';
  page.hooks.showPairView('expired');await Promise.resolve();
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.elements.get('optionsPanel').hidden,true);
  assert.equal(page.elements.get('pairView').hidden,false);
  assert.equal(page.elements.get('remoteView').hidden,true);
});

test('option clicks use the observed catalog once and stale window epochs block later clicks',async()=>{
  const actions=[];let reads=0;
  const page=makePage(async(path,request)=>{
    if(path==='/api/composer')return response(optionsEvent(reads++?{key:'menu-a',menu:true,category:'model',items:[{id:'choice',kind:'option',title:'One',enabled:true}]}:{}));
    actions.push(JSON.parse(request.body));return response({ok:true,epoch:7});
  });
  page.hooks.setPanel('options');page.hooks.state.optionsIntent='model';await page.hooks.refreshOptions();
  const rendered=page.elements.get('optionsList').children[0].children[1];
  assert.ok(rendered.textContent.includes('Model <script>'));
  await page.hooks.invokeOption(page.hooks.state.options.items[0]);
  assert.equal(actions.length,1);assert.equal(actions[0].optionsKey,'options-a');
  assert.equal(actions[0].targetId,'model');assert.equal(actions[0].epoch,7);
  assert.equal(page.hooks.state.options.menu,true);
  page.hooks.state.epoch=8;await page.hooks.invokeOption(page.hooks.state.options.items[0]);
  assert.equal(actions.length,1);
});

test('opening the model sheet refreshes synced options before invoking a native menu',async()=>{
  const paths=[];
  const page=makePage(async(path,request)=>{
    paths.push(path);
    if(path==='/api/composer')return response(optionsEvent());
    assert.equal(path,'/api/action');
    return response({ok:true,epoch:7,options:optionsEvent({key:'menu-next',menu:true,category:'model',items:[{id:'choice',title:'One',kind:'option',enabled:true}]})});
  });
  const composer=optionsEvent();
  page.hooks.applyConversationEvent({kind:'conversation',replace:true,key:'chat-a',epoch:7,available:true,
    messages:[],order:[],composer:{available:true,hasDraft:false,options:composer}});
  await page.hooks.openModelPicker();
  assert.deepEqual(paths,['/api/composer','/api/action']);assert.equal(page.hooks.state.options.menu,true);
});

test('reopening the model sheet reads the account current choices without retaining unavailable Max',async()=>{
  let levels=['Standard','Extended','Max'],reads=0;
  const page=makePage(async path=>{
    assert.equal(path,'/api/composer');reads++;
    return response(optionsEvent({menu:true,category:'model',items:levels.map((title,index)=>({
      id:'native-'+index,title,kind:'option',enabled:true,selected:index===0
    }))}));
  });
  await page.hooks.openModelPicker();
  assert.ok(page.hooks.state.modelChoices.some(choice=>choice.item.title==='Max'));
  page.hooks.state.options.menu=false;
  page.hooks.setPanel('');await Promise.resolve();
  levels=['Standard','Extended'];
  await page.hooks.openModelPicker();
  assert.equal(reads,2);
  assert.deepEqual(Array.from(page.hooks.state.modelChoices,choice=>choice.item.title),levels);
  assert.equal(page.hooks.state.modelChoices.some(choice=>choice.item.title==='Max'),false);
});

test('native power choices use their visible captions and the thumb reaches both endpoints',async()=>{
  const page=makePage(async()=>response(optionsEvent({menu:true,category:'model',items:
    Array.from({length:3},(_,index)=>({id:'power-'+index,title:String(index+1),kind:'power-step',
      enabled:true,selected:index===1,label:['轻度','中','高'][index],level:index+1,currentLevel:2,levelCount:3}))
  })));
  await page.hooks.openModelPicker();
  const choices=page.hooks.state.modelChoices;
  assert.equal(choices.length,3);
  assert.ok(choices.every(choice=>choice.section==='effort'));
  const track=()=>page.elements.get('optionsList').children[0].children[1];
  assert.equal(track().getAttribute('aria-label'),'思考强度');
  assert.equal(track().getAttribute('role'),'slider');
  assert.equal(track().getAttribute('aria-valuetext'),'中');
  assert.equal(track().textContent,'');
  assert.equal(track().children[1].getAttribute('aria-label'),'中');
  assert.equal(track().style.props['--level'],'50%');
  page.hooks.stageModelChoice(choices[0]);
  assert.equal(track().style.props['--level'],'0%');
  page.hooks.stageModelChoice(choices[2]);
  assert.equal(track().children[2].getAttribute('aria-label'),'高');
  assert.equal(track().style.props['--level'],'100%');
});

test('numeric placeholder labels never become selectable thinking tiers',async()=>{
  const page=makePage(async()=>response(optionsEvent({menu:true,category:'model',items:
    Array.from({length:6},(_,index)=>({id:'power-'+index,title:String(index+1),label:String(index+1),kind:'power-step',
      enabled:true,selected:index===4,level:index+1,currentLevel:5,levelCount:6}))
  })));
  await page.hooks.openModelPicker();
  assert.match(page.elements.get('optionsList').textContent,/暂未读全档位名称/);
  assert.equal(page.elements.get('optionsList').children.some(section=>section.className==='model-tuning'),false);
});

test('the computer screen follows the top of the composer and floating draft controls',()=>{
  const page=makePage(),properties={};
  page.elements.get('screenCard').style.setProperty=(key,value)=>properties[key]=value;
  page.elements.get('composerCard').getBoundingClientRect=()=>({top:660,left:12,width:366});
  page.elements.get('composerFloat').getBoundingClientRect=()=>({top:620,height:32});
  page.hooks.setPanel('screen');
  assert.equal(properties['--panel-width'],'366px');
  assert.equal(properties['--panel-max-height'],'600px');
  assert.equal(properties['--panel-bottom'],'168px');
  page.elements.get('composerCard').getBoundingClientRect=()=>({top:490,left:12,width:366});
  page.elements.get('composerFloat').getBoundingClientRect=()=>({top:450,height:32});
  page.hooks.positionFloatingSurfaces();
  assert.equal(properties['--panel-max-height'],'430px');
  assert.equal(properties['--panel-bottom'],'338px');
});

test('the model sheet and status notice share the composer width and leave room for capsules',()=>{
  const page=makePage(),properties={},noticeStyles={};
  const options=page.elements.get('optionsPanel'),notice=page.elements.get('toast');
  options.style.setProperty=(key,value)=>properties[key]=value;
  options.getBoundingClientRect=()=>({height:160});
  notice.getBoundingClientRect=()=>({height:44});
  notice.style.setProperty=(key,value)=>noticeStyles[key]=value;
  page.elements.get('composerCard').getBoundingClientRect=()=>({top:600,left:100,width:600});
  page.elements.get('composerFloat').getBoundingClientRect=()=>({top:560,height:32});
  page.hooks.setPanel('options');
  assert.equal(properties['--panel-bottom'],'228px');
  assert.equal(properties['--panel-center'],'400px');
  assert.equal(properties['--panel-width'],'600px');
  assert.equal(properties['--panel-max-height'],'540px');
  notice.hidden=false;
  page.hooks.positionFloatingSurfaces();
  assert.equal(properties['--panel-bottom'],'228px');
  assert.equal(properties['--panel-max-height'],'488px');
  assert.deepEqual(noticeStyles,{'--notice-center':'400px','--notice-width':'600px','--notice-top':'12px'});
});

test('settings and projects cover the composer while complex notices stay below the toolbar',()=>{
  const page=makePage(),props={},noticeProps={};
  page.elements.get('remoteToolbar').getBoundingClientRect=()=>({bottom:68});
  page.elements.get('composerCard').getBoundingClientRect=()=>({top:620,bottom:776,left:12,width:366});
  page.elements.get('composerFloat').getBoundingClientRect=()=>({top:584,height:28});
  page.elements.get('toast').hidden=false;
  page.elements.get('toast').getBoundingClientRect=()=>({height:44});
  page.elements.get('toast').style.setProperty=(key,value)=>noticeProps[key]=value;
  for(const name of ['settings','navigation']){
    page.elements.get(name+'Panel').style.setProperty=(key,value)=>props[key]=value;
    page.hooks.setPanel(name);
    page.elements.get('toast').hidden=false;page.hooks.positionFloatingSurfaces();
    assert.equal(props['--panel-bottom'],'12px');
    assert.equal(props['--panel-height'],'640px');
    assert.equal(noticeProps['--notice-top'],'76px');
  }
});

test('draft text and attachments survive reload and native conversation identity changes',()=>{
  const first=makePage();first.hooks.applyConversationEvent(chatEvent({threadId:'thread-a'}));
  first.elements.get('message').value='A 的草稿';
  first.hooks.state.phoneFiles=[{id:'photo-a',name:'photo.png',size:8,state:'uploaded',key:'chat-a',epoch:7}];
  first.hooks.savePhoneDraft();
  first.hooks.applyConversationEvent(chatEvent({key:'chat-b',threadId:'thread-b'}));
  assert.equal(first.elements.get('message').value,'');
  assert.equal(first.hooks.state.phoneFiles.length,0);
  first.elements.get('message').value='B 的草稿';first.hooks.savePhoneDraft();
  first.hooks.applyConversationEvent(chatEvent({key:'new-native-a',threadId:'thread-a'}));
  assert.equal(first.elements.get('message').value,'A 的草稿');
  assert.equal(first.hooks.state.phoneFiles[0].name,'photo.png');
  const second=makePage();for(const [key,value] of first.storage)second.storage.set(key,value);
  second.hooks.applyConversationEvent(chatEvent({key:'after-refresh-a',threadId:'thread-a'}));
  assert.equal(second.elements.get('message').value,'A 的草稿');
  assert.equal(second.hooks.state.phoneFiles[0].id,'photo-a');
  second.hooks.applyConversationEvent(chatEvent({key:'chat-b-again',threadId:'thread-b'}));
  assert.equal(second.elements.get('message').value,'B 的草稿');
});

test('removing an attached picture waits for native success and preserves a failure',async()=>{
  let fail=true;const paths=[];
  const page=makePage(async(path,options)=>{
    paths.push(path);assert.equal(path,'/api/uploads/remove');
    assert.equal(JSON.parse(options.body).ids[0],'photo-a');
    if(fail)return {ok:false,status:409,json:async()=>({error:'移除失败',code:'attachment_changed'})};
    return response({ok:true});
  });
  const item={id:'photo-a',name:'photo.png',state:'attached',key:'chat-a',epoch:7,previewUrl:'blob:test-photo'};
  page.hooks.state.phoneFiles=[item];page.hooks.renderPhoneFiles();
  const remove=page.elements.get('phoneFiles').children[0].children.at(-1);
  assert.equal(remove.getAttribute('aria-label'),'移除附件 photo.png');
  await page.hooks.removePhoneFile(item);
  assert.equal(page.hooks.state.phoneFiles.length,1);assert.equal(item.state,'attached');
  fail=false;await page.hooks.removePhoneFile(item);
  assert.equal(page.hooks.state.phoneFiles.length,0);assert.ok(page.revoked.includes('blob:test-photo'));
  assert.equal(paths.length,2);
});

test('multiple attachments bind one native file at a time before one message submission',async()=>{
  const requests=[];const page=makePage(async(path,options)=>{
    const body=JSON.parse(options.body);requests.push({path,body});
    if(path==='/api/uploads/attach'){
      assert.equal(body.ids.length,1);return response({ok:true,state:'attached'});
    }
    assert.equal(path,'/api/action');return response({ok:true,epoch:7,submission:{state:'sent'}});
  });
  page.hooks.applyConversationEvent(chatEvent());page.elements.get('message').value='图片说明';
  page.hooks.state.phoneFiles=['first','second'].map(id=>({id,name:id+'.png',state:'uploaded',key:'chat-a',epoch:7}));
  assert.equal(await page.hooks.sendText(true),true);
  assert.deepEqual(requests.map(row=>row.path),['/api/uploads/attach','/api/uploads/attach','/api/action']);
  assert.equal(page.hooks.state.phoneFiles.length,0);
});

test('short completion status uses the small capsule separately from complex reminders',()=>{
  const page=makePage();page.hooks.toast('已发送');
  assert.equal(page.elements.get('sendProgressText').textContent,'已发送');
  assert.equal(page.elements.get('sendProgressSpinner').hidden,true);
  assert.equal(page.elements.get('sendProgress').hidden,false);
  assert.equal(page.elements.get('noticeText').textContent,'');
  assert.ok(!html.includes('id="keepSendDraftBtn"'));
});

test('conversation table blocks pass through the message boundary and same-length edits render',()=>{
  const page=makePage();
  const make=(value,revision)=>chatEvent({revision,messages:[{id:'m0',role:'assistant',text:'table',blocks:[
    {kind:'table',text:'h | '+value,headers:[{text:'h'}],rows:[[{text:value}]]}
  ]}]});
  page.hooks.applyConversationEvent(make('A','1'));
  const body=page.hooks.state.chatElements.m0.body;
  assert.equal(body.children[0].className,'chat-table-wrap');
  assert.equal(body.children[0].children[0].tagName,'TABLE');
  page.hooks.applyConversationEvent(make('B','2'));
  assert.equal(body.textContent.includes('B'),true);
  assert.equal(body.textContent.includes('A'),false);
});

test('historical image preview uses binary transport and reuses its decoded resource URL',async()=>{
  let requests=0;
  const page=makePage(async path=>{assert.ok(path.startsWith('/api/resource?'));requests++;return {ok:true,status:200,blob:async()=>new Blob(['image'],{type:'image/jpeg'})};});
  const ref={id:'registered-image',kind:'image',name:'电脑截图.png'};
  await page.hooks.openMessageResource(ref,page.elements.get('modelPickerBtn'));
  assert.equal(page.elements.get('attachmentImage').src,'blob:test-1');
  assert.equal(page.elements.get('attachmentImage').hidden,false);
  page.hooks.setPanel('');await Promise.resolve();
  await page.hooks.openMessageResource(ref,page.elements.get('modelPickerBtn'));
  assert.equal(requests,1);
  page.hooks.resetRemote();assert.ok(page.revoked.includes('blob:test-1'));
});

test('recommended defaults are actions rather than model names',async()=>{
  const page=makePage(async()=>response(optionsEvent({menu:true,category:'model',items:[
    {id:'default',title:'默认 推荐模型集',kind:'option',enabled:true},
    {id:'gpt',title:'GPT Fixture',kind:'option',enabled:true,selected:true}
  ]})));
  await page.hooks.openModelPicker();
  assert.deepEqual(Array.from(page.hooks.state.modelChoices,c=>c.item.title),['GPT Fixture']);
});

test('a native model catalog loads all labels in one request',async()=>{
  const paths=[];
  const trigger={id:'model',title:'GPT Fixture',kind:'model',enabled:true};
  const root=optionsEvent({menu:false,scope:'chat-a',items:[trigger]});
  const power=optionsEvent({menu:true,scope:'chat-a',category:'model',items:[
    {id:'a',title:'GPT Fixture Light',label:'GPT Fixture Light',kind:'power-step',enabled:true,selected:true,level:1,currentLevel:1,levelCount:2},
    {id:'b',title:'GPT Fixture Deep',label:'GPT Fixture Deep',kind:'power-step',enabled:true,level:2,currentLevel:1,levelCount:2},
    {id:'list',title:'选择模型',kind:'model-list',enabled:true}
  ]});
  const models=optionsEvent({menu:true,scope:'chat-a',category:'model',items:[{id:'gpt',title:'GPT Fixture',kind:'option',enabled:true,selected:true}]});
  const page=makePage(async(path,options)=>{paths.push(path);assert.equal(JSON.parse(options.body).operation,'catalog');return response({ok:true,epoch:7,options:models,catalog:{root,power,models}});});
  page.hooks.applyConversationEvent(chatEvent());page.hooks.state.capabilities={composerCatalog:true,composerOptions:true};
  await page.hooks.openModelPicker();
  assert.deepEqual(paths,['/api/action']);
  assert.equal(page.hooks.state.modelChoices.length,3);
  assert.equal(page.hooks.state.modelChoices[1].item.label,'GPT Fixture Deep');
});

test('a native rail with only its current label is displayed without actionable placeholder tiers',async()=>{
  const root=optionsEvent({menu:false,scope:'chat-a',items:[{id:'model',title:'GPT Fixture',kind:'model',enabled:true}]});
  const power=optionsEvent({menu:true,scope:'chat-a',category:'model',items:[
    {id:'current',title:'GPT Fixture 深度',label:'高',kind:'power-step',enabled:false,selected:true,level:3,currentLevel:3,levelCount:6}
  ]});
  const page=makePage(async()=>response({ok:true,epoch:7,options:power,catalog:{root,power,models:power}}));
  page.hooks.applyConversationEvent(chatEvent());
  page.hooks.state.capabilities={composerCatalog:true,composerOptions:true,windowView:true,windowPointer:true};
  await page.hooks.openModelPicker();
  const tuning=page.elements.get('optionsList').children[0],track=tuning.children[1];
  assert.equal(tuning.children[0].textContent,'高');
  assert.equal(track.getAttribute('data-readonly'),'true');
  assert.equal(track.style.props['--level'],'40%');
  assert.equal(track.children.filter(child=>child.className==='model-tick').length,6);
  assert.ok(track.children.every(child=>child.tagName!=='BUTTON'));
  assert.equal(page.elements.get('optionsScreenBtn').hidden,false);
});

test('applying after the native model catalog ages rebinds the staged choice before invoking it',async()=>{
  const requests=[];
  let generation=0, model='GPT Fixture A';
  const root=()=>optionsEvent({key:'root-'+generation,menu:false,scope:'chat-a',items:[
    {id:'model',title:model,kind:'model',enabled:true}
  ]});
  const page=makePage(async(path,options)=>{
    assert.equal(path,'/api/action');
    const action=JSON.parse(options.body);
    requests.push(action.operation==='catalog'?'catalog':action.targetId);
    if(action.operation==='invoke'){
      assert.equal(action.optionsKey,'models-2');
      assert.equal(action.targetId,'b-2');
      model='GPT Fixture B';
      return response({ok:true,epoch:7,options:root()});
    }
    generation++;
    const models=optionsEvent({key:'models-'+generation,menu:true,scope:'chat-a',category:'model',items:[
      {id:'a-'+generation,title:'GPT Fixture A',kind:'option',enabled:true,selected:true},
      {id:'b-'+generation,title:'GPT Fixture B',kind:'option',enabled:true,selected:false}
    ]});
    return response({ok:true,epoch:7,options:models,catalog:{root:root(),power:models,models}});
  });
  page.hooks.applyConversationEvent(chatEvent());
  page.hooks.state.capabilities={composerCatalog:true,composerOptions:true};
  await page.hooks.openModelPicker();
  page.hooks.stageModelChoice(page.hooks.state.modelChoices.find(choice=>choice.item.id==='b-1'));
  page.setNow(125_000);
  await page.hooks.applyModelDraft();
  assert.deepEqual(requests,['catalog','catalog','b-2','catalog']);
  const selected=page.hooks.state.modelChoices.find(page.hooks.modelChoiceConfirmed);
  assert.equal(selected.item.title,'GPT Fixture B');
  assert.equal(selected.item.selected,false);
});

test('switching the preview to another model uses its tiers and applies both choices once',async()=>{
  const calls=[];
  const labels={low:'轻度',medium:'中',high:'高',xhigh:'极高',max:'Max',ultra:'Ultra'};
  const profiles={sol:Object.keys(labels),luna:Object.keys(labels).slice(0,-1)};
  let model='sol',effort='max',key='first-selection';
  function result(){
    const modelName=model==='sol'?'GPT Fixture Sol':'GPT Fixture Luna';
    const root=optionsEvent({key:'root',menu:false,scope:'chat-a',items:[{id:'model',kind:'model',title:modelName,enabled:true}]});
    const power=optionsEvent({key:'power',menu:true,scope:'chat-a',category:'model',items:profiles[model].map((tier,index)=>({
      id:tier,kind:'power-step',title:modelName+' '+labels[tier],label:labels[tier],effortKey:tier,
      enabled:true,selected:tier===effort,level:index+1,currentLevel:profiles[model].indexOf(effort)+1,levelCount:profiles[model].length
    }))});
    const models=optionsEvent({key:'models',menu:true,scope:'chat-a',category:'model',items:['sol','luna'].map(name=>({
      id:name,kind:'option',modelKey:name,title:name==='sol'?'GPT Fixture Sol':'GPT Fixture Luna',enabled:true,selected:name===model
    }))});
    const selection={key,current:{model,effort,mode:'model',label:modelName,effortLabel:labels[effort]},
      profiles:Object.fromEntries(Object.entries(profiles).map(([name,tiers])=>[name,tiers.map(tier=>({key:tier,label:labels[tier]}))]))};
    return {ok:true,epoch:7,options:models,catalog:{root,power,models,selection}};
  }
  const page=makePage(async(path,options)=>{
    assert.equal(path,'/api/action');
    const action=JSON.parse(options.body);calls.push(action);
    if(action.operation==='catalog')return response(result());
    assert.deepEqual(action,{epoch:7,kind:'composer',operation:'apply-model',optionsKey:'first-selection',model:'luna',effort:'high'});
    model='luna';effort='high';key='second-selection';
    return response({...result(),modelApply:{state:'applied',model,effort}});
  });
  const {hooks,elements}=page;
  hooks.applyConversationEvent(chatEvent());hooks.state.capabilities={composerCatalog:true,composerOptions:true};
  await hooks.openModelPicker();
  const rail=()=>elements.get('optionsList').children[0].children[1];
  hooks.stageModelChoice(hooks.state.modelChoices.find(choice=>choice.item.effortKey==='ultra'));
  hooks.stageModelChoice(hooks.state.modelChoices.find(choice=>choice.item.modelKey==='luna'));
  assert.equal(calls.length,1);
  assert.equal(rail().getAttribute('aria-valuemax'),'5');
  assert.equal(rail().getAttribute('aria-valuetext'),'随模型');
  rail().children[2].fire('click',{});
  assert.equal(rail().getAttribute('aria-valuetext'),'高');
  assert.equal(calls.length,1);
  await hooks.applyModelDraft();
  assert.deepEqual(calls.map(call=>call.operation),['catalog','apply-model']);
  assert.equal(elements.get('modelPickerLabel').textContent,'GPT Fixture Luna 高');
  assert.equal(rail().getAttribute('aria-valuetext'),'高');
});

test('the plus opens the system chooser directly without adding a panel or reading desktop options',()=>{
  const page=makePage();
  let opened=0;
  page.elements.get('phoneFileInput').click=()=>opened++;
  page.hooks.openFilePicker();
  assert.equal(opened,1);
  assert.equal(page.hooks.state.panel,'');
  assert.match(html,/id="phoneFileInput" type="file" multiple/);
  assert.doesNotMatch(html,/id="filesPanel"/);
});

test('desktop approvals open from chat while a send is pending and only an explicit choice invokes the computer',async()=>{
  const actions=[];
  const prompt={epoch:7,key:'prompt-catalog',menu:false,category:'prompt',
    prompt:{title:'批准命令',text:'读取示例目录。\nGet-ChildItem -Name',approval:true,truncated:false},
    items:[{id:'allow',title:'允许一次',kind:'option',enabled:true},{id:'deny',title:'拒绝',kind:'option',enabled:true}]};
  const page=makePage(async(path,options)=>{
    if(path==='/api/composer')return response(prompt);
    assert.equal(path,'/api/action');
    actions.push(JSON.parse(options.body));
    return response({ok:true,epoch:7,options:{epoch:7,key:'root-options',menu:false,items:[]}});
  });
  const {hooks,elements}=page;
  Object.assign(hooks.state,{view:'chat',pending:{...hooks.state.pending,sendText:true}});
  hooks.applyConversationEvent(chatEvent({prompt,capabilities:{rawInput:false}}));
  assert.equal(elements.get('desktopPromptBtn').hidden,false);
  await hooks.openDesktopPrompt();
  assert.equal(elements.get('optionsPromptText').textContent,'读取示例目录。');
  assert.equal(elements.get('optionsPromptCommand').textContent,'Get-ChildItem -Name');
  assert.deepEqual(elements.get('optionsList').children.map(button=>button.textContent),['拒绝','允许一次']);
  assert.equal(elements.get('optionsRetryBtn').hidden,true);
  assert.equal(elements.get('optionsScreenBtn').hidden,true);
  hooks.setPanel('');await Promise.resolve();
  assert.equal(actions.length,0);
  await hooks.openDesktopPrompt();
  await hooks.invokeOption(hooks.state.options.items[0]);
  assert.deepEqual(actions,[{epoch:7,kind:'composer',operation:'invoke',optionsKey:'prompt-catalog',targetId:'allow'}]);
  assert.equal(elements.get('optionsPanel').hidden,true);
  assert.equal(hooks.state.pending.sendText,true);
});

test('a sheet keeps text syncing and pauses an active picture reader',()=>{
  const chatPage=makePage(),chat=new AbortController();
  Object.assign(chatPage.hooks.state,{view:'chat',mediaStarted:true,chatAbort:chat});
  chatPage.hooks.setPanel('settings');
  assert.equal(chat.signal.aborted,false);
  assert.equal(chatPage.elements.get('sheetBackdrop').hidden,false);
  const screenPage=makePage(),frame=new AbortController();
  Object.assign(screenPage.hooks.state,{view:'screen',mediaStarted:true,frameAbort:frame});
  screenPage.hooks.setPanel('settings');
  assert.equal(frame.signal.aborted,true);
});

test('one finger sends one left click, two fingers send one right click, and moving only points',async()=>{
  const requests=[];
  const page=makePage(async(path,options)=>{
    assert.equal(path,'/api/action');
    requests.push(JSON.parse(options.body));
    return response({ok:true,epoch:7});
  });
  const {hooks,elements}=page;
  Object.assign(hooks.state,{status:'ready',capabilities:{windowPointer:true,windowView:true,rawInput:false},
    windows:[{id:1,title:'ChatGPT',exe:'ChatGPT.exe',selected:true}]});
  hooks.openScreenView();
  assert.equal(hooks.state.panel,'screen');
  assert.equal(elements.get('screenCard').hidden,false);
  Object.assign(hooks.state,{frameW:1000,frameH:600,displayedEpoch:7,displayedFrameId:55,frameSeen:true});
  const image=elements.get('screen');
  image.getBoundingClientRect=()=>({left:10,top:40,width:300,height:240});
  elements.get('screenShell').getBoundingClientRect=image.getBoundingClientRect;
  hooks.bindScreenGestures();
  const finger=(event,pointerId,clientX,clientY)=>image.fire(event,{pointerId,clientX,clientY,button:0,pointerType:'touch'});
  finger('pointerdown',1,160,160);finger('pointerup',1,160,160);
  finger('pointerdown',2,160,160);finger('pointerdown',3,180,170);
  finger('pointerup',3,180,170);finger('pointerup',2,160,160);
  finger('pointerdown',4,160,160);finger('pointermove',4,200,160);finger('pointerup',4,200,160);
  finger('pointerdown',5,160,160);finger('pointercancel',5,160,160);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(requests,['left','right'].map(operation=>({kind:'window-pointer',operation,epoch:7,frameId:55,x:.5,y:.5})));
  assert.equal(elements.get('screenCursor').hidden,false);
  hooks.setPanel('');assert.equal(hooks.state.view,'chat');assert.equal(elements.get('screenCard').hidden,true);
});

test('message progress remains independent of other-chat attention notices',()=>{
  const page=makePage(),{hooks,elements}=page;
  hooks.state.pending.sendText=true;
  hooks.applyConversationEvent(chatEvent({pendingRequests:[{id:'question',pending:'input',kind:'thread',title:'另一条对话'}]}));
  assert.equal(elements.get('sendProgress').hidden,false);
  assert.equal(elements.get('sendProgressSpinner').hidden,false);
  assert.equal(elements.get('noticeText').textContent,'其他对话有待处理请求');
  assert.equal(elements.get('noticeOpenBtn').hidden,false);
  assert.equal(elements.get('desktopPromptBtn').hidden,true);
  hooks.state.sending={state:'confirming',previousAgent:true};
  hooks.renderControls();
  assert.equal(elements.get('sendProgressText').textContent,'发送中');
});

test('project navigation retains its running conversation stream',async()=>{
  const controller=new AbortController();
  const page=makePage(async(path,request)=>{
    assert.equal(path,'/api/action');
    const action=JSON.parse(request.body);
    assert.equal(action.targetId,'p');
    return response({ok:true,epoch:7,navigation:navigationEvent({key:'expanded'})});
  });
  page.hooks.acceptNavigation(navigationEvent({items:[{id:'p',kind:'project',title:'Alpha',expanded:false}]}));
  Object.assign(page.hooks.state,{view:'chat',mediaStarted:true,chatAbort:controller});
  await page.hooks.navigateItem(page.hooks.state.navigation.items[0]);
  assert.equal(page.hooks.state.chatAbort,controller);
  assert.equal(controller.signal.aborted,false);
});

test('a stale conversation read resynchronizes without a connection warning',async()=>{
  const paths=[];
  let markRetry;
  const retry=new Promise(resolve=>{markRetry=resolve;});
  const page=makePage(async(path,{signal})=>{
    paths.push(path);
    if(paths.length===1){
      const status=new TextEncoder().encode(JSON.stringify({kind:'status',code:'conversation_changed',error:'目标窗口已经变化，正在同步对话'})+'\n');
      return {ok:true,body:{getReader:()=>({read:async()=>({value:status}),cancel:async()=>{}})}};
    }
    markRetry();
    return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error('Aborted'),{name:'AbortError'})),{once:true}));
  });
  page.hooks.state.view='chat';page.hooks.startMedia();
  try{
    await retry;
    assert.deepEqual(paths,['/api/conversation/stream','/api/conversation/stream']);
    assert.equal(page.hooks.state.error,'');
    assert.equal(page.elements.get('chatStatus').textContent,'正在同步对话…');
    assert.equal(page.elements.get('chatRetryBtn').hidden,true);
  }finally{page.hooks.stopLive();}
});

test('a blocked text stream falls back to snapshots and preserves rows, scroll and draft',async()=>{
  const paths=[];
  const page=makePage(async path=>{
    paths.push(path);
    if(path.endsWith('/stream'))return {ok:false,status:415,json:async()=>({error:'stream unavailable'})};
    assert.equal(path,'/api/conversation');
    return response(chatEvent({revision:'2'}));
  });
  const {hooks,elements}=page;
  hooks.state.view='chat';
  hooks.applyConversationEvent(chatEvent());
  const row=hooks.state.chatElements.m0;
  elements.get('chatMessages').scrollTop=5;
  elements.get('message').value='手机草稿';
  hooks.startMedia();
  try{
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(paths,['/api/conversation/stream','/api/conversation']);
    assert.equal(hooks.state.chat.revision,'2');
    assert.equal(hooks.state.chatConnected,true);
    assert.equal(hooks.state.chatElements.m0,row);
    assert.equal(elements.get('chatMessages').scrollTop,5);
    assert.equal(elements.get('message').value,'手机草稿');
  }finally{hooks.stopLive();}
});

test('a restarted Agent can report a lower epoch and delayed data from the old process is ignored',()=>{
  const {hooks,elements}=makePage();
  hooks.applyConversationEvent(chatEvent({instance:'first',epoch:17}));
  const row=hooks.state.chatElements.m0;
  elements.get('message').value='草稿';
  hooks.applyWindowState({...info,instance:'second',epoch:1});
  assert.equal(hooks.state.epoch,1);
  assert.equal(hooks.state.chatConnected,false);
  hooks.applyConversationEvent(chatEvent({instance:'second',epoch:1}));
  hooks.applyWindowState({...info,instance:'first',epoch:18});
  assert.equal(hooks.state.instance,'second');
  assert.equal(hooks.state.epoch,1);
  assert.equal(hooks.state.chatElements.m0,row);
  assert.equal(elements.get('message').value,'草稿');
});

test('reasoning collapses separately while body and queue keep their own locations',()=>{
  const page=makePage();
  page.hooks.applyConversationEvent({kind:'conversation',replace:true,key:'chat-a',epoch:7,available:true,
    messages:[{id:'m1',role:'assistant',text:'Process\n\nAnswer',blocks:[
      {kind:'paragraph',text:'Process',channel:'reasoning'},{kind:'paragraph',text:'Answer'}]}],order:['m1'],
    queued:[{id:'q1',text:'Pending user text'}]});
  const body=page.hooks.state.chatElements.m1.body;
  assert.equal(body.children[0].tagName,'DETAILS');assert.equal(body.children[0].open,false);
  assert.equal(body.children[1].textContent,'Answer');
  assert.equal(body.textContent.includes('Pending user text'),false);
  assert.equal(page.elements.get('queuedStrip').hidden,false);
  assert.equal(page.elements.get('queuedList').textContent,'Pending user text');
  assert.equal(page.elements.get('queuedList').hidden,false);
  body.children[0].open=true;
  page.hooks.renderMessageBody(body,[{kind:'paragraph',text:'Updated process',channel:'reasoning'},{kind:'paragraph',text:'Answer'}]);
  assert.equal(body.children[0].open,true);
  page.hooks.state.chat.queued=[];page.hooks.renderQueued();
  assert.equal(page.elements.get('queuedStrip').hidden,true);
});

test('a confirmed queued send clears input and reconciles with the native queue without duplicate rows',async()=>{
  const page=makePage(async()=>response({ok:true,epoch:7,submission:{state:'queued'}}));
  page.hooks.state.view='chat';page.hooks.state.chat={key:'chat-a',epoch:7,available:true,messages:[],generating:true,composer:{available:true,hasDraft:false}};
  page.elements.get('message').value='Queue this';
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(page.elements.get('message').value,'');
  assert.equal(page.elements.get('queueHeader').hidden,true);
  page.hooks.state.chat.queued=[{id:'native',text:'Queue this'}];page.hooks.renderQueued();
  assert.equal(page.elements.get('queuedList').children.length,1);
  page.hooks.state.chat.queued=[];page.hooks.state.chat.messages=[{role:'user',text:'Queue this'}];page.hooks.renderQueued();
  assert.equal(page.elements.get('queuedStrip').hidden,true);
});

test('uncertain option actions are not retried and keep the phone draft',async()=>{
  let calls=0;
  const page=makePage(async(path)=>{
    if(path==='/api/composer')return response(optionsEvent());
    calls++;throw new Error('connection lost');
  });
  page.elements.get('message').value='Unsent text';
  await page.hooks.openModelPicker();
  assert.equal(calls,1);assert.equal(page.hooks.state.optionsBusy,false);
  assert.equal(page.elements.get('message').value,'Unsent text');
  assert.ok(page.hooks.state.optionsError);
  assert.equal(page.hooks.state.optionsInvalid,true);
  await page.hooks.invokeOption(page.hooks.state.options.items[0]);assert.equal(calls,1);
});

test('closing a nested native menu stops after a failed dismissal instead of retrying',async()=>{
  const actions=[];
  const page=makePage(async(path,request)=>{
    if(path==='/api/composer')return response(optionsEvent({key:'menu-b',menu:true}));
    actions.push(JSON.parse(request.body));
    if(actions.length===2)throw new Error('uncertain close');
    return response({ok:true,epoch:7});
  });
  await page.hooks.dismissOptions(optionsEvent({menu:true}),true);
  assert.equal(actions.length,2);assert.equal(actions[0].optionsKey,'menu-b');
  assert.equal(actions[1].optionsKey,'menu-b');assert.equal(page.hooks.state.optionsBusy,false);
});

test('closing an already-closed native menu does not invoke an obsolete option',async()=>{
  let actions=0;
  const page=makePage(async path=>{
    if(path==='/api/composer')return response(optionsEvent({menu:false}));
    actions++;throw new Error('No dismissal should be sent');
  });
  await page.hooks.dismissOptions(optionsEvent({key:'obsolete',menu:true}),true);
  assert.equal(actions,0);
  assert.equal(page.hooks.state.optionsBusy,false);
});

test('opening the computer view keeps the native menu available for inspection',()=>{
  let requests=0;
  const page=makePage(async()=>{requests++;throw new Error('Keep the native menu open');});
  page.hooks.setPanel('options');
  page.hooks.state.options=optionsEvent({menu:true});
  page.hooks.setPanel('screen');
  assert.equal(page.hooks.state.panel,'screen');
  assert.equal(requests,0);
});

test('a model list with unreadable strength controls retains its computer view entry',async()=>{
  const page=makePage(async()=>response(optionsEvent({menu:true,category:'model',
    items:[{id:'a',kind:'option',title:'GPT Fixture A',enabled:true,selected:true}]})));
  await page.hooks.openModelPicker();
  assert.equal(page.elements.get('optionsScreenBtn').hidden,false);
});

test('native control images are bound to their observed option and window',async()=>{
  const item={id:'strength',kind:'surface',title:'强度',enabled:true};
  const page=makePage(async(path)=>path==='/api/composer'?response(optionsEvent({menu:true,category:'model',items:[item]})):
    response({key:'options-a',epoch:8,targetId:'strength',image:'data:image/png;base64,AA==',bounds:{x:0,y:0,width:1,height:1}}));
  await page.hooks.openModelPicker();await page.hooks.invokeOption(item);
  assert.equal(page.hooks.state.optionsSurface,null);
  assert.ok(page.hooks.state.optionsError.includes('已变化'));
});

test('native control images decode through blob URLs and release them after painting',async()=>{
  const item={id:'strength',kind:'surface',title:'强度',enabled:true};
  const page=makePage(async(path)=>path==='/api/composer'?response(optionsEvent({menu:true,category:'model',items:[item]})):
    response({key:'options-a',epoch:7,targetId:'strength',image:'data:image/png;base64,AA==',bounds:{x:0,y:0,width:1,height:1}}));
  await page.hooks.openModelPicker();await page.hooks.invokeOption(item);
  await Promise.resolve();await Promise.resolve();
  const canvas=page.elements.get('optionsList').children[0];
  assert.equal(canvas.tagName,'CANVAS');assert.equal(canvas['aria-busy'],'false');
  assert.equal(page.draws.length,1);assert.ok(page.revoked.includes('blob:test-1'));
});

test('navigation filters separate project groups from recent threads',()=>{
  const page=makePage();page.hooks.state.navigation={key:'nav',items:[
    {id:'p',kind:'project',title:'Project',project:'p',expanded:true},
    {id:'p-chat',kind:'thread',title:'Project chat',project:'p'},
    {id:'recent',kind:'thread',title:'Recent chat',project:''}]};
  page.hooks.state.navigationFilter='projects';page.hooks.renderNavigation();
  assert.equal(page.elements.get('navigationList').children.length,1);
  assert.ok(!page.elements.get('navigationList').textContent.includes('Recent chat'));
  page.hooks.state.navigationFilter='recent';page.hooks.renderNavigation();
  assert.equal(page.elements.get('navigationList').children.length,1);
  assert.ok(!page.elements.get('navigationList').textContent.includes('Project chat'));
  page.hooks.state.navigation.items=[{id:'section',kind:'section',title:'项目',expanded:false}];
  page.hooks.state.navigationFilter='projects';page.hooks.renderNavigation();
  assert.ok(page.elements.get('navigationList').textContent.includes('项目'));
});

const navigationEvent=(overrides={})=>({available:true,epoch:7,source:'window-a',key:'nav-a',items:[
  {id:'p',kind:'project',title:'Alpha',project:'p',projectTitle:'Alpha',expanded:true},
  {id:'new-p',kind:'new',title:'在 Alpha 中开始新聊天',project:'p',projectTitle:'Alpha'},
  {id:'thread-a',kind:'thread',title:'Saved chat',project:'p',projectTitle:'Alpha'}],...overrides});

test('reopening the project list keeps rows and scroll without age-based requests',async()=>{
  let requests=0;const page=makePage(async()=>{requests++;throw new Error('Unexpected read');});
  page.hooks.acceptNavigation(navigationEvent());page.hooks.setNavigation(true);
  const row=page.elements.get('navigationList').children[0];page.elements.get('navigationList').scrollTop=140;
  page.hooks.setNavigation(false);await Promise.resolve();page.setNow(600000);page.hooks.setNavigation(true);
  assert.equal(requests,0);assert.equal(page.elements.get('navigationList').children[0],row);
  assert.equal(page.elements.get('navigationList').scrollTop,140);
});

test('loaded projects fold locally without desktop expansion or changing the message epoch',async()=>{
  let requests=0;const page=makePage(async()=>{requests++;throw new Error('Unexpected action');});
  page.hooks.acceptNavigation(navigationEvent());
  const project=page.hooks.state.navigation.items[0];
  for(let i=0;i<4;i++)await page.hooks.navigateItem(project);
  assert.equal(requests,0);assert.equal(page.hooks.state.epoch,7);assert.equal(page.hooks.state.navigationFold.p,false);
  page.hooks.renderNavigation();const row=page.elements.get('navigationList').children[0];
  page.hooks.state.navigationBusy=true;page.hooks.state.navigationPendingId='p';page.hooks.renderNavigation();
  assert.equal(page.elements.get('navigationList').children[0],row);
});

test('cached project disclosure remains usable while sending and reading in the background',async()=>{
  let requests=0;const page=makePage(async()=>{requests++;throw new Error('Local disclosure must not send a request');});
  page.hooks.acceptNavigation(navigationEvent());page.hooks.state.pending.sendText=true;page.hooks.state.navigationLoading=true;
  page.hooks.renderNavigation();
  const header=page.hooks.state.navigationButtons.find(row=>row.item.id==='p');
  assert.equal(header.button.disabled,false);
  await page.hooks.navigateItem(header.item);assert.equal(page.hooks.state.navigationFold.p,true);
  assert.ok(page.elements.get('navigationList').textContent.includes('Saved chat'));
  assert.equal(page.hooks.state.navigationButtons.find(row=>row.item.id==='thread-a').button.disabled,true);
  await page.hooks.navigateItem(header.item);assert.equal(page.hooks.state.navigationFold.p,false);assert.equal(requests,0);
});

test('project browsing can open during a send without permitting a conversation switch',async()=>{
  let requests=0;const page=makePage(async()=>{requests++;throw new Error('Cached browsing must not send a request');});
  page.hooks.acceptNavigation(navigationEvent());page.hooks.state.pending.sendText=true;page.hooks.renderControls();
  assert.equal(page.elements.get('navigationToggleBtn').disabled,false);
  page.hooks.setNavigation(true);assert.equal(page.hooks.state.panel,'navigation');
  await page.hooks.navigateItem(page.hooks.state.navigation.items[0]);
  assert.equal(page.hooks.state.navigationFold.p,true);
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.equal(requests,0);assert.equal(page.hooks.state.pending.sendText,true);
});

test('a window revision does not leave dead project cards in an open panel',async()=>{
  let resolveRead;const page=makePage(()=>new Promise(resolve=>{resolveRead=resolve;}));
  page.hooks.state.windows=[{id:123,title:'ChatGPT',selected:true}];
  page.hooks.acceptNavigation(navigationEvent());page.hooks.setNavigation(true);
  page.hooks.applyWindowState({epoch:8,status:'ready',mode:'window',windows:[{id:123,title:'ChatGPT',selected:true}]});
  assert.ok(page.hooks.state.navigation);assert.equal(page.hooks.state.navigation.epoch,8);
  await page.hooks.navigateItem(page.hooks.state.navigation.items[0]);
  assert.equal(page.hooks.state.navigationFold.p,true);
  assert.ok(page.elements.get('navigationList').textContent.includes('Saved chat'));
  assert.equal(typeof resolveRead,'function');resolveRead(response(navigationEvent({epoch:8})));await Promise.resolve();await Promise.resolve();
});

test('an empty project in a complete local catalog opens immediately with an empty state',async()=>{
  let requests=0;const page=makePage(async()=>{requests++;throw new Error('Complete catalog must not expand the desktop');});
  page.hooks.acceptNavigation(navigationEvent({complete:true,items:[{id:'p',kind:'project',title:'Alpha',project:'p',expanded:false}]}));
  await page.hooks.navigateItem(page.hooks.state.navigation.items[0]);
  assert.equal(requests,0);assert.equal(page.hooks.state.navigationFold.p,true);
  assert.ok(page.elements.get('navigationList').textContent.includes('暂无对话'));
});

test('an unloaded project requests its children once and then opens locally',async()=>{
  const actions=[];const page=makePage(async(path,request)=>{
    actions.push({path,data:JSON.parse(request.body)});return response({ok:true,epoch:7,navigation:navigationEvent()});
  });
  page.hooks.acceptNavigation(navigationEvent({items:[{id:'p',kind:'project',title:'Alpha',project:'p',expanded:false}]}));
  const project=page.hooks.state.navigation.items[0];await page.hooks.navigateItem(project);
  assert.deepEqual(actions.map(a=>a.path),['/api/action']);assert.equal(actions[0].data.expanded,true);
  await page.hooks.navigateItem(project);await page.hooks.navigateItem(project);
  assert.equal(actions.length,1);assert.equal(page.hooks.state.navigationLoaded.p,true);
  assert.ok(page.elements.get('navigationList').textContent.includes('Saved chat'));
});

test('switching chats retains project catalogs and phone draft without an extra window read',async()=>{
  const actions=[];const page=makePage(async(path,request)=>{
    actions.push({path,data:JSON.parse(request.body)});return response({ok:true,epoch:8});
  });
  page.hooks.acceptNavigation(navigationEvent());page.elements.get('message').value='Unsent phone draft';
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.equal(actions.length,1);assert.equal(actions[0].path,'/api/action');
  assert.equal(page.hooks.state.navigation.epoch,8);assert.equal(page.hooks.state.navigation.items.length,3);
  assert.equal(page.elements.get('message').value,'Unsent phone draft');
  page.hooks.setNavigation(true);assert.equal(actions.length,1);
});

test('switching uses one HTTP action even when the control socket is ready',async()=>{
  const paths=[];const page=makePage(async path=>{paths.push(path);return response({ok:true,epoch:8});});
  page.hooks.state.control={ready:true,socket:{readyState:1,send(){assert.fail('navigation must not wait on the control socket');}}};
  page.hooks.acceptNavigation(navigationEvent());page.hooks.setNavigation(true);
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.deepEqual(paths,['/api/action']);
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.hooks.state.error,'');
});

test('a lost switch response reads the selected thread and never invokes it twice',async()=>{
  const paths=[];
  const catalog=navigationEvent();
  const page=makePage(async path=>{
    paths.push(path);
    if(path==='/api/action')throw new TypeError('socket reset after action');
    assert.equal(path,'/api/navigation');
    return response({...catalog,epoch:8,items:catalog.items.map(item=>item.id==='thread-a'?{...item,id:'new-runtime-id',selected:true}:item)});
  });
  page.hooks.acceptNavigation(catalog);page.hooks.setNavigation(true);
  page.elements.get('message').value='保留手机输入';
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.deepEqual(paths,['/api/action','/api/navigation']);
  assert.equal(page.hooks.state.activeNavigationId,'new-runtime-id');
  assert.equal(page.hooks.state.epoch,8);
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.hooks.state.error,'');
  assert.equal(page.elements.get('message').value,'保留手机输入');
});

test('a lost switch response stays unconfirmed when the selected thread differs',async()=>{
  const paths=[];
  const catalog=navigationEvent();
  const page=makePage(async path=>{
    paths.push(path);
    if(path==='/api/action')throw new TypeError('request was lost');
    return response(catalog);
  });
  page.hooks.acceptNavigation(catalog);page.hooks.setNavigation(true);
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.deepEqual(paths,['/api/action','/api/navigation']);
  assert.equal(page.hooks.state.panel,'navigation');
  assert.match(page.elements.get('navigationError').textContent,/切换结果待确认/);
});

test('new chat chooser lists projects and sends only the selected native new-chat target',async()=>{
  const actions=[];const page=makePage(async(path,request)=>{actions.push(JSON.parse(request.body));return response({ok:true,epoch:8});});
  page.hooks.acceptNavigation(navigationEvent());page.elements.get('message').value='Keep this';
  page.hooks.openNewChat();assert.equal(page.elements.get('navigationTitle').textContent,'新建对话');
  assert.equal(page.elements.get('navigationList').children.length,1);
  assert.ok(page.elements.get('navigationList').textContent.includes('Alpha'));
  await page.hooks.navigateItem(page.hooks.state.navigation.items[1]);
  assert.equal(actions.length,1);assert.equal(actions[0].targetId,'new-p');assert.equal(actions[0].kind,'navigate');
  assert.equal(page.elements.get('message').value,'Keep this');assert.equal(page.hooks.state.navigationNewDraft,true);
});

test('native work status is above the answer and never a copied message body or footer',()=>{
  const page=makePage();page.hooks.applyConversationEvent(chatEvent({messages:[
    {id:'m0',role:'assistant',text:'Answer',duration:'已处理 1分钟 27秒'},
    {id:'m1',role:'assistant',text:'',phase:'activity',duration:'已处理 2分钟'}],order:['m0','m1']}));
  const answer=page.hooks.state.chatElements.m0,status=page.hooks.state.chatElements.m1;
  assert.equal(answer.text,'Answer');assert.equal(answer.body.children[0].textContent,'已处理 1分钟 27秒');
  assert.equal(answer.body.children[1].textContent,'Answer');assert.equal(status.copy.hidden,true);
  assert.equal(status.body.textContent,'已处理 2分钟');assert.equal(status.foot.hidden,true);
  assert.equal(answer.foot.textContent.includes('已处理'),false);
});

test('process blocks collapse between visible commentary paragraphs and preserve manual expansion',()=>{
  const page=makePage();const blocks=[{kind:'paragraph',text:'检查输入',channel:'commentary'},
    {kind:'paragraph',text:'公开思考摘要',channel:'reasoning'},
    {kind:'operation',icon:'terminal',text:'运行了命令',channel:'activity'},
    {kind:'paragraph',text:'继续核对',channel:'commentary'},
    {kind:'search',text:'搜索网页',channel:'activity'}];
  const event=chatEvent({messages:[{id:'m0',role:'assistant',phase:'activity',text:'过程',blocks,
    complete:false,work:{status:'working'}}]});
  page.hooks.applyConversationEvent(event);const row=page.hooks.state.chatElements.m0;
  assert.equal(row.body.children.length,4);
  assert.equal(row.body.children[0].textContent,'检查输入');
  assert.equal(row.body.children[2].textContent,'继续核对');
  const first=row.body.children[1],second=row.body.children[3];
  assert.equal(first.tagName,'DETAILS');assert.equal(first.open,false);assert.equal(second.open,false);
  assert.equal(first.children[0].textContent,'工作过程 · 进行中');
  assert.equal(second.children[0].textContent,'工作过程 · 进行中');
  assert.deepEqual(first.children[1].children.map(node=>node.textContent),['公开思考摘要','运行了命令']);
  first.open=true;event.replace=false;event.messages[0].blocks[1].text='后续摘要';
  page.hooks.applyConversationEvent(event);
  assert.equal(row.body.children[1].open,true);assert.equal(row.body.children[3].open,false);
  event.messages[0].complete=true;event.messages[0].work={status:'worked',durationMs:65000};
  page.hooks.applyConversationEvent(event);
  assert.equal(row.body.children[1].open,true);assert.equal(row.body.children[3].open,false);
});

test('cached project children retain their observed catalog while other projects are read',()=>{
  const page=makePage();page.hooks.acceptNavigation(navigationEvent());page.hooks.state.navigationFold.p=true;
  page.hooks.acceptNavigation(navigationEvent({key:'nav-b',items:[{id:'p',kind:'project',title:'Alpha',project:'p',expanded:false}]}));
  assert.equal(page.hooks.state.navigation.items.find(i=>i.id==='thread-a').catalogKey,'nav-a');
  assert.equal(page.hooks.state.navigationFold.p,true);
  page.hooks.applyWindowState({...info,epoch:8});assert.equal(page.hooks.state.navigation,null);
});

test('selecting the already active native chat closes navigation without emptying the conversation',async()=>{
  const page=makePage(async()=>response({ok:true,epoch:7,navigationUnchanged:true}));
  page.hooks.acceptNavigation(navigationEvent());page.hooks.applyConversationEvent(chatEvent());
  const row=page.hooks.state.chatElements.m0,chat=page.hooks.state.chat;
  await page.hooks.navigateItem(page.hooks.state.navigation.items[2]);
  assert.equal(page.hooks.state.chat,chat);assert.equal(page.hooks.state.chatElements.m0,row);
});

test('an explicit refresh invalidates previously cached children of an unloaded project',()=>{
  const page=makePage();page.hooks.acceptNavigation(navigationEvent());page.hooks.state.navigationFold.p=true;
  page.hooks.acceptNavigation(navigationEvent({key:'nav-b',items:[{id:'p',kind:'project',title:'Alpha',project:'p',expanded:false}]}),true);
  assert.equal(page.hooks.state.navigation.items.length,1);assert.equal(page.hooks.state.navigationLoaded.p,false);
  assert.equal(page.hooks.state.navigationFold.p,false);
});

test('a lost action reply queries its receipt, sends once, and preserves the next draft',async()=>{
  let actions=0,lookups=0,id='';
  const page=makePage(async(path,options)=>{
    if(path==='/api/action'){actions++;id=JSON.parse(options.body).messageId;throw new Error('reply lost');}
    assert.ok(path.startsWith('/api/delivery?id='));lookups++;
    page.elements.get('message').value='下一条草稿';
    return response({ok:true,delivery:{id,state:'sent'}});
  });
  page.elements.get('message').value='继续';
  assert.equal(await page.hooks.sendText(true),true);
  assert.equal(actions,1);assert.equal(lookups,1);
  assert.equal(page.elements.get('message').value,'下一条草稿');
  assert.equal(page.hooks.state.error,'');assert.equal(page.storage.has('chatgpt-harmony-pending-send'),false);
});

test('a visible new message confirms a send even when the original response arrives as an error',async()=>{
  let rejectAction,actions=0;
  const page=makePage((path)=>{
    assert.equal(path,'/api/action');actions++;
    return new Promise((resolve,reject)=>{rejectAction=reject;});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());
  page.elements.get('message').value='继续';
  const sending=page.hooks.sendText(true);
  page.hooks.applyConversationEvent(chatEvent({order:['m0','m1'],messages:[{id:'m0',role:'assistant',text:'先前回复'},
    {id:'m1',role:'user',text:'继续',sourceId:'new-user'}]}));
  assert.equal(page.elements.get('message').value,'');
  rejectAction(new Error('late response failure'));
  assert.equal(await sending,true);assert.equal(actions,1);assert.equal(page.hooks.state.error,'');
});

test('an older identical continue message cannot clear a new send',()=>{
  const page=makePage();page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({order:['m0'],messages:[{id:'m0',role:'user',text:'继续',sourceId:'old'}]}));
  page.elements.get('message').value='继续';
  const task=page.hooks.beginPhoneSend({text:'继续',conversationKey:'chat-a',epoch:7});
  page.hooks.observePhoneSend(page.hooks.state.chat);
  assert.equal(task.state,'sending');assert.equal(page.elements.get('message').value,'继续');
  page.hooks.applyConversationEvent(chatEvent({order:['m0','m1'],messages:[{id:'m0',role:'user',text:'继续',sourceId:'old'},
    {id:'m1',role:'user',text:'继续',sourceId:'new'}]}));
  assert.equal(task.state,'sent');assert.equal(page.elements.get('message').value,'');
});

test('a queued send clears its original input and stays above the composer',()=>{
  const page=makePage();page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());
  page.elements.get('message').value='排队的消息';
  const task=page.hooks.beginPhoneSend({text:'排队的消息',conversationKey:'chat-a',epoch:7});
  page.hooks.applyConversationEvent(chatEvent({queued:[{id:'q-new',text:'排队的消息'}]}));
  assert.equal(task.state,'queued');assert.equal(page.elements.get('message').value,'');
  assert.equal(page.elements.get('queuedStrip').hidden,false);
  assert.equal(page.hooks.state.chat.messages.length,1);
});

test('a known send failure keeps editable text and gives its concrete cause',async()=>{
  const page=makePage(async(path,options)=>{
    const command=JSON.parse(options.body);
    return response({ok:true,delivery:{id:command.messageId,state:'failed',error:'未发送：电脑发送按钮不可用',code:'send_not_started'}});
  });
  page.elements.get('message').value='保留这条消息';
  assert.equal(await page.hooks.sendText(true),false);
  assert.equal(page.elements.get('message').value,'保留这条消息');
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.equal(page.hooks.state.error,'未发送：电脑发送按钮不可用');
  assert.equal(page.storage.has('chatgpt-harmony-pending-send'),false);
});

test('creating a first message can change identity but an unrelated existing conversation cannot confirm',()=>{
  const page=makePage();page.hooks.state.view='chat';
  page.hooks.applyConversationEvent(chatEvent({order:[],messages:[]}));page.elements.get('message').value='首条消息';
  const task=page.hooks.beginPhoneSend({text:'首条消息',conversationKey:'chat-a',epoch:7});
  page.hooks.applyConversationEvent(chatEvent({key:'created-chat',order:['m0'],messages:[{id:'m0',role:'user',text:'首条消息'}]}));
  assert.equal(task.state,'sent');
  page.elements.get('message').value='下一条';
  const next=page.hooks.beginPhoneSend({text:'下一条',conversationKey:'created-chat',epoch:7});
  page.hooks.applyConversationEvent(chatEvent({key:'unrelated',order:['m0'],messages:[{id:'m0',role:'user',text:'下一条',sourceId:'other'}]}));
  assert.equal(next.state,'sending');assert.equal(page.elements.get('message').value,'');
  page.hooks.applyConversationEvent(chatEvent({key:'created-chat'}));
  assert.equal(page.elements.get('message').value,'下一条');
});

test('a rejected old service instance releases send without waiting for a missing receipt',async()=>{
  const paths=[];
  const page=makePage(async(path)=>{
    paths.push(path);
    if(path==='/api/windows')return response({ok:true,epoch:7,instance:'new-instance',status:'ready',mode:'window',windows:[]});
    return response({ok:false,code:'delivery_instance',error:'连接已更新'},409);
  });
  page.elements.get('message').value='保留并重新发送';
  assert.equal(await page.hooks.sendText(true),false);
  assert.equal(page.elements.get('message').value,'保留并重新发送');
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
  assert.ok(!paths.some(path=>path.startsWith('/api/delivery')));
  assert.equal(page.storage.has('chatgpt-harmony-pending-send'),false);
});

test('reopening the page recovers the same receipt without submitting again',async()=>{
  let reads=0;
  const page=makePage(async(path)=>{
    assert.ok(path.startsWith('/api/delivery?id='));reads++;
    return response({ok:true,delivery:{id:'send_saved_1234567890',state:'sent'}});
  });
  page.storage.set('chatgpt-harmony-pending-send',JSON.stringify({id:'send_saved_1234567890',text:'继续',key:'chat-a',epoch:7,
    state:'confirming',sources:[],matches:0,empty:true,queueIds:[],attachmentIds:[]}));
  await page.hooks.resumePhoneSend();
  assert.equal(reads,1);assert.equal(page.elements.get('message').value,'');assert.equal(page.hooks.state.pending.sendText,false);
});

test('without an observed baseline an older message must wait for its server receipt',()=>{
  const page=makePage();page.elements.get('message').value='继续';
  const task=page.hooks.beginPhoneSend({text:'继续',conversationKey:'chat-a',epoch:7});
  page.hooks.applyConversationEvent(chatEvent({order:['m0'],messages:[{id:'m0',role:'user',text:'继续',sourceId:'old'}]}));
  assert.equal(task.state,'sending');assert.equal(page.elements.get('message').value,'继续');
});

test('a conversation acknowledgement releases send immediately while receipt lookup is slow',async()=>{
  let lookupStarted;
  const waiting=new Promise(resolve=>{lookupStarted=resolve;});
  const page=makePage(async(path,options)=>{
    if(path==='/api/action')return response({ok:true,delivery:{id:JSON.parse(options.body).messageId,state:'sending'}});
    lookupStarted();return new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new Error('cancelled lookup')),{once:true});});
  });
  page.hooks.state.view='chat';page.hooks.applyConversationEvent(chatEvent());page.elements.get('message').value='继续';
  const sending=page.hooks.sendText(true);await waiting;
  page.hooks.applyConversationEvent(chatEvent({order:['m0','m1'],messages:[{id:'m0',role:'assistant',text:'之前'},
    {id:'m1',role:'user',text:'继续',sourceId:'new'}]}));
  assert.equal(await Promise.race([sending,new Promise(resolve=>setTimeout(()=>resolve('too slow'),100))]),true);
  assert.equal(page.hooks.state.pending.sendText,false);
});

test('a request that never arrived is resolved before re-enabling send',async()=>{
  let actions=0,seals=0;
  const page=makePage(async(path,options)=>{
    if(path==='/api/action'){actions++;throw new Error('connection lost before arrival');}
    if(path.startsWith('/api/delivery?id='))return {ok:false,status:404,json:async()=>({error:'发送记录不存在'})};
    assert.equal(path,'/api/delivery/resolve');seals++;
    const data=JSON.parse(options.body);assert.equal(data.instance,'current-startup');
    return response({ok:true,delivery:{id:data.id,state:'failed',code:'send_not_received',error:'连接中断，点发送重试。'}});
  });
  page.hooks.state.instance='current-startup';page.elements.get('message').value='继续';
  assert.equal(await page.hooks.sendText(true),false);
  assert.equal(actions,1);assert.equal(seals,1);assert.equal(page.elements.get('message').value,'继续');
  assert.equal(page.elements.get('sendEnterBtn').disabled,false);
});

test('a prior startup receipt is recovered by the service without replaying the message',async()=>{
  const paths=[],id='send_previous_startup';let registered=false;
  const page=makePage(async(path,options)=>{
    paths.push(path);
    if(path==='/api/delivery/resolve'){
      const body=JSON.parse(options.body);registered=true;
      assert.equal(body.recovery.text,'原始文字');assert.equal(body.recovery.key,'chat-a');
      return response({ok:true,delivery:{id,state:'confirming'}});
    }
    assert.equal(path,'/api/delivery?id='+id);
    return registered?response({ok:true,delivery:{id,state:'sent'}}):{ok:false,status:404,json:async()=>({error:'missing'})};
  });
  page.hooks.state.instance='second';
  page.storage.set('chatgpt-harmony-pending-send',JSON.stringify({id,text:'原始文字',key:'chat-a',epoch:7,instance:'first',
    state:'confirming',sources:[],matches:0,empty:true,queueIds:[],attachmentIds:[]}));
  await page.hooks.resumePhoneSend();
  assert.equal(page.elements.get('message').value,'');
  assert.equal(page.elements.get('sendProgressText').textContent,'已发送');
  assert.equal(page.storage.has('chatgpt-harmony-pending-send'),false);
  assert.equal(paths.filter(path=>path==='/api/action').length,0);
});

function queueSnapshot(){
  return {ok:true,key:'observed-queue',epoch:7,paused:true,items:[{id:'q1',text:'待处理的原生消息',actions:[
    {id:'delete-q1',operation:'delete',enabled:true},{id:'edit-q1',operation:'edit',enabled:true},{id:'steer-q1',operation:'steer',enabled:true}]}],
    actions:[{id:'resume',operation:'resume',enabled:true}]};
}

test('the composer stops from an empty input and uses the same button to send a queued draft',async()=>{
  const actions=[];
  const paused=queueSnapshot();
  const running={...paused,paused:false,actions:[{id:'observed-stop',operation:'stop',enabled:true}]};
  const page=makePage(async(path,options)=>{
    assert.equal(path,'/api/action');
    const action=JSON.parse(options.body);actions.push(action);
    return response(action.kind==='queue'?{ok:true,epoch:7,queue:paused}:{ok:true,epoch:7,submission:{state:'queued'}});
  });
  const {hooks,elements}=page;
  const button=elements.get('sendEnterBtn'),message=elements.get('message');
  hooks.state.view='chat';
  hooks.applyConversationEvent(chatEvent({generating:true,queue:running}));
  assert.equal(button.getAttribute('data-mode'),'stop');
  assert.equal(button.getAttribute('aria-label'),'停止当前回复');
  assert.equal(elements.get('queueActions').children.length,0);
  assert.equal(await hooks.invokeComposerAction(),true);
  assert.equal(actions[0].kind,'queue');
  assert.equal(actions[0].targetId,'observed-stop');
  assert.equal(elements.get('queueActions').children[0].textContent,'继续');
  assert.equal(button.getAttribute('data-mode'),'send');
  assert.equal(button.disabled,true);
  hooks.applyConversationEvent(chatEvent({generating:true,queue:running,revision:'2'}));
  message.value='加入队列';hooks.renderControls();
  assert.equal(button.getAttribute('data-mode'),'send');
  assert.equal(await hooks.invokeComposerAction(),true);
  assert.equal(actions.length,2);
  assert.equal(actions[1].kind,'message');
  assert.equal(actions[1].text,'加入队列');
});

test('queue controls stay inline during receipt confirmation without navigating or blocking chat',async()=>{
  const page=makePage(async path=>{assert.equal(path,'/api/queue');return response(queueSnapshot());});
  page.hooks.state.pending.sendText=true;
  await page.hooks.openQueue();
  assert.equal(page.hooks.state.panel,'');
  assert.equal(page.historyEntries.length,1);
  assert.equal(page.elements.get('queuePanel').hidden,false);
  assert.equal(page.elements.get('queueStatus').hidden,true);
  assert.match(page.elements.get('queueItems').textContent,/待处理的原生消息/);
  assert.doesNotMatch(page.elements.get('queueItems').textContent,/编辑|删除排队消息|引导/);
  const actions=page.elements.get('queueItems').children[0].children[0].children[1].children;
  assert.deepEqual(actions.map(action=>action['aria-label']),['编辑排队消息','删除排队消息','引导']);
  assert.ok(actions.every(action=>action.children.length===1&&action.children[0].tagName==='SVG'));
  assert.equal(page.elements.get('queueItems').children[0].children.length,1);
  assert.equal(page.elements.get('queueActions').children[0].textContent,'继续');
});

test('queue edit uses one observed action and takes back the actual computer draft',async()=>{
  let count=0;
  const page=makePage(async(path,options)=>{
    assert.equal(path,'/api/action');count++;
    const command=JSON.parse(options.body);assert.equal(command.kind,'queue');assert.equal(command.queueKey,'observed-queue');assert.equal(command.targetId,'edit-q1');
    return response({ok:true,epoch:7,queue:{...queueSnapshot(),items:[]},
      queueDraft:{text:'电脑原生全文',conversationKey:'chat-a',epoch:7,revision:'revision',hasDraft:true}});
  });
  page.hooks.state.queue=queueSnapshot();
  assert.equal(await page.hooks.invokeQueue(page.hooks.state.queue.items[0].actions[1],page.hooks.state.queue.items[0]),true);
  assert.equal(count,1);assert.equal(page.elements.get('message').value,'电脑原生全文');
  assert.equal(page.hooks.state.boundDraft.revision,'revision');
  assert.equal(page.elements.get('computerDraftBtn').hidden,true);
  assert.equal(page.elements.get('draftConflict').hidden,true);
});

test('queue edit never overwrites an existing phone draft',async()=>{
  let count=0;const page=makePage(async()=>{count++;throw Error('must not invoke');});
  page.hooks.state.queue=queueSnapshot();page.elements.get('message').value='手机原稿';
  assert.equal(await page.hooks.invokeQueue(page.hooks.state.queue.items[0].actions[1],page.hooks.state.queue.items[0]),false);
  assert.equal(count,0);assert.equal(page.elements.get('message').value,'手机原稿');
});

test('a lost queue action reply is not replayed or reported successful',async()=>{
  let count=0;const page=makePage(async()=>{count++;throw Error('connection interrupted');});
  page.hooks.state.queue=queueSnapshot();
  assert.equal(await page.hooks.invokeQueue(page.hooks.state.queue.items[0].actions[0],page.hooks.state.queue.items[0]),false);
  assert.equal(count,1);assert.ok(page.hooks.state.queue);
  assert.match(page.elements.get('queueError').textContent,/待核对/);
  assert.equal(page.elements.get('queueItems').children[0].children[0].children[1].children.every(button=>button.disabled),true);
});

test('a queue catalog from an old window cannot invoke native controls',async()=>{
  let count=0;const page=makePage(async()=>{count++;throw Error('must not invoke');});
  page.hooks.state.queue=queueSnapshot();page.hooks.state.epoch=8;
  assert.equal(await page.hooks.invokeQueue(page.hooks.state.queue.items[0].actions[0],page.hooks.state.queue.items[0]),false);
  assert.equal(count,0);
});

test('a late queue read cannot restore another conversation queue',async()=>{
  let deliver;const page=makePage(()=>new Promise(resolve=>{deliver=resolve;}));
  const opening=page.hooks.openQueue();
  page.hooks.state.activeChatKey='other-conversation';deliver(response(queueSnapshot()));await opening;
  assert.equal(page.hooks.state.queue,null);assert.equal(page.elements.get('queuePanel').hidden,true);
});

test('unchanged inline queue retains its controls while other content renders',()=>{
  const page=makePage();page.hooks.state.queue=queueSnapshot();page.hooks.renderQueue();
  const item=page.elements.get('queueItems').children[0];
  page.hooks.renderControls();page.hooks.renderQueue();
  assert.equal(page.elements.get('queueItems').children[0],item);
});

test('late queue editing result cannot replace the draft in a different conversation',async()=>{
  let reply;const page=makePage(()=>new Promise(resolve=>{reply=resolve;}));
  page.hooks.state.queue=queueSnapshot();
  const action=page.hooks.invokeQueue(page.hooks.state.queue.items[0].actions[1],page.hooks.state.queue.items[0]);
  page.hooks.state.activeChatKey='another';page.elements.get('message').value='另一段对话草稿';
  reply(response({ok:true,epoch:7,queue:queueSnapshot(),queueDraft:{text:'旧消息',revision:'old'}}));
  assert.equal(await action,false);assert.equal(page.elements.get('message').value,'另一段对话草稿');
});

test('a full reconnect snapshot of the same conversation keeps rows and reading position',()=>{
  const page=makePage();page.hooks.state.view='chat';
  const event=chatEvent({replace:true,order:['a'],messages:[{id:'a',role:'assistant',text:'可见说明',phase:'commentary',complete:true}]});
  page.hooks.applyConversationEvent(event);
  const row=page.hooks.state.chatElements.a,list=page.elements.get('chatMessages');
  list.scrollHeight=2000;list.clientHeight=400;list.scrollTop=120;
  page.hooks.applyConversationEvent(event);
  assert.equal(page.hooks.state.chatElements.a,row);assert.equal(list.scrollTop,120);
  assert.equal(row.article['data-phase'],'commentary');assert.equal(row.body.children[0].tagName,'P');
});

test('the current commentary has visible inline text and stops pulsing when the final answer begins',()=>{
  const page=makePage();
  const step={id:'step',role:'assistant',text:'正在检查文件',phase:'commentary',complete:true};
  page.hooks.applyConversationEvent(chatEvent({generating:true,order:['step'],messages:[step]}));
  const row=page.hooks.state.chatElements.step;
  assert.equal(row.body.children[0].textContent,'正在检查文件');
  assert.equal(row.article['data-running'],'true');
  page.hooks.applyConversationEvent(chatEvent({generating:true,order:['step','answer'],messages:[step,{id:'answer',role:'assistant',text:'检查结果'}]}));
  assert.equal(row.article['data-running'],'false');
  assert.equal(page.hooks.state.chatElements.answer.article['data-phase'],'answer');
  assert.equal(page.hooks.state.chatElements.answer.article['data-running'],'false');
});
