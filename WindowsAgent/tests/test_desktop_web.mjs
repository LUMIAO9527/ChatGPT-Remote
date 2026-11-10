import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
let html=readFileSync(new URL('../desktop.html',import.meta.url),'utf8');
for(const [marker,name] of [['MOTION','desktop-motion.js'],['PREFERENCES','desktop-preferences.js'],['LIFECYCLE','desktop-lifecycle.js']])html=html.replace('/*REMOTE_'+marker+'*/',readFileSync(new URL('../assets/'+name,import.meta.url),'utf8'));
html=html.replace('/*REMOTE_HOST*/','window.REMOTE_TRAY_HOST=false;');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const base={running:true,windows:[{id:1,title:'ChatGPT',selected:true}],devices:[{id:'phone',name:'手机'}],version:'0.16.0',computerName:'我的电脑',phoneUrl:'https://computer.example.ts.net:8443/',preferences:{theme:'system',material:'glass',motion:true,autoHide:true},autoStart:true};
const pair={pin:'123456',qr:'data:image/png;base64,example',expiresAt:Date.now()/1000+600};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(initial={},reply=null){
 const nodes=new Map(),listeners=new Map(),intervals=new Map();let id=0;
 const node=key=>{if(!nodes.has(key))nodes.set(key,{hidden:false,open:false,disabled:false,dataset:{},scrollTop:0,innerHTML:'',textContent:'',value:'',style:{setProperty(){}},classList:{toggle(){}},setAttribute(name,value){this[name]=value;},getAttribute(name){return this[name];},querySelectorAll(){return [];},getBoundingClientRect(){return {height:42};},addEventListener(name,fn){listeners.set(key+':'+name,fn);},close(){this.open=false;},show(){this.open=true;this.modal=false;},showModal(){this.open=true;this.modal=true;},focus(){},select(){},scrollIntoView(){},insertAdjacentHTML(){},contains(target){return !!target.inMenu;}});return nodes.get(key);};
 const calls=[];
 const context=vm.createContext({JSON,Date,Number,String,Math,Promise,Error,queueMicrotask,
  document:{hidden:false,documentElement:{dataset:{}},querySelector:node,querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners.set(name,fn)},
  matchMedia:()=>({matches:false,addEventListener(){}}),setTimeout:()=>1,clearTimeout(){},
  setInterval:fn=>{intervals.set(++id,fn);return id;},clearInterval:timer=>intervals.delete(timer),
  window:{addEventListener(){}}
 });
 vm.runInContext(source,context);
 context.window.pywebview={api:{call:async(action,payload)=>{calls.push({action,payload});if(reply){const result=reply(action,payload);if(result!==undefined)return result;}if(action==='status')return {ok:true,...structuredClone(base)};if(action==='pair')return {ok:true,...pair};if(action==='settings.get')return {ok:true,preferences:base.preferences,autoStart:true};return {ok:true};}}};
 context.initial={panelVisible:false,...initial};vm.runInContext('Object.assign(state,initial)',context);
 const click=(action,data={})=>{
  const button=node('[data-action='+action+']');
  button.dataset={action,...data};
  return listeners.get('click')({target:{closest:()=>button,inMenu:['rename','remove'].includes(action)}});
 };
 return {context,node,calls,intervals,listeners,run:code=>vm.runInContext(code,context),click,submitInline:()=>listeners.get('submit')({target:{dataset:{inlineForm:'name'}},preventDefault(){}}),submit:()=>listeners.get('#dialog-form:submit')({preventDefault(){}})};
}

function motionHarness(reduced=false){
 const frames=[],paint=[],media={matches:reduced,addEventListener(){}};
 const context=vm.createContext({window:{},matchMedia:()=>media,
  requestAnimationFrame:fn=>paint.push(fn),getComputedStyle:()=>({opacity:'1',transform:'none'})});
 vm.runInContext(readFileSync(new URL('../assets/desktop-motion.js',import.meta.url),'utf8'),context);
 const node={hidden:false,style:{},getBoundingClientRect(){return {height:this.hidden?0:80};},
  animate(keyframes,options){const animation={cancelled:false,cancel(){this.cancelled=true;}};frames.push({keyframes,options,animation});return animation;}};
 return {motion:context.window.RemoteMotion,node,frames,media,paint:()=>{while(paint.length)paint.shift()();}};
}

test('explicit app motion plays even when Windows reduces animations',()=>{
 const h=motionHarness(true);h.motion.setEnabled(true);h.motion.enter(h.node);
 assert.equal(h.frames.length,1);assert.equal(h.frames[0].animation.cancelled,false);
});

test('unset motion follows the system until the app choice is explicit',()=>{
 const h=motionHarness(true);h.motion.enter(h.node);assert.equal(h.frames.length,0);
 h.motion.setEnabled(true);h.motion.enter(h.node);assert.equal(h.frames.length,1);
});

