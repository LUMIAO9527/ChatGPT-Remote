/* First use, installation, verified updates and uninstall share the existing panel. */
function install(s){const i=s.installation||{};if(i.sourceMode)return `<div class="page lifecycle"><section class="card lifecycle-copy"><strong>源码运行</strong><p>当前从源码运行，安装向导在打包程序中提供。</p><p>${esc(i.current||'WindowsAgent')}</p></section><button class="secondary" data-action="github">查看项目说明 ${icon('external')}</button></div>`;return `<div class="page lifecycle"><div class="support-identity"><span class="brand">${$('#brand').innerHTML}</span><div><strong>欢迎使用 Remote</strong><small>版本 ${esc(s.version)}</small></div></div><p>选择一个程序文件夹。程序、配对和设置保存在这里，以后直接打开程序即可。</p><label class="section-label" for="install-path">程序位置</label><div class="card lifecycle-path"><input id="install-path" aria-label="程序位置" value="${esc(state.installPath||i.suggested||'')}" spellcheck="false"><button class="secondary" data-action="choose-install">选择文件夹</button></div><button class="primary" data-action="install-program">确认并开始使用</button><small>电脑端需有 WebView2，并安装和配置 Tailscale；手机使用浏览器连接。</small></div>`;}
function firstUse(s){return `<div class="page lifecycle"><section class="card lifecycle-copy"><strong>第一次使用</strong><p>1. 电脑安装并登录 Tailscale。</p><p>2. 在电脑启用 Funnel 公网入口，将 HTTPS 8443 转发到本机 8765。此步骤需另行配置，程序不会自动启用。</p><p>3. 打开 Windows 版 ChatGPT 并登录，在 Remote 首页启动服务。</p><p>4. 手机用浏览器扫描 Rm 的完整连接二维码，输入配对码；手机无需安装 Tailscale。</p><p>5. 选择要控制的 ChatGPT 窗口，并保持电脑运行。</p></section><section class="card lifecycle-copy"><strong>程序与数据</strong><p>${s.installation?.sourceMode?'当前从源码运行。配对和设置保存在 WindowsAgent/data。':'程序和配对数据保存在首次选择的文件夹。更新会保留配对和设置。'}</p><p>卸载入口在设置中，默认保留配对与设置。手机端移除电脑连接不会卸载电脑程序。</p></section><button class="secondary" data-action="import-legacy" data-operation data-unavailable="${!!s.running}" ${s.running?'disabled':''}>导入旧版配对与设置</button><small>先停止本机服务和旧版后台，再选择旧版项目文件夹。原文件会保留。</small><button class="secondary" data-action="github">查看项目说明 ${icon('external')}</button></div>`;}
function uninstall(s){const source=s.installation?.sourceMode;return `<div class="page lifecycle"><section class="card lifecycle-copy"><strong>卸载 ChatGPT Remote</strong><p>停止 Remote 后台，移除它的开机启动、程序和运行缓存。</p><p>ChatGPT 和聊天记录保存在 ChatGPT 中。</p></section>${source?'<p>当前从源码运行。请按项目说明先停止服务、移除计划任务，再移除源码目录。</p>':`<div class="toggle-row"><span class="grow">保留配对与设置</span><button class="switch" role="switch" aria-label="保留配对与设置" aria-checked="${state.keepData!==false}" data-action="uninstall-keep"></button></div><small>${state.keepData===false?'此电脑的配对与设置将被清除，手机需要重新配对。':'下次安装到原来的文件夹，可继续使用配对与设置。'}</small><button class="primary" data-action="uninstall-program">卸载程序</button>`}</div>`;}
function lifecycleUpdate(s) {
 const update = s.update || {}, status = update.status || 'idle';
 const working = ['checking', 'downloading', 'installing'].includes(status);
 const retryDownload = status === 'error' && update.available;
 const progress = Math.max(0, Math.min(100, Number(update.progress) || 0));
 const titles = {
  idle: '检查是否有新版本', checking: '正在检查更新…', current: '当前已是最新版本',
  available: '发现可用更新', downloading: '正在下载更新…', ready: '更新已准备好',
  installing: '正在重启并更新…', error: '更新未完成',
 };
 let action = 'check-update', label = '检查更新';
 if (status === 'available' || retryDownload) {
  action = update.installable ? 'download-update' : 'releases';
  label = update.installable ? (retryDownload ? '重新下载' : '下载更新') : '打开下载页';
 } else if (status === 'ready') {
  action = update.installable ? 'install-update' : 'releases';
  label = update.installable ? '重启并更新' : '打开下载页';
 } else if (working || status === 'error') {
  label = {checking: '正在检查…', downloading: `正在下载 ${progress}%`, installing: '正在重启…', error: '重试'}[status];
 }
 const hasVersion = update.version && (retryDownload || ['available', 'downloading', 'ready', 'installing'].includes(status));
 const hasNotes = hasVersion && update.notes;
 const notesOpen = hasNotes && state.releaseNotesOpen;
 const version = '版本 ' + esc(update.version || '');
 const details = status === 'error' ? (update.message || '请重试') : {
  idle: '从 GitHub 获取最新正式版。', current: '已检查 GitHub 正式版。',
 }[status];
 const release = hasNotes ? `<div class="update-release">
  <button class="update-notes-toggle" data-action="update-notes" aria-controls="release-notes"
   aria-expanded="${!!notesOpen}">${version} · 更新说明 ${icon('right')}</button>
  <div id="release-notes" class="update-notes-body" role="region" aria-label="更新内容"
   tabindex="0" ${notesOpen ? '' : 'hidden'}>${esc(update.notes)}</div>
 </div>` : hasVersion ? `<p class="update-description">${version}</p>` : '';
 return `<div class="update-page">
  <section class="update-identity"><span class="brand">${$('#brand').innerHTML}</span>
   <h3 class="update-brand">ChatGPT Remote</h3>
   <p class="update-version">当前版本 ${esc(update.current || s.version)}</p>
   <button class="update-github" data-action="github">GitHub 项目 ${icon('external')}</button>
  </section>
  <button class="primary" data-action="${action}" data-operation data-unavailable="${working}" ${working ? 'disabled' : ''}>${label}</button>
  <section class="update-state">
   <h3 class="update-message" aria-live="polite">${titles[status] || titles.idle}</h3>
   ${details ? `<p class="update-description">${esc(details)}</p>` : ''}
   ${status === 'downloading' ? '<button class="text-action update-cancel" data-action="cancel-update">取消下载</button>' : ''}
   ${release}
  </section>
 </div>`;
}

