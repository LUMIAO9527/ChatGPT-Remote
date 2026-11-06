import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
let html=readFileSync(new URL('../desktop.html',import.meta.url),'utf8');
for(const [marker,name] of [['MOTION','desktop-motion.js'],['PREFERENCES','desktop-preferences.js'],['LIFECYCLE','desktop-lifecycle.js']])html=html.replace('/*REMOTE_'+marker+'*/',readFileSync(new URL('../assets/'+name,import.meta.url),'utf8'));
html=html.replace('/*REMOTE_HOST*/','window.REMOTE_TRAY_HOST=false;');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const base={running:true,windows:[{id:1,title:'ChatGPT',selected:true}],devices:[{id:'phone',name:'手机'}],version:'0.16.0',computerName:'我的电脑',phoneUrl:'https://computer.example.ts.net:8443/',preferences:{theme:'system',material:'glass',motion:true,autoHide:true}};
const pair={pin:'123456',qr:'data:image/png;base64,example',expiresAt:Date.now()/1000+600};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(initial={},reply=null){
 const nodes=new Map(),listeners=new Map(),intervals=new Map();let id=0;
 const node=key=>{if(!nodes.has(key))nodes.set(key,{hidden:false,open:false,disabled:false,dataset:{},scrollTop:0,innerHTML:'',textContent:'',value:'',style:{setProperty(){}},classList:{toggle(){}},setAttribute(name,value){this[name]=value;},getAttribute(name){return this[name];},querySelectorAll(){return [];},getBoundingClientRect(){return {height:42};},addEventListener(name,fn){listeners.set(key+':'+name,fn);},close(){this.open=false;},showModal(){this.open=true;},focus(){},select(){}});return nodes.get(key);};
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
 return {context,node,calls,intervals,listeners,run:code=>vm.runInContext(code,context),click:(action,data={})=>listeners.get('click')({target:{closest:()=>({disabled:false,dataset:{action,...data},getAttribute(name){return this[name];},setAttribute(name,value){this[name]=value;}})}}),submit:()=>listeners.get('#dialog-form:submit')({preventDefault(){}})};
}
test('home directly shows pairing, one window and one phone',async()=>{
 const h=harness({status:structuredClone(base),panelVisible:true});await h.run('ensurePair()');
 const content=h.node('#main').innerHTML;
 assert.match(content,/123 456/);assert.match(content,/手机连接二维码/);assert.match(content,/控制窗口/);assert.match(content,/已配对设备/);
 assert.doesNotMatch(content,/data-action="(?:targets|devices|pair)"/);
 assert.equal(h.run('state.page'),'home');assert.match(content,/computer.example.ts.net/);assert.match(content,/我的电脑/);assert.doesNotMatch(content,/0\.16\.0/);
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


test('failed refresh retains cached content and hides its pairing code until status recovers',async()=>{
 const h=harness({status:structuredClone(base),pair,ready:true,panelVisible:true},action=>action==='status'?{ok:false,error:'连接失败'}:undefined);
 await h.run('refresh()');assert.equal(h.run('state.status.running'),true);assert.equal(h.run('state.statusStale'),true);
 assert.match(h.node('#main').innerHTML,/控制窗口|已配对设备/);assert.match(h.node('#main').innerHTML,/状态待确认/);
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
test('target selection preserves the numeric bridge identity',async()=>{
 const h=harness({status:structuredClone(base)});await h.click('select-target',{id:'42'});
 assert.equal(h.calls.find(x=>x.action==='target.select').payload.id,42);
});
test('device rename and removal wait for confirmation and preserve the exact device identity',async()=>{
 const h=harness({status:structuredClone(base)});await h.click('rename',{id:'phone'});
 assert.equal(h.calls.filter(x=>x.action==='device.rename').length,0);assert.equal(h.node('#dialog-input').value,'手机');
 h.node('#dialog-input').value='我的手机';await h.submit();
 const rename=h.calls.find(x=>x.action==='device.rename');assert.equal(rename.payload.id,'phone');assert.equal(rename.payload.name,'我的手机');
 await h.click('remove',{id:'phone'});assert.equal(h.calls.filter(x=>x.action==='device.remove').length,0);
 await h.submit();assert.equal(h.calls.find(x=>x.action==='device.remove').payload.id,'phone');
});
test('installing an update requires confirmation and a non-installable update opens the release page',async()=>{
 const h=harness({status:{...structuredClone(base),update:{status:'ready',installable:true,version:'0.17.0'}}});h.run("go('update')");
 assert.match(h.node('#main').innerHTML,/data-action="install-update"/);
 await h.click('install-update');assert.equal(h.calls.filter(x=>x.action==='updates.install').length,0);
 assert.equal(h.node('#dialog').open,true);await h.submit();assert.equal(h.calls.filter(x=>x.action==='updates.install').length,1);
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
 assert.equal(h.node('#main').innerHTML,content);assert.equal(h.node('#startup-loading').hidden,true);
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