test('disabled motion settles visibility without starting effects',()=>{
 const h=motionHarness();h.motion.setEnabled(false);h.motion.reveal(h.node,false);
 assert.equal(h.node.hidden,true);assert.equal(h.frames.length,0);
 h.motion.pop(h.node);assert.equal(h.node.hidden,false);assert.equal(h.frames.length,0);
});

test('turning motion off during a closing disclosure finishes the close',()=>{
 const h=motionHarness();h.motion.setEnabled(true);h.motion.reveal(h.node,false);
 assert.equal(h.node.hidden,false);h.motion.setEnabled(false);
 assert.equal(h.node.hidden,true);assert.equal(h.frames[0].animation.cancelled,true);
 assert.equal(h.node.style.height,'');
});

test('reversing a disclosure ignores the cancelled close completion',()=>{
 const h=motionHarness();h.motion.setEnabled(true);h.motion.reveal(h.node,false);
 const close=h.frames[0].animation;h.motion.reveal(h.node,true);
 close.onfinish();assert.equal(h.node.hidden,false);
 h.frames[1].animation.onfinish();assert.equal(h.node.hidden,false);assert.equal(h.node.style.height,'');
});

test('a hidden panel cannot start a queued opening animation',()=>{
 const h=motionHarness();h.motion.setEnabled(true);h.motion.open(h.node);h.motion.stop();h.paint();
 assert.equal(h.frames.length,0);
 h.motion.open(h.node);h.paint();assert.equal(h.frames.length,1);
});

test('reopening a popup cancels its pending disappearance',()=>{
 const h=motionHarness();h.motion.setEnabled(true);
 h.motion.dismiss(h.node,()=>{h.node.hidden=true;});const exit=h.frames[0].animation;
 h.motion.pop(h.node);exit.onfinish();assert.equal(h.node.hidden,false);
 assert.equal(h.node.style.pointerEvents,'');h.frames[1].animation.onfinish();assert.equal(h.node.hidden,false);
});

test('the appearance motion switch persists the app choice and updates immediately',async()=>{
 const h=harness({status:structuredClone(base),page:'settings',panelVisible:true});h.run('render()');
 assert.match(h.node('#main').innerHTML,/aria-label="界面动效"/);
 await h.click('motion');await flush();
 assert.equal(h.calls.findLast(x=>x.action==='settings.motion').payload.motion,false);
 assert.equal(h.run('document.documentElement.dataset.motion'),'off');
 await h.click('motion');await flush();
 assert.equal(h.calls.findLast(x=>x.action==='settings.motion').payload.motion,true);
 assert.equal(h.run('document.documentElement.dataset.motion'),'on');
});
test('home directly shows pairing, one window and one phone',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true});await h.run('ensurePair()');
 const content=h.node('#main').innerHTML;
 assert.match(content,/123 456/);assert.match(content,/手机连接二维码/);assert.match(content,/控制窗口/);assert.match(content,/已配对设备/);
 assert.doesNotMatch(content,/data-action="(?:targets|devices|pair)"/);
 assert.equal(h.run('state.page'),'home');assert.match(content,/computer.example.ts.net/);assert.doesNotMatch(content,/0\.16\.0/);
 assert.match(h.node('#homebar').innerHTML,/我的电脑/);assert.match(h.node('#homebar').innerHTML,/data-action="computer-name"/);
 assert.match(h.node('#homebar').innerHTML,/service-pill" data-tone="ok"/);
 assert.equal(h.node('#homebar').hidden,false);assert.equal(h.node('#heading').hidden,true);
 assert.equal(h.node('#footer').hidden,true);assert.equal(h.node('#footer-state').hidden,false);
});

test('service startup is single-flight and keeps connection sections available through pairing',async()=>{
 let finish;
 const stopped={...structuredClone(base),running:false,windows:[],phoneUrl:''};
 const h=harness({status:stopped,panelVisible:true},action=>action==='service.start'?new Promise(resolve=>finish=resolve):undefined);
 h.run('render()');
 assert.match(h.node('#main').innerHTML,/connection-line/);
 assert.match(h.node('#main').innerHTML,/window-placeholder/);
 const pending=h.click('start');
 await flush();
 assert.equal(h.run('state.starting'),true);assert.equal(h.run('state.busy'),true);
 assert.match(h.node('#main').innerHTML,/启动中/);
 await h.click('start');assert.equal(h.calls.filter(row=>row.action==='service.start').length,1);
 finish({ok:true,...structuredClone(base)});await pending;await flush();
 assert.equal(h.run('state.starting'),false);assert.equal(h.run('state.busy'),false);
 assert.equal(h.run('state.status.running'),true);
 assert.match(h.node('#main').innerHTML,/connection-line/);
 assert.match(h.node('#main').innerHTML,/控制窗口 · 已连接/);
 assert.equal(h.calls.filter(row=>row.action==='pair').length,1);
});