let releaseRefreshPending = null, releaseActionPending = false;

function applyReleaseState(update) {
 if (!update || typeof update.status !== 'string') throw new Error('无法读取更新状态，请重试。');
 if (JSON.stringify(update) === JSON.stringify(state.status.update)) return;
 state.status.update = update;
 if (!$('#dialog').open) render();
}

async function refreshRelease() {
 if (!state.status || !state.panelVisible || document.hidden || state.page !== 'update' || state.busy || releaseActionPending) return;
 if (releaseRefreshPending) return releaseRefreshPending;
 const previous = state.status.update;
 const stillCurrent = () => state.panelVisible && state.page === 'update' &&
  !releaseActionPending && state.status.update === previous;
 releaseRefreshPending = (async () => {
  try {
   const {update} = await call('updates.status');
   if (stillCurrent()) applyReleaseState(update);
  } catch (error) {
   if (stillCurrent()) applyReleaseState({...previous, status: 'error', available: false, message: error.message});
  }
 })();
 try {
  await releaseRefreshPending;
 } finally {
  releaseRefreshPending = null;
 }
}

async function requestRelease(action) {
 if (state.busy || releaseActionPending) return;
 const previous = state.status.update || {};
 releaseActionPending = true;
 try {
  if (action === 'updates.check') applyReleaseState({...previous, status: 'checking'});
  const {update} = await call(action);
  applyReleaseState(update);
 } catch (error) {
  applyReleaseState({...previous, status: 'error', available: action !== 'updates.check' && !!previous.available, message: error.message});
 } finally {
  releaseActionPending = false;
 }
}

async function lifecycleAction(action,button){
 if(action==='exit-setup'){await call('window.close');return true;}
 if(['first-use','uninstall'].includes(action)){go(action);return true;}
 if(action==='import-legacy'){const r=await operate('installation.import');if(r?.imported)toast('配对与设置已导入');return true;}
 if(action==='choose-install'){const r=await operate('installation.choose');if(r?.path){state.installPath=r.path;render();}return true;}
 if(action==='install-program'){state.installPath=$('#install-path').value.trim();if(!state.installPath){toast('请选择程序文件夹',true);return true;}await operate('installation.install',{path:state.installPath});return true;}
 if(action==='uninstall-keep'){state.keepData=state.keepData===false;render();return true;}
 if(action==='uninstall-program'){showDialog({title:'确认卸载？',description:state.keepData===false?'配对与设置将一并清除。':'配对与设置会保留。',confirm:'卸载',danger:true,submit:()=>call('uninstall',{keep:state.keepData!==false})});return true;}
 if(action==='install-update'){if(!state.status?.update?.installable)return true;showDialog({title:'重启并更新？',description:'手机连接会短暂断开，配对与设置会保留。',confirm:'重启并更新',submit:async()=>{const r=await call('updates.install');if(r?.update)state.status.update=r.update;}});return true;}
 const operations={'check-update':'updates.check','download-update':'updates.download','cancel-update':'updates.cancel'};
 if(operations[action]){await requestRelease(operations[action]);return true;}
 return false;
}