test('a failed start preserves device rows, explains the failure and permits retry',async()=>{
 let attempts=0;
 const stopped={...structuredClone(base),running:false,windows:[],phoneUrl:''};
 const h=harness({status:stopped,panelVisible:true},action=>action==='service.start'?
  ++attempts===1?{ok:false,error:'后台启动失败，请检查连接'}:{ok:true,...structuredClone(base)}:undefined);
 await h.click('start');
 assert.equal(h.run('state.status.running'),false);assert.equal(h.run('state.starting'),false);
 assert.equal(h.run('state.busy'),false);assert.equal(h.run('state.status.devices.length'),1);
 assert.match(h.node('#footer-state').textContent,/后台启动失败/);
 assert.match(h.node('#main').innerHTML,/data-action="start"/);
 assert.equal(h.calls.filter(row=>row.action==='pair').length,0);
 await h.click('start');await flush();
 assert.equal(h.run('state.status.running'),true);assert.equal(h.run('state.startError'),'');
 assert.equal(attempts,2);
});

test('the header reports service state and the failure reason stays beside the action',async()=>{
 const stopped={...structuredClone(base),running:false,windows:[],phoneUrl:''};
 const h=harness({status:stopped,panelVisible:true},action=>action==='service.start'?{ok:false,error:'后台启动失败，请检查连接'}:undefined);
 h.run('render()');
 assert.equal(h.node('#footer').hidden,true);
 assert.match(h.node('#homebar').innerHTML,/service-pill" data-tone="off"/);
 await h.click('start');
 assert.match(h.node('#homebar').innerHTML,/data-tone="error"/);
 assert.match(h.node('#homebar').innerHTML,/启动失败/);
 const content=h.node('#main').innerHTML;
 assert.match(content,/后台启动失败，请检查连接/);
 assert.match(content,/重试启动/);
 assert.doesNotMatch(content,/启动后生成配对码/);
});
test('subpages keep the plain page title instead of the computer identity',()=>{
 const h=harness({status:structuredClone(base)});h.run("go('settings')");
 assert.equal(h.node('#homebar').hidden,true);assert.equal(h.node('#heading').hidden,false);
 assert.equal(h.node('#heading').textContent,'设置');assert.equal(h.node('#footer').hidden,true);
});
test('the computer display name can be edited and pairing code copied from the card',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true,pair});
 h.run('render()');const content=h.node('#main').innerHTML;
 assert.match(h.node('#homebar').innerHTML,/data-action="computer-name"/);assert.match(content,/复制配对码/);
 await h.click('computer-name');assert.equal(h.run('state.inline.value'),'我的电脑');
 h.node('#inline-name').value='书房电脑';await h.submitInline();
 assert.equal(h.calls.find(row=>row.action==='settings.computerName').payload.computerName,'书房电脑');
 await h.click('copy-code');assert.equal(h.calls.find(row=>row.action==='pair.copy').payload.pin,pair.pin);
 h.run('state.pair.expiresAt=Date.now()/1000+60;updateExpiry()');
 assert.match(h.node('#pair-expiry').textContent,/^(59|60) 秒有效$/);
});

test('name editing and device removal stay within their original rows',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true,pair});
 await h.click('computer-name');
 assert.equal(h.run('state.inline.kind'),'computer');assert.equal(h.node('#dialog').open,false);
 h.listeners.get('keydown')({key:'Escape',preventDefault(){}});
 assert.equal(h.run('state.inline'),null);
 assert.equal(h.calls.filter(row=>row.action==='settings.computerName').length,0);
 await h.click('device-menu',{id:'phone'});await h.click('remove');
 assert.equal(h.run('state.inline.kind'),'remove');assert.equal(h.node('#dialog').open,false);
 assert.match(h.node('#main').innerHTML,/移除后需重新配对/);
 assert.equal(h.calls.filter(row=>row.action==='device.remove').length,0);
 await h.click('cancel-inline');
 assert.equal(h.run('state.inline'),null);
 assert.equal(h.calls.filter(row=>row.action==='device.remove').length,0);
});

test('a failed inline name save keeps the typed value available for correction',async()=>{
 let attempts=0;
 const h=harness({status:structuredClone(base),panelVisible:true,pair},action=>{
  if(action==='settings.computerName'&&++attempts===1)return {ok:false,error:'保存未完成'};
 });
 await h.click('computer-name');h.node('#inline-name').value='工作电脑';
 await h.submitInline();
 assert.equal(h.run('state.inline.value'),'工作电脑');
 assert.equal(h.run('state.inline.error'),'保存未完成');
 assert.equal(h.run('state.inline.saving'),false);
 await h.submitInline();
 assert.equal(attempts,2);assert.equal(h.run('state.inline'),null);
});

test('tray header has settings and no window close control',()=>{
 const header=html.match(/<header[\s\S]*?<\/header>/)[0];
 assert.match(header,/aria-label="设置"/);assert.doesNotMatch(header,/data-action="(?:close|minimize)"/);
});
test('settings lookup never blocks appearance controls or replaces the page on completion',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base)},action=>action==='settings.get'?deferred:undefined);
 const pending=h.run('openSettings()');const content=h.node('#main').innerHTML;
 assert.equal(h.run('state.busy'),false);assert.match(content,/开机自启/);assert.doesNotMatch(content,/允许手机控制|复制诊断|界面动画/);
 finish({ok:true,preferences:base.preferences,autoStart:true});await pending;
 assert.equal(h.node('#main').innerHTML,content);assert.equal(h.node('[data-action=autostart]').hidden,false);
});
test('settings reuses recent startup state',async()=>{
 const h=harness({status:structuredClone(base)});await h.run('openSettings()');h.run('back()');await h.run('openSettings()');
 assert.equal(h.calls.filter(x=>x.action==='settings.get').length,1);
});
test('status reads are coalesced',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base),ready:true,panelVisible:true,pair},action=>action==='status'?deferred:undefined);
 const first=h.run('refresh()'),second=h.run('refresh()');assert.equal(h.calls.filter(x=>x.action==='status').length,1);
 finish({ok:true,...base});await Promise.all([first,second]);
});
test('hiding cancels timers and a late pairing response',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base),panelVisible:true},action=>action==='pair'?deferred:undefined);
 h.run('startPolling()');const pending=h.run('ensurePair()');h.run('window.remoteHidden()');
 assert.equal(h.intervals.size,0);finish({ok:true,...pair});await pending;assert.equal(h.run('state.pair'),null);
});
test('reopening always resets home and settings disclosures',()=>{
 const h=harness({status:structuredClone(base),page:'settings',appearanceOpen:true,panelVisible:true});h.node('#main').scrollTop=65;
 h.run('window.remoteHidden()');h.run('window.remoteShown()');
 assert.equal(h.run('state.page'),'home');assert.equal(h.run('state.appearanceOpen'),false);assert.equal(h.node('#main').scrollTop,0);assert.equal(h.intervals.size,2);
});
test('backend status does not delay native ready',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({},action=>action==='status'?deferred:undefined);const pending=h.run('initialize()');
 assert.equal(h.calls.filter(x=>x.action==='window.ready').length,1);
 finish({ok:true,...base});await pending;
});
test('prewarmed hidden panel does not generate a pairing code',async()=>{
 const h=harness({status:structuredClone(base)});await h.run('ensurePair()');assert.equal(h.calls.length,0);
});
test('preference changes update immediately while writes remain serialized',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);let writes=0;
 const h=harness({status:structuredClone(base)},action=>action==='settings.theme'?(++writes===1?deferred:Promise.resolve({ok:true})):undefined);
 h.run("prefWrites.set('theme','dark')");assert.equal(h.run('state.status.preferences.theme'),'dark');
 await flush();h.run("prefWrites.set('theme','light')");assert.equal(h.run('state.status.preferences.theme'),'light');
 assert.equal(writes,1);finish({ok:true});await flush();assert.equal(writes,2);assert.equal(h.run('state.status.preferences.theme'),'light');
});
test('a status read started before a preference write cannot undo it',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base),ready:true,panelVisible:true,pair},action=>action==='status'?deferred:undefined);
 const pending=h.run('refresh()');h.run("prefWrites.set('theme','dark')");await flush();
 finish({ok:true,...structuredClone(base)});await pending;assert.equal(h.run('state.status.preferences.theme'),'dark');
});
test('native material follows the current choice before the settings write finishes',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const initial=structuredClone(base);initial.preferences.material='solid';
 const h=harness({status:initial},action=>action==='settings.material'?deferred:undefined);
 h.run("window.REMOTE_TRAY_HOST=true;prefWrites.set('material','glass')");
 assert.equal(h.calls.findLast(x=>x.action==='window.appearance').payload.material,'glass');
 await flush();finish({ok:true});await flush();
 assert.equal(h.run('state.status.preferences.material'),'glass');
});
test('failed status has a reconnect action',async()=>{
 const h=harness({ready:true,panelVisible:true},action=>action==='status'?Promise.resolve({ok:false,error:'连接失败'}):undefined);
 await h.run('refresh()');assert.match(h.node('#main').innerHTML,/data-action="retry"/);
});
test('failed pairing has a regeneration button on home',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true},action=>action==='pair'?Promise.resolve({ok:false,error:'无法生成配对码'}):undefined);
 await h.run('ensurePair()');assert.equal(h.run('state.pairError'),true);assert.match(h.node('#main').innerHTML,/data-action="renew-pair"/);
});
test('support exposes project, feedback and contact actions',()=>{
 const h=harness({status:structuredClone(base)});h.run("go('support')");const content=h.node('#main').innerHTML;
 for(const action of ['support-qr','issues','copy-qq'])assert.match(content,new RegExp('data-action="'+action+'"'));
});

test('leaving and returning to settings collapses appearance',()=>{
 const h=harness({status:structuredClone(base),page:'settings',appearanceOpen:true});
 h.run("go('support')");assert.equal(h.run('state.appearanceOpen'),false);h.run('back()');
 assert.equal(h.run('state.page'),'settings');assert.equal(h.run('state.appearanceOpen'),false);
});
test('version is confined to the update page',()=>{
 const h=harness({status:structuredClone(base)});h.run("go('update')");
 assert.match(h.node('#main').innerHTML,/当前版本 0\.16\.0/);assert.match(h.node('#main').innerHTML,/GitHub 项目/);
});

test('current release uses a single description with the executable version',()=>{
 const h=harness({status:{...structuredClone(base),update:{status:'current',current:'1.0.0',version:'1.0.0',notes:'Old release notes'}}});
 h.run("go('update')");
 const content=h.node('#main').innerHTML;
 assert.match(content,/当前版本 1\.0\.0/);
 assert.match(content,/当前已是最新版本/);
 assert.doesNotMatch(content,/data-action="update-notes"|Old release notes|已检查 GitHub 正式版/);
});

test('the updater finishes independently and a delayed agent snapshot cannot overwrite it',async()=>{
 const current={status:'current',current:'1.0.0',version:'1.0.0',installable:true};
 const available={...current,status:'available',version:'1.0.1',available:true,notes:'<script>text only</script>'};
 let finishStatus, release=current;
 const oldAgent=new Promise(resolve=>{finishStatus=resolve;});
 const h=harness({status:{...structuredClone(base),update:current},ready:true,panelVisible:true},action=>{
  if(action==='status')return oldAgent;
  if(action==='updates.status')return {ok:true,update:release};
  if(action==='updates.check'){
   release={...current,status:'checking'};
   return {ok:true,update:release};
  }
 });
 const staleRead=h.run('refresh()');
 h.run("go('update')");await flush();
 await h.click('check-update');
 assert.match(h.node('#main').innerHTML,/正在检查…/);
 release=available;
 await h.run('refresh()');
 finishStatus({ok:true,...structuredClone(base),update:current});await staleRead;
 const content=h.node('#main').innerHTML;
 assert.match(content,/data-action="download-update"/);
 assert.match(content,/版本 1\.0\.1 · 更新说明/);
 assert.match(content,/&lt;script&gt;text only&lt;\/script&gt;/);
 assert.doesNotMatch(content,/class="update-description"/);
 assert.equal(h.calls.filter(x=>x.action==='status').length,1);
});


test('failed refresh retains cached content and hides its pairing code until status recovers',async()=>{
 const h=harness({status:structuredClone(base),pair,ready:true,panelVisible:true},action=>action==='status'?{ok:false,error:'连接失败'}:undefined);
 await h.run('refresh()');assert.equal(h.run('state.status.running'),true);assert.equal(h.run('state.statusStale'),true);
 assert.match(h.node('#main').innerHTML,/控制窗口|已配对设备/);assert.match(h.node('#main').innerHTML,/连接暂时中断/);
 assert.doesNotMatch(h.node('#main').innerHTML,/123 456|data:image\/png/);
 assert.equal(h.node('#footer-state').textContent,'状态待确认');assert.equal(h.node('#status-retry').hidden,false);
 await h.run('ensurePair()');assert.equal(h.calls.filter(x=>x.action==='pair').length,0);
});
test('cached bridge status stays unconfirmed until a fresh status restores the original home',async()=>{
 let stale=true;
 const h=harness({status:structuredClone(base),ready:true,panelVisible:true},action=>action==='status'?{ok:true,...structuredClone(base),stale}:undefined);
 await h.run('refresh()');assert.equal(h.run('state.statusStale'),false);assert.equal(h.run('state.status.stale'),true);
 assert.equal(h.node('#footer-state').textContent,'状态待确认');assert.equal(h.calls.filter(x=>x.action==='pair').length,0);
 stale=false;await h.run('refresh()');await flush();
 assert.equal(h.run('state.status.stale'),false);assert.equal(h.node('#footer-state').textContent,'本机服务运行中');
 assert.equal(h.node('#status-retry').hidden,true);assert.match(h.node('#main').innerHTML,/123 456/);
 assert.equal(h.calls.filter(x=>x.action==='pair').length,1);
});
test('malformed or expired pairing responses never render an unusable PIN or external image',async()=>{
 const invalid=[{...pair,pin:'bad'},{...pair,qr:'https://third-party.example/image'},{...pair,expiresAt:Date.now()/1000-1}];
 for(const result of invalid){
  const h=harness({status:structuredClone(base),panelVisible:true},action=>action==='pair'?{ok:true,...result}:undefined);
  await h.run('ensurePair()');assert.equal(h.run('state.pair'),null);assert.equal(h.run('state.pairError'),true);
  assert.doesNotMatch(h.node('#main').innerHTML,/third-party|123 456/);assert.match(h.node('#main').innerHTML,/data-action="renew-pair"/);
 }
});
test('a failed renewal clears the expired PIN and waits for an explicit retry',async()=>{
 const h=harness({status:structuredClone(base),pair:{...pair,expiresAt:Date.now()/1000-1},panelVisible:true},action=>action==='pair'?{ok:false,error:'无法生成配对码'}:undefined);
 await h.run('ensurePair(true)');h.run('updateExpiry()');await flush();
 assert.equal(h.run('state.pair'),null);assert.equal(h.run('state.pairError'),true);assert.equal(h.calls.filter(x=>x.action==='pair').length,1);
});

test('renewal keeps a still-valid code visible until the replacement arrives',async()=>{
 let finish;
 const h=harness({status:structuredClone(base),pair,panelVisible:true},action=>action==='pair'?new Promise(resolve=>finish=resolve):undefined);
 h.run('render()');const pending=h.run('ensurePair(true)');
 assert.equal(h.run('state.pair.pin'),pair.pin);assert.match(h.node('#main').innerHTML,/123 456/);
 assert.equal(h.run('state.pairLoading'),true);
 finish({ok:true,...pair,pin:'654321'});await pending;
 assert.equal(h.run('state.pair.pin'),'654321');assert.match(h.node('#main').innerHTML,/654 321/);
});

test('a delayed pairing response does not take focus back from another window',async()=>{
 let finish,focusCalls=0;
 const h=harness({status:structuredClone(base),pair,panelVisible:true},action=>action==='pair'?new Promise(resolve=>finish=resolve):undefined);
 const button=h.node('[data-action=renew-pair]');button.dataset={action:'renew-pair'};button.isConnected=false;button.focus=()=>{focusCalls++;};
 h.context.refreshNode=button;
 h.run("document.body={};document.activeElement=refreshNode;document.hasFocus=()=>true;document.querySelectorAll=selector=>selector==='[data-action]'?[refreshNode]:[]");
 const pending=h.run('ensurePair(true)');
 h.run('document.activeElement=document.body;document.hasFocus=()=>false');
 finish({ok:true,...pair,pin:'654321'});await pending;
 assert.equal(focusCalls,0);assert.equal(h.run('state.pair.pin'),'654321');
});

test('an inline save failure does not replace a newer focus choice',async()=>{
 let finish,focusCalls=0;
 const h=harness({status:structuredClone(base),panelVisible:true,pair},action=>action==='settings.computerName'?new Promise(resolve=>finish=resolve):undefined);
 await h.click('computer-name');const input=h.node('#inline-name');input.value='new name';input.focus=()=>{focusCalls++;};
 const pending=h.submitInline();
 h.run("document.body={};document.activeElement={isConnected:true,dataset:{action:'copy-code'}};document.hasFocus=()=>true");
 finish({ok:false,error:'暂时不可用'});await pending;
 assert.equal(focusCalls,0);assert.equal(h.run('state.inline.error'),'暂时不可用');
});

test('renewal failures retain a valid code and hide it as soon as it expires',async()=>{
 const h=harness({status:structuredClone(base),pair:structuredClone(pair),panelVisible:true},action=>action==='pair'?{ok:false,error:'网络暂时中断'}:undefined);
 await h.run('ensurePair(true)');
 assert.equal(h.run('state.pair.pin'),pair.pin);assert.match(h.node('#main').innerHTML,/123 456/);
 h.run('state.pair.expiresAt=Date.now()/1000-1;updateExpiry()');
 assert.equal(h.run('state.pair'),null);assert.doesNotMatch(h.node('#main').innerHTML,/123 456/);
 assert.equal(h.calls.filter(row=>row.action==='pair').length,1);
});

test('an expired code is hidden while its replacement request is pending',async()=>{
 let finish;
 const h=harness({status:structuredClone(base),pair:structuredClone(pair),panelVisible:true},action=>action==='pair'?new Promise(resolve=>finish=resolve):undefined);
 const pending=h.run('ensurePair(true)');
 h.run('state.pair.expiresAt=Date.now()/1000-1;updateExpiry()');
 assert.equal(h.run('state.pair'),null);assert.doesNotMatch(h.node('#main').innerHTML,/123 456/);
 assert.equal(h.calls.filter(row=>row.action==='pair').length,1);
 finish({ok:true,...pair,pin:'654321'});await pending;assert.equal(h.run('state.pair.pin'),'654321');
});

test('empty inline names explain the error beside the input without submitting',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true,pair});
 await h.click('computer-name');h.node('#inline-name').value='   ';await h.submitInline();
 assert.equal(h.calls.filter(row=>row.action==='settings.computerName').length,0);
 assert.match(h.node('#homebar').innerHTML,/aria-describedby="inline-name-error"/);
 assert.match(h.node('#homebar').innerHTML,/id="inline-name-error"[^>]*role="alert"[^>]*>请输入名称/);
 assert.equal(h.node('#footer').hidden,true);
});

test('failed removal retains the device and provides an inline retry',async()=>{
 let fail=true;
 const h=harness({status:structuredClone(base),panelVisible:true,pair},action=>action==='device.remove'?(fail?{ok:false,error:'连接中断'}:{ok:true}):undefined);
 await h.click('device-menu',{id:'phone'});await h.click('remove');await h.click('confirm-remove');
 assert.equal(h.run('state.status.devices.length'),1);assert.equal(h.run('state.inline.kind'),'remove');
 assert.match(h.node('#main').innerHTML,/role="alert"[^>]*>连接中断/);
 fail=false;await h.click('confirm-remove');assert.equal(h.run('state.status.devices.length'),0);
 assert.equal(h.calls.filter(row=>row.action==='device.remove').length,2);
});

test('inline device actions wait for a confirmed connection before submitting',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true,pair});
 await h.click('device-menu',{id:'phone'});await h.click('remove');h.run('state.statusStale=true');await h.click('confirm-remove');
 assert.equal(h.calls.filter(row=>row.action==='device.remove').length,0);
 assert.equal(h.run('state.status.devices.length'),1);assert.match(h.node('#main').innerHTML,/连接恢复后再试/);
});

test('a status read begun before removal cannot resurrect the removed device',async()=>{
 let finish;
 const oldStatus=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base),panelVisible:true,ready:true,pair},action=>action==='status'?oldStatus:undefined);
 const pending=h.run('refresh()');
 await h.click('device-menu',{id:'phone'});await h.click('remove');await h.click('confirm-remove');
 assert.equal(h.run('state.status.devices.length'),0);
 finish({ok:true,...structuredClone(base)});await pending;
 assert.equal(h.run('state.status.devices.length'),0);assert.doesNotMatch(h.node('#main').innerHTML,/data-id="phone"/);
});

test('a stale service snapshot cannot undo a successful service start',async()=>{
 let finish;
 const stopped={...structuredClone(base),running:false};
 const h=harness({status:stopped,panelVisible:true,ready:true},action=>action==='status'?new Promise(resolve=>finish=resolve):action==='service.start'?{ok:true,...structuredClone(base)}:undefined);
 const pending=h.run('refresh()');await h.click('start');
 finish({ok:true,...stopped});await pending;await flush();
 assert.equal(h.run('state.status.running'),true);
});

test('multiple windows use a separate chooser and leave the status band compact',async()=>{
 const windows=[{id:1,title:'窗口一',selected:true},{id:2,title:'窗口二'},{id:3,title:'窗口三'}];
 const h=harness({status:{...structuredClone(base),windows},panelVisible:true,pair});h.run('render()');
 assert.match(h.node('#main').innerHTML,/data-action="choose-target"/);
 assert.doesNotMatch(h.node('#main').innerHTML,/data-action="select-target"/);
 await h.click('choose-target');assert.equal(h.node('#dialog').open,true);
 assert.equal((h.node('#dialog-media').innerHTML.match(/role="radio"/g)||[]).length,3);
 assert.equal(h.node('#dialog button[type=submit]').hidden,true);
});
test('target selection preserves the numeric bridge identity',async()=>{
 const h=harness({status:structuredClone(base)});await h.click('select-target',{id:'42'});
 assert.equal(h.calls.find(x=>x.action==='target.select').payload.id,42);
});
test('device rename and removal wait for confirmation and preserve the exact device identity',async()=>{
 const h=harness({status:structuredClone(base)});await h.click('device-menu',{id:'phone'});await h.click('rename');
 assert.equal(h.calls.filter(x=>x.action==='device.rename').length,0);assert.equal(h.run('state.inline.value'),'手机');
 h.node('#inline-name').value='我的手机';await h.submitInline();
 const rename=h.calls.find(x=>x.action==='device.rename');assert.equal(rename.payload.id,'phone');assert.equal(rename.payload.name,'我的手机');
 await h.click('device-menu',{id:'phone'});await h.click('remove');assert.equal(h.calls.filter(x=>x.action==='device.remove').length,0);
 await h.click('confirm-remove');assert.equal(h.calls.find(x=>x.action==='device.remove').payload.id,'phone');
});
test('a verified update starts once without a second confirmation',async()=>{
 let finish;const reply=new Promise(resolve=>finish=resolve);
 const h=harness({status:{...structuredClone(base),update:{status:'ready',installable:true,version:'0.17.0'}}},action=>action==='updates.install'?reply:undefined);h.run("go('update')");
 assert.match(h.node('#main').innerHTML,/data-action="install-update"/);
 const install=h.click('install-update');await h.click('install-update');
 assert.equal(h.node('#dialog').open,false);
 assert.equal(h.calls.filter(x=>x.action==='updates.install').length,1);
 assert.match(h.node('#main').innerHTML,/正在重启…/);
 finish({ok:true});await install;
});
test('an install failure is inline and permits retrying the verified update',async()=>{
 const h=harness({status:{...structuredClone(base),update:{status:'ready',installable:true,version:'0.17.0'}}},
   action=>action==='updates.install'?{ok:false,error:'请等待当前发送完成'}:undefined);
 h.run("go('update')");await h.click('install-update');
 assert.equal(h.node('#dialog').open,false);
 assert.match(h.node('#main').innerHTML,/请等待当前发送完成/);
 assert.match(h.node('#main').innerHTML,/重启并更新/);
});
test('a non-installable update opens the release page',async()=>{
 const source=harness({status:{...structuredClone(base),installation:{sourceMode:true},update:{status:'ready',installable:false}}});source.run("go('update')");
 assert.doesNotMatch(source.node('#main').innerHTML,/data-action="install-update"/);assert.match(source.node('#main').innerHTML,/data-action="releases"/);
 await source.click('install-update');assert.equal(source.calls.filter(x=>x.action==='updates.install').length,0);
});
test('source mode has no installer action and an empty installation path cannot be submitted',async()=>{
 const source=harness({status:{...structuredClone(base),installation:{sourceMode:true,current:'WindowsAgent'}}});source.run("go('install')");
 assert.match(source.node('#main').innerHTML,/源码运行/);assert.doesNotMatch(source.node('#main').innerHTML,/data-action="install-program"/);
 const h=harness({status:{...structuredClone(base),installation:{sourceMode:false}}});h.node('#install-path').value='   ';
 await h.click('install-program');assert.equal(h.calls.filter(x=>x.action==='installation.install').length,0);
});
test('uninstall keeps data by default and source mode exposes no executable removal action',async()=>{
 const h=harness({status:{...structuredClone(base),installation:{sourceMode:false}}});h.run("go('uninstall')");
 await h.click('uninstall-program');assert.equal(h.calls.filter(x=>x.action==='uninstall').length,0);await h.submit();
 assert.equal(h.calls.find(x=>x.action==='uninstall').payload.keep,true);
 const source=harness({status:{...structuredClone(base),installation:{sourceMode:true}}});source.run("go('uninstall')");
 assert.doesNotMatch(source.node('#main').innerHTML,/data-action="uninstall-program"/);
});
test('settings uses nested preferences and respects disabled motion without rebuilding the page',async()=>{
 const latest={theme:'dark',material:'solid',motion:false,autoHide:false};
 const h=harness({status:structuredClone(base)},action=>action==='settings.get'?{ok:true,preferences:latest,autoStart:true}:undefined);
 const pending=h.run('openSettings()'),content=h.node('#main').innerHTML;await pending;
 assert.equal(h.run('state.status.preferences.theme'),'dark');assert.equal(h.run('state.status.preferences.material'),'solid');
 assert.equal(h.run('state.status.preferences.motion'),false);assert.equal(h.run('document.documentElement.dataset.motion'),'off');
 assert.equal(h.run('state.status.preferences.autoHide'),false);assert.equal(h.run('state.settings.autoStart'),true);
 assert.equal(h.node('#main').innerHTML,content);assert.equal(h.node('[data-action=autostart]').hidden,false);assert.equal(h.node('[data-action=autostart]').getAttribute('aria-checked'),'true');
});
test('a delayed settings read cannot undo newer appearance and startup writes',async()=>{
 let finish;const deferred=new Promise(resolve=>finish=resolve);
 const h=harness({status:structuredClone(base)},action=>action==='settings.get'?deferred:undefined);
 const pending=h.run('openSettings()');h.run("prefWrites.set('theme','dark');prefWrites.set('autoStart',true)");await flush();
 finish({ok:true,preferences:structuredClone(base.preferences),autoStart:false});await pending;
 assert.equal(h.run('state.status.preferences.theme'),'dark');assert.equal(h.run('state.settings.autoStart'),true);
});
test('operation settling preserves import and update capability restrictions',()=>{
 const h=harness({status:{...structuredClone(base),update:{status:'downloading',installable:true,progress:43}}});
 h.run("go('first-use')");assert.match(h.node('#main').innerHTML,/data-action="import-legacy"[^>]*data-unavailable="true"[^>]*disabled/);
 h.run("go('update')");assert.match(h.node('#main').innerHTML,/data-operation data-unavailable="true" disabled/);
 h.run("globalThis.restrictedButton={dataset:{unavailable:'true'},disabled:true};globalThis.allowedButton={dataset:{},disabled:false};document.querySelectorAll=()=>[restrictedButton,allowedButton];setBusy(true);setBusy(false)");
 assert.equal(h.run('restrictedButton.disabled'),true);assert.equal(h.run('allowedButton.disabled'),false);
});
